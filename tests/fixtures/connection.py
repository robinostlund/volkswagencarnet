"""Session and connection related test fixtures."""

import os

from aiohttp import ClientSession, CookieJar
import pytest
import pytest_asyncio
from volkswagencarnet.vw_connection import Connection

from .constants import resource_path


@pytest_asyncio.fixture
async def session():
    """Client session that can be used in tests."""
    jar = CookieJar()
    jar.load(os.path.join(resource_path, "dummy_cookies.pickle"))
    sess = ClientSession(headers={"Connection": "keep-alive"}, cookie_jar=jar)
    yield sess
    await sess.close()


@pytest.fixture
def connection(session):
    """Real connection for integration tests."""
    return Connection(
        session=session,
        username="",
        password="",
        country="DE",
        interval=999,
    )
