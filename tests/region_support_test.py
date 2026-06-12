"""Test region detection and configuration."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from aiohttp import ClientConnectionError, ClientSession
from volkswagencarnet.vw_const import (
    get_region_from_country,
    get_region_config,
)
from volkswagencarnet.vw_connection import Connection


class TestRegionMapping:
    """Test region detection from country codes."""

    def test_us_maps_to_na(self):
        assert get_region_from_country("US") == "NA"
        assert get_region_from_country("us") == "NA"  # Case insensitive

    def test_canada_maps_to_na(self):
        assert get_region_from_country("CA") == "NA"

    def test_germany_maps_to_emea(self):
        assert get_region_from_country("DE") == "EMEA"

    def test_france_maps_to_emea(self):
        assert get_region_from_country("FR") == "EMEA"

    def test_unknown_country_defaults_to_emea(self):
        assert get_region_from_country("XX") == "EMEA"

    def test_emea_config_has_base_api(self):
        config = get_region_config("EMEA")
        assert config["base_api"] == "https://emea.bff.cariad.digital"
        assert config["homeregion"] == "https://msg.volkswagen.de"

    def test_na_config_has_candidates(self):
        """base_api_candidates is intentionally empty — hardcoded base_api is used directly."""
        config = get_region_config("NA")
        assert "base_api_candidates" in config
        assert (
            config["base_api_candidates"] == []
        )  # intentionally empty: hardcoded base_api used instead
        assert config["base_api"] == "https://b-h-s.spr.us00.p.con-veh.net"


class TestConnectionRegionDetection:
    """Test Connection class region detection."""

    @pytest.mark.asyncio
    async def test_connection_defaults_to_emea(self):
        """No country parameter should default to EMEA."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password")
            assert conn._session_region == "EMEA"
            assert conn._session_country == "DE"
            assert conn._base_api == "https://emea.bff.cariad.digital"

    @pytest.mark.asyncio
    async def test_connection_with_de_country(self):
        """DE country should map to EMEA."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="DE")
            assert conn._session_region == "EMEA"
            assert conn._base_api == "https://emea.bff.cariad.digital"

    @pytest.mark.asyncio
    async def test_connection_with_us_country(self):
        """US country should map to NA."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="US")
            assert conn._session_region == "NA"
            assert conn._session_country == "US"
            assert conn._base_api == "https://b-h-s.spr.us00.p.con-veh.net"

    @pytest.mark.asyncio
    async def test_connection_with_ca_country(self):
        """CA country should map to NA."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="CA")
            assert conn._session_region == "NA"
            assert conn._session_country == "CA"

    @pytest.mark.asyncio
    async def test_canada_country_detected_as_na(self):
        """CA country should set _session_region='NA' and is_na=True."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="CA")
            assert conn._session_region == "NA"
            assert conn.is_na is True


class TestEndpointDiscovery:
    """Test market config discovery for NA region."""

    @pytest.mark.asyncio
    async def test_discovery_not_needed_for_emea(self):
        """EMEA region should skip discovery and return True."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="DE")
            result = await conn._discover_market_config()
            assert result is True  # Should return immediately for non-NA
            assert conn._service_status.get("discovery") == "Skipped"

    @pytest.mark.asyncio
    async def test_discovery_empty_candidates_returns_true_skipped(self):
        """With empty base_api_candidates, discovery returns True immediately with status Skipped (D-13 fix)."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="US")
            # Clear discovery cache so method runs (not cached from __init__)
            conn.discovery_config = {}
            # Store the initial hardcoded base_api
            initial_base_api = conn._base_api

            # No session.get mock needed — empty candidates means no HTTP calls are made
            result = await conn._discover_market_config()

            assert (
                result is True
            )  # empty candidates → early return True (discovery skipped, not failed)
            assert conn._base_api == initial_base_api  # hardcoded value preserved
            assert conn._service_status.get("discovery") == "Skipped"

    @pytest.mark.asyncio
    async def test_discovery_uses_cache_on_second_call(self):
        """Second call with populated discovery_config should return True immediately."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="US")
            # Pre-populate cache
            conn.discovery_config = {"issuer": "https://identity.na.vwgroup.io"}

            with patch.object(conn._session, "get") as mock_get:
                result = await conn._discover_market_config()

                assert result is True
                mock_get.assert_not_called()  # No network call when cached

    @pytest.mark.asyncio
    async def test_discovery_fails_all_candidates(self):
        """Should return False when all candidates fail (with actual non-empty candidate list), NOT block login."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="US")
            # Clear cache so method runs
            conn.discovery_config = {}
            original_base_api = conn._base_api  # pre-confirmed hardcoded value
            # Inject a non-empty candidate list so the failure path is actually exercised
            conn._session_region_config = dict(conn._session_region_config)
            conn._session_region_config["base_api_candidates"] = [
                "https://b-h-s.spr.us00.p.con-veh.net"
            ]

            with patch.object(conn._session, "get") as mock_get:
                # All candidates fail with a ClientError (caught by _discover_market_config)
                mock_get.side_effect = [
                    ClientConnectionError("Connection refused")
                ] * 10

                result = await conn._discover_market_config()

                assert result is False
                assert conn._service_status.get("discovery") == "Failed"
                # base_api stays at hardcoded value (not cleared on failure)
                assert conn._base_api == original_base_api


