import logging.config
import sys
import unittest

if sys.version_info >= (3, 8):
    # This won't work on python versions less than 3.8
    from unittest import IsolatedAsyncioTestCase
else:

    class IsolatedAsyncioTestCase(unittest.TestCase):
        pass


from io import StringIO
from unittest.mock import patch

import pytest

import volkswagencarnet.vw_connection
from volkswagencarnet.vw_connection import Connection
from volkswagencarnet.vw_vehicle import Vehicle


@pytest.mark.skipif(condition=sys.version_info < (3, 8), reason="Test incompatible with Python < 3.8")
def test_clear_cookies(connection):
    assert len(connection._session._cookie_jar._cookies) > 0
    connection._clear_cookies()
    assert len(connection._session._cookie_jar._cookies) == 0


class CmdLineTest(IsolatedAsyncioTestCase):
    class FailingLoginConnection:
        def __init__(self, sess, **kwargs):
            self._session = sess

        async def doLogin(self):
            return False

    class TwoVehiclesConnection:
        def __init__(self, sess, **kwargs):
            self._session = sess

        async def doLogin(self):
            return True

        async def update(self):
            return True

        @property
        def vehicles(self):
            vehicle1 = Vehicle(None, "vin1")
            vehicle2 = Vehicle(None, "vin2")
            return [vehicle1, vehicle2]

    @pytest.mark.asyncio
    @patch.object(volkswagencarnet.vw_connection.logging, "basicConfig")
    @patch("volkswagencarnet.vw_connection.Connection", spec_set=Connection, new=FailingLoginConnection)
    @pytest.mark.skipif(condition=sys.version_info < (3, 8), reason="Test incompatible with Python < 3.8")
    async def test_main_argv(self, logger_config):
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
            with patch.object(volkswagencarnet.vw_connection.sys, "argv", args), self.subTest(msg=c[0]):
                await volkswagencarnet.vw_connection.main()
                logger_config.assert_called_with(level=c[2])
                logger_config.reset()

    @pytest.mark.asyncio
    @patch("sys.stdout", new_callable=StringIO)
    @patch("volkswagencarnet.vw_connection.Connection", spec_set=Connection, new=FailingLoginConnection)
    @pytest.mark.skipif(condition=sys.version_info < (3, 8), reason="Test incompatible with Python < 3.8")
    async def test_main_output_failed(self, stdout: StringIO):
        await volkswagencarnet.vw_connection.main()
        assert stdout.getvalue() == ""

    @pytest.mark.asyncio
    @patch("sys.stdout", new_callable=StringIO)
    @patch("volkswagencarnet.vw_connection.Connection", spec_set=Connection, new=TwoVehiclesConnection)
    @pytest.mark.skipif(condition=sys.version_info < (3, 8), reason="Test incompatible with Python < 3.8")
    async def test_main_output_two_vehicles(self, stdout: StringIO):
        await volkswagencarnet.vw_connection.main()
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
