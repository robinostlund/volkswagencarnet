"""Integration tests.

These tests use actual credentials, and should thus be used with care.
Credentials have to be specified in credentials.py.
"""

import logging

from aiohttp import ClientSession
import pytest
from volkswagencarnet import vw_connection

try:
    from tests.credentials import password, spin, username, vin
except ImportError:
    username = password = spin = vin = None


@pytest.mark.skipif(
    username is None or password is None,
    reason="Username or password is not set. Check credentials.py.sample",
)
@pytest.mark.asyncio
async def test_successful_login() -> None:
    """Test that login succeeds."""
    async with ClientSession() as session:
        connection = vw_connection.Connection(session, username, password)
        await connection.doLogin()
        assert connection.logged_in is True


@pytest.mark.skipif(
    username is None or password is None,
    reason="Username or password is not set. Check credentials.py.sample",
)
@pytest.mark.asyncio
async def test_refresh_token() -> None:
    """Test that token refresh works after login."""
    async with ClientSession() as session:
        connection = vw_connection.Connection(session, username, password)
        await connection.doLogin()
        assert connection.logged_in is True

        has_refresh_token = bool(
            connection._session_tokens.get("identity", {}).get("refresh_token")
        )
        logging.getLogger().info(
            "refresh_token present after login: %s", has_refresh_token
        )

        if not has_refresh_token:
            pytest.skip(
                "No refresh_token available from hybrid flow — cannot test refresh"
            )

        result = await connection.refresh_tokens()
        assert result is True, "refresh_tokens() returned False"
        assert connection._session_tokens["identity"].get("access_token"), (
            "access_token missing after refresh"
        )
        logging.getLogger().info("Token refresh succeeded")


@pytest.mark.skipif(
    username is None or password is None,
    reason="Username or password is not set. Check credentials.py.sample",
)
@pytest.mark.asyncio
@pytest.mark.skip("Not yet implemented")
async def test_spin_action() -> None:
    """Test something that uses s-pin.

    Not yet implemented...
    """
    logging.getLogger().debug("using vin: %s and s-pin: %s", vin, spin)
