"""Tests for main connection class."""

import asyncio
import inspect
import json
import logging
import re
import sys
import time
from datetime import timedelta
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiohttp import ClientSession, client_exceptions
import jwt.exceptions
import pytest
from volkswagencarnet import vw_connection
from volkswagencarnet.vw_connection import Connection
from volkswagencarnet.vw_const import (
    APP_VERSION,
    APP_VERSION_SHORT,
    MAX_REDIRECT_DEPTH,
    COUNTRY_TO_LOCALE,
    USER_AGENT,
)
from volkswagencarnet.vw_exceptions import (
    APIError,
    AuthenticationError,
    RedirectError,
    RequestError,
    SPINError,
)


class TwoVehiclesConnection(Connection):
    """Connection that return two vehicles."""

    ALLOW_RATE_LIMIT_DELAY = False

    # noinspection PyUnusedLocal
    # noinspection PyMissingConstructor
    def __init__(self, sess, username="", password="", **kwargs):
        """Init."""
        super().__init__(session=sess, username=username, password=password)

    async def doLogin(self, tries=1):
        """No-op update."""
        return True

    async def update(self):
        """No-op update."""
        return True

    @property
    def vehicles(self):
        """Return the vehicles."""
        vehicle1 = vw_connection.Vehicle(None, "vin1")
        vehicle2 = vw_connection.Vehicle(None, "vin2")
        return [vehicle1, vehicle2]


@pytest.mark.skipif(
    condition=sys.version_info < (3, 11), reason="Test incompatible with Python < 3.11"
)
def test_clear_cookies(connection) -> None:
    """Check that we can clear old cookies."""
    assert len(connection._session._cookie_jar._cookies) > 0
    connection._clear_cookies()
    assert len(connection._session._cookie_jar._cookies) == 0


class SendCommandsTest(IsolatedAsyncioTestCase):
    """Test command sending."""

    async def test_set_schedule(self):
        """Test setDepartureTimers sends PUT with timer data to departure/timers endpoint."""
        mock_session = AsyncMock()
        mock_session._cookie_jar = MagicMock()
        mock_session._cookie_jar._cookies = {}
        conn = Connection(mock_session, "test@example.com", "password123")
        conn._base_api = "https://emea.bff.cariad.digital"
        mock_raw = AsyncMock()
        mock_raw.json = AsyncMock(return_value={"data": {"requestID": "schedule-123"}})
        conn.put = AsyncMock(return_value=mock_raw)
        data = {"timers": [{"id": 1, "enabled": True, "departureTime": "07:00"}]}
        result = await conn.setDepartureTimers("WVWTEST1234567890", data=data)
        assert result is not None
        assert result.get("id") == "schedule-123"
        call_url = conn.put.call_args[0][0]
        assert "departure/timers" in call_url


