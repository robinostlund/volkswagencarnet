import pytest
from aiohttp import ClientSession

from volkswagencarnet import vw_connection

try:
    from credentials import username, password, spin, vin
except ImportError:
    username = password = spin = vin = None
    pass


@pytest.mark.skipif(
    username is None or password is None,
    reason="Username or password is not set. Check credentials.py.sample"
)
@pytest.mark.asyncio
async def test_successful_login():
    async with ClientSession() as session:
        connection = vw_connection.Connection(session, username, password)
        await connection.doLogin()
        if connection.logged_in:
            return True
    pytest.fail('Login failed')
