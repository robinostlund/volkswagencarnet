"""Tests for Phase 5: Reliability & Discovery.

Covers:
  INT-01 — Market config discovery with allowlist validation and fallback
  INT-04 — Per-vehicle home region routing with session caching
  INT-05 — Rate limit retry with exponential backoff and Retry-After support
"""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import client_exceptions

from volkswagencarnet.vw_connection import Connection, VW_DOMAIN_ALLOWLIST
from volkswagencarnet.vw_const import REGION_CONFIGS
from volkswagencarnet.vw_vehicle import Vehicle


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_na_conn(**kwargs) -> Connection:
    """Create a minimal NA Connection with a mocked aiohttp session."""
    mock_session = AsyncMock()
    mock_session._cookie_jar = MagicMock()
    mock_session._cookie_jar._cookies = {}
    conn = Connection(
        mock_session, "user@example.com", "password", country="US", **kwargs
    )
    conn._session_tokens = {"identity": {"access_token": "test_token"}}
    conn._base_api = "https://b-h-s.spr.us00.p.con-veh.net"
    return conn


def _make_emea_conn() -> Connection:
    """Create a minimal EMEA Connection."""
    mock_session = AsyncMock()
    mock_session._cookie_jar = MagicMock()
    mock_session._cookie_jar._cookies = {}
    return Connection(mock_session, "user@example.com", "password")


def _make_na_vehicle(conn: Connection) -> Vehicle:
    """Create a Vehicle attached to an NA Connection."""
    conn._session_region = "NA"
    conn._session_region_config = {
        **conn._session_region_config,
        "homeregion_candidates": [
            "https://msg.vw.com",
            "https://msg.volkswagen.com",
            "https://msg.vw.us",
        ],
        "homeregion": None,
    }
    return Vehicle(conn, "TESTVIN1234567890")


def _make_resp_ctx(status: int, json_data=None, headers=None):
    """Build a mock async context manager returning a response with given status."""
    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx, resp


# ---------------------------------------------------------------------------
# INT-01: Market Config Discovery
# ---------------------------------------------------------------------------