class RateLimitTest(IsolatedAsyncioTestCase):
    """Test that rate limiting towards VW works."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        condition=sys.version_info < (3, 11),
        reason="Test incompatible with Python < 3.11",
    )
    @patch(
        "volkswagencarnet.vw_connection.Connection",
        spec_set=vw_connection.Connection,
        new=TwoVehiclesConnection,
    )
    async def test_rate_limit(self):
        """Test that get() returns Throttled state after 429 retry exhaustion.

        Retry logic is centralized in _request(). get() catches the raised
        ClientResponseError with status 429 and returns {"state": "Throttled"}.
        """
        from unittest.mock import AsyncMock

        sess = AsyncMock()

        # noinspection PyArgumentList
        conn = vw_connection.Connection(sess, "", "")

        ri = MagicMock(aiohttp.RequestInfo)
        e = client_exceptions.ClientResponseError(request_info=ri, history=tuple([]))
        e.status = 429

        with patch.object(conn, "_request", side_effect=e):
            res = await conn.get("foo")
            assert res == {"state": "Throttled"}


class NAOAuthLoginTest(IsolatedAsyncioTestCase):
    """Test NA OAuth login flow."""

    def _make_na_conn(self):
        """Create a Connection with country='US' and mocked session."""
        mock_session = AsyncMock()
        mock_session._cookie_jar = MagicMock()
        mock_session._cookie_jar._cookies = {}
        conn = Connection(mock_session, "user@example.com", "password", country="US")
        return conn

    async def test_na_login_success(self):
        """Test successful NA login via the _login() dispatch chain."""
        conn = self._make_na_conn()

        openid_config = {
            "authorization_endpoint": "https://identity.na.vwgroup.io/authorize",
            "token_endpoint": "https://identity.na.vwgroup.io/token",
            "issuer": "https://identity.na.vwgroup.io",
        }
        token_response = {
            "access_token": "idk_access_token",
            "id_token": "idk_id_token",
            "token_type": "Bearer",
            "refresh_token": "idk_refresh_token",
        }

        # Call _login() — validates that _login() dispatches to _login_na() for NA
        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_123"
            ),
            patch.object(
                conn, "_exchange_code_for_tokens", return_value=token_response
            ),
        ):
            result = await conn._login()

        assert result is True
        assert conn._session_tokens["identity"]["access_token"] == "idk_access_token"
        assert conn._session_region == "NA"

    async def test_na_login_bad_credentials(self):
        """Test that bad credentials cause _login_na() to raise AuthenticationError (not return False)."""
        conn = self._make_na_conn()

        openid_config = {
            "authorization_endpoint": "https://identity.na.vwgroup.io/authorize",
            "token_endpoint": "https://identity.na.vwgroup.io/token",
            "issuer": "https://identity.na.vwgroup.io",
        }

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn,
                "_get_authorization_code_na",
                side_effect=AuthenticationError("Wrong username or password"),
            ),
            pytest.raises(AuthenticationError, match="Wrong username or password"),
        ):
            await conn._login_na()

        assert conn._session_logged_in is False
        assert "identity" not in conn._session_tokens

    async def test_na_login_token_exchange_failure(self):
        """Test that a token exchange failure (missing required keys) returns False."""
        conn = self._make_na_conn()

        openid_config = {
            "authorization_endpoint": "https://identity.na.vwgroup.io/authorize",
            "token_endpoint": "https://identity.na.vwgroup.io/token",
            "issuer": "https://identity.na.vwgroup.io",
        }
        # Missing required keys (access_token, id_token, token_type)
        bad_token_response = {
            "error": "invalid_grant",
            "error_description": "Token expired",
        }

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_bad"
            ),
            patch.object(
                conn, "_exchange_code_for_tokens", return_value=bad_token_response
            ),
        ):
            result = await conn._login_na()

        assert result is False

    async def test_na_login_redirect_extraction_failure(self):
        """Test that a redirect failure during NA login raises RedirectError (not returns False)."""
        conn = self._make_na_conn()

        openid_config = {
            "authorization_endpoint": "https://identity.na.vwgroup.io/authorize",
            "token_endpoint": "https://identity.na.vwgroup.io/token",
            "issuer": "https://identity.na.vwgroup.io",
        }

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn,
                "_get_authorization_code_na",
                side_effect=RedirectError("Too many redirects"),
            ),
            pytest.raises(RedirectError, match="Too many redirects"),
        ):
            await conn._login_na()

        assert conn._session_logged_in is False

    async def test_na_login_network_error(self):
        """Test that a network error during login returns False without raising."""
        conn = self._make_na_conn()

        with patch.object(
            conn,
            "get_openid_config",
            side_effect=client_exceptions.ClientConnectionError(),
        ):
            result = await conn._login_na()

        assert result is False

    async def test_emea_login_not_routed_to_na(self):
        """Test that EMEA connections do not dispatch to _login_na()."""
        mock_session = AsyncMock()
        mock_session._cookie_jar = MagicMock()
        mock_session._cookie_jar._cookies = {}
        conn = Connection(
            mock_session, "user@example.com", "password"
        )  # No country = DE = EMEA

        assert conn._session_region == "EMEA"
        assert conn._session_region != "NA"

        # Confirm _login() would not dispatch to _login_na() for this connection
        with patch.object(conn, "_login_na") as mock_na:
            with patch.object(
                conn, "get_openid_config", side_effect=AuthenticationError("stop")
            ):
                await conn._login()
            mock_na.assert_not_called()


class NAThreeTokenTest(IsolatedAsyncioTestCase):
    """Test NA three-token chain: IDK, Brand, and MBB acquisition."""

    def _make_na_conn(self, **kwargs):
        """Create a Connection with country='US' and mocked session."""
        mock_session = AsyncMock()
        mock_session._cookie_jar = MagicMock()
        mock_session._cookie_jar._cookies = {}
        return Connection(
            mock_session, "user@example.com", "password", country="US", **kwargs
        )

    def _idk_fixtures(self):
        """Return standard openid_config and idk_tokens fixtures."""
        openid_config = {
            "authorization_endpoint": "https://identity.na.vwgroup.io/authorize",
            "token_endpoint": "https://identity.na.vwgroup.io/token",
            "issuer": "https://identity.na.vwgroup.io",
        }
        idk_tokens = {
            "access_token": "idk_at",
            "id_token": "idk_id",
            "refresh_token": "idk_rt",
            "token_type": "Bearer",
        }
        return openid_config, idk_tokens

    async def test_na_full_three_token_login_success(self):
        """Test full three-token chain succeeds and all tokens are stored."""
        conn = self._make_na_conn()
        openid_config, idk_tokens = self._idk_fixtures()
        brand_tokens = {"access_token": "brand_at", "refresh_token": "brand_rt"}
        mbb_initial = {"access_token": "mbb_at_1", "refresh_token": "mbb_rt_1"}
        mbb_refreshed = {"access_token": "mbb_at_2", "refresh_token": "mbb_rt_2"}

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_123"
            ),
            patch.object(conn, "_exchange_code_for_tokens", return_value=idk_tokens),
            patch.object(conn, "_exchange_brand_token", return_value=brand_tokens),
            patch.object(conn, "_register_mbb_client", return_value="xclient-001"),
            patch.object(conn, "_exchange_mbb_token", return_value=mbb_initial),
            patch.object(conn, "_refresh_mbb_token", return_value=mbb_refreshed),
        ):
            result = await conn._login_na()

        assert result is True
        assert conn._na_auth_level == "full"
        assert conn._xclient_id == "xclient-001"
        assert conn._na_tokens["idk"]["access_token"] == "idk_at"
        assert conn._na_tokens["brand"]["access_token"] == "brand_at"
        assert conn._na_tokens["mbb"]["access_token"] == "mbb_at_2"  # refreshed token
        assert conn._session_tokens["identity"]["access_token"] == "idk_at"  # COMPAT-04

    async def test_na_brand_failure_falls_back_to_idk_only(self):
        """Test that brand exchange failure falls back to IDK-only auth level."""
        conn = self._make_na_conn()
        openid_config, idk_tokens = self._idk_fixtures()

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_brand_fail"
            ),
            patch.object(conn, "_exchange_code_for_tokens", return_value=idk_tokens),
            patch.object(
                conn,
                "_exchange_brand_token",
                side_effect=AuthenticationError("brand exchange failed"),
            ),
        ):
            result = await conn._login_na()

        assert result is True
        assert conn._na_auth_level == "idk_only"
        assert "brand" not in conn._na_tokens
        assert "mbb" not in conn._na_tokens

    async def test_na_mbb_registration_failure_falls_back_to_idk_only(self):
        """Test that MBB registration failure falls back to IDK-only auth level."""
        conn = self._make_na_conn()
        openid_config, idk_tokens = self._idk_fixtures()
        brand_tokens = {"access_token": "brand_at", "refresh_token": "brand_rt"}

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_reg_fail"
            ),
            patch.object(conn, "_exchange_code_for_tokens", return_value=idk_tokens),
            patch.object(conn, "_exchange_brand_token", return_value=brand_tokens),
            patch.object(
                conn,
                "_register_mbb_client",
                side_effect=AuthenticationError("registration failed"),
            ),
        ):
            result = await conn._login_na()

        assert result is True
        assert conn._na_auth_level == "idk_only"
        assert "mbb" not in conn._na_tokens

    async def test_na_mbb_token_exchange_failure_falls_back_to_idk_only(self):
        """Test that MBB token exchange failure falls back to IDK-only auth level."""
        conn = self._make_na_conn()
        openid_config, idk_tokens = self._idk_fixtures()
        brand_tokens = {"access_token": "brand_at", "refresh_token": "brand_rt"}

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_mbb_fail"
            ),
            patch.object(conn, "_exchange_code_for_tokens", return_value=idk_tokens),
            patch.object(conn, "_exchange_brand_token", return_value=brand_tokens),
            patch.object(conn, "_register_mbb_client", return_value="xclient-002"),
            patch.object(
                conn,
                "_exchange_mbb_token",
                side_effect=AuthenticationError("MBB exchange failed"),
            ),
        ):
            result = await conn._login_na()

        assert result is True
        assert conn._na_auth_level == "idk_only"

    async def test_na_mbb_refresh_called_immediately_after_initial_grant(self):
        """Test that _refresh_mbb_token is called once with initial MBB refresh_token."""
        conn = self._make_na_conn()
        openid_config, idk_tokens = self._idk_fixtures()
        brand_tokens = {"access_token": "brand_at", "refresh_token": "brand_rt"}
        mbb_initial = {"access_token": "mbb_at_1", "refresh_token": "mbb_rt_1"}
        mbb_refreshed = {"access_token": "mbb_at_2", "refresh_token": "mbb_rt_2"}

        mock_refresh = AsyncMock(return_value=mbb_refreshed)

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_refresh"
            ),
            patch.object(conn, "_exchange_code_for_tokens", return_value=idk_tokens),
            patch.object(conn, "_exchange_brand_token", return_value=brand_tokens),
            patch.object(conn, "_register_mbb_client", return_value="xclient-003"),
            patch.object(conn, "_exchange_mbb_token", return_value=mbb_initial),
            patch.object(conn, "_refresh_mbb_token", mock_refresh),
        ):
            await conn._login_na()

        assert mock_refresh.call_count == 1
        # Verify the initial refresh_token was passed to _refresh_mbb_token
        call_kwargs = mock_refresh.call_args
        assert call_kwargs[1]["refresh_token"] == "mbb_rt_1"

    async def test_na_injected_xclient_id_skips_registration(self):
        """Test that a caller-injected xclientId bypasses _register_mbb_client."""
        conn = self._make_na_conn(xclient_id="injected-123")
        openid_config, idk_tokens = self._idk_fixtures()
        brand_tokens = {"access_token": "brand_at", "refresh_token": "brand_rt"}
        mbb_initial = {"access_token": "mbb_at_1", "refresh_token": "mbb_rt_1"}
        mbb_refreshed = {"access_token": "mbb_at_2", "refresh_token": "mbb_rt_2"}

        mock_register = AsyncMock(return_value="should-not-be-called")

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_inject"
            ),
            patch.object(conn, "_exchange_code_for_tokens", return_value=idk_tokens),
            patch.object(conn, "_exchange_brand_token", return_value=brand_tokens),
            patch.object(conn, "_register_mbb_client", mock_register),
            patch.object(conn, "_exchange_mbb_token", return_value=mbb_initial),
            patch.object(conn, "_refresh_mbb_token", return_value=mbb_refreshed),
        ):
            result = await conn._login_na()

        mock_register.assert_not_called()
        assert conn._xclient_id == "injected-123"  # unchanged, not overwritten
        assert result is True
        assert conn._na_auth_level == "full"

    async def test_na_on_xclient_id_callback_fired_for_new_registration(self):
        """Test that on_xclient_id callback fires when a new xclientId is registered."""
        callback = MagicMock()
        conn = self._make_na_conn(on_xclient_id=callback)
        openid_config, idk_tokens = self._idk_fixtures()
        brand_tokens = {"access_token": "brand_at", "refresh_token": "brand_rt"}
        mbb_initial = {"access_token": "mbb_at_1", "refresh_token": "mbb_rt_1"}
        mbb_refreshed = {"access_token": "mbb_at_2", "refresh_token": "mbb_rt_2"}

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_cb"
            ),
            patch.object(conn, "_exchange_code_for_tokens", return_value=idk_tokens),
            patch.object(conn, "_exchange_brand_token", return_value=brand_tokens),
            patch.object(conn, "_register_mbb_client", return_value="new-xclient"),
            patch.object(conn, "_exchange_mbb_token", return_value=mbb_initial),
            patch.object(conn, "_refresh_mbb_token", return_value=mbb_refreshed),
        ):
            await conn._login_na()

        assert callback.call_count == 1
        assert callback.call_args[0][0] == "new-xclient"

    async def test_na_on_xclient_id_callback_not_fired_for_injected_id(self):
        """Test that on_xclient_id callback does NOT fire when xclientId is caller-injected."""
        callback = MagicMock()
        conn = self._make_na_conn(xclient_id="injected-456", on_xclient_id=callback)
        openid_config, idk_tokens = self._idk_fixtures()
        brand_tokens = {"access_token": "brand_at", "refresh_token": "brand_rt"}
        mbb_initial = {"access_token": "mbb_at_1", "refresh_token": "mbb_rt_1"}
        mbb_refreshed = {"access_token": "mbb_at_2", "refresh_token": "mbb_rt_2"}

        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn, "_get_authorization_code_na", return_value="auth_code_no_cb"
            ),
            patch.object(conn, "_exchange_code_for_tokens", return_value=idk_tokens),
            patch.object(conn, "_exchange_brand_token", return_value=brand_tokens),
            patch.object(
                conn, "_register_mbb_client", return_value="should-not-be-used"
            ),
            patch.object(conn, "_exchange_mbb_token", return_value=mbb_initial),
            patch.object(conn, "_refresh_mbb_token", return_value=mbb_refreshed),
        ):
            await conn._login_na()

        callback.assert_not_called()


class NATokenLifecycleTest(IsolatedAsyncioTestCase):
    """Test NA token lifecycle management: endpoint classification, per-token refresh, cascade, and validation."""

    def _make_na_conn(self, **kwargs):
        """Create a Connection with country='US' and mocked session."""
        session = AsyncMock()
        session._cookie_jar = MagicMock()
        session._cookie_jar._cookies = {}
        return Connection(
            session, "user@example.com", "password", country="US", **kwargs
        )

    def _na_tokens_fixture(self, expires_at_offset=7200):
        """Return populated _na_tokens dict with future expires_at values."""
        now = time.time()
        return {
            "idk": {
                "access_token": "idk_at",
                "refresh_token": "idk_rt",
                "id_token": "idk_id",
                "expires_at": now + expires_at_offset,
                "issued_at": now,
            },
            "brand": {
                "access_token": "brand_at",
                "refresh_token": "brand_rt",
                "expires_at": now + expires_at_offset,
                "issued_at": now,
            },
            "mbb": {
                "access_token": "mbb_at",
                "refresh_token": "mbb_rt",
                "expires_at": now + expires_at_offset,
                "issued_at": now,
            },
        }

    # --- Group A: _classify_endpoint() ---

    async def test_classify_endpoint_idk_for_base_api_url(self):
        """_classify_endpoint returns 'idk' for Cariad BFF base API URLs."""
        conn = self._make_na_conn()
        conn._base_api = "https://b-h-s.spr.us00.p.con-veh.net"
        result = conn._classify_endpoint(
            "https://b-h-s.spr.us00.p.con-veh.net/vehicle/v2/vehicles"
        )
        assert result == "idk"

    async def test_classify_endpoint_mbb_for_mbb_host(self):
        """_classify_endpoint returns 'mbb' for MBB OAuth service URLs."""
        conn = self._make_na_conn()
        result = conn._classify_endpoint(
            "https://mbboauth-1d.prd.ece.vwg-connect.com/mbbcoauth/mobile/oauth2/v1/token"
        )
        assert result == "mbb"

    async def test_classify_endpoint_brand_for_volkswagen_token_path(self):
        """_classify_endpoint returns 'brand' for brand token path URLs."""
        conn = self._make_na_conn()
        conn._base_api = "https://b-h-s.spr.us00.p.con-veh.net"
        result = conn._classify_endpoint(
            "https://b-h-s.spr.us00.p.con-veh.net/login/v1/volkswagen/token"
        )
        assert result == "brand"

    async def test_classify_endpoint_raises_for_unknown_url(self):
        """_classify_endpoint raises ValueError for unknown NA URLs."""
        conn = self._make_na_conn()
        conn._base_api = "https://b-h-s.spr.us00.p.con-veh.net"
        with pytest.raises(ValueError):
            conn._classify_endpoint("https://unknown.example.com/api")

    # --- Group B: EMEA guard ---

    async def test_classify_endpoint_always_returns_idk_for_emea(self):
        """_classify_endpoint always returns 'idk' for EMEA connections (never raises)."""
        session = AsyncMock()
        session._cookie_jar = MagicMock()
        session._cookie_jar._cookies = {}
        conn = Connection(session, "user@example.com", "password", country="DE")
        result = conn._classify_endpoint("https://unknown.example.com/anything")
        assert result == "idk"

    # --- Group C: _refresh_idk_token() ---

    async def test_idk_refresh_updates_na_tokens_and_session_mirror(self):
        """_refresh_idk_token updates na_tokens, session_tokens mirror, and Authorization header."""
        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture()
        conn._na_token_endpoint = "https://id.example.com/token"
        # Pre-populate identity entry so mirror update doesn't KeyError
        conn._session_tokens["identity"] = {"access_token": "old_at"}

        # Remove brand so cascade doesn't fire
        del conn._na_tokens["brand"]

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "access_token": "new_at",
                "refresh_token": "new_rt",
                "id_token": "new_id",
                "expires_in": 3600,
            }
        )

        mock_post = AsyncMock(return_value=mock_response)
        with patch.object(conn._session, "post", mock_post):
            await conn._refresh_idk_token()

        assert conn._na_tokens["idk"]["access_token"] == "new_at"
        assert conn._session_tokens["identity"]["access_token"] == "new_at"
        assert "Bearer new_at" in conn._session_headers["Authorization"]

    async def test_idk_refresh_triggers_brand_cascade(self):
        """_refresh_idk_token calls _refresh_brand_token() when brand key is present."""
        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture()
        conn._na_token_endpoint = "https://id.example.com/token"
        # Ensure identity entry exists for mirror update
        conn._session_tokens["identity"] = {"access_token": "old_at"}

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "access_token": "new_at",
                "refresh_token": "new_rt",
                "id_token": "new_id",
                "expires_in": 3600,
            }
        )

        mock_brand_refresh = AsyncMock()

        mock_post = AsyncMock(return_value=mock_response)
        with (
            patch.object(conn._session, "post", mock_post),
            patch.object(conn, "_refresh_brand_token", mock_brand_refresh),
        ):
            await conn._refresh_idk_token()

        assert mock_brand_refresh.call_count == 1

    async def test_idk_refresh_retries_up_to_three_times(self):
        """IDK refresh retries up to 3 times on failure; body contains refresh_token,
        grant_type=refresh_token, and code_verifier (no X-QMAuth — server rejects it with HTTP 400)."""
        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture()
        conn._na_token_endpoint = "https://id.example.com/token"
        # Pre-populate identity entry so mirror update doesn't KeyError
        conn._session_tokens["identity"] = {"access_token": "old_at"}

        fail_response = AsyncMock()
        fail_response.status = 500
        fail_response.text = AsyncMock(return_value="Server Error")

        success_response = AsyncMock()
        success_response.status = 200
        success_response.json = AsyncMock(
            return_value={
                "access_token": "recovered_at",
                "refresh_token": "recovered_rt",
                "id_token": "recovered_id",
                "expires_in": 3600,
            }
        )
        # Remove brand to avoid cascade
        del conn._na_tokens["brand"]

        mock_session_post = AsyncMock(
            side_effect=[fail_response, fail_response, success_response]
        )

        with patch.object(conn._session, "post", mock_session_post):
            with patch("asyncio.sleep", AsyncMock()):
                await conn._refresh_idk_token()

        assert mock_session_post.call_count == 3
        # Verify all calls sent correct body parameters (no X-QMAuth — server rejects it with HTTP 400)
        for call in mock_session_post.call_args_list:
            data = call[1].get("data", {})
            assert data.get("refresh_token") == "idk_rt"
            assert data.get("grant_type") == "refresh_token"
            assert data.get("code_verifier") is not None

    async def test_idk_refresh_raises_after_max_retries(self):
        """_refresh_idk_token raises AuthenticationError after 3 failed attempts."""
        from volkswagencarnet.vw_exceptions import AuthenticationError as VWAuthError

        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture()
        conn._na_token_endpoint = "https://id.example.com/token"

        fail_response = AsyncMock()
        fail_response.status = 500
        fail_response.text = AsyncMock(return_value="Server Error")

        mock_session_post = AsyncMock(return_value=fail_response)

        with patch.object(conn._session, "post", mock_session_post):
            with patch("asyncio.sleep", AsyncMock()):
                with pytest.raises(VWAuthError):
                    await conn._refresh_idk_token()

        assert mock_session_post.call_count == 3

    # --- Group D: _refresh_brand_token() ---

    async def test_brand_refresh_calls_exchange_brand_token_with_current_idk(self):
        """_refresh_brand_token calls _exchange_brand_token with current IDK access_token."""
        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture()
        conn._na_tokens["idk"]["access_token"] = "current_idk_at"

        mock_exchange = AsyncMock(
            return_value={
                "access_token": "new_brand_at",
                "refresh_token": "new_brand_rt",
                "expires_in": 3600,
            }
        )

        with patch.object(conn, "_exchange_brand_token", mock_exchange):
            await conn._refresh_brand_token()

        assert mock_exchange.call_args[0][0] == "current_idk_at"
        assert conn._na_tokens["brand"]["access_token"] == "new_brand_at"

    # --- Group E: _refresh_mbb_from_refresh_token() ---

    async def test_mbb_refresh_uses_existing_refresh_token(self):
        """_refresh_mbb_from_refresh_token calls _refresh_mbb_token with stored refresh_token."""
        conn = self._make_na_conn()
        conn._xclient_id = "xclient-123"
        conn._na_tokens = self._na_tokens_fixture()
        conn._na_tokens["mbb"]["refresh_token"] = "mbb_rt"

        mock_refresh_mbb = AsyncMock(
            return_value={
                "access_token": "new_mbb_at",
                "refresh_token": "new_mbb_rt",
                "expires_in": 3600,
            }
        )

        with patch.object(conn, "_refresh_mbb_token", mock_refresh_mbb):
            await conn._refresh_mbb_from_refresh_token()

        # Verify the stored refresh_token was passed
        call_kwargs = mock_refresh_mbb.call_args
        assert (
            call_kwargs[1].get("refresh_token") == "mbb_rt"
            or call_kwargs[0][0] == "mbb_rt"
        )
        assert conn._na_tokens["mbb"]["access_token"] == "new_mbb_at"

    async def test_mbb_refresh_falls_back_to_re_exchange_on_failure(self):
        """_refresh_mbb_from_refresh_token falls back to re-exchange via IDK id_token on refresh failure."""
        from volkswagencarnet.vw_exceptions import AuthenticationError as VWAuthError

        conn = self._make_na_conn()
        conn._xclient_id = "xclient-123"
        conn._na_tokens = self._na_tokens_fixture()
        conn._na_tokens["mbb"]["refresh_token"] = "mbb_rt"
        conn._na_tokens["idk"]["id_token"] = "idk_id"

        # First _refresh_mbb_token call fails (primary), second succeeds (fallback after re-exchange)
        mock_refresh_mbb = AsyncMock(
            side_effect=[
                VWAuthError("refresh failed"),
                {
                    "access_token": "fallback_at",
                    "refresh_token": "fallback_rt",
                    "expires_in": 3600,
                },
            ]
        )
        mock_exchange_mbb = AsyncMock(
            return_value={
                "access_token": "init_at",
                "refresh_token": "init_rt",
            }
        )

        with (
            patch.object(conn, "_refresh_mbb_token", mock_refresh_mbb),
            patch.object(conn, "_exchange_mbb_token", mock_exchange_mbb),
        ):
            await conn._refresh_mbb_from_refresh_token()

        assert conn._na_tokens["mbb"]["access_token"] == "fallback_at"
        assert mock_exchange_mbb.call_count == 1  # fallback path triggered

    # --- Group F: _validate_na_tokens() ---

    async def test_validate_na_tokens_no_refresh_when_tokens_fresh(self):
        """_validate_na_tokens returns True without refreshing any tokens when all tokens are fresh."""
        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture(expires_at_offset=7200)  # 2 hours out
        conn._na_auth_level = "full"

        mock_idk_refresh = AsyncMock()
        mock_brand_refresh = AsyncMock()
        mock_mbb_refresh = AsyncMock()

        with (
            patch.object(conn, "_refresh_idk_token", mock_idk_refresh),
            patch.object(conn, "_refresh_brand_token", mock_brand_refresh),
            patch.object(conn, "_refresh_mbb_from_refresh_token", mock_mbb_refresh),
        ):
            result = await conn._validate_na_tokens()

        assert result is True
        assert mock_idk_refresh.call_count == 0
        assert mock_brand_refresh.call_count == 0
        assert mock_mbb_refresh.call_count == 0

    async def test_validate_na_tokens_refreshes_idk_within_15min_window(self):
        """_validate_na_tokens refreshes IDK when it expires within 15-minute window."""
        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture(expires_at_offset=7200)
        conn._na_auth_level = "full"
        # Override IDK to expire within 15-min window (< 900 seconds)
        conn._na_tokens["idk"]["expires_at"] = time.time() + 500

        mock_idk_refresh = AsyncMock()

        with patch.object(conn, "_refresh_idk_token", mock_idk_refresh):
            result = await conn._validate_na_tokens()

        assert result is True
        assert mock_idk_refresh.call_count == 1

    async def test_validate_na_tokens_skipped_for_idk_only_auth_level_mbb(self):
        """_validate_na_tokens skips brand and MBB refresh when auth level is idk_only."""
        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture(expires_at_offset=7200)
        conn._na_auth_level = "idk_only"
        # Make brand and idk expiring (brand within window)
        conn._na_tokens["brand"]["expires_at"] = time.time() + 100

        mock_brand_refresh = AsyncMock()
        mock_mbb_refresh = AsyncMock()

        with (
            patch.object(conn, "_refresh_brand_token", mock_brand_refresh),
            patch.object(conn, "_refresh_mbb_from_refresh_token", mock_mbb_refresh),
        ):
            await conn._validate_na_tokens()

        assert mock_brand_refresh.call_count == 0  # idk_only skips brand
        assert mock_mbb_refresh.call_count == 0  # idk_only skips mbb

    # --- Group G: validate_tokens() NA branch ---

    async def test_validate_tokens_routes_na_to_validate_na_tokens(self):
        """validate_tokens() routes NA connections to _validate_na_tokens()."""
        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture()

        mock_validate_na = AsyncMock(return_value=True)

        with patch.object(conn, "_validate_na_tokens", mock_validate_na):
            result = await conn.validate_tokens()

        assert result is True
        assert mock_validate_na.call_count == 1

    # --- Group H: _request() 401 inline retry ---

    async def test_request_401_triggers_inline_refresh_and_retry_for_na(self):
        """_request() retries once after inline token refresh on 401 for NA connections."""
        conn = self._make_na_conn()
        conn._na_tokens = self._na_tokens_fixture()
        conn._base_api = "https://b-h-s.spr.us00.p.con-veh.net"

        # Mock 401 response (first call)
        mock_response_401 = MagicMock()
        mock_response_401.status = 401
        mock_response_401.raise_for_status = MagicMock()  # does not raise

        # Mock 200 response (second call - retry)
        mock_response_ok = MagicMock()
        mock_response_ok.status = 200
        mock_response_ok.json = AsyncMock(return_value={"data": "ok"})
        mock_response_ok.raise_for_status = MagicMock()
        mock_response_ok.cookies = {}
        mock_response_ok.headers = {}

        # Build async context manager mocks
        cm_401 = MagicMock()
        cm_401.__aenter__ = AsyncMock(return_value=mock_response_401)
        cm_401.__aexit__ = AsyncMock(return_value=False)

        cm_ok = MagicMock()
        cm_ok.__aenter__ = AsyncMock(return_value=mock_response_ok)
        cm_ok.__aexit__ = AsyncMock(return_value=False)

        mock_session_request = MagicMock(side_effect=[cm_401, cm_ok])
        mock_idk_refresh = AsyncMock()

        with (
            patch.object(conn._session, "request", mock_session_request),
            patch.object(conn, "_classify_endpoint", return_value="idk"),
            patch.object(conn, "_refresh_idk_token", mock_idk_refresh),
            patch.object(conn, "update_service_status", AsyncMock()),
        ):
            result = await conn._request(
                "GET", "https://b-h-s.spr.us00.p.con-veh.net/vehicle/v2/vehicles"
            )

        assert mock_idk_refresh.call_count == 1
        assert mock_session_request.call_count == 2  # original + retry

    async def test_request_401_not_triggered_for_emea(self):
        """_request() 401 inline retry is NOT triggered for EMEA connections."""
        from aiohttp import client_exceptions as aiohttp_exc
        import aiohttp

        session = AsyncMock()
        session._cookie_jar = MagicMock()
        session._cookie_jar._cookies = {}
        conn = Connection(session, "user@example.com", "password", country="DE")

        # EMEA 401 path: ClientResponseError is raised by raise_for_status
        ri = MagicMock(aiohttp.RequestInfo)
        e = aiohttp_exc.ClientResponseError(request_info=ri, history=tuple([]))
        e.status = 401

        mock_response_401 = MagicMock()
        mock_response_401.status = 401
        mock_response_401.raise_for_status = MagicMock(side_effect=e)

        cm_401 = MagicMock()
        cm_401.__aenter__ = AsyncMock(return_value=mock_response_401)
        cm_401.__aexit__ = AsyncMock(return_value=False)

        # Use MagicMock directly (not return_value kwarg) so session.request returns cm_401 synchronously
        mock_session_request = MagicMock(return_value=cm_401)

        with (
            patch.object(conn._session, "request", mock_session_request),
            patch.object(conn, "update_service_status", AsyncMock()),
        ):
            # EMEA 401: get() catches ClientResponseError 401 -> sets _session_logged_in=False
            result = await conn.get(
                "https://emea.bff.cariad.digital/vehicle/v2/vehicles"
            )

        # For EMEA, get() handles 401 by setting logged_in=False and returning status_code dict
        assert result == {"status_code": 401}
        assert conn._session_logged_in is False
        # Inline 401 retry guard only activates for NA (session_region == "NA")
        # EMEA path does not attempt token refresh — raise_for_status raises ClientResponseError
        assert mock_session_request.call_count == 1  # no retry for EMEA


# ---------------------------------------------------------------------------
# Phase 23-01: Comprehensive Connection tests
# ---------------------------------------------------------------------------

VIN = "WVWTEST1234567890"
BASE_API = "https://emea.bff.cariad.digital"


def _make_connection(country="DE", **overrides):
    """Create a Connection with mocked session for testing."""
    session = AsyncMock(spec=ClientSession)
    session._cookie_jar = MagicMock()
    session._cookie_jar._cookies = {}
    conn = Connection(session, "test@example.com", "password123", country=country)
    conn._base_api = BASE_API
    for k, v in overrides.items():
        setattr(conn, k, v)
    return conn


def _mock_action_response():
    """Create a mock raw response that _handle_action_result can parse."""
    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(return_value={"data": {"requestID": "req-123"}})
    return mock_resp


# ---------------------------------------------------------------------------
# EMEA OAuth Flow Tests
# ---------------------------------------------------------------------------
class TestEmeaOAuthFlow:
    """Test EMEA OAuth login flow components."""

    @pytest.mark.asyncio
    async def test_get_openid_config_returns_endpoints(self):
        """Mock session.get to return openid config, verify returned dict has auth and token endpoints."""
        conn = _make_connection()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "authorization_endpoint": "https://identity.vwgroup.io/oidc/v1/authorize",
                "token_endpoint": "https://emea.bff.cariad.digital/login/v1/idk/token",
                "issuer": "https://identity.vwgroup.io",
            }
        )
        conn._session.get = AsyncMock(return_value=mock_resp)
        result = await conn.get_openid_config()
        assert "authorization_endpoint" in result
        assert "token_endpoint" in result

    @pytest.mark.asyncio
    async def test_login_emea_full_flow(self):
        """Test EMEA login via _login() dispatching to EMEA path with mocked sub-methods."""
        conn = _make_connection()
        openid_config = {
            "authorization_endpoint": "https://identity.vwgroup.io/oidc/v1/authorize",
            "token_endpoint": "https://emea.bff.cariad.digital/login/v1/idk/token",
            "issuer": "https://identity.vwgroup.io",
        }
        token_response = {
            "access_token": "emea_at",
            "id_token": "emea_id",
            "refresh_token": "emea_rt",
            "token_type": "Bearer",
        }
        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn,
                "_get_authorization_code",
                return_value=("emea_code_123", "emea_id", "emea_at"),
            ),
            patch.object(conn, "_build_session_tokens", return_value=token_response),
        ):
            result = await conn._login()

        assert result is True
        assert conn._session_tokens["identity"]["access_token"] == "emea_at"
        assert conn._session_region == "EMEA"

    @pytest.mark.asyncio
    async def test_login_emea_invalid_credentials(self):
        """Mock auth that raises AuthenticationError, verify _login returns False."""
        conn = _make_connection()
        openid_config = {
            "authorization_endpoint": "https://identity.vwgroup.io/oidc/v1/authorize",
            "token_endpoint": "https://emea.bff.cariad.digital/login/v1/idk/token",
            "issuer": "https://identity.vwgroup.io",
        }
        with (
            patch.object(conn, "get_openid_config", return_value=openid_config),
            patch.object(
                conn,
                "_get_authorization_code",
                side_effect=AuthenticationError("Invalid credentials"),
            ),
        ):
            result = await conn._login()

        # _login() catches AuthenticationError and returns False
        assert result is False

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_success(self):
        """Mock token endpoint POST, verify tokens parsed correctly."""
        conn = _make_connection()
        conn._na_token_endpoint = None  # EMEA path
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(
            return_value='{"access_token":"test_at","refresh_token":"test_rt","id_token":"test_id","token_type":"Bearer","expires_in":3600}'
        )

        mock_post = AsyncMock(return_value=mock_resp)
        with patch.object(conn._session, "post", mock_post):
            result = await conn._exchange_code_for_tokens(
                "test_code",
                "https://emea.bff.cariad.digital/login/v1/idk/token",
            )

        assert result["access_token"] == "test_at"
        assert result["refresh_token"] == "test_rt"

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_error(self):
        """Mock token endpoint returning 400, verify AuthenticationError raised."""
        conn = _make_connection()
        conn._na_token_endpoint = None  # EMEA path
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value='{"error":"invalid_grant"}')

        mock_post = AsyncMock(return_value=mock_resp)
        with patch.object(conn._session, "post", mock_post):
            with pytest.raises(AuthenticationError, match="Token exchange failed"):
                await conn._exchange_code_for_tokens(
                    "bad_code",
                    "https://emea.bff.cariad.digital/login/v1/idk/token",
                )


# ---------------------------------------------------------------------------
# Token Management Tests
# ---------------------------------------------------------------------------
class TestTokenManagement:
    """Test token validation and refresh paths for both regions."""

    @pytest.mark.asyncio
    async def test_validate_tokens_emea_fresh_tokens_no_refresh(self):
        """Set EMEA tokens with long expiry, verify no refresh call."""
        import jwt as pyjwt

        conn = _make_connection()
        # Create fake JWT tokens with far-future expiry
        future_exp = int(time.time()) + 7200
        fake_payload = {"exp": future_exp, "sub": "test"}
        # Use a simple unsigned token for testing
        fake_token = pyjwt.encode(fake_payload, "secret", algorithm="HS256")
        conn._session_tokens["identity"] = {
            "access_token": fake_token,
            "id_token": fake_token,
            "refresh_token": "rt",
        }

        mock_refresh = AsyncMock(return_value=True)
        with patch.object(conn, "refresh_tokens", mock_refresh):
            result = await conn.validate_tokens()

        assert result is True
        mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_validate_tokens_emea_expired_triggers_refresh(self):
        """Set expired EMEA tokens, verify re-login is triggered."""
        import jwt as pyjwt

        conn = _make_connection()
        # Create tokens that expire in the past
        past_exp = int(time.time()) - 100
        fake_payload = {"exp": past_exp, "sub": "test"}
        fake_token = pyjwt.encode(fake_payload, "secret", algorithm="HS256")
        conn._session_tokens["identity"] = {
            "access_token": fake_token,
            "id_token": fake_token,
            "refresh_token": "rt",
        }

        mock_login = AsyncMock(return_value=True)
        mock_get = AsyncMock(return_value={"data": None})
        with (
            patch.object(conn, "_login", mock_login),
            patch.object(conn, "get", mock_get),
        ):
            result = await conn.validate_tokens()

        assert result is True
        mock_login.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_tokens_na_refreshes_idk_only(self):
        """For NA connection, verify only IDK refresh (no Brand/MBB) when idk_only."""
        conn = _make_connection(country="US")
        now = time.time()
        conn._na_tokens = {
            "idk": {
                "access_token": "idk_at",
                "refresh_token": "idk_rt",
                "id_token": "idk_id",
                "expires_at": now + 500,  # within 15-min window
                "issued_at": now,
            },
        }
        conn._na_auth_level = "idk_only"

        mock_idk_refresh = AsyncMock()
        mock_brand_refresh = AsyncMock()
        mock_mbb_refresh = AsyncMock()

        with (
            patch.object(conn, "_refresh_idk_token", mock_idk_refresh),
            patch.object(conn, "_refresh_brand_token", mock_brand_refresh),
            patch.object(conn, "_refresh_mbb_from_refresh_token", mock_mbb_refresh),
        ):
            result = await conn.validate_tokens()

        assert result is True
        mock_idk_refresh.assert_called_once()
        mock_brand_refresh.assert_not_called()
        mock_mbb_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_failure_triggers_full_relogin(self):
        """Mock refresh failure for EMEA, verify doLogin() would be needed."""
        import jwt as pyjwt

        conn = _make_connection()
        past_exp = int(time.time()) - 100
        fake_payload = {"exp": past_exp, "sub": "test"}
        fake_token = pyjwt.encode(fake_payload, "secret", algorithm="HS256")
        conn._session_tokens["identity"] = {
            "access_token": fake_token,
            "id_token": fake_token,
            "refresh_token": "rt",
        }

        mock_refresh = AsyncMock(return_value=False)
        with patch.object(conn, "refresh_tokens", mock_refresh):
            result = await conn.validate_tokens()

        # When refresh fails, validate_tokens returns False (caller should re-login)
        assert result is False


# ---------------------------------------------------------------------------
# Action Methods Tests
# ---------------------------------------------------------------------------
class TestActionMethods:
    """Test every Connection action method."""

    @pytest.mark.asyncio
    async def test_setCharging_start(self):
        """Test setCharging with start action."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        result = await conn.setCharging(VIN, action="start")
        assert result is not None
        assert result.get("id") == "req-123"
        conn.post.assert_called_once()
        call_url = conn.post.call_args[0][0]
        assert "charging/start" in call_url

    @pytest.mark.asyncio
    async def test_setCharging_stop(self):
        """Test setCharging with stop action (falsy)."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        result = await conn.setCharging(VIN, action=False)
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "charging/stop" in call_url

    @pytest.mark.asyncio
    async def test_setClimater_start(self):
        """Test setClimater with start action and data."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        data = {"targetTemperature_C": 22}
        result = await conn.setClimater(VIN, data=data, action="start")
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "climatisation/start" in call_url

    @pytest.mark.asyncio
    async def test_setClimater_stop(self):
        """Test setClimater with stop action."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        result = await conn.setClimater(VIN, data={}, action=False)
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "climatisation/stop" in call_url

    @pytest.mark.asyncio
    async def test_setClimaterSettings(self):
        """Test setClimaterSettings sends PUT to correct endpoint."""
        conn = _make_connection()
        conn.put = AsyncMock(return_value=_mock_action_response())
        data = {"targetTemperature_C": 20}
        result = await conn.setClimaterSettings(VIN, data=data)
        assert result is not None
        call_url = conn.put.call_args[0][0]
        assert "climatisation/settings" in call_url

    @pytest.mark.asyncio
    async def test_setClimatisationTimers(self):
        """Test setClimatisationTimers sends PUT to timers endpoint."""
        conn = _make_connection()
        conn.put = AsyncMock(return_value=_mock_action_response())
        data = {"timers": []}
        result = await conn.setClimatisationTimers(VIN, data=data)
        assert result is not None
        call_url = conn.put.call_args[0][0]
        assert "climatisation/timers" in call_url

    @pytest.mark.asyncio
    async def test_setAuxiliary_start(self):
        """Test setAuxiliary with start action."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        result = await conn.setAuxiliary(VIN, data={}, action="start")
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "auxiliaryheating/start" in call_url

    @pytest.mark.asyncio
    async def test_setAuxiliary_stop(self):
        """Test setAuxiliary with stop action."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        result = await conn.setAuxiliary(VIN, data={}, action=False)
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "auxiliaryheating/stop" in call_url

    @pytest.mark.asyncio
    async def test_setAuxiliaryHeatingTimers(self):
        """Test setAuxiliaryHeatingTimers sends PUT to timers endpoint."""
        conn = _make_connection()
        conn.put = AsyncMock(return_value=_mock_action_response())
        data = {"timers": []}
        result = await conn.setAuxiliaryHeatingTimers(VIN, data=data)
        assert result is not None
        call_url = conn.put.call_args[0][0]
        assert "auxiliaryheating/timers" in call_url

    @pytest.mark.asyncio
    async def test_setWindowHeater_start(self):
        """Test setWindowHeater with start action."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        result = await conn.setWindowHeater(VIN, action="start")
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "windowheating/start" in call_url

    @pytest.mark.asyncio
    async def test_setWindowHeater_stop(self):
        """Test setWindowHeater with stop action."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        result = await conn.setWindowHeater(VIN, action=False)
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "windowheating/stop" in call_url

    @pytest.mark.asyncio
    async def test_setLock_lock(self):
        """Test setLock with lock=True."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        conn.check_spin_state = AsyncMock(return_value=True)
        result = await conn.setLock(VIN, lock=True, spin="1234")
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "access/lock" in call_url

    @pytest.mark.asyncio
    async def test_setLock_unlock(self):
        """Test setLock with lock=False."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        conn.check_spin_state = AsyncMock(return_value=True)
        result = await conn.setLock(VIN, lock=False, spin="1234")
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "access/unlock" in call_url

    @pytest.mark.asyncio
    async def test_setHonkAndFlash(self):
        """Test setHonkAndFlash sends correct position data."""
        conn = _make_connection()
        conn.post = AsyncMock(return_value=_mock_action_response())
        conn.check_spin_state = AsyncMock(return_value=True)
        position = {"lat": 52.520, "lng": 13.405}
        result = await conn.setHonkAndFlash(VIN, position=position)
        assert result is not None
        call_url = conn.post.call_args[0][0]
        assert "honkandflash" in call_url
        # Verify position data is in the json payload
        call_kwargs = conn.post.call_args[1]
        json_data = call_kwargs.get("json", {})
        assert json_data["userPosition"]["latitude"] == 52.520
        assert json_data["userPosition"]["longitude"] == 13.405

    @pytest.mark.asyncio
    async def test_setChargingSettings(self):
        """Test setChargingSettings sends PUT to charging settings."""
        conn = _make_connection()
        conn.put = AsyncMock(return_value=_mock_action_response())
        data = {"maxChargeCurrentAC": "maximum"}
        result = await conn.setChargingSettings(VIN, data=data)
        assert result is not None
        call_url = conn.put.call_args[0][0]
        assert "charging/settings" in call_url

    @pytest.mark.asyncio
    async def test_setChargingCareModeSettings(self):
        """Test setChargingCareModeSettings sends PUT to care settings."""
        conn = _make_connection()
        conn.put = AsyncMock(return_value=_mock_action_response())
        data = {"batteryCareMode": "activated"}
        result = await conn.setChargingCareModeSettings(VIN, data=data)
        assert result is not None
        call_url = conn.put.call_args[0][0]
        assert "charging/care/settings" in call_url

    @pytest.mark.asyncio
    async def test_setReadinessBatterySupport(self):
        """Test setReadinessBatterySupport sends PUT to batterysupport."""
        conn = _make_connection()
        conn.put = AsyncMock(return_value=_mock_action_response())
        data = {"batterySupportEnabled": True}
        result = await conn.setReadinessBatterySupport(VIN, data=data)
        assert result is not None
        call_url = conn.put.call_args[0][0]
        assert "readiness/batterysupport" in call_url

    @pytest.mark.asyncio
    async def test_setDepartureProfiles(self):
        """Test setDepartureProfiles sends PUT to departure profiles."""
        conn = _make_connection()
        conn.put = AsyncMock(return_value=_mock_action_response())
        data = {"profiles": []}
        result = await conn.setDepartureProfiles(VIN, data=data)
        assert result is not None
        call_url = conn.put.call_args[0][0]
        assert "departure/profiles" in call_url

    @pytest.mark.asyncio
    async def test_setDepartureTimers(self):
        """Test setDepartureTimers sends PUT to departure timers."""
        conn = _make_connection()
        conn.put = AsyncMock(return_value=_mock_action_response())
        data = {"timers": []}
        result = await conn.setDepartureTimers(VIN, data=data)
        assert result is not None
        call_url = conn.put.call_args[0][0]
        assert "departure/timers" in call_url

    @pytest.mark.asyncio
    async def test_action_method_raises_api_error_on_exception(self):
        """Test that action methods wrap exceptions in APIError."""
        conn = _make_connection()
        conn.post = AsyncMock(side_effect=Exception("network failure"))
        with pytest.raises(APIError, match="setCharging"):
            await conn.setCharging(VIN, action="start")


# ---------------------------------------------------------------------------
# Data Fetch Methods Tests
# ---------------------------------------------------------------------------
class TestDataFetchMethods:
    """Test all data fetch methods."""

    @pytest.mark.asyncio
    async def test_getSelectiveStatus_returns_data(self):
        """Test getSelectiveStatus returns service data."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(
            return_value={
                "charging": {"chargingState": "readyForCharging"},
                "climatisation": {"climatisationState": "off"},
            }
        )
        result = await conn.getSelectiveStatus(
            VIN, services=["charging", "climatisation"]
        )
        assert result is not None
        assert "refreshTimestamp" in result
        assert "charging" in result

    @pytest.mark.asyncio
    async def test_getSelectiveStatus_returns_false_on_invalid_tokens(self):
        """Test getSelectiveStatus returns False when token validation fails."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=False)
        result = await conn.getSelectiveStatus(VIN, services=["charging"])
        assert result is False

    @pytest.mark.asyncio
    async def test_getVehicleData_returns_data(self):
        """Test getVehicleData returns vehicle data for matching VIN."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(
            return_value={
                "data": [
                    {"vin": VIN, "nickname": "My Car", "model": "ID.4"},
                    {"vin": "OTHER_VIN", "nickname": "Other Car"},
                ]
            }
        )
        result = await conn.getVehicleData(VIN)
        assert result is not None
        assert result["vehicle"]["vin"] == VIN

    @pytest.mark.asyncio
    async def test_getVehicleData_returns_none_for_unknown_vin(self):
        """Test getVehicleData returns None for VIN not in response."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(
            return_value={"data": [{"vin": "OTHER_VIN", "nickname": "Other Car"}]}
        )
        result = await conn.getVehicleData(VIN)
        assert result is None

    @pytest.mark.asyncio
    async def test_getParkingPosition_returns_data(self):
        """Test getParkingPosition returns position data."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(return_value={"data": {"lat": 52.520, "lng": 13.405}})
        result = await conn.getParkingPosition(VIN)
        assert result is not None
        assert result["isMoving"] is False
        assert result["parkingposition"]["lat"] == 52.520

    @pytest.mark.asyncio
    async def test_getParkingPosition_204_is_moving(self):
        """Test getParkingPosition returns isMoving=True on 204."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(return_value={"status_code": 204})
        result = await conn.getParkingPosition(VIN)
        assert result is not None
        assert result["isMoving"] is True

    @pytest.mark.asyncio
    async def test_getTripLast_returns_data(self):
        """Test getTripLast returns trip data."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(
            return_value={"data": {"tripId": "123", "averageSpeed_kmph": 45}}
        )
        result = await conn.getTripLast(VIN)
        assert result is not None
        assert "trip_last" in result

    @pytest.mark.asyncio
    async def test_getTripRefuel_returns_data(self):
        """Test getTripRefuel returns trip since last refuel."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(
            return_value={"data": {"tripId": "456", "fuelConsumption_lper100km": 6.5}}
        )
        result = await conn.getTripRefuel(VIN)
        assert result is not None
        assert "trip_refuel" in result

    @pytest.mark.asyncio
    async def test_getTripLongterm_returns_data(self):
        """Test getTripLongterm returns longterm trip data."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(
            return_value={"data": {"tripId": "789", "totalDistance_km": 15000}}
        )
        result = await conn.getTripLongterm(VIN)
        assert result is not None
        assert "trip_longterm" in result

    @pytest.mark.asyncio
    async def test_getPendingRequests_returns_data(self):
        """Test getPendingRequests returns pending request data."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(
            return_value={"data": [{"id": "req-1", "status": "in_progress"}]}
        )
        result = await conn.getPendingRequests(VIN)
        assert result is not None
        assert "refreshTimestamp" in result

    @pytest.mark.asyncio
    async def test_getOperationList_returns_data(self):
        """Test getOperationList returns capabilities."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(
            return_value={
                "capabilities": [
                    {"id": "charging", "status": [200]},
                    {"id": "climatisation", "status": [200]},
                ]
            }
        )
        result = await conn.getOperationList(VIN)
        assert result is not None
        assert "capabilities" in result

    @pytest.mark.asyncio
    async def test_getOperationList_handles_status_code_error(self):
        """Test getOperationList handles error status code."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(return_value={"status_code": 404})
        result = await conn.getOperationList(VIN)
        assert result is not None
        assert result.get("status_code") == 404

    @pytest.mark.asyncio
    async def test_get_request_status_returns_status(self):
        """Test get_request_status translates pending request status."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.getPendingRequests = AsyncMock(
            return_value={
                "data": [
                    {"id": "req-abc", "status": "request_successful"},
                    {"id": "req-other", "status": "in_progress"},
                ]
            }
        )
        result = await conn.get_request_status(VIN, requestId="req-abc")
        assert result == "Success"

    @pytest.mark.asyncio
    async def test_get_request_status_in_progress(self):
        """Test get_request_status returns In Progress."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.getPendingRequests = AsyncMock(
            return_value={"data": [{"id": "req-1", "status": "in_progress"}]}
        )
        result = await conn.get_request_status(VIN, requestId="req-1")
        assert result == "In Progress"

    @pytest.mark.asyncio
    async def test_get_request_status_failed(self):
        """Test get_request_status returns Failed."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.getPendingRequests = AsyncMock(
            return_value={"data": [{"id": "req-1", "status": "request_fail"}]}
        )
        result = await conn.get_request_status(VIN, requestId="req-1")
        assert result == "Failed"

    @pytest.mark.asyncio
    async def test_get_request_status_unknown_id_returns_unknown(self):
        """Test get_request_status returns Unknown for unmatched request ID."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.getPendingRequests = AsyncMock(
            return_value={"data": [{"id": "req-other", "status": "successful"}]}
        )
        result = await conn.get_request_status(VIN, requestId="req-nonexistent")
        assert result == "Unknown"

    @pytest.mark.asyncio
    async def test_data_fetch_returns_none_on_exception(self):
        """Test that data fetch methods return None (not False) on exceptions."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(
            side_effect=aiohttp.client_exceptions.ClientError("network error")
        )
        result = await conn.getTripLast(VIN)
        assert result is None

    @pytest.mark.asyncio
    async def test_request_preserves_traceback_chain(self):
        """Test that _request() preserves traceback chain (no from None suppression)."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn._session_auth_headers = {"Authorization": "Bearer test"}
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.raise_for_status = MagicMock(
            side_effect=aiohttp.client_exceptions.ClientResponseError(
                request_info=MagicMock(), history=(), status=500, message="Server Error"
            )
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        conn._session.request = MagicMock(return_value=mock_resp)
        with pytest.raises(aiohttp.client_exceptions.ClientResponseError) as exc_info:
            await conn._request("GET", "https://example.com/test")
        # from None sets __suppress_context__ = True; bare raise preserves it as False
        assert exc_info.value.__suppress_context__ is False

    @pytest.mark.asyncio
    async def test_getVehicleData_missing_data_key(self):
        """Test getVehicleData handles response with no 'data' key without crashing."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.get = AsyncMock(return_value={"status": "ok"})
        # Should not raise TypeError; response.get("data") returns None, iterating crashes
        result = await conn.getVehicleData(VIN)
        # Should return None (no matching VIN found in empty iteration)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_request_status_handles_none_response(self):
        """Test get_request_status returns Unknown when getPendingRequests returns None."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.getPendingRequests = AsyncMock(return_value=None)
        result = await conn.get_request_status(VIN, requestId="req-1")
        assert result == "Unknown"


# ---------------------------------------------------------------------------
# Service Status Tests
# ---------------------------------------------------------------------------
class TestServiceStatus:
    """Test service status tracking."""

    @pytest.mark.asyncio
    async def test_update_service_status_stores_up(self):
        """Test that 200 response sets service status to Up."""
        conn = _make_connection()
        await conn.update_service_status("vehicle/v2/vehicles", 200)
        assert conn._service_status["vehicles"] == "Up"

    @pytest.mark.asyncio
    async def test_update_service_status_stores_unauthorized(self):
        """Test that 401 response sets service status to Unauthorized."""
        conn = _make_connection()
        await conn.update_service_status("selectivestatus", 401)
        assert conn._service_status["selectivestatus"] == "Unauthorized"

    @pytest.mark.asyncio
    async def test_update_service_status_stores_rate_limited(self):
        """Test that 429 response sets service status to Rate limited."""
        conn = _make_connection()
        await conn.update_service_status("token", 429)
        assert conn._service_status["token"] == "Rate limited"

    @pytest.mark.asyncio
    async def test_update_service_status_stores_down(self):
        """Test that 500 response sets service status to Down."""
        conn = _make_connection()
        await conn.update_service_status("parkingposition", 500)
        assert conn._service_status["parkingposition"] == "Down"

    @pytest.mark.asyncio
    async def test_get_service_status_returns_stored(self):
        """Test get_service_status returns full status dict."""
        conn = _make_connection()
        await conn.update_service_status("token", 200)
        result = await conn.get_service_status()
        assert result["token"] == "Up"

    @pytest.mark.asyncio
    async def test_get_service_status_default_empty(self):
        """Test get_service_status returns default dict for fresh connection."""
        conn = _make_connection()
        result = await conn.get_service_status()
        assert isinstance(result, dict)


# ===========================================================================
# Merged from phase21_pr_review_fixes_test.py
# ===========================================================================

# Shared helpers for Phase 21 tests
VW_CONNECTION_SRC = (
    Path(__file__).parent.parent / "volkswagencarnet" / "vw_connection.py"
)
FAKE_IDK_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.fake-token-value"
FAKE_IDK_ID_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.fake-id-token"
FAKE_SPIN = "0560"
FAKE_CHALLENGE = "1D02046F451D9ECCA3E4FB6B564958DF0495A069"
USER_ID = "user-sub-12345"
VIN = "WVWZZZ3HZPK002581"
BASE_API = "https://b-h-s.spr.us00.p.con-veh.net"


def _make_na_connection() -> Connection:
    """Create a minimal NA Connection with a mocked aiohttp session."""
    sess = MagicMock()
    sess._cookie_jar = MagicMock()
    sess._cookie_jar._cookies = {}
    conn = Connection(sess, "user@test.com", "password", country="US")
    conn._base_api = BASE_API
    return conn


def _make_na_connection_with_tokens(spin: str | None = None) -> Connection:
    """Create an NA Connection with IDK tokens pre-populated.

    Pre-populates a vehicle_session entry for VIN with an already-expired token
    so that _create_na_vehicle_session bypasses the cache on the first call,
    while still satisfying the Fix 2 guard in _get_na_vehicle_data (the key exists).
    """
    conn = _make_na_connection()
    if spin:
        conn._spin = spin
    conn._na_tokens = {
        "idk": {
            "id_token": FAKE_IDK_ID_TOKEN,
            "access_token": FAKE_IDK_ACCESS_TOKEN,
        },
        VIN: {
            "tsp_provider": "ATC",
            "vehicle_session": {
                "token": "expired-vehicle-token",
                "expires_at": time.time() - 3600,  # already expired
                "issued_at": time.time() - 7200,
            },
        },
    }
    return conn


def _mock_resp(status: int = 200, json_data=None, text_data: str = ""):
    """Build a lightweight mock aiohttp response."""
    r = MagicMock()
    r.status = status
    r.json = AsyncMock(return_value=json_data if json_data is not None else {})
    r.text = AsyncMock(return_value=text_data)
    return r


def _read_source() -> str:
    """Read vw_connection.py source code."""
    return VW_CONNECTION_SRC.read_text()


class SpinRedactionTest(IsolatedAsyncioTestCase):
    """Tests for C-2: SPIN plaintext never appears in log output."""

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_spin_hash_length_logged_not_spin_value(self, mock_jwt):
        """During vehicle session challenge, log shows hash length, not SPIN value."""
        mock_jwt.side_effect = [{"sub": USER_ID}, {"exp": int(time.time()) + 3600}]
        conn = _make_na_connection_with_tokens(spin=FAKE_SPIN)

        challenge_resp = _mock_resp(
            200, {"challenge": FAKE_CHALLENGE, "remainingTries": 6}
        )
        conn._session.get = AsyncMock(return_value=challenge_resp)
        conn._session.post = AsyncMock(
            return_value=_mock_resp(200, {"carnetVehicleToken": "fake-vehicle-token"})
        )

        with self.assertLogs("volkswagencarnet.vw_connection", level="DEBUG") as log:
            await conn._create_na_vehicle_session(VIN)

        all_logs = "\n".join(log.output)
        self.assertNotIn(FAKE_SPIN, all_logs)
        self.assertIn("len=128", all_logs)

    def test_no_self_spin_in_logger_calls(self):
        """Source code contains no _LOGGER call that logs self._spin directly."""
        source = _read_source()
        matches = re.findall(r"_LOGGER\.\w+\([^)]*self\._spin[^)]*\)", source)
        self.assertEqual(
            len(matches),
            0,
            f"Found {len(matches)} _LOGGER call(s) referencing self._spin: {matches}",
        )

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_spin_redaction_with_no_spin_set(self, mock_jwt):
        """When no SPIN is set, no SPIN-related data appears in logs at all."""
        mock_jwt.side_effect = [{"sub": USER_ID}, {"exp": int(time.time()) + 3600}]
        conn = _make_na_connection_with_tokens(spin=None)

        conn._session.post = AsyncMock(
            return_value=_mock_resp(200, {"carnetVehicleToken": "fake-vehicle-token"})
        )
        conn._session.get = AsyncMock()

        with self.assertLogs("volkswagencarnet.vw_connection", level="DEBUG") as log:
            await conn._create_na_vehicle_session(VIN)

        all_logs = "\n".join(log.output)
        self.assertNotIn("spinHash computed", all_logs)
        conn._session.get.assert_not_called()

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_challenge_response_logs_only_key_names(self, mock_jwt):
        """Challenge response logs only dictionary key names, not values."""
        mock_jwt.side_effect = [{"sub": USER_ID}, {"exp": int(time.time()) + 3600}]
        conn = _make_na_connection_with_tokens(spin=FAKE_SPIN)

        challenge_data = {"challenge": FAKE_CHALLENGE, "remainingTries": 6}
        conn._session.get = AsyncMock(return_value=_mock_resp(200, challenge_data))
        conn._session.post = AsyncMock(
            return_value=_mock_resp(200, {"carnetVehicleToken": "fake-token"})
        )

        with self.assertLogs("volkswagencarnet.vw_connection", level="DEBUG") as log:
            await conn._create_na_vehicle_session(VIN)

        all_logs = "\n".join(log.output)
        self.assertNotIn(FAKE_CHALLENGE, all_logs)
        self.assertIn("challenge", all_logs)


class AuthHeaderRedactionTest(IsolatedAsyncioTestCase):
    """Tests for C-3: Auth headers logged as key names only."""

    async def test_get_authorization_page_logs_header_keys_only(self):
        """get_authorization_page logs header key names, not header values."""
        conn = _make_na_connection()
        conn._session_auth_headers = {
            "Authorization": f"Bearer {FAKE_IDK_ACCESS_TOKEN}",
            "Accept": "text/html",
            "User-Agent": "okhttp/5.0.0-alpha.2",
        }
        conn._session_region_config = {
            "redirect_uri": "kombi:///login",
            "scope": "openid",
        }
        conn._client_id = "test-client-id"
        conn._session_country = "US"

        mock_resp = MagicMock()
        mock_resp.status = 302
        mock_resp.headers = {"Location": "https://identity.na.vwgroup.io/login"}
        mock_resp.text = AsyncMock(return_value="")
        conn._session.get = AsyncMock(return_value=mock_resp)

        with self.assertLogs("volkswagencarnet.vw_connection", level="DEBUG") as log:
            try:
                await conn.get_authorization_page(
                    "https://identity.na.vwgroup.io/authorize"
                )
            except Exception:
                pass

        all_logs = "\n".join(log.output)
        self.assertNotIn(FAKE_IDK_ACCESS_TOKEN, all_logs)
        self.assertNotIn("eyJ0eXA", all_logs)

    async def test_auth_header_log_contains_no_bearer_tokens(self):
        """No Bearer token string appears in any log from get_authorization_page."""
        conn = _make_na_connection()
        conn._session_auth_headers = {
            "Authorization": "Bearer super-secret-token-12345",
            "Accept": "text/html",
        }
        conn._session_region_config = {
            "redirect_uri": "kombi:///login",
            "scope": "openid",
        }
        conn._client_id = "test-client-id"
        conn._session_country = "US"

        mock_resp = MagicMock()
        mock_resp.status = 302
        mock_resp.headers = {"Location": "https://identity.na.vwgroup.io/login"}
        mock_resp.text = AsyncMock(return_value="")
        conn._session.get = AsyncMock(return_value=mock_resp)

        with self.assertLogs("volkswagencarnet.vw_connection", level="DEBUG") as log:
            try:
                await conn.get_authorization_page(
                    "https://identity.na.vwgroup.io/authorize"
                )
            except Exception:
                pass

        all_logs = "\n".join(log.output)
        self.assertNotIn("super-secret-token-12345", all_logs)
        self.assertNotIn("Bearer ", all_logs)

    async def test_auth_header_keys_logged_as_python_list(self):
        """No auth header values (bearer tokens) appear in logs from get_authorization_page."""
        conn = _make_na_connection()
        conn._session_auth_headers = {
            "Authorization": "Bearer secret-token-xyz",
            "Accept": "text/html",
        }
        conn._session_region_config = {
            "redirect_uri": "kombi:///login",
            "scope": "openid",
        }
        conn._client_id = "test-client-id"
        conn._session_country = "US"

        mock_resp = MagicMock()
        mock_resp.status = 302
        mock_resp.headers = {"Location": "https://identity.na.vwgroup.io/login"}
        mock_resp.text = AsyncMock(return_value="")
        conn._session.get = AsyncMock(return_value=mock_resp)

        with self.assertLogs("volkswagencarnet.vw_connection", level="DEBUG") as log:
            try:
                await conn.get_authorization_page(
                    "https://identity.na.vwgroup.io/authorize"
                )
            except Exception:
                pass

        all_logs = "\n".join(log.output)
        self.assertNotIn("secret-token-xyz", all_logs)
        self.assertNotIn("Bearer ", all_logs)


class DoLoginRetryTest(IsolatedAsyncioTestCase):
    """Tests for H-2: doLogin retry loop uses for/else with correct exhaustion."""

    async def test_doLogin_returns_false_on_single_failure(self):
        """doLogin(tries=1) returns False when the single login attempt fails."""
        conn = _make_na_connection()
        conn._login = AsyncMock(return_value=False)
        conn._discover_market_config = AsyncMock(return_value=True)

        result = await conn.doLogin(tries=1)

        self.assertFalse(result)
        self.assertEqual(conn._login.call_count, 1)

    async def test_doLogin_succeeds_on_second_attempt(self):
        """doLogin(tries=3) succeeds when second login attempt returns True."""
        conn = _make_na_connection()
        conn._login = AsyncMock(side_effect=[False, True])
        conn._discover_market_config = AsyncMock(return_value=True)
        conn._session_tokens = {
            "identity": {"access_token": "test", "id_token": "test-id"}
        }
        conn._session_headers = {"Authorization": ""}

        conn._request = AsyncMock(return_value={"data": {"vehicles": []}})
        conn.update = AsyncMock()

        with patch(
            "volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock
        ):
            result = await conn.doLogin(tries=3)

        self.assertTrue(result)
        self.assertEqual(conn._login.call_count, 2)

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    async def test_doLogin_returns_false_after_exhausting_all_tries(self, mock_sleep):
        """doLogin(tries=3) returns False after all 3 attempts fail."""
        conn = _make_na_connection()
        conn._login = AsyncMock(return_value=False)
        conn._discover_market_config = AsyncMock(return_value=True)

        result = await conn.doLogin(tries=3)

        self.assertFalse(result)
        self.assertEqual(conn._login.call_count, 3)

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    async def test_doLogin_logs_error_on_exhaustion(self, mock_sleep):
        """doLogin logs an error message when all retry attempts are exhausted."""
        conn = _make_na_connection()
        conn._login = AsyncMock(return_value=False)
        conn._discover_market_config = AsyncMock(return_value=True)

        with self.assertLogs("volkswagencarnet.vw_connection", level="ERROR") as log:
            await conn.doLogin(tries=2)

        all_logs = "\n".join(log.output)
        self.assertIn("Login failed after", all_logs)
        self.assertIn("2", all_logs)

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    async def test_doLogin_sleeps_between_retries_but_not_after_last(self, mock_sleep):
        """Sleep is called between retry attempts but not after the final failure."""
        conn = _make_na_connection()
        conn._login = AsyncMock(return_value=False)
        conn._discover_market_config = AsyncMock(return_value=True)

        await conn.doLogin(tries=3)

        self.assertEqual(mock_sleep.call_count, 2)

    def test_doLogin_uses_for_else_pattern(self):
        """doLogin source code uses for/else pattern (not if i > tries)."""
        import ast

        source = _read_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "doLogin":
                doLogin_source = ast.get_source_segment(source, node)
                if doLogin_source:
                    self.assertIn("for i in range(tries)", doLogin_source)
                    self.assertNotIn("if i > tries", doLogin_source)
                    self.assertNotIn("if i >= tries", doLogin_source)
                break


class Phase21IntegrationTest(IsolatedAsyncioTestCase):
    """Integration tests verifying Phase 21 fixes don't regress each other."""

    def test_connection_class_is_importable(self):
        """Connection class imports without error after all Phase 21 changes."""
        from volkswagencarnet.vw_connection import Connection as Conn

        self.assertTrue(callable(Conn))

    def test_connection_has_expected_login_method(self):
        """Connection.doLogin still exists and is async after retry loop fix."""
        self.assertTrue(hasattr(Connection, "doLogin"))
        self.assertTrue(inspect.iscoroutinefunction(Connection.doLogin))

    def test_connection_has_request_method(self):
        """Connection._request still exists after response body log removal."""
        self.assertTrue(hasattr(Connection, "_request"))
        self.assertTrue(inspect.iscoroutinefunction(Connection._request))

    def test_connection_has_get_authorization_page(self):
        """Connection.get_authorization_page still exists after header log fix."""
        self.assertTrue(hasattr(Connection, "get_authorization_page"))
        self.assertTrue(inspect.iscoroutinefunction(Connection.get_authorization_page))

    def test_connection_still_has_discover_market_config(self):
        """_discover_market_config (live discovery) still exists."""
        self.assertTrue(hasattr(Connection, "_discover_market_config"))

    async def test_na_connection_initialization_after_dead_code_removal(self):
        """NA Connection can be instantiated after _discover_endpoints removal."""
        conn = _make_na_connection()
        self.assertEqual(conn._session_region, "NA")
        self.assertFalse(hasattr(conn, "_discover_endpoints"))


