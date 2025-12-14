"""Custom exceptions for volkswagencarnet."""


class VWError(Exception):
    """Base exception for VW CarNet errors."""

    pass


class AuthenticationError(VWError):
    """Authentication failed."""

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
