"""Vehicle class tests."""

from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from aiohttp import ClientSession
from freezegun import freeze_time
import pytest
from volkswagencarnet.vw_const import Services
from volkswagencarnet.vw_vehicle import (
    ENGINE_TYPE_DIESEL,
    ENGINE_TYPE_ELECTRIC,
    ENGINE_TYPE_GASOLINE,
    Vehicle,
)


class VehicleTest(IsolatedAsyncioTestCase):
    """Test Vehicle methods."""

    @freeze_time("2022-02-14 03:04:05")
    async def test_init(self):
        """Test __init__."""
        async with ClientSession() as conn:
            target_date = datetime.fromisoformat("2022-02-14 03:04:05").replace(
                tzinfo=UTC
            )
            url = "https://foo.bar"
            vehicle = Vehicle(conn, url)
            assert conn == vehicle._connection
            assert url == vehicle._url
            assert vehicle._homeregion == "https://msg.volkswagen.de"
            assert not vehicle._discovered
            assert not vehicle._states
            expected_requests = {
                "departuretimer": {"status": "", "timestamp": target_date},
                "batterycharge": {"status": "", "timestamp": target_date},
                "climatisation": {"status": "", "timestamp": target_date},
                "refresh": {"status": "", "timestamp": target_date},
                "lock": {"status": "", "timestamp": target_date},
                "latest": "",
                "state": "",
            }

            expected_services = {
                Services.ACCESS: {"active": False},
                Services.BATTERY_CHARGING_CARE: {"active": False},
                Services.BATTERY_SUPPORT: {"active": False},
                Services.CHARGING: {"active": False},
                Services.CLIMATISATION: {"active": False},
                Services.CLIMATISATION_TIMERS: {"active": False},
                Services.DEPARTURE_PROFILES: {"active": False},
                Services.DEPARTURE_TIMERS: {"active": False},
                Services.FUEL_STATUS: {"active": False},
                Services.HONK_AND_FLASH: {"active": False},
                Services.MEASUREMENTS: {"active": False},
                Services.PARKING_POSITION: {"active": False},
                Services.TRIP_STATISTICS: {"active": False},
                Services.READINESS: {"active": False},
                Services.USER_CAPABILITIES: {"active": False},
                Services.PARAMETERS: {},
            }

            assert vehicle._requests == expected_requests
            assert vehicle._services == expected_services

    def test_str(self):
        """Test __str__."""
        vehicle = Vehicle(None, "XYZ1234567890")
        assert str(vehicle) == "XYZ1234567890"

    def test_discover(self):
        """Test the discovery process."""

    @pytest.mark.asyncio
    async def test_update_deactivated(self):
        """Test that calling update on a deactivated Vehicle does nothing."""
        vehicle = MagicMock(spec=Vehicle, name="MockDeactivatedVehicle")
        vehicle.update = lambda: Vehicle.update(vehicle)
        vehicle._discovered = True
        vehicle._deactivated = True

        await vehicle.update()

        vehicle.discover.assert_not_called()
        # Verify that no other methods were called
        assert len(vehicle.method_calls) == 0, (
            f"Expected none, got {vehicle.method_calls}"
        )

    async def test_update(self):
        """Test that update calls the wanted methods and nothing else."""
        vehicle = MagicMock(spec=Vehicle, name="MockUpdateVehicle")
        vehicle.update = lambda: Vehicle.update(vehicle)

        vehicle._discovered = False
        vehicle.deactivated = False
        await vehicle.update()

        vehicle.discover.assert_called_once()
        vehicle.get_selectivestatus.assert_called_once()
        vehicle.get_vehicle.assert_called_once()
        vehicle.get_parkingposition.assert_called_once()
        vehicle.get_trip_last.assert_called_once()
        vehicle.get_service_status.assert_called_once()

        # Verify that only the expected functions above were called
        assert len(vehicle.method_calls) == 8, (
            f"Wrong number of methods called. Expected 8, got {len(vehicle.method_calls)}"
        )