# ===========================================================================
# Merged from phase22_constants_and_dedup_test.py
# ===========================================================================

_TEST_VIN = "TESTVIN22PHASE001"


def _make_mock_response(status, json_data=None, text_data=""):
    """Build a lightweight mock aiohttp response for phase22 tests."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.text = AsyncMock(return_value=text_data)
    mock_resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    return mock_resp


def _make_na_connection_for_rvs() -> Connection:
    """Create a minimal NA Connection for _fetch_rvs_endpoint testing."""
    sess = MagicMock()
    conn = Connection(sess, "user@test.com", "password", country="US")
    conn._base_api = "https://b-h-s.spr.us00.p.con-veh.net"
    conn._na_tokens = {
        _TEST_VIN: {
            "vehicle_session": {
                "token": "fake.vehicle.token",
                "expires_at": 9999999999,
                "issued_at": 0,
            },
        },
    }
    conn._create_na_vehicle_session = AsyncMock(return_value="refreshed.vehicle.token")
    return conn


class FetchRvsEndpointTest(IsolatedAsyncioTestCase):
    """Tests for the _fetch_rvs_endpoint helper method."""

    async def test_fetch_rvs_endpoint_200_returns_parsed_json(self):
        """200 response returns parsed JSON dict directly."""
        conn = _make_na_connection_for_rvs()
        expected = {"lockStatus": "LOCKED", "platform": "VW_NA"}
        conn._session.get = AsyncMock(
            return_value=_make_mock_response(200, json_data=expected)
        )

        result = await conn._fetch_rvs_endpoint(
            url="https://example.com/rvs/v1/vehicle/VIN",
            vin=_TEST_VIN,
            rvs_headers={"Authorization": "Bearer token"},
            label="status",
        )

        assert result == expected

    async def test_fetch_rvs_endpoint_200_unwraps_data_key(self):
        """200 response with {"data": {...}} envelope unwraps the inner dict."""
        conn = _make_na_connection_for_rvs()
        inner = {"latitude": 40.0, "longitude": -74.0}
        conn._session.get = AsyncMock(
            return_value=_make_mock_response(200, json_data={"data": inner})
        )

        result = await conn._fetch_rvs_endpoint(
            url="https://example.com/rvs/v1/location/vehicle/VIN",
            vin=_TEST_VIN,
            rvs_headers={"Authorization": "Bearer token"},
            label="location",
        )

        assert result == inner

    async def test_fetch_rvs_endpoint_401_refreshes_session_and_retries(self):
        """401 triggers session recreation, updates headers, and retries the request."""
        conn = _make_na_connection_for_rvs()
        expected = {"lockStatus": "LOCKED"}
        conn._session.get = AsyncMock(
            side_effect=[
                _make_mock_response(401),
                _make_mock_response(200, json_data=expected),
            ]
        )

        headers = {"Authorization": "Bearer old-token"}
        result = await conn._fetch_rvs_endpoint(
            url="https://example.com/rvs/v1/vehicle/VIN",
            vin=_TEST_VIN,
            rvs_headers=headers,
            label="status",
        )

        assert result == expected
        conn._create_na_vehicle_session.assert_called_once_with(_TEST_VIN)
        assert headers["Authorization"] == "Bearer refreshed.vehicle.token"

    async def test_fetch_rvs_endpoint_401_returns_none_when_session_refresh_fails(self):
        """401 with failed session recreation returns None immediately."""
        conn = _make_na_connection_for_rvs()
        conn._create_na_vehicle_session = AsyncMock(return_value=None)
        conn._session.get = AsyncMock(return_value=_make_mock_response(401))

        result = await conn._fetch_rvs_endpoint(
            url="https://example.com/rvs/v1/vehicle/VIN",
            vin=_TEST_VIN,
            rvs_headers={"Authorization": "Bearer token"},
            label="status",
        )

        assert result is None

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    async def test_fetch_rvs_endpoint_5xx_retries_with_backoff(self, _mock_sleep):
        """5xx responses retry up to RVS_MAX_RETRIES times, succeeding on last attempt."""
        conn = _make_na_connection_for_rvs()
        expected = {"lockStatus": "LOCKED"}
        conn._session.get = AsyncMock(
            side_effect=[
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(200, json_data=expected),
            ]
        )

        result = await conn._fetch_rvs_endpoint(
            url="https://example.com/rvs/v1/vehicle/VIN",
            vin=_TEST_VIN,
            rvs_headers={"Authorization": "Bearer token"},
            label="status",
        )

        assert result == expected
        assert conn._session.get.call_count == 3

    async def test_fetch_rvs_endpoint_non_5xx_non_200_breaks_immediately(self):
        """403/404 does not retry -- returns None after first attempt."""
        conn = _make_na_connection_for_rvs()
        conn._session.get = AsyncMock(
            return_value=_make_mock_response(403, text_data="Forbidden")
        )

        result = await conn._fetch_rvs_endpoint(
            url="https://example.com/rvs/v1/vehicle/VIN",
            vin=_TEST_VIN,
            rvs_headers={"Authorization": "Bearer token"},
            label="status",
        )

        assert result is None
        assert conn._session.get.call_count == 1

    async def test_fetch_rvs_endpoint_exception_returns_none(self):
        """Network exception is caught and returns None."""
        conn = _make_na_connection_for_rvs()
        conn._session.get = AsyncMock(
            side_effect=aiohttp.ClientError("Connection reset")
        )

        result = await conn._fetch_rvs_endpoint(
            url="https://example.com/rvs/v1/vehicle/VIN",
            vin=_TEST_VIN,
            rvs_headers={"Authorization": "Bearer token"},
            label="status",
        )

        assert result is None


class IsNaPropertyTest(IsolatedAsyncioTestCase):
    """Tests for Connection.is_na property."""

    def test_is_na_true_for_us_country(self):
        """country='US' sets session_region to NA, so is_na returns True."""
        sess = MagicMock()
        conn = Connection(sess, "user@test.com", "password", country="US")
        assert conn.is_na is True

    def test_is_na_false_for_de_country(self):
        """country='DE' defaults to EMEA region, so is_na returns False."""
        sess = MagicMock()
        conn = Connection(sess, "user@test.com", "password", country="DE")
        assert conn.is_na is False


class ConstantsTest(IsolatedAsyncioTestCase):
    """Tests for Phase 22-01 extracted constants."""

    def test_constants_used_in_user_agent(self):
        """USER_AGENT string contains APP_VERSION_SHORT."""
        assert APP_VERSION_SHORT in USER_AGENT
        assert "Volkswagen/" in USER_AGENT

    def test_app_version_format(self):
        """APP_VERSION matches expected format pattern."""
        assert APP_VERSION == "2025.12.10-8414"

    def test_max_redirect_depth_is_positive_int(self):
        """MAX_REDIRECT_DEPTH is a positive integer."""
        assert isinstance(MAX_REDIRECT_DEPTH, int)
        assert MAX_REDIRECT_DEPTH > 0

    def test_country_to_locale_has_expected_keys(self):
        """COUNTRY_TO_LOCALE contains US, CA, GB mappings."""
        assert "US" in COUNTRY_TO_LOCALE
        assert "CA" in COUNTRY_TO_LOCALE
        assert "GB" in COUNTRY_TO_LOCALE
        assert COUNTRY_TO_LOCALE["US"] == "en-US"


# ===========================================================================
# Merged from na_connection_test.py
# ===========================================================================

NA_FIXTURE_DIR = (
    Path(__file__).parent / "fixtures" / "resources" / "responses" / "na_vehicle"
)


def _load_na_fixture(name: str) -> dict:
    with open(NA_FIXTURE_DIR / name) as f:
        return json.load(f)


class NAVehicleSessionTest(IsolatedAsyncioTestCase):
    """Tests for Connection._create_na_vehicle_session()."""

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_create_session_stores_spin_from_constructor(self, _mock_jwt):
        """Connection stores spin parameter passed at construction time."""
        sess = MagicMock()
        conn = Connection(
            sess, "user@test.com", "password", country="US", spin=FAKE_SPIN
        )
        assert conn._spin == FAKE_SPIN

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_create_session_spin_defaults_to_none(self, _mock_jwt):
        """Connection._spin defaults to None when spin not passed."""
        sess = MagicMock()
        conn = Connection(sess, "user@test.com", "password", country="US")
        assert conn._spin is None

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_create_session_fetches_challenge_and_includes_spinhash(
        self, mock_jwt
    ):
        """When spin is set: GET challenge with IDK Bearer + x-user-id, then POST with spinHash."""
        mock_jwt.side_effect = [{"sub": USER_ID}, {"exp": int(time.time()) + 3600}]
        conn = _make_na_connection_with_tokens(spin=FAKE_SPIN)
        conn._session.get = AsyncMock(
            return_value=_mock_resp(
                200, {"challenge": FAKE_CHALLENGE, "remainingTries": 6}
            )
        )
        conn._session.post = AsyncMock(
            return_value=_mock_resp(200, {"carnetVehicleToken": "fake-vehicle-token"})
        )
        result = await conn._create_na_vehicle_session(VIN)
        assert result == "fake-vehicle-token"
        assert conn._session.get.call_count == 1
        challenge_call = conn._session.get.call_args_list[0]
        challenge_url = challenge_call[0][0]
        assert f"/ss/v1/user/{USER_ID}/challenge" in challenge_url
        challenge_headers = challenge_call.kwargs.get("headers", {})
        assert (
            challenge_headers.get("Authorization") == f"Bearer {FAKE_IDK_ACCESS_TOKEN}"
        )
        assert challenge_headers.get("x-user-id") == USER_ID
        assert conn._session.post.call_count == 1
        post_body = conn._session.post.call_args.kwargs["json"]
        assert post_body["spinHash"] is not None
        assert isinstance(post_body["spinHash"], str)
        assert len(post_body["spinHash"]) == 128

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_create_session_skips_challenge_and_sends_null_spinhash_when_no_spin(
        self, mock_jwt
    ):
        """When no spin set, skips challenge GET and sends spinHash=None."""
        mock_jwt.side_effect = [{"sub": USER_ID}, {"exp": int(time.time()) + 3600}]
        conn = _make_na_connection_with_tokens()
        conn._session.post = AsyncMock(
            return_value=_mock_resp(200, {"carnetVehicleToken": "fake-vehicle-token"})
        )
        conn._session.get = AsyncMock()
        await conn._create_na_vehicle_session(VIN)
        conn._session.get.assert_not_called()
        post_body = conn._session.post.call_args.kwargs["json"]
        assert post_body["spinHash"] is None

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_create_session_returns_none_when_challenge_fetch_fails(
        self, _mock_jwt
    ):
        """Returns None when challenge GET fails (non-200)."""
        conn = _make_na_connection_with_tokens(spin=FAKE_SPIN)
        conn._session.get = AsyncMock(
            return_value=_mock_resp(401, text_data="Unauthorized")
        )
        conn._session.post = AsyncMock()
        result = await conn._create_na_vehicle_session(VIN)
        assert result is None
        assert conn._session.get.call_count == 1
        conn._session.post.assert_not_called()

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_create_session_returns_none_when_idk_missing(self, mock_jwt):
        """Returns None immediately when no IDK id_token is present."""
        conn = _make_na_connection()
        conn._na_tokens = {}
        result = await conn._create_na_vehicle_session(VIN)
        assert result is None
        mock_jwt.assert_not_called()

    @patch(
        "volkswagencarnet.vw_connection.jwt.decode",
        side_effect=jwt.exceptions.InvalidTokenError("bad jwt"),
    )
    async def test_create_session_returns_none_on_invalid_jwt(self, _mock_jwt):
        """Returns None when IDK id_token fails JWT decode."""
        conn = _make_na_connection_with_tokens()
        result = await conn._create_na_vehicle_session(VIN)
        assert result is None

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_create_session_returns_cached_token(self, _mock_jwt):
        """Returns the cached vehicle token without any HTTP call when cache is fresh."""
        conn = _make_na_connection_with_tokens()
        conn._na_tokens[VIN] = {
            "vehicle_session": {
                "token": "cached-vehicle-token",
                "expires_at": time.time() + 3600,
                "issued_at": time.time(),
            }
        }
        result = await conn._create_na_vehicle_session(VIN)
        assert result == "cached-vehicle-token"
        conn._session.post.assert_not_called()

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_create_session_uses_tsp_provider_from_na_tokens(self, mock_jwt):
        """Session POST uses tsp value from _na_tokens[vin]['tsp_provider']."""
        mock_jwt.side_effect = [{"sub": USER_ID}, {"exp": int(time.time()) + 3600}]
        conn = _make_na_connection_with_tokens()
        conn._na_tokens[VIN]["tsp_provider"] = "ATC"
        conn._session.post = AsyncMock(
            return_value=_mock_resp(200, {"carnetVehicleToken": "fake-vehicle-token"})
        )
        result = await conn._create_na_vehicle_session(VIN)
        assert result == "fake-vehicle-token"
        assert conn._session.post.call_count == 1
        call = conn._session.post.call_args_list[0]
        assert call.kwargs["json"]["tsp"] == "ATC"

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_create_session_defaults_to_atc_when_no_tsp_provider(self, mock_jwt):
        """Falls back to tsp='ATC' when no tsp_provider is stored for the VIN."""
        mock_jwt.side_effect = [{"sub": USER_ID}, {"exp": int(time.time()) + 3600}]
        conn = _make_na_connection()
        conn._na_tokens = {
            "idk": {
                "id_token": FAKE_IDK_ID_TOKEN,
                "access_token": FAKE_IDK_ACCESS_TOKEN,
            },
        }
        conn._session.post = AsyncMock(
            return_value=_mock_resp(200, {"carnetVehicleToken": "fake-vehicle-token"})
        )
        result = await conn._create_na_vehicle_session(VIN)
        assert result == "fake-vehicle-token"
        call = conn._session.post.call_args_list[0]
        assert call.kwargs["json"]["tsp"] == "ATC"

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_create_session_retries_without_auth_on_401(self, mock_jwt):
        """On 401 response, retries the same tsp value without Authorization header."""
        mock_jwt.side_effect = [{"sub": USER_ID}, {"exp": int(time.time()) + 3600}]
        conn = _make_na_connection_with_tokens()
        conn._session.post = AsyncMock(
            side_effect=[
                _mock_resp(401),
                _mock_resp(200, {"carnetVehicleToken": "fake-vehicle-token"}),
            ]
        )
        result = await conn._create_na_vehicle_session(VIN)
        assert result == "fake-vehicle-token"
        assert conn._session.post.call_count == 2
        no_auth_call = conn._session.post.call_args_list[1]
        assert "Authorization" not in no_auth_call.kwargs["headers"]

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_create_session_caches_token_with_exp(self, mock_jwt):
        """Stores token in _na_tokens[vin]['vehicle_session'] with JWT exp as expires_at."""
        future_exp = int(time.time()) + 3600
        mock_jwt.side_effect = [{"sub": USER_ID}, {"exp": future_exp}]
        conn = _make_na_connection_with_tokens()
        conn._session.post = AsyncMock(
            return_value=_mock_resp(200, {"carnetVehicleToken": "fake-vehicle-token"})
        )
        result = await conn._create_na_vehicle_session(VIN)
        assert result == "fake-vehicle-token"
        cached = conn._na_tokens[VIN]["vehicle_session"]
        assert cached["token"] == "fake-vehicle-token"
        assert cached["expires_at"] == future_exp
        assert "issued_at" in cached

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_create_session_returns_none_when_tsp_fails(self, _mock_jwt):
        """Returns None when session POST returns 400."""
        conn = _make_na_connection_with_tokens()
        conn._session.post = AsyncMock(
            return_value=_mock_resp(400, text_data="Bad Request")
        )
        result = await conn._create_na_vehicle_session(VIN)
        assert result is None
        assert conn._session.post.call_count == 1


class NAVehicleDataFetchTest(IsolatedAsyncioTestCase):
    """Tests for Connection._get_na_vehicle_data()."""

    async def test_get_na_vehicle_data_returns_none_when_session_fails(self):
        """Returns None when vehicle session creation fails."""
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._create_na_vehicle_session = AsyncMock(return_value=None)
        result = await conn._get_na_vehicle_data(VIN)
        assert result is None

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_get_na_vehicle_data_returns_partial_on_location_failure(
        self, _mock_jwt, _mock_sleep
    ):
        """Returns {'na_location': None, 'na_status': {...}} when location endpoint always 500s."""
        status_fixture = _load_na_fixture("rvs_status.json")
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._create_na_vehicle_session = AsyncMock(return_value="fake-vehicle-token")
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(
                    500, text_data="Internal Server Error"
                ),  # location attempt 1/3 (500)
                _mock_resp(
                    500, text_data="Internal Server Error"
                ),  # location attempt 2/3 (500)
                _mock_resp(
                    500, text_data="Internal Server Error"
                ),  # location attempt 3/3 (500 → give up)
                _mock_resp(200, json_data=status_fixture),  # status (200)
                _mock_resp(404),  # ev_charge (404 → None)
                _mock_resp(404),  # climate_settings (404 → None)
                _mock_resp(404),  # trip_stats (404 → None)
            ]
        )
        result = await conn._get_na_vehicle_data(VIN)
        assert result is not None
        assert result["na_location"] is None
        assert result["na_status"] == status_fixture
        assert result["na_ev"] is None
        assert result["na_climate"] is None
        assert result["na_trip"] is None

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_get_na_vehicle_data_returns_partial_on_status_failure(
        self, _mock_jwt, _mock_sleep
    ):
        """Returns {'na_location': {...}, 'na_status': None} when status endpoint always 500s."""
        location_fixture = _load_na_fixture("rvs_location.json")
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._create_na_vehicle_session = AsyncMock(return_value="fake-vehicle-token")
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(200, json_data=location_fixture),  # location (200)
                _mock_resp(
                    500, text_data="Internal Server Error"
                ),  # status attempt 1/3 (500)
                _mock_resp(
                    500, text_data="Internal Server Error"
                ),  # status attempt 2/3 (500)
                _mock_resp(
                    500, text_data="Internal Server Error"
                ),  # status attempt 3/3 (500 → give up)
                _mock_resp(404),  # ev_charge (404 → None)
                _mock_resp(404),  # climate_settings (404 → None)
                _mock_resp(404),  # trip_stats (404 → None)
            ]
        )
        result = await conn._get_na_vehicle_data(VIN)
        assert result is not None
        assert result["na_location"] == location_fixture
        assert result["na_status"] is None
        assert result["na_ev"] is None
        assert result["na_climate"] is None
        assert result["na_trip"] is None

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_get_na_vehicle_data_invalidates_cache_on_401(self, _mock_jwt):
        """On 401 from RVS: clears vehicle_session cache and retries with fresh token."""
        location_fixture = _load_na_fixture("rvs_location.json")
        status_fixture = _load_na_fixture("rvs_status.json")
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._create_na_vehicle_session = AsyncMock(return_value="fake-vehicle-token")
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(401),  # location attempt 1 (401 → refresh session)
                _mock_resp(
                    200, json_data=location_fixture
                ),  # location retry after 401 (200)
                _mock_resp(200, json_data=status_fixture),  # status (200)
                _mock_resp(404),  # ev_charge (404 → None)
                _mock_resp(404),  # climate_settings (404 → None)
                _mock_resp(404),  # trip_stats (404 → None)
            ]
        )
        result = await conn._get_na_vehicle_data(VIN)
        assert result is not None
        assert result["na_location"] == location_fixture
        assert result["na_status"] == status_fixture
        assert conn._create_na_vehicle_session.call_count == 2

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_get_na_vehicle_data_includes_ev_climate_trip_on_success(
        self, _mock_jwt
    ):
        """When all optional endpoints return 200, result dict contains parsed data."""
        loc = _load_na_fixture("rvs_location.json")
        status = _load_na_fixture("rvs_status.json")
        ev = _load_na_fixture("ev_charge.json")
        climate = _load_na_fixture("climate_settings.json")
        trip = _load_na_fixture("trip_stats.json")
        conn = _make_na_connection_with_tokens()
        conn._na_tokens[VIN]["vehicle_id"] = "test-vehicle-id"
        conn._na_tokens[VIN]["vehicle_session"] = {"token": "vtoken"}
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._create_na_vehicle_session = AsyncMock(return_value="fake-vehicle-token")
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(200, json_data=loc),  # location (200)
                _mock_resp(200, json_data=status),  # status (200)
                _mock_resp(200, json_data=ev),  # ev_charge (200)
                _mock_resp(200, json_data=climate),  # climate_settings (200)
                _mock_resp(200, json_data=trip),  # trip_stats (200)
            ]
        )
        result = await conn._get_na_vehicle_data(VIN)
        assert result is not None
        assert result["na_ev"] == ev
        assert result["na_climate"] == climate
        assert result["na_trip"] == trip


class NARVSCacheTest(IsolatedAsyncioTestCase):
    """Tests for RVS TTL cache behavior in Connection._get_na_vehicle_data()."""

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_rvs_cache_prevents_second_api_call(self, _mock_jwt):
        """Two consecutive calls within TTL: second returns cached data with zero extra HTTP calls."""
        location_fixture = _load_na_fixture("rvs_location.json")
        status_fixture = _load_na_fixture("rvs_status.json")
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._create_na_vehicle_session = AsyncMock(return_value="fake-vehicle-token")
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(200, json_data=location_fixture),
                _mock_resp(200, json_data=status_fixture),
                _mock_resp(404),
                _mock_resp(404),
                _mock_resp(404),
            ]
        )
        result1 = await conn._get_na_vehicle_data(VIN)
        call_count_after_first = conn._session.get.call_count
        result2 = await conn._get_na_vehicle_data(VIN)
        assert conn._session.get.call_count == call_count_after_first
        assert result1 == result2
        assert result1 is not None

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_rvs_cache_expires_after_ttl(self, _mock_jwt):
        """Cache entry past TTL triggers new HTTP requests on second call."""
        location_fixture = _load_na_fixture("rvs_location.json")
        status_fixture = _load_na_fixture("rvs_status.json")
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._create_na_vehicle_session = AsyncMock(return_value="fake-vehicle-token")
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(200, json_data=location_fixture),
                _mock_resp(200, json_data=status_fixture),
                _mock_resp(404),
                _mock_resp(404),
                _mock_resp(404),
                _mock_resp(200, json_data=location_fixture),
                _mock_resp(200, json_data=status_fixture),
                _mock_resp(404),
                _mock_resp(404),
                _mock_resp(404),
            ]
        )
        await conn._get_na_vehicle_data(VIN)
        call_count_after_first = conn._session.get.call_count
        conn._na_rvs_cache[VIN]["fetched_at"] = time.time() - 31
        await conn._get_na_vehicle_data(VIN)
        assert conn._session.get.call_count > call_count_after_first

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_rvs_cache_cleared_on_401(self, _mock_jwt):
        """401 on location fetch clears the RVS cache, new session re-establishes it."""
        location_fixture = _load_na_fixture("rvs_location.json")
        status_fixture = _load_na_fixture("rvs_status.json")
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=True)

        # Simulate _create_na_vehicle_session storing the token back in _na_tokens
        # (real implementation writes to _na_tokens[vin]["vehicle_session"])
        async def _fake_create_session(vin):
            conn._na_tokens.setdefault(vin, {})["vehicle_session"] = {
                "token": "fake-vehicle-token",
                "expires_at": time.time() + 3600,
                "issued_at": time.time(),
            }
            return "fake-vehicle-token"

        conn._create_na_vehicle_session = _fake_create_session
        conn._na_rvs_cache[VIN] = {
            "data": {"na_location": location_fixture, "na_status": status_fixture},
            "fetched_at": time.time() - 31,
        }
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(401),
                _mock_resp(200, json_data=location_fixture),
                _mock_resp(200, json_data=status_fixture),
                _mock_resp(404),
                _mock_resp(404),
                _mock_resp(404),
            ]
        )
        result = await conn._get_na_vehicle_data(VIN)
        assert result is not None
        assert conn._na_rvs_cache.get(VIN) is not None
        assert conn._na_rvs_cache[VIN]["data"]["na_location"] == location_fixture

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_rvs_cache_respects_custom_ttl(self, _mock_jwt):
        """Custom TTL of 5s: cache aged 6s triggers new fetch."""
        location_fixture = _load_na_fixture("rvs_location.json")
        status_fixture = _load_na_fixture("rvs_status.json")
        conn = _make_na_connection_with_tokens()
        conn._rvs_cache_ttl = 5
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._create_na_vehicle_session = AsyncMock(return_value="fake-vehicle-token")
        conn._na_rvs_cache[VIN] = {
            "data": {"na_location": location_fixture, "na_status": status_fixture},
            "fetched_at": time.time() - 6,
        }
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(200, json_data=location_fixture),
                _mock_resp(200, json_data=status_fixture),
                _mock_resp(404),
                _mock_resp(404),
                _mock_resp(404),
            ]
        )
        result = await conn._get_na_vehicle_data(VIN)
        assert result is not None
        assert conn._session.get.call_count == 5


class NAErrorPathTest(IsolatedAsyncioTestCase):
    """Tests for NA error paths: token exchange failure, garage 404, RVS 5xx."""

    async def test_token_exchange_failure_raises_auth_error(self):
        """_exchange_code_for_tokens raises AuthenticationError on HTTP 400."""
        conn = _make_na_connection()
        conn._pkce_verifier = "test-verifier"
        conn._session_auth_headers = {}
        conn._session_region_config = {"redirect_uri": "kombi:///login"}
        conn._session.post = AsyncMock(
            return_value=_mock_resp(400, text_data='{"error":"invalid_grant"}')
        )
        with self.assertRaises(AuthenticationError) as ctx:
            await conn._exchange_code_for_tokens(
                "bad-code", "https://example.com/token"
            )
        assert "400" in str(ctx.exception)

    async def test_validate_tokens_false_causes_get_na_vehicle_data_to_return_none(
        self,
    ):
        """When validate_tokens() returns False, _get_na_vehicle_data exits early with None."""
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=False)
        conn._create_na_vehicle_session = AsyncMock()
        result = await conn._get_na_vehicle_data(VIN)
        assert result is None
        conn._create_na_vehicle_session.assert_not_called()

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_rvs_5xx_on_both_endpoints_returns_none_values(
        self, _mock_jwt, _mock_sleep
    ):
        """RVS 5xx on both location and status returns dict with None values."""
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._create_na_vehicle_session = AsyncMock(return_value="fake-vehicle-token")
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(500, text_data="Internal Server Error"),
                _mock_resp(500, text_data="Internal Server Error"),
                _mock_resp(500, text_data="Internal Server Error"),
                _mock_resp(500, text_data="Internal Server Error"),
                _mock_resp(500, text_data="Internal Server Error"),
                _mock_resp(500, text_data="Internal Server Error"),
                _mock_resp(404),
                _mock_resp(404),
                _mock_resp(404),
            ]
        )
        result = await conn._get_na_vehicle_data(VIN)
        assert result is not None
        assert result["na_location"] is None
        assert result["na_status"] is None
        assert conn._session.get.call_count == 9


class NAWriteCommandTest(IsolatedAsyncioTestCase):
    """Tests for NA write commands: lock_na, honk_and_flash_na, charging, climate."""

    VEHICLE_ID = "vehicle-uuid-1234"

    def _make_conn(self) -> Connection:
        conn = _make_na_connection_with_tokens()
        conn._na_tokens[VIN]["vehicle_id"] = self.VEHICLE_ID
        conn._na_tokens[VIN]["vehicle_session"] = {"token": "fake-vehicle-token"}
        conn.validate_tokens = AsyncMock(return_value=True)
        return conn

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_success(self, _mock_jwt):
        """lock_na sends PUT /lockunlock/v1/ with action=lock and returns True on 200."""
        conn = self._make_conn()
        conn._session.put = AsyncMock(return_value=_mock_resp(200))
        result = await conn.lock_na(VIN, action="lock")
        assert result is True
        conn._session.put.assert_called_once()
        call_kwargs = conn._session.put.call_args
        assert f"/lockunlock/v1/vehicle/{self.VEHICLE_ID}" in call_kwargs.kwargs["url"]
        assert call_kwargs.kwargs["json"] == {"action": "lock"}

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_unlock_na_success(self, _mock_jwt):
        """lock_na with action=unlock sends unlock body."""
        conn = self._make_conn()
        conn._session.put = AsyncMock(return_value=_mock_resp(204))
        result = await conn.lock_na(VIN, action="unlock")
        assert result is True
        assert conn._session.put.call_args.kwargs["json"] == {"action": "unlock"}

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_401_retries_with_fresh_token(self, _mock_jwt):
        """On 401, lock_na refreshes vehicle session and retries."""
        conn = self._make_conn()

        async def _fake_create_session(vin):
            conn._na_tokens[vin]["vehicle_session"] = {"token": "new-vehicle-token"}
            return "new-vehicle-token"

        conn._create_na_vehicle_session = AsyncMock(side_effect=_fake_create_session)
        conn._session.put = AsyncMock(
            side_effect=[
                _mock_resp(401),
                _mock_resp(200),
            ]
        )
        result = await conn.lock_na(VIN, action="lock")
        assert result is True
        assert conn._session.put.call_count == 2
        conn._create_na_vehicle_session.assert_called_once()

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_returns_false_on_error(self, _mock_jwt):
        """lock_na returns False on non-2xx status."""
        conn = self._make_conn()
        conn._session.put = AsyncMock(return_value=_mock_resp(500))
        result = await conn.lock_na(VIN, action="lock")
        assert result is False

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_returns_false_when_no_token(self, _mock_jwt):
        """lock_na returns False when no vehicle session token is cached."""
        conn = _make_na_connection_with_tokens()
        conn._na_tokens[VIN]["vehicle_id"] = self.VEHICLE_ID
        # Explicitly remove vehicle_session to simulate no token cached
        conn._na_tokens[VIN].pop("vehicle_session", None)
        conn.validate_tokens = AsyncMock(return_value=True)
        conn._session.put = AsyncMock()
        result = await conn.lock_na(VIN, action="lock")
        assert result is False
        conn._session.put.assert_not_called()

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_honk_and_flash_na_success(self, _mock_jwt):
        """honk_and_flash_na sends PUT /honkflash/v1/ with empty body."""
        conn = self._make_conn()
        conn._session.put = AsyncMock(return_value=_mock_resp(202))
        result = await conn.honk_and_flash_na(VIN)
        assert result is True
        call_kwargs = conn._session.put.call_args
        assert f"/honkflash/v1/vehicle/{self.VEHICLE_ID}" in call_kwargs.kwargs["url"]
        assert call_kwargs.kwargs["json"] == {}

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_start_charging_na_success(self, _mock_jwt):
        """start_charging_na sends POST to charging/start endpoint."""
        conn = self._make_conn()
        conn._session.post = AsyncMock(return_value=_mock_resp(200))
        result = await conn.start_charging_na(VIN)
        assert result is True
        call_url = conn._session.post.call_args.kwargs["url"]
        assert f"/ev/v1/vehicle/{self.VEHICLE_ID}/charging/start" in call_url

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_stop_charging_na_success(self, _mock_jwt):
        """stop_charging_na sends POST to charging/stop endpoint."""
        conn = self._make_conn()
        conn._session.post = AsyncMock(return_value=_mock_resp(200))
        result = await conn.stop_charging_na(VIN)
        assert result is True
        call_url = conn._session.post.call_args.kwargs["url"]
        assert f"/ev/v1/vehicle/{self.VEHICLE_ID}/charging/stop" in call_url

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_start_climatisation_na_success(self, _mock_jwt):
        """start_climatisation_na sends POST to pretripclimate/start."""
        conn = self._make_conn()
        conn._session.post = AsyncMock(return_value=_mock_resp(200))
        result = await conn.start_climatisation_na(VIN)
        assert result is True
        call_url = conn._session.post.call_args.kwargs["url"]
        assert f"/ev/v1/vehicle/{self.VEHICLE_ID}/pretripclimate/start" in call_url

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_stop_climatisation_na_success(self, _mock_jwt):
        """stop_climatisation_na sends POST to pretripclimate/stop."""
        conn = self._make_conn()
        conn._session.post = AsyncMock(return_value=_mock_resp(200))
        result = await conn.stop_climatisation_na(VIN)
        assert result is True
        call_url = conn._session.post.call_args.kwargs["url"]
        assert f"/ev/v1/vehicle/{self.VEHICLE_ID}/pretripclimate/stop" in call_url

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_start_charging_na_uses_post(self, _mock_jwt):
        """start_charging_na uses POST, not PUT."""
        conn = self._make_conn()
        conn._session.post = AsyncMock(return_value=_mock_resp(200))
        conn._session.put = AsyncMock()
        result = await conn.start_charging_na(VIN)
        assert result is True
        conn._session.post.assert_called_once()
        conn._session.put.assert_not_called()

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_stop_charging_na_uses_post(self, _mock_jwt):
        """stop_charging_na uses POST, not PUT."""
        conn = self._make_conn()
        conn._session.post = AsyncMock(return_value=_mock_resp(200))
        conn._session.put = AsyncMock()
        result = await conn.stop_charging_na(VIN)
        assert result is True
        conn._session.post.assert_called_once()
        conn._session.put.assert_not_called()

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_start_climatisation_na_uses_post(self, _mock_jwt):
        """start_climatisation_na uses POST, not PUT."""
        conn = self._make_conn()
        conn._session.post = AsyncMock(return_value=_mock_resp(200))
        conn._session.put = AsyncMock()
        result = await conn.start_climatisation_na(VIN)
        assert result is True
        conn._session.post.assert_called_once()
        conn._session.put.assert_not_called()

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_uses_put(self, _mock_jwt):
        """lock_na uses PUT, not POST."""
        conn = self._make_conn()
        conn._session.put = AsyncMock(return_value=_mock_resp(200))
        conn._session.post = AsyncMock()
        result = await conn.lock_na(VIN, action="lock")
        assert result is True
        conn._session.put.assert_called_once()
        conn._session.post.assert_not_called()


class TestFetchNAOptionalEndpoint(IsolatedAsyncioTestCase):
    """Tests for Connection._fetch_na_optional_endpoint()."""

    VEHICLE_ID = "vehicle-uuid-1234"
    URL = "https://b-h-s.spr.us00.p.con-veh.net/ev/v1/vehicle/vehicle-uuid-1234/charge/summary"

    def _make_conn(self) -> Connection:
        conn = _make_na_connection_with_tokens()
        conn._na_tokens[VIN]["vehicle_id"] = self.VEHICLE_ID
        return conn

    async def test_optional_endpoint_200_returns_data(self):
        """200 with JSON body returns the parsed dict."""
        conn = self._make_conn()
        conn._session.get = AsyncMock(
            return_value=_mock_resp(200, json_data={"batteryPercentageAvailable": 80})
        )
        result = await conn._fetch_na_optional_endpoint(self.URL, VIN, {}, "ev_charge")
        assert result == {"batteryPercentageAvailable": 80}

    async def test_optional_endpoint_200_unwraps_data_envelope(self):
        """200 with {\"data\": {...}} envelope unwraps to the inner dict."""
        conn = self._make_conn()
        inner = {"batteryPercentageAvailable": 60}
        conn._session.get = AsyncMock(
            return_value=_mock_resp(200, json_data={"data": inner})
        )
        result = await conn._fetch_na_optional_endpoint(self.URL, VIN, {}, "ev_charge")
        assert result == inner

    async def test_optional_endpoint_404_returns_none(self):
        """404 returns None (non-EV vehicle) — no WARNING logged."""
        conn = self._make_conn()
        conn._session.get = AsyncMock(return_value=_mock_resp(404))
        with self.assertLogs(
            "volkswagencarnet.vw_connection", level="WARNING"
        ) as log_ctx:
            # Use a sentinel to detect that NO warning was logged — we have to provoke one
            # to satisfy assertLogs, then confirm ours is absent.
            import logging as _logging

            logger = _logging.getLogger("volkswagencarnet.vw_connection")
            logger.warning("_sentinel_warning_not_related")
            result = await conn._fetch_na_optional_endpoint(
                self.URL, VIN, {}, "ev_charge"
            )
        assert result is None
        # Only the sentinel warning should be present, not one from 404
        assert not any("404" in msg and "ev_charge" in msg for msg in log_ctx.output)

    async def test_optional_endpoint_403_returns_none_with_warning(self):
        """403 returns None and logs a WARNING about insufficient permissions."""
        conn = self._make_conn()
        conn._session.get = AsyncMock(return_value=_mock_resp(403))
        with self.assertLogs(
            "volkswagencarnet.vw_connection", level="WARNING"
        ) as log_ctx:
            result = await conn._fetch_na_optional_endpoint(
                self.URL, VIN, {}, "ev_charge"
            )
        assert result is None
        assert any(
            "403" in msg and "insufficient permissions" in msg for msg in log_ctx.output
        )

    async def test_optional_endpoint_500_returns_none_with_warning(self):
        """500 returns None and logs a WARNING."""
        conn = self._make_conn()
        conn._session.get = AsyncMock(return_value=_mock_resp(500))
        with self.assertLogs(
            "volkswagencarnet.vw_connection", level="WARNING"
        ) as log_ctx:
            result = await conn._fetch_na_optional_endpoint(
                self.URL, VIN, {}, "ev_charge"
            )
        assert result is None
        assert any("500" in msg for msg in log_ctx.output)

    async def test_optional_endpoint_json_decode_error_returns_none(self):
        """200 + invalid JSON body returns None and logs a WARNING."""
        conn = self._make_conn()
        mock_r = MagicMock()
        mock_r.status = 200
        mock_r.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock())
        )
        mock_r.text = AsyncMock(return_value="<html>not json</html>")
        conn._session.get = AsyncMock(return_value=mock_r)
        with self.assertLogs(
            "volkswagencarnet.vw_connection", level="WARNING"
        ) as log_ctx:
            result = await conn._fetch_na_optional_endpoint(
                self.URL, VIN, {}, "ev_charge"
            )
        assert result is None
        assert any("failed to decode JSON" in msg for msg in log_ctx.output)

    async def test_optional_endpoint_client_error_returns_none(self):
        """aiohttp.ClientError during GET returns None."""
        conn = self._make_conn()
        conn._session.get = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("connection refused")
        )
        result = await conn._fetch_na_optional_endpoint(self.URL, VIN, {}, "ev_charge")
        assert result is None

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_optional_endpoint_401_invalidates_session_and_cache(self, _mock_jwt):
        """On 401, vehicle session is popped and RVS cache is cleared."""
        conn = _make_na_connection_with_tokens()
        conn._na_tokens[VIN]["vehicle_session"] = {"token": "old-token"}
        conn._na_rvs_cache[VIN] = {"data": {}, "fetched_at": 0.0}
        conn._create_na_vehicle_session = AsyncMock(return_value=None)  # retry fails
        conn._session.get = AsyncMock(return_value=_mock_resp(401))

        headers = {"Authorization": "Bearer old-token"}
        result = await conn._fetch_na_optional_endpoint(
            f"{BASE_API}/ev/v1/vehicle/test-id/charge/summary",
            VIN,
            headers,
            "ev_charge",
        )

        assert result is None
        assert "vehicle_session" not in conn._na_tokens.get(VIN, {})
        assert VIN not in conn._na_rvs_cache

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_optional_endpoint_401_retries_with_new_token_on_success(
        self, _mock_jwt
    ):
        """401 → session refresh succeeds → retry returns 200 with data."""
        conn = _make_na_connection_with_tokens()
        conn._na_tokens[VIN]["vehicle_session"] = {"token": "old-token"}

        async def _fake_create_session(vin):
            conn._na_tokens.setdefault(vin, {})["vehicle_session"] = {
                "token": "new-vehicle-token"
            }
            return "new-vehicle-token"

        conn._create_na_vehicle_session = AsyncMock(side_effect=_fake_create_session)
        conn._session.get = AsyncMock(
            side_effect=[
                _mock_resp(401),
                _mock_resp(200, json_data={"batteryPercentageAvailable": 75}),
            ]
        )

        headers = {"Authorization": "Bearer old-token"}
        result = await conn._fetch_na_optional_endpoint(
            f"{BASE_API}/ev/v1/vehicle/test-id/charge/summary",
            VIN,
            headers,
            "ev_charge",
        )

        assert result == {"batteryPercentageAvailable": 75}
        assert conn._session.get.call_count == 2


