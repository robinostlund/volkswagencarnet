"""Tests for main connection class."""
import sys

from volkswagencarnet import vw_connection

if sys.version_info >= (3, 8):
    # This won't work on python versions less than 3.8
    from unittest import IsolatedAsyncioTestCase
else:
    from unittest import TestCase

    class IsolatedAsyncioTestCase(TestCase):
        """Dummy class to use instead (tests might need to skipped separately also)."""

        pass


from io import StringIO
from unittest.mock import patch

import pytest


@pytest.mark.skipif(condition=sys.version_info < (3, 8), reason="Test incompatible with Python < 3.8")
def test_clear_cookies(connection):
    """Check that we can clear old cookies."""
    assert len(connection._session._cookie_jar._cookies) > 0
    connection._clear_cookies()
    assert len(connection._session._cookie_jar._cookies) == 0


class CmdLineTest(IsolatedAsyncioTestCase):
    """Tests mostly for testing how to test..."""

    class FailingLoginConnection:
        """This connection always fails login."""

        def __init__(self, sess, **kwargs):
            """Init."""
            self._session = sess

        async def doLogin(self):
            """Failed login attempt."""
            return False

    class TwoVehiclesConnection:
        """Connection that return two vehicles."""

        def __init__(self, sess, **kwargs):
            """Init."""
            self._session = sess

        async def doLogin(self):
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

    @pytest.mark.asyncio
    @patch.object(vw_connection.logging, "basicConfig")
    @patch("volkswagencarnet.vw_connection.Connection", spec_set=vw_connection.Connection, new=FailingLoginConnection)
    @pytest.mark.skipif(condition=sys.version_info < (3, 8), reason="Test incompatible with Python < 3.8")
    async def test_main_argv(self, logger_config):
        """Test verbosity flags."""
        from logging import ERROR
        from logging import INFO
        from logging import DEBUG

        cases = [
            ["none", [], ERROR],
            ["-v", ["-v"], INFO],
            ["-v2", ["-v2"], ERROR],
            ["-vv", ["-vv"], DEBUG],
        ]
        for c in cases:
            args = ["dummy"]
            args.extend(c[1])
            with patch.object(vw_connection.sys, "argv", args), self.subTest(msg=c[0]):
                await vw_connection.main()
                logger_config.assert_called_with(level=c[2])
                logger_config.reset()

    @pytest.mark.asyncio
    @patch("sys.stdout", new_callable=StringIO)
    @patch("volkswagencarnet.vw_connection.Connection", spec_set=vw_connection.Connection, new=FailingLoginConnection)
    @pytest.mark.skipif(condition=sys.version_info < (3, 8), reason="Test incompatible with Python < 3.8")
    async def test_main_output_failed(self, stdout: StringIO):
        """Verify empty stdout on failed login."""
        await vw_connection.main()
        assert stdout.getvalue() == ""

    @pytest.mark.asyncio
    @patch("sys.stdout", new_callable=StringIO)
    @patch("volkswagencarnet.vw_connection.Connection", spec_set=vw_connection.Connection, new=TwoVehiclesConnection)
    @pytest.mark.skipif(condition=sys.version_info < (3, 8), reason="Test incompatible with Python < 3.8")
    async def test_main_output_two_vehicles(self, stdout: StringIO):
        """Get console output for two vehicles."""
        await vw_connection.main()
        assert (
            stdout.getvalue()
            == """Vehicle id: vin1
Supported sensors:
 - Force data refresh (domain:switch) - Off
 - Request results (domain:sensor) - Unknown
 - Requests remaining (domain:sensor) - -1
 - Request in progress (domain:binary_sensor) - Off
Vehicle id: vin2
Supported sensors:
 - Force data refresh (domain:switch) - Off
 - Request results (domain:sensor) - Unknown
 - Requests remaining (domain:sensor) - -1
 - Request in progress (domain:binary_sensor) - Off
"""
        )


class SendCommandsTest(IsolatedAsyncioTestCase):
    """Test command sending."""

    async def test_set_schedule(self):
        """Test set schedule."""
        pass
