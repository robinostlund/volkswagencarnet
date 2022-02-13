import os
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import CookieJar, ClientSession

from volkswagencarnet.vw_connection import Connection

current_path = Path(os.path.dirname(os.path.realpath(__file__)))
resource_path = os.path.join(current_path, "resources")


@pytest_asyncio.fixture
async def session():
    """Client session that can be used in tests"""
    jar = CookieJar()
    jar.load(os.path.join(resource_path, "dummy_cookies.pickle"))
    sess = ClientSession(headers={"Connection": "keep-alive"}, cookie_jar=jar)
    yield sess
    await sess.close()


@pytest.fixture
def connection(session):
    """Real connection for integration tests"""
    return Connection(session=session, username="", password="", country="DE", interval=999, fulldebug=True)
