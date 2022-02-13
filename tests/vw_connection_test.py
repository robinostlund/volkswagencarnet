import logging.config
import sys

# This won't work on python versions less than 3.8
if sys.version_info >= (3, 8):
    from unittest import IsolatedAsyncioTestCase
else:

    class IsolatedAsyncioTestCase:
        pass


import unittest
from io import StringIO
from sys import argv
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


class CmdLineTest(IsolatedAsyncioTestCase, unittest.TestCase):
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
        # TODO: use patch to only change argv during the test?
        if "-v" in argv:
            argv.remove("-v")
        if "-vv" in argv:
            argv.remove("-vv")
        # Assert default logger level is ERROR
        await volkswagencarnet.vw_connection.main()
        logger_config.assert_called_with(level=logging.ERROR)

        # -v should be INFO
        argv.append("-v")
        await volkswagencarnet.vw_connection.main()
        logger_config.assert_called_with(level=logging.INFO)
        argv.remove("-v")

        # -vv should be DEBUG
        argv.append("-vv")
        await volkswagencarnet.vw_connection.main()
        logger_config.assert_called_with(level=logging.DEBUG)

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