class TestLoginWithDiscovery:
    """Test login process with market config discovery."""

    @pytest.mark.asyncio
    async def test_login_proceeds_even_when_discovery_fails(self):
        """Discovery failure should NOT block NA login — hardcoded fallback is used."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="US")

            with patch.object(
                conn, "_discover_market_config", new_callable=AsyncMock
            ) as mock_discover:
                with patch.object(conn, "_login", new_callable=AsyncMock) as mock_login:
                    mock_discover.return_value = False  # discovery fails
                    mock_login.return_value = False  # login also fails (no real creds)

                    result = await conn.doLogin()

                    # Discovery is called on every NA doLogin()
                    mock_discover.assert_called_once()
                    # Login is still attempted even when discovery fails
                    mock_login.assert_called_once()

    @pytest.mark.asyncio
    async def test_login_calls_discovery_on_every_na_login(self):
        """doLogin() calls _discover_market_config() for NA on every login (even when discovery returns False, login still proceeds)."""
        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="US")

            with patch.object(
                conn, "_discover_market_config", new_callable=AsyncMock
            ) as mock_discover:
                with patch.object(conn, "_login", new_callable=AsyncMock) as mock_login:
                    with patch.object(
                        conn, "update", new_callable=AsyncMock
                    ) as mock_update:
                        with patch.object(
                            conn, "_request", new_callable=AsyncMock
                        ) as mock_request:
                            # Discovery returns False (empty candidates — normal NA behavior)
                            async def discovery_side_effect():
                                return False

                            mock_discover.side_effect = discovery_side_effect
                            mock_login.return_value = True
                            # NA garage endpoint returns a vehicle list dict
                            mock_request.return_value = {"data": {"vehicles": []}}

                            result = await conn.doLogin()

                            # Discovery IS called (requirement — always called for NA)
                            mock_discover.assert_called_once()
                            # Login is still attempted after discovery (even when discovery returns False)
                            mock_login.assert_called_once()
                            # NA vehicle list is fetched via _request (not get)
                            mock_request.assert_called_once()


class TestVehicleRegionConfig:
    """Test Vehicle class uses region config."""

    @pytest.mark.asyncio
    async def test_vehicle_uses_emea_homeregion(self):
        """Vehicle should use EMEA homeregion from connection."""
        from volkswagencarnet.vw_vehicle import Vehicle

        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="DE")
            vehicle = Vehicle(conn, "WVWZZZ3CZHE123456")

            assert vehicle._homeregion == "https://msg.volkswagen.de"

    @pytest.mark.asyncio
    async def test_vehicle_uses_na_homeregion_when_discovered(self):
        """Vehicle should use NA homeregion from connection config."""
        from volkswagencarnet.vw_vehicle import Vehicle

        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="CA")

            # Simulate discovered NA homeregion (using different value to test it's actually read)
            original_homeregion = conn._session_region_config.get("homeregion")
            conn._session_region_config["homeregion"] = "https://msg.vw.us"

            try:
                vehicle = Vehicle(conn, "1VWSA7A3XLC123456")

                assert vehicle._homeregion == "https://msg.vw.us"
            finally:
                # Restore original value to avoid affecting other tests
                conn._session_region_config["homeregion"] = original_homeregion

    @pytest.mark.asyncio
    async def test_vehicle_falls_back_to_default_homeregion(self):
        """Vehicle should fall back to DE if homeregion is None."""
        from volkswagencarnet.vw_vehicle import Vehicle

        async with ClientSession() as session:
            conn = Connection(session, "test@example.com", "password", country="MX")

            # Ensure homeregion is None
            original_homeregion = conn._session_region_config.get("homeregion")
            conn._session_region_config["homeregion"] = None

            try:
                vehicle = Vehicle(conn, "1VWSA7A3XLC123456")

                # Should fall back to default
                assert vehicle._homeregion == "https://msg.volkswagen.de"
            finally:
                # Restore original value
                conn._session_region_config["homeregion"] = original_homeregion