class TestNAWriteRequestEdgeCases(IsolatedAsyncioTestCase):
    """Edge case tests for Connection._na_write_request()."""

    VEHICLE_ID = "vehicle-uuid-edge"

    def _make_conn(self) -> Connection:
        conn = _make_na_connection_with_tokens()
        conn._na_tokens[VIN]["vehicle_id"] = self.VEHICLE_ID
        conn._na_tokens[VIN]["vehicle_session"] = {"token": "fake-vehicle-token"}
        conn.validate_tokens = AsyncMock(return_value=True)
        return conn

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_returns_false_on_network_error(self, _mock_jwt):
        """lock_na returns False when aiohttp.ClientError is raised."""
        conn = self._make_conn()
        conn._session.put = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("network error")
        )
        result = await conn.lock_na(VIN, action="lock")
        assert result is False

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_second_401_logs_warning(self, _mock_jwt):
        """After 401 + refresh + second 401, logs WARNING and returns False."""
        conn = self._make_conn()

        async def _fake_create_session(vin):
            conn._na_tokens.setdefault(vin, {})["vehicle_session"] = {
                "token": "new-vehicle-token"
            }
            return "new-vehicle-token"

        conn._create_na_vehicle_session = AsyncMock(side_effect=_fake_create_session)
        conn._session.put = AsyncMock(side_effect=[_mock_resp(401), _mock_resp(401)])
        with self.assertLogs(
            "volkswagencarnet.vw_connection", level="WARNING"
        ) as log_ctx:
            result = await conn.lock_na(VIN, action="lock")
        assert result is False
        assert any("failed after 401 retry" in msg for msg in log_ctx.output)

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_na_write_request_invalid_method_returns_false(self, _mock_jwt):
        """_na_write_request with method='delete' logs ERROR and returns False."""
        conn = self._make_conn()
        conn._session.put = AsyncMock()
        conn._session.post = AsyncMock()
        url = f"{conn._base_api}/lockunlock/v1/vehicle/{self.VEHICLE_ID}"
        with self.assertLogs(
            "volkswagencarnet.vw_connection", level="ERROR"
        ) as log_ctx:
            result = await conn._na_write_request(VIN, url, method="delete")
        assert result is False
        assert any("unsupported HTTP method" in msg for msg in log_ctx.output)
        conn._session.put.assert_not_called()
        conn._session.post.assert_not_called()

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_returns_false_when_session_refresh_fails(self, _mock_jwt):
        """On 401, if _create_na_vehicle_session returns None, lock_na returns False without retrying."""
        conn = self._make_conn()
        conn._create_na_vehicle_session = AsyncMock(return_value=None)
        conn._session.put = AsyncMock(side_effect=[_mock_resp(401)])
        with self.assertLogs(
            "volkswagencarnet.vw_connection", level="WARNING"
        ) as log_ctx:
            result = await conn.lock_na(VIN, action="lock")
        assert result is False
        assert conn._session.put.call_count == 1  # no retry
        assert any("session refresh failed" in msg for msg in log_ctx.output)

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_401_retry_uses_new_token_in_headers(self, _mock_jwt):
        """After 401 + session refresh, the retry request carries the new Authorization token."""
        conn = self._make_conn()

        # Simulate _create_na_vehicle_session storing the new token in _na_tokens
        # (real implementation writes to _na_tokens[vin]["vehicle_session"])
        async def _fake_create_session(vin):
            conn._na_tokens.setdefault(vin, {})["vehicle_session"] = {
                "token": "new-vehicle-token"
            }
            return "new-vehicle-token"

        conn._create_na_vehicle_session = _fake_create_session
        conn._session.put = AsyncMock(
            side_effect=[
                _mock_resp(401),
                _mock_resp(200),
            ]
        )
        result = await conn.lock_na(VIN, action="lock")
        assert result is True
        assert conn._session.put.call_count == 2
        second_call_headers = conn._session.put.call_args_list[1].kwargs["headers"]
        assert second_call_headers["Authorization"] == "Bearer new-vehicle-token"

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_lock_na_returns_false_on_timeout(self, _mock_jwt):
        """asyncio.TimeoutError from the network call returns False."""
        conn = self._make_conn()
        conn._session.put = AsyncMock(side_effect=asyncio.TimeoutError())
        result = await conn.lock_na(VIN, action="lock")
        assert result is False

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_na_write_429_retries_then_succeeds(self, _mock_jwt, _mock_sleep):
        """429 on first attempt, 200 on retry → returns True."""
        conn = self._make_conn()
        conn._session.put = AsyncMock(side_effect=[_mock_resp(429), _mock_resp(200)])
        url = f"{conn._base_api}/lockunlock/v1/vehicle/{self.VEHICLE_ID}"
        result = await conn._na_write_request(VIN, url, method="put")
        assert result is True
        assert conn._session.put.call_count == 2

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_na_write_429_exhausts_retries_returns_false(
        self, _mock_jwt, _mock_sleep
    ):
        """All attempts return 429 → returns False."""
        conn = self._make_conn()
        # Initial + MAX_RETRIES_ON_RATE_LIMIT retries all return 429
        conn._session.put = AsyncMock(return_value=_mock_resp(429))
        url = f"{conn._base_api}/lockunlock/v1/vehicle/{self.VEHICLE_ID}"
        with self.assertLogs(
            "volkswagencarnet.vw_connection", level="WARNING"
        ) as log_ctx:
            result = await conn._na_write_request(VIN, url, method="put")
        assert result is False
        assert any("rate limited after" in msg for msg in log_ctx.output)

    async def test_na_write_returns_false_when_validate_tokens_fails(self):
        """validate_tokens returns False → returns False, no HTTP call made."""
        conn = self._make_conn()
        conn.validate_tokens = AsyncMock(return_value=False)
        conn._session.put = AsyncMock()
        url = f"{conn._base_api}/lockunlock/v1/vehicle/{self.VEHICLE_ID}"
        result = await conn._na_write_request(VIN, url, method="put")
        assert result is False
        conn._session.put.assert_not_called()

    async def test_na_write_returns_false_when_jwt_decode_fails(self):
        """Corrupt id_token causes jwt.decode to raise → returns False."""
        conn = self._make_conn()
        conn._na_tokens["idk"]["id_token"] = "not-a-jwt"
        conn._session.put = AsyncMock()
        url = f"{conn._base_api}/lockunlock/v1/vehicle/{self.VEHICLE_ID}"
        # Let real jwt.decode run — "not-a-jwt" will raise DecodeError
        result = await conn._na_write_request(VIN, url, method="put")
        assert result is False
        conn._session.put.assert_not_called()

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"iss": "vw"})
    async def test_na_write_returns_false_when_sub_claim_missing(self, _mock_jwt):
        """jwt.decode returns claims without 'sub' → returns False."""
        conn = self._make_conn()
        conn._session.put = AsyncMock()
        url = f"{conn._base_api}/lockunlock/v1/vehicle/{self.VEHICLE_ID}"
        result = await conn._na_write_request(VIN, url, method="put")
        assert result is False
        conn._session.put.assert_not_called()


