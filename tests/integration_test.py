"""
Integration tests.

These tests use actual credentials, and should thus be used with care.
Credentials have to be specified in credentials.py.
"""
import logging

import pytest
from aiohttp import ClientSession

from volkswagencarnet import vw_connection
from volkswagencarnet.vw_timer import TimerData

try:
    from credentials import username, password, spin, vin
except ImportError:
    username = password = spin = vin = None


@pytest.mark.skipif(
    username is None or password is None, reason="Username or password is not set. Check credentials.py.sample"
)
@pytest.mark.asyncio
async def test_successful_login():
    """Test that login succeeds."""
    async with ClientSession() as session:
        connection = vw_connection.Connection(session, username, password)
        await connection.doLogin()
        assert connection.logged_in is True


@pytest.mark.skipif(
    username is None or password is None, reason="Username or password is not set. Check credentials.py.sample"
)
@pytest.mark.asyncio
@pytest.mark.skip("Not sure if this is even a good idea...")
async def test_set_timer():
    """Test that login succeeds."""
    async with ClientSession() as session:
        connection = vw_connection.Connection(session, username, password)
        await connection.doLogin()
        data = TimerData({}, {})
        await connection.setTimersAndProfiles(vin=vin, data=data.timersAndProfiles)

        assert 1 == 1


@pytest.mark.skipif(
    username is None or password is None, reason="Username or password is not set. Check credentials.py.sample"
)
@pytest.mark.asyncio
@pytest.mark.skip("Not yet implemented")
async def test_spin_action():
    """
    Test something that uses s-pin.

    Not yet implemented...
    """
    logging.getLogger().debug(f"using vin: {vin} and s-pin: {spin}")
