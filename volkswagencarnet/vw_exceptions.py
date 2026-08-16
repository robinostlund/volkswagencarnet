"""Custom exceptions for volkswagencarnet."""


class VWError(Exception):
    """Base exception for VW CarNet errors."""

    pass


class AuthenticationError(VWError):
    """Authentication failed."""

    pass


class LoginError(AuthenticationError):
    """Base for all device-flow login failures."""

    pass


class LoginFlowChangedError(LoginError):
    """VW login page structure changed; flow cannot continue safely."""

    def __init__(self, *, stage: str, dump_path: str | None = None) -> None:
        self.stage = stage
        self.dump_path = dump_path
        msg = f"VW login flow changed at stage {stage!r}."
        if dump_path:
            msg += f" HTML snapshot: {dump_path}"
        super().__init__(msg)


class LoginPageParseError(LoginError):
    """IDKit page structure not recognised; cannot extract login state."""

    pass


class LoginCredentialsError(LoginError):
    """Authentication rejected; credentials appear to be invalid."""

    pass


class APIError(VWError):
    """API request failed."""

    pass


class SPINError(VWError):
    """S-PIN related error."""

    pass


class RedirectError(VWError):
    """Redirect handling failed."""

    pass


class RequestError(VWError):
    """Request execution failed."""

    pass


class TermsAndConditionsError(AuthenticationError):
    """Terms and Conditions need to be accepted."""

    pass
