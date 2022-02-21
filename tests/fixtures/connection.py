"""Session and connection related test fixtures."""
import os

import pytest
import pytest_asyncio
from aiohttp import CookieJar, ClientSession
from volkswagencarnet.vw_timer import TimerData

from .constants import timers_json_file, resource_path
from volkswagencarnet.vw_connection import Connection
from volkswagencarnet.vw_utilities import json_loads


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
    return Connection(session=session, username="", password="", country="DE", interval=999, fulldebug=True)


class TimersConnection:
    """Connection that has timers defined."""

    def __init__(self, sess, **kwargs):
        """Init."""
        self._session = sess

    async def doLogin(self):
        """No-op login."""
        return True

    async def update(self):
        """No-op update."""
        return True

    async def getTimers(self, vin):
        """Get timers data from backend."""
        # test with a "real" response
        with open(timers_json_file) as f:
            json = json_loads(f.read()).get("timer", {})
            data = TimerData(**json)
            return data
