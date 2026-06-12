#!/usr/bin/env python3
"""Communicate with Volkswagen Connect services."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import logging
from random import randint, random
import re
import secrets
import time
import uuid
from urllib.parse import parse_qs, urljoin, urlparse
from typing import Any

import aiohttp
from aiohttp import ClientTimeout, client_exceptions
from aiohttp.hdrs import METH_GET, METH_POST, METH_PUT
from bs4 import BeautifulSoup
import jwt

from .vw_const import (
    ANDROID_PACKAGE_NAME,
    APP_URI,
    APP_VERSION,
    APP_VERSION_SHORT,
    BASE_API,
    BRAND,
    CLIENT_ID,
    CLIENT_SCOPE,
    COUNTRY,
    COUNTRY_TO_LOCALE,
    HEADERS_AUTH,
    HEADERS_SESSION,
    MAX_REDIRECT_DEPTH,
    MBB_BRAND_CONFIG,
    USER_AGENT,
    XQMAUTH_PREFIX,
    XQMAUTH_SECRET,
    get_region_from_country,
    get_region_config,
)

from .vw_exceptions import (
    AuthenticationError,
    APIError,
    SPINError,
    RedirectError,
    RequestError,
    TermsAndConditionsError,
)

from .vw_utilities import json_loads, redact
from .vw_vehicle import Vehicle

MAX_RETRIES_ON_RATE_LIMIT = 3
RVS_MAX_RETRIES = 2  # Max retry attempts for transient 5xx on RVS endpoints

VW_DOMAIN_ALLOWLIST = (
    ".vwgroup.io",  # identity.na.vwgroup.io, identity.vwgroup.io
    ".con-veh.net",  # b-h-s.spr.us00.p.con-veh.net (confirmed NA base)
    ".cariad.digital",  # emea.bff.cariad.digital, na.bff.cariad.digital candidates
    ".vwg-connect.com",  # mbboauth-1d.prd.ece.vwg-connect.com
    ".volkswagen.de",  # msg.volkswagen.de (EMEA home region)
    ".volkswagen.com",  # msg.volkswagen.com (NA homeregion candidate)
    ".vw.com",  # msg.vw.com (NA homeregion candidate)
    ".vw.us",  # msg.vw.us (NA homeregion candidate)
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = timedelta(seconds=30)
JWT_ALGORITHMS = ["RS256"]


# noinspection PyPep8Naming
class Connection:
    """Connection to VW-Group Connect services."""

    def __init__(
        self,
        session: Any,
        username: str,
        password: str,
        country: str = COUNTRY,
        interval: timedelta = timedelta(minutes=5),
        xclient_id: str | None = None,
        on_xclient_id: Any | None = None,
        spin: str | None = None,
        rvs_cache_ttl: int = 30,
    ) -> None:
        """Initialize a Connection to VW Connect services.

        Supports both EMEA (Europe) and North America regions. The region is
        auto-detected from the ``country`` parameter: US and CA route to NA
        endpoints; all other country codes use EMEA (default).

        Args:
            session: An aiohttp ClientSession for HTTP requests.
            username: VW account email address.
            password: VW account password.
            country: ISO 3166-1 alpha-2 country code. Defaults to 'DE'.
                Use 'US' or 'CA' for North America Car-Net.
            interval: Minimum interval between update cycles. Defaults to 5 minutes.
            xclient_id: Optional X-Client-Id header override.
            on_xclient_id: Optional callback invoked when X-Client-Id changes.
            spin: Security PIN for operations that require it (lock/unlock, honk & flash).
            rvs_cache_ttl: TTL in seconds for the per-vehicle RVS data cache. Defaults to 30.
                Consecutive update() calls within this window reuse cached data without API calls.

        Example:
            >>> async with ClientSession() as session:
            ...     conn = Connection(session, "user@example.com", "pass", country="US")
            ...     await conn.doLogin()
        """
        self._session = session
        self._session_headers = HEADERS_SESSION.copy()
        self._session_auth_headers = HEADERS_AUTH.copy()
        self._session_refresh_interval = interval
        self._session_logged_in = False
        self._session_first_update = False
        self._session_auth_username = username
        self._session_auth_password = password
        self._session_tokens: dict[str, Any] = {}
        self._session_country = country.upper()
        self._spin = spin
        self._rvs_cache_ttl = rvs_cache_ttl

        # Determine region from country
        self._session_region = get_region_from_country(self._session_country)
        self._session_region_config = get_region_config(self._session_region)

        # Set region-specific base API (hardcoded for NA, no discovery)
        self._base_api = self._session_region_config.get("base_api")

        # Set region-specific client ID for OAuth
        self._client_id = self._session_region_config.get("client_id", CLIENT_ID)

        self._vehicles: list[Vehicle] = []

        self._jarCookie = None

        self._service_status: dict[str, Any] = {}
        self._pkce_verifier: str | None = None
        self._pkce_challenge: str | None = None
        self._is_throttled: bool = False
        self.discovery_config: dict = {}

        # NA three-token registry (empty for EMEA, populated lazily during _login_na())
        self._na_tokens: dict = {}  # keys: "idk", "brand", "mbb", plus per-VIN keys (e.g., _na_tokens["WVWZZZ..."]["vehicle_session"])
        self._na_rvs_cache: dict = {}  # Per-VIN RVS data cache (empty for EMEA, populated during NA data fetch)
        self._xclient_id: str | None = (
            xclient_id  # caller-injected or registered during login
        )
        self._xclient_id_callback = (
            on_xclient_id  # called only when NEW xclientId generated
        )
        self._na_auth_level: str | None = None  # "full", "idk_only", or None (EMEA)
        # Shared lock for login and token refresh (prevents concurrent login+refresh race)
        self._login_lock = asyncio.Lock()
        self._update_lock = asyncio.Lock()
        # NA token endpoint URL (populated during _login_na, needed for IDK refresh)
        self._na_token_endpoint: str | None = None

    def _clear_cookies(self) -> None:
        self._session._cookie_jar._cookies.clear()  # pylint: disable=protected-access

    def _generate_pkce_verifier(self) -> str:
        """Generate PKCE code_verifier.

        Returns:
            Base64URL-encoded random string (43-128 characters)
        """
        # Generate 32 random bytes, base64url encode (43 chars)
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(
            "utf-8"
        )
        # Remove padding
        return code_verifier.rstrip("=")

    def _generate_pkce_challenge(self, code_verifier: str) -> str:
        """Generate PKCE code_challenge from code_verifier.

        Args:
            code_verifier: The code verifier string

        Returns:
            Base64URL-encoded SHA256 hash of the verifier
        """
        # SHA256 hash
        digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        # Base64URL encode
        code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8")
        # Remove padding
        return code_challenge.rstrip("=")

    @staticmethod
    def _calculate_xqmauth(timestamp: float | None = None) -> str:
        """Calculate X-QMAuth header value using HMAC-SHA256.

        The X-QMAuth header is required for IDK token exchange and refresh
        on VW Group platforms. It uses a time-based HMAC with a shared secret.

        Args:
            timestamp: Unix epoch timestamp in seconds. Defaults to current time.
                       Accepts float for deterministic testing with frozen time.

        Returns:
            X-QMAuth header string in format 'v1:01da27b0:<hmac-hex-digest>'
        """
        if timestamp is None:
            timestamp = time.time()
        gmtime_100sec = int(timestamp / 100)
        xqmauth_val = hmac.new(
            XQMAUTH_SECRET,
            str(gmtime_100sec).encode("ascii"),
            digestmod="sha256",
        ).hexdigest()
        return XQMAUTH_PREFIX + xqmauth_val

    def _classify_endpoint(self, url: str) -> str:
        """Classify an API URL to determine which NA token type to use.

        Token routing rules:
        - IDK token: Cariad BFF URLs (self._base_api prefix).
        - MBB token: MBB OAuth service (mbboauth-1d.prd.ece.vwg-connect.com).
        - Brand token: Brand token paths (/login/v1/volkswagen/token or /login/v1/vw/token).

        For EMEA connections, always returns 'idk' (single-token model).

        Args:
            url: The full URL being requested.

        Returns:
            One of: 'idk', 'mbb', 'brand'.

        Raises:
            ValueError: If url does not match any known NA endpoint pattern.
                This is a programmer error — fail loudly.
        """
        if self._session_region != "NA":
            return "idk"  # EMEA always uses the single IDK/access_token

        mbb_host = "mbboauth-1d.prd.ece.vwg-connect.com"
        brand_paths = ("/login/v1/volkswagen/token", "/login/v1/vw/token")

        if mbb_host in url:
            return "mbb"
        if any(path in url for path in brand_paths):
            return "brand"
        if self._base_api and url.startswith(self._base_api):
            return "idk"

        raise ValueError(
            f"Cannot classify NA endpoint — unknown URL pattern: {url!r}. "
            "Add URL to _classify_endpoint() or check base_api configuration."
        )

    def _is_allowed_vw_domain(self, url: str) -> bool:
        """Return True if URL hostname ends with a known VW Group domain suffix."""
        hostname = urlparse(url).hostname or ""
        return any(hostname.endswith(suffix) for suffix in VW_DOMAIN_ALLOWLIST)

    async def _discover_market_config(self) -> bool:
        """Discover and cache market configuration from VW OIDC discovery endpoint.

        Called on every doLogin() for NA region. Uses self.discovery_config as
        session cache guard — if already populated, returns True immediately.
        Validates all URL values against VW_DOMAIN_ALLOWLIST before storing.

        On failure: logs WARNING, leaves self._base_api at hardcoded default,
        sets self._service_status["discovery"] = "Failed". Does NOT block login.

        Returns:
            True if discovery succeeded or was already cached, False on failure.
        """
        if self._session_region != "NA":
            self._service_status["discovery"] = "Skipped"
            return True

        if self.discovery_config:
            # Already discovered this session — use cache
            return True

        candidates = self._session_region_config.get("base_api_candidates", [])
        if not candidates:
            self._service_status["discovery"] = "Skipped"
            return True

        timeout = ClientTimeout(total=10)

        for candidate in candidates:
            config_url = f"{candidate}/auth/v1/idk/oidc/openid-configuration"
            try:
                _LOGGER.debug("Attempting market config discovery at %s", config_url)
                async with self._session.get(url=config_url, timeout=timeout) as resp:
                    if resp.status != 200:
                        continue
                    raw_config = await resp.json()

                    # Validate all URL values against allowlist before applying
                    validated = {}
                    for key, value in raw_config.items():
                        if isinstance(value, str) and value.startswith("http"):
                            if not self._is_allowed_vw_domain(value):
                                _LOGGER.warning(
                                    "Discovery: rejected URL %r for key %r (domain not in allowlist)",
                                    value,
                                    key,
                                )
                                continue
                        validated[key] = value

                    self.discovery_config = validated
                    self._base_api = candidate
                    self._service_status["discovery"] = "Success"
                    _LOGGER.debug("Market config discovery succeeded via %s", candidate)
                    return True

            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                OSError,
                ValueError,
                KeyError,
            ) as exc:
                _LOGGER.debug(
                    "Config discovery attempt failed for %s: %s", candidate, exc
                )
                continue

        _LOGGER.warning(
            "Discovery failed, falling back to hardcoded config. "
            "Endpoint %s confirmed working as fallback.",
            self._session_region_config.get("base_api", "unknown"),
        )
        self._service_status["discovery"] = "Failed"
        return False

    async def doLogin(self, tries: int = 1) -> bool:
        """Authenticate with VW Connect and discover vehicles.

        Performs the full OAuth2 authorization code flow:
        - EMEA: standard flow with IDK + Brand + MBB token exchange.
        - NA (country='US'/'CA'): PKCE-based flow producing a single IDK token.
          NA Car-Net does not support Brand or MBB tokens (auth level: IDK-only).

        After successful authentication, populates ``self.vehicles`` with
        discovered Vehicle objects.

        Args:
            tries: Number of login attempts before giving up. Defaults to 1.

        Returns:
            True if login succeeded and at least one vehicle was discovered,
            False otherwise.

        Raises:
            AuthenticationError: If credentials are invalid or the OAuth flow fails.
            APIError: If the vehicle garage endpoint returns an unexpected error.

        Example:
            >>> if await connection.doLogin():
            ...     print(f"Found {len(connection.vehicles)} vehicles")
        """
        async with self._login_lock:
            _LOGGER.debug("Initiating new login")

            # Discover market config for NA (result cached in self.discovery_config)
            if self._session_region == "NA":
                if not await self._discover_market_config():
                    _LOGGER.warning(
                        "Market config discovery failed, using hardcoded values"
                    )
                    # Do NOT return False — self._base_api has hardcoded pre-confirmed value

            for i in range(tries):
                self._session_logged_in = await self._login()
                if self._session_logged_in:
                    break
                if i < tries - 1:
                    await asyncio.sleep(random() * 5)
            else:
                _LOGGER.error("Login failed after %s tries", tries)
                return False

            if not self._session_logged_in:
                return False

            _LOGGER.info("Successfully logged in")

            # Get list of vehicles from account
            _LOGGER.debug("Fetching vehicles associated with account")
            self._session_headers.pop("Content-Type", None)

            if self._session_region == "NA":
                # NA uses Car-Net garage endpoint: GET /account/v1/garage?idToken={id_token}
                # Confirmed from APK decompilation: cz.a Retrofit interface @GET("account/v1/garage")
                id_token = self._session_tokens.get("identity", {}).get("id_token", "")
                loaded_vehicles = await self._request(
                    METH_GET,
                    f"{self._base_api}/account/v1/garage",
                    params={"idToken": id_token},
                )
                vehicle_list = loaded_vehicles.get("data", {}).get("vehicles")
            else:
                loaded_vehicles = await self.get(
                    url=f"{self._base_api}/vehicle/v2/vehicles"
                )
                vehicle_list = loaded_vehicles.get("data")

            # Store NA-specific per-VIN metadata for session creation
            if self._session_region == "NA" and vehicle_list:
                for vehicle_data in vehicle_list:
                    vin = vehicle_data.get("vin")
                    if vin:
                        self._na_tokens.setdefault(vin, {})["tsp_provider"] = (
                            vehicle_data.get("tspProvider", "ATC")
                        )
                        vehicle_id = vehicle_data.get("vehicleId")
                        if vehicle_id:
                            self._na_tokens[vin]["vehicle_id"] = vehicle_id
                        _LOGGER.debug(
                            "NA: stored garage metadata for %s — tspProvider=%r, vehicleId=%r",
                            vin,
                            vehicle_data.get("tspProvider"),
                            vehicle_id,
                        )

            # Add Vehicle class object for all VIN-numbers from account
            if vehicle_list is not None:
                _LOGGER.debug("Found vehicle(s) associated with account")
                self._vehicles = []
                for vehicle in vehicle_list:
                    self._vehicles.append(Vehicle(self, vehicle.get("vin")))
            else:
                if self._session_region == "NA":
                    garage_url = f"{self._base_api}/account/v1/garage"
                    raise APIError(
                        f"NA garage endpoint returned no vehicle list: {garage_url!r}. "
                        "Verify credentials, country='US', and that the account has registered vehicles."
                    )
                _LOGGER.warning("Failed to login to Volkswagen Connect API")
                self._session_logged_in = False
                return False

            # Update all vehicles data before returning
            await self.update()
            return True

    async def get_openid_config(self) -> dict[str, str]:
        """Get OpenID config."""
        # NA: use hardcoded endpoints from region config (app does not fetch well-known)
        auth_ep = self._session_region_config.get("auth_endpoint")
        token_ep = self._session_region_config.get("token_endpoint")
        if auth_ep and token_ep:
            _LOGGER.debug("NA: using hardcoded auth=%s token=%s", auth_ep, token_ep)
            return {
                "authorization_endpoint": auth_ep,
                "token_endpoint": token_ep,
            }

        config_url = f"{self._base_api}/auth/v1/idk/oidc/openid-configuration"
        _LOGGER.debug("Requesting openid config from base API: %s", config_url)
        req = await self._session.get(
            url=config_url, timeout=ClientTimeout(total=TIMEOUT.seconds)
        )
        if req.status != 200:
            _LOGGER.error("Failed to get OpenID configuration, status: %s", req.status)
            raise AuthenticationError(
                f"OpenID configuration error: status {req.status}"
            )
        config = await req.json()
        _LOGGER.debug("OpenID config: %s", config)
        return config

    async def get_authorization_page(self, authorization_endpoint: str) -> str:
        """Fetch the Auth0 Universal Login page for credential submission.

        Hits the OIDC authorization endpoint with response_type=code id_token token
        (hybrid flow). Auth0 redirects to the login form; we follow that single
        redirect and return the HTML so the caller can extract the state token.
        """
        _LOGGER.debug('Requesting authorization page from "%s"', authorization_endpoint)
        self._session_auth_headers.pop("Referer", None)
        self._session_auth_headers.pop("Origin", None)
        # Build OAuth parameters with region-specific settings
        oauth_params = {
            "redirect_uri": self._session_region_config.get("redirect_uri", APP_URI),
            "response_type": self._session_region_config.get("response_type", "code"),
            "client_id": self._client_id,
            "scope": self._session_region_config.get("scope", CLIENT_SCOPE),
            "nonce": uuid.uuid4().hex,
        }

        if self._session_country:
            oauth_params["ui_locales"] = COUNTRY_TO_LOCALE.get(
                self._session_country,
                f"{self._session_country.lower()}-{self._session_country}",
            )
            oauth_params["prompt"] = "login"

        if self._pkce_challenge:
            oauth_params["code_challenge"] = self._pkce_challenge
            oauth_params["code_challenge_method"] = "S256"
            _LOGGER.debug("Added PKCE challenge to authorization request")

        req = await self._session.get(
            url=authorization_endpoint,
            headers=self._session_auth_headers,
            allow_redirects=False,
            params=oauth_params,
        )

        location = req.headers.get("Location")
        if not location:
            raise AuthenticationError(
                f"Missing 'Location' header in authorization response. "
                f"Status: {req.status}"
            )

        ref = urljoin(authorization_endpoint, location)
        if "error" in ref:
            parsed_query = parse_qs(urlparse(ref).query)
            error_msg = parsed_query.get("error", ["Unknown error"])[0]
            error_description = parsed_query.get(
                "error_description", ["No description"]
            )[0]
            _LOGGER.info("Authorization error: %s", error_description)
            raise AuthenticationError(f"{error_msg}: {error_description}")

        # Follow the redirect to the actual login page (allow multi-hop redirects on login pages)
        req = await self._session.get(
            url=ref, headers=self._session_auth_headers, allow_redirects=True
        )
        if req.status != 200:
            raise AuthenticationError(f"Failed to fetch login page (HTTP {req.status})")

        return await req.text()

    def extract_state_token(self, page_content: str) -> str | None:
        """Extract state token from a page."""
        soup = BeautifulSoup(page_content, "html.parser")
        state_input = soup.select_one('input[name="state"]')
        if not state_input or not state_input.get("value"):
            _LOGGER.debug("State token not found.")
            return None
        return str(state_input["value"])

    def _extract_identitykit_form(self, page_html: str) -> dict:
        """Extract hidden fields from a VW IdentiKit login form.

        Handles two page types:
        - Email step: server-rendered form[id="emailPasswordForm"] with hidden inputs
        - Password step: React SPA — data embedded in window._IDK JavaScript object

        Returns dict with keys: csrf, relay_state, hmac, form_action
        Raises AuthenticationError if neither format is recognised.
        """
        soup = BeautifulSoup(page_html, "html.parser")
        form = soup.select_one('form[id="emailPasswordForm"]')
        if form:
            # Server-rendered email step
            def _val(selector: str) -> str | None:
                el = form.select_one(selector)
                return str(el["value"]) if el and el.get("value") else None

            return {
                "csrf": _val('input[name="_csrf"]'),
                "relay_state": _val('input[name="relayState"]'),
                "hmac": _val('input[name="hmac"]'),
                "form_action": form.get("action"),
            }

        # React-rendered password step — extract from window._IDK object
        # templateModel value is valid JSON; outer object uses unquoted JS keys
        tm_match = re.search(
            r"templateModel\s*:\s*(\{.*?\}),\s*\n", page_html, re.DOTALL
        )
        csrf_match = re.search(r"csrf_token\s*:\s*'([^']+)'", page_html)

        if not tm_match or not csrf_match:
            raise AuthenticationError(
                "IdentiKit form not found — login page structure unknown"
            )

        try:
            tm = json.loads(tm_match.group(1))
        except json.JSONDecodeError as exc:
            raise AuthenticationError(
                f"IdentiKit form not found — templateModel JSON parse error: {exc}"
            )

        hmac = tm.get("hmac")
        relay_state = tm.get("relayState")
        post_action = tm.get("postAction")  # e.g. "login/authenticate"
        client_id = (
            tm.get("clientLegalEntityModel", {}).get("clientId") or self._client_id
        )
        csrf = csrf_match.group(1)

        if not all([hmac, relay_state, post_action, csrf]):
            raise AuthenticationError(
                f"IdentiKit form incomplete (JS path) — missing: "
                f"{[k for k, v in {'hmac': hmac, 'relayState': relay_state, 'postAction': post_action, 'csrf': csrf}.items() if not v]}"
            )

        # Reconstruct the full form action path from client_id + postAction
        form_action = f"/signin-service/v1/{client_id}/{post_action}"
        return {
            "csrf": csrf,
            "relay_state": relay_state,
            "hmac": hmac,
            "form_action": form_action,
        }

    async def post_form(
        self,
        session: Any,
        url: str,
        headers: dict[str, str],
        form_data: dict[str, Any],
        redirect: bool = True,
    ) -> str:
        """Post a form and check for success."""
        req = await session.post(
            url, headers=headers, data=form_data, allow_redirects=redirect
        )

        # Redirect case
        if not redirect and 300 <= req.status < 400:
            return req.headers.get("Location")

        # Handle explicit error 400 (form validation failure)
        if req.status == 400:
            page_content = await req.text()
            soup = BeautifulSoup(page_content, "html.parser")

            # Try both username + password fields in one pass
            for field_id in ("error-element-username", "error-element-password"):
                span = soup.select_one(f'span[id="{field_id}"]')
                if not span:
                    continue

                error_code = span.get("data-error-code")
                if error_code == "wrong-email-credentials":
                    raise AuthenticationError("Wrong username or password")

            # Unknown 400 error — log truncated body for debugging
            _LOGGER.debug(
                "post_form 400 response (first 500 chars): %s",
                page_content[:500],
            )
            raise AuthenticationError(
                "Login form validation failed with unknown 400 error"
            )

        # Any unexpected HTTP code
        if req.status not in (200, 400):
            raise RequestError(
                f"Login form submission failed with HTTP {req.status}. "
                "This might indicate incorrect credentials or a temporary service issue."
            )

        # Normal success path
        return await req.text()

    async def follow_redirects(
        self, session: Any, pw_url: str, redirect_location: str
    ) -> str:
        """Handle redirects."""
        ref = urljoin(pw_url, redirect_location)
        depth = MAX_REDIRECT_DEPTH
        stop_uri = self._session_region_config.get("redirect_uri", APP_URI)
        _LOGGER.debug("follow_redirects: stop_uri=%s start_ref=%s", stop_uri, ref)
        while not ref.startswith(stop_uri):
            if depth == 0:
                raise RedirectError(
                    f"Too many redirects during login flow (max depth: {MAX_REDIRECT_DEPTH}). "
                    "This might indicate an authentication loop."
                )
            response = await session.get(
                url=ref, headers=self._session_auth_headers, allow_redirects=False
            )
            location = response.headers.get("Location")
            _LOGGER.debug(
                "follow_redirects: GET %s → HTTP %s, Location: %s",
                ref,
                response.status,
                location,
            )

            # Check if we hit a terms and conditions page (HTTP 200 with no redirect)
            if response.status == 200 and not location:
                page_content = await response.text()
                if (
                    "termsAndConditions" in page_content
                    or '"page":"termsAndConditions"' in page_content
                ):
                    _LOGGER.error(
                        "Terms and Conditions acceptance required. "
                        "Please log in to https://www.myvolkswagen.net/ and accept the updated terms."
                    )
                    raise TermsAndConditionsError(
                        "Terms and Conditions must be accepted. "
                        "Please visit https://www.myvolkswagen.net/ to accept the updated terms and conditions, "
                        "then try logging in again."
                    )

            if not location:
                _LOGGER.warning("Failed to find next redirect location")
                raise RedirectError("Failed to find next redirect location")
            ref = urljoin(ref, location)
            depth -= 1
        return ref

    async def _get_authorization_code(self, openid_config: dict) -> tuple:
        """Run the OIDC login flow and return the callback tokens.

        For EMEA: uses response_type=code id_token token (hybrid flow) so the
        identity provider delivers access_token and id_token directly in the
        callback — usable with CARIAD BFF without a separate token exchange step.

        For NA: uses response_type=code (plain auth-code flow with PKCE).

        Returns:
            Tuple of (auth_code, id_token, access_token)
        """
        authorization_endpoint = openid_config["authorization_endpoint"]
        auth_issuer = openid_config["issuer"]

        authorization_page = await self.get_authorization_page(authorization_endpoint)

        state_token = self.extract_state_token(authorization_page)
        if not state_token:
            _LOGGER.error(
                "Unable to find valid login page. "
                "Try logging in to the portal: https://www.myvolkswagen.net/"
            )
            raise AuthenticationError("Invalid login page - missing state token")

        login_form = {
            "username": self._session_auth_username,
            "password": self._session_auth_password,
            "state": state_token,
            "action": "default",
        }
        login_url = f"{auth_issuer}/u/login?state={state_token}"

        redirect_location = await self.post_form(
            self._session,
            login_url,
            self._session_auth_headers,
            login_form,
            False,
        )

        callback_url = await self.follow_redirects(
            self._session, auth_issuer, redirect_location
        )

        parsed = urlparse(callback_url)
        all_params = {**parse_qs(parsed.query), **parse_qs(parsed.fragment)}
        _LOGGER.debug("Callback params keys: %s", list(all_params.keys()))

        auth_code = all_params.get("code", [None])[0]
        id_token = all_params.get("id_token", [None])[0]
        access_token = all_params.get("access_token", [None])[0]

        if not auth_code:
            raise AuthenticationError("No authorization code in callback URL")

        return auth_code, id_token, access_token

    def _build_session_tokens(
        self, auth_code: str, id_token: str, access_token: str
    ) -> dict:
        """Build the session token dict from the hybrid OIDC callback values."""
        try:
            payload = jwt.decode(auth_code, options={"verify_signature": False})
            _LOGGER.debug("Auth code JWT payload: %s", payload)
        except Exception:
            _LOGGER.debug("Auth code is not a JWT, continuing without decode")

        return {
            "access_token": access_token,
            "id_token": id_token,
            "token_type": "Bearer",
        }

    async def _get_authorization_code_na(self, openid_config: dict) -> str:
        """NA-specific authorization code flow using VW IdentiKit two-step login."""
        authorization_endpoint = openid_config["authorization_endpoint"]
        _LOGGER.debug(
            "NA auth: fetching authorization page endpoint=%s",
            authorization_endpoint,
        )
        # Login forms are served by the identity server (identity.na.vwgroup.io),
        # not by the base API (b-h-s.spr.us00.p.con-veh.net).
        identity_base = self._session_region_config.get(
            "identity_endpoint", "https://identity.na.vwgroup.io"
        )

        # ── Step 1: get the email identifier page ────────────────────────────
        identifier_page = await self.get_authorization_page(authorization_endpoint)

        # ── Step 2: parse IdentiKit email form ────────────────────────────────
        form_data = self._extract_identitykit_form(identifier_page)
        if not all(form_data.values()):
            raise AuthenticationError(
                f"IdentiKit form incomplete — missing fields: "
                f"{[k for k, v in form_data.items() if not v]}"
            )

        # ── Step 3: POST email ─────────────────────────────────────────────────
        identifier_url = urljoin(identity_base, form_data["form_action"])
        email_payload = {
            "_csrf": form_data["csrf"],
            "relayState": form_data["relay_state"],
            "hmac": form_data["hmac"],
            "email": self._session_auth_username,
        }
        redirect_loc = await self.post_form(
            self._session,
            identifier_url,
            self._session_auth_headers,
            email_payload,
            redirect=False,
        )
        _LOGGER.debug(
            "NA auth: email form submitted redirect=%s",
            (redirect_loc or "")[:60],
        )
        if not redirect_loc:
            raise AuthenticationError("No redirect received after email submission")

        # ── Step 4: GET password page ──────────────────────────────────────────
        password_page_url = urljoin(identity_base, redirect_loc)
        async with self._session.get(
            password_page_url, headers=self._session_auth_headers, allow_redirects=False
        ) as resp:
            if resp.status != 200:
                raise AuthenticationError(
                    f"Password page returned HTTP {resp.status} — check credentials or service availability"
                )
            password_page = await resp.text()
        _LOGGER.debug("NA auth: password page fetched status=%s", resp.status)

        # ── Step 5: parse IdentiKit password form ─────────────────────────────
        form_data2 = self._extract_identitykit_form(password_page)
        if not all(form_data2.values()):
            raise AuthenticationError(
                f"IdentiKit password form incomplete — missing: "
                f"{[k for k, v in form_data2.items() if not v]}"
            )

        # ── Step 6: POST password ──────────────────────────────────────────────
        authenticate_url = urljoin(identity_base, form_data2["form_action"])
        _LOGGER.debug(
            "_get_authorization_code_na Step 6: authenticate_url=%s relayState=%s hmac=%s csrf=%s",
            authenticate_url,
            form_data2.get("relay_state", "?")[:20],
            form_data2.get("hmac", "?")[:20],
            form_data2.get("csrf", "?")[:20],
        )
        password_payload = {
            "_csrf": form_data2["csrf"],
            "relayState": form_data2["relay_state"],
            "hmac": form_data2["hmac"],
            "email": self._session_auth_username,
            "password": self._session_auth_password,
        }
        redirect_loc2 = await self.post_form(
            self._session,
            authenticate_url,
            self._session_auth_headers,
            password_payload,
            redirect=False,
        )
        _LOGGER.debug(
            "NA auth: password form submitted redirect=%s",
            (redirect_loc2 or "")[:60],
        )
        if not redirect_loc2:
            raise AuthenticationError(
                "No redirect received after password submission — check credentials"
            )

        # Detect explicit password rejection before spending hops on follow_redirects
        if "error=login.errors.password_invalid" in redirect_loc2:
            raise AuthenticationError(
                "Password rejected by VW identity server (login.errors.password_invalid). "
                "Verify credentials and account is not locked."
            )

        # Detect throttling — too many failed login attempts
        if "login.error.throttled" in redirect_loc2:
            raise AuthenticationError(
                "VW identity server is throttling login attempts (login.error.throttled). "
                "Too many failed attempts. Wait a few minutes before retrying."
            )

        # ── Step 7: follow redirect chain to callback URL ──────────────────────
        final_url = await self.follow_redirects(
            self._session, identity_base, redirect_loc2
        )

        # ── Step 8: extract authorization code ────────────────────────────────
        code = parse_qs(urlparse(final_url).query).get("code", [None])[0]
        if not code:
            raise AuthenticationError(
                f"Authorization code not found in callback URL: {final_url!r}"
            )
        _LOGGER.debug("NA: authorization code obtained")
        return code

    async def _exchange_code_for_tokens(
        self, auth_code: str, token_endpoint: str
    ) -> Any:
        """Exchange an OAuth2 authorization code for access tokens.

        For NA PKCE flow, sends the code_verifier instead of a client_secret.
        For EMEA, sends the standard client_id and redirect_uri.

        Args:
            auth_code: The authorization code obtained from the OAuth redirect.
            token_endpoint: The token endpoint URL for the exchange.

        Returns:
            Parsed JSON response containing access_token, refresh_token,
            id_token, and expiry fields. Returns None if the exchange fails.

        Raises:
            AuthenticationError: If the token endpoint returns an error response.
        """
        token_body = {
            "client_id": self._client_id,  # Use region-specific client ID
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": self._session_region_config.get(
                "redirect_uri", APP_URI
            ),  # Use region-specific redirect URI
        }

        # Add PKCE code_verifier if available (required for NA region)
        if self._pkce_verifier:
            token_body["code_verifier"] = self._pkce_verifier
            _LOGGER.debug("Added PKCE verifier to token exchange")

        # X-QMAuth is required for token exchange but must NOT be sent during IDK refresh
        # (causes HTTP 400). See _refresh_idk_token().
        if self._session_region == "NA":
            self._session_auth_headers["X-QMAuth"] = self._calculate_xqmauth()

        _LOGGER.debug(
            "Token exchange request: endpoint=%s keys=%s has_verifier=%s",
            token_endpoint,
            list(token_body.keys()),
            bool(token_body.get("code_verifier")),
        )

        # Use direct POST for token exchange to capture error body on failure
        resp = await self._session.post(
            token_endpoint,
            headers=self._session_auth_headers,
            data=token_body,
            allow_redirects=False,
        )
        resp_text = await resp.text()
        if resp.status != 200:
            _LOGGER.debug(
                "Token exchange HTTP %s response body: %s", resp.status, resp_text[:500]
            )
            raise AuthenticationError(
                f"Token exchange failed with HTTP {resp.status}: {resp_text[:200]}"
            )

        tokens_data = json_loads(resp_text)
        _LOGGER.debug(
            "NA token exchange: status=%s has_access_token=%s",
            resp.status,
            "access_token" in tokens_data,
        )
        return tokens_data

    async def _register_mbb_client(self) -> str:
        """Register as MBB OAuth client and return xclientId.

        POSTs to MBB registration endpoint using the VW Car-Net app identity.
        Returns the assigned xclientId (client_id) string.

        Raises:
            AuthenticationError: If registration fails or response is malformed.
        """
        mbb_base = self._session_region_config.get("mbb_oauth_base_url")
        if not mbb_base:
            raise AuthenticationError("NA region config missing mbb_oauth_base_url")

        register_url = f"{mbb_base}/mobile/register/v1"
        register_body = {
            "client_name": "Android Phone",
            "platform": "google",
            "client_brand": "Volkswagen",
            "appName": "myVW",
            "appVersion": APP_VERSION_SHORT,
            "appId": ANDROID_PACKAGE_NAME,
        }
        reg_headers = {**self._session_auth_headers, "Content-Type": "application/json"}

        _LOGGER.debug("Registering as MBB OAuth client at %s", register_url)
        response = await self._session.post(
            url=register_url,
            headers=reg_headers,
            json=register_body,
        )

        if response.status != 200:
            text = await response.text()
            raise AuthenticationError(
                f"MBB client registration failed with HTTP {response.status}: {text}"
            )

        data = await response.json()
        xclient_id = data.get("client_id")
        if not xclient_id:
            raise AuthenticationError(
                f"MBB client registration response missing 'client_id': {data}"
            )

        _LOGGER.info("NA: MBB client registered, xclientId obtained")
        return xclient_id

    async def _exchange_brand_token(self, idk_access_token: str) -> dict:
        """Exchange IDK access_token for Brand token.

        Tries /login/v1/volkswagen/token first, falls back to /login/v1/vw/token on 404.
        Uses JSON body (not form-encoded) per 2026 traffic analysis.

        Args:
            idk_access_token: The IDK access token from the authorization code exchange.

        Returns:
            Dict containing brand access_token and refresh_token.

        Raises:
            AuthenticationError: If brand token exchange fails on both paths.
        """
        brand_body = {
            "token": idk_access_token,
            "grant_type": "id_token",
            "stage": "live",
            "config": MBB_BRAND_CONFIG,
        }
        brand_headers = {
            **self._session_auth_headers,
            "Content-Type": "application/json",
        }

        primary_path = "/login/v1/volkswagen/token"
        fallback_path = "/login/v1/vw/token"

        for path in (primary_path, fallback_path):
            url = f"{self._base_api}{path}"
            _LOGGER.debug("Attempting Brand token exchange at %s", url)
            response = await self._session.post(
                url=url,
                headers=brand_headers,
                json=brand_body,
            )
            if response.status == 404 and path == primary_path:
                _LOGGER.debug(
                    "Brand token primary path 404, trying fallback: %s", fallback_path
                )
                continue
            if response.status == 200:
                data = await response.json()
                _LOGGER.info("NA: Brand token obtained")
                return data
            text = await response.text()
            raise AuthenticationError(
                f"Brand token exchange failed at {path} with HTTP {response.status}: {text}"
            )

        # Should not reach here — loop always returns or raises
        raise AuthenticationError("Brand token exchange failed on all paths")

    async def _exchange_mbb_token(self, idk_id_token: str, xclient_id: str) -> dict:
        """Exchange IDK id_token for initial MBB token (form-encoded).

        NOTE: Uses id_token (not access_token) per MBB OAuth spec.
        NOTE: Body key is 'token' (non-standard) not 'id_token'.
        NOTE: Body is form-encoded (not JSON) — unlike Brand token exchange.

        Args:
            idk_id_token: The IDK id_token from the authorization code exchange.
            xclient_id: The registered MBB client ID (X-Client-ID header).

        Returns:
            Dict containing mbb access_token and refresh_token.

        Raises:
            AuthenticationError: If MBB initial token exchange fails.
        """
        mbb_base = self._session_region_config.get("mbb_oauth_base_url")
        mbb_url = f"{mbb_base}/mobile/oauth2/v1/token"
        mbb_body = {
            "grant_type": "id_token",
            "token": idk_id_token,  # Uses id_token value, key is "token"
            "scope": "sc2:fal",
        }
        mbb_headers = {
            **self._session_auth_headers,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Client-ID": xclient_id,
        }

        _LOGGER.debug("Requesting initial MBB token at %s", mbb_url)
        response = await self._session.post(
            url=mbb_url,
            headers=mbb_headers,
            data=mbb_body,
        )

        if response.status != 200:
            text = await response.text()
            raise AuthenticationError(
                f"MBB initial token exchange failed with HTTP {response.status}: {text}"
            )

        data = await response.json()
        _LOGGER.info("NA: MBB initial token obtained")
        return data

    async def _refresh_mbb_token(self, refresh_token: str, xclient_id: str) -> dict:
        """Refresh an MBB token using the refresh_token grant.

        Separate method (not embedded in login) so Phase 4 token lifecycle
        management can call it independently without re-login.

        NOTE: Body key for refresh_token value is 'token' (non-standard VW convention).

        Args:
            refresh_token: The MBB refresh_token from a previous MBB grant.
            xclient_id: The registered MBB client ID (X-Client-ID header).

        Returns:
            Dict containing refreshed access_token and refresh_token.

        Raises:
            AuthenticationError: If MBB token refresh fails.
        """
        mbb_base = self._session_region_config.get("mbb_oauth_base_url")
        mbb_url = f"{mbb_base}/mobile/oauth2/v1/token"
        refresh_body = {
            "grant_type": "refresh_token",
            "token": refresh_token,  # Key is "token" not "refresh_token" per VW spec
            "scope": "sc2:fal",
        }
        mbb_headers = {
            **self._session_auth_headers,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Client-ID": xclient_id,
        }

        _LOGGER.debug("Refreshing MBB token at %s", mbb_url)
        response = await self._session.post(
            url=mbb_url,
            headers=mbb_headers,
            data=refresh_body,
        )

        if response.status != 200:
            text = await response.text()
            raise AuthenticationError(
                f"MBB token refresh failed with HTTP {response.status}: {text}"
            )

        data = await response.json()
        _LOGGER.info("NA: MBB token refreshed")
        return data

    async def _refresh_idk_token(self) -> None:
        """Refresh the IDK access token using the stored refresh token.

        The refresh request includes the original PKCE ``code_verifier`` from
        the initial login. This is a non-standard OAuth extension required by
        the NA AZS server; omitting it causes the server to demand a
        ``client_secret`` that does not exist for this public client.

        The ``X-QMAuth`` header is intentionally NOT sent, as the server
        rejects it with HTTP 400 for this public PKCE client.

        After a successful refresh, updates ``self._na_tokens['idk']``,
        ``self._session_tokens['identity']``, and the Authorization header.

        Returns:
            None.

        Raises:
            AuthenticationError: If no refresh token is stored, the token
                endpoint is not set, or all retry attempts (up to 3) fail.
        """
        idk_entry = self._na_tokens.get("idk", {})
        refresh_token = idk_entry.get("refresh_token")
        if not refresh_token:
            raise AuthenticationError(
                "Cannot refresh IDK token: no refresh_token stored"
            )
        if not self._na_token_endpoint:
            raise AuthenticationError(
                "Cannot refresh IDK token: _na_token_endpoint not set (login not completed?)"
            )

        # NA AZS server requires the same code_verifier from the original PKCE login
        # (non-standard extension — confirmed from APK decompilation of AzsRefreshRequest)
        pkce_verifier = getattr(self, "_pkce_verifier", None) or ""
        refresh_body = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id,
            "code_verifier": pkce_verifier,
        }
        # Public PKCE client (59992128_MYVW_ANDROID) does NOT use X-QMAuth —
        # that header causes HTTP 400 "Internal Service validation failure" from b-h-s server.
        refresh_headers = {
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "x-android-package-name": ANDROID_PACKAGE_NAME,
        }

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._session.post(
                    url=self._na_token_endpoint,
                    headers=refresh_headers,
                    data=refresh_body,
                    timeout=ClientTimeout(total=TIMEOUT.seconds),
                )
                if response.status == 200:
                    tokens = await response.json()
                    now = time.time()
                    # Update NA token registry
                    self._na_tokens["idk"].update(
                        {
                            "access_token": tokens["access_token"],
                            "refresh_token": tokens.get("refresh_token", refresh_token),
                            "id_token": tokens.get(
                                "id_token", idk_entry.get("id_token")
                            ),
                            "expires_at": now + tokens.get("expires_in", 3600),
                            "issued_at": now,
                        }
                    )
                    # Mirror to session_tokens for EMEA-compatible validate_tokens()
                    self._session_tokens["identity"].update(self._na_tokens["idk"])
                    self._session_headers["Authorization"] = (
                        "Bearer " + tokens["access_token"]
                    )
                    _LOGGER.info("NA: IDK token refreshed successfully")
                    # Cascade: Brand token derived from IDK access_token; always refresh it
                    if "brand" in self._na_tokens:
                        await self._refresh_brand_token()
                    return
                text = await response.text()
                _LOGGER.warning(
                    "IDK refresh attempt %s/%s failed with HTTP %s: %s",
                    attempt,
                    max_attempts,
                    response.status,
                    text,
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                _LOGGER.warning(
                    "IDK refresh attempt %s/%s error: %s", attempt, max_attempts, exc
                )

            if attempt < max_attempts:
                await asyncio.sleep(2)

        raise AuthenticationError(
            f"IDK token refresh failed after {max_attempts} attempts"
        )

    async def _refresh_brand_token(self) -> None:
        """Refresh the Brand token by re-exchanging the current IDK access_token.

        Brand is derived from IDK, so this re-calls _exchange_brand_token().
        On exchange failure, raises AuthenticationError.

        Raises:
            AuthenticationError: If brand token re-derivation fails.
        """
        idk_access_token = self._na_tokens.get("idk", {}).get("access_token")
        if not idk_access_token:
            raise AuthenticationError(
                "Cannot refresh Brand token: no IDK access_token available"
            )

        try:
            brand_tokens = await self._exchange_brand_token(idk_access_token)
            now = time.time()
            self._na_tokens["brand"] = {
                "access_token": brand_tokens.get("access_token"),
                "refresh_token": brand_tokens.get("refresh_token"),
                "expires_at": now + brand_tokens.get("expires_in", 3600),
                "issued_at": now,
                "scopes": brand_tokens.get("scope", ""),
            }
            _LOGGER.info("NA: Brand token refreshed via IDK re-exchange")
        except AuthenticationError:
            _LOGGER.error("NA: Brand token re-derivation failed")
            raise

    async def _refresh_mbb_from_refresh_token(self) -> None:
        """Refresh the MBB token using the stored MBB refresh_token.

        On failure, falls back to re-exchange from current IDK id_token
        (_exchange_mbb_token + _refresh_mbb_token) before raising.

        Requires self._xclient_id to be set.

        Raises:
            AuthenticationError: If both MBB refresh and re-exchange fallback fail.
        """
        if not self._xclient_id:
            raise AuthenticationError("Cannot refresh MBB token: no xclientId stored")

        mbb_entry = self._na_tokens.get("mbb", {})
        refresh_token = mbb_entry.get("refresh_token")

        # Primary: refresh via stored refresh_token
        if refresh_token:
            try:
                mbb_tokens = await self._refresh_mbb_token(
                    refresh_token=refresh_token,
                    xclient_id=self._xclient_id,
                )
                now = time.time()
                self._na_tokens["mbb"].update(
                    {
                        "access_token": mbb_tokens.get("access_token"),
                        "refresh_token": mbb_tokens.get("refresh_token", refresh_token),
                        "expires_at": now + mbb_tokens.get("expires_in", 3600),
                        "issued_at": now,
                    }
                )
                _LOGGER.info("NA: MBB token refreshed via refresh_token")
                return
            except AuthenticationError as exc:
                _LOGGER.warning(
                    "NA: MBB refresh_token grant failed, trying re-exchange: %s", exc
                )

        # Fallback: re-exchange from IDK id_token
        idk_id_token = self._na_tokens.get("idk", {}).get("id_token")
        if not idk_id_token:
            raise AuthenticationError(
                "MBB refresh failed and IDK id_token unavailable for re-exchange fallback"
            )
        try:
            mbb_initial = await self._exchange_mbb_token(
                idk_id_token=idk_id_token,
                xclient_id=self._xclient_id,
            )
            mbb_working = await self._refresh_mbb_token(
                refresh_token=mbb_initial["refresh_token"],
                xclient_id=self._xclient_id,
            )
            now = time.time()
            self._na_tokens["mbb"] = {
                "access_token": mbb_working.get("access_token"),
                "refresh_token": mbb_working.get("refresh_token"),
                "expires_at": now + mbb_working.get("expires_in", 3600),
                "issued_at": now,
                "scopes": mbb_working.get("scope", "sc2:fal"),
            }
            _LOGGER.info("NA: MBB token re-exchanged from IDK id_token (fallback)")
        except AuthenticationError:
            _LOGGER.error("NA: MBB re-exchange fallback also failed")
            raise

    async def _create_na_vehicle_session(self, vin: str) -> str | None:
        """Create and cache a carnetVehicleToken for the given VIN.

        Uses ``tsp_provider`` stored in ``self._na_tokens[vin]["tsp_provider"]``
        during the garage parse in ``doLogin()``.  Valid TSP enum values are
        ``"ATC"`` (Aeris Telecommunications Corporation), ``"WCT"``
        (WirelessCar Technologies), and ``"Unknown"``.  ``"VWNA"`` and ``"VW"``
        are NOT valid TSP enum values — both return HTTP 400.  Defaults to
        ``"ATC"`` if no stored tsp_provider is found.

        When SPIN is configured the method performs a two-step flow:

        1. GET ``ss/v1/user/{userId}/challenge`` with IDK access_token as
           Bearer and x-user-id header — returns ``{"challenge": "...",
           "remainingTries": N}``.
        2. Compute ``spinHash = SHA-512(UTF-8("{challenge}.{spin}"))``.
        3. POST vehicle session with computed ``spinHash`` — validates the SPIN
           and returns the ``carnetVehicleToken``.

        When SPIN is not configured the method performs a single POST with
        ``spinHash=null``.

        On the first attempt the IDK access_token is sent as a Bearer
        Authorization header; if the server responds 401 the same ``tsp`` is
        retried with NO Authorization header (some NA environments reject the
        header).

        The resulting JWT is cached in
        ``self._na_tokens[vin]["vehicle_session"]`` with ``token``,
        ``expires_at`` (JWT ``exp`` claim), and ``issued_at`` fields.  A 5-minute
        buffer is applied so callers never receive a near-expired token.

        Token values are NEVER logged at any log level.

        Args:
            vin: Vehicle Identification Number.

        Returns:
            The carnetVehicleToken string on success, or ``None`` if the
            session POST fails or the IDK id_token is missing.
        """
        idk_entry = self._na_tokens.get("idk", {})
        idk_id_token = idk_entry.get("id_token", "")
        idk_access_token = idk_entry.get("access_token", "")
        _LOGGER.debug("NA vehicle session: creating session for vin=%s", redact(vin))

        if not idk_id_token:
            _LOGGER.warning(
                "NA: cannot create vehicle session for %s — IDK id_token missing", vin
            )
            return None

        # Parse userId from IDK id_token JWT sub claim
        try:
            claims = jwt.decode(idk_id_token, options={"verify_signature": False})
            user_id = claims.get("sub", "")
        except jwt.exceptions.InvalidTokenError as exc:
            _LOGGER.warning("NA: failed to decode IDK id_token for userId: %s", exc)
            return None

        if not user_id:
            _LOGGER.warning(
                "NA: IDK id_token has no 'sub' claim, cannot create vehicle session"
            )
            return None

        # Check cache — return cached token if not expiring within 5 minutes
        cached = self._na_tokens.get(vin, {}).get("vehicle_session")
        if cached and cached.get("expires_at", 0) > time.time() + 300:
            _LOGGER.debug("NA vehicle session: cache hit for vin=%s", redact(vin))
            return cached["token"]

        base_api = self._base_api
        # Use the vehicleId (UUID from garage response) for the session URL.
        # The APK uses {vehicleId} (UUID like "7f3e...") NOT the VIN string.
        vehicle_id = self._na_tokens.get(vin, {}).get("vehicle_id", vin)
        session_url = f"{base_api}/ss/v1/user/{user_id}/vehicle/{vehicle_id}/session"
        _LOGGER.debug("NA vehicle session: url=%s", session_url)

        # Use tspProvider from garage response (stored during doLogin)
        # Valid values: "ATC" (Aeris), "WCT" (WirelessCar), "Unknown"
        # "VWNA" and "VW" are NOT valid TSP enum values — both return HTTP 400
        tsp_value = self._na_tokens.get(vin, {}).get("tsp_provider", "ATC")
        _LOGGER.debug("NA vehicle session: using tsp=%r for %s", tsp_value, vin)

        vehicle_token: str | None = None

        def _post_vehicle_session(spin_hash: str | None) -> Any:
            """Helper returning the coroutine for a vehicle session POST."""
            body = {"idToken": idk_id_token, "tsp": tsp_value, "spinHash": spin_hash}
            auth_headers = {
                "Authorization": f"Bearer {idk_access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            return self._session.post(
                url=session_url,
                json=body,
                headers=auth_headers,
                timeout=ClientTimeout(total=TIMEOUT.seconds),
                allow_redirects=False,
            )

        async def _execute_session_post(spin_hash: str | None) -> str | None:
            """POST vehicle session and return vehicle_token or None."""
            nonlocal tsp_value
            try:
                resp = await _post_vehicle_session(spin_hash)
                status = resp.status

                if status == 401:
                    # Retry without Authorization header
                    _LOGGER.debug(
                        "NA vehicle session: 401 with auth header for tsp=%r, retrying without auth",
                        tsp_value,
                    )
                    body = {
                        "idToken": idk_id_token,
                        "tsp": tsp_value,
                        "spinHash": spin_hash,
                    }
                    no_auth_headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    }
                    resp = await self._session.post(
                        url=session_url,
                        json=body,
                        headers=no_auth_headers,
                        timeout=ClientTimeout(total=TIMEOUT.seconds),
                        allow_redirects=False,
                    )
                    status = resp.status

                if status == 200:
                    try:
                        data = await resp.json(content_type=None)
                    except (json.JSONDecodeError, ValueError) as exc:
                        body_text = await resp.text()
                        _LOGGER.warning(
                            "NA vehicle session: got 200 but body is not valid JSON: %s — body: %.300s",
                            exc,
                            body_text,
                        )
                        return None
                    _LOGGER.debug(
                        "NA vehicle session 200 response keys: %s", list(data.keys())
                    )
                    # Support both flat {"carnetVehicleToken": "..."} and wrapped {"data": {"carnetVehicleToken": "..."}}
                    payload = data.get("data") if "data" in data else data
                    raw_token = (
                        payload.get("carnetVehicleToken")
                        if isinstance(payload, dict)
                        else None
                    )
                    if raw_token is None:
                        # Also check top-level as fallback
                        raw_token = data.get("carnetVehicleToken")
                    # Handle both plain string and nested object {"token": "<jwt>"}
                    if isinstance(raw_token, dict):
                        # "f49219a" is an obfuscated field name from APK decompilation
                        # (ProGuard-minified key) — fallback when "token" key is renamed
                        tok = raw_token.get("token") or raw_token.get("f49219a")
                        _LOGGER.debug(
                            "NA vehicle session: carnetVehicleToken was a dict, extracted token"
                        )
                    else:
                        tok = raw_token
                    if tok:
                        _LOGGER.debug(
                            "NA vehicle session: token obtained for vin=%s", redact(vin)
                        )
                        _LOGGER.info(
                            "NA vehicle session created with tsp='%s'", tsp_value
                        )
                        return tok
                    _LOGGER.warning(
                        "NA vehicle session: tsp=%r got 200 but no carnetVehicleToken in response. "
                        "Response keys: %s, payload keys: %s",
                        tsp_value,
                        list(data.keys()),
                        list(payload.keys()) if isinstance(payload, dict) else payload,
                    )
                    return None
                body_text = await resp.text()
                _LOGGER.warning(
                    "NA vehicle session: tsp=%r returned %d: %s",
                    tsp_value,
                    status,
                    body_text[:300],
                )
                return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                _LOGGER.warning(
                    "NA vehicle session: exception for tsp=%r: %s", tsp_value, exc
                )
                return None

        if self._spin:
            # SPIN flow (from APK d20/i.java UserSession interceptor analysis):
            #   The AzsSession in the APK is the IDK OIDC session (not a separate carnetVehicleToken).
            #   d20.i adds:
            #     - Authorization: Bearer {idk_access_token}   ← azsSession.token.access_token
            #     - x-user-id: {userId}
            #     - x-mobile-session-id: {sessionId}
            #       ^ TBD: field name from APK analysis, not confirmed from live traffic
            #     - x-app-version: APP_VERSION
            #   The challenge endpoint (ss/v1/user/{userId}/challenge) accepts IDK access_token as
            #   Bearer when the x-user-id header is also present.
            #   PinResponse may be wrapped in {"data": {...}} or flat — code handles both.
            #
            #   Flow:
            #   1. GET challenge with IDK access_token Bearer + x-user-id header
            #   2. Compute spinHash = SHA-512(UTF-8("{challenge}.{spin}"))
            #   3. POST vehicle session with computed spinHash → carnetVehicleToken
            challenge_url = f"{base_api}/ss/v1/user/{user_id}/challenge"
            spin_hash: str | None = None
            try:
                challenge_resp = await self._session.get(
                    challenge_url,
                    headers={
                        "Authorization": f"Bearer {idk_access_token}",
                        "x-user-id": user_id,
                        "x-user-agent": "mobile-android",
                        "x-app-version": APP_VERSION,
                        "Accept": "application/json",
                    },
                    timeout=ClientTimeout(total=TIMEOUT.seconds),
                )
                challenge_status = challenge_resp.status
                if challenge_status == 200:
                    # Server wraps PinResponse: {"data": {"challenge": "<hex>", "remainingTries": N}}
                    challenge_resp_data = await challenge_resp.json()
                    _LOGGER.debug(
                        "NA vehicle session: challenge response keys: %s",
                        list(challenge_resp_data.keys()),
                    )
                    # Support both wrapped {"data": {"challenge": ...}} and flat {"challenge": ...}
                    challenge_data = (
                        challenge_resp_data.get("data") or challenge_resp_data
                    )
                    challenge_hex = challenge_data.get("challenge")
                    if challenge_hex:
                        spin_hash = self.hash_spin(challenge_hex, self._spin)
                        _LOGGER.debug(
                            "NA vehicle session: challenge obtained, spinHash computed (len=%d)",
                            len(spin_hash),
                        )
                    else:
                        _LOGGER.warning(
                            "NA vehicle session: challenge response missing 'challenge' field. "
                            "Top-level keys: %s, data keys: %s",
                            list(challenge_resp_data.keys()),
                            list(challenge_data.keys())
                            if isinstance(challenge_data, dict)
                            else challenge_data,
                        )
                        return None
                else:
                    body_text = await challenge_resp.text()
                    _LOGGER.warning(
                        "NA vehicle session: challenge GET %d: %s",
                        challenge_status,
                        body_text[:200],
                    )
                    return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                _LOGGER.warning("NA vehicle session: challenge GET failed: %s", exc)
                return None

            # POST session with computed spinHash → returns carnetVehicleToken
            vehicle_token = await _execute_session_post(spin_hash=spin_hash)
        else:
            # No SPIN configured — single session POST with spinHash=None
            vehicle_token = await _execute_session_post(spin_hash=None)

        if not vehicle_token:
            _LOGGER.warning(
                "NA: vehicle session creation failed for %s (tsp=%r)",
                vin,
                tsp_value,
            )
            return None

        # Parse JWT exp claim for cache TTL
        try:
            token_claims = jwt.decode(
                vehicle_token, options={"verify_signature": False}
            )
            expires_at = token_claims.get("exp", time.time() + 1800)
        except jwt.exceptions.InvalidTokenError as exc:
            _LOGGER.warning(
                "NA: could not decode vehicle token JWT for exp claim: %s", exc
            )
            expires_at = time.time() + 600

        if vin not in self._na_tokens:
            self._na_tokens[vin] = {}
        self._na_tokens[vin]["vehicle_session"] = {
            "token": vehicle_token,
            "expires_at": expires_at,
            "issued_at": time.time(),
        }
        return vehicle_token

    async def _fetch_rvs_endpoint(
        self,
        url: str,
        vin: str,
        rvs_headers: dict[str, str],
        label: str,
    ) -> dict | None:
        """Fetch a single RVS endpoint with retry, 401 session refresh, and 5xx backoff.

        Args:
            url: Full RVS endpoint URL.
            vin: Vehicle identification number.
            rvs_headers: Headers including vehicle token. Modified in-place on 401 refresh.
            label: Human-readable label for logging (e.g., "location", "status").

        Returns:
            Parsed JSON dict on success, None on failure.
        """
        try:
            for attempt in range(RVS_MAX_RETRIES + 1):
                resp = await self._session.get(
                    url=url,
                    headers=rvs_headers,
                    timeout=ClientTimeout(total=TIMEOUT.seconds),
                    allow_redirects=False,
                )
                _LOGGER.debug(
                    "NA vehicle data: RVS %s status=%s for vin=%s",
                    label,
                    resp.status,
                    redact(vin),
                )
                if resp.status == 401:
                    _LOGGER.debug(
                        "NA RVS %s: 401 — refreshing vehicle session and retrying",
                        label,
                    )
                    self._na_tokens.get(vin, {}).pop("vehicle_session", None)
                    self._na_rvs_cache.pop(vin, None)
                    vehicle_token = await self._create_na_vehicle_session(vin)
                    if not vehicle_token:
                        return None
                    rvs_headers["Authorization"] = f"Bearer {vehicle_token}"
                    resp = await self._session.get(
                        url=url,
                        headers=rvs_headers,
                        timeout=ClientTimeout(total=TIMEOUT.seconds),
                        allow_redirects=False,
                    )
                    _LOGGER.debug(
                        "NA RVS %s: retry after 401 returned status=%s for vin=%s",
                        label,
                        resp.status,
                        redact(vin),
                    )
                    if resp.status not in (200, 202, 204):
                        _LOGGER.warning(
                            "NA RVS %s: 401 retry failed (status=%s), giving up",
                            label,
                            resp.status,
                        )
                        return None
                if resp.status == 204:
                    return None  # 204 No Content — nothing to parse
                if resp.status in (200, 202):
                    try:
                        data = await resp.json(content_type=None)
                    except (json.JSONDecodeError, aiohttp.ContentTypeError) as exc:
                        body_preview = await resp.text()
                        _LOGGER.warning(
                            "NA RVS %s: failed to decode JSON for vin=%s: %s — body: %.200s",
                            label,
                            redact(vin),
                            exc,
                            body_preview,
                        )
                        return None
                    if isinstance(data, dict) and "data" in data:
                        data = data["data"]
                    _LOGGER.debug("NA RVS %s response: %s", label, data)
                    return data
                elif resp.status >= 500:
                    body_preview = await resp.text()
                    if attempt < RVS_MAX_RETRIES:
                        _LOGGER.warning(
                            "NA RVS %s: transient %d for %s (attempt %d/%d), retrying",
                            label,
                            resp.status,
                            redact(vin),
                            attempt + 1,
                            RVS_MAX_RETRIES + 1,
                        )
                        await asyncio.sleep(1.0)
                        continue
                    _LOGGER.warning(
                        "NA RVS %s fetch failed for %s: HTTP %d — %s",
                        label,
                        redact(vin),
                        resp.status,
                        body_preview[:200],
                    )
                else:
                    body_preview = await resp.text()
                    _LOGGER.warning(
                        "NA RVS %s fetch failed for %s: HTTP %d — %s",
                        label,
                        redact(vin),
                        resp.status,
                        body_preview[:200],
                    )
                    break  # non-5xx non-200 — do not retry
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _LOGGER.warning(
                "NA RVS %s fetch exception for %s: %s", label, redact(vin), exc
            )
        return None

    async def _get_na_vehicle_data(self, vin: str) -> dict | None:
        """Fetch NA vehicle telemetry from RVS and supplemental endpoints.

        Calls ``_create_na_vehicle_session(vin)`` to obtain a
        ``carnetVehicleToken``, then fetches:

        - ``GET {base_api}/rvs/v1/location/vehicle/{vehicle_id}`` — GPS location
        - ``GET {base_api}/rvs/v1/vehicle/{vehicle_id}`` — vehicle status / lock state
        - ``GET {base_api}/ev/v1/vehicle/{vehicle_id}/charge/summary`` — EV battery/charging (404 for non-EV)
        - ``GET {base_api}/ev/v1/vehicle/{vehicle_id}/pretripclimate/settings`` — climate settings (404 for non-EV)
        - ``GET {base_api}/remotetripstats/v1/vehicle/{vehicle_id}?type=SHORT_TERM`` — last trip stats

        On HTTP 401 from any endpoint, the cached vehicle session is
        invalidated and ``_create_na_vehicle_session`` is called once more
        before a single retry.

        Raw response bodies are logged at DEBUG level for diagnostics.

        Token values (vehicle token, IDK tokens) are NEVER logged.

        Args:
            vin: Vehicle Identification Number.

        Returns:
            A dict with keys ``"na_location"``, ``"na_status"``, ``"na_ev"``,
            ``"na_climate"``, and ``"na_trip"`` (any may be ``None`` if that
            endpoint failed or is unsupported).  Returns ``None`` only when
            vehicle session creation itself fails.
        """
        # Check RVS cache — skip API roundtrip if data is fresh
        cached = self._na_rvs_cache.get(vin)
        if cached and (time.time() - cached["fetched_at"]) < self._rvs_cache_ttl:
            _LOGGER.debug(
                "NA vehicle data: returning cached RVS data for vin=%s (age=%.1fs, ttl=%ds)",
                redact(vin),
                time.time() - cached["fetched_at"],
                self._rvs_cache_ttl,
            )
            return cached["data"]

        # Ensure IDK token is valid before proceeding
        if not await self.validate_tokens():
            _LOGGER.warning(
                "NA: validate_tokens() returned False, skipping vehicle data fetch for %s",
                redact(vin),
            )
            return None

        _LOGGER.debug("NA vehicle data: fetching data for vin=%s", redact(vin))
        vehicle_token = await self._create_na_vehicle_session(vin)
        if vehicle_token is None:
            return None

        # Parse userId from IDK id_token for RVS headers
        idk_id_token = self._na_tokens.get("idk", {}).get("id_token", "")
        decode_failed = False
        try:
            claims = jwt.decode(idk_id_token, options={"verify_signature": False})
            user_id = claims.get("sub", "")
        except jwt.exceptions.InvalidTokenError as exc:
            _LOGGER.warning(
                "NA: failed to decode IDK id_token for x-user-id header: %s", exc
            )
            user_id = ""
            decode_failed = True

        if not user_id:
            if not decode_failed:
                _LOGGER.warning(
                    "NA: IDK id_token has no 'sub' claim for vin=%s — skipping vehicle data fetch",
                    redact(vin),
                )
            return None

        # Build RVS headers (vehicle_token used in Authorization — NEVER logged)
        rvs_headers: dict = {
            "Authorization": f"Bearer {vehicle_token}",
            "x-user-id": user_id,
            "x-app-version": APP_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Add x-mobile-session-id if cached from prior session response
        session_id = (
            self._na_tokens.get(vin, {}).get("vehicle_session", {}).get("session_id")
        )
        if session_id:
            rvs_headers["x-mobile-session-id"] = session_id

        base_api = self._base_api
        # Use the vehicleId (UUID from garage) for RVS paths — same as vehicle session.
        vehicle_id = self._na_tokens.get(vin, {}).get("vehicle_id", vin)

        # Fetch location and status via deduplicated helper
        location_url = f"{base_api}/rvs/v1/location/vehicle/{vehicle_id}"
        status_url = f"{base_api}/rvs/v1/vehicle/{vehicle_id}"
        _LOGGER.debug(
            "NA vehicle data: fetching RVS for vin=%s urls=%s, %s",
            redact(vin),
            location_url,
            status_url,
        )

        location_data = await self._fetch_rvs_endpoint(
            location_url, vin, dict(rvs_headers), "location"
        )
        status_data = await self._fetch_rvs_endpoint(
            status_url, vin, dict(rvs_headers), "status"
        )

        # Fetch optional supplemental endpoints (404 expected for non-EV/non-supported vehicles)
        ev_data = await self._fetch_na_optional_endpoint(
            f"{base_api}/ev/v1/vehicle/{vehicle_id}/charge/summary",
            vin,
            dict(rvs_headers),
            "ev_charge",
        )
        climate_data = await self._fetch_na_optional_endpoint(
            f"{base_api}/ev/v1/vehicle/{vehicle_id}/pretripclimate/settings",
            vin,
            dict(rvs_headers),
            "climate",
        )
        trip_data = await self._fetch_na_optional_endpoint(
            f"{base_api}/remotetripstats/v1/vehicle/{vehicle_id}?type=SHORT_TERM",
            vin,
            dict(rvs_headers),
            "trip_stats",
        )

        # Return partial data even if one endpoint failed
        result = {
            "na_location": location_data,
            "na_status": status_data,
            "na_ev": ev_data,
            "na_climate": climate_data,
            "na_trip": trip_data,
        }
        # Only cache if vehicle session is still valid (not invalidated by a 401)
        if self._na_tokens.get(vin, {}).get("vehicle_session"):
            self._na_rvs_cache[vin] = {"data": result, "fetched_at": time.time()}
        else:
            _LOGGER.debug(
                "NA vehicle data: skipping RVS cache for vin=%s — vehicle session was invalidated during fetch",
                redact(vin),
            )
        return result

    async def _fetch_na_optional_endpoint(
        self,
        url: str,
        vin: str,
        headers: dict[str, str],
        label: str,
        *,
        _retry: bool = False,
    ) -> dict | None:
        """Fetch an optional NA endpoint, returning None on 404 (non-EV/unsupported).

        Unlike ``_fetch_rvs_endpoint``, a 404 is logged at DEBUG rather than
        WARNING since these endpoints are expected to be absent for non-EV or
        non-supported vehicles.

        On 401, the vehicle session cache is invalidated and one retry is
        attempted (consistent with ``_fetch_rvs_endpoint``). The ``_retry``
        flag prevents infinite recursion.

        Args:
            url: Full endpoint URL.
            vin: Vehicle identification number (for logging).
            headers: Auth headers including vehicle token.
            label: Human-readable label for log messages.
            _retry: Internal flag — do not pass from outside. True on the
                retry pass to prevent recursive re-entry.

        Returns:
            Parsed JSON dict on 200/202, None on 204/404 or any other
            non-2xx response.
        """
        try:
            resp = await self._session.get(
                url=url,
                headers=headers,
                timeout=ClientTimeout(total=TIMEOUT.seconds),
                allow_redirects=False,
            )
            _LOGGER.debug(
                "NA optional endpoint %s: status=%s for vin=%s",
                label,
                resp.status,
                redact(vin),
            )
            if resp.status in (200, 202):
                try:
                    data = await resp.json(content_type=None)
                except (json.JSONDecodeError, aiohttp.ContentTypeError) as exc:
                    body_preview = await resp.text()
                    _LOGGER.warning(
                        "NA optional endpoint %s: failed to decode JSON for vin=%s: %s — body: %.200s",
                        label,
                        redact(vin),
                        exc,
                        body_preview,
                    )
                    return None
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                return data
            if resp.status == 204:
                return None  # 204 No Content — nothing to parse
            if resp.status == 404:
                _LOGGER.debug(
                    "NA optional endpoint %s: 404 (not supported for vin=%s)",
                    label,
                    redact(vin),
                )
            elif resp.status == 401 and not _retry:
                _LOGGER.warning(
                    "NA optional endpoint %s: HTTP 401 (vehicle session expired) for vin=%s — refreshing and retrying once",
                    label,
                    redact(vin),
                )
                self._na_tokens.get(vin, {}).pop("vehicle_session", None)
                self._na_rvs_cache.pop(vin, None)
                vehicle_token = await self._create_na_vehicle_session(vin)
                if vehicle_token:
                    retry_headers = dict(headers)
                    retry_headers["Authorization"] = f"Bearer {vehicle_token}"
                    return await self._fetch_na_optional_endpoint(
                        url, vin, retry_headers, label, _retry=True
                    )
                _LOGGER.warning(
                    "NA optional endpoint %s: 401 session refresh failed for vin=%s",
                    label,
                    redact(vin),
                )
            elif resp.status == 401:
                _LOGGER.warning(
                    "NA optional endpoint %s: HTTP 401 (vehicle session rejected) for vin=%s "
                    "— invalidating session cache so next poll re-authenticates",
                    label,
                    redact(vin),
                )
                self._na_tokens.get(vin, {}).pop("vehicle_session", None)
                self._na_rvs_cache.pop(vin, None)
            elif resp.status == 403:
                _LOGGER.warning(
                    "NA optional endpoint %s: HTTP 403 (insufficient permissions) for vin=%s",
                    label,
                    redact(vin),
                )
            else:
                _LOGGER.warning(
                    "NA optional endpoint %s: unexpected HTTP %d for vin=%s",
                    label,
                    resp.status,
                    redact(vin),
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _LOGGER.warning(
                "NA optional endpoint %s fetch exception for %s: %s",
                label,
                redact(vin),
                exc,
            )
        return None

    # NA write commands (lock/unlock, honk/flash, EV charging, climate) #

    def _get_na_write_headers(self, vin: str) -> dict[str, str] | None:
        """Build auth headers for NA write commands from cached vehicle session.

        Reads vehicle token from ``_na_tokens[vin]["vehicle_session"]["token"]``
        and user_id from IDK id_token JWT claims (no network calls).

        Returns a headers dict suitable for PUT/POST requests to NA endpoints.
        Returns None if the vehicle session token is missing, the IDK id_token
        cannot be decoded (JWT error), or the ``sub`` claim is absent (re-login required).
        """
        vehicle_token = (
            self._na_tokens.get(vin, {}).get("vehicle_session", {}).get("token", "")
        )
        if not vehicle_token:
            _LOGGER.warning(
                "NA write headers: no vehicle session token for vin=%s — aborting command",
                redact(vin),
            )
            return None
        idk_id_token = self._na_tokens.get("idk", {}).get("id_token", "")
        try:
            claims = jwt.decode(idk_id_token, options={"verify_signature": False})
            user_id = claims.get("sub", "")
        except jwt.exceptions.InvalidTokenError as exc:
            _LOGGER.warning(
                "NA write headers: failed to decode IDK id_token for %s — aborting command (re-login required): %s",
                redact(vin),
                exc,
            )
            return None

        if not user_id:
            _LOGGER.warning(
                "NA write headers: IDK id_token has no 'sub' claim for vin=%s — aborting command",
                redact(vin),
            )
            return None

        headers: dict[str, str] = {
            "Authorization": f"Bearer {vehicle_token}",
            "x-user-id": user_id,
            "x-app-version": APP_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        session_id = (
            self._na_tokens.get(vin, {}).get("vehicle_session", {}).get("session_id")
        )
        if session_id:
            headers["x-mobile-session-id"] = session_id
        return headers

    async def _na_write_request(
        self,
        vin: str,
        url: str,
        method: str = "put",
        body: dict | None = None,
    ) -> bool:
        """Execute a NA write command (PUT or POST) with 401 retry and 429 rate-limit backoff.

        On 401 the cached vehicle session is discarded and re-created once
        before retrying. On 429 (rate limited), retries up to ``MAX_RETRIES_ON_RATE_LIMIT``
        times with exponential backoff. Other non-2xx responses are logged and
        return False.

        Args:
            vin: Vehicle Identification Number.
            url: Full endpoint URL.
            method: ``"put"`` or ``"post"``.
            body: JSON body dict (defaults to empty dict).

        Returns:
            True if HTTP response is 200, 202, or 204, False otherwise.
        """
        if not await self.validate_tokens():
            _LOGGER.warning(
                "NA write: validate_tokens() failed for vin=%s, skipping %s",
                redact(vin),
                url,
            )
            return False
        vehicle_token_value = (
            self._na_tokens.get(vin, {}).get("vehicle_session", {}).get("token")
        )
        if not vehicle_token_value or not isinstance(vehicle_token_value, str):
            _LOGGER.warning(
                "NA write: no valid vehicle session token for %s, skipping %s",
                redact(vin),
                url,
            )
            return False
        headers = self._get_na_write_headers(vin)
        if headers is None:
            return False

        if method not in ("put", "post"):
            _LOGGER.error(
                "NA write: unsupported HTTP method %r for %s — must be 'put' or 'post'",
                method,
                url,
            )
            return False
        aio_method = self._session.put if method == "put" else self._session.post
        json_body = body if body is not None else {}

        async def _do_request() -> Any:
            return await aio_method(
                url=url,
                headers=headers,
                json=json_body,
                timeout=ClientTimeout(total=TIMEOUT.seconds),
                allow_redirects=False,
            )

        try:
            resp = await _do_request()
            # Rate-limit retry (429) with exponential backoff
            if resp.status == 429:
                for attempt in range(1, MAX_RETRIES_ON_RATE_LIMIT + 1):
                    delay = min(2**attempt, 30)
                    _LOGGER.warning(
                        "NA write %s %s: rate limited (429) for vin=%s, retrying in %.0fs (attempt %d/%d)",
                        method.upper(),
                        url,
                        redact(vin),
                        delay,
                        attempt,
                        MAX_RETRIES_ON_RATE_LIMIT,
                    )
                    await asyncio.sleep(delay)
                    resp = await _do_request()
                    if resp.status != 429:
                        break
                if resp.status == 429:
                    _LOGGER.warning(
                        "NA write %s %s: rate limited after %d retries for vin=%s",
                        method.upper(),
                        url,
                        MAX_RETRIES_ON_RATE_LIMIT,
                        redact(vin),
                    )
                    return False
            if resp.status == 401:
                _LOGGER.warning(
                    "NA write %s %s: HTTP 401 (vehicle session expired) for vin=%s — refreshing and retrying once",
                    method.upper(),
                    url,
                    redact(vin),
                )
                self._na_tokens.get(vin, {}).pop("vehicle_session", None)
                vehicle_token = await self._create_na_vehicle_session(vin)
                if not vehicle_token:
                    _LOGGER.warning(
                        "NA write %s %s: 401 received but vehicle session refresh failed for vin=%s — cannot retry",
                        method.upper(),
                        url,
                        redact(vin),
                    )
                    return False
                # Rebuild ALL headers (not just Authorization) so x-mobile-session-id is fresh
                new_headers = self._get_na_write_headers(vin)
                if not new_headers:
                    _LOGGER.warning(
                        "NA write: failed to build headers after session refresh for %s",
                        redact(vin),
                    )
                    return False
                headers.clear()
                headers.update(new_headers)
                resp = await _do_request()
                if resp.status not in (200, 202, 204):
                    _LOGGER.warning(
                        "NA write %s %s: failed after 401 retry (status=%s) for vin=%s",
                        method.upper(),
                        url,
                        resp.status,
                        redact(vin),
                    )
                    if resp.status == 401:
                        self._na_tokens.get(vin, {}).pop("vehicle_session", None)
                        self._na_rvs_cache.pop(vin, None)
            elif resp.status not in (200, 202, 204):
                body_preview = await resp.text()
                _LOGGER.warning(
                    "NA write %s %s: non-2xx response (status=%s) for vin=%s — body: %.200s",
                    method.upper(),
                    url,
                    resp.status,
                    redact(vin),
                    body_preview,
                )
            _LOGGER.debug(
                "NA write %s %s: status=%s for vin=%s",
                method.upper(),
                url,
                resp.status,
                redact(vin),
            )
            return resp.status in (200, 202, 204)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _LOGGER.warning(
                "NA write %s %s failed for %s: %s",
                method.upper(),
                url,
                redact(vin),
                exc,
            )
            return False

    async def lock_na(self, vin: str, action: str = "lock") -> bool:
        """Remote lock or unlock via NA ``/lockunlock/v1/`` endpoint.

        Args:
            vin: Vehicle Identification Number.
            action: ``"lock"`` or ``"unlock"``.

        Returns:
            True on success (2xx), False otherwise.
        """
        if action not in ("lock", "unlock"):
            _LOGGER.error(
                "lock_na: unsupported action %r for vin=%s — must be 'lock' or 'unlock'",
                action,
                redact(vin),
            )
            return False
        vehicle_id = self._na_tokens.get(vin, {}).get("vehicle_id", vin)
        url = f"{self._base_api}/lockunlock/v1/vehicle/{vehicle_id}"
        return await self._na_write_request(
            vin, url, method="put", body={"action": action}
        )

    async def honk_and_flash_na(self, vin: str) -> bool:
        """Remote honk and flash via NA ``/honkflash/v1/`` endpoint.

        Returns:
            True on success (2xx), False otherwise.
        """
        vehicle_id = self._na_tokens.get(vin, {}).get("vehicle_id", vin)
        url = f"{self._base_api}/honkflash/v1/vehicle/{vehicle_id}"
        return await self._na_write_request(vin, url, method="put", body={})

    async def start_charging_na(self, vin: str) -> bool:
        """Start EV charging via NA ``/ev/v1/.../charging/start`` endpoint."""
        vehicle_id = self._na_tokens.get(vin, {}).get("vehicle_id", vin)
        url = f"{self._base_api}/ev/v1/vehicle/{vehicle_id}/charging/start"
        return await self._na_write_request(vin, url, method="post", body={})

    async def stop_charging_na(self, vin: str) -> bool:
        """Stop EV charging via NA ``/ev/v1/.../charging/stop`` endpoint."""
        vehicle_id = self._na_tokens.get(vin, {}).get("vehicle_id", vin)
        url = f"{self._base_api}/ev/v1/vehicle/{vehicle_id}/charging/stop"
        return await self._na_write_request(vin, url, method="post", body={})

    async def start_climatisation_na(self, vin: str) -> bool:
        """Start pre-trip climate via NA ``/ev/v1/.../pretripclimate/start`` endpoint."""
        vehicle_id = self._na_tokens.get(vin, {}).get("vehicle_id", vin)
        url = f"{self._base_api}/ev/v1/vehicle/{vehicle_id}/pretripclimate/start"
        return await self._na_write_request(vin, url, method="post", body={})

    async def stop_climatisation_na(self, vin: str) -> bool:
        """Stop pre-trip climate via NA ``/ev/v1/.../pretripclimate/stop`` endpoint."""
        vehicle_id = self._na_tokens.get(vin, {}).get("vehicle_id", vin)
        url = f"{self._base_api}/ev/v1/vehicle/{vehicle_id}/pretripclimate/stop"
        return await self._na_write_request(vin, url, method="post", body={})

    async def _login_na(self) -> bool:
        """Perform the NA-specific OAuth2 + PKCE login flow.

        Uses the b-h-s base API OIDC endpoints with client ID
        59992128_MYVW_ANDROID (public client, no secret). Obtains an IDK
        access token and refresh token via authorization code exchange.

        The flow:
        1. Generate PKCE code_verifier and code_challenge.
        2. GET authorize endpoint to obtain the login form.
        3. POST credentials to the identity provider form action.
        4. Follow redirects to extract the authorization code.
        5. Exchange code for tokens at the base API token endpoint.

        Returns:
            True if login succeeded and tokens were stored, False otherwise.

        Raises:
            AuthenticationError: If credential submission or code extraction fails.
                Wraps ``RequestError`` (network/HTTP failures) and ``KeyError``
                (missing response fields) with actionable guidance —
                e.g. "verify country='US' and credentials are correct".
            RedirectError: If the OAuth redirect chain produces an unexpected URL.
        """
        try:
            # Clear cookies and reset headers (same as _login())
            self._clear_cookies()
            self._session_headers = HEADERS_SESSION.copy()
            self._session_auth_headers = HEADERS_AUTH.copy()

            # NA uses PKCE: code_verifier replaces client_secret at token exchange
            self._pkce_verifier = self._generate_pkce_verifier()
            self._pkce_challenge = self._generate_pkce_challenge(self._pkce_verifier)
            _LOGGER.debug(
                "NA login: PKCE challenge created verifier=%s challenge=%s",
                redact(self._pkce_verifier),
                redact(self._pkce_challenge),
            )

            # Get OpenID config (routes to identity.na.vwgroup.io for NA)
            openid_config = await self.get_openid_config()
            token_endpoint = openid_config["token_endpoint"]
            self._na_token_endpoint = token_endpoint  # persist for Phase 4 IDK refresh
            _LOGGER.debug(
                "NA login: OpenID config fetched token_endpoint=%s",
                openid_config.get("token_endpoint", "?"),
            )

            # Get authorization code via IdentiKit two-step flow
            auth_code = await self._get_authorization_code_na(openid_config)

            # Exchange code for tokens (X-QMAuth header injected inside for NA)
            tokens = await self._exchange_code_for_tokens(auth_code, token_endpoint)
            _LOGGER.debug(
                "NA login: token exchange complete access_token=%s",
                redact(tokens.get("access_token")),
            )

            # Validate token structure
            required_keys = ["access_token", "id_token", "token_type"]
            if not all(key in tokens for key in required_keys):
                _LOGGER.error(
                    "NA token exchange returned invalid response. Missing keys. Got: %s",
                    list(tokens.keys()),
                )
                return False

            # Store IDK token to session_tokens for validate_tokens() compatibility (COMPAT-04)
            self._session_tokens["identity"] = tokens
            self._session_headers["Authorization"] = (
                "Bearer " + self._session_tokens["identity"]["access_token"]
            )

            _LOGGER.info("NA: IDK token obtained and stored")

            # Populate NA token registry with IDK tokens
            self._na_tokens["idk"] = {
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token"),
                "id_token": tokens["id_token"],
                "issued_at": time.time(),
                "expires_at": tokens.get("expires_in", 3600) + time.time(),
                "scopes": tokens.get("scope", ""),
            }

            try:
                # --- Brand token exchange ---
                brand_tokens = await self._exchange_brand_token(tokens["access_token"])
                self._na_tokens["brand"] = {
                    "access_token": brand_tokens.get("access_token"),
                    "refresh_token": brand_tokens.get("refresh_token"),
                    "issued_at": time.time(),
                    "expires_at": brand_tokens.get("expires_in", 3600) + time.time(),
                    "scopes": brand_tokens.get("scope", ""),
                }
                _LOGGER.info("NA: Brand token stored")

                # --- MBB client registration (skip if caller injected xclientId) ---
                if self._xclient_id is None:
                    self._xclient_id = await self._register_mbb_client()
                    # Fire callback only for newly registered xclientId
                    if self._xclient_id_callback is not None:
                        self._xclient_id_callback(self._xclient_id)
                else:
                    _LOGGER.debug(
                        "NA: Using caller-provided xclientId, skipping registration"
                    )

                # --- MBB initial token grant ---
                mbb_initial = await self._exchange_mbb_token(
                    idk_id_token=tokens["id_token"],
                    xclient_id=self._xclient_id,
                )

                # --- Immediate MBB refresh (working token is the refreshed one) ---
                mbb_working = await self._refresh_mbb_token(
                    refresh_token=mbb_initial["refresh_token"],
                    xclient_id=self._xclient_id,
                )
                self._na_tokens["mbb"] = {
                    "access_token": mbb_working.get("access_token"),
                    "refresh_token": mbb_working.get("refresh_token"),
                    "issued_at": time.time(),
                    "expires_at": mbb_working.get("expires_in", 3600) + time.time(),
                    "scopes": mbb_working.get("scope", "sc2:fal"),
                }
                _LOGGER.info("NA: MBB token obtained and immediately refreshed")

                self._na_auth_level = "full"
                _LOGGER.info("NA: Full three-token authentication complete")

            except (AuthenticationError, RequestError) as brand_mbb_error:
                _LOGGER.warning(
                    "NA: Brand/MBB token acquisition failed, continuing with IDK-only: %s",
                    brand_mbb_error,
                )
                self._na_auth_level = "idk_only"

            self._session_logged_in = True
            return True

        except (AuthenticationError, RedirectError):
            # Authentication failures already carry actionable messages — propagate to caller
            self._session_logged_in = False
            raise
        except RequestError as error:
            self._session_logged_in = False
            raise AuthenticationError(
                f"NA login failed: {error}. Verify country='US' and that credentials are correct."
            ) from error
        except KeyError as error:
            self._session_logged_in = False
            raise AuthenticationError(
                f"NA login failed — unexpected API response structure: {error}. "
                "This may indicate a VW API change."
            ) from error
        except client_exceptions.ClientError as error:
            _LOGGER.error("NA network error during login: %s", error)
            self._session_logged_in = False
            return False
        except (TypeError, ValueError, AttributeError, asyncio.TimeoutError) as error:
            _LOGGER.error("NA unexpected error during login: %s", error, exc_info=True)
            self._session_logged_in = False
            return False

    async def _login(self) -> bool:
        """Login function.

        Returns:
            True if login successful, False otherwise
        """
        # Route NA users to region-specific login flow
        if self._session_region == "NA":
            return await self._login_na()

        try:
            # Clear cookies and reset headers
            self._clear_cookies()
            self._session_headers = HEADERS_SESSION.copy()
            self._session_auth_headers = HEADERS_AUTH.copy()

            # Get OpenID configuration (authorization_endpoint, issuer)
            openid_config = await self.get_openid_config()

            # Get authorization code and hybrid tokens from login flow
            auth_code, id_token, access_token = await self._get_authorization_code(
                openid_config
            )

            # Build session tokens from hybrid flow response (no server-side exchange needed)
            tokens = self._build_session_tokens(auth_code, id_token, access_token)

            # Validate token structure
            required_keys = ["access_token", "id_token", "token_type"]
            if not all(key in tokens for key in required_keys):
                _LOGGER.error(
                    "Invalid token response. Missing required keys. Got: %s",
                    list(tokens.keys()),
                )
                self._session_logged_in = False
                return False

            self._session_tokens["identity"] = tokens
            self._session_headers["Authorization"] = (
                "Bearer " + self._session_tokens["identity"]["access_token"]
            )
            _LOGGER.debug("Successfully stored authentication tokens")
            self._session_logged_in = True
            return True

        # Note: EMEA login catches AuthenticationError and returns False, while NA re-raises it.
        except (AuthenticationError, RequestError, RedirectError) as error:
            _LOGGER.error("Authentication error during login: %s", error)
            self._session_logged_in = False
            return False
        except client_exceptions.ClientError as error:
            _LOGGER.error("Network error during login: %s", error)
            self._session_logged_in = False
            return False
        except KeyError as error:
            _LOGGER.error("Missing required data during login: %s", error)
            self._session_logged_in = False
            return False
        except (TypeError, ValueError, AttributeError, asyncio.TimeoutError) as error:
            _LOGGER.error("Unexpected error during login: %s", error, exc_info=True)
            self._session_logged_in = False
            return False

    async def _handle_action_result(self, response_raw: Any) -> Any:
        response = await response_raw.json(loads=json_loads)
        if not response:
            raise APIError("Invalid or no response from action endpoint")
        request_id = response.get("data", {}).get("requestID", 0)
        _LOGGER.debug("Request returned with request id: %s", request_id)
        return {"id": str(request_id)}

    async def terminate(self) -> None:
        """Log out from connect services."""
        _LOGGER.info("Initiating logout")
        await self.logout()

    async def logout(self) -> None:
        """Logout, revoke tokens."""
        self._session_headers.pop("Authorization", None)

        if self._session_logged_in:
            if self._session_tokens.get("identity", {}).get("refresh_token"):
                _LOGGER.info("Revoking Identity Refresh Token")
                params = {"token": self._session_tokens["identity"]["refresh_token"]}
                await self.post(
                    f"{self._base_api}/auth/v1/idk/oidc/revoke", data=params
                )

    # HTTP methods to API
    async def _request(
        self,
        method: str,
        url: str,
        return_raw: bool = False,
        _retry_401: bool = False,
        _no_retry: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Perform a query to the VW-Group API with retry on 429 and transient errors."""
        _LOGGER.debug('HTTP %s "%s"', method, url)
        if kwargs.get("json", None):
            _LOGGER.debug("Request payload: %s", kwargs.get("json", None))

        attempt = 0

        while True:
            try:
                async with self._session.request(
                    method,
                    url,
                    headers=self._session_headers,
                    timeout=ClientTimeout(total=TIMEOUT.seconds),
                    cookies=self._jarCookie,
                    raise_for_status=False,
                    **kwargs,
                ) as response:
                    # NA inline 401 retry (Phase 4 — unchanged)
                    if (
                        response.status == 401
                        and self._session_region == "NA"
                        and not _retry_401
                    ):
                        _LOGGER.debug(
                            "NA: Got 401 on %s, attempting inline token refresh and retry",
                            url,
                        )
                        try:
                            token_type = self._classify_endpoint(url)
                            if token_type == "idk":
                                await self._refresh_idk_token()
                            elif token_type == "brand":
                                await self._refresh_brand_token()
                            elif token_type == "mbb":
                                await self._refresh_mbb_from_refresh_token()
                        except (AuthenticationError, ValueError) as refresh_exc:
                            _LOGGER.warning(
                                "NA: Inline token refresh failed for 401: %s",
                                refresh_exc,
                            )
                        else:
                            return await self._request(
                                method,
                                url,
                                return_raw=return_raw,
                                _retry_401=True,
                                **kwargs,
                            )

                    # Phase 5: 429 handling BEFORE raise_for_status
                    if (
                        response.status == 429
                        and not _no_retry
                        and attempt < MAX_RETRIES_ON_RATE_LIMIT
                    ):
                        retry_after_raw = response.headers.get("Retry-After")
                        if retry_after_raw:
                            try:
                                delay = max(float(retry_after_raw), 1.0)
                            except (ValueError, TypeError):
                                delay = float(2**attempt)
                        else:
                            delay = float(2**attempt)  # 1s, 2s, 4s
                        attempt += 1
                        self._is_throttled = True
                        self._service_status["throttled"] = True
                        _LOGGER.warning(
                            "Rate limited, retrying in %.0fs (attempt %d/%d)",
                            delay,
                            attempt,
                            MAX_RETRIES_ON_RATE_LIMIT,
                        )
                        await asyncio.sleep(delay)
                        continue  # retry the while loop

                    # All retries exhausted (or _no_retry): surface the error
                    response.raise_for_status()

                    # Successful response — reset throttle state
                    self._is_throttled = False
                    self._service_status["throttled"] = False

                    # Update cookie jar
                    if self._jarCookie is not None:
                        self._jarCookie.update(response.cookies)
                    else:
                        self._jarCookie = response.cookies

                    # Update service status
                    await self.update_service_status(url, response.status)

                    try:
                        if response.status == 204:
                            if return_raw:
                                res = response
                            else:
                                res = {"status_code": response.status}
                        elif 200 <= response.status < 300:
                            res = await response.json(loads=json_loads)
                        else:
                            res = {}
                            _LOGGER.debug(
                                "Not success status code [%s]",
                                response.status,
                            )
                    except (
                        json.JSONDecodeError,
                        aiohttp.ContentTypeError,
                        KeyError,
                        ValueError,
                    ) as exc:
                        res = {}
                        _LOGGER.warning(
                            "Request to '%s' failed to parse response [status %s]: %s",
                            url,
                            response.status,
                            exc,
                            exc_info=True,
                        )
                        if return_raw:
                            return response
                        return res

                    _LOGGER.debug(
                        'Request for "%s" returned with status code [%s]',
                        url,
                        response.status,
                    )

                    if return_raw:
                        res = response
                    return res

            except (
                client_exceptions.ClientConnectionError,
                client_exceptions.ServerTimeoutError,
            ) as net_err:
                if _no_retry or attempt >= MAX_RETRIES_ON_RATE_LIMIT:
                    await self.update_service_status(url, 1000)
                    raise
                delay = float(2**attempt)
                attempt += 1
                _LOGGER.warning(
                    "Transient network error, retrying in %.0fs (attempt %d/%d): %s",
                    delay,
                    attempt,
                    MAX_RETRIES_ON_RATE_LIMIT,
                    net_err,
                )
                await asyncio.sleep(delay)
                # continue is implicit — while loop wraps the try/except

            except client_exceptions.ClientResponseError as httperror:
                await self.update_service_status(url, httperror.status)
                raise

            except (
                TypeError,
                ValueError,
                asyncio.TimeoutError,
                aiohttp.ClientError,
            ) as error:
                await self.update_service_status(url, 1000)
                raise

    async def get(self, url: str, vin: str = "", tries: int = 0) -> Any:
        """Perform a get query."""
        try:
            return await self._request(METH_GET, url)
        except client_exceptions.ClientResponseError as error:
            if error.status == 400:
                _LOGGER.error(
                    'Got HTTP 400 "Bad Request" from server, this request might be malformed or not implemented'
                    " correctly for this vehicle"
                )
            elif error.status == 401:
                _LOGGER.warning(
                    'Received "unauthorized" error while fetching data: %s', error
                )
                self._session_logged_in = False
            elif error.status == 429:
                # Retry exhausted in _request() — surface as Throttled state
                return {"state": "Throttled"}
            elif error.status == 500:
                _LOGGER.debug(
                    "Got HTTP 500 from server, service might be temporarily unavailable"
                )
            elif error.status == 502:
                _LOGGER.debug(
                    "Got HTTP 502 from server, this request might not be supported for this vehicle"
                )
            else:
                _LOGGER.error("Got unhandled error from server: %s", error.status)
            return {"status_code": error.status}

    async def post(
        self,
        url: str,
        vin: str = "",
        tries: int = 0,
        return_raw: bool = False,
        **data: Any,
    ) -> Any:
        """Perform a post query."""
        if data:
            return await self._request(METH_POST, url, return_raw=return_raw, **data)
        return await self._request(METH_POST, url, return_raw=return_raw)

    async def put(
        self,
        url: str,
        vin: str = "",
        tries: int = 0,
        return_raw: bool = False,
        **data: Any,
    ) -> Any:
        """Perform a put query."""
        if data:
            return await self._request(METH_PUT, url, return_raw=return_raw, **data)
        return await self._request(METH_PUT, url, return_raw=return_raw)

    # Update data for all Vehicles
    async def update(self) -> bool:
        """Update status."""
        async with self._update_lock:
            if not self.logged_in:
                if not await self._login():
                    _LOGGER.warning("Login for %s account failed!", BRAND)
                    return False
            try:
                if not await self.validate_tokens():
                    _LOGGER.info(
                        "Session expired. Initiating new login for %s account", BRAND
                    )
                    if not await self.doLogin():
                        _LOGGER.warning("Login for %s account failed!", BRAND)
                        raise AuthenticationError(f"Login for {BRAND} account failed")
                else:
                    _LOGGER.debug("Going to call vehicle updates")
                    # Get all Vehicle objects and update in parallel
                    updatelist = [vehicle.update() for vehicle in self.vehicles]
                    # Wait for all data updates to complete
                    await asyncio.gather(*updatelist)

                    return True
            except (
                OSError,
                LookupError,
                ValueError,
                json.JSONDecodeError,
                AuthenticationError,
                APIError,
                RequestError,
                client_exceptions.ClientError,
                asyncio.TimeoutError,
            ) as error:
                _LOGGER.warning("Could not update information: %s", error)
            return False

    async def getPendingRequests(self, vin: str) -> Any:
        """Get status information for pending requests."""
        if not await self.validate_tokens():
            return False
        try:
            response = await self.get(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/pendingrequests"
            )

            if response:
                response["refreshTimestamp"] = datetime.now(UTC)
                return response

        except (
            client_exceptions.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning(
                "Could not fetch information for pending requests, error: %s", error
            )
        return None

    async def getOperationList(self, vin: str) -> Any:
        """Collect operationlist for VIN, supported/licensed functions."""
        if not await self.validate_tokens():
            return False
        try:
            response = await self.get(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/capabilities", ""
            )
            if response.get("capabilities", False):
                data = response
            elif response.get("status_code", {}):
                _LOGGER.warning(
                    "Could not fetch operation list, HTTP status code: %s",
                    response.get("status_code"),
                )
                data = response
            else:
                _LOGGER.info("Could not fetch operation list: %s", response)
                data = {"error": "unknown"}
        except (
            client_exceptions.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning("Could not fetch operation list, error: %s", error)
            data = {"error": "unknown"}
        return data

    async def getSelectiveStatus(self, vin: str, services: list[str]) -> Any:
        """Get status information for specified services."""
        if not await self.validate_tokens():
            return False
        try:
            response = await self.get(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/selectivestatus?jobs={','.join(services)}",
                "",
            )

            for service in services:
                if not response.get(service):
                    _LOGGER.debug(
                        "Did not receive return data for requested service %s. (This is expected for several service/car combinations)",
                        service,
                    )

            if response:
                response.update({"refreshTimestamp": datetime.now(UTC)})
                return response

        except (
            client_exceptions.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning("Could not fetch selectivestatus, error: %s", error)
        return None

    async def getVehicleData(self, vin: str) -> Any:
        """Get car information like VIN, nickname, etc."""
        if not await self.validate_tokens():
            return False
        try:
            response = await self.get(f"{self._base_api}/vehicle/v2/vehicles", "")

            for vehicle in response.get("data") or []:
                if vehicle.get("vin") == vin:
                    return {"vehicle": vehicle}

            _LOGGER.warning("Could not fetch vehicle data for vin %s", vin)

        except (
            client_exceptions.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning("Could not fetch vehicle data, error: %s", error)
        return None

    async def getParkingPosition(self, vin: str) -> Any:
        """Get information about the parking position."""
        if not await self.validate_tokens():
            return False
        try:
            response = await self.get(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/parkingposition", ""
            )

            if "data" in response:
                return {"isMoving": False, "parkingposition": response["data"]}
            if response.get("status_code", {}):
                if response.get("status_code", 0) == 204:
                    _LOGGER.debug(
                        "Seems car is moving, HTTP 204 received from parkingposition"
                    )
                    return {"isMoving": True, "parkingposition": {}}

                _LOGGER.warning(
                    "Could not fetch parkingposition, HTTP status code: %s",
                    response.get("status_code"),
                )
            else:
                _LOGGER.info(
                    "Unhandled error while trying to fetch parkingposition data"
                )
        except (
            client_exceptions.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning("Could not fetch parkingposition, error: %s", error)
        return None

    async def getTripLast(self, vin: str) -> Any:
        """Get car information like VIN, nickname, etc."""
        if not await self.validate_tokens():
            return False
        try:
            response = await self.get(
                f"{self._base_api}/vehicle/v1/trips/{vin}/shortterm/last", ""
            )
            if "data" in response:
                return {"trip_last": response["data"]}

            if response.get("status_code", 0) in [404, 502]:
                _LOGGER.debug("No last trip data available for this vehicle")
            else:
                _LOGGER.warning(
                    "Could not fetch last trip data, server response: %s", response
                )

        except (
            client_exceptions.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning("Could not fetch last trip data, error: %s", error)
        return None

    async def getTripRefuel(self, vin: str) -> Any:
        """Get information about the trip since last refuel"""
        if not await self.validate_tokens():
            return False
        try:
            response = await self.get(
                f"{self._base_api}/vehicle/v1/trips/{vin}/cyclic/last", ""
            )
            if "data" in response:
                return {"trip_refuel": response["data"]}

            if response.get("status_code", 0) in [404, 502]:
                _LOGGER.debug("No refuel trip data available for this vehicle")
            else:
                _LOGGER.warning(
                    "Could not fetch refuel trip data, server response: %s", response
                )

        except (
            client_exceptions.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning("Could not fetch last trip data, error: %s", error)
        return None

    async def getTripLongterm(self, vin: str) -> Any:
        """Get information about the trip last longterm"""
        if not await self.validate_tokens():
            return False
        try:
            response = await self.get(
                f"{self._base_api}/vehicle/v1/trips/{vin}/longterm/last", ""
            )
            if "data" in response:
                return {"trip_longterm": response["data"]}

            if response.get("status_code", 0) in [404, 502]:
                _LOGGER.debug("No longterm trip data available for this vehicle")
            else:
                _LOGGER.warning(
                    "Could not fetch longterm trip data, server response: %s", response
                )

        except (
            client_exceptions.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning("Could not fetch last trip data, error: %s", error)
        return None

    async def wakeUpVehicle(self, vin: str) -> Any:
        """Wake up vehicle to send updated data to VW Backend."""
        if not await self.validate_tokens():
            return False
        try:
            return await self.post(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/vehiclewakeuptrigger",
                json={},
                return_raw=True,
            )

        except (
            client_exceptions.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
        ) as error:
            _LOGGER.warning("Could not refresh the data, error: %s", error)
        return None

    async def get_request_status(
        self, vin: str, requestId: str, actionId: str = ""
    ) -> Any:
        """Return status of a request ID for a given section ID."""
        if self.logged_in is False:
            if not await self.doLogin():
                _LOGGER.warning("Login for %s account failed!", BRAND)
                raise AuthenticationError(f"Login for {BRAND} account failed")
        try:
            if not await self.validate_tokens():
                _LOGGER.info(
                    "Session expired. Initiating new login for %s account", BRAND
                )
                if not await self.doLogin():
                    _LOGGER.warning("Login for %s account failed!", BRAND)
                    raise AuthenticationError(f"Login for {BRAND} account failed")

            response = await self.getPendingRequests(vin)
            if not response:
                return "Unknown"

            requests = response.get("data", [])
            result = None
            for request in requests:
                if request.get("id", "") == requestId:
                    result = request.get("status")

            # Translate status messages to meaningful info
            if result in ("in_progress", "queued", "fetched"):
                status = "In Progress"
            elif result in ("request_fail", "failed"):
                status = "Failed"
            elif result == "unfetched":
                status = "No response"
            elif result in ("request_successful", "successful"):
                status = "Success"
            elif result == "fail_ignition_on":
                status = "Failed because ignition is on"
            else:
                status = str(result) if result is not None else "Unknown"
        except Exception as error:
            _LOGGER.warning("Failure during get request status: %s", error)
            raise RequestError(f"Failure during get request status: {error}") from error
        else:
            return status

    async def check_spin_state(self) -> bool:
        """Determine SPIN state to prevent lockout due to wrong SPIN."""
        result = await self.get(f"{self._base_api}/vehicle/v1/spin/state")
        remainingTries = result.get("remainingTries", None)
        if remainingTries is None:
            raise SPINError("Couldn't determine S-PIN state")

        if remainingTries < 3:
            raise SPINError(
                "Remaining tries for S-PIN is < 3. Bailing out for security reasons. "
                "To resume operation, please make sure the correct S-PIN has been set in the integration "
                "and then use the correct S-PIN once via the Volkswagen app."
            )

        return True

    async def setClimater(
        self, vin: str, data: dict[str, Any], action: bool | str
    ) -> Any:
        """Execute climatisation actions."""
        action = "start" if action else "stop"
        try:
            response_raw = await self.post(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/climatisation/{action}",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(f"Unknown error during setClimater: {str(e)}") from e

    async def setClimaterSettings(self, vin: str, data: dict[str, Any]) -> Any:
        """Execute climatisation settings."""
        try:
            response_raw = await self.put(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/climatisation/settings",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(f"Unknown error during setClimaterSettings: {str(e)}") from e

    async def setAuxiliary(
        self, vin: str, data: dict[str, Any], action: bool | str
    ) -> Any:
        """Execute auxiliary climatisation actions."""
        action = "start" if action else "stop"
        try:
            response_raw = await self.post(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/auxiliaryheating/{action}",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(f"Unknown error during setAuxiliary: {str(e)}") from e

    async def setWindowHeater(self, vin: str, action: bool | str) -> Any:
        """Execute window heating actions."""
        action = "start" if action else "stop"
        try:
            response_raw = await self.post(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/windowheating/{action}",
                json={},
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(f"Unknown error during setWindowHeater: {str(e)}") from e

    async def setCharging(self, vin: str, action: bool | str) -> Any:
        """Execute charging actions."""
        action = "start" if action else "stop"
        try:
            response_raw = await self.post(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/charging/{action}",
                json={},
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(f"Unknown error during setCharging: {str(e)}") from e

    async def setChargingSettings(self, vin: str, data: dict[str, Any]) -> Any:
        """Execute charging actions."""
        try:
            response_raw = await self.put(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/charging/settings",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(f"Unknown error during setChargingSettings: {str(e)}") from e

    async def setChargingCareModeSettings(self, vin: str, data: dict[str, Any]) -> Any:
        """Execute battery care mode actions."""
        try:
            response_raw = await self.put(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/charging/care/settings",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(
                f"Unknown error during setChargingCareModeSettings: {str(e)}"
            ) from e

    async def setReadinessBatterySupport(self, vin: str, data: dict[str, Any]) -> Any:
        """Execute readiness battery support actions."""
        try:
            response_raw = await self.put(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/readiness/batterysupport",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(
                f"Unknown error during setReadinessBatterySupport: {str(e)}"
            ) from e

    async def setDepartureProfiles(self, vin: str, data: dict[str, Any]) -> Any:
        """Execute departure timers actions."""
        try:
            response_raw = await self.put(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/departure/profiles",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(
                f"Unknown error during setDepartureProfiles: {str(e)}"
            ) from e

    async def setClimatisationTimers(self, vin: str, data: dict[str, Any]) -> Any:
        """Execute climatisation timers actions."""
        try:
            response_raw = await self.put(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/climatisation/timers",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(
                f"Unknown error during setClimatisationTimers: {str(e)}"
            ) from e

    async def setAuxiliaryHeatingTimers(self, vin: str, data: dict[str, Any]) -> Any:
        """Execute auxiliary heating timers actions."""
        try:
            response_raw = await self.put(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/auxiliaryheating/timers",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(
                f"Unknown error during setAuxiliaryHeatingTimers: {str(e)}"
            ) from e

    async def setDepartureTimers(self, vin: str, data: dict[str, Any]) -> Any:
        """Execute departure timers actions."""
        try:
            response_raw = await self.put(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/departure/timers",
                json=data,
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(f"Unknown error during setDepartureTimers: {str(e)}") from e

    async def setLock(self, vin: str, lock: bool | str, spin: str) -> Any:
        """Remote lock and unlock actions."""
        await self.check_spin_state()
        action = "lock" if lock else "unlock"
        try:
            response_raw = await self.post(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/access/{action}",
                json={"spin": spin},
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(f"Unknown error during setLock: {str(e)}") from e

    async def setHonkAndFlash(self, vin: str, position: dict[str, Any]) -> Any:
        """Remote Honk and Flash actions."""
        await self.check_spin_state()
        try:
            response_raw = await self.post(
                f"{self._base_api}/vehicle/v1/vehicles/{vin}/honkandflash",
                json={
                    "userPosition": {
                        "longitude": position["lng"],
                        "latitude": position["lat"],
                    },
                    "mode": "flash",
                    "duration_s": 15,
                },
                return_raw=True,
            )
            return await self._handle_action_result(response_raw)
        except Exception as e:
            raise APIError(f"Unknown error during setHonkAndFlash: {str(e)}") from e

    # Token handling #
    def _is_token_expiring(
        self, entry: dict, now: float, window_seconds: float = 900
    ) -> bool:
        """Return True if token entry expires within window_seconds from now.

        Uses expires_at if present. Falls back to issued_at + assumed lifetime
        (3600s for access tokens) if expires_at is missing.

        Args:
            entry: Token dict with optional 'expires_at' and 'issued_at' keys.
            now: Current Unix timestamp.
            window_seconds: Seconds before expiry to consider token "expiring". Default: 900 (15 min).

        Returns:
            True if token will expire within window_seconds, False otherwise.
        """
        expires_at = entry.get("expires_at")
        if expires_at is None:
            issued_at = entry.get("issued_at", now)
            expires_at = issued_at + 3600  # assumed 1h lifetime if unknown
        return (expires_at - now) <= window_seconds

    async def _validate_na_tokens(self) -> bool:
        """Proactively refresh NA tokens expiring within 15 minutes.

        Checks IDK, Brand, and MBB token expiry. IDK refresh cascades to Brand
        refresh automatically (Brand is derived from IDK access_token).
        MBB is refreshed independently.

        For idk_only auth level: only IDK is checked and refreshed.

        Returns:
            True if all available tokens are valid after any needed refresh.
            False if a critical refresh fails (IDK failure is critical).
        """
        if not self._na_tokens:
            _LOGGER.warning("NA: _validate_na_tokens called but _na_tokens is empty")
            return False

        now = time.time()
        window = 900  # 15-minute proactive refresh window

        # IDK token — critical; failure means session cannot continue
        idk_entry = self._na_tokens.get("idk", {})
        if self._is_token_expiring(idk_entry, now, window):
            _LOGGER.debug(
                "NA: IDK token expiring within %s seconds, refreshing", window
            )
            try:
                await self._refresh_idk_token()
                # Note: _refresh_idk_token() cascades Brand refresh automatically
            except AuthenticationError as exc:
                _LOGGER.error("NA: IDK token refresh failed: %s", exc)
                return False

        elif "brand" in self._na_tokens and self._na_auth_level == "full":
            # IDK not expiring — check Brand independently (IDK refresh cascade didn't run)
            brand_entry = self._na_tokens.get("brand", {})
            if self._is_token_expiring(brand_entry, now, window):
                _LOGGER.debug(
                    "NA: Brand token expiring within %s seconds, refreshing", window
                )
                try:
                    await self._refresh_brand_token()
                except AuthenticationError as exc:
                    _LOGGER.warning(
                        "NA: Brand token refresh failed (non-critical): %s", exc
                    )
                    # Brand failure is non-critical — degrade to idk_only
                    self._na_auth_level = "idk_only"

        # MBB token — independent of IDK refresh path
        if self._na_auth_level == "full" and "mbb" in self._na_tokens:
            mbb_entry = self._na_tokens.get("mbb", {})
            if self._is_token_expiring(mbb_entry, now, window):
                _LOGGER.debug(
                    "NA: MBB token expiring within %s seconds, refreshing", window
                )
                try:
                    await self._refresh_mbb_from_refresh_token()
                except AuthenticationError as exc:
                    _LOGGER.warning(
                        "NA: MBB token refresh failed (non-critical): %s", exc
                    )
                    # MBB failure is non-critical for IDK-accessible endpoints

        return True

    async def validate_tokens(self) -> bool:
        """Validate expiry of tokens."""
        # NA region: use dedicated token lifecycle validation
        if self._session_region == "NA":
            return await self._validate_na_tokens()

        try:
            idtoken = self._session_tokens["identity"]["id_token"]
            atoken = self._session_tokens["identity"]["access_token"]
        except KeyError as error:
            _LOGGER.warning("Token validation failed - missing token data: %s", error)
            return False

        try:
            id_exp = jwt.decode(
                idtoken,
                options={"verify_signature": False, "verify_aud": False},
                algorithms=JWT_ALGORITHMS,
            ).get("exp", None)
            at_exp = jwt.decode(
                atoken,
                options={"verify_signature": False, "verify_aud": False},
                algorithms=JWT_ALGORITHMS,
            ).get("exp", None)
            id_dt = datetime.fromtimestamp(int(id_exp))
            at_dt = datetime.fromtimestamp(int(at_exp))
            now = datetime.now()
            later = now + self._session_refresh_interval

            # Check if tokens have expired or will expire soon
            if now >= id_dt or now >= at_dt or later >= id_dt or later >= at_dt:
                # Use the login lock to prevent multiple concurrent relogins
                async with self._login_lock:
                    # Double-check: re-verify token expiry after acquiring lock
                    # (another thread may have already logged in and refreshed tokens)
                    try:
                        idtoken = self._session_tokens["identity"]["id_token"]
                        atoken = self._session_tokens["identity"]["access_token"]

                        id_exp = jwt.decode(
                            idtoken,
                            options={"verify_signature": False, "verify_aud": False},
                            algorithms=JWT_ALGORITHMS,
                        ).get("exp", None)
                        at_exp = jwt.decode(
                            atoken,
                            options={"verify_signature": False, "verify_aud": False},
                            algorithms=JWT_ALGORITHMS,
                        ).get("exp", None)
                        id_dt = datetime.fromtimestamp(int(id_exp))
                        at_dt = datetime.fromtimestamp(int(at_exp))
                        now = datetime.now()
                        later = now + self._session_refresh_interval

                        # If tokens are now valid, another thread already refreshed them
                        if (
                            now < id_dt
                            and now < at_dt
                            and later < id_dt
                            and later < at_dt
                        ):
                            _LOGGER.debug(
                                "Tokens were refreshed by another thread, skipping relogin"
                            )
                            return True
                    except (KeyError, jwt.InvalidTokenError, TypeError, ValueError):
                        # Token check failed after lock, proceed with relogin
                        pass

                    _LOGGER.debug("Tokens expired or expiring soon, triggering relogin")
                    # Call _login() directly (we already have the lock)
                    if await self._login():
                        _LOGGER.debug("Successfully relogged in with fresh tokens")
                        self._session_logged_in = True

                        # Fetch vehicles list
                        _LOGGER.debug("Fetching vehicles associated with account")
                        self._session_headers.pop("Content-Type", None)
                        loaded_vehicles = await self.get(
                            url=f"{self._base_api}/vehicle/v2/vehicles"
                        )
                        if loaded_vehicles.get("data") is not None:
                            _LOGGER.debug("Found vehicle(s) associated with account")
                            # Don't replace vehicles, just validate they're still there
                            _LOGGER.debug("Token refresh complete")

                        return True
                    else:
                        _LOGGER.warning("Relogin failed after token expiration")
                        self._session_logged_in = False
                        return False

            return True
        except Exception as error:
            _LOGGER.error("Error validating tokens: %s", error)
            return False

    async def refresh_tokens(self) -> bool:
        """Refresh tokens.

        Note: Currently not in use — token refresh is handled via re-login in validate_tokens().
        """
        try:
            tHeaders = {
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
                "x-android-package-name": ANDROID_PACKAGE_NAME,
            }

            body = {
                "grant_type": "refresh_token",
                "refresh_token": self._session_tokens["identity"]["refresh_token"],
                "client_id": self._client_id,  # Use region-specific client ID
            }
            response = await self._session.post(
                url=f"{self._base_api}/auth/v1/idk/oidc/token",
                headers=tHeaders,
                data=body,
                timeout=ClientTimeout(total=TIMEOUT.seconds),
            )
            await self.update_service_status("token", response.status)
            if response.status == 200:
                tokens = await response.json()

                if not tokens or "access_token" not in tokens:
                    _LOGGER.error("Invalid refresh token response: %s", tokens)
                    return False
                for token in tokens:
                    self._session_tokens["identity"][token] = tokens[token]
                self._session_headers["Authorization"] = (
                    "Bearer " + self._session_tokens["identity"]["access_token"]
                )
                _LOGGER.debug("Successfully refreshed and updated tokens")
            else:
                response_text = await response.text()
                _LOGGER.warning(
                    "Token refresh failed with status %s: %s",
                    response.status,
                    response_text,
                )
                return False
        except Exception as error:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Could not refresh tokens: %s", error)
            return False
        else:
            return True

    async def update_service_status(self, url: str, response_code: int) -> None:
        """Update service status."""
        if response_code in [200, 204, 207]:
            status = "Up"
        elif response_code == 401:
            status = "Unauthorized"
        elif response_code == 403:
            status = "Forbidden"
        elif response_code == 429:
            status = "Rate limited"
        elif response_code == 1000:
            status = "Error"
        else:
            status = "Down"

        if "vehicle/v2/vehicles" in url:
            self._service_status["vehicles"] = status
        elif "parkingposition" in url:
            self._service_status["parkingposition"] = status
        elif "/vehicle/v1/trips/" in url:
            self._service_status["trips"] = status
        elif "capabilities" in url:
            self._service_status["capabilities"] = status
        elif "selectivestatus" in url:
            self._service_status["selectivestatus"] = status
        elif "token" in url:
            self._service_status["token"] = status
        else:
            _LOGGER.debug('Unhandled API URL: "%s"', url)

    async def get_service_status(self) -> dict[str, Any]:
        """Return list of service statuses."""
        _LOGGER.debug("Getting API status updates")
        return self._service_status

    # Class helpers #
    @property
    def vehicles(self) -> list[Vehicle]:
        """Return list of Vehicle objects."""
        return self._vehicles

    @property
    def logged_in(self) -> bool:
        """Return cached logged in state.

        Not actually checking anything.
        """
        return self._session_logged_in

    @property
    def na_auth_level(self) -> str | None:
        """Return the NA authentication level.

        Returns:
            "full" if all three tokens (IDK, Brand, MBB) were obtained.
            "idk_only" if only the IDK token was obtained (Brand/MBB failed).
            None for EMEA connections or before login.
        """
        return self._na_auth_level

    @property
    def is_na(self) -> bool:
        """Return True if this connection is configured for North America."""
        return self._session_region == "NA"

    @property
    def is_throttled(self) -> bool:
        """Return True if the last request exhausted all retry attempts due to rate limiting.

        Useful for Home Assistant integrations to skip a poll cycle when throttled.
        Resets to False on the next successful request.
        """
        return self._is_throttled

    def vehicle(self, vin: str) -> Vehicle | None:
        """Return vehicle object for given vin."""
        return next(
            (
                vehicle
                for vehicle in self.vehicles
                if vehicle.unique_id is not None
                and vehicle.unique_id.lower() == vin.lower()
            ),
            None,
        )

    def hash_spin(self, challenge: str, spin: str) -> str:
        """Compute SPIN hash for NA vehicle session authentication.

        Algorithm confirmed from APK decompilation (f90/y0.java RemoteStartUseCase):
        SHA-512(UTF-8("{challenge}.{spin}"))

        The challenge is the hex string returned by GET ss/v1/user/{userId}/challenge.
        The spin is the 4-digit security PIN as a string (e.g. "0560").
        Both are concatenated with "." separator and hashed as UTF-8 text.
        """
        combined = f"{challenge}.{spin}"
        return hashlib.sha512(combined.encode("utf-8")).hexdigest()

    async def validate_login(self) -> bool:
        """Check that we have a valid access token."""
        try:
            if not await self.validate_tokens():
                return False
        except OSError as error:
            _LOGGER.warning("Could not validate login: %s", error)
            return False
        else:
            return True
