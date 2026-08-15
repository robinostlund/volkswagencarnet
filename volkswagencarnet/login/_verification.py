"""Soft verification checks for the login flow (warnings only, never hard-fail)."""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..vw_utilities import check_str
from ._idkit import IdKitInfo, IdKitPageObject, IdKitStage

_LOGGER = logging.getLogger(__name__)


class LoginVerifier:
    """Logs warnings when IDKit data or form fields look unexpected."""

    _EXPECTED_CSRF_PARAM = "_csrf"
    _IDENTITYKIT_META_SELECTOR = 'meta[name="identitykit"]'

    def verify_idk_obj(
        self,
        stage: IdKitStage,
        html: str,
        idk_obj: IdKitPageObject,
        idk_info: IdKitInfo,
        configured_client_id: str,
        expected_username: str,
    ) -> None:
        csrf_param = check_str(idk_obj.obj.get("csrf_parameterName"))
        if csrf_param != self._EXPECTED_CSRF_PARAM:
            self._warn(
                f"IDK csrf_parameterName={csrf_param!r}, expected {self._EXPECTED_CSRF_PARAM!r}"
            )

        meta_stage = self._extract_meta_stage(html)
        if meta_stage is None:
            self._warn("Meta identitykit stage missing or unknown")
        elif meta_stage != stage:
            self._warn(f"Meta stage {meta_stage.value!r} differs from IDK stage {stage.value!r}")

        if idk_obj.client_id and idk_obj.client_id != configured_client_id:
            self._warn(
                f"IDK client_id={idk_obj.client_id!r} differs from configured {configured_client_id!r}"
            )

        for field, page_val, info_val in (
            ("client_id", idk_obj.client_id, idk_info.client_id),
        ):
            if page_val and info_val and page_val != info_val:
                self._warn(f"Sticky field deviation: {field}: page={page_val!r}, info={info_val!r}")

        if stage == IdKitStage.PASSWORD:
            page_email = idk_obj.email
            if page_email is None:
                self._warn("PASSWORD stage: email missing in IDK page")
            elif page_email != expected_username:
                self._warn(
                    f"PASSWORD stage: page email {page_email!r} != submitted {expected_username!r}"
                )

    def verify_request_matches_form(
        self,
        stage: IdKitStage,
        current_url: str,
        html: str,
        planned_url: str,
        planned_payload: dict[str, str],
    ) -> None:
        # PASSWORD page is JS-driven; no static form to compare against.
        if stage == IdKitStage.PASSWORD:
            return

        if stage == IdKitStage.IDENTIFIER:
            selector, required = "form#emailPasswordForm", ["_csrf", "relayState", "hmac", "email"]
        else:
            selector, required = "form#allowAccess", ["_csrf", "client_identity_name"]

        form_data = self._extract_form(html, selector)
        if form_data is None:
            self._warn(f"Expected form {selector!r} not found for stage {stage.value}")
            return

        form_action, form_payload = form_data
        actual_url = urljoin(current_url, form_action)
        if actual_url != planned_url:
            self._warn(f"Form action {actual_url!r} differs from planned URL {planned_url!r}")

        missing = [f for f in required if f not in form_payload]
        if missing:
            self._warn(f"Form missing fields for {stage.value}: {missing}")

        for key, value in planned_payload.items():
            # Empty form fields are filled by the user/JS; no mismatch to warn about.
            if key in form_payload and form_payload[key] and form_payload[key] != value:
                self._warn(
                    f"Payload field {key!r}: planned={value!r}, form={form_payload[key]!r}"
                )

    @staticmethod
    def _extract_form(html: str, selector: str) -> tuple[str, dict[str, str]] | None:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.select_one(selector)
        if not form or not form.get("action"):
            return None
        payload: dict[str, str] = {}
        for el in form.select("input[name], button[name]"):
            name = el.get("name")
            if name:
                payload[name] = el.get("value", "")
        return form["action"], payload

    def _extract_meta_stage(self, html: str) -> IdKitStage | None:
        soup = BeautifulSoup(html, "html.parser")
        meta = soup.select_one(self._IDENTITYKIT_META_SELECTOR)
        if not meta or not meta.get("content"):
            return None
        try:
            return IdKitStage(str(meta["content"]))
        except ValueError:
            return None

    def _warn(self, message: str) -> None:
        _LOGGER.warning("LoginVerifier: %s", message)