class NATokenValidationTest(IsolatedAsyncioTestCase):
    """Tests for NA token validation and IDK refresh failure paths."""

    async def test_idk_refresh_failure_triggers_relogin_via_validate_tokens(self):
        """Expired IDK + _refresh_idk_token raising AuthenticationError -> _validate_na_tokens returns False."""
        conn = _make_na_connection_with_tokens()
        conn._na_tokens["idk"]["expires_at"] = time.time() - 100
        conn._na_tokens["idk"]["issued_at"] = time.time() - 3700
        conn._refresh_idk_token = AsyncMock(
            side_effect=AuthenticationError("refresh failed")
        )
        result = await conn._validate_na_tokens()
        assert result is False
        conn._refresh_idk_token.assert_called_once()

    async def test_validate_na_tokens_returns_false_on_empty_na_tokens(self):
        """_validate_na_tokens() returns False when _na_tokens is empty."""
        conn = _make_na_connection()
        conn._na_tokens = {}
        result = await conn._validate_na_tokens()
        assert result is False


# ===========================================================================
# Merged from na_vehicle_data_test.py (RVS retry tests)
# ===========================================================================

_RETRY_VIN = "TESTVIN123"
_FAKE_IDK_ID_TOKEN_RETRY = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0LXVzZXItaWQifQ.sig"


