import pytest
from aiohttp import ClientSession

# we need to change os path to be able to import volkswagecarnet
from volkswagencarnet import vw_connection


@pytest.mark.asyncio
async def test_volkswagencarnet():
    async with ClientSession() as session:
        connection = vw_connection.Connection(session, 'test@example.com', 'test_password')
        # if await connection._login():
        if not connection.logged_in:
            return True
    pytest.fail('Something happend we should have got a False from vw.logged_in')
