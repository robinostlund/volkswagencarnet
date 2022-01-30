import pytest
import sys
import os
from aiohttp import ClientSession

# we need to change os path to be able to import volkswagencarnet
myPath = os.path.dirname(os.path.abspath(__file__))
print(myPath)
sys.path.insert(0, myPath + '/../')

try:
    from credentials import username, password, spin, vin
    import credentials
except ImportError:
    username = password = spin = vin = None
    pass


@pytest.mark.skipif(
    username is None or password is None,
    reason="Username or password is not set. Check credentials.py.sample"
)
@pytest.mark.asyncio
async def test_successful_login():
    import vw_connection
    async with ClientSession() as session:
        connection = vw_connection.Connection(session, username, password)
        await connection.doLogin()
        if connection.logged_in:
            return True
    pytest.fail('Login failed')