class MarketConfigDiscoveryTest(IsolatedAsyncioTestCase):
    """Test market config discovery — INT-01."""

    async def test_discovery_success_populates_discovery_config(self):
        """Successful OIDC discovery doc fetch populates discovery_config with validated entries."""
        conn = _make_na_conn()
        oidc_config = {
            "issuer": "https://identity.na.vwgroup.io",
            "authorization_endpoint": "https://identity.na.vwgroup.io/authorize",
            "token_endpoint": "https://identity.na.vwgroup.io/token",
        }
        ctx, _ = _make_resp_ctx(200, json_data=oidc_config)
        conn._session.get = MagicMock(return_value=ctx)

        fake_candidates = ["https://b-h-s.spr.us00.p.con-veh.net"]
        with patch.dict(REGION_CONFIGS["NA"], {"base_api_candidates": fake_candidates}):
            result = await conn._discover_market_config()

        self.assertTrue(result)
        self.assertEqual(
            conn.discovery_config.get("issuer"), "https://identity.na.vwgroup.io"
        )
        self.assertEqual(
            conn.discovery_config.get("token_endpoint"),
            "https://identity.na.vwgroup.io/token",
        )
        self.assertEqual(conn._service_status.get("discovery"), "Success")

    async def test_discovery_rejects_malicious_urls_from_response(self):
        """URLs in discovery response that fail allowlist validation are excluded from discovery_config."""
        conn = _make_na_conn()
        oidc_config = {
            "token_endpoint": "https://evil.com/steal-tokens",
            "authorization_endpoint": "https://identity.na.vwgroup.io/authorize",
            "issuer": "https://identity.na.vwgroup.io",
        }
        ctx, _ = _make_resp_ctx(200, json_data=oidc_config)
        conn._session.get = MagicMock(return_value=ctx)

        fake_candidates = ["https://b-h-s.spr.us00.p.con-veh.net"]
        with patch.dict(REGION_CONFIGS["NA"], {"base_api_candidates": fake_candidates}):
            await conn._discover_market_config()

        # Malicious URL must NOT be in discovery_config
        token_ep = conn.discovery_config.get("token_endpoint", "")
        self.assertNotIn("evil.com", token_ep)
        # Safe VW URL must be included
        self.assertEqual(
            conn.discovery_config.get("authorization_endpoint"),
            "https://identity.na.vwgroup.io/authorize",
        )

    async def test_discovery_uses_session_cache_on_second_call(self):
        """_discover_market_config called twice should only hit the network once."""
        conn = _make_na_conn()
        conn.discovery_config = {"already": "cached"}  # simulate prior discovery

        conn._session.get = MagicMock()  # should NOT be called

        result = await conn._discover_market_config()

        self.assertTrue(result)
        conn._session.get.assert_not_called()

    async def test_discovery_failure_logs_warning_and_continues(self):
        """When all candidates fail (non-empty list), discovery returns False and logs a WARNING."""
        conn = _make_na_conn()
        # Inject a non-empty candidate list so the warning path is actually exercised.
        # The NA production config intentionally uses base_api_candidates=[] (D-13 fix), so we
        # override here to test the failure branch with an actual candidate that returns 503.
        conn._session_region_config = dict(conn._session_region_config)
        conn._session_region_config["base_api_candidates"] = [
            "https://b-h-s.spr.us00.p.con-veh.net"
        ]
        # All candidates return non-200
        ctx, _ = _make_resp_ctx(503)
        conn._session.get = MagicMock(return_value=ctx)

        with self.assertLogs("volkswagencarnet.vw_connection", level="WARNING") as log:
            result = await conn._discover_market_config()

        self.assertFalse(result)
        self.assertEqual(conn._service_status.get("discovery"), "Failed")
        self.assertTrue(
            any(
                "Discovery failed" in m or "falling back" in m.lower()
                for m in log.output
            )
        )

    async def test_discovery_skipped_for_emea(self):
        """EMEA connections skip discovery entirely and return True."""
        conn = _make_emea_conn()
        conn._session.get = MagicMock()  # should NOT be called

        result = await conn._discover_market_config()

        self.assertTrue(result)
        conn._session.get.assert_not_called()
        self.assertEqual(conn._service_status.get("discovery"), "Skipped")

    async def test_vw_domain_allowlist_contains_expected_domains(self):
        """VW_DOMAIN_ALLOWLIST contains the expected set of VW Group domain suffixes."""
        expected = {
            ".vwgroup.io",
            ".con-veh.net",
            ".cariad.digital",
            ".vwg-connect.com",
        }
        for domain in expected:
            self.assertIn(
                domain, VW_DOMAIN_ALLOWLIST, f"Missing expected domain: {domain}"
            )

    async def test_is_allowed_vw_domain_accepts_known_vw_urls(self):
        """_is_allowed_vw_domain returns True for known VW Group hostnames."""
        conn = _make_na_conn()
        vw_urls = [
            "https://identity.na.vwgroup.io/authorize",
            "https://b-h-s.spr.us00.p.con-veh.net/api",
            "https://emea.bff.cariad.digital/data",
            "https://msg.volkswagen.de/cs/vds",
        ]
        for url in vw_urls:
            with self.subTest(url=url):
                self.assertTrue(conn._is_allowed_vw_domain(url))

    async def test_is_allowed_vw_domain_rejects_unknown_domains(self):
        """_is_allowed_vw_domain returns False for non-VW hostnames."""
        conn = _make_na_conn()
        bad_urls = [
            "https://evil.com/steal",
            "https://attacker.vwgroup.io.evil.com/path",  # subdomain spoofing
            "https://volkswagen.de.evil.com/phish",
        ]
        for url in bad_urls:
            with self.subTest(url=url):
                self.assertFalse(conn._is_allowed_vw_domain(url))


# ---------------------------------------------------------------------------
# INT-04: Home Region Routing
# ---------------------------------------------------------------------------


