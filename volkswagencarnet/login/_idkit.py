"""IDKit page models and HTML extractor (private to login module)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, urlparse

import json5
from bs4 import BeautifulSoup

from ..vw_utilities import check_str


class _IdKitError(Exception):
    """Internal: IDKit data missing or malformed. Never escapes the login module."""


class IdKitStage(StrEnum):
    IDENTIFIER = "loginIdentifier"
    PASSWORD = "loginAuthenticate"
    CONFIRM = "codeConfirmation"
    SUCCESS = "verificationSuccess"


@dataclass
class IdKitInfo:
    """Accumulated fields across IDKit pages."""

    stage: IdKitStage | None = None
    client_id: str | None = None
    relay_state: str | None = None
    hmac: str | None = None
    csrf_token: str | None = None
    user_code: str | None = None
    user_id: str | None = None
    client_identity_name: str | None = None
    email: str | None = None

    # getters — raise if not yet set

    def get_csrf_token(self) -> str:
        return self._require("csrf_token")

    def get_client_id(self) -> str:
        return self._require("client_id")

    def get_relay_state(self) -> str:
        return self._require("relay_state")

    def get_hmac(self) -> str:
        return self._require("hmac")

    def get_user_code(self) -> str:
        return self._require("user_code")

    def get_user_id(self) -> str:
        return self._require("user_id")

    def get_client_identity_name(self) -> str:
        return self._require("client_identity_name")

    # sticky setters — raise if value changes after first set

    def set_client_id(self, value: str | None) -> None:
        self._set_sticky("client_id", value)

    def set_user_code(self, value: str | None) -> None:
        self._set_sticky("user_code", value)

    def set_user_id(self, value: str | None) -> None:
        self._set_sticky("user_id", value)

    def set_client_identity_name(self, v: str | None) -> None:
        self._set_sticky("client_identity_name", v)

    # mutable setters — per-request values that rotate between pages

    def set_relay_state(self, value: str | None) -> None:
        self._set_field("relay_state", value)

    def set_hmac(self, value: str | None) -> None:
        self._set_field("hmac", value)

    def _require(self, field: str) -> str:
        value = check_str(getattr(self, field))
        if value is None:
            raise _IdKitError(f"Missing or invalid {field}")
        return value

    def _set_field(self, field: str, value: str | None) -> None:
        normalized = check_str(value)
        if normalized is None:
            raise _IdKitError(f"Missing or invalid {field}")
        setattr(self, field, normalized)

    def _set_sticky(self, field: str, value: str | None) -> None:
        normalized = check_str(value)
        if normalized is None:
            raise _IdKitError(f"Missing or invalid {field}")
        current = getattr(self, field)
        if current is not None and current != normalized:
            raise _IdKitError(
                f"Sticky field changed: {field}: {current!r} -> {normalized!r}"
            )
        setattr(self, field, normalized)


@dataclass(frozen=True)
class IdKitPageObject:
    """Parsed window._IDK object with derived accessors."""

    obj: dict[str, object]
    template_model: dict[str, object]
    device_url: dict[str, str]

    def __init__(self, obj: dict[str, object]) -> None:
        template_model = obj.get("templateModel")
        if not isinstance(template_model, dict):
            raise _IdKitError("IDK missing templateModel")
        object.__setattr__(self, "obj", obj)
        object.__setattr__(self, "template_model", template_model)
        object.__setattr__(self, "device_url", self._parse_device_url(template_model))

    @property
    def stage(self) -> IdKitStage:
        raw = check_str(self.template_model.get("template"))
        if not raw:
            raise _IdKitError("IDK missing templateModel.template")
        try:
            return IdKitStage(raw)
        except ValueError as error:
            raise _IdKitError(f"Unknown IDK stage {raw!r}") from error

    @property
    def client_id(self) -> str | None:
        client_model = self.template_model.get("clientLegalEntityModel")
        if isinstance(client_model, dict):
            value = check_str(client_model.get("clientId"))
            if value is not None:
                return value
        return check_str(self.device_url.get("client_id"))

    @property
    def relay_state(self) -> str | None:
        value = check_str(self.template_model.get("relayState"))
        return (
            value if value is not None else check_str(self.device_url.get("relayState"))
        )

    @property
    def hmac(self) -> str | None:
        value = check_str(self.template_model.get("hmac"))
        return value if value is not None else check_str(self.device_url.get("hmac"))

    @property
    def csrf_token(self) -> str | None:
        return check_str(self.obj.get("csrf_token"))

    @property
    def user_code(self) -> str | None:
        return check_str(self.device_url.get("user_code"))

    @property
    def user_id(self) -> str | None:
        return check_str(self.device_url.get("user_id"))

    @property
    def client_identity_name(self) -> str | None:
        return check_str(self.template_model.get("clientIdentityName"))

    @property
    def email(self) -> str | None:
        email_form = self.template_model.get("emailPasswordForm")
        if not isinstance(email_form, dict):
            return None
        return check_str(email_form.get("email"))

    @staticmethod
    def _parse_device_url(template_model: dict[str, object]) -> dict[str, str]:
        device_url_raw = check_str(template_model.get("url"))
        if not device_url_raw:
            return {}
        parsed = urlparse(device_url_raw)
        parts = [p for p in parsed.path.split("/") if p]
        result: dict[str, str] = {}
        if len(parts) == 3 and parts[0] == "device":
            if (v := check_str(parts[1])) is not None:
                result["client_id"] = v
            if (v := check_str(parts[2])) is not None:
                result["user_code"] = v
        query = parse_qs(parsed.query)
        for key in ("relayState", "user_id", "hmac"):
            if (v := check_str(query.get(key, [None])[0])) is not None:
                result[key] = v
        return result


class IdKitPageObjectExtractor:
    """Extracts IdKitPageObject from an HTML page containing window._IDK."""

    _ASSIGNMENT = "window._IDK"

    @classmethod
    def from_html(cls, html: str) -> IdKitPageObject:
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script"):
            text = script.string if script.string is not None else script.get_text()
            if not text:
                continue
            idx = text.find(cls._ASSIGNMENT)
            if idx < 0:
                continue
            eq = text.find("=", idx + len(cls._ASSIGNMENT))
            if eq < 0:
                raise _IdKitError(f"Found {cls._ASSIGNMENT} but no '=' assignment")
            rhs = text[eq + 1 :]
            if "{" not in rhs:
                raise _IdKitError(f"Found {cls._ASSIGNMENT} but no object literal")
            try:
                parsed = json5.loads(rhs)
            except (ValueError, json5.JSONDecodeError) as error:
                raise _IdKitError(
                    f"Failed to parse {cls._ASSIGNMENT}: {error}"
                ) from error
            if not isinstance(parsed, dict):
                raise _IdKitError(f"{cls._ASSIGNMENT} is not an object")
            return IdKitPageObject(parsed)
        raise _IdKitError(f"{cls._ASSIGNMENT} not found in any script tag")