class VehiclePropertyTest(IsolatedAsyncioTestCase):
    """Tests for properties in Vehicle."""

    async def test_json(self):
        """Test JSON serialization of dict containing datetime."""
        vehicle = Vehicle(conn=None, url="dummy34")

        vehicle._discovered = True
        dtstring = "2022-02-22T02:22:20+02:00"
        d = datetime.fromisoformat(dtstring)

        with patch.dict(vehicle.attrs, {"a string": "yay", "some date": d}):
            res = f"{vehicle.json}"
            expected_res = '{\n    "a string": "yay",\n    "some date": "2022-02-22T02:22:20+02:00"\n}'
            assert res == expected_res

    async def test_lock_not_supported(self):
        """Test that remote locking throws exception if not supported."""
        vehicle = Vehicle(conn=None, url="dummy34")
        vehicle._discovered = True
        vehicle._services[Services.ACCESS] = {"active": False}

        with pytest.raises(Exception) as exc_info:
            await vehicle.set_lock("any", "")

        expected_message = "Remote lock/unlock is not supported."
        assert str(exc_info.value) == expected_message

    async def test_lock_supported(self):
        """Test that invalid locking action raises exception."""
        vehicle = Vehicle(conn=None, url="dummy34")
        vehicle._discovered = True
        vehicle._services[Services.ACCESS] = {"active": True}

        with pytest.raises(Exception) as exc_info:
            await vehicle.set_lock("any", "")

        expected_message = "Invalid lock action: any"
        assert str(exc_info.value) == expected_message

        # simulate request in progress
        vehicle._requests["lock"] = {
            "id": "Foo",
            "timestamp": datetime.now(UTC) - timedelta(seconds=20),
        }
        assert await vehicle.set_lock("lock", "") is False

    async def test_in_progress(self):
        """Test that _in_progress works as expected."""
        vehicle = Vehicle(conn=None, url="dummy34")
        vehicle._requests["timed_out"] = {
            "id": "1",
            "timestamp": datetime.now(UTC) - timedelta(minutes=20),
        }
        vehicle._requests["in_progress"] = {
            "id": 2,
            "timestamp": datetime.now(UTC) - timedelta(seconds=20),
        }
        vehicle._requests["unknown"] = {"id": "Foo"}
        assert not vehicle._in_progress("timed_out")
        assert vehicle._in_progress("in_progress")
        assert not vehicle._in_progress("not-defined")
        assert vehicle._in_progress("unknown", 2)
        assert not vehicle._in_progress("unknown", 4)

    async def test_is_primary_engine_electric(self):
        """Test primary electric engine."""
        vehicle = Vehicle(conn=None, url="dummy34")
        vehicle._states[f"{Services.MEASUREMENTS}"] = {
            "fuelLevelStatus": {"value": {"primaryEngineType": ENGINE_TYPE_ELECTRIC}}
        }
        assert vehicle.is_primary_drive_electric()
        assert not vehicle.is_primary_drive_combustion()

    async def test_is_primary_engine_combustion(self):
        """Test primary ICE."""
        vehicle = Vehicle(conn=None, url="dummy34")
        # f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.type"
        vehicle._states[f"{Services.MEASUREMENTS}"] = {
            "fuelLevelStatus": {
                "value": {
                    "primaryEngineType": ENGINE_TYPE_DIESEL,
                    "secondaryEngineType": ENGINE_TYPE_ELECTRIC,
                }
            }
        }

        assert vehicle.is_primary_drive_combustion()
        assert not vehicle.is_primary_drive_electric()
        assert not vehicle.is_secondary_drive_combustion()
        assert vehicle.is_secondary_drive_electric()

        # No secondary engine
        vehicle._states[f"{Services.MEASUREMENTS}"] = {
            "fuelLevelStatus": {"value": {"primaryEngineType": ENGINE_TYPE_GASOLINE}}
        }
        assert vehicle.is_primary_drive_combustion()
        assert not vehicle.is_secondary_drive_electric()

    async def test_has_combustion_engine(self):
        """Test check for ICE."""
        vehicle = Vehicle(conn=None, url="dummy34")
        vehicle._states[f"{Services.MEASUREMENTS}"] = {
            "fuelLevelStatus": {
                "value": {
                    "primaryEngineType": ENGINE_TYPE_DIESEL,
                    "secondaryEngineType": ENGINE_TYPE_ELECTRIC,
                }
            }
        }
        assert vehicle.has_combustion_engine

        # not sure if this exists, but :shrug:
        vehicle._states[f"{Services.MEASUREMENTS}"] = {
            "fuelLevelStatus": {
                "value": {
                    "primaryEngineType": ENGINE_TYPE_ELECTRIC,
                    "secondaryEngineType": ENGINE_TYPE_GASOLINE,
                }
            }
        }
        assert vehicle.has_combustion_engine

        # not sure if this exists, but :shrug:
        vehicle._states[f"{Services.MEASUREMENTS}"] = {
            "fuelLevelStatus": {
                "value": {
                    "primaryEngineType": ENGINE_TYPE_ELECTRIC,
                    "secondaryEngineType": ENGINE_TYPE_ELECTRIC,
                }
            }
        }
        assert not vehicle.has_combustion_engine
