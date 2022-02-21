"""Vehicle class tests."""
import sys
from datetime import datetime

import pytest

from volkswagencarnet.vw_timer import TimerData
from volkswagencarnet.vw_utilities import json_loads
from .fixtures.connection import TimersConnection
from .fixtures.constants import status_report_json_file, MOCK_VIN

if sys.version_info >= (3, 8):
    # This won't work on python versions less than 3.8
    from unittest import IsolatedAsyncioTestCase
else:
    from unittest import TestCase

    class IsolatedAsyncioTestCase(TestCase):
        """Python 3.7 compatibility dummy class."""

        pass


from unittest.mock import MagicMock, patch

from aiohttp import ClientSession
from freezegun import freeze_time

from volkswagencarnet.vw_vehicle import Vehicle


class VehicleTest(IsolatedAsyncioTestCase):
    """Test Vehicle methods."""

    @freeze_time("2022-02-14 03:04:05")
    async def test_init(self):
        """Test __init__."""
        async with ClientSession() as conn:
            target_date = datetime.fromisoformat("2022-02-14 03:04:05")
            url = "https://foo.bar"
            vehicle = Vehicle(conn, url)
            self.assertEqual(conn, vehicle._connection)
            self.assertEqual(url, vehicle._url)
            self.assertEqual("https://msg.volkswagen.de", vehicle._homeregion)
            self.assertFalse(vehicle._discovered)
            self.assertEqual({}, vehicle._states)
            self.assertEqual(30, vehicle._climate_duration)
            self.assertDictEqual(
                {
                    "batterycharge": {"status": "", "timestamp": target_date},
                    "climatisation": {"status": "", "timestamp": target_date},
                    "departuretimer": {"status": "", "timestamp": target_date},
                    "latest": "",
                    "lock": {"status": "", "timestamp": target_date},
                    "preheater": {"status": "", "timestamp": target_date},
                    "refresh": {"status": "", "timestamp": target_date},
                    "remaining": -1,
                    "state": "",
                },
                vehicle._requests,
            )

            self.assertDictEqual(
                {
                    "carfinder_v1": {"active": False},
                    "rbatterycharge_v1": {"active": False},
                    "rclima_v1": {"active": False},
                    "rheating_v1": {"active": False},
                    "rhonk_v1": {"active": False},
                    "rlu_v1": {"active": False},
                    "statusreport_v1": {"active": False},
                    "timerprogramming_v1": {"active": False},
                    "trip_statistic_v1": {"active": False},
                },
                vehicle._services,
            )

    def test_str(self):
        """Test __str__."""
        vehicle = Vehicle(None, "XYZ1234567890")
        self.assertEqual("XYZ1234567890", vehicle.__str__())

    def test_discover(self):
        """Test the discovery process."""
        pass

    @pytest.mark.asyncio
    async def test_get_timerprogramming(self):
        """Vehicle with timers loaded."""
        vehicle = Vehicle(conn=TimersConnection(None), url=MOCK_VIN)
        vehicle._discovered = True

        with patch.dict(vehicle._services, {"timerprogramming_v1": {"active": True}}):
            await vehicle.get_timerprogramming()
            self.assertIn("timer", vehicle._states)
            self.assertIsInstance(vehicle._states["timer"], TimerData)

    async def test_update_deactivated(self):
        """Test that calling update on a deactivated Vehicle does nothing."""
        vehicle = MagicMock(spec=Vehicle, name="MockDeactivatedVehicle")
        vehicle.update = lambda: Vehicle.update(vehicle)
        vehicle._discovered = True
        vehicle._deactivated = True

        await vehicle.update()

        vehicle.discover.assert_not_called()
        # Verify that no other methods were called
        self.assertEqual(0, vehicle.method_calls.__len__(), f"xpected none, got {vehicle.method_calls}")

    async def test_update(self):
        """Test that update calls the wanted methods and nothing else."""
        vehicle = MagicMock(spec=Vehicle, name="MockUpdateVehicle")
        vehicle.update = lambda: Vehicle.update(vehicle)

        vehicle._discovered = False
        vehicle.deactivated = False
        await vehicle.update()

        vehicle.discover.assert_called_once()
        vehicle.get_preheater.assert_called_once()
        vehicle.get_climater.assert_called_once()
        vehicle.get_trip_statistic.assert_called_once()
        vehicle.get_position.assert_called_once()
        vehicle.get_statusreport.assert_called_once()
        vehicle.get_charger.assert_called_once()
        vehicle.get_timerprogramming.assert_called_once()

        # Verify that only the expected functions above were called
        self.assertEqual(
            8, vehicle.method_calls.__len__(), f"Wrong number of methods called. Expected 8, got {vehicle.method_calls}"
        )