def _make_retry_connection() -> Connection:
    """Create a minimal NA Connection pre-loaded with tokens for retry testing."""
    sess = MagicMock()
    conn = Connection(sess, "user@test.com", "password", country="US")
    conn._base_api = "https://b-h-s.spr.us00.p.con-veh.net"
    conn._na_tokens = {
        "idk": {
            "id_token": _FAKE_IDK_ID_TOKEN_RETRY,
            "access_token": "fake-access-token",
        },
        _RETRY_VIN: {
            "vehicle_id": "test-vehicle-uuid",
            "vehicle_session": {
                "token": "fake.vehicle.token",
                "expires_at": 9999999999,
                "issued_at": 0,
            },
        },
    }
    conn.validate_tokens = AsyncMock(return_value=True)
    conn._create_na_vehicle_session = AsyncMock(return_value="fake.vehicle.token")
    return conn


class RVSRetryTest(IsolatedAsyncioTestCase):
    """Tests for RVS 5xx retry behavior in _get_na_vehicle_data()."""

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    async def test_rvs_5xx_retry_location(self, _mock_sleep):
        """On 5xx from RVS location: retries up to RVS_MAX_RETRIES times and succeeds on 3rd attempt."""
        conn = _make_retry_connection()
        location_success = {"latitude": 40.0, "longitude": -74.0}
        status_success = {"lockStatus": "LOCKED"}

        conn._session.get = AsyncMock(
            side_effect=[
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(200, json_data=location_success),
                _make_mock_response(200, json_data=status_success),
                _make_mock_response(404),
                _make_mock_response(404),
                _make_mock_response(404),
            ]
        )

        result = await conn._get_na_vehicle_data(_RETRY_VIN)

        assert conn._session.get.call_count >= 3
        assert result is not None
        assert result["na_location"] is not None
        assert result["na_location"] == location_success

    @patch("volkswagencarnet.vw_connection.asyncio.sleep", new_callable=AsyncMock)
    async def test_rvs_5xx_retry_exhausted(self, _mock_sleep):
        """When all RVS retry attempts return 5xx, the method returns a dict with None values."""
        conn = _make_retry_connection()

        conn._session.get = AsyncMock(
            side_effect=[
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(503, text_data="Service Unavailable"),
                _make_mock_response(404),
                _make_mock_response(404),
                _make_mock_response(404),
            ]
        )

        result = await conn._get_na_vehicle_data(_RETRY_VIN)

        assert result is not None
        assert isinstance(result, dict)
        assert result.get("na_location") is None
        assert result.get("na_status") is None


# ===========================================================================
# Phase 23-05: Coverage gap closure tests
# ===========================================================================


# ---------------------------------------------------------------------------
# EMEA OAuth Helper Tests
# ---------------------------------------------------------------------------
class TestEmeaOAuthHelpers:
    """Test EMEA OAuth helper methods: _extract_identitykit_form, post_form, follow_redirects."""

    # --- _extract_identitykit_form ---

    def test_extract_identitykit_form_server_rendered(self):
        """Test extraction from server-rendered emailPasswordForm HTML."""
        conn = _make_connection()
        html = """
        <html><body>
        <form id="emailPasswordForm" action="/signin-service/v1/client123/login/identifier">
            <input type="hidden" name="_csrf" value="csrf-tok-1">
            <input type="hidden" name="relayState" value="relay-state-1">
            <input type="hidden" name="hmac" value="hmac-val-1">
        </form>
        </body></html>
        """
        result = conn._extract_identitykit_form(html)
        assert result["csrf"] == "csrf-tok-1"
        assert result["relay_state"] == "relay-state-1"
        assert result["hmac"] == "hmac-val-1"
        assert result["form_action"] == "/signin-service/v1/client123/login/identifier"

    def test_extract_identitykit_form_react_idk(self):
        """Test extraction from React/IDK templateModel JS block."""
        conn = _make_connection()
        html = """
        <html><body>
        <script>
        window._IDK = {
            templateModel: {"hmac":"hmac-react-1","relayState":"relay-react-1","postAction":"login/authenticate","clientLegalEntityModel":{"clientId":"client-react-1"}},
            csrf_token: 'csrf-react-1'
        };
        </script>
        </body></html>
        """
        result = conn._extract_identitykit_form(html)
        assert result["csrf"] == "csrf-react-1"
        assert result["relay_state"] == "relay-react-1"
        assert result["hmac"] == "hmac-react-1"
        assert "login/authenticate" in result["form_action"]

    def test_extract_identitykit_form_missing_raises(self):
        """Test that missing form data raises AuthenticationError."""
        conn = _make_connection()
        html = "<html><body><p>No form here</p></body></html>"
        with pytest.raises(AuthenticationError, match="IdentiKit form not found"):
            conn._extract_identitykit_form(html)

    def test_extract_identitykit_form_incomplete_react_raises(self):
        """Test that incomplete React templateModel raises AuthenticationError."""
        conn = _make_connection()
        # Has templateModel but missing hmac
        html = """
        <html><body>
        <script>
        window._IDK = {
            templateModel: {"relayState":"relay","postAction":"login/authenticate"},
            csrf_token: 'csrf-tok'
        };
        </script>
        </body></html>
        """
        with pytest.raises(AuthenticationError, match="IdentiKit form incomplete"):
            conn._extract_identitykit_form(html)

    # --- post_form ---

    @pytest.mark.asyncio
    async def test_post_form_redirect_302(self):
        """Test post_form returns Location header on 302 redirect."""
        conn = _make_connection()
        mock_resp = AsyncMock()
        mock_resp.status = 302
        mock_resp.headers = {"Location": "https://example.com/callback?code=abc"}
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_resp)

        result = await conn.post_form(
            mock_session,
            "https://login.example.com/submit",
            {"Content-Type": "application/x-www-form-urlencoded"},
            {"username": "test"},
            redirect=False,
        )
        assert result == "https://example.com/callback?code=abc"

    @pytest.mark.asyncio
    async def test_post_form_400_wrong_credentials(self):
        """Test post_form raises AuthenticationError on wrong-email-credentials error."""
        conn = _make_connection()
        error_html = """
        <html><body>
        <span id="error-element-username" data-error-code="wrong-email-credentials">Wrong credentials</span>
        </body></html>
        """
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value=error_html)
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="Wrong username or password"):
            await conn.post_form(
                mock_session, "https://login.example.com/submit", {}, {}
            )

    @pytest.mark.asyncio
    async def test_post_form_400_unknown_error(self):
        """Test post_form raises AuthenticationError on unknown 400 error."""
        conn = _make_connection()
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(
            return_value="<html><body>Something went wrong</body></html>"
        )
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="unknown 400 error"):
            await conn.post_form(
                mock_session, "https://login.example.com/submit", {}, {}
            )

    @pytest.mark.asyncio
    async def test_post_form_500_raises_request_error(self):
        """Test post_form raises RequestError on non-200/400 status."""
        conn = _make_connection()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RequestError, match="HTTP 500"):
            await conn.post_form(
                mock_session, "https://login.example.com/submit", {}, {}
            )

    @pytest.mark.asyncio
    async def test_post_form_success_200(self):
        """Test post_form returns response text on 200."""
        conn = _make_connection()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="<html>success page</html>")
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_resp)

        result = await conn.post_form(
            mock_session, "https://login.example.com/submit", {}, {}
        )
        assert result == "<html>success page</html>"

    # --- follow_redirects ---

    @pytest.mark.asyncio
    async def test_follow_redirects_until_stop_uri(self):
        """Test follow_redirects follows 302s until reaching the stop URI."""
        conn = _make_connection()
        conn._session_region_config = {"redirect_uri": "volkswagencarnet://callback"}
        conn._session_auth_headers = {}

        resp1 = AsyncMock()
        resp1.status = 302
        resp1.headers = {"Location": "https://step2.example.com/next"}
        resp2 = AsyncMock()
        resp2.status = 302
        resp2.headers = {"Location": "volkswagencarnet://callback?code=auth123"}

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[resp1, resp2])

        result = await conn.follow_redirects(
            mock_session, "https://login.example.com", "https://step1.example.com/start"
        )
        assert result.startswith("volkswagencarnet://callback")
        assert "code=auth123" in result

    @pytest.mark.asyncio
    async def test_follow_redirects_max_depth_exceeded(self):
        """Test follow_redirects raises RedirectError on max depth."""
        conn = _make_connection()
        conn._session_region_config = {"redirect_uri": "volkswagencarnet://callback"}
        conn._session_auth_headers = {}

        loop_resp = AsyncMock()
        loop_resp.status = 302
        loop_resp.headers = {"Location": "https://loop.example.com/redirect"}

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=loop_resp)

        with pytest.raises(RedirectError, match="Too many redirects"):
            await conn.follow_redirects(
                mock_session,
                "https://login.example.com",
                "https://loop.example.com/redirect",
            )

    # --- _get_authorization_code ---

    @pytest.mark.asyncio
    async def test_get_authorization_code_extracts_code(self):
        """Test _get_authorization_code extracts JWT code from redirect URL."""
        conn = _make_connection()
        openid_config = {
            "authorization_endpoint": "https://identity.vwgroup.io/authorize",
            "issuer": "https://identity.vwgroup.io",
        }
        with (
            patch.object(
                conn,
                "get_authorization_page",
                AsyncMock(return_value="<html>login</html>"),
            ),
            patch.object(conn, "extract_state_token", return_value="state-tok-1"),
            patch.object(
                conn,
                "post_form",
                AsyncMock(return_value="https://redirect.example.com/next"),
            ),
            patch.object(
                conn,
                "follow_redirects",
                AsyncMock(
                    return_value="volkswagencarnet://callback?code=jwt_code_xyz&state=state-tok-1"
                ),
            ),
        ):
            code = await conn._get_authorization_code(openid_config)
        assert code[0] == "jwt_code_xyz"

    @pytest.mark.asyncio
    async def test_get_authorization_code_missing_state_token_raises(self):
        """Test _get_authorization_code raises AuthenticationError when state token is missing."""
        conn = _make_connection()
        openid_config = {
            "authorization_endpoint": "https://identity.vwgroup.io/authorize",
            "issuer": "https://identity.vwgroup.io",
        }
        with (
            patch.object(
                conn,
                "get_authorization_page",
                AsyncMock(return_value="<html>no form</html>"),
            ),
            patch.object(conn, "extract_state_token", return_value=None),
            pytest.raises(AuthenticationError, match="missing state token"),
        ):
            await conn._get_authorization_code(openid_config)


# ---------------------------------------------------------------------------
# check_spin_state Tests
# ---------------------------------------------------------------------------
class TestCheckSpinState:
    """Test check_spin_state success and error paths."""

    @pytest.mark.asyncio
    async def test_check_spin_state_success(self):
        """Test check_spin_state returns True with sufficient remaining tries."""
        conn = _make_connection()
        conn.get = AsyncMock(return_value={"remainingTries": 5})
        result = await conn.check_spin_state()
        assert result is True

    @pytest.mark.asyncio
    async def test_check_spin_state_no_remaining_tries(self):
        """Test check_spin_state raises SPINError when remainingTries missing."""
        conn = _make_connection()
        conn.get = AsyncMock(return_value={})
        with pytest.raises(SPINError, match="Couldn't determine S-PIN state"):
            await conn.check_spin_state()

    @pytest.mark.asyncio
    async def test_check_spin_state_too_few_tries(self):
        """Test check_spin_state raises SPINError when remainingTries < 3."""
        conn = _make_connection()
        conn.get = AsyncMock(return_value={"remainingTries": 2})
        with pytest.raises(SPINError, match="Remaining tries"):
            await conn.check_spin_state()


# ---------------------------------------------------------------------------
# _request Edge Case Tests
# ---------------------------------------------------------------------------
class TestRequestEdgeCases:
    """Test _request method edge cases."""

    @pytest.mark.asyncio
    async def test_request_204_returns_status_code_dict(self):
        """Test _request with 204 status returns {"status_code": 204}."""
        conn = _make_connection()
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.raise_for_status = MagicMock()
        mock_resp.cookies = {}
        mock_resp.headers = {}

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.request = MagicMock(return_value=cm)

        with patch.object(conn, "update_service_status", AsyncMock()):
            result = await conn._request(
                "GET", "https://emea.bff.cariad.digital/vehicle/v1/test"
            )
        assert result == {"status_code": 204}

    @pytest.mark.asyncio
    async def test_request_204_return_raw_returns_response(self):
        """Test _request with 204 and return_raw=True returns response object."""
        conn = _make_connection()
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.raise_for_status = MagicMock()
        mock_resp.cookies = {}
        mock_resp.headers = {}

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.request = MagicMock(return_value=cm)

        with patch.object(conn, "update_service_status", AsyncMock()):
            result = await conn._request(
                "GET", "https://emea.bff.cariad.digital/test", return_raw=True
            )
        assert result is mock_resp

    @pytest.mark.asyncio
    async def test_request_json_parse_failure_returns_empty_dict(self):
        """Test _request where response.json() raises returns empty dict."""
        conn = _make_connection()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.cookies = {}
        mock_resp.headers = {}
        mock_resp.json = AsyncMock(side_effect=ValueError("bad json"))

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.request = MagicMock(return_value=cm)

        with patch.object(conn, "update_service_status", AsyncMock()):
            result = await conn._request("GET", "https://emea.bff.cariad.digital/test")
        assert result == {}

    @pytest.mark.asyncio
    async def test_request_json_parse_failure_return_raw(self):
        """Test _request with return_raw=True and json parse failure returns response."""
        conn = _make_connection()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.cookies = {}
        mock_resp.headers = {}
        mock_resp.json = AsyncMock(side_effect=ValueError("bad json"))

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.request = MagicMock(return_value=cm)

        with patch.object(conn, "update_service_status", AsyncMock()):
            result = await conn._request(
                "GET", "https://emea.bff.cariad.digital/test", return_raw=True
            )
        assert result is mock_resp

    @pytest.mark.asyncio
    async def test_request_network_error_retries(self):
        """Test _request retries on ClientConnectionError."""
        conn = _make_connection()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.cookies = {}
        mock_resp.headers = {}
        mock_resp.json = AsyncMock(return_value={"data": "ok"})

        cm_ok = MagicMock()
        cm_ok.__aenter__ = AsyncMock(return_value=mock_resp)
        cm_ok.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise client_exceptions.ClientConnectionError("Connection refused")
            return cm_ok

        conn._session.request = MagicMock(side_effect=side_effect)

        with (
            patch.object(conn, "update_service_status", AsyncMock()),
            patch("asyncio.sleep", AsyncMock()),
        ):
            result = await conn._request("GET", "https://emea.bff.cariad.digital/test")
        assert result == {"data": "ok"}
        assert call_count == 2


