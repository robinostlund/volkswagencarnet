"""Tests for main connection class."""

import sys
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

import aiohttp
from aiohttp import client_exceptions
import pytest
from volkswagencarnet import vw_connection
from volkswagencarnet.vw_connection import Connection


class TwoVehiclesConnection(Connection):
    """Connection that return two vehicles."""

    ALLOW_RATE_LIMIT_DELAY = False

    # noinspection PyUnusedLocal
    # noinspection PyMissingConstructor
    def __init__(self, sess, username="", password="", **kwargs):
        """Init."""
        super().__init__(session=sess, username=username, password=password)

    async def doLogin(self, tries=1):
        """No-op update."""
        return True

    async def update(self):
        """No-op update."""
        return True

    @property
    def vehicles(self):
        """Return the vehicles."""
        vehicle1 = vw_connection.Vehicle(None, "vin1")
        vehicle2 = vw_connection.Vehicle(None, "vin2")
        return [vehicle1, vehicle2]


@pytest.mark.skipif(
    condition=sys.version_info < (3, 11), reason="Test incompatible with Python < 3.11"
)
def test_clear_cookies(connection) -> None:
    """Check that we can clear old cookies."""
    assert len(connection._session._cookie_jar._cookies) > 0
    connection._clear_cookies()
    assert len(connection._session._cookie_jar._cookies) == 0


class SendCommandsTest(IsolatedAsyncioTestCase):
    """Test command sending."""

    async def test_set_schedule(self):
        """Test set schedule."""
        pass


class RateLimitTest(IsolatedAsyncioTestCase):
    """Test that rate limiting towards VW works."""

    invocations = 0

    async def rateLimitedFunction(self, url, vin=""):
        """Limit calls test function."""
        ri = MagicMock(aiohttp.RequestInfo)
        e = client_exceptions.ClientResponseError(request_info=ri, history=tuple([]))
        e.status = 429
        self.invocations = self.invocations + 1
        raise e

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        condition=sys.version_info < (3, 11),
        reason="Test incompatible with Python < 3.11",
    )
    @patch(
        "volkswagencarnet.vw_connection.Connection",
        spec_set=vw_connection.Connection,
        new=TwoVehiclesConnection,
    )
    @patch("volkswagencarnet.vw_connection.MAX_RETRIES_ON_RATE_LIMIT", 1)
    async def test_rate_limit(self):
        """Test rate limiting functionality."""

        from unittest.mock import AsyncMock

        sess = AsyncMock()

        # noinspection PyArgumentList
        conn = vw_connection.Connection(sess, "", "")

        self.invocations = 0
        with patch.object(conn, "_request", self.rateLimitedFunction):
            res = await conn.get("foo")
            assert res == {"status_code": 429}
        assert self.invocations == vw_connection.MAX_RETRIES_ON_RATE_LIMIT + 1
