"""VW device-flow login orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
from aiohttp import client_exceptions
from yarl import URL

from ..vw_const import (
    CLIENT_SCOPE,
    DEVICE_FLOW_AUTHORIZATION_URL,
    DEVICE_FLOW_BROWSER_HEADERS,
    DEVICE_FLOW_BROWSER_HEADERS_FIREFOX,
    DEVICE_FLOW_CLIENT_ID,
    DEVICE_FLOW_CODE_CONFIRMATION_URL,
    DEVICE_FLOW_LOGIN_AUTHENTICATE_URL,
    DEVICE_FLOW_LOGIN_IDENTIFIER_URL,
    DEVICE_FLOW_TOKEN_URL,
)
from ..vw_exceptions import (
    LoginCredentialsError,
    LoginError,
    LoginFlowChangedError,
    LoginPageParseError,
)
from ..vw_utilities import dump_html_debug, safe_int
from ._idkit import (
    _IdKitError,
    IdKitInfo,
    IdKitPageObject,
    IdKitPageObjectExtractor,
    IdKitStage,
)
from ._verification import LoginVerifier

_LOGGER = logging.getLogger(__name__)

FULL_ROUTE = [
    IdKitStage.IDENTIFIER,
    IdKitStage.PASSWORD,
    IdKitStage.CONFIRM,
    IdKitStage.SUCCESS,
]
QUICK_ROUTE = [IdKitStage.CONFIRM, IdKitStage.SUCCESS]

_TRANSIENT_POLL_STATUSES = {429, 500, 502, 503, 504}
_TRANSIENT_POLL_ERRORS = {"temporarily_unavailable", "server_error"}

# Known VW identity error codes returned as error= in the redirect URL.
_VW_AUTH_ERROR_MESSAGES: dict[str, str] = {
    "login.errors.password_invalid": "Incorrect password.",
    "login.error.throttled": "Too many failed login attempts — please wait before trying again.",
    "login.error.locked": "Account has been locked due to too many failed attempts.",
    "login.error.blocked": "Login blocked by VW identity service.",
}


@dataclass(frozen=True)
class LoginRequest:
    """Request payload for a single login stage."""

    stage: IdKitStage
    url: str
    payload: dict[str, str]


class VWLoginFlow:
    """Route-driven VW device-flow login."""

    def __init__(
        self,
        verifier: LoginVerifier | None = None,
        html_debug_dir: Path | None = None,
        use_fake_user_agent: bool = False,
        client_id: str = DEVICE_FLOW_CLIENT_ID,
        client_scope: str = CLIENT_SCOPE,
    ) -> None:
        self._verifier = verifier or LoginVerifier()
        self._html_debug_dir = html_debug_dir
        self._use_fake_user_agent = use_fake_user_agent
        self._client_id = _require_str(client_id, "client_id")
        self._client_scope = _require_str(client_scope, "client_scope")

    async def login(
        self,
        username: str,
        password: str,
        *,
        cookies_file: Path | None = None,
    ) -> dict:
        _require_str(username, "username")
        _require_str(password, "password")

        async with aiohttp.ClientSession(headers=self._browser_headers()) as session:
            device = await self._start_device_flow(session)
            device_code = _require_str(device.get("device_code"), "device_code")
            verification_uri = (
                device.get("verification_uri_complete")
                or device.get("verification_uri")
                or ""
            )
            if not verification_uri:
                raise LoginError("Device flow response missing verification URI")

            interval = safe_int(device.get("interval", 5), 5)
            expires_in = safe_int(device.get("expires_in", 330), 330)
            max_wait = max(330, max(expires_in - 5, 30))

            try:
                await self._run_browser_route(
                    verification_uri=verification_uri,
                    username=username,
                    password=password,
                    cookies_file=cookies_file,
                )
            except (LoginFlowChangedError, LoginPageParseError):
                raise
            except _IdKitError as error:
                raise LoginPageParseError(str(error)) from error

            return await self._poll_token(
                session=session,
                device_code=device_code,
                interval=interval,
                max_wait_seconds=max_wait,
            )

    async def _start_device_flow(self, session: aiohttp.ClientSession) -> dict:
        _LOGGER.debug("POST %s", DEVICE_FLOW_AUTHORIZATION_URL)
        async with session.post(
            DEVICE_FLOW_AUTHORIZATION_URL,
            data={"client_id": self._client_id, "scope": self._client_scope},
        ) as response:
            _LOGGER.debug("device_authorization response: HTTP %s", response.status)
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _run_browser_route(
        self,
        verification_uri: str,
        username: str,
        password: str,
        cookies_file: Path | None,
    ) -> None:
        jar = aiohttp.CookieJar(unsafe=True)
        if cookies_file is not None:
            await _load_cookies(jar, cookies_file, "identity.vwgroup.io")
        current_url = ""
        html = ""

        async with aiohttp.ClientSession(
            headers=self._browser_headers(),
            cookie_jar=jar,
        ) as browser:
            try:
                _LOGGER.debug("GET %s", verification_uri)
                async with browser.get(verification_uri, allow_redirects=True) as response:
                    current_url = str(response.url)
                    html = await response.text()
                _LOGGER.debug("initial landing: HTTP %s url=%s", response.status, current_url)

                try:
                    idk_obj = IdKitPageObjectExtractor.from_html(html)
                except _IdKitError as error:
                    self._raise_page_parse_error("initial_landing", current_url, html, error)

                try:
                    initial_stage = idk_obj.stage
                except _IdKitError as error:
                    self._raise_initial_parse_error(current_url, html, error)

                route = self._pick_route(initial_stage, current_url, html)
                _LOGGER.debug("initial stage=%s route=%s", initial_stage.value, [s.value for s in route])
                idk_info = IdKitInfo()

                for index, expected_stage in enumerate(route[:-1]):
                    try:
                        current_stage = idk_obj.stage
                    except _IdKitError as error:
                        self._raise_initial_parse_error(current_url, html, error)

                    if current_stage != expected_stage:
                        self._raise_flow_changed(
                            "unexpected_current_stage",
                            current_url,
                            html,
                            f"expected {expected_stage.value!r}, got {current_stage.value!r}",
                        )

                    self._verifier.verify_idk_obj(
                        stage=expected_stage,
                        html=html,
                        idk_obj=idk_obj,
                        idk_info=idk_info,
                        configured_client_id=self._client_id,
                        expected_username=username,
                    )

                    self._update_idk_info(idk_info, idk_obj, expected_stage)
                    request = self._build_request(expected_stage, idk_info, username, password)

                    self._verifier.verify_request_matches_form(
                        stage=expected_stage,
                        current_url=current_url,
                        html=html,
                        planned_url=request.url,
                        planned_payload=request.payload,
                    )

                    _LOGGER.debug("POST %s (stage=%s)", request.url, expected_stage.value)
                    async with browser.post(
                        request.url,
                        data=request.payload,
                        allow_redirects=True,
                    ) as response:
                        current_url = str(response.url)
                        html = await response.text()
                        status = response.status
                    _LOGGER.debug("POST response: HTTP %s url=%s", status, current_url)

                    if status >= 400:
                        raise LoginError(f"HTTP {status} while handling {expected_stage.value}")

                    if expected_stage == IdKitStage.CONFIRM:
                        self._check_final_url(current_url)

                    self._check_url_for_credentials_error(current_url)

                    try:
                        idk_obj = IdKitPageObjectExtractor.from_html(html)
                    except _IdKitError as error:
                        self._raise_page_parse_error(
                            f"after_{expected_stage.value}", current_url, html, error
                        )

                    expected_next = route[index + 1]
                    try:
                        next_stage = idk_obj.stage
                    except _IdKitError as error:
                        self._raise_initial_parse_error(current_url, html, error)

                    if next_stage != expected_next:
                        self._raise_flow_changed(
                            "unexpected_next_stage",
                            current_url,
                            html,
                            f"after {expected_stage.value!r}: expected {expected_next.value!r}, "
                            f"got {next_stage.value!r}",
                        )

                if cookies_file is not None:
                    await _save_cookies(jar, cookies_file)

            except (LoginFlowChangedError, LoginPageParseError, LoginError) as error:
                await self._dump_and_reraise(error, current_url, html)

    def _build_request(
        self,
        stage: IdKitStage,
        idk_info: IdKitInfo,
        username: str,
        password: str,
    ) -> LoginRequest:
        if stage == IdKitStage.IDENTIFIER:
            client_id = idk_info.client_id or self._client_id
            return LoginRequest(
                stage=stage,
                url=DEVICE_FLOW_LOGIN_IDENTIFIER_URL.format(client_id=client_id),
                payload={
                    "_csrf": idk_info.get_csrf_token(),
                    "relayState": idk_info.get_relay_state(),
                    "hmac": idk_info.get_hmac(),
                    "email": username,
                },
            )

        if stage == IdKitStage.PASSWORD:
            return LoginRequest(
                stage=stage,
                url=DEVICE_FLOW_LOGIN_AUTHENTICATE_URL.format(
                    client_id=idk_info.get_client_id()
                ),
                payload={
                    "_csrf": idk_info.get_csrf_token(),
                    "relayState": idk_info.get_relay_state(),
                    "hmac": idk_info.get_hmac(),
                    "email": username,
                    "password": password,
                },
            )

        if stage == IdKitStage.CONFIRM:
            query = urlencode({
                "relayState": idk_info.get_relay_state(),
                "user_id": idk_info.get_user_id(),
                "hmac": idk_info.get_hmac(),
            })
            return LoginRequest(
                stage=stage,
                url=(
                    DEVICE_FLOW_CODE_CONFIRMATION_URL.format(
                        client_id=idk_info.get_client_id(),
                        user_code=idk_info.get_user_code(),
                    )
                    + "?"
                    + query
                ),
                payload={
                    "_csrf": idk_info.get_csrf_token(),
                    "client_identity_name": idk_info.get_client_identity_name(),
                    "allow": "",
                },
            )

        raise LoginFlowChangedError(stage=stage.value)

    def _update_idk_info(
        self,
        idk_info: IdKitInfo,
        idk_obj: IdKitPageObject,
        stage: IdKitStage,
    ) -> None:
        idk_info.stage = stage
        idk_info.csrf_token = _require_str(idk_obj.csrf_token, "csrf_token")

        if stage == IdKitStage.IDENTIFIER:
            idk_info.set_client_id(idk_obj.client_id)
            idk_info.set_relay_state(idk_obj.relay_state)
            idk_info.set_hmac(idk_obj.hmac)
        elif stage == IdKitStage.PASSWORD:
            idk_info.set_relay_state(idk_obj.relay_state)
            idk_info.set_hmac(idk_obj.hmac)
        elif stage == IdKitStage.CONFIRM:
            idk_info.set_client_id(idk_obj.client_id)
            idk_info.set_relay_state(idk_obj.relay_state)
            idk_info.set_hmac(idk_obj.hmac)
            idk_info.set_user_code(idk_obj.user_code)
            idk_info.set_user_id(idk_obj.user_id)
            idk_info.set_client_identity_name(idk_obj.client_identity_name)
        elif stage not in (IdKitStage.PASSWORD, IdKitStage.SUCCESS):
            raise LoginFlowChangedError(stage=stage.value)

    def _pick_route(
        self,
        initial_stage: IdKitStage,
        current_url: str,
        html: str,
    ) -> list[IdKitStage]:
        if initial_stage == IdKitStage.IDENTIFIER:
            return FULL_ROUTE
        if initial_stage == IdKitStage.CONFIRM:
            return QUICK_ROUTE
        self._raise_flow_changed(
            "unexpected_initial_stage",
            current_url,
            html,
            f"expected identifier or confirm, got {initial_stage.value!r}",
        )

    async def _poll_token(
        self,
        session: aiohttp.ClientSession,
        device_code: str,
        interval: int,
        max_wait_seconds: int,
    ) -> dict:
        deadline = time.monotonic() + max_wait_seconds
        poll_interval = max(interval, 1)

        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            _LOGGER.debug("POST %s (polling, interval=%ss)", DEVICE_FLOW_TOKEN_URL, poll_interval)
            async with session.post(
                DEVICE_FLOW_TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": self._client_id,
                },
            ) as response:
                if response.status == 200:
                    return await response.json(content_type=None)

                if response.status in _TRANSIENT_POLL_STATUSES:
                    _LOGGER.debug("Transient token poll HTTP %s", response.status)
                    continue

                try:
                    body = await response.json(content_type=None)
                except (client_exceptions.ContentTypeError, json.JSONDecodeError, ValueError):
                    body = {}

                error_code = body.get("error", "")

            if error_code == "authorization_pending":
                continue
            if error_code == "slow_down":
                poll_interval += 5
                continue
            if error_code in _TRANSIENT_POLL_ERRORS:
                _LOGGER.debug("Transient token poll OAuth error=%s", error_code)
                continue

            raise LoginError(f"Token polling failed with OAuth error: {error_code!r}")

        raise LoginError("Token polling timed out")

    # --- error helpers ---

    def _raise_initial_parse_error(
        self, current_url: str, html: str, error: _IdKitError
    ) -> None:
        # registerCredentials means the email is not a VW account.
        if "registerCredentials" in str(error):
            raise LoginCredentialsError("Email address not found in VW account system")
        self._raise_page_parse_error("initial_landing", current_url, html, error)

    def _check_url_for_credentials_error(self, url: str) -> None:
        error_val = parse_qs(urlparse(url).query).get("error", [None])[0]
        if not error_val:
            return
        message = _VW_AUTH_ERROR_MESSAGES.get(error_val) or f"Authentication rejected by VW: {error_val!r}"
        raise LoginCredentialsError(message)

    def _raise_flow_changed(
        self, stage: str, current_url: str, html: str, reason: str
    ) -> None:
        _LOGGER.error("Login flow changed (stage=%s, url=%s): %s", stage, current_url, reason)
        raise LoginFlowChangedError(stage=stage)

    def _raise_page_parse_error(
        self, stage: str, current_url: str, html: str, error: _IdKitError
    ) -> None:
        _LOGGER.error("IDKit parse failed (stage=%s, url=%s): %s", stage, current_url, error)
        raise LoginPageParseError(
            f"IDKit parse failed at {stage} (url={current_url}): {error}"
        ) from error

    def _check_final_url(self, url: str) -> None:
        error_val = parse_qs(urlparse(url).query).get("error", [None])[0]
        if error_val:
            raise LoginError(f"Code confirmation returned error={error_val!r}")

    async def _dump_and_reraise(self, error: Exception, current_url: str, html: str) -> None:
        if not current_url or not html or self._html_debug_dir is None:
            raise error
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            raise error

        if isinstance(error, LoginFlowChangedError):
            dump_stage = f"flow_changed_{error.stage}"
        elif isinstance(error, LoginPageParseError):
            dump_stage = "page_parse_error"
        else:
            dump_stage = "login_error"

        dump_path = dump_html_debug(dump_stage, html, self._html_debug_dir, url=current_url)
        dump_path_str = str(dump_path) if dump_path else None

        if isinstance(error, LoginFlowChangedError):
            raise LoginFlowChangedError(
                stage=error.stage, dump_path=dump_path_str
            ) from error
        raise type(error)(f"{error} (dump={dump_path_str})") from error

    def _browser_headers(self) -> dict[str, str]:
        if self._use_fake_user_agent:
            return DEVICE_FLOW_BROWSER_HEADERS_FIREFOX.copy()
        return DEVICE_FLOW_BROWSER_HEADERS.copy()


def _require_str(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise LoginError(f"Missing or invalid {field_name}")
    return value


async def _save_cookies(jar: aiohttp.CookieJar, path: Path) -> None:
    data = [
        {
            "name": morsel.key,
            "value": morsel.value,
            "domain": morsel.get("domain", ""),
            "path": morsel.get("path", "/"),
        }
        for morsel in jar
    ]
    await asyncio.to_thread(path.write_text, json.dumps(data, indent=2), encoding="utf-8")


async def _load_cookies(jar: aiohttp.CookieJar, path: Path, default_domain: str) -> None:
    if not await asyncio.to_thread(path.exists):
        return
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    for cookie in json.loads(text):
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        domain = cookie.get("domain") or default_domain
        cookie_path = cookie.get("path") or "/"
        jar.update_cookies(
            {name: value},
            response_url=URL(f"https://{domain}{cookie_path}"),
        )