class HomeRegionDiscoveryTest(IsolatedAsyncioTestCase):
    """Test per-vehicle home region discovery — INT-04."""

    async def test_first_responding_candidate_becomes_home_region(self):
        """First candidate returning any HTTP response is assigned as home region."""
        conn = _make_na_conn()
        vehicle = _make_na_vehicle(conn)

        ctx, _ = _make_resp_ctx(200)
        conn._session.get = MagicMock(return_value=ctx)

        await vehicle._ensure_home_region()

        self.assertEqual(vehicle.home_region_url, "https://msg.vw.com")
        self.assertTrue(vehicle._home_region_discovered)

    async def test_discovery_guard_prevents_second_network_call(self):
        """Second call to _ensure_home_region() returns without any network access."""
        conn = _make_na_conn()
        vehicle = _make_na_vehicle(conn)
        vehicle._home_region_discovered = True  # already discovered

        conn._session.get = MagicMock()  # should NOT be called

        await vehicle._ensure_home_region()

        conn._session.get.assert_not_called()

    async def test_emea_vehicle_skips_discovery(self):
        """EMEA vehicle _ensure_home_region() returns without probing any candidate."""
        conn = _make_emea_conn()
        vehicle = Vehicle(conn, "EMEAVIN1234567890")

        conn._session.get = MagicMock()  # should NOT be called

        await vehicle._ensure_home_region()

        conn._session.get.assert_not_called()
        # homeregion retains EMEA default
        self.assertIn("volkswagen.de", vehicle.home_region_url)

    async def test_all_candidates_failing_retains_fallback_homeregion(self):
        """When no candidate responds, self._homeregion keeps its initialized fallback."""
        conn = _make_na_conn()
        vehicle = _make_na_vehicle(conn)
        initial_homeregion = vehicle._homeregion  # save fallback

        # All probes raise a network error
        conn._session.get = MagicMock(
            side_effect=client_exceptions.ClientConnectionError("unreachable")
        )

        with self.assertLogs("volkswagencarnet.vw_vehicle", level="WARNING") as log:
            await vehicle._ensure_home_region()

        self.assertEqual(vehicle.home_region_url, initial_homeregion)
        self.assertTrue(any("Could not discover home region" in m for m in log.output))

    async def test_candidate_failing_domain_validation_is_skipped(self):
        """Candidate URL that fails allowlist validation is skipped without probing."""
        conn = _make_na_conn()
        vehicle = _make_na_vehicle(conn)
        # Override candidates with one bad and one good
        conn._session_region_config["homeregion_candidates"] = [
            "https://evil.com",
            "https://msg.volkswagen.com",
        ]

        # Second candidate (volkswagen.com) should respond
        ctx, _ = _make_resp_ctx(200)
        conn._session.get = MagicMock(return_value=ctx)

        await vehicle._ensure_home_region()

        # evil.com must not be the result
        self.assertNotEqual(vehicle.home_region_url, "https://evil.com")
        self.assertEqual(vehicle.home_region_url, "https://msg.volkswagen.com")

    async def test_home_region_url_property_returns_homeregion(self):
        """home_region_url property returns the value of self._homeregion."""
        conn = _make_na_conn()
        vehicle = _make_na_vehicle(conn)
        vehicle._homeregion = "https://msg.vw.us"

        self.assertEqual(vehicle.home_region_url, "https://msg.vw.us")


# ---------------------------------------------------------------------------
# INT-05: Rate Limit Retry / Backoff
# ---------------------------------------------------------------------------