class VehiclePropertyTest(IsolatedAsyncioTestCase):
    """Tests for properties in Vehicle."""

    async def test_is_last_connected_supported(self):
        """Test that parsing last connected works."""
        vehicle = Vehicle(conn=None, url="dummy34")

        vehicle._discovered = True

        with patch.dict(vehicle.attrs, {}):
            res = vehicle.is_last_connected_supported
            self.assertFalse(res, "Last connected supported returned True without attributes.")

        with patch.dict(vehicle.attrs, {"StoredVehicleDataResponse": {}}):
            res = vehicle.is_last_connected_supported
            self.assertFalse(res, "Last connected supported returned True without 'vehicleData'.")

        with patch.dict(vehicle.attrs, {"StoredVehicleDataResponse": {"vehicleData": {}}}):
            res = vehicle.is_last_connected_supported
            self.assertFalse(res, "Last connected supported returned True without 'vehicleData.data'.")

        with patch.dict(vehicle.attrs, {"StoredVehicleDataResponse": {"vehicleData": {"data": []}}}):
            res = vehicle.is_last_connected_supported
            self.assertFalse(res, "Last connected supported returned True without 'vehicleData.data[].field[]'.")

        # test with a "real" response
        with open(status_report_json_file) as f:
            data = json_loads(f.read())
        with patch.dict(vehicle.attrs, data):
            res = vehicle.is_last_connected_supported
            self.assertTrue(res, "Last connected supported returned False when it should have been True")

    async def test_get_schedule3(self):
        """Test that schedule 3 support works."""
        vehicle = Vehicle(conn=TimersConnection(None), url=MOCK_VIN)
        vehicle._discovered = True

        with patch.dict(vehicle._services, {"timerprogramming_v1": {"active": True}}):
            await vehicle.get_timerprogramming()
            self.assertTrue(vehicle.is_departure_timer3_supported)
            self.assertEqual(
                {
                    "timestamp": datetime.fromisoformat("2022-02-22T20:00:22+00:00"),
                    "timerID": "3",
                    "profileID": "1",
                    "timerProgrammedStatus": "notProgrammed",
                    "timerFrequency": "cyclic",
                    "currentCalendarProvider": {},
                    "departureTimeOfDay": "07:55",
                    "departureWeekdayMask": "nnnnnyn",
                },
                vehicle.departure_timer3.__dict__,
            )

    async def test_get_schedule2(self):
        """Test that schedule 2 support works."""
        vehicle = Vehicle(conn=TimersConnection(None), url=MOCK_VIN)
        vehicle._discovered = True

        with patch.dict(vehicle._services, {"timerprogramming_v1": {"active": True}}):
            await vehicle.get_timerprogramming()
            self.assertFalse(vehicle.is_departure_timer2_supported)
            self.assertIsNone(vehicle.departure_timer2)

    async def test_get_schedule1(self):
        """Test that schedule 1 support works."""
        vehicle = Vehicle(conn=TimersConnection(None), url=MOCK_VIN)
        vehicle._discovered = True

        with patch.dict(vehicle._services, {"timerprogramming_v1": {"active": True}}):
            await vehicle.get_timerprogramming()
            self.assertFalse(vehicle.is_departure_timer1_supported)
            self.assertIsNone(vehicle.departure_timer1)

    async def test_get_schedule_not_supported(self):
        """Test that not found schedule is unsupported."""
        vehicle = Vehicle(conn=TimersConnection(None), url=MOCK_VIN)
        vehicle._discovered = True

        with patch.dict(vehicle._services, {"timerprogramming_v1": {"active": True}}):
            await vehicle.get_timerprogramming()
            self.assertFalse(vehicle.is_schedule_supported(42))
            self.assertIsNone(vehicle.schedule(42))

    async def test_last_connected(self):
        """
        Test that parsing last connected works.

        Data in json is: "tsCarSentUtc": "2022-02-14T00:00:45Z",
        and the function returns local time
        """
        vehicle = Vehicle(conn=None, url="dummy34")

        vehicle._discovered = True

        with open(status_report_json_file) as f:
            data = json_loads(f.read())
        with patch.dict(vehicle.attrs, data):
            res = vehicle.last_connected
            self.assertEqual(
                datetime.fromisoformat("2022-02-14T00:00:45+00:00").astimezone(None).strftime("%Y-%m-%d %H:%M:%S"), res
            )

    def test_requests_remaining(self):
        """Test requests remaining logic."""
        vehicle = Vehicle(conn=None, url="")
        with patch.dict(vehicle._requests, {"remaining": 22}):
            self.assertTrue(vehicle.is_requests_remaining_supported)
            self.assertEqual(22, vehicle.requests_remaining)
        # if remaining is missing _and_ attrs has no rate limit remaining attribute
        with patch.dict(vehicle._requests, {}):
            del vehicle._requests["remaining"]
            self.assertFalse(vehicle.is_requests_remaining_supported)
            with self.assertRaises(KeyError):
                vehicle.requests_remaining()

            # and with the attribute
            with patch.dict(vehicle._states, {"rate_limit_remaining": 99}):
                self.assertEqual(99, vehicle.requests_remaining)
                # attribute should be removed once read
                self.assertNotIn("rate_limit_remaining", vehicle.attrs)

    async def test_json(self):
        """Test JSON serialization of dict containing datetime."""
        vehicle = Vehicle(conn=None, url="dummy34")

        vehicle._discovered = True
        dtstring = "2022-02-22T02:22:20+02:00"
        d = datetime.fromisoformat(dtstring)

        with patch.dict(vehicle.attrs, {"a string": "yay", "some date": d}):
            res = f"{vehicle.json}"
            self.assertEqual('{\n    "a string": "yay",\n    "some date": "2022-02-22T02:22:20+02:00"\n}', res)