# ---------------------------------------------------------------------------
# Additional Coverage: validate_login, get_request_status edge cases
# ---------------------------------------------------------------------------
class TestValidateLogin:
    """Test validate_login method."""

    @pytest.mark.asyncio
    async def test_validate_login_returns_false_no_tokens(self):
        """Test validate_login returns False when validate_tokens returns False."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=False)
        result = await conn.validate_login()
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_login_returns_true_with_valid_tokens(self):
        """Test validate_login returns True when validate_tokens succeeds."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(return_value=True)
        result = await conn.validate_login()
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_login_catches_os_error(self):
        """Test validate_login catches OSError and returns False."""
        conn = _make_connection()
        conn.validate_tokens = AsyncMock(side_effect=OSError("Network unreachable"))
        result = await conn.validate_login()
        assert result is False


class TestGetRequestStatusEdgeCases:
    """Test get_request_status additional status translations."""

    @pytest.mark.asyncio
    async def test_get_request_status_unfetched(self):
        """Test get_request_status returns 'No response' for unfetched status."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.getPendingRequests = AsyncMock(
            return_value={"data": [{"id": "req-1", "status": "unfetched"}]}
        )
        result = await conn.get_request_status(VIN, requestId="req-1")
        assert result == "No response"

    @pytest.mark.asyncio
    async def test_get_request_status_fail_ignition_on(self):
        """Test get_request_status returns ignition-on message."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.getPendingRequests = AsyncMock(
            return_value={"data": [{"id": "req-1", "status": "fail_ignition_on"}]}
        )
        result = await conn.get_request_status(VIN, requestId="req-1")
        assert result == "Failed because ignition is on"

    @pytest.mark.asyncio
    async def test_get_request_status_queued(self):
        """Test get_request_status returns 'In Progress' for queued status."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=True)
        conn.getPendingRequests = AsyncMock(
            return_value={"data": [{"id": "req-1", "status": "queued"}]}
        )
        result = await conn.get_request_status(VIN, requestId="req-1")
        assert result == "In Progress"


class TestServiceStatusExtended:
    """Test additional service status categorization."""

    @pytest.mark.asyncio
    async def test_update_service_status_204_is_up(self):
        """Test that 204 response sets service status to Up."""
        conn = _make_connection()
        await conn.update_service_status("selectivestatus", 204)
        assert conn._service_status["selectivestatus"] == "Up"

    @pytest.mark.asyncio
    async def test_update_service_status_207_is_up(self):
        """Test that 207 response sets service status to Up."""
        conn = _make_connection()
        await conn.update_service_status("capabilities", 207)
        assert conn._service_status["capabilities"] == "Up"

    @pytest.mark.asyncio
    async def test_update_service_status_403_is_forbidden(self):
        """Test that 403 response sets service status to Forbidden."""
        conn = _make_connection()
        await conn.update_service_status("parkingposition", 403)
        assert conn._service_status["parkingposition"] == "Forbidden"

    @pytest.mark.asyncio
    async def test_update_service_status_1000_is_error(self):
        """Test that internal 1000 code sets service status to Error."""
        conn = _make_connection()
        await conn.update_service_status("token", 1000)
        assert conn._service_status["token"] == "Error"

    @pytest.mark.asyncio
    async def test_update_service_status_trips_url(self):
        """Test trips URL pattern recognized."""
        conn = _make_connection()
        await conn.update_service_status("/vehicle/v1/trips/last", 200)
        assert conn._service_status["trips"] == "Up"

    @pytest.mark.asyncio
    async def test_update_service_status_capabilities_url(self):
        """Test capabilities URL pattern recognized."""
        conn = _make_connection()
        await conn.update_service_status("capabilities", 200)
        assert conn._service_status["capabilities"] == "Up"


# ---------------------------------------------------------------------------
# MBB/Brand Token Exchange Tests (Phase 23-05 coverage closure)
# ---------------------------------------------------------------------------
class TestMBBTokenExchange:
    """Test _register_mbb_client, _exchange_brand_token, _exchange_mbb_token, _refresh_mbb_token."""

    def _make_na_conn(self):
        """Create NA connection with mocked session."""
        session = AsyncMock()
        session._cookie_jar = MagicMock()
        session._cookie_jar._cookies = {}
        conn = Connection(session, "test@example.com", "password", country="US")
        conn._session_auth_headers = {}
        return conn

    @pytest.mark.asyncio
    async def test_register_mbb_client_success(self):
        """Test _register_mbb_client returns xclient_id on success."""
        conn = self._make_na_conn()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"client_id": "xclient-new-123"})
        conn._session.post = AsyncMock(return_value=mock_resp)

        result = await conn._register_mbb_client()
        assert result == "xclient-new-123"

    @pytest.mark.asyncio
    async def test_register_mbb_client_failure(self):
        """Test _register_mbb_client raises AuthenticationError on non-200."""
        conn = self._make_na_conn()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        conn._session.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="MBB client registration failed"):
            await conn._register_mbb_client()

    @pytest.mark.asyncio
    async def test_register_mbb_client_missing_client_id(self):
        """Test _register_mbb_client raises AuthenticationError when client_id missing."""
        conn = self._make_na_conn()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"other_field": "value"})
        conn._session.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="missing 'client_id'"):
            await conn._register_mbb_client()

    @pytest.mark.asyncio
    async def test_register_mbb_client_missing_config(self):
        """Test _register_mbb_client raises when mbb_oauth_base_url missing."""
        conn = self._make_na_conn()
        conn._session_region_config = {}  # No mbb_oauth_base_url

        with pytest.raises(AuthenticationError, match="mbb_oauth_base_url"):
            await conn._register_mbb_client()

    @pytest.mark.asyncio
    async def test_exchange_brand_token_success(self):
        """Test _exchange_brand_token returns brand tokens on success."""
        conn = self._make_na_conn()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"access_token": "brand_at", "refresh_token": "brand_rt"}
        )
        conn._session.post = AsyncMock(return_value=mock_resp)

        result = await conn._exchange_brand_token("idk_at")
        assert result["access_token"] == "brand_at"

    @pytest.mark.asyncio
    async def test_exchange_brand_token_404_fallback(self):
        """Test _exchange_brand_token tries fallback path on 404."""
        conn = self._make_na_conn()
        resp_404 = AsyncMock()
        resp_404.status = 404
        resp_200 = AsyncMock()
        resp_200.status = 200
        resp_200.json = AsyncMock(return_value={"access_token": "fallback_at"})
        conn._session.post = AsyncMock(side_effect=[resp_404, resp_200])

        result = await conn._exchange_brand_token("idk_at")
        assert result["access_token"] == "fallback_at"
        assert conn._session.post.call_count == 2

    @pytest.mark.asyncio
    async def test_exchange_brand_token_failure(self):
        """Test _exchange_brand_token raises on non-200/404."""
        conn = self._make_na_conn()
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="Bad Request")
        conn._session.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="Brand token exchange failed"):
            await conn._exchange_brand_token("idk_at")

    @pytest.mark.asyncio
    async def test_exchange_mbb_token_success(self):
        """Test _exchange_mbb_token returns MBB tokens on success."""
        conn = self._make_na_conn()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"access_token": "mbb_at", "refresh_token": "mbb_rt"}
        )
        conn._session.post = AsyncMock(return_value=mock_resp)

        result = await conn._exchange_mbb_token("idk_id", "xclient-1")
        assert result["access_token"] == "mbb_at"

    @pytest.mark.asyncio
    async def test_exchange_mbb_token_failure(self):
        """Test _exchange_mbb_token raises on non-200."""
        conn = self._make_na_conn()
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="Bad Request")
        conn._session.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(
            AuthenticationError, match="MBB initial token exchange failed"
        ):
            await conn._exchange_mbb_token("idk_id", "xclient-1")

    @pytest.mark.asyncio
    async def test_refresh_mbb_token_success(self):
        """Test _refresh_mbb_token returns refreshed tokens on success."""
        conn = self._make_na_conn()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"access_token": "mbb_at_new", "refresh_token": "mbb_rt_new"}
        )
        conn._session.post = AsyncMock(return_value=mock_resp)

        result = await conn._refresh_mbb_token(
            refresh_token="mbb_rt_old", xclient_id="xclient-1"
        )
        assert result["access_token"] == "mbb_at_new"

    @pytest.mark.asyncio
    async def test_refresh_mbb_token_failure(self):
        """Test _refresh_mbb_token raises on non-200."""
        conn = self._make_na_conn()
        mock_resp = AsyncMock()
        mock_resp.status = 401
        mock_resp.text = AsyncMock(return_value="Unauthorized")
        conn._session.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="MBB token refresh failed"):
            await conn._refresh_mbb_token(
                refresh_token="mbb_rt", xclient_id="xclient-1"
            )


# ---------------------------------------------------------------------------
# refresh_tokens (EMEA) Tests
# ---------------------------------------------------------------------------
class TestRefreshTokens:
    """Test EMEA refresh_tokens method."""

    @pytest.mark.asyncio
    async def test_refresh_tokens_success(self):
        """Test refresh_tokens updates tokens on success."""
        conn = _make_connection()
        conn._session_tokens["identity"] = {
            "access_token": "old_at",
            "refresh_token": "old_rt",
            "id_token": "old_id",
        }
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "access_token": "new_at",
                "refresh_token": "new_rt",
                "id_token": "new_id",
            }
        )
        conn._session.post = AsyncMock(return_value=mock_resp)

        with patch.object(conn, "update_service_status", AsyncMock()):
            result = await conn.refresh_tokens()

        assert result is True
        assert conn._session_tokens["identity"]["access_token"] == "new_at"
        assert "Bearer new_at" in conn._session_headers["Authorization"]

    @pytest.mark.asyncio
    async def test_refresh_tokens_failure(self):
        """Test refresh_tokens returns False on non-200."""
        conn = _make_connection()
        conn._session_tokens["identity"] = {
            "access_token": "old_at",
            "refresh_token": "old_rt",
        }
        mock_resp = AsyncMock()
        mock_resp.status = 401
        mock_resp.text = AsyncMock(return_value="Unauthorized")
        conn._session.post = AsyncMock(return_value=mock_resp)

        with patch.object(conn, "update_service_status", AsyncMock()):
            result = await conn.refresh_tokens()

        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_tokens_empty_response(self):
        """Test refresh_tokens returns False when response has no access_token."""
        conn = _make_connection()
        conn._session_tokens["identity"] = {
            "access_token": "old_at",
            "refresh_token": "old_rt",
        }
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={})
        conn._session.post = AsyncMock(return_value=mock_resp)

        with patch.object(conn, "update_service_status", AsyncMock()):
            result = await conn.refresh_tokens()

        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_tokens_exception(self):
        """Test refresh_tokens returns False on exception."""
        conn = _make_connection()
        conn._session_tokens["identity"] = {
            "access_token": "old_at",
            "refresh_token": "old_rt",
        }
        conn._session.post = AsyncMock(side_effect=Exception("Network error"))

        result = await conn.refresh_tokens()
        assert result is False


# ---------------------------------------------------------------------------
# get() Error Handling Tests
# ---------------------------------------------------------------------------
class TestGetErrorHandling:
    """Test get() error handling paths."""

    @pytest.mark.asyncio
    async def test_get_400_returns_status_code(self):
        """Test get() returns status_code dict on 400 error."""
        conn = _make_connection()
        ri = MagicMock(aiohttp.RequestInfo)
        e = client_exceptions.ClientResponseError(request_info=ri, history=tuple([]))
        e.status = 400
        conn._request = AsyncMock(side_effect=e)

        result = await conn.get("https://emea.bff.cariad.digital/test")
        assert result == {"status_code": 400}

    @pytest.mark.asyncio
    async def test_get_500_returns_status_code(self):
        """Test get() returns status_code dict on 500 error."""
        conn = _make_connection()
        ri = MagicMock(aiohttp.RequestInfo)
        e = client_exceptions.ClientResponseError(request_info=ri, history=tuple([]))
        e.status = 500
        conn._request = AsyncMock(side_effect=e)

        result = await conn.get("https://emea.bff.cariad.digital/test")
        assert result == {"status_code": 500}

    @pytest.mark.asyncio
    async def test_get_502_returns_status_code(self):
        """Test get() returns status_code dict on 502 error."""
        conn = _make_connection()
        ri = MagicMock(aiohttp.RequestInfo)
        e = client_exceptions.ClientResponseError(request_info=ri, history=tuple([]))
        e.status = 502
        conn._request = AsyncMock(side_effect=e)

        result = await conn.get("https://emea.bff.cariad.digital/test")
        assert result == {"status_code": 502}

    @pytest.mark.asyncio
    async def test_get_unknown_error_returns_status_code(self):
        """Test get() returns status_code dict on unhandled error status."""
        conn = _make_connection()
        ri = MagicMock(aiohttp.RequestInfo)
        e = client_exceptions.ClientResponseError(request_info=ri, history=tuple([]))
        e.status = 418
        conn._request = AsyncMock(side_effect=e)

        result = await conn.get("https://emea.bff.cariad.digital/test")
        assert result == {"status_code": 418}


# ---------------------------------------------------------------------------
# update() Method Tests
# ---------------------------------------------------------------------------
class TestUpdateMethod:
    """Test Connection.update() method."""

    @pytest.mark.asyncio
    async def test_update_success_with_vehicles(self):
        """Test update() calls validate_tokens and updates vehicles."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=True)
        mock_vehicle = MagicMock()
        mock_vehicle.update = AsyncMock()
        conn._vehicles = [mock_vehicle]

        result = await conn.update()
        assert result is True
        mock_vehicle.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_relogin_on_token_failure(self):
        """Test update() tries to relogin when validate_tokens fails."""
        conn = _make_connection()
        conn._session_logged_in = True
        conn.validate_tokens = AsyncMock(return_value=False)
        conn.doLogin = AsyncMock(return_value=False)

        result = await conn.update()
        assert result is False

    @pytest.mark.asyncio
    async def test_update_not_logged_in_calls_login(self):
        """Test update() calls _login when not logged in."""
        conn = _make_connection()
        conn._session_logged_in = False
        conn._login = AsyncMock(return_value=False)

        result = await conn.update()
        assert result is False
        conn._login.assert_called_once()


# ---------------------------------------------------------------------------
# post() and put() Tests
# ---------------------------------------------------------------------------
class TestPostPut:
    """Test post() and put() methods."""

    @pytest.mark.asyncio
    async def test_post_with_data(self):
        """Test post() passes data to _request."""
        conn = _make_connection()
        conn._request = AsyncMock(return_value={"result": "ok"})
        result = await conn.post("https://example.com/api", json={"key": "val"})
        assert result == {"result": "ok"}
        conn._request.assert_called_once()

    @pytest.mark.asyncio
    async def test_post_without_data(self):
        """Test post() without data passes to _request."""
        conn = _make_connection()
        conn._request = AsyncMock(return_value={"result": "ok"})
        result = await conn.post("https://example.com/api")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_put_with_data(self):
        """Test put() passes data to _request."""
        conn = _make_connection()
        conn._request = AsyncMock(return_value={"result": "ok"})
        result = await conn.put("https://example.com/api", json={"key": "val"})
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_put_without_data(self):
        """Test put() without data passes to _request."""
        conn = _make_connection()
        conn._request = AsyncMock(return_value={"result": "ok"})
        result = await conn.put("https://example.com/api")
        assert result == {"result": "ok"}


# ---------------------------------------------------------------------------
# validate_tokens EMEA edge cases
# ---------------------------------------------------------------------------
class TestValidateTokensEMEA:
    """Test validate_tokens EMEA path edge cases."""

    @pytest.mark.asyncio
    async def test_validate_tokens_missing_identity_returns_false(self):
        """Test validate_tokens returns False when identity tokens missing."""
        conn = _make_connection()
        conn._session_tokens = {}  # No identity key
        result = await conn.validate_tokens()
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_tokens_about_to_expire_refreshes(self):
        """Test validate_tokens re-logins when tokens expire before next update."""
        import jwt as pyjwt

        conn = _make_connection()
        # Tokens expire in 60 seconds (within 5-minute interval)
        near_future = int(time.time()) + 60
        fake_token = pyjwt.encode(
            {"exp": near_future, "sub": "test"}, "secret", algorithm="HS256"
        )
        conn._session_tokens["identity"] = {
            "access_token": fake_token,
            "id_token": fake_token,
            "refresh_token": "rt",
        }
        mock_login = AsyncMock(return_value=True)
        mock_get = AsyncMock(return_value={"data": None})
        with (
            patch.object(conn, "_login", mock_login),
            patch.object(conn, "get", mock_get),
        ):
            result = await conn.validate_tokens()
        assert result is True
        mock_login.assert_called_once()


# ---------------------------------------------------------------------------
# NA Authorization Code Flow Test (covers lines 765-875)
# ---------------------------------------------------------------------------
class TestNAAuthorizationCodeFlow:
    """Test _get_authorization_code_na full flow."""

    def _make_na_conn(self):
        session = AsyncMock()
        session._cookie_jar = MagicMock()
        session._cookie_jar._cookies = {}
        conn = Connection(session, "user@example.com", "pass123", country="US")
        conn._session_auth_headers = {}
        return conn

    @pytest.mark.asyncio
    async def test_get_authorization_code_na_full_success(self):
        """Test full NA two-step login flow with mocked sub-methods."""
        conn = self._make_na_conn()
        openid_config = {
            "authorization_endpoint": "https://b-h-s.spr.us00.p.con-veh.net/oidc/v1/authorize",
        }

        form_data_email = {
            "csrf": "csrf1",
            "relay_state": "rs1",
            "hmac": "hmac1",
            "form_action": "/signin-service/v1/client/login/identifier",
        }
        form_data_password = {
            "csrf": "csrf2",
            "relay_state": "rs2",
            "hmac": "hmac2",
            "form_action": "/signin-service/v1/client/login/authenticate",
        }

        # Mock password page GET response
        mock_pw_resp = AsyncMock()
        mock_pw_resp.status = 200
        mock_pw_resp.text = AsyncMock(return_value="<html>password form</html>")
        pw_cm = MagicMock()
        pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_resp)
        pw_cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.get = MagicMock(return_value=pw_cm)

        with (
            patch.object(
                conn,
                "get_authorization_page",
                AsyncMock(return_value="<html>email form</html>"),
            ),
            patch.object(
                conn,
                "_extract_identitykit_form",
                side_effect=[form_data_email, form_data_password],
            ),
            patch.object(
                conn,
                "post_form",
                AsyncMock(
                    side_effect=[
                        "https://identity.na.vwgroup.io/signin-service/v1/client/login/password",
                        "https://identity.na.vwgroup.io/signin-service/v1/redirect",
                    ]
                ),
            ),
            patch.object(
                conn,
                "follow_redirects",
                AsyncMock(
                    return_value="kombi:///login?code=na_auth_code_123&state=xyz"
                ),
            ),
        ):
            code = await conn._get_authorization_code_na(openid_config)

        assert code == "na_auth_code_123"

    @pytest.mark.asyncio
    async def test_get_authorization_code_na_no_redirect_after_email(self):
        """Test raises AuthenticationError when email submission returns no redirect."""
        conn = self._make_na_conn()
        openid_config = {"authorization_endpoint": "https://example.com/authorize"}

        form_data = {
            "csrf": "c",
            "relay_state": "r",
            "hmac": "h",
            "form_action": "/action",
        }

        with (
            patch.object(
                conn,
                "get_authorization_page",
                AsyncMock(return_value="<html>form</html>"),
            ),
            patch.object(conn, "_extract_identitykit_form", return_value=form_data),
            patch.object(conn, "post_form", AsyncMock(return_value=None)),
            pytest.raises(
                AuthenticationError, match="No redirect received after email"
            ),
        ):
            await conn._get_authorization_code_na(openid_config)

    @pytest.mark.asyncio
    async def test_get_authorization_code_na_password_invalid(self):
        """Test raises AuthenticationError on password_invalid redirect."""
        conn = self._make_na_conn()
        openid_config = {"authorization_endpoint": "https://example.com/authorize"}

        form_data = {
            "csrf": "c",
            "relay_state": "r",
            "hmac": "h",
            "form_action": "/action",
        }

        mock_pw_resp = AsyncMock()
        mock_pw_resp.status = 200
        mock_pw_resp.text = AsyncMock(return_value="<html>pw form</html>")
        pw_cm = MagicMock()
        pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_resp)
        pw_cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.get = MagicMock(return_value=pw_cm)

        with (
            patch.object(
                conn,
                "get_authorization_page",
                AsyncMock(return_value="<html>form</html>"),
            ),
            patch.object(conn, "_extract_identitykit_form", return_value=form_data),
            patch.object(
                conn,
                "post_form",
                AsyncMock(
                    side_effect=[
                        "https://identity.na.vwgroup.io/next",
                        "https://example.com/callback?error=login.errors.password_invalid",
                    ]
                ),
            ),
            pytest.raises(AuthenticationError, match="Password rejected"),
        ):
            await conn._get_authorization_code_na(openid_config)

    @pytest.mark.asyncio
    async def test_get_authorization_code_na_throttled(self):
        """Test raises AuthenticationError on login.error.throttled."""
        conn = self._make_na_conn()
        openid_config = {"authorization_endpoint": "https://example.com/authorize"}

        form_data = {
            "csrf": "c",
            "relay_state": "r",
            "hmac": "h",
            "form_action": "/action",
        }

        mock_pw_resp = AsyncMock()
        mock_pw_resp.status = 200
        mock_pw_resp.text = AsyncMock(return_value="<html>pw form</html>")
        pw_cm = MagicMock()
        pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_resp)
        pw_cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.get = MagicMock(return_value=pw_cm)

        with (
            patch.object(
                conn,
                "get_authorization_page",
                AsyncMock(return_value="<html>form</html>"),
            ),
            patch.object(conn, "_extract_identitykit_form", return_value=form_data),
            patch.object(
                conn,
                "post_form",
                AsyncMock(
                    side_effect=[
                        "https://identity.na.vwgroup.io/next",
                        "https://example.com/callback?login.error.throttled=true",
                    ]
                ),
            ),
            pytest.raises(AuthenticationError, match="throttling"),
        ):
            await conn._get_authorization_code_na(openid_config)

    @pytest.mark.asyncio
    async def test_get_authorization_code_na_no_code_in_callback(self):
        """Test raises AuthenticationError when code missing from callback URL."""
        conn = self._make_na_conn()
        openid_config = {"authorization_endpoint": "https://example.com/authorize"}

        form_data = {
            "csrf": "c",
            "relay_state": "r",
            "hmac": "h",
            "form_action": "/action",
        }

        mock_pw_resp = AsyncMock()
        mock_pw_resp.status = 200
        mock_pw_resp.text = AsyncMock(return_value="<html>pw form</html>")
        pw_cm = MagicMock()
        pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_resp)
        pw_cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.get = MagicMock(return_value=pw_cm)

        with (
            patch.object(
                conn,
                "get_authorization_page",
                AsyncMock(return_value="<html>form</html>"),
            ),
            patch.object(conn, "_extract_identitykit_form", return_value=form_data),
            patch.object(
                conn,
                "post_form",
                AsyncMock(
                    side_effect=[
                        "https://identity.na.vwgroup.io/next",
                        "https://identity.na.vwgroup.io/redirect",
                    ]
                ),
            ),
            patch.object(
                conn,
                "follow_redirects",
                AsyncMock(return_value="kombi:///login?state=xyz"),
            ),
            pytest.raises(AuthenticationError, match="Authorization code not found"),
        ):
            await conn._get_authorization_code_na(openid_config)

    @pytest.mark.asyncio
    async def test_get_authorization_code_na_password_page_non200(self):
        """Test raises AuthenticationError when password page returns non-200."""
        conn = self._make_na_conn()
        openid_config = {"authorization_endpoint": "https://example.com/authorize"}

        form_data = {
            "csrf": "c",
            "relay_state": "r",
            "hmac": "h",
            "form_action": "/action",
        }

        mock_pw_resp = AsyncMock()
        mock_pw_resp.status = 403
        pw_cm = MagicMock()
        pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_resp)
        pw_cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.get = MagicMock(return_value=pw_cm)

        with (
            patch.object(
                conn,
                "get_authorization_page",
                AsyncMock(return_value="<html>form</html>"),
            ),
            patch.object(conn, "_extract_identitykit_form", return_value=form_data),
            patch.object(
                conn,
                "post_form",
                AsyncMock(return_value="https://identity.na.vwgroup.io/next"),
            ),
            pytest.raises(AuthenticationError, match="Password page returned HTTP 403"),
        ):
            await conn._get_authorization_code_na(openid_config)

    @pytest.mark.asyncio
    async def test_get_authorization_code_na_incomplete_form(self):
        """Test raises AuthenticationError when identitykit form is incomplete."""
        conn = self._make_na_conn()
        openid_config = {"authorization_endpoint": "https://example.com/authorize"}

        form_data = {
            "csrf": None,
            "relay_state": "r",
            "hmac": "h",
            "form_action": "/action",
        }

        with (
            patch.object(
                conn,
                "get_authorization_page",
                AsyncMock(return_value="<html>form</html>"),
            ),
            patch.object(conn, "_extract_identitykit_form", return_value=form_data),
            pytest.raises(AuthenticationError, match="IdentiKit form incomplete"),
        ):
            await conn._get_authorization_code_na(openid_config)


