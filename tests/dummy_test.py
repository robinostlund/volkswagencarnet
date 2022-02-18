"""Dummy tests. Might be removed once there are proper ones."""
import pytest
from aiohttp import ClientSession

from volkswagencarnet import vw_connection


@pytest.mark.asyncio
async def test_volkswagencarnet():
    """Dummy test to ensure logged in status is false by default."""
    async with ClientSession() as session:
        connection = vw_connection.Connection(session, "test@example.com", "test_password")
        # if await connection._login():
        assert connection.logged_in is False