class RetryBackoffTest(IsolatedAsyncioTestCase):
    """Test retry with exponential backoff and Retry-After — INT-05."""

    def _make_request_mock(self, statuses: list[int], headers_list=None):
        """Return a mock session.request callable that yields responses in order.

        statuses: list of HTTP status codes to return in sequence.
        headers_list: list of header dicts (one per call); defaults to {} for all.
        """
        if headers_list is None:
            headers_list = [{}] * len(statuses)

        call_count = [0]

        def mock_request(*args, **kwargs):
            i = call_count[0]
            call_count[0] += 1
            status = statuses[i] if i < len(statuses) else statuses[-1]
            headers = headers_list[i] if i < len(headers_list) else {}
            resp = MagicMock()
            resp.status = status
            resp.headers = headers
            resp.cookies = {}
            resp.raise_for_status = MagicMock(
                side_effect=(
                    client_exceptions.ClientResponseError(
                        MagicMock(), MagicMock(), status=status
                    )
                    if status >= 400
                    else None
                )
            )
            if 200 <= status < 300:
                resp.raise_for_status = MagicMock(return_value=None)
                resp.json = AsyncMock(return_value={"ok": True})
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=resp)
            ctx.__aexit__ = AsyncMock(return_value=None)
            return ctx

        return mock_request, call_count

    async def test_429_retried_up_to_three_times(self):
        """HTTP 429 causes up to 3 retries (4 total calls) before raising."""
        conn = _make_na_conn()
        mock_request, call_count = self._make_request_mock([429, 429, 429, 429])
        conn._session.request = mock_request

        with patch("asyncio.sleep"):
            with self.assertRaises(Exception):
                await conn._request("GET", "https://b-h-s.spr.us00.p.con-veh.net/test")

        self.assertEqual(
            call_count[0], 4, f"Expected 4 calls (1 + 3 retries), got {call_count[0]}"
        )

    async def test_429_with_retry_after_header_uses_header_delay(self):
        """Retry-After header value is used as the sleep delay (not exponential backoff)."""
        conn = _make_na_conn()
        mock_request, _ = self._make_request_mock(
            [429, 429, 429, 429],
            headers_list=[
                {"Retry-After": "7"},
                {"Retry-After": "7"},
                {"Retry-After": "7"},
                {"Retry-After": "7"},
            ],
        )
        conn._session.request = mock_request

        with patch("asyncio.sleep") as mock_sleep:
            with self.assertRaises(Exception):
                await conn._request("GET", "https://b-h-s.spr.us00.p.con-veh.net/test")

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        self.assertTrue(
            all(d == 7.0 for d in delays), f"Expected all delays 7.0, got: {delays}"
        )

    async def test_429_without_retry_after_uses_exponential_backoff(self):
        """Without Retry-After, delays follow 2^attempt pattern (1, 2, 4 seconds)."""
        conn = _make_na_conn()
        mock_request, _ = self._make_request_mock([429, 429, 429, 429])
        conn._session.request = mock_request

        with patch("asyncio.sleep") as mock_sleep:
            with self.assertRaises(Exception):
                await conn._request("GET", "https://b-h-s.spr.us00.p.con-veh.net/test")

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        self.assertEqual(len(delays), 3, f"Expected 3 sleep calls, got: {delays}")
        self.assertAlmostEqual(delays[0], 1.0, places=1)  # 2^0
        self.assertAlmostEqual(delays[1], 2.0, places=1)  # 2^1
        self.assertAlmostEqual(delays[2], 4.0, places=1)  # 2^2

    async def test_is_throttled_true_after_exhausted_retries(self):
        """connection.is_throttled is True after all 429 retry attempts are exhausted."""
        conn = _make_na_conn()
        mock_request, _ = self._make_request_mock([429, 429, 429, 429])
        conn._session.request = mock_request

        with patch("asyncio.sleep"):
            with self.assertRaises(Exception):
                await conn._request("GET", "https://b-h-s.spr.us00.p.con-veh.net/test")

        self.assertTrue(conn.is_throttled)

    async def test_is_throttled_resets_on_subsequent_success(self):
        """connection.is_throttled resets to False after a successful request."""
        conn = _make_na_conn()
        # First call: 429 (sets throttled), second call: 200 (resets)
        mock_request, _ = self._make_request_mock([429, 200])
        conn._session.request = mock_request

        with patch("asyncio.sleep"):
            try:
                # First request: 429 exhausted after 1 attempt? No — only 1 of 3 retries.
                # Actually with [429, 200]: attempt=0 hits 429, retries → attempt=1 hits 200.
                # So the request should SUCCEED on second try.
                result = await conn._request(
                    "GET", "https://b-h-s.spr.us00.p.con-veh.net/test"
                )
            except Exception:
                pass

        self.assertFalse(conn.is_throttled)

    async def test_transient_network_error_retried(self):
        """ServerDisconnectedError is retried up to 3 times before re-raising."""
        conn = _make_na_conn()
        call_count = [0]

        def raises_disconnect(*args, **kwargs):
            call_count[0] += 1
            raise client_exceptions.ServerDisconnectedError()

        conn._session.request = raises_disconnect

        with patch("asyncio.sleep"):
            with self.assertRaises(client_exceptions.ServerDisconnectedError):
                await conn._request("GET", "https://b-h-s.spr.us00.p.con-veh.net/test")

        self.assertEqual(
            call_count[0], 4, f"Expected 4 calls (1 + 3 retries), got {call_count[0]}"
        )

    async def test_no_retry_flag_skips_retry_on_429(self):
        """_no_retry=True causes 429 to propagate immediately without retrying."""
        conn = _make_na_conn()
        mock_request, call_count = self._make_request_mock([429])
        conn._session.request = mock_request

        with patch("asyncio.sleep") as mock_sleep:
            with self.assertRaises(Exception):
                await conn._request(
                    "GET",
                    "https://b-h-s.spr.us00.p.con-veh.net/test",
                    _no_retry=True,
                )

        self.assertEqual(
            call_count[0], 1, "Expected exactly 1 call with _no_retry=True"
        )
        mock_sleep.assert_not_called()

    async def test_get_returns_throttled_state_after_retry_exhaustion(self):
        """conn.get() returns {'state': 'Throttled'} after 429 retry exhaustion."""
        conn = _make_na_conn()
        mock_request, _ = self._make_request_mock([429, 429, 429, 429])
        conn._session.request = mock_request

        with patch("asyncio.sleep"):
            result = await conn.get("https://b-h-s.spr.us00.p.con-veh.net/test")

        self.assertEqual(result, {"state": "Throttled"})
