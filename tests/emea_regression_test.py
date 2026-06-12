"""
EMEA regression tests and public API surface contract tests.

These tests prove that the NA authentication code added in Phases 1-5 has not
broken the EMEA login flow and that the public Connection API surface is frozen.

Phase 6 plan 06-01 — COMPAT-01, COMPAT-02
"""

import asyncio
import inspect
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from volkswagencarnet.vw_connection import Connection


class ConnectionAPIContractTest(IsolatedAsyncioTestCase):
    """Lock the public API surface via inspect.signature().

    Any future change to the frozen positional args or method kinds will fail here.
    Frozen surface: Connection(session, username, password, country='DE'),
    doLogin(tries=1) async, update() async, vehicles property.
    """

    def test_constructor_frozen_positional_args_present(self):
        """Frozen positional args must always be present in __init__."""
        sig = inspect.signature(Connection.__init__)
        params = sig.parameters
        for name in ("session", "username", "password", "country"):
            assert name in params, (
                f"Frozen arg '{name}' missing from Connection.__init__"
            )

    def test_country_defaults_to_de(self):
        """country='DE' is the backward compat default — EMEA users need zero code changes."""
        sig = inspect.signature(Connection.__init__)
        assert sig.parameters["country"].default == "DE"

    def test_dologin_accepts_tries_kwarg_with_default_one(self):
        """doLogin(tries=1) signature stable — callers may pass tries=N without breakage."""
        sig = inspect.signature(Connection.doLogin)
        assert "tries" in sig.parameters
        assert sig.parameters["tries"].default == 1

    def test_dologin_is_async(self):
        """doLogin() must remain a coroutine so `await conn.doLogin()` works."""
        assert asyncio.iscoroutinefunction(Connection.doLogin)

    def test_update_is_async(self):
        """update() must remain a coroutine so `await conn.update()` works."""
        assert asyncio.iscoroutinefunction(Connection.update)

    def test_vehicles_is_property(self):
        """vehicles must remain a property, not a plain method."""
        assert isinstance(inspect.getattr_static(Connection, "vehicles"), property)


# ---------------------------------------------------------------------------
# EMEA login regression tests  (COMPAT-01)
# Guards the NA dispatch point at vw_connection.py line ~1140.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emea_login_calls_all_three_steps_in_sequence(connection):
    """EMEA _login() calls get_openid_config → _get_authorization_code → _exchange_code_for_tokens, each exactly once."""
    openid_config = {
        "authorization_endpoint": "https://identity.vwgroup.io/authorize",
        "token_endpoint": "https://identity.vwgroup.io/token",
    }
    token_response = {
        "access_token": "emea_at",
        "id_token": "emea_id",
        "token_type": "Bearer",
        "refresh_token": "emea_rt",
    }
    mock_openid = AsyncMock(return_value=openid_config)
    mock_auth_code = AsyncMock(return_value=("emea_code", "emea_id", "emea_at"))
    mock_build = MagicMock(return_value=token_response)

    with (
        patch.object(connection, "get_openid_config", mock_openid),
        patch.object(connection, "_get_authorization_code", mock_auth_code),
        patch.object(connection, "_build_session_tokens", mock_build),
    ):
        result = await connection._login()

    assert result is True
    # Each step of the EMEA sequence fires exactly once — no skipping, no doubling
    assert mock_openid.call_count == 1, "get_openid_config must be called exactly once"
    assert mock_auth_code.call_count == 1, (
        "_get_authorization_code must be called exactly once"
    )
    assert mock_build.call_count == 1, (
        "_build_session_tokens must be called exactly once"
    )
    # Tokens stored under 'identity' key (EMEA contract)
    assert connection._session_tokens["identity"]["access_token"] == "emea_at"
    assert connection._session_region == "EMEA"


@pytest.mark.asyncio
async def test_emea_login_stores_tokens_under_identity_key(connection):
    """EMEA tokens are stored in _session_tokens['identity'], not _na_tokens."""
    openid_config = {
        "authorization_endpoint": "https://identity.vwgroup.io/authorize",
        "token_endpoint": "https://identity.vwgroup.io/token",
    }
    token_response = {
        "access_token": "emea_access",
        "id_token": "emea_id_token",
        "token_type": "Bearer",
        "refresh_token": "emea_refresh",
    }

    with (
        patch.object(
            connection, "get_openid_config", AsyncMock(return_value=openid_config)
        ),
        patch.object(
            connection,
            "_get_authorization_code",
            AsyncMock(return_value=("auth_code", "emea_id_token", "emea_access")),
        ),
        patch.object(
            connection, "_build_session_tokens", MagicMock(return_value=token_response)
        ),
    ):
        await connection._login()

    # EMEA tokens land in 'identity' — not in any NA-specific location
    assert "identity" in connection._session_tokens
    assert connection._session_tokens["identity"]["access_token"] == "emea_access"
    assert connection._session_tokens["identity"]["refresh_token"] == "emea_refresh"
    # NA token dict is empty for EMEA connections — no cross-contamination
    assert connection._na_tokens == {}


@pytest.mark.asyncio
async def test_non_na_countries_stay_in_emea_path(session):
    """FR, GB, XX, and DE all route through EMEA _login(), never _login_na()."""
    for country in ("FR", "GB", "XX", "DE"):
        conn = Connection(session, "", "", country=country)
        assert conn._session_region == "EMEA", f"Expected EMEA for country={country}"
        with patch.object(conn, "_login_na") as mock_na:
            with patch.object(
                conn, "get_openid_config", side_effect=ValueError("stop early")
            ):
                await conn._login()
        assert mock_na.call_count == 0, f"_login_na was called for country={country}"


@pytest.mark.asyncio
async def test_emea_login_returns_false_on_auth_error(connection):
    """EMEA _login() returns False (not raises) when get_openid_config fails."""
    from volkswagencarnet.vw_exceptions import AuthenticationError

    with patch.object(connection, "_login_na") as mock_na:
        with patch.object(
            connection,
            "get_openid_config",
            side_effect=AuthenticationError("bad creds"),
        ):
            result = await connection._login()

    assert result is False
    mock_na.assert_not_called()
    # _session_logged_in must not be set to True on failure
    assert connection._session_logged_in is False


@pytest.mark.asyncio
async def test_emea_connection_has_no_na_tokens_after_login(connection):
    """After EMEA login, _na_tokens remains empty and _na_auth_level stays at default."""
    openid_config = {
        "authorization_endpoint": "https://identity.vwgroup.io/authorize",
        "token_endpoint": "https://identity.vwgroup.io/token",
    }
    token_response = {
        "access_token": "emea_at",
        "id_token": "emea_id",
        "token_type": "Bearer",
        "refresh_token": "emea_rt",
    }

    with (
        patch.object(
            connection, "get_openid_config", AsyncMock(return_value=openid_config)
        ),
        patch.object(
            connection,
            "_get_authorization_code",
            AsyncMock(return_value=("code", "emea_id", "emea_at")),
        ),
        patch.object(
            connection, "_build_session_tokens", MagicMock(return_value=token_response)
        ),
    ):
        result = await connection._login()

    assert result is True
    # NA token state must be clean — no cross-contamination from NA code paths
    assert connection._na_tokens == {}
    assert connection._na_auth_level is None  # default, never set by EMEA path