# ---------------------------------------------------------------------------
# Action Method Exception Paths (covers setClimater, setAuxiliary, etc. exceptions)
# ---------------------------------------------------------------------------
class TestActionMethodExceptions:
    """Test action method exception wrapping."""

    @pytest.mark.asyncio
    async def test_setClimater_exception_wrapped(self):
        """Test setClimater wraps exceptions in APIError."""
        conn = _make_connection()
        conn.post = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setClimater"):
            await conn.setClimater(VIN, data={}, action="start")

    @pytest.mark.asyncio
    async def test_setClimaterSettings_exception_wrapped(self):
        """Test setClimaterSettings wraps exceptions in APIError."""
        conn = _make_connection()
        conn.put = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setClimaterSettings"):
            await conn.setClimaterSettings(VIN, data={})

    @pytest.mark.asyncio
    async def test_setAuxiliary_exception_wrapped(self):
        """Test setAuxiliary wraps exceptions in APIError."""
        conn = _make_connection()
        conn.post = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setAuxiliary"):
            await conn.setAuxiliary(VIN, data={}, action="start")

    @pytest.mark.asyncio
    async def test_setWindowHeater_exception_wrapped(self):
        """Test setWindowHeater wraps exceptions in APIError."""
        conn = _make_connection()
        conn.post = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setWindowHeater"):
            await conn.setWindowHeater(VIN, action="start")

    @pytest.mark.asyncio
    async def test_setChargingSettings_exception_wrapped(self):
        """Test setChargingSettings wraps exceptions in APIError."""
        conn = _make_connection()
        conn.put = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setChargingSettings"):
            await conn.setChargingSettings(VIN, data={})

    @pytest.mark.asyncio
    async def test_setLock_exception_wrapped(self):
        """Test setLock wraps exceptions in APIError."""
        conn = _make_connection()
        conn.check_spin_state = AsyncMock(return_value=True)
        conn.post = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setLock"):
            await conn.setLock(VIN, lock=True, spin="1234")

    @pytest.mark.asyncio
    async def test_setHonkAndFlash_exception_wrapped(self):
        """Test setHonkAndFlash wraps exceptions in APIError."""
        conn = _make_connection()
        conn.check_spin_state = AsyncMock(return_value=True)
        conn.post = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setHonkAndFlash"):
            await conn.setHonkAndFlash(VIN, position={"lat": 0, "lng": 0})

    @pytest.mark.asyncio
    async def test_setDepartureProfiles_exception_wrapped(self):
        """Test setDepartureProfiles wraps exceptions in APIError."""
        conn = _make_connection()
        conn.put = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setDepartureProfiles"):
            await conn.setDepartureProfiles(VIN, data={})

    @pytest.mark.asyncio
    async def test_setClimatisationTimers_exception_wrapped(self):
        """Test setClimatisationTimers wraps exceptions in APIError."""
        conn = _make_connection()
        conn.put = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setClimatisationTimers"):
            await conn.setClimatisationTimers(VIN, data={})

    @pytest.mark.asyncio
    async def test_setAuxiliaryHeatingTimers_exception_wrapped(self):
        """Test setAuxiliaryHeatingTimers wraps exceptions in APIError."""
        conn = _make_connection()
        conn.put = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setAuxiliaryHeatingTimers"):
            await conn.setAuxiliaryHeatingTimers(VIN, data={})

    @pytest.mark.asyncio
    async def test_setDepartureTimers_exception_wrapped(self):
        """Test setDepartureTimers wraps exceptions in APIError."""
        conn = _make_connection()
        conn.put = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setDepartureTimers"):
            await conn.setDepartureTimers(VIN, data={})

    @pytest.mark.asyncio
    async def test_setChargingCareModeSettings_exception_wrapped(self):
        """Test setChargingCareModeSettings wraps exceptions in APIError."""
        conn = _make_connection()
        conn.put = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setChargingCareModeSettings"):
            await conn.setChargingCareModeSettings(VIN, data={})

    @pytest.mark.asyncio
    async def test_setReadinessBatterySupport_exception_wrapped(self):
        """Test setReadinessBatterySupport wraps exceptions in APIError."""
        conn = _make_connection()
        conn.put = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(APIError, match="setReadinessBatterySupport"):
            await conn.setReadinessBatterySupport(VIN, data={})


# ---------------------------------------------------------------------------
# PR Review Issue 10: PKCE generation tests
# ---------------------------------------------------------------------------
class TestPKCEGeneration:
    """Test PKCE code_verifier and code_challenge generation."""

    def test_generate_pkce_verifier_length(self):
        """Verifier should be 43 chars (32 bytes base64url without padding)."""
        conn = _make_connection()
        verifier = conn._generate_pkce_verifier()
        assert len(verifier) == 43

    def test_generate_pkce_verifier_no_padding(self):
        """Verifier should not contain '=' padding characters."""
        conn = _make_connection()
        verifier = conn._generate_pkce_verifier()
        assert "=" not in verifier

    def test_generate_pkce_verifier_uniqueness(self):
        """Two calls should produce different values."""
        conn = _make_connection()
        v1 = conn._generate_pkce_verifier()
        v2 = conn._generate_pkce_verifier()
        assert v1 != v2

    def test_generate_pkce_challenge_is_sha256(self):
        """Challenge should be SHA256 of verifier, base64url encoded without padding."""
        import base64
        import hashlib

        conn = _make_connection()
        verifier = conn._generate_pkce_verifier()
        challenge = conn._generate_pkce_challenge(verifier)

        # Manually compute expected challenge
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        expected = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        assert challenge == expected

    def test_generate_pkce_challenge_known_vector(self):
        """Use known input/output pair from RFC 7636 Appendix B."""
        conn = _make_connection()
        # RFC 7636 test vector
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        challenge = conn._generate_pkce_challenge(verifier)
        assert challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


# ---------------------------------------------------------------------------
# PR Review Issue 11: PKCE verifier in NA token exchange
# ---------------------------------------------------------------------------
class TestNATokenExchangePKCE:
    """Test that NA token exchange includes PKCE code_verifier."""

    @pytest.mark.asyncio
    async def test_na_token_exchange_includes_pkce_verifier(self):
        """Token exchange POST body should contain code_verifier matching _pkce_verifier."""
        conn = _make_connection(country="US")
        conn._pkce_verifier = "test-pkce-verifier-value-1234567890abc"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(
            return_value='{"access_token":"at","id_token":"id","token_type":"Bearer"}'
        )
        conn._session.post = AsyncMock(return_value=mock_resp)

        await conn._exchange_code_for_tokens("auth_code", "https://example.com/token")

        # Verify POST was called with code_verifier in the data
        call_kwargs = conn._session.post.call_args
        post_data = call_kwargs.kwargs.get("data") or call_kwargs[1].get("data")
        assert post_data["code_verifier"] == "test-pkce-verifier-value-1234567890abc"


# ---------------------------------------------------------------------------
# PR Review Issue 12: _refresh_idk_token guard clause tests
# ---------------------------------------------------------------------------
class TestRefreshIdkTokenGuards:
    """Test guard clauses in _refresh_idk_token."""

    @pytest.mark.asyncio
    async def test_refresh_idk_token_no_refresh_token_raises(self):
        """Should raise AuthenticationError when no refresh_token is stored."""
        conn = _make_connection(country="US")
        conn._na_tokens = {"idk": {}}

        with pytest.raises(AuthenticationError, match="no refresh_token"):
            await conn._refresh_idk_token()

    @pytest.mark.asyncio
    async def test_refresh_idk_token_no_token_endpoint_raises(self):
        """Should raise AuthenticationError when _na_token_endpoint is None."""
        conn = _make_connection(country="US")
        conn._na_tokens = {"idk": {"refresh_token": "some_rt"}}
        conn._na_token_endpoint = None

        with pytest.raises(AuthenticationError, match="_na_token_endpoint not set"):
            await conn._refresh_idk_token()


# ---------------------------------------------------------------------------
# PR Review Issue 18: X-QMAuth header in NA token exchange
# ---------------------------------------------------------------------------
class Plan24_02_RegressionTests(IsolatedAsyncioTestCase):
    """Regression tests for Plan 24-01 fixes: JWT narrowing, 401 retry break, SPIN challenge."""

    @patch(
        "volkswagencarnet.vw_connection.jwt.decode",
        side_effect=jwt.exceptions.DecodeError("bad token"),
    )
    async def test_jwt_decode_invalid_token_returns_none_vehicle_session(
        self, _mock_jwt
    ):
        """_create_na_vehicle_session returns None when jwt.decode raises DecodeError (subclass of InvalidTokenError)."""
        conn = _make_na_connection_with_tokens()
        result = await conn._create_na_vehicle_session(VIN)
        assert result is None

    @patch("volkswagencarnet.vw_connection.jwt.decode")
    async def test_jwt_decode_invalid_token_returns_none_vehicle_data(self, mock_jwt):
        """_get_na_vehicle_data returns None when jwt.decode for x-user-id raises InvalidAudienceError."""
        conn = _make_na_connection_with_tokens()
        conn.validate_tokens = AsyncMock(return_value=True)
        # First call (in _create_na_vehicle_session) succeeds; second call (in _get_na_vehicle_data) fails
        mock_jwt.side_effect = [
            {"sub": USER_ID},  # _create_na_vehicle_session: decode id_token for userId
            {
                "exp": int(time.time()) + 3600
            },  # _create_na_vehicle_session: decode vehicle token for exp
            jwt.exceptions.InvalidAudienceError(
                "bad aud"
            ),  # _get_na_vehicle_data: decode id_token for x-user-id
        ]
        conn._session.post = AsyncMock(
            return_value=_mock_resp(200, {"carnetVehicleToken": "fake-vehicle-token"})
        )
        result = await conn._get_na_vehicle_data(VIN)
        assert result is None

    async def test_rvs_401_retry_break_on_second_failure(self):
        """_fetch_rvs_endpoint returns None after 401 retry fails (second 401), with exactly 2 GET calls."""
        conn = _make_na_connection_for_rvs()
        # First GET returns 401, session refresh succeeds, retry GET returns 401 again
        conn._session.get = AsyncMock(
            side_effect=[
                _make_mock_response(401),
                _make_mock_response(401),
            ]
        )
        result = await conn._fetch_rvs_endpoint(
            url="https://example.com/rvs/v1/vehicle/VIN",
            vin=_TEST_VIN,
            rvs_headers={"Authorization": "Bearer token"},
            label="status",
        )
        assert result is None
        assert conn._session.get.call_count == 2

    @patch("volkswagencarnet.vw_connection.jwt.decode", return_value={"sub": USER_ID})
    async def test_spin_challenge_missing_challenge_field(self, _mock_jwt):
        """_create_na_vehicle_session returns None when challenge response lacks 'challenge' key."""
        conn = _make_na_connection_with_tokens(spin=FAKE_SPIN)
        # Challenge response has remainingTries but no "challenge" key
        conn._session.get = AsyncMock(
            return_value=_mock_resp(200, {"data": {"remainingTries": 3}})
        )
        conn._session.post = AsyncMock()
        result = await conn._create_na_vehicle_session(VIN)
        assert result is None
        # POST should never be called since challenge fetch returned no challenge
        conn._session.post.assert_not_called()


class TestNATokenExchangeXQMAuth:
    """Test X-QMAuth header presence in NA token exchange."""

    @pytest.mark.asyncio
    async def test_na_token_exchange_includes_xqmauth_header(self):
        """NA token exchange should include X-QMAuth header."""
        conn = _make_connection(country="US")
        conn._pkce_verifier = "test-verifier"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(
            return_value='{"access_token":"at","id_token":"id","token_type":"Bearer"}'
        )
        conn._session.post = AsyncMock(return_value=mock_resp)

        await conn._exchange_code_for_tokens("auth_code", "https://example.com/token")

        # Verify X-QMAuth header was set before the POST
        call_kwargs = conn._session.post.call_args
        post_headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get(
            "headers"
        )
        assert "X-QMAuth" in post_headers


# ---------------------------------------------------------------------------
# CFIX-03 / CFIX-04: JWT guard and timeout tests
# ---------------------------------------------------------------------------
class TestCFIX03ValidateTokens(IsolatedAsyncioTestCase):
    """Tests for CFIX-03: validate_tokens should handle malformed JWT gracefully."""

    async def test_validate_tokens_malformed_jwt(self):
        """validate_tokens should return False for malformed JWT, not crash."""
        conn = _make_connection(country="DE")
        conn._session_region = "EMEA"
        conn._session_tokens = {
            "identity": {
                "id_token": "not-a-jwt",
                "access_token": "also-not-a-jwt",
            }
        }
        conn._session_refresh_interval = timedelta(minutes=10)
        result = await conn.validate_tokens()
        assert result is False

    async def test_validate_tokens_missing_exp(self):
        """validate_tokens should return False when exp claim is missing, not TypeError."""
        conn = _make_connection(country="DE")
        conn._session_region = "EMEA"
        conn._session_tokens = {
            "identity": {
                "id_token": "dummy.token.value",
                "access_token": "dummy.token.value",
            }
        }
        conn._session_refresh_interval = timedelta(minutes=10)
        # Mock jwt.decode to return dict without exp claim
        with patch("volkswagencarnet.vw_connection.jwt.decode", return_value={}):
            result = await conn.validate_tokens()
        assert result is False


class TestCFIX04Timeouts(IsolatedAsyncioTestCase):
    """Tests for CFIX-04: session calls must pass explicit timeout."""

    async def test_get_openid_config_passes_timeout(self):
        """get_openid_config should pass timeout= to session.get."""
        conn = _make_connection(country="DE")
        conn._session_region = "EMEA"
        conn._session_region_config = {}  # No hardcoded endpoints -> falls through to session.get
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"authorization_endpoint": "x", "token_endpoint": "y"}
        )
        conn._session.get = AsyncMock(return_value=mock_resp)
        await conn.get_openid_config()
        call_kwargs = conn._session.get.call_args
        assert "timeout" in call_kwargs.kwargs, (
            "get_openid_config must pass timeout= to session.get"
        )

    async def test_refresh_tokens_passes_timeout(self):
        """refresh_tokens should pass timeout= to session.post."""
        conn = _make_connection(country="DE")
        conn._session_region = "EMEA"
        conn._session_tokens = {
            "identity": {
                "refresh_token": "some-refresh-token",
                "access_token": "some-access-token",
            }
        }
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"access_token": "new-at", "id_token": "new-id"}
        )
        conn._session.post = AsyncMock(return_value=mock_resp)
        await conn.refresh_tokens()
        call_kwargs = conn._session.post.call_args
        assert "timeout" in call_kwargs.kwargs, (
            "refresh_tokens must pass timeout= to session.post"
        )


class TestRequestClientErrorHandling(IsolatedAsyncioTestCase):
    """Tests for D-09 / M-01: _request() final except must catch aiohttp.ClientError."""

    async def test_request_client_error_is_caught(self):
        """_request() must catch aiohttp.ClientError, call update_service_status(url, 1000), and re-raise."""
        conn = _make_connection(country="DE")
        conn._session_logged_in = True
        conn._session_auth_headers = {"Authorization": "Bearer test-token"}

        # Patch session.request to raise aiohttp.ClientError (base class — not covered by earlier handlers)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection reset"))
        cm.__aexit__ = AsyncMock(return_value=False)
        conn._session.request = MagicMock(return_value=cm)

        conn.update_service_status = AsyncMock()

        url = "https://emea.bff.cariad.digital/api/test"
        with pytest.raises(aiohttp.ClientError):
            await conn._request("GET", url)

        conn.update_service_status.assert_called_once_with(url, 1000)


class TestConcurrentUpdate(IsolatedAsyncioTestCase):
    """Two concurrent update() calls are serialized via _update_lock."""

    async def test_concurrent_update_serialized(self):
        """Two concurrent update() calls result in only one running at a time."""
        session = MagicMock(spec=ClientSession)
        conn = Connection(session=session, username="test", password="test")
        conn._session_logged_in = True

        # Mock validate_tokens to return True
        conn.validate_tokens = AsyncMock(return_value=True)

        # Track concurrent vehicle.update() calls
        running = 0
        max_concurrent = 0
        block_event = asyncio.Event()

        async def mock_vehicle_update():
            nonlocal running, max_concurrent
            running += 1
            max_concurrent = max(max_concurrent, running)
            await block_event.wait()
            running -= 1

        vehicle = MagicMock()
        vehicle.update = mock_vehicle_update
        conn._vehicles = [vehicle]

        # Start two concurrent update() calls
        task1 = asyncio.create_task(conn.update())
        task2 = asyncio.create_task(conn.update())

        # Give the event loop a chance to start both tasks
        await asyncio.sleep(0.05)

        # Release the block
        block_event.set()

        await task1
        await task2

        # With a lock, max concurrent should be 1 (serialized)
        self.assertEqual(
            max_concurrent, 1, "update() calls should be serialized by _update_lock"
        )


# ---------------------------------------------------------------------------
# update() exception-handling gap tests (D-10)
# ---------------------------------------------------------------------------
class TestUpdateJsonErrorHandling(IsolatedAsyncioTestCase):
    """Test that update() catches ValueError and json.JSONDecodeError (D-10 fix)."""

    async def test_update_json_decode_error_returns_false(self):
        """update() must return False (not raise) when a vehicle raises json.JSONDecodeError."""
        session = MagicMock(spec=ClientSession)
        conn = Connection(session=session, username="test", password="test")
        conn._session_logged_in = True
        conn._session_region = "EMEA"

        # Patch validate_tokens so update() proceeds to the vehicle gather step
        conn.validate_tokens = AsyncMock(return_value=True)

        # Fake vehicle whose update() raises json.JSONDecodeError
        fake_vehicle = MagicMock()
        fake_vehicle.update = AsyncMock(side_effect=json.JSONDecodeError("doc", "", 0))
        conn._vehicles = [fake_vehicle]

        result = await conn.update()

        self.assertIs(
            result,
            False,
            "update() should return False when a vehicle raises json.JSONDecodeError",
        )


class TestDiscoverMarketConfigNoCandidates(IsolatedAsyncioTestCase):
    """Test that _discover_market_config() does not emit a WARNING when base_api_candidates is empty (D-13 fix)."""

    async def test_discover_market_config_empty_candidates_no_warning(self):
        """_discover_market_config() must return True and NOT emit any WARNING when candidates is []."""
        sess = MagicMock()
        sess._cookie_jar = MagicMock()
        sess._cookie_jar._cookies = {}
        conn = Connection(sess, "user@test.com", "password", country="US")
        # Override region config so base_api_candidates is explicitly empty.
        conn._session_region_config = {
            "base_api": "https://b-h-s.spr.us00.p.con-veh.net",
            "base_api_candidates": [],
        }
        conn._session_region = "NA"

        warning_logger = patch.object(
            vw_connection._LOGGER,
            "warning",
            wraps=vw_connection._LOGGER.warning,
        )
        with warning_logger as mock_warn:
            result = await conn._discover_market_config()

        self.assertTrue(
            result,
            "_discover_market_config() must return True when candidates is empty",
        )
        for call in mock_warn.call_args_list:
            msg = call.args[0] if call.args else ""
            self.assertNotIn(
                "Discovery failed",
                msg,
                "No 'Discovery failed' WARNING must be emitted when base_api_candidates is empty",
            )
