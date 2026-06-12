#!/usr/bin/env python3
"""Vehicle class for Volkswagen Connect."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import UTC, datetime, timedelta, date
from json import dumps as to_json
from typing import TYPE_CHECKING, Any
import logging

if TYPE_CHECKING:
    from .vw_connection import Connection

import aiohttp
from aiohttp import ClientTimeout

from .vw_const import Services, VehicleStatusParameter as P, Paths
from .vw_exceptions import APIError, UnsupportedOperationError, VWError
from .vw_utilities import find_path, is_valid_path

# TODO
# Images (https://emea.bff.cariad.digital/media/v2/vehicle-images/WVWZZZ3HZPK002581?resolution=3x)
# {"data":[{"id":"door_right_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_front_overlay.png","fileName":"image_door_right_front_overlay.png"},{"id":"light_right","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_light_right.png","fileName":"image_light_right.png"},{"id":"sunroof_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_sunroof_overlay.png","fileName":"image_sunroof_overlay.png"},{"id":"trunk_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_trunk_overlay.png","fileName":"image_trunk_overlay.png"},{"id":"car_birdview","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_car_birdview.png","fileName":"image_car_birdview.png"},{"id":"door_left_front","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_front.png","fileName":"image_door_left_front.png"},{"id":"door_right_front","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_front.png","fileName":"image_door_right_front.png"},{"id":"sunroof","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_sunroof.png","fileName":"image_sunroof.png"},{"id":"window_right_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_right_front_overlay.png","fileName":"image_window_right_front_overlay.png"},{"id":"car_34view","url":"https://media.volkswagen.com/Vilma/V/3H9/2023/Front_Right/c8ca31fcf999b04d42940620653c494215e0d49756615f3524499261d96ccdce.png?width=1163","fileName":""},{"id":"door_left_back","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_back.png","fileName":"image_door_left_back.png"},{"id":"door_right_back","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_back.png","fileName":"image_door_right_back.png"},{"id":"window_left_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_left_back_overlay.png","fileName":"image_window_left_back_overlay.png"},{"id":"window_right_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_right_back_overlay.png","fileName":"image_window_right_back_overlay.png"},{"id":"bonnet_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_bonnet_overlay.png","fileName":"image_bonnet_overlay.png"},{"id":"door_left_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_back_overlay.png","fileName":"image_door_left_back_overlay.png"},{"id":"door_left_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_front_overlay.png","fileName":"image_door_left_front_overlay.png"},{"id":"door_right_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_back_overlay.png","fileName":"image_door_right_back_overlay.png"},{"id":"light_left","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_light_left.png","fileName":"image_light_left.png"},{"id":"window_left_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_left_front_overlay.png","fileName":"image_window_left_front_overlay.png"}]}
#
# Model Year (unclear, seems to only be available via the web API, language dependent and with separate authentication)

BACKEND_RECEIVED_TIMESTAMP = "BACKEND_RECEIVED_TIMESTAMP"

_LOGGER = logging.getLogger(__name__)

# Mapping from EMEA door names (used by existing properties) to NA doorStatus keys.
# NA uses "hood" where EMEA uses "bonnet"; all other front/rear names match.
_NA_DOOR_NAMES: dict[str, str] = {
    "frontLeft": "frontLeft",
    "frontRight": "frontRight",
    "rearLeft": "rearLeft",
    "rearRight": "rearRight",
    "trunk": "trunk",
    "bonnet": "hood",  # EMEA calls it "bonnet"; NA calls it "hood"
}

ENGINE_TYPE_ELECTRIC = "electric"
ENGINE_TYPE_DIESEL = "diesel"
ENGINE_TYPE_GASOLINE = "gasoline"
ENGINE_TYPE_CNG = "cng"
ENGINE_TYPE_HYBRID = "hybrid"
ENGINE_TYPE_COMBUSTION = [
    ENGINE_TYPE_DIESEL,
    ENGINE_TYPE_GASOLINE,
    ENGINE_TYPE_CNG,
]
ENGINE_TYPE_GAS = [ENGINE_TYPE_CNG]
DEFAULT_TARGET_TEMP = 24


class Vehicle:
    """Vehicle contains the state of sensors and methods for interacting with the car."""

    def __init__(self, conn: Connection | None, url: str | None) -> None:
        """Initialize the Vehicle with default values."""
        self._connection = conn
        self._url = url
        # Get homeregion from connection's region config
        if conn is not None and hasattr(conn, "_session_region_config"):
            region_config = conn._session_region_config
            self._homeregion = (
                region_config.get("homeregion") or "https://msg.volkswagen.de"
            )
        else:
            self._homeregion = "https://msg.volkswagen.de"
        self._home_region_discovered: bool = (
            False  # session cache guard for lazy discovery
        )
        self._discovered = False
        self._states: dict[str, Any] = {}
        self._requests: dict[str, Any] = {
            "departuretimer": {"status": "", "timestamp": datetime.now(UTC)},
            "batterycharge": {"status": "", "timestamp": datetime.now(UTC)},
            "climatisation": {"status": "", "timestamp": datetime.now(UTC)},
            "refresh": {"status": "", "timestamp": datetime.now(UTC)},
            "lock": {"status": "", "timestamp": datetime.now(UTC)},
            "latest": "",
            "state": "",
        }

        # API Endpoints that might be enabled for car (that we support)
        self._services: dict[str, Any] = {
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
            Services.READINESS: {"active": False},
            Services.TRIP_STATISTICS: {"active": False},
            Services.USER_CAPABILITIES: {"active": False},
            Services.PARAMETERS: {},
        }

    def _in_progress(self, topic: str, unknown_offset: int = 0) -> bool:
        """Check if request is already in progress."""
        if self._requests.get(topic, {}).get("id", False):
            timestamp = self._requests.get(topic, {}).get(
                "timestamp",
                datetime.now(UTC) - timedelta(minutes=unknown_offset),
            )
            if timestamp + timedelta(minutes=3) < datetime.now(UTC):
                self._requests.get(topic, {}).pop("id")
            else:
                _LOGGER.info("Action (%s) already in progress", topic)
                return True
        return False

    async def _handle_response(
        self, response: Any, topic: str, error_msg: str | None = None
    ) -> bool:
        """Handle errors in response and get requests remaining."""
        if not response:
            self._requests[topic] = {
                "status": "Failed",
                "timestamp": datetime.now(UTC),
            }
            _LOGGER.error(
                error_msg
                if error_msg is not None
                else f"Failed to perform {topic} action"
            )

            raise APIError(
                error_msg
                if error_msg is not None
                else f"Failed to perform {topic} action"
            )
        self._requests[topic] = {
            "timestamp": datetime.now(UTC),
            "status": response.get("state", "Unknown"),
            "id": response.get("id", 0),
        }
        if response.get("state", None) == "Throttled":
            status = "Throttled"
            _LOGGER.warning("Request throttled (%s)", topic)
        else:
            status = await self.wait_for_request(request=response.get("id", 0))
        self._requests[topic] = {
            "status": status,
            "timestamp": datetime.now(UTC),
        }
        return True

    async def _ensure_home_region(self) -> None:
        """Lazily discover and cache this vehicle's home region server.

        Called at the start of discover() on first vehicle API access.
        Sets self._home_region_discovered = True immediately to prevent
        concurrent re-entry (optimistic lock pattern).

        For NA region: probes homeregion_candidates from region config.
        Any HTTP response (200, 400, 401, 403, 404) = server is reachable.
        Validates candidate URL against VW_DOMAIN_ALLOWLIST before assigning.

        For EMEA: returns immediately (homeregion is statically configured).

        On failure: logs WARNING and retains self._homeregion fallback value.
        """
        if self._home_region_discovered:
            return

        # Set guard BEFORE async probes to prevent concurrent re-entry
        self._home_region_discovered = True

        if self._connection is None:
            return

        if not self._connection.is_na:
            return  # EMEA: home region is static from config — no discovery needed

        candidates = self._connection._session_region_config.get(
            "homeregion_candidates", []
        )

        timeout = ClientTimeout(total=10)

        for candidate in candidates:
            # Validate candidate before probing — skip malformed entries
            if not self._connection._is_allowed_vw_domain(candidate):
                _LOGGER.debug(
                    "Home region candidate %r failed domain validation, skipping",
                    candidate,
                )
                continue

            probe_url = f"{candidate}/vehicle/v1/vehicles/{self._url}/capabilities"
            try:
                _LOGGER.debug("Probing home region candidate: %s", candidate)
                async with self._connection._session.get(
                    url=probe_url,
                    headers=self._connection._session_headers,
                    timeout=timeout,
                    raise_for_status=False,
                ) as resp:
                    # Any response (even auth error) = server is reachable and routing works
                    if resp.status in (200, 400, 401, 403, 404):
                        self._homeregion = candidate
                        _LOGGER.debug(
                            "Home region for %s: %s (HTTP %d)",
                            self._url,
                            candidate,
                            resp.status,
                        )
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                _LOGGER.debug(
                    "Home region probe failed for %s at %s: %s",
                    self._url,
                    candidate,
                    exc,
                )
                continue

        _LOGGER.warning(
            "Could not discover home region for %s, using fallback: %s",
            self._url,
            self._homeregion,
        )
        # self._homeregion retains its initialized value (from __init__)

    # API get and set functions #
    # Init and update vehicle data
    async def discover(self) -> None:
        """Discover vehicle and initial data."""
        await self._ensure_home_region()

        # NA region: skip EMEA capability/selectivestatus endpoints (return 404 for NA)
        if self._connection is not None and self._connection.is_na:
            _LOGGER.debug("NA vehicle %s: skipping EMEA capability discovery", self.vin)
            self._discovered = True
            return

        _LOGGER.debug("Attempting discovery of supported API endpoints for vehicle")

        if self._connection is None:
            self._discovered = True
            return
        capabilities_response = await self._connection.getOperationList(self.vin)
        parameters_list = capabilities_response.get("parameters", {})
        capabilities_list = capabilities_response.get("capabilities", {})

        # Update services with parameters
        if parameters_list:
            self._services[Services.PARAMETERS].update(parameters_list)

        # If there are no capabilities, log a warning
        if not capabilities_list:
            _LOGGER.warning(
                "Could not determine available API endpoints for %s", self.vin
            )
            self._discovered = True
            return

        for service_id, service in capabilities_list.items():
            if service_id not in self._services:
                continue

            service_name = service.get("id", "Unknown Service")
            data: dict[str, Any] = {}

            if service.get("isEnabled", False):
                data["active"] = True
                _LOGGER.debug("Discovered enabled service: %s", service_name)

                expiration_date = service.get("expirationDate", None)
                if expiration_date:
                    data["expiration"] = expiration_date

                operations = service.get("operations", {})
                data["operations"] = [op.get("id", None) for op in operations.values()]

                parameters = service.get("parameters", [])
                data["parameters"] = parameters

            else:
                reason = service.get("status", "Unknown reason")
                _LOGGER.debug(
                    "Service: %s is disabled due to: %s", service_name, reason
                )
                data["active"] = False

            # Update the service data
            try:
                self._services[service_name].update(data)
            except Exception as error:  # pylint: disable=broad-exception-caught
                _LOGGER.warning(
                    'Exception "%s" while updating service "%s": %s',
                    error,
                    service_name,
                    data,
                )

        _LOGGER.debug("API endpoints: %s", self._services)
        self._discovered = True

    async def _update_na_vehicle(self) -> bool:
        """Fetch NA vehicle telemetry from RVS and supplemental endpoints.

        Calls Connection._get_na_vehicle_data() and stores the result in _states.
        Returns True if _get_na_vehicle_data returned a result dict (even if individual
        endpoints failed), False only when vehicle session creation itself fails.
        """
        if self._connection is None:
            return False
        data = await self._connection._get_na_vehicle_data(self.vin)
        if data is None:
            _LOGGER.warning("NA vehicle %s: all telemetry fetches failed", self.vin)
            return False
        self._states.update({k: v for k, v in data.items() if v is not None})
        _LOGGER.debug(
            "NA vehicle %s: updated states keys=%s", self.vin, list(data.keys())
        )
        return True

    async def update(self) -> None:
        """Try to fetch data for all known API endpoints."""
        if not self._discovered:
            await self.discover()
        if not self.deactivated:
            # NA region: use RVS endpoint path instead of EMEA selectivestatus
            if self._connection is not None and self._connection.is_na:
                await self._update_na_vehicle()
                return
            await asyncio.gather(
                self.get_selectivestatus(
                    [
                        Services.ACCESS,
                        Services.BATTERY_CHARGING_CARE,
                        Services.BATTERY_SUPPORT,
                        Services.CHARGING,
                        Services.CLIMATISATION,
                        Services.CLIMATISATION_TIMERS,
                        Services.DEPARTURE_PROFILES,
                        Services.DEPARTURE_TIMERS,
                        Services.FUEL_STATUS,
                        Services.MEASUREMENTS,
                        Services.READINESS,
                        Services.VEHICLE_LIGHTS,
                        Services.VEHICLE_HEALTH_INSPECTION,
                        Services.USER_CAPABILITIES,
                    ]
                ),
                self.get_vehicle(),
                self.get_parkingposition(),
                self.get_trip_last(),
                self.get_trip_refuel(),
                self.get_trip_longterm(),
            )
            await asyncio.gather(self.get_service_status())
        else:
            _LOGGER.info("Vehicle with VIN %s is deactivated", self.vin)

    # Data collection functions
    async def get_selectivestatus(self, services: list[str]) -> None:
        """Fetch selective status for specified services."""
        if self._connection is None:
            return
        data = await self._connection.getSelectiveStatus(self.vin, services)
        if data:
            self._states.update(data)

    async def get_vehicle(self) -> None:
        """Fetch car masterdata."""
        if self._connection is None:
            return
        data = await self._connection.getVehicleData(self.vin)
        if data:
            self._states.update(data)

    async def get_parkingposition(self) -> None:
        """Fetch parking position if supported."""
        if self._connection is None:
            return
        if self._services.get(Services.PARKING_POSITION, {}).get("active", False):
            data = await self._connection.getParkingPosition(self.vin)
            if data:
                self._states.update(data)

    async def get_trip_last(self) -> None:
        """Fetch last trip statistics if supported."""
        if self._connection is None:
            return
        if self._services.get(Services.TRIP_STATISTICS, {}).get("active", False):
            data = await self._connection.getTripLast(self.vin)
            if data:
                self._states.update(data)

    async def get_trip_refuel(self) -> None:
        """Fetch trip since refuel statistics if supported."""
        if self._connection is None:
            return
        if self._services.get(Services.TRIP_STATISTICS, {}).get("active", False):
            data = await self._connection.getTripRefuel(self.vin)
            if data:
                self._states.update(data)

    async def get_trip_longterm(self) -> None:
        """Fetch trip since refuel statistics if supported."""
        if self._connection is None:
            return
        if self._services.get(Services.TRIP_STATISTICS, {}).get("active", False):
            data = await self._connection.getTripLongterm(self.vin)
            if data:
                self._states.update(data)

    async def get_service_status(self) -> None:
        """Fetch service status."""
        if self._connection is None:
            return
        data = await self._connection.get_service_status()
        if data:
            self._states.update({Services.SERVICE_STATUS: data})

    async def wait_for_request(self, request: Any, retry_count: int = 18) -> str:
        """Update status of outstanding requests."""
        for _ in range(retry_count - 1):
            try:
                if self._connection is None:
                    return "Exception"
                status = await self._connection.get_request_status(self.vin, request)
                _LOGGER.debug("Request ID %s: %s", request, status)
                self._requests["state"] = status
                if status != "In Progress":
                    return status
                await asyncio.sleep(10)
            except (asyncio.TimeoutError, aiohttp.ClientError, VWError) as error:
                _LOGGER.warning(
                    "Exception encountered while waiting for request status: %s", error
                )
                return "Exception"
        _LOGGER.info("Timeout while waiting for result of %s", request.requestId)
        return "Timeout"

    async def wait_for_data_refresh(self, retry_count: int = 18) -> str:
        """Update status of outstanding requests."""
        for _ in range(retry_count - 1):
            try:
                await self.get_selectivestatus([Services.MEASUREMENTS])
                refresh_trigger_time = self._requests.get("refresh", {}).get(
                    "timestamp"
                )
                if self.last_connected >= refresh_trigger_time:
                    return "successful"
                await asyncio.sleep(10)
            except (asyncio.TimeoutError, aiohttp.ClientError, VWError) as error:
                _LOGGER.warning(
                    "Exception encountered while waiting for data refresh: %s", error
                )
                return "Exception"
        _LOGGER.info("Timeout while waiting for data refresh")
        return "Timeout"

    # Data set functions
    # Charging (BATTERYCHARGE)
    async def set_charger(self, action: str) -> bool:
        """Turn on/off charging."""
        # NA path
        if self._connection is not None and self._connection.is_na:
            if action not in ["start", "stop"]:
                _LOGGER.error('Charging action "%s" is not supported', action)
                raise UnsupportedOperationError(
                    f'Charging action "{action}" is not supported.'
                )
            if not self.is_charging_supported:
                _LOGGER.error("No charging support (vehicle may not be electric)")
                raise UnsupportedOperationError("No charging support.")
            if self._in_progress("charging", unknown_offset=-5):
                _LOGGER.debug(
                    "NA: charging command already in progress for vin=%s, ignoring duplicate",
                    self.vin,
                )
                return False
            self._requests["latest"] = "Batterycharge"
            self._requests["charging"] = {
                "id": "na-charging-in-flight",
                "timestamp": datetime.now(UTC),
            }
            if action == "start":
                result = await self._connection.start_charging_na(self.vin)
            else:
                result = await self._connection.stop_charging_na(self.vin)
            if not result:
                _LOGGER.warning(
                    "NA: %s_charging command failed for vin=%s", action, self.vin
                )
            self._requests["charging"] = {
                "status": "Completed" if result else "Failed",
                "timestamp": datetime.now(UTC),
            }
            return result

        # EMEA path — preserves original check order
        if self.is_charging_supported:
            if action not in ["start", "stop"]:
                _LOGGER.error('Charging action "%s" is not supported', action)
                raise UnsupportedOperationError(
                    f'Charging action "{action}" is not supported.'
                )
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            self._requests["latest"] = "Batterycharge"
            response = await self._connection.setCharging(self.vin, (action == "start"))
            return await self._handle_response(
                response=response,
                topic="charging",
                error_msg=f"Failed to {action} charging",
            )
        _LOGGER.error("No charging support")
        raise UnsupportedOperationError("No charging support.")

    async def set_charging_settings(self, setting: str, value: Any) -> bool:
        """Set charging settings."""
        if (
            self.is_charge_max_ac_setting_supported
            or self.is_auto_release_ac_connector_supported
            or self.is_battery_target_charge_level_supported
            or self.is_charge_max_ac_ampere_supported
        ):
            if setting == "reduced_ac_charging" and value not in ["reduced", "maximum"]:
                _LOGGER.error('Charging setting "%s" is not supported', value)
                raise UnsupportedOperationError(
                    f'Charging setting "{value}" is not supported.'
                )
            if setting == "max_charge_amperage" and int(value) not in [
                5,
                10,
                13,
                16,
                32,
            ]:
                _LOGGER.error(
                    "Setting maximum charge amperage to %s is not supported", value
                )

                raise UnsupportedOperationError(
                    f"Setting maximum charge amperage to {value} is not supported."
                )
            data = {}
            if (
                self.is_charge_max_ac_setting_supported
                and setting != "max_charge_amperage"
            ):
                data["maxChargeCurrentAC"] = (
                    value
                    if setting == "reduced_ac_charging"
                    else self.charge_max_ac_setting
                )
            if self.is_auto_release_ac_connector_supported:
                data["autoUnlockPlugWhenChargedAC"] = (
                    value
                    if setting == "auto_release_ac_connector"
                    else self.auto_release_ac_connector_state
                )
            if self.is_battery_target_charge_level_supported:
                data["targetSOC_pct"] = (
                    value
                    if setting == "battery_target_charge_level"
                    else self.battery_target_charge_level
                )
            if (
                self.is_charge_max_ac_ampere_supported
                and setting != "reduced_ac_charging"
            ):
                data["maxChargeCurrentAC_A"] = (
                    int(value)
                    if setting == "max_charge_amperage"
                    else self.charge_max_ac_ampere
                )
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            self._requests["latest"] = "Batterycharge"
            response = await self._connection.setChargingSettings(self.vin, data)
            return await self._handle_response(
                response=response,
                topic="charging",
                error_msg="Failed to change charging settings",
            )
        _LOGGER.error("Charging settings are not supported")
        raise UnsupportedOperationError("Charging settings are not supported.")

    async def set_charging_care_settings(self, value: Any) -> bool:
        """Set charging care settings."""
        if self.is_battery_care_mode_supported:
            if value not in ["activated", "deactivated"]:
                _LOGGER.error('Charging care mode "%s" is not supported', value)
                raise UnsupportedOperationError(
                    f'Charging care mode "{value}" is not supported.'
                )
            data = {"batteryCareMode": value}
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            self._requests["latest"] = "Batterycharge"
            response = await self._connection.setChargingCareModeSettings(
                self.vin, data
            )
            return await self._handle_response(
                response=response,
                topic="charging",
                error_msg="Failed to change charging care settings",
            )
        _LOGGER.error("Charging care settings are not supported")
        raise UnsupportedOperationError("Charging care settings are not supported.")

    async def set_readiness_battery_support(self, value: Any) -> bool:
        """Set readiness battery support settings."""
        if self.is_optimised_battery_use_supported:
            if value not in [True, False]:
                _LOGGER.error('Battery support mode "%s" is not supported', value)
                raise UnsupportedOperationError(
                    f'Battery support mode "{value}" is not supported.'
                )
            data = {"batterySupportEnabled": value}
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            self._requests["latest"] = "Batterycharge"
            response = await self._connection.setReadinessBatterySupport(self.vin, data)
            return await self._handle_response(
                response=response,
                topic="charging",
                error_msg="Failed to change battery support settings",
            )
        _LOGGER.error("Battery support settings are not supported")
        raise UnsupportedOperationError("Battery support settings are not supported.")

    # Climatisation electric/auxiliary/windows (CLIMATISATION)
    async def set_climatisation_settings(self, setting: str, value: Any) -> bool:
        """Set climatisation settings."""
        if (
            self.is_climatisation_target_temperature_supported
            or self.is_climatisation_without_external_power_supported
            or self.is_auxiliary_air_conditioning_supported
            or self.is_automatic_window_heating_supported
            or self.is_zone_front_left_supported
            or self.is_zone_front_right_supported
        ):
            if (
                setting == "climatisation_target_temperature"
                and 15.5 <= float(value) <= 30
                or setting
                in [
                    "climatisation_without_external_power",
                    "auxiliary_air_conditioning",
                    "automatic_window_heating",
                    "zone_front_left",
                    "zone_front_right",
                ]
                and value in [True, False]
            ):
                temperature = (
                    value
                    if setting == "climatisation_target_temperature"
                    else (
                        self.climatisation_target_temperature
                        if self.climatisation_target_temperature is not None
                        else DEFAULT_TARGET_TEMP
                    )
                )
                data = {
                    "targetTemperature": float(temperature),
                    "targetTemperatureUnit": "celsius",
                }
                if self.is_climatisation_without_external_power_supported:
                    data["climatisationWithoutExternalPower"] = (
                        value
                        if setting == "climatisation_without_external_power"
                        else self.climatisation_without_external_power
                    )
                if self.is_auxiliary_air_conditioning_supported:
                    data["climatizationAtUnlock"] = (
                        value
                        if setting == "auxiliary_air_conditioning"
                        else self.auxiliary_air_conditioning
                    )
                if self.is_automatic_window_heating_supported:
                    data["windowHeatingEnabled"] = (
                        value
                        if setting == "automatic_window_heating"
                        else self.automatic_window_heating
                    )
                if self.is_zone_front_left_supported:
                    data["zoneFrontLeftEnabled"] = (
                        value if setting == "zone_front_left" else self.zone_front_left
                    )
                if self.is_zone_front_right_supported:
                    data["zoneFrontRightEnabled"] = (
                        value
                        if setting == "zone_front_right"
                        else self.zone_front_right
                    )
                if self._connection is None:
                    raise RuntimeError("Vehicle not associated with a connection")
                self._requests["latest"] = "Climatisation"
                response = await self._connection.setClimaterSettings(self.vin, data)
                return await self._handle_response(
                    response=response,
                    topic="climatisation",
                    error_msg="Failed to set climatisation settings",
                )
            _LOGGER.error('Set climatisation setting to "%s" is not supported', value)
            raise UnsupportedOperationError(
                f'Set climatisation setting to "{value}" is not supported.'
            )
        _LOGGER.error("Climatisation settings are not supported")
        raise UnsupportedOperationError("Climatisation settings are not supported.")

    async def set_window_heating(self, action: str = "stop") -> bool:
        """Turn on/off window heater."""
        if self.is_window_heater_supported:
            if action not in ["start", "stop"]:
                _LOGGER.error('Window heater action "%s" is not supported', action)
                raise UnsupportedOperationError(
                    f'Window heater action "{action}" is not supported.'
                )
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            self._requests["latest"] = "Climatisation"
            response = await self._connection.setWindowHeater(
                self.vin, (action == "start")
            )
            return await self._handle_response(
                response=response,
                topic="climatisation",
                error_msg=f"Failed to {action} window heating",
            )
        _LOGGER.error("No climatisation support")
        raise UnsupportedOperationError("No climatisation support.")

    async def set_climatisation(self, action: str = "stop") -> bool:
        """Turn on/off climatisation with electric heater."""
        # NA path
        if self._connection is not None and self._connection.is_na:
            if action not in ["start", "stop"]:
                _LOGGER.error("Invalid climatisation action: %s", action)
                raise UnsupportedOperationError(
                    f"Invalid climatisation action: {action}"
                )
            if not self.is_climatisation_state_supported:
                _LOGGER.error(
                    "No climatisation support (vehicle may not support pre-trip climate)"
                )
                raise UnsupportedOperationError("No climatisation support.")
            if self._in_progress("climatisation", unknown_offset=-5):
                _LOGGER.debug(
                    "NA: climatisation command already in progress for vin=%s, ignoring duplicate",
                    self.vin,
                )
                return False
            self._requests["latest"] = "Climatisation"
            self._requests["climatisation"] = {
                "id": "na-climatisation-in-flight",
                "timestamp": datetime.now(UTC),
            }
            if action == "start":
                result = await self._connection.start_climatisation_na(self.vin)
            else:
                result = await self._connection.stop_climatisation_na(self.vin)
            if not result:
                _LOGGER.warning(
                    "NA: %s_climatisation command failed for vin=%s", action, self.vin
                )
            self._requests["climatisation"] = {
                "status": "Completed" if result else "Failed",
                "timestamp": datetime.now(UTC),
            }
            return result

        # EMEA path — preserves original check order
        if self.is_electric_climatisation_supported:
            if action == "start":
                data = {
                    "targetTemperature": self.climatisation_target_temperature,
                    "targetTemperatureUnit": "celsius",
                }
                if self.is_climatisation_without_external_power_supported:
                    data["climatisationWithoutExternalPower"] = (
                        self.climatisation_without_external_power
                    )
                if self.is_auxiliary_air_conditioning_supported:
                    data["climatizationAtUnlock"] = self.auxiliary_air_conditioning
                if self.is_automatic_window_heating_supported:
                    data["windowHeatingEnabled"] = self.automatic_window_heating
                if self.is_zone_front_left_supported:
                    data["zoneFrontLeftEnabled"] = self.zone_front_left
                if self.is_zone_front_right_supported:
                    data["zoneFrontRightEnabled"] = self.zone_front_right
            elif action == "stop":
                data = {}
            else:
                _LOGGER.error("Invalid climatisation action: %s", action)
                raise UnsupportedOperationError(
                    f"Invalid climatisation action: {action}"
                )
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            self._requests["latest"] = "Climatisation"
            response = await self._connection.setClimater(
                self.vin, data, (action == "start")
            )
            return await self._handle_response(
                response=response,
                topic="climatisation",
                error_msg=f"Failed to {action} climatisation with electric heater.",
            )
        _LOGGER.error("No climatisation support")
        raise UnsupportedOperationError("No climatisation support.")

    async def set_auxiliary_climatisation(self, action: str, spin: str) -> bool:
        """Turn on/off climatisation with auxiliary heater."""
        if self.is_auxiliary_climatisation_supported:
            data: dict[str, Any] = {}
            if action == "start":
                data = {"spin": spin}
                if self.is_auxiliary_duration_supported:
                    data["duration_min"] = self.auxiliary_duration
            elif action == "stop":
                data = {}
            else:
                _LOGGER.error("Invalid auxiliary heater action: %s", action)
                raise UnsupportedOperationError(
                    f"Invalid auxiliary heater action: {action}"
                )
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            self._requests["latest"] = "Climatisation"
            response = await self._connection.setAuxiliary(
                self.vin, data, (action == "start")
            )
            return await self._handle_response(
                response=response,
                topic="climatisation",
                error_msg=f"Failed to {action} climatisation with auxiliary heater.",
            )
        _LOGGER.error("No climatisation support")
        raise UnsupportedOperationError("No climatisation support.")

    async def set_departure_timer(self, timer_id: int, spin: str, enable: bool) -> bool:
        """Turn on/off departure timer."""
        if self.is_departure_timer_supported(timer_id):
            if not isinstance(enable, bool):
                _LOGGER.error("Charging departure timers setting is not supported")
                raise UnsupportedOperationError(
                    "Charging departure timers setting is not supported."
                )
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            data = None
            response = None
            if is_valid_path(
                self.attrs, Paths.DEPARTURE_PROFILES_TIMERS
            ) and is_valid_path(self.attrs, Paths.DEPARTURE_PROFILES_PROFILES):
                timers = find_path(self.attrs, Paths.DEPARTURE_PROFILES_TIMERS)
                profiles = find_path(self.attrs, Paths.DEPARTURE_PROFILES_PROFILES)
                for index, timer in enumerate(timers):
                    if timer.get("id", 0) == timer_id:
                        timers[index]["enabled"] = enable
                data = {"timers": timers, "profiles": profiles}
                response = await self._connection.setDepartureProfiles(self.vin, data)
            if is_valid_path(self.attrs, Paths.AUXILIARY_HEATING_TIMERS):
                timers = find_path(self.attrs, Paths.AUXILIARY_HEATING_TIMERS)
                for index, timer in enumerate(timers):
                    if timer.get("id", 0) == timer_id:
                        timers[index]["enabled"] = enable
                data = {"spin": spin, "timers": timers}
                response = await self._connection.setAuxiliaryHeatingTimers(
                    self.vin, data
                )
            if is_valid_path(self.attrs, Paths.DEPARTURE_TIMERS):
                timers = find_path(self.attrs, Paths.DEPARTURE_TIMERS)
                for index, timer in enumerate(timers):
                    if timer.get("id", 0) == timer_id:
                        timers[index]["enabled"] = enable
                data = {"timers": timers}
                response = await self._connection.setDepartureTimers(self.vin, data)
            return await self._handle_response(
                response=response,
                topic="departuretimer",
                error_msg="Failed to change departure timers setting.",
            )
        _LOGGER.error("Departure timers are not supported")
        raise UnsupportedOperationError("Departure timers are not supported.")

    async def update_departure_timer(
        self, timer_id: int, spin: str, timer_data: dict[str, Any]
    ) -> bool:
        """Turn on/off departure timer."""
        if self.is_departure_timer_supported(timer_id):
            if timer_data is None:
                _LOGGER.error("Charging departure timers setting is not supported")
                raise UnsupportedOperationError(
                    "Charging departure timers setting is not supported."
                )
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            data = None
            response = None
            if is_valid_path(
                self.attrs, Paths.DEPARTURE_PROFILES_TIMERS
            ) and is_valid_path(self.attrs, Paths.DEPARTURE_PROFILES_PROFILES):
                timers = find_path(self.attrs, Paths.DEPARTURE_PROFILES_TIMERS)
                profiles = find_path(self.attrs, Paths.DEPARTURE_PROFILES_PROFILES)
                for index, timer in enumerate(timers):
                    if timer.get("id", 0) == timer_id:
                        timers[index] = timer_data
                data = {"timers": timers, "profiles": profiles}
                response = await self._connection.setDepartureProfiles(self.vin, data)
            if is_valid_path(self.attrs, Paths.AUXILIARY_HEATING_TIMERS):
                timers = find_path(self.attrs, Paths.AUXILIARY_HEATING_TIMERS)
                for index, timer in enumerate(timers):
                    if timer.get("id", 0) == timer_id:
                        timers[index] = timer_data
                data = {"spin": spin, "timers": timers}
                response = await self._connection.setAuxiliaryHeatingTimers(
                    self.vin, data
                )
            if is_valid_path(self.attrs, Paths.DEPARTURE_TIMERS):
                timers = find_path(self.attrs, Paths.DEPARTURE_TIMERS)
                for index, timer in enumerate(timers):
                    if timer.get("id", 0) == timer_id:
                        timers[index] = timer_data
                data = {"timers": timers}
                response = await self._connection.setDepartureTimers(self.vin, data)
            return await self._handle_response(
                response=response,
                topic="departuretimer",
                error_msg="Failed to change departure timers setting.",
            )
        _LOGGER.error("Departure timers are not supported")
        raise UnsupportedOperationError("Departure timers are not supported.")

    async def set_ac_departure_timer(self, timer_id: int, enable: bool) -> bool:
        """Turn on/off ac departure timer."""
        if self.is_ac_departure_timer_supported(timer_id):
            if not isinstance(enable, bool):
                _LOGGER.error(
                    "Charging climatisation departure timers setting is not supported"
                )
                raise UnsupportedOperationError(
                    "Charging climatisation departure timers setting is not supported."
                )
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            timers = find_path(self.attrs, Paths.CLIMATISATION_TIMERS)
            for index, timer in enumerate(timers):
                if timer.get("id", 0) == timer_id:
                    timers[index]["enabled"] = enable
            data = {"timers": timers}
            response = await self._connection.setClimatisationTimers(self.vin, data)
            return await self._handle_response(
                response=response,
                topic="departuretimer",
                error_msg="Failed to change climatisation departure timers setting.",
            )
        _LOGGER.error("Climatisation departure timers are not supported")
        raise UnsupportedOperationError(
            "Climatisation departure timers are not supported."
        )

    async def update_ac_departure_timer(
        self, timer_id: int, timer_data: dict[str, Any]
    ) -> bool:
        """Turn on/off ac departure timer."""
        if self.is_ac_departure_timer_supported(timer_id):
            if timer_data is None:
                _LOGGER.error(
                    "Charging climatisation departure timers setting is not supported"
                )
                raise UnsupportedOperationError(
                    "Charging climatisation departure timers setting is not supported."
                )
            if self._connection is None:
                raise RuntimeError("Vehicle not associated with a connection")
            data = None
            response = None
            timers = find_path(self.attrs, Paths.CLIMATISATION_TIMERS)
            for index, timer in enumerate(timers):
                if timer.get("id", 0) == timer_id:
                    timers[index] = timer_data
            data = {"timers": timers}
            response = await self._connection.setClimatisationTimers(self.vin, data)
            return await self._handle_response(
                response=response,
                topic="departuretimer",
                error_msg="Failed to change climatisation departure timers setting.",
            )
        _LOGGER.error("Climatisation departure timers are not supported")
        raise UnsupportedOperationError(
            "Climatisation departure timers are not supported."
        )

    # Lock (RLU)
    async def set_lock(self, action: str, spin: str) -> bool:
        """Remote lock and unlock actions."""
        # NA path: no SPIN, no service discovery
        if self._connection is not None and self._connection.is_na:
            if action not in ["lock", "unlock"]:
                _LOGGER.error("Invalid lock action: %s", action)
                raise UnsupportedOperationError(f"Invalid lock action: {action}")
            if self._in_progress("lock", unknown_offset=-5):
                _LOGGER.debug(
                    "NA: lock command already in progress for vin=%s, ignoring duplicate",
                    self.vin,
                )
                return False
            self._requests["latest"] = "Lock"
            self._requests["lock"] = {
                "id": "na-lock-in-flight",
                "timestamp": datetime.now(UTC),
            }
            result = await self._connection.lock_na(self.vin, action)
            if not result:
                _LOGGER.warning("NA: %s command failed for vin=%s", action, self.vin)
            self._requests["lock"] = {
                "status": "Completed" if result else "Failed",
                "timestamp": datetime.now(UTC),
            }
            return result

        # EMEA path — preserves original check order
        if not self._services.get(Services.ACCESS, {}).get("active", False):
            _LOGGER.info("Remote lock/unlock is not supported")
            raise UnsupportedOperationError("Remote lock/unlock is not supported.")
        if self._in_progress("lock", unknown_offset=-5):
            return False
        if action not in ["lock", "unlock"]:
            _LOGGER.error("Invalid lock action: %s", action)
            raise UnsupportedOperationError(f"Invalid lock action: {action}")

        if self._connection is None:
            raise RuntimeError("Vehicle not associated with a connection")
        try:
            self._requests["latest"] = "Lock"
            response = await self._connection.setLock(
                self.vin, (action == "lock"), spin
            )
            return await self._handle_response(
                response=response,
                topic="access",
                error_msg=f"Failed to {action} vehicle",
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Failed to %s vehicle - %s", action, error)
            self._requests["lock"] = {
                "status": "Exception",
                "timestamp": datetime.now(UTC),
            }
            raise APIError("Lock action failed") from error

    # Honk and flash
    async def set_honk_and_flash(self) -> bool:
        """Remote honk and flash actions."""
        # NA path
        if self._connection is not None and self._connection.is_na:
            if self._in_progress("honk_and_flash", unknown_offset=-5):
                _LOGGER.debug(
                    "NA: honk_and_flash command already in progress for vin=%s, ignoring duplicate",
                    self.vin,
                )
                return False
            self._requests["latest"] = "HonkAndFlash"
            self._requests["honk_and_flash"] = {
                "id": "na-honk-in-flight",
                "timestamp": datetime.now(UTC),
            }
            result = await self._connection.honk_and_flash_na(self.vin)
            if not result:
                _LOGGER.warning(
                    "NA: honk_and_flash command failed for vin=%s", self.vin
                )
            self._requests["honk_and_flash"] = {
                "status": "Completed" if result else "Failed",
                "timestamp": datetime.now(UTC),
            }
            return result

        if not self._services.get(Services.HONK_AND_FLASH, {}).get("active", False):
            _LOGGER.info("Remote honk and flash is not supported")
            raise UnsupportedOperationError("Remote honk and flash is not supported.")
        if self._in_progress("honk_and_flash", unknown_offset=-5):
            return False

        if self._connection is None:
            raise RuntimeError("Vehicle not associated with a connection")
        try:
            self._requests["latest"] = "HonkAndFlash"
            response = await self._connection.setHonkAndFlash(self.vin, self.position)
            return await self._handle_response(
                response=response,
                topic="honkandflash",
                error_msg="Failed to honk and flash vehicle",
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Failed to honk and flash vehicle - %s", error)
            self._requests["honk_and_flash"] = {
                "status": "Exception",
                "timestamp": datetime.now(UTC),
            }
            raise APIError("Honk and flash action failed") from error

    # Refresh vehicle data (VSR)
    async def set_refresh(self) -> bool:
        """Wake up vehicle and update status data."""
        if self._in_progress("refresh", unknown_offset=-5):
            return False
        if self._connection is None:
            raise RuntimeError("Vehicle not associated with a connection")
        try:
            self._requests["latest"] = "Refresh"
            response = await self._connection.wakeUpVehicle(self.vin)
            if response:
                if response.status == 204:
                    self._requests["state"] = "in_progress"
                    self._requests["refresh"] = {
                        "timestamp": datetime.now(UTC),
                        "status": "in_progress",
                        "id": 0,
                    }
                    status = await self.wait_for_data_refresh()
                elif response.status == 429:
                    status = "Throttled"
                    _LOGGER.debug("Server side throttled. Try again later")
                else:
                    status = f"Error {response.status}"
                    _LOGGER.debug(
                        "Unable to refresh the data. Incorrect response code: %s",
                        response.status,
                    )
                self._requests["state"] = status
                self._requests["refresh"] = {
                    "status": status,
                    "timestamp": datetime.now(UTC),
                }
                return True
            _LOGGER.debug("Unable to refresh the data")
        except Exception as error:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Failed to execute data refresh - %s", error)
            self._requests["refresh"] = {
                "status": "Exception",
                "timestamp": datetime.now(UTC),
            }
            raise APIError("Data refresh failed") from error

    # Vehicle class helpers #
    # Vehicle info
    @property
    def attrs(self) -> dict[str, Any]:
        """Return all attributes.

        :return:
        """
        return self._states

    def has_attr(self, attr: str) -> bool:
        """Return true if attribute exists.

        :param attr:
        :return:
        """
        return is_valid_path(self.attrs, attr)

    def get_attr(self, attr: str) -> Any:
        """Return a specific attribute.

        :param attr:
        :return:
        """
        return find_path(self.attrs, attr)

    async def expired(self, service: str) -> bool:
        """Check if access to service has expired."""
        try:
            now = datetime.now(UTC)
            expiration: datetime | bool = self._services.get(service, {}).get(
                "expiration", False
            )
            if not expiration:
                _LOGGER.debug(
                    "Could not determine end of access for service %s, assuming it is valid",
                    service,
                )
                expiration = datetime.now(UTC) + timedelta(days=1)
            if isinstance(expiration, datetime):
                if expiration.tzinfo is None:
                    expiration = expiration.replace(tzinfo=UTC)
            else:
                expiration = datetime.now(UTC) + timedelta(days=1)
            if now >= expiration:
                _LOGGER.warning("Access to %s has expired!", service)
                self._discovered = False
                return True
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "Exception. Could not determine end of access for service %s, assuming it is valid",
                service,
            )
            return False
        else:
            return False

    def dashboard(self, **config: Any) -> Any:
        """Return dashboard with specified configuration.

        :param config:
        :return:
        """
        from .vw_dashboard import Dashboard  # pylint: disable=import-outside-toplevel

        return Dashboard(self, **config)

    @property
    def vin(self) -> str:
        """Vehicle identification number.

        :return:
        """
        assert self._url is not None, "Vehicle URL (VIN) is not set"
        return self._url

    @property
    def unique_id(self) -> str | None:
        """Return unique id for the vehicle (vin).

        :return:
        """
        return self._url

    @property
    def home_region_url(self) -> str:
        """Return the discovered (or fallback) home region URL for this vehicle.

        Useful for Home Assistant logging and debugging which regional server
        is being used for this vehicle's API calls.
        """
        return self._homeregion

    # Information from vehicle states #
    # Car information
    @property
    def nickname(self) -> str | None:
        """Return nickname of the vehicle.

        :return:
        """
        return self.attrs.get("vehicle", {}).get("nickname", None)

    @property
    def is_nickname_supported(self) -> bool:
        """Return true if naming the vehicle is supported.

        :return:
        """
        return self.attrs.get("vehicle", {}).get("nickname", False) is not False

    @property
    def deactivated(self) -> bool | None:
        """Return true if service is deactivated.

        :return:
        """
        return self.attrs.get("carData", {}).get("deactivated", None)

    @property
    def is_deactivated_supported(self) -> bool:
        """Return true if service deactivation status is supported.

        :return:
        """
        return self.attrs.get("carData", {}).get("deactivated", False) is True

    @property
    def model(self) -> str | None:
        """Return model."""
        return self.attrs.get("vehicle", {}).get("model", None)

    @property
    def is_model_supported(self) -> bool:
        """Return true if model is supported."""
        return self.attrs.get("vehicle", {}).get("modelName", False) is not False

    @property
    def model_year(self) -> int | None:
        """Return model year."""
        return self.attrs.get("vehicle", {}).get("modelYear", None)

    @property
    def is_model_year_supported(self) -> bool:
        """Return true if model year is supported."""
        return self.attrs.get("vehicle", {}).get("modelYear", False) is not False

    @property
    def model_image(self) -> str | None:
        # Not implemented
        """Return vehicle model image."""
        return self.attrs.get("imageUrl")

    @property
    def is_model_image_supported(self) -> bool:
        """Return true if vehicle model image is supported.

        :return:
        """
        # Not implemented
        return self.attrs.get("imageUrl", False) is not False

    # Lights
    @property
    def parking_light(self) -> bool:
        """Return true if parking light is on."""
        lights = find_path(self.attrs, Paths.LIGHTS) or []
        lights_on_count = sum(1 for light in lights if light.get("status") == "on")
        return lights_on_count > 0

    @property
    def parking_light_last_updated(self) -> datetime:
        return find_path(self.attrs, Paths.LIGHTS_TS)

    @property
    def is_parking_light_supported(self) -> bool:
        """Return true if parking light is supported."""
        if not self.attrs.get(Services.VEHICLE_LIGHTS, False):
            return False
        return is_valid_path(self.attrs, Paths.LIGHTS)

    # Readiness
    @property
    def connection_state_is_online(self) -> bool:
        """Return isOnline connection."""
        return find_path(self.attrs, Paths.READINESS_IS_ONLINE)

    @property
    def connection_state_is_online_last_updated(self) -> datetime:
        return datetime.now(UTC)

    @property
    def is_connection_state_is_online_supported(self) -> bool:
        """Return true if connection state isOnline is supported."""
        return is_valid_path(self.attrs, Paths.READINESS_IS_ONLINE)

    @property
    def connection_state_is_active(self) -> bool:
        """Return isActive connection."""
        return find_path(self.attrs, Paths.READINESS_IS_ACTIVE)

    @property
    def connection_state_is_active_last_updated(self) -> datetime:
        return datetime.now(UTC)

    @property
    def is_connection_state_is_active_supported(self) -> bool:
        """Return true if connection state isActive is supported."""
        return is_valid_path(self.attrs, Paths.READINESS_IS_ACTIVE)

    @property
    def connection_state_battery_power_level(self) -> str | None:
        """Return batteryPowerLevel status."""
        battery_power_level = find_path(self.attrs, Paths.READINESS_BATTERY_POWER_LEVEL)
        if battery_power_level:
            return battery_power_level.capitalize()
        return None

    @property
    def connection_state_battery_power_level_last_updated(self) -> datetime:
        return datetime.now(UTC)

    @property
    def is_connection_state_battery_power_level_supported(self) -> bool:
        """Return true if connection state batteryPowerLevel is supported."""
        return is_valid_path(self.attrs, Paths.READINESS_BATTERY_POWER_LEVEL)

    @property
    def connection_state_daily_power_budget_available(self) -> str:
        """Return dailyPowerBudgetAvailable status."""
        daily_power_budget = find_path(
            self.attrs, Paths.READINESS_DAILY_POWER_BUDGET_AVAILABLE
        )
        if daily_power_budget:
            return "Available"
        return "Unavailable"

    @property
    def connection_state_daily_power_budget_available_last_updated(self) -> datetime:
        return datetime.now(UTC)

    @property
    def is_connection_state_daily_power_budget_available_supported(self) -> bool:
        """Return true if connection state dailyPowerBudgetAvailable is supported."""
        return is_valid_path(self.attrs, Paths.READINESS_DAILY_POWER_BUDGET_AVAILABLE)

    @property
    def connection_warning_insufficient_battery_level_warning(self) -> str:
        """Return dailyPowerBudgetAvailable status."""
        return find_path(self.attrs, Paths.READINESS_INSUFFICIENT_BATTERY_LEVEL_WARNING)

    @property
    def connection_warning_insufficient_battery_level_warning_last_updated(
        self,
    ) -> datetime:
        return datetime.now(UTC)

    @property
    def is_connection_warning_insufficient_battery_level_warning_supported(
        self,
    ) -> bool:
        """Return true if connection state dailyPowerBudgetAvailable is supported."""
        return is_valid_path(
            self.attrs, Paths.READINESS_INSUFFICIENT_BATTERY_LEVEL_WARNING
        )

    @property
    def connection_warning_daily_power_budget_warning(self) -> str:
        """Return dailyPowerBudgetAvailable status."""
        return find_path(self.attrs, Paths.READINESS_DAILY_POWER_BUDGET_WARNING)

    @property
    def connection_warning_daily_power_budget_warning_last_updated(self) -> datetime:
        return datetime.now(UTC)

    @property
    def is_connection_warning_daily_power_budget_warning_supported(self) -> bool:
        """Return true if connection state dailyPowerBudgetAvailable is supported."""
        return is_valid_path(self.attrs, Paths.READINESS_DAILY_POWER_BUDGET_WARNING)

    # Connection status
    @property
    def last_connected(self) -> datetime | None:
        """Return when vehicle was last connected to connect servers in local time."""
        # this field is only a dirty hack, because there is no overarching information for the car anymore,
        # only information per service, so we just use the one for fuelStatus.rangeStatus when car is ideling
        # and charing.batteryStatus when electic car is charging
        # Return attribute last updated timestamp.
        if self.is_battery_level_supported and self.charging:
            return self.battery_level_last_updated
        if self.is_distance_supported:
            if isinstance(self.distance_last_updated, str):
                return (
                    datetime.strptime(
                        self.distance_last_updated, "%Y-%m-%dT%H:%M:%S.%fZ"
                    )
                    .replace(microsecond=0)
                    .replace(tzinfo=UTC)
                )
            return self.distance_last_updated
        return None

    @property
    def last_connected_last_updated(self) -> datetime | None:
        """Return attribute last updated timestamp."""
        if self.is_battery_level_supported and self.charging:
            return self.battery_level_last_updated
        if self.is_distance_supported:
            if isinstance(self.distance_last_updated, str):
                return (
                    datetime.strptime(
                        self.distance_last_updated, "%Y-%m-%dT%H:%M:%S.%fZ"
                    )
                    .replace(microsecond=0)
                    .replace(tzinfo=UTC)
                )
            return self.distance_last_updated
        return None

    @property
    def is_last_connected_supported(self) -> bool:
        """Return if when vehicle was last connected to connect servers is supported."""
        return self.is_battery_level_supported or self.is_distance_supported

    # Service information
    @property
    def distance(self) -> int | None:
        """Return vehicle odometer."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return na_status.get("currentMileage")
        return find_path(self.attrs, Paths.MEASUREMENTS_ODO)

    @property
    def distance_last_updated(self) -> datetime | None:
        """Return last updated timestamp."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            ts = na_status.get("currentMileageTimestamp")
            if ts is not None:
                try:
                    return datetime.fromisoformat(ts)
                except ValueError:
                    return None
            return None
        return find_path(self.attrs, Paths.MEASUREMENTS_ODO_TS)

    @property
    def is_distance_supported(self) -> bool:
        """Return true if odometer is supported."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return "currentMileage" in na_status
        return is_valid_path(self.attrs, Paths.MEASUREMENTS_ODO)

    @property
    def service_inspection(self) -> Any:
        """Return time left for service inspection."""
        return find_path(self.attrs, Paths.VEHICLE_HEALTH_INSPECTION_DAYS)

    @property
    def service_inspection_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.VEHICLE_HEALTH_TS)

    @property
    def is_service_inspection_supported(self) -> bool:
        """Return true if days to service inspection is supported."""
        return is_valid_path(self.attrs, Paths.VEHICLE_HEALTH_INSPECTION_DAYS)

    @property
    def service_inspection_distance(self) -> Any:
        """Return distance left for service inspection."""
        return find_path(self.attrs, Paths.VEHICLE_HEALTH_INSPECTION_KM)

    @property
    def service_inspection_distance_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.VEHICLE_HEALTH_TS)

    @property
    def is_service_inspection_distance_supported(self) -> bool:
        """Return true if distance to service inspection is supported."""
        return is_valid_path(self.attrs, Paths.VEHICLE_HEALTH_INSPECTION_KM)

    @property
    def oil_inspection(self) -> Any:
        """Return time left for oil inspection."""
        return find_path(self.attrs, Paths.VEHICLE_HEALTH_OIL_DAYS)

    @property
    def oil_inspection_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.VEHICLE_HEALTH_TS)

    @property
    def is_oil_inspection_supported(self) -> bool:
        """Return true if days to oil inspection is supported."""
        if not self.has_combustion_engine:
            return False
        return is_valid_path(self.attrs, Paths.VEHICLE_HEALTH_OIL_DAYS)

    @property
    def oil_inspection_distance(self) -> int | None:
        """Return distance left for oil inspection."""
        return find_path(self.attrs, Paths.VEHICLE_HEALTH_OIL_KM)

    @property
    def oil_inspection_distance_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.VEHICLE_HEALTH_TS)

    @property
    def is_oil_inspection_distance_supported(self) -> bool:
        """Return true if oil inspection distance is supported."""
        if not self.has_combustion_engine:
            return False
        return is_valid_path(self.attrs, Paths.VEHICLE_HEALTH_OIL_KM)

    @property
    def adblue_level(self) -> int | None:
        """Return adblue level."""
        return find_path(self.attrs, Paths.MEASUREMENTS_RNG_ADBLUE)

    @property
    def adblue_level_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.MEASUREMENTS_RNG_TS)

    @property
    def is_adblue_level_supported(self) -> bool:
        """Return true if adblue level is supported."""
        return is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_ADBLUE)

    # Charger related states for EV and PHEV
    @property
    def charging(self) -> bool:
        """Return charging state.

        For NA vehicles, True when chargingStatus is one of
        ``"CHARGING"``, ``"CHARGING_AC"``, or ``"CHARGING_DC"``.
        """
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("chargingStatus") in (
                "CHARGING",
                "CHARGING_AC",
                "CHARGING_DC",
            )
        return find_path(self.attrs, Paths.CHARGING_STATE) == "charging"

    @property
    def charging_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.CHARGING_TS)

    @property
    def is_charging_supported(self) -> bool:
        """Return true if charging is supported."""
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("chargingStatus") is not None
        return is_valid_path(self.attrs, Paths.CHARGING_STATE)

    @property
    def charging_state(self) -> str:
        """Return charging state."""
        charging_state = find_path(self.attrs, Paths.CHARGING_STATE)
        state_map = {
            "off": "Off",
            "readyForCharging": "Ready",
            "notReadyForCharging": "Not ready",
            "conservation": "Conservation",
            "chargePurposeReachedAndNotConservationCharging": "Charge purpose reached and not conservation charging",
            "chargePurposeReachedAndConservation": "Charge purpose reached and conservation charging",
            "charging": "Charging",
            "error": "Error",
            "unsupported": "Unsupported",
            "discharging": "Discharging",
        }

        return state_map.get(charging_state, "Unknown")

    @property
    def charging_state_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.CHARGING_TS)

    @property
    def is_charging_state_supported(self) -> bool:
        """Return true if charging is supported."""
        return is_valid_path(self.attrs, Paths.CHARGING_STATE)

    @property
    def charging_power(self) -> int | None:
        return find_path(self.attrs, Paths.CHARGING_POWER)

    @property
    def charging_power_last_updated(self) -> datetime:
        return find_path(self.attrs, Paths.CHARGING_TS)

    @property
    def is_charging_power_supported(self) -> bool:
        return is_valid_path(self.attrs, Paths.CHARGING_POWER)

    @property
    def charging_rate(self) -> int | None:
        return find_path(self.attrs, Paths.CHARGING_RATE)

    @property
    def charging_rate_last_updated(self) -> datetime:
        return find_path(self.attrs, Paths.CHARGING_TS)

    @property
    def is_charging_rate_supported(self) -> bool:
        return is_valid_path(self.attrs, Paths.CHARGING_RATE)

    @property
    def charging_status_charge_mode(self) -> str | None:
        """Return current charge mode from charging status."""
        return find_path(self.attrs, Paths.CHARGING_STATUS_CHARGE_MODE)

    @property
    def is_charging_status_charge_mode_supported(self) -> bool:
        """Return true if charging status charge mode is supported."""
        return is_valid_path(self.attrs, Paths.CHARGING_STATUS_CHARGE_MODE)

    @property
    def charger_type(self) -> str:
        ct = find_path(self.attrs, Paths.CHARGING_TYPE)
        return "AC" if ct == "ac" else "DC" if ct == "dc" else "Unknown"

    @property
    def charger_type_last_updated(self) -> datetime:
        return find_path(self.attrs, Paths.CHARGING_TS)

    @property
    def is_charger_type_supported(self) -> bool:
        return is_valid_path(self.attrs, Paths.CHARGING_TYPE)

    @property
    def battery_level(self) -> int | None:
        """Return battery level."""
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("batteryPercentageAvailable")
        return find_path(self.attrs, Paths.BATTERY_SOC)

    @property
    def battery_level_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.BATTERY_TS)

    @property
    def is_battery_level_supported(self) -> bool:
        """Return true if battery level is supported."""
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("batteryPercentageAvailable") is not None
        return is_valid_path(self.attrs, Paths.BATTERY_SOC)

    @property
    def battery_target_charge_level(self) -> int | None:
        """Return target charge level."""
        return find_path(self.attrs, Paths.CHARGING_SET_TARGET_SOC)

    @property
    def battery_target_charge_level_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.CHARGING_SET_TS)

    @property
    def is_battery_target_charge_level_supported(self) -> bool:
        """Return true if target charge level is supported."""
        return is_valid_path(self.attrs, Paths.CHARGING_SET_TARGET_SOC)

    @property
    def hv_battery_min_temperature(self) -> float | None:
        """Return HV battery min temperature."""
        temp_k = find_path(self.attrs, Paths.MEASUREMENTS_BAT_TEMP_MIN_K)
        return float(temp_k) - 273.15 if temp_k is not None else None

    @property
    def hv_battery_min_temperature_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.MEASUREMENTS_BAT_TEMP_TS)

    @property
    def is_hv_battery_min_temperature_supported(self) -> bool:
        """Return true if HV battery min temperature is supported."""
        return is_valid_path(self.attrs, Paths.MEASUREMENTS_BAT_TEMP_MIN_K)

    @property
    def hv_battery_max_temperature(self) -> float | None:
        """Return HV battery max temperature."""
        temp_k = find_path(self.attrs, Paths.MEASUREMENTS_BAT_TEMP_MAX_K)
        return float(temp_k) - 273.15 if temp_k is not None else None

    @property
    def hv_battery_max_temperature_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.MEASUREMENTS_BAT_TEMP_TS)

    @property
    def is_hv_battery_max_temperature_supported(self) -> bool:
        """Return true if HV battery max temperature is supported."""
        return is_valid_path(self.attrs, Paths.MEASUREMENTS_BAT_TEMP_MAX_K)

    @property
    def charge_max_ac_setting(self) -> str | int | None:
        """Return charger max ampere setting."""
        return find_path(self.attrs, Paths.CHARGING_SET_MAX_CHARGE_AC)

    @property
    def charge_max_ac_setting_last_updated(self) -> datetime:
        """Return charger max ampere last updated."""
        return find_path(self.attrs, Paths.CHARGING_SET_TS)

    @property
    def is_charge_max_ac_setting_supported(self) -> bool:
        """Return true if Charger Max Ampere is supported."""
        if is_valid_path(self.attrs, Paths.CHARGING_SET_MAX_CHARGE_AC):
            value = find_path(self.attrs, Paths.CHARGING_SET_MAX_CHARGE_AC)
            return value in ["reduced", "maximum", "invalid"]
        return False

    @property
    def charge_max_ac_ampere(self) -> int | None:
        """Return charger max ampere setting."""
        return find_path(self.attrs, Paths.CHARGING_SET_MAX_CHARGE_AC_A)

    @property
    def charge_max_ac_ampere_last_updated(self) -> datetime:
        """Return charger max ampere last updated."""
        return find_path(self.attrs, Paths.CHARGING_SET_TS)

    @property
    def is_charge_max_ac_ampere_supported(self) -> bool:
        """Return true if Charger Max Ampere is supported."""
        return is_valid_path(self.attrs, Paths.CHARGING_SET_MAX_CHARGE_AC_A)

    @property
    def charge_mode(self) -> dict[str, object] | None:
        """Return charge mode configuration from selectivestatus."""
        return find_path(self.attrs, Paths.CHARGE_MODE)

    @property
    def preferred_charge_mode(self) -> str | None:
        """Return preferred charge mode from selectivestatus."""
        return find_path(self.attrs, Paths.CHARGE_MODE_PREFERRED)

    @property
    def available_charge_modes(self) -> list[str] | None:
        """Return available charge modes from selectivestatus."""
        return find_path(self.attrs, Paths.CHARGE_MODE_AVAILABLE)

    @property
    def is_charge_mode_supported(self) -> bool:
        """Return true if charge mode information is supported."""
        return is_valid_path(self.attrs, Paths.CHARGE_MODE)

    @property
    def charging_cable_locked(self) -> bool:
        """Return plug locked state."""
        response = find_path(self.attrs, Paths.PLUG_LOCK)
        return response == "locked"

    @property
    def charging_cable_locked_last_updated(self) -> datetime:
        """Return plug locked state."""
        return find_path(self.attrs, Paths.PLUG_TS)

    @property
    def is_charging_cable_locked_supported(self) -> bool:
        """Return true if plug locked state is supported."""
        return is_valid_path(self.attrs, Paths.PLUG_LOCK)

    @property
    def charging_cable_connected(self) -> bool:
        """Return plug connected state."""
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("plugStatus") in ("CONNECTED", "CHARGING")
        response = find_path(self.attrs, Paths.PLUG_CONN)
        return response == "connected"

    @property
    def charging_cable_connected_last_updated(self) -> datetime:
        """Return plug connected state last updated."""
        return find_path(self.attrs, Paths.PLUG_TS)

    @property
    def is_charging_cable_connected_supported(self) -> bool:
        """Return true if supported."""
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("plugStatus") is not None
        return is_valid_path(self.attrs, Paths.PLUG_CONN)

    @property
    def charging_time_left(self) -> int | None:
        """Return minutes to charging complete."""
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("remainingChargingTime")
        if is_valid_path(self.attrs, Paths.CHARGING_TIME_LEFT):
            return find_path(self.attrs, Paths.CHARGING_TIME_LEFT)
        return None

    @property
    def charging_time_left_last_updated(self) -> datetime:
        """Return minutes to charging complete last updated."""
        return find_path(self.attrs, Paths.CHARGING_TS)

    @property
    def is_charging_time_left_supported(self) -> bool:
        """Return true if charging time left is supported."""
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("remainingChargingTime") is not None
        # EMEA: intentionally checks CHARGING_STATE (not CHARGING_TIME_LEFT) — preserved from original.
        return is_valid_path(self.attrs, Paths.CHARGING_STATE)

    @property
    def external_power(self) -> bool:
        """Return true if external power is connected."""
        check = find_path(self.attrs, Paths.PLUG_EXT_PWR)
        return check in ["stationConnected", "available", "ready"]

    @property
    def external_power_last_updated(self) -> datetime:
        """Return external power last updated."""
        return find_path(self.attrs, Paths.PLUG_TS)

    @property
    def is_external_power_supported(self) -> bool:
        """External power supported."""
        return is_valid_path(self.attrs, Paths.PLUG_EXT_PWR)

    @property
    def reduced_ac_charging(self) -> bool:
        """Return reduced charging state."""
        return self.charge_max_ac_setting == "reduced"

    @property
    def reduced_ac_charging_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.charge_max_ac_setting_last_updated

    @property
    def is_reduced_ac_charging_supported(self) -> bool:
        """Return true if reduced charging is supported."""
        return self.is_charge_max_ac_setting_supported

    @property
    def auto_release_ac_connector_state(self) -> str:
        """Return auto release ac connector state value."""
        return find_path(self.attrs, Paths.CHARGING_SET_AUTO_UNLOCK_PLUG)

    @property
    def auto_release_ac_connector(self) -> bool:
        """Return auto release ac connector state."""
        return find_path(self.attrs, Paths.CHARGING_SET_AUTO_UNLOCK_PLUG) == "permanent"

    @property
    def auto_release_ac_connector_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.CHARGING_SET_TS)

    @property
    def is_auto_release_ac_connector_supported(self) -> bool:
        """Return true if auto release ac connector is supported."""
        return is_valid_path(self.attrs, Paths.CHARGING_SET_AUTO_UNLOCK_PLUG)

    @property
    def battery_care_mode(self) -> bool:
        """Return battery care mode state."""
        return find_path(self.attrs, Paths.BATTERY_CARE_MODE) == "activated"

    @property
    def battery_care_mode_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(UTC)

    @property
    def is_battery_care_mode_supported(self) -> bool:
        """Return true if battery care mode is supported."""
        return is_valid_path(self.attrs, Paths.BATTERY_CARE_MODE)

    @property
    def optimised_battery_use(self) -> bool:
        """Return optimised battery use state."""
        return find_path(self.attrs, Paths.BATTERY_SUPPORT) == "enabled"

    @property
    def optimised_battery_use_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(UTC)

    @property
    def is_optimised_battery_use_supported(self) -> bool:
        """Return true if optimised battery use is supported."""
        return is_valid_path(self.attrs, Paths.BATTERY_SUPPORT)

    @property
    def energy_flow(self) -> bool:
        # noqa: T000 — Pre-existing EMEA technical debt: energy_flow parsing untouched since original codebase
        """Return true if energy is flowing through charging port."""
        check = (
            self.attrs.get("charger", {})
            .get("status", {})
            .get("chargingStatusData", {})
            .get("energyFlow", {})
            .get("content", "off")
        )
        return check == "on"

    @property
    def energy_flow_last_updated(self) -> datetime:
        # noqa: T000 — Pre-existing EMEA technical debt: energy_flow parsing untouched since original codebase
        """Return energy flow last updated."""
        return (
            self.attrs.get("charger", {})
            .get("status", {})
            .get("chargingStatusData", {})
            .get("energyFlow", {})
            .get("timestamp")
        )

    @property
    def is_energy_flow_supported(self) -> bool:
        # noqa: T000 — Pre-existing EMEA technical debt: energy_flow parsing untouched since original codebase
        """Energy flow supported."""
        return (
            self.attrs.get("charger", {})
            .get("status", {})
            .get("chargingStatusData", {})
            .get("energyFlow", False)
        )

    # Vehicle location states
    @property
    def position(self) -> dict[str, str | float | None]:
        """Return the vehicle's last known GPS position.

        For EMEA vehicles, reads from the ``parkingposition`` or
        ``storedVehicleDataResponse`` state populated by the selectivestatus API.

        For NA vehicles (country='US'/'CA'), reads from the ``na_location``
        state populated by the RVS (Remote Vehicle Status) location endpoint.

        Returns:
            Dict with keys ``lat`` (float or None), ``lng`` (float or None),
            and ``timestamp`` (str or None).

        Example:
            >>> pos = vehicle.position
            >>> print(f"Lat: {pos['lat']}, Lng: {pos['lng']}")
        """
        # NA region: read from na_location state populated by RVS endpoint
        if self._connection is not None and self._connection.is_na:
            try:
                return self._na_position()
            except ValueError as exc:
                _LOGGER.warning("NA position data malformed for %s: %s", self.vin, exc)
                return {"lat": None, "lng": None, "timestamp": None}
        # EMEA: existing logic unchanged
        output: dict[str, str | float | None]
        try:
            if self.vehicle_moving:
                output = {"lat": None, "lng": None, "timestamp": None}
            else:
                lat = float(find_path(self.attrs, Paths.PARKING_LAT))
                lng = float(find_path(self.attrs, Paths.PARKING_LON))
                parking_time = find_path(self.attrs, Paths.PARKING_TS)
                output = {"lat": lat, "lng": lng, "timestamp": parking_time}
        except (KeyError, TypeError, ValueError):
            output = {"lat": None, "lng": None, "timestamp": None}
        return output

    def _na_position(self) -> dict[str, str | float | None]:
        """Return position for NA region from na_location state.

        Returns:
            Dict with lat, lng, timestamp keys.

        Raises:
            ValueError: If na_location data is present but has unexpected structure.
        """
        na_loc = self._states.get("na_location")
        if na_loc is None:
            return {"lat": None, "lng": None, "timestamp": None}
        try:
            loc = na_loc["location"]
            return {
                "lat": float(loc["latitude"]),
                "lng": float(loc["longitude"]),
                "timestamp": na_loc.get("eventTimeStamp"),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"NA location data has unexpected structure: {exc!r}. "
                f"Raw na_location: {na_loc!r}"
            ) from exc

    def _na_door_locked(self) -> bool:
        """Return door locked state for NA region from na_status state.

        Returns:
            True if lockStatus == 'LOCKED', False otherwise.

        Raises:
            ValueError: If na_status is present but lockStatus field is missing.
        """
        na_status = self._states.get("na_status")
        if na_status is None:
            return False
        try:
            lock_status = na_status["lockStatus"]
        except KeyError as exc:
            raise ValueError(
                f"NA status data missing 'lockStatus' field. "
                f"Raw na_status keys: {list(na_status.keys())!r}"
            ) from exc
        return lock_status == "LOCKED"

    @property
    def position_last_updated(self) -> datetime | str:
        """Return position last updated."""
        return self.attrs.get("parkingposition", {}).get(
            "carCapturedTimestamp", "Unknown"
        )

    @property
    def is_position_supported(self) -> bool:
        """Return true if position is available."""
        # NA region: supported if na_location data is present
        if self._connection is not None and self._connection.is_na:
            return self._states.get("na_location") is not None
        # EMEA: existing logic unchanged
        return is_valid_path(self.attrs, Paths.PARKING_TS) or self.attrs.get(
            "isMoving", False
        )

    @property
    def vehicle_moving(self) -> bool:
        """Return true if vehicle is moving."""
        na_location = self._states.get("na_location")
        if na_location is not None:
            return not na_location.get("parked", True)
        return self.attrs.get("isMoving", False)

    @property
    def vehicle_moving_last_updated(self) -> datetime | str | None:
        """Return attribute last updated timestamp."""
        return self.position_last_updated

    @property
    def is_vehicle_moving_supported(self) -> bool:
        """Return true if vehicle supports position."""
        na_location = self._states.get("na_location")
        if na_location is not None:
            return "parked" in na_location
        return self.is_position_supported

    @property
    def parking_time(self) -> datetime | None:
        """Return timestamp of last parking time."""
        if is_valid_path(self.attrs, Paths.PARKING_TS):
            return find_path(self.attrs, Paths.PARKING_TS)
        return None

    @property
    def parking_time_last_updated(self) -> datetime | str | None:
        """Return attribute last updated timestamp."""
        return self.position_last_updated

    @property
    def is_parking_time_supported(self) -> bool:
        """Return true if vehicle parking timestamp is supported."""
        return self.is_position_supported

    # Vehicle fuel level and range
    @property
    def electric_range(self) -> int | None:
        """Return electric range."""
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("electricRange")
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_ELECTRIC):
            return find_path(self.attrs, Paths.MEASUREMENTS_RNG_ELECTRIC)
        return find_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_RNG)

    @property
    def electric_range_last_updated(self) -> datetime:
        """Return electric range last updated."""
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_TS):
            return find_path(self.attrs, Paths.MEASUREMENTS_RNG_TS)
        return find_path(self.attrs, Paths.FUEL_STATUS_TS)

    @property
    def is_electric_range_supported(self) -> bool:
        """Return true if electric range is supported."""
        na_ev = self._states.get("na_ev")
        if na_ev is not None:
            return na_ev.get("electricRange") is not None
        return is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_ELECTRIC) or (
            self.is_car_type_electric
            and is_valid_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_RNG)
        )

    @property
    def combustion_range(self) -> int | None:
        """Return combustion engine range."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return (na_status.get("powerStatus") or {}).get("cruiseRange")
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_CNG):
            return find_path(self.attrs, Paths.MEASUREMENTS_RNG_TOTAL)
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_DIESEL):
            return find_path(self.attrs, Paths.MEASUREMENTS_RNG_DIESEL)
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_GASOLINE):
            return find_path(self.attrs, Paths.MEASUREMENTS_RNG_GASOLINE)
        return None

    @property
    def combustion_range_last_updated(self) -> datetime | None:
        """Return combustion engine range last updated."""
        return find_path(self.attrs, Paths.MEASUREMENTS_RNG_TS)

    @property
    def is_combustion_range_supported(self) -> bool:
        """Return true if combustion range is supported, i.e. false for EVs."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return (na_status.get("powerStatus") or {}).get("cruiseRange") is not None
        return (
            is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_DIESEL)
            or is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_GASOLINE)
            or is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_CNG)
        )

    @property
    def cruise_range_units(self) -> str | None:
        """Return cruise range units indicator (NA only)."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return (na_status.get("powerStatus") or {}).get("cruiseRangeUnits")
        return None

    @property
    def cruise_range_units_last_updated(self) -> datetime | None:
        """Return cruise range units last updated (no timestamp available)."""
        return None

    @property
    def is_cruise_range_units_supported(self) -> bool:
        """Return true if cruise range units is supported."""
        na_status = self._states.get("na_status")
        return (
            na_status is not None
            and (na_status.get("powerStatus") or {}).get("cruiseRangeUnits") is not None
        )

    @property
    def fuel_range(self) -> int | None:
        """Return fuel engine range."""
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_DIESEL):
            return find_path(self.attrs, Paths.MEASUREMENTS_RNG_DIESEL)
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_GASOLINE):
            return find_path(self.attrs, Paths.MEASUREMENTS_RNG_GASOLINE)
        return None

    @property
    def fuel_range_last_updated(self) -> datetime | None:
        """Return fuel engine range last updated."""
        return find_path(self.attrs, Paths.MEASUREMENTS_RNG_TS)

    @property
    def is_fuel_range_supported(self) -> bool:
        """Return true if fuel range is supported, i.e. false for EVs."""
        return is_valid_path(
            self.attrs, Paths.MEASUREMENTS_RNG_DIESEL
        ) or is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_GASOLINE)

    @property
    def gas_range(self) -> int | None:
        """Return gas engine range."""
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_CNG):
            return find_path(self.attrs, Paths.MEASUREMENTS_RNG_CNG)
        return None

    @property
    def gas_range_last_updated(self) -> datetime | None:
        """Return gas engine range last updated."""
        return find_path(self.attrs, Paths.MEASUREMENTS_RNG_TS)

    @property
    def is_gas_range_supported(self) -> bool:
        """Return true if gas range is supported, i.e. false for EVs."""
        return is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_CNG)

    @property
    def combined_range(self) -> int | None:
        """Return combined range."""
        return find_path(self.attrs, Paths.MEASUREMENTS_RNG_TOTAL)

    @property
    def combined_range_last_updated(self) -> datetime | None:
        """Return combined range last updated."""
        return find_path(self.attrs, Paths.MEASUREMENTS_RNG_TS)

    @property
    def is_combined_range_supported(self) -> bool:
        """Return true if combined range is supported."""
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_RNG_TOTAL):
            return (
                self.is_electric_range_supported and self.is_combustion_range_supported
            )
        return False

    @property
    def battery_cruising_range(self) -> int | None:
        """Return battery cruising range."""
        return find_path(self.attrs, Paths.BATTERY_RANGE_E)

    @property
    def battery_cruising_range_last_updated(self) -> datetime | None:
        """Return battery cruising range last updated."""
        return find_path(self.attrs, Paths.BATTERY_TS)

    @property
    def is_battery_cruising_range_supported(self) -> bool:
        """Return true if battery cruising range is supported."""
        return is_valid_path(self.attrs, Paths.BATTERY_RANGE_E)

    @property
    def fuel_level(self) -> int | None:
        """Return fuel level."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return (na_status.get("powerStatus") or {}).get("fuelPercentRemaining")
        fuel_level_pct = None
        if (
            is_valid_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_LVL)
            and not self.is_primary_drive_gas()
        ):
            fuel_level_pct = find_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_LVL)

        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_LVL):
            fuel_level_pct = find_path(self.attrs, Paths.MEASUREMENTS_FUEL_LVL)
        return fuel_level_pct

    @property
    def fuel_level_last_updated(self) -> datetime | str | None:
        """Return fuel level last updated."""
        fuel_level_lastupdated = ""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_TS):
            fuel_level_lastupdated = find_path(self.attrs, Paths.FUEL_STATUS_TS)

        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_TS):
            fuel_level_lastupdated = find_path(self.attrs, Paths.MEASUREMENTS_FUEL_TS)
        return fuel_level_lastupdated

    @property
    def is_fuel_level_supported(self) -> bool:
        """Return true if fuel level reporting is supported."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return (na_status.get("powerStatus") or {}).get(
                "fuelPercentRemaining"
            ) is not None
        return (
            is_valid_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_LVL)
            and not self.is_primary_drive_gas()
        ) or is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_LVL)

    @property
    def gas_level(self) -> int | None:
        """Return gas level."""
        gas_level_pct = None
        if (
            is_valid_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_LVL)
            and self.is_primary_drive_gas()
        ):
            gas_level_pct = find_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_LVL)

        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_GAS_LVL):
            gas_level_pct = find_path(self.attrs, Paths.MEASUREMENTS_FUEL_GAS_LVL)
        return gas_level_pct

    @property
    def gas_level_last_updated(self) -> datetime | str | None:
        """Return gas level last updated."""
        gas_level_lastupdated = ""
        if (
            is_valid_path(self.attrs, Paths.FUEL_STATUS_TS)
            and self.is_primary_drive_gas()
        ):
            gas_level_lastupdated = find_path(self.attrs, Paths.FUEL_STATUS_TS)

        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_TS):
            gas_level_lastupdated = find_path(self.attrs, Paths.MEASUREMENTS_FUEL_TS)
        return gas_level_lastupdated

    @property
    def is_gas_level_supported(self) -> bool:
        """Return true if gas level reporting is supported."""
        return (
            is_valid_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_LVL)
            and self.is_primary_drive_gas()
        ) or is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_GAS_LVL)

    @property
    def car_type(self) -> str:
        """Return car type."""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE):
            return find_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE).capitalize()
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE):
            return find_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE).capitalize()
        return "Unknown"

    @property
    def car_type_last_updated(self) -> datetime | None:
        """Return car type last updated."""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_TS):
            return find_path(self.attrs, Paths.FUEL_STATUS_TS)
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_TS):
            return find_path(self.attrs, Paths.MEASUREMENTS_FUEL_TS)
        return None

    @property
    def is_car_type_supported(self) -> bool:
        """Return true if car type is supported."""
        return is_valid_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE) or is_valid_path(
            self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE
        )

    # Climatisation settings
    @property
    def climatisation_target_temperature(self) -> float | None:
        """Return the target temperature from climater."""
        na_climate = self._states.get("na_climate")
        if na_climate is not None:
            temp = na_climate.get("targetTemperature_C")
            return float(temp) if temp is not None else None
        temp = find_path(self.attrs, Paths.CLIMATISATION_TARGET_TEMP)
        return float(temp) if temp is not None else None

    @property
    def climatisation_target_temperature_last_updated(self) -> datetime:
        """Return the target temperature from climater last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_SETTINGS_TS)

    @property
    def is_climatisation_target_temperature_supported(self) -> bool:
        """Return true if climatisation target temperature is supported."""
        na_climate = self._states.get("na_climate")
        if na_climate is not None:
            return na_climate.get("targetTemperature_C") is not None
        return is_valid_path(self.attrs, Paths.CLIMATISATION_TARGET_TEMP)

    @property
    def climatisation_duration(self) -> int | None:
        """Return climatisation duration in seconds (NA only)."""
        na_climate = self._states.get("na_climate")
        if na_climate is not None:
            return na_climate.get("climatisationDuration")
        return None

    @property
    def climatisation_duration_last_updated(self) -> datetime | None:
        """Return climatisation duration last updated."""
        return None

    @property
    def is_climatisation_duration_supported(self) -> bool:
        """Return true if climatisation duration is supported."""
        na_climate = self._states.get("na_climate")
        return na_climate is not None and "climatisationDuration" in na_climate

    @property
    def climatisation_without_external_power(self) -> bool | None:
        """Return state of climatisation from battery power."""
        return find_path(self.attrs, Paths.CLIMATISATION_WITHOUT_EXT_PWR)

    @property
    def climatisation_without_external_power_last_updated(self) -> datetime:
        """Return state of climatisation from battery power last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_SETTINGS_TS)

    @property
    def is_climatisation_without_external_power_supported(self) -> bool:
        """Return true if climatisation on battery power is supported."""
        return is_valid_path(self.attrs, Paths.CLIMATISATION_WITHOUT_EXT_PWR)

    @property
    def auxiliary_air_conditioning(self) -> bool | None:
        """Return state of auxiliary air conditioning."""
        return find_path(self.attrs, Paths.CLIMATISATION_AT_UNLOCK)

    @property
    def auxiliary_air_conditioning_last_updated(self) -> datetime:
        """Return state of auxiliary air conditioning last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_SETTINGS_TS)

    @property
    def is_auxiliary_air_conditioning_supported(self) -> bool:
        """Return true if auxiliary air conditioning is supported."""
        return is_valid_path(self.attrs, Paths.CLIMATISATION_AT_UNLOCK)

    @property
    def automatic_window_heating(self) -> bool | None:
        """Return state of automatic window heating."""
        return find_path(self.attrs, Paths.CLIMATISATION_WINDOW_HEATING)

    @property
    def automatic_window_heating_last_updated(self) -> datetime:
        """Return state of automatic window heating last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_SETTINGS_TS)

    @property
    def is_automatic_window_heating_supported(self) -> bool:
        """Return true if automatic window heating is supported."""
        return is_valid_path(self.attrs, Paths.CLIMATISATION_WINDOW_HEATING)

    @property
    def zone_front_left(self) -> bool | None:
        """Return state of zone front left."""
        return find_path(self.attrs, Paths.CLIMATISATION_ZONE_FRONT_LEFT)

    @property
    def zone_front_left_last_updated(self) -> datetime:
        """Return state of zone front left last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_SETTINGS_TS)

    @property
    def is_zone_front_left_supported(self) -> bool:
        """Return true if zone front left is supported."""
        return is_valid_path(self.attrs, Paths.CLIMATISATION_ZONE_FRONT_LEFT)

    @property
    def zone_front_right(self) -> bool | None:
        """Return state of zone front right."""
        return find_path(self.attrs, Paths.CLIMATISATION_ZONE_FRONT_RIGHT)

    @property
    def zone_front_right_last_updated(self) -> datetime:
        """Return state of zone front right last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_SETTINGS_TS)

    @property
    def is_zone_front_right_supported(self) -> bool:
        """Return true if zone front right is supported."""
        return is_valid_path(self.attrs, Paths.CLIMATISATION_ZONE_FRONT_RIGHT)

    # Climatisation, electric
    @property
    def electric_climatisation(self) -> bool:
        """Return status of climatisation."""
        status = find_path(self.attrs, Paths.CLIMATISATION_STATE)
        return status in ["ventilation", "heating", "cooling", "on"]

    @property
    def electric_climatisation_last_updated(self) -> datetime:
        """Return status of climatisation last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_STATUS_TS)

    @property
    def is_electric_climatisation_supported(self) -> bool:
        """Return true if vehicle has climater."""
        return (
            self.is_climatisation_supported
            and self.is_climatisation_target_temperature_supported
            and self.is_climatisation_without_external_power_supported
        ) or (
            self.is_car_type_electric
            and self.is_climatisation_supported
            and self.is_climatisation_target_temperature_supported
        )

    @property
    def electric_remaining_climatisation_time(self) -> int:
        """Return remaining climatisation time for electric climatisation."""
        return find_path(self.attrs, Paths.CLIMATISATION_REM_TIME)

    @property
    def electric_remaining_climatisation_time_last_updated(self) -> str | None:
        """Return status of electric climatisation remaining climatisation time last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_STATUS_TS)

    @property
    def is_electric_remaining_climatisation_time_supported(self) -> bool:
        """Return true if electric climatisation remaining climatisation time is supported."""
        return is_valid_path(self.attrs, Paths.CLIMATISATION_REM_TIME)

    # Active ventilation (read-only)
    @property
    def active_ventilation(self) -> bool:
        """Return status of active ventilation."""
        state = find_path(self.attrs, Paths.ACTIVE_VENTILATION_STATE)

        if state is None:
            return False
        return state not in ["off", "invalid"]

    @property
    def active_ventilation_last_updated(self) -> datetime:
        """Return timestamp of last active ventilation update."""
        return find_path(self.attrs, Paths.ACTIVE_VENTILATION_TS)

    @property
    def is_active_ventilation_supported(self) -> bool:
        """Return true if active ventilation is available in the vehicle."""
        return is_valid_path(self.attrs, Paths.ACTIVE_VENTILATION_STATE)

    @property
    def active_ventilation_remaining_time(self) -> int:
        """Return remaining time for active ventilation."""
        return find_path(self.attrs, Paths.ACTIVE_VENTILATION_REM_TIME)

    @property
    def active_ventilation_remaining_time_last_updated(self) -> datetime:
        """Return timestamp of last active ventilation remaining time update."""
        return find_path(self.attrs, Paths.ACTIVE_VENTILATION_TS)

    @property
    def is_active_ventilation_remaining_time_supported(self) -> bool:
        """Return true if active ventilation remaining time is supported."""
        return is_valid_path(self.attrs, Paths.ACTIVE_VENTILATION_REM_TIME)

    @property
    def active_ventilation_state(self) -> bool:
        """Return state of active ventilation."""
        state = find_path(self.attrs, Paths.ACTIVE_VENTILATION_STATE)

        return state

    @property
    def active_ventilation_state_last_updated(self) -> datetime:
        """Return timestamp of last active ventilation update."""
        return find_path(self.attrs, Paths.ACTIVE_VENTILATION_TS)

    @property
    def is_active_ventilation_state_supported(self) -> bool:
        """Return true if active ventilation is available in the vehicle."""
        return is_valid_path(self.attrs, Paths.ACTIVE_VENTILATION_STATE)

    @property
    def auxiliary_climatisation(self) -> bool:
        """Return status of auxiliary climatisation."""
        climatisation_state = None
        if is_valid_path(self.attrs, Paths.CLIMATISATION_AUX_STATE):
            climatisation_state = find_path(self.attrs, Paths.CLIMATISATION_AUX_STATE)
        if is_valid_path(self.attrs, Paths.CLIMATISATION_STATE):
            climatisation_state = find_path(self.attrs, Paths.CLIMATISATION_STATE)
        if climatisation_state in ["heating", "heatingAuxiliary", "on"]:
            return True
        return False

    @property
    def auxiliary_climatisation_last_updated(self) -> datetime | str | None:
        """Return status of auxiliary climatisation last updated."""
        if is_valid_path(self.attrs, Paths.CLIMATISATION_AUX_TS):
            return find_path(self.attrs, Paths.CLIMATISATION_AUX_TS)
        if is_valid_path(self.attrs, Paths.CLIMATISATION_STATUS_TS):
            return find_path(self.attrs, Paths.CLIMATISATION_STATUS_TS)
        return None

    @property
    def is_auxiliary_climatisation_supported(self) -> bool:
        """Return true if vehicle has auxiliary climatisation."""
        if is_valid_path(
            self.attrs,
            Paths.CLIMATISATION_AUX_STATE,
        ):
            return True
        if is_valid_path(self.attrs, Paths.USER_CAPABILITIES):
            capabilities = find_path(self.attrs, Paths.USER_CAPABILITIES)
            for capability in capabilities:
                if capability.get("id", None) == "hybridCarAuxiliaryHeating":
                    if 1007 in capability.get("status", []):
                        return False
                    return True
        return False

    @property
    def climatisation_state(self) -> str | None:
        """Return state of climatisation."""
        na_climate = self._states.get("na_climate")
        if na_climate is not None:
            return na_climate.get("climatisationStatus")
        climatisation_state = None
        if is_valid_path(self.attrs, Paths.CLIMATISATION_AUX_STATE):
            climatisation_state = find_path(self.attrs, Paths.CLIMATISATION_AUX_STATE)
        if is_valid_path(self.attrs, Paths.CLIMATISATION_STATE):
            climatisation_state = find_path(self.attrs, Paths.CLIMATISATION_STATE)
        return climatisation_state

    @property
    def climatisation_state_last_updated(self) -> datetime | str | None:
        """Return state of climatisation last updated."""
        if is_valid_path(self.attrs, Paths.CLIMATISATION_AUX_TS):
            return find_path(self.attrs, Paths.CLIMATISATION_AUX_TS)
        if is_valid_path(self.attrs, Paths.CLIMATISATION_STATUS_TS):
            return find_path(self.attrs, Paths.CLIMATISATION_STATUS_TS)
        return None

    @property
    def is_climatisation_state_supported(self) -> bool:
        """Return true if vehicle has climatisation state."""
        na_climate = self._states.get("na_climate")
        if na_climate is not None:
            return na_climate.get("climatisationStatus") is not None
        return (
            self.is_climatisation_supported
            or self.is_auxiliary_climatisation_supported
            or self.is_electric_climatisation_supported
        )

    @property
    def auxiliary_duration(self) -> int:
        """Return heating duration for auxiliary heater."""
        return find_path(self.attrs, Paths.CLIMATISATION_AUX_DURATION)

    @property
    def auxiliary_duration_last_updated(self) -> str | None:
        """Return status of auxiliary heater last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_SETTINGS_TS)

    @property
    def is_auxiliary_duration_supported(self) -> bool:
        """Return true if auxiliary heater is supported."""
        return is_valid_path(self.attrs, Paths.CLIMATISATION_AUX_DURATION)

    @property
    def auxiliary_remaining_climatisation_time(self) -> int:
        """Return remaining climatisation time for auxiliary heater."""
        return find_path(self.attrs, Paths.CLIMATISATION_AUX_REM_TIME)

    @property
    def auxiliary_remaining_climatisation_time_last_updated(self) -> str | None:
        """Return status of auxiliary heater remaining climatisation time last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_AUX_TS)

    @property
    def is_auxiliary_remaining_climatisation_time_supported(self) -> bool:
        """Return true if auxiliary heater remaining climatisation time is supported."""
        return is_valid_path(self.attrs, Paths.CLIMATISATION_AUX_REM_TIME)

    @property
    def is_climatisation_supported(self) -> bool:
        """Return true if climatisation has State."""
        return is_valid_path(self.attrs, Paths.CLIMATISATION_STATE)

    @property
    def is_climatisation_supported_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.CLIMATISATION_STATUS_TS)

    @property
    def window_heater_front(self) -> bool:
        """Return status of front window heater."""
        window_heating_status = (
            find_path(self.attrs, Paths.CLIMATISATION_WINDOW_HEATING_STATUS) or []
        )
        for window_heating_state in window_heating_status:
            if window_heating_state.get("windowLocation") == "front":
                return window_heating_state.get("windowHeatingState") == "on"
        return False

    @property
    def window_heater_front_last_updated(self) -> datetime:
        """Return front window heater last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_WINDOW_HEATING_TS)

    @property
    def is_window_heater_front_supported(self) -> bool:
        """Return true if vehicle has heater."""
        # Check that path exists
        if not is_valid_path(self.attrs, Paths.CLIMATISATION_WINDOW_HEATING_STATUS):
            return False

        # Find the front window in the list
        window_status_list = find_path(
            self.attrs, Paths.CLIMATISATION_WINDOW_HEATING_STATUS
        )
        if not isinstance(window_status_list, list):
            return False

        for window_status in window_status_list:
            if (
                window_status.get("windowLocation") == "front"
                and "windowHeatingState" in window_status
            ):
                return True

        return False

    @property
    def window_heater_back(self) -> bool:
        """Return status of rear window heater."""
        window_heating_status = (
            find_path(self.attrs, Paths.CLIMATISATION_WINDOW_HEATING_STATUS) or []
        )
        for window_heating_state in window_heating_status:
            if window_heating_state.get("windowLocation") == "rear":
                return window_heating_state.get("windowHeatingState") == "on"
        return False

    @property
    def window_heater_back_last_updated(self) -> datetime:
        """Return front window heater last updated."""
        return find_path(self.attrs, Paths.CLIMATISATION_WINDOW_HEATING_TS)

    @property
    def is_window_heater_back_supported(self) -> bool:
        """Return true if vehicle has heater."""
        # Check that path exists
        if not is_valid_path(self.attrs, Paths.CLIMATISATION_WINDOW_HEATING_STATUS):
            return False

        # Find the rear window in the list
        window_status_list = find_path(
            self.attrs, Paths.CLIMATISATION_WINDOW_HEATING_STATUS
        )
        if not isinstance(window_status_list, list):
            return False

        for window_status in window_status_list:
            if (
                window_status.get("windowLocation") == "rear"
                and "windowHeatingState" in window_status
            ):
                return True

        return False

    @property
    def window_heater(self) -> bool:
        """Return status of window heater."""
        if self.is_window_heater_front_supported:
            return self.window_heater_front
        return self.window_heater_back

    @property
    def window_heater_last_updated(self) -> datetime:
        """Return front window heater last updated."""
        return self.window_heater_front_last_updated

    @property
    def is_window_heater_supported(self) -> bool:
        """Return true if vehicle has heater."""
        # ID models detection
        if (
            self._services.get(Services.PARAMETERS, {}).get(
                "supportsStartWindowHeating", "false"
            )
            == "true"
        ):
            return True
        # "Legacy" models detection
        parameters = self._services.get(Services.CLIMATISATION, {}).get(
            "parameters", None
        )
        if parameters:
            for parameter in parameters:
                if (
                    parameter["key"] == "supportsStartWindowHeating"
                    and parameter["value"] == "true"
                ):
                    return True
        return False

    # Windows
    @property
    def windows_closed(self) -> bool | None:
        """Return true if all supported windows are closed.

        :return:
        """
        return (
            (
                not self.is_window_closed_left_front_supported
                or self.window_closed_left_front
            )
            and (
                not self.is_window_closed_left_back_supported
                or self.window_closed_left_back
            )
            and (
                not self.is_window_closed_right_front_supported
                or self.window_closed_right_front
            )
            and (
                not self.is_window_closed_right_back_supported
                or self.window_closed_right_back
            )
        )

    @property
    def windows_closed_last_updated(self) -> datetime:
        """Return timestamp for windows state last updated."""
        return self.window_closed_left_front_last_updated

    @property
    def is_windows_closed_supported(self) -> bool:
        """Return true if window state is supported."""
        return (
            self.is_window_closed_left_front_supported
            or self.is_window_closed_left_back_supported
            or self.is_window_closed_right_front_supported
            or self.is_window_closed_right_back_supported
        )

    # HELPERS
    def _get_window_state(self, window_name: str) -> bool | None:
        windows = find_path(self.attrs, Paths.ACCESS_WINDOWS) or []
        for window in windows:
            if window.get("name") == window_name:
                status = window.get("status") or []
                if not any(valid in status for valid in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in status
        return False

    def _get_door_state(self, door_name: str) -> bool | None:
        # NA region: read individual door state from na_status exteriorStatus.doorStatus
        if self._connection is not None and self._connection.is_na:
            na_status = self._states.get("na_status")
            if na_status is None:
                return None
            na_key = _NA_DOOR_NAMES.get(door_name, door_name)
            door_status = (na_status.get("exteriorStatus") or {}).get(
                "doorStatus"
            ) or {}
            value = door_status.get(na_key)
            if value is None or value == "NOTAVAILABLE":
                return None
            return value == "CLOSED"
        # EMEA: existing logic unchanged
        doors = find_path(self.attrs, Paths.ACCESS_DOORS) or []
        for door in doors:
            if door.get("name") == door_name:
                status = door.get("status") or []
                if not any(valid in status for valid in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in status
        return False

    def _is_window_supported(self, window_name: str) -> bool:
        """Check if a window is supported by name."""
        if not is_valid_path(self.attrs, Paths.ACCESS_WINDOWS):
            return False
        windows = find_path(self.attrs, Paths.ACCESS_WINDOWS) or []
        return any(
            w.get("name") == window_name
            and "unsupported" not in (w.get("status") or [])
            for w in windows
        )

    def _is_door_supported(self, door_name: str) -> bool:
        """Check if a door is supported by name."""
        # NA region: supported when the door has a non-NOTAVAILABLE value in doorStatus
        if self._connection is not None and self._connection.is_na:
            na_status = self._states.get("na_status")
            if na_status is None:
                return False
            na_key = _NA_DOOR_NAMES.get(door_name, door_name)
            door_status = (na_status.get("exteriorStatus") or {}).get(
                "doorStatus"
            ) or {}
            value = door_status.get(na_key)
            return value is not None and value != "NOTAVAILABLE"
        # EMEA: existing logic unchanged
        if not is_valid_path(self.attrs, Paths.ACCESS_DOORS):
            return False
        doors = find_path(self.attrs, Paths.ACCESS_DOORS) or []
        return any(
            d.get("name") == door_name and "unsupported" not in (d.get("status") or [])
            for d in doors
        )

    def _get_na_door_lock_state(self, door_name: str) -> bool | None:
        """Return per-door lock state for NA vehicles; None for EMEA or missing data.

        Args:
            door_name: NA doorLockStatus key (e.g. "frontLeft", "rearRight").

        Returns:
            True if LOCKED, False if UNLOCKED, None if data unavailable or not NA.
        """
        if self._connection is None or not self._connection.is_na:
            return None
        na_status = self._states.get("na_status")
        if na_status is None:
            return None
        lock_status = (na_status.get("exteriorStatus") or {}).get(
            "doorLockStatus"
        ) or {}
        value = lock_status.get(door_name)
        if value is None:
            return None
        return value == "LOCKED"

    def _is_na_door_lock_supported(self, door_name: str) -> bool:
        """Return True when per-door lock data is available for NA vehicles.

        Args:
            door_name: NA doorLockStatus key (e.g. "frontLeft", "rearRight").

        Returns:
            True if data present, False otherwise (always False for EMEA).
        """
        if self._connection is None or not self._connection.is_na:
            return False
        na_status = self._states.get("na_status")
        if na_status is None:
            return False
        lock_status = (na_status.get("exteriorStatus") or {}).get(
            "doorLockStatus"
        ) or {}
        return door_name in lock_status

    def _get_trip_value(self, trip_type: str, key: str, default: Any = None) -> Any:
        """Generic getter for trip statistics."""
        entry = self.attrs.get(trip_type, {}) or {}
        # Try direct key first (no logging)
        if key in entry and entry[key] is not None:
            return entry[key]
        # Try nested path only if it exists
        nested_path = f"{trip_type}.{key}"
        if is_valid_path(self.attrs, nested_path):
            value = find_path(self.attrs, nested_path)
            if value is not None:
                return value
        return default

    def _is_trip_supported(self, trip_type: str, key: str) -> bool:
        """Generic support checker for trip statistics."""
        # Prefer direct entry type check to avoid logging
        entry = self.attrs.get(trip_type, {}) or {}
        if key in entry and isinstance(entry[key], (float, int)):
            return True
        # Otherwise, only check nested path existence without reading the value
        return is_valid_path(self.attrs, f"{trip_type}.{key}")

    @property
    def safety_status(self) -> bool | None:
        return find_path(self.attrs, Paths.ACCESS_OVERALL_STATUS) == "unsafe"

    @property
    def safety_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_safety_status_supported(self) -> bool:
        return is_valid_path(self.attrs, Paths.ACCESS_OVERALL_STATUS)

    @property
    def window_closed_left_front(self) -> bool | None:
        return self._get_window_state("frontLeft")

    @property
    def window_closed_left_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_window_closed_left_front_supported(self) -> bool:
        return self._is_window_supported("frontLeft")

    @property
    def window_closed_right_front(self) -> bool | None:
        return self._get_window_state("frontRight")

    @property
    def window_closed_right_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_window_closed_right_front_supported(self) -> bool:
        return self._is_window_supported("frontRight")

    @property
    def window_closed_left_back(self) -> bool | None:
        return self._get_window_state("rearLeft")

    @property
    def window_closed_left_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_window_closed_left_back_supported(self) -> bool:
        return self._is_window_supported("rearLeft")

    @property
    def window_closed_right_back(self) -> bool | None:
        return self._get_window_state("rearRight")

    @property
    def window_closed_right_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_window_closed_right_back_supported(self) -> bool:
        return self._is_window_supported("rearRight")

    @property
    def sunroof_closed(self) -> bool | None:
        return self._get_window_state("sunRoof")

    @property
    def sunroof_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_sunroof_closed_supported(self) -> bool:
        return self._is_window_supported("sunRoof")

    @property
    def sunroof_rear_closed(self) -> bool | None:
        return self._get_window_state("sunRoofRear")

    @property
    def sunroof_rear_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_sunroof_rear_closed_supported(self) -> bool:
        return self._is_window_supported("sunRoofRear")

    @property
    def roof_cover_closed(self) -> bool | None:
        return self._get_window_state("roofCover")

    @property
    def roof_cover_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_roof_cover_closed_supported(self) -> bool:
        return self._is_window_supported("roofCover")

    # Locks
    @property
    def door_locked_sensor(self) -> bool:
        """Return same state as lock entity, since they are mutually exclusive."""
        return self.door_locked

    @property
    def door_locked(self) -> bool:
        """Return whether all doors are locked.

        For EMEA vehicles, reads the lock status from the selectivestatus
        ``accessStatus`` path.

        For NA vehicles (country='US'/'CA'), reads from the ``na_status``
        state populated by the RVS (Remote Vehicle Status) endpoint.

        Returns:
            True if all doors are locked, False otherwise.
        """
        # NA region: read from na_status state populated by RVS endpoint
        if self._connection is not None and self._connection.is_na:
            try:
                return self._na_door_locked()
            except ValueError as exc:
                _LOGGER.warning("NA door lock data malformed for %s: %s", self.vin, exc)
                return False
        # EMEA: existing logic unchanged
        return find_path(self.attrs, Paths.ACCESS_DOOR_LOCK) == "locked"

    @property
    def door_locked_last_updated(self) -> datetime:
        """Return door lock last updated."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def door_locked_sensor_last_updated(self) -> datetime:
        """Return door lock last updated."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_door_locked_supported(self) -> bool:
        """Return true if supported."""
        # NA region: supported if na_status data is present
        if self._connection is not None and self._connection.is_na:
            return self._states.get("na_status") is not None
        # EMEA: existing logic unchanged
        if not self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        return is_valid_path(self.attrs, Paths.ACCESS_DOOR_LOCK)

    @property
    def is_door_locked_sensor_supported(self) -> bool:
        """Return true if supported.

        :return:
        """
        # Use real lock if the service is actually enabled
        if self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        return is_valid_path(self.attrs, Paths.ACCESS_DOOR_LOCK)

    # Per-door lock properties (NA only; EMEA always returns None/False)

    @property
    def door_locked_left_front(self) -> bool | None:
        """Return left-front door lock state. NA only; None for EMEA."""
        return self._get_na_door_lock_state("frontLeft")

    @property
    def is_door_locked_left_front_supported(self) -> bool:
        """Return True when left-front door lock data is available."""
        return self._is_na_door_lock_supported("frontLeft")

    @property
    def door_locked_right_front(self) -> bool | None:
        """Return right-front door lock state. NA only; None for EMEA."""
        return self._get_na_door_lock_state("frontRight")

    @property
    def is_door_locked_right_front_supported(self) -> bool:
        """Return True when right-front door lock data is available."""
        return self._is_na_door_lock_supported("frontRight")

    @property
    def door_locked_left_back(self) -> bool | None:
        """Return left-rear door lock state. NA only; None for EMEA."""
        return self._get_na_door_lock_state("rearLeft")

    @property
    def is_door_locked_left_back_supported(self) -> bool:
        """Return True when left-rear door lock data is available."""
        return self._is_na_door_lock_supported("rearLeft")

    @property
    def door_locked_right_back(self) -> bool | None:
        """Return right-rear door lock state. NA only; None for EMEA."""
        return self._get_na_door_lock_state("rearRight")

    @property
    def is_door_locked_right_back_supported(self) -> bool:
        """Return True when right-rear door lock data is available."""
        return self._is_na_door_lock_supported("rearRight")

    @property
    def security_status(self) -> str | None:
        """Return aggregate vehicle security status (NA only)."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return (na_status.get("exteriorStatus") or {}).get("secure")
        return None

    @property
    def security_status_last_updated(self) -> datetime | None:
        """Return security status last updated timestamp."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            ts = (
                (na_status.get("exteriorStatus") or {})
                .get("doorStatus", {})
                .get("doorStatusTimestamp")
            )
            if ts is not None:
                try:
                    return datetime.fromisoformat(ts)
                except ValueError:
                    return None
        return None

    @property
    def is_security_status_supported(self) -> bool:
        """Return true if aggregate security status is supported."""
        na_status = self._states.get("na_status")
        return (
            na_status is not None
            and (na_status.get("exteriorStatus") or {}).get("secure") is not None
        )

    @property
    def trunk_locked(self) -> bool:
        """Return trunk locked state."""
        doors = find_path(self.attrs, Paths.ACCESS_DOORS) or []
        for door in doors:
            if door.get("name") == "trunk":
                return "locked" in (door.get("status") or [])
        return False

    @property
    def trunk_locked_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_trunk_locked_supported(self) -> bool:
        """Return true if supported.

        :return:
        """
        if not self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        if not is_valid_path(self.attrs, Paths.ACCESS_DOORS):
            return False
        doors = find_path(self.attrs, Paths.ACCESS_DOORS) or []
        for door in doors:
            if door.get("name") == "trunk" and "unsupported" not in (
                door.get("status") or []
            ):
                return True
        return False

    @property
    def trunk_locked_sensor(self) -> bool:
        """Return trunk locked state."""
        doors = find_path(self.attrs, Paths.ACCESS_DOORS) or []
        for door in doors:
            if door.get("name") == "trunk":
                return "locked" in (door.get("status") or [])
        return False

    @property
    def trunk_locked_sensor_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_trunk_locked_sensor_supported(self) -> bool:
        """Return true if supported.

        :return:
        """
        if self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        if not is_valid_path(self.attrs, Paths.ACCESS_DOORS):
            return False
        doors = find_path(self.attrs, Paths.ACCESS_DOORS) or []
        for door in doors:
            if door.get("name") == "trunk" and "unsupported" not in (
                door.get("status") or []
            ):
                return True
        return False

    # Aggregate door/window status
    @property
    def any_door_open(self) -> bool:
        """True if any entry in ``exteriorStatus.doorStatus`` is ``"OPEN"``. NA region only."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            door_status = (na_status.get("exteriorStatus") or {}).get(
                "doorStatus"
            ) or {}
            return any(v == "OPEN" for v in door_status.values() if isinstance(v, str))
        return False

    @property
    def is_any_door_open_supported(self) -> bool:
        """True if any_door_open data is available. NA region only."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return (na_status.get("exteriorStatus") or {}).get("doorStatus") is not None
        return False

    @property
    def any_door_unlocked(self) -> bool:
        """True if any door lock status is UNLOCKED. NA region only."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            lock_status = (na_status.get("exteriorStatus") or {}).get(
                "doorLockStatus"
            ) or {}
            return any(
                v == "UNLOCKED" for v in lock_status.values() if isinstance(v, str)
            )
        return False

    @property
    def is_any_door_unlocked_supported(self) -> bool:
        """True if any_door_unlocked data is available. NA region only."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return (na_status.get("exteriorStatus") or {}).get(
                "doorLockStatus"
            ) is not None
        return False

    @property
    def any_window_open(self) -> bool:
        """True if any window is open. NA region only."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            window_status = (na_status.get("exteriorStatus") or {}).get(
                "windowStatus"
            ) or {}
            return any(
                v == "OPEN" for v in window_status.values() if isinstance(v, str)
            )
        return False

    @property
    def is_any_window_open_supported(self) -> bool:
        """True if any_window_open data is available. NA region only."""
        na_status = self._states.get("na_status")
        if na_status is not None:
            return (na_status.get("exteriorStatus") or {}).get(
                "windowStatus"
            ) is not None
        return False

    # Doors, hood and trunk
    @property
    def hood_closed(self) -> bool | None:
        return self._get_door_state("bonnet")

    @property
    def hood_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_hood_closed_supported(self) -> bool:
        return self._is_door_supported("bonnet")

    @property
    def door_closed_left_front(self) -> bool | None:
        return self._get_door_state("frontLeft")

    @property
    def door_closed_left_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_door_closed_left_front_supported(self) -> bool:
        return self._is_door_supported("frontLeft")

    @property
    def door_closed_right_front(self) -> bool | None:
        return self._get_door_state("frontRight")

    @property
    def door_closed_right_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_door_closed_right_front_supported(self) -> bool:
        return self._is_door_supported("frontRight")

    @property
    def door_closed_left_back(self) -> bool | None:
        return self._get_door_state("rearLeft")

    @property
    def door_closed_left_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_door_closed_left_back_supported(self) -> bool:
        return self._is_door_supported("rearLeft")

    @property
    def door_closed_right_back(self) -> bool | None:
        return self._get_door_state("rearRight")

    @property
    def door_closed_right_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_door_closed_right_back_supported(self) -> bool:
        return self._is_door_supported("rearRight")

    @property
    def trunk_closed(self) -> bool | None:
        return self._get_door_state("trunk")

    @property
    def trunk_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, Paths.ACCESS_TS)

    @property
    def is_trunk_closed_supported(self) -> bool:
        return self._is_door_supported("trunk")

    # Departure timers
    @property
    def departure_timer1(self) -> bool:
        """Return timer #1 status."""
        return self.departure_timer_enabled(1)

    @property
    def departure_timer2(self) -> bool:
        """Return timer #2 status."""
        return self.departure_timer_enabled(2)

    @property
    def departure_timer3(self) -> bool:
        """Return timer #3 status."""
        return self.departure_timer_enabled(3)

    @property
    def departure_timer1_last_updated(self) -> datetime | str | None:
        """Return last updated timestamp."""
        if is_valid_path(self.attrs, Paths.DEPARTURE_PROFILES_TS):
            return find_path(self.attrs, Paths.DEPARTURE_PROFILES_TS)
        if is_valid_path(self.attrs, Paths.AUXILIARY_HEATING_TIMERS_TS):
            return find_path(self.attrs, Paths.AUXILIARY_HEATING_TIMERS_TS)
        if is_valid_path(self.attrs, Paths.DEPARTURE_TIMERS_TS):
            return find_path(self.attrs, Paths.DEPARTURE_TIMERS_TS)
        return None

    @property
    def departure_timer2_last_updated(self) -> datetime | str | None:
        """Return last updated timestamp."""
        return self.departure_timer1_last_updated

    @property
    def departure_timer3_last_updated(self) -> datetime | str | None:
        """Return last updated timestamp."""
        return self.departure_timer1_last_updated

    @property
    def is_departure_timer1_supported(self) -> bool:
        """Check if timer 1 is supported."""
        return self.is_departure_timer_supported(1)

    @property
    def is_departure_timer2_supported(self) -> bool:
        """Check if timer 2is supported."""
        return self.is_departure_timer_supported(2)

    @property
    def is_departure_timer3_supported(self) -> bool:
        """Check if timer 3 is supported."""
        return self.is_departure_timer_supported(3)

    def departure_timer_enabled(self, timer_id: str | int) -> bool:
        """Return if departure timer is enabled."""
        timer = self.departure_timer(timer_id)
        if timer is None:
            return False
        return timer.get("enabled", False)

    def is_departure_timer_supported(self, timer_id: str | int) -> bool:
        """Return true if departure timer is supported."""
        return self.departure_timer(timer_id) is not None

    def timer_attributes(self, timer_id: str | int) -> dict[str, Any]:
        """Return departure timer attributes."""
        timer = self.departure_timer(timer_id)
        if timer is None:
            return {}
        profile = self.departure_profile(timer.get("profileIDs", [0])[0])
        timer_type = None
        recurring_on = []
        start_time = None
        if timer.get("singleTimer", None):
            timer_type = "single"
            if timer.get("singleTimer", None).get("startDateTime", None):
                start_date_time = timer.get("singleTimer", None).get(
                    "startDateTime", None
                )
                start_time = (
                    start_date_time.replace(tzinfo=UTC)
                    .astimezone(tz=None)
                    .strftime("%Y-%m-%dT%H:%M:%S")
                )
            if timer.get("singleTimer", None).get("startDateTimeLocal", None):
                start_date_time = timer.get("singleTimer", None).get(
                    "startDateTimeLocal", None
                )
                if isinstance(start_date_time, str):
                    start_date_time = datetime.strptime(
                        start_date_time, "%Y-%m-%dT%H:%M:%S"
                    )
                start_time = start_date_time
            if timer.get("singleTimer", None).get("departureDateTimeLocal", None):
                start_date_time = timer.get("singleTimer", None).get(
                    "departureDateTimeLocal", None
                )
                if isinstance(start_date_time, str):
                    start_date_time = datetime.strptime(
                        start_date_time, "%Y-%m-%dT%H:%M:%S"
                    )
                start_time = start_date_time
        elif timer.get("recurringTimer", None):
            timer_type = "recurring"
            if timer.get("recurringTimer", None).get("startTime", None):
                start_date_time = timer.get("recurringTimer", None).get(
                    "startTime", None
                )
                utc_time = datetime.strptime(start_date_time, "%H:%M").time()
                start_time = (
                    datetime.combine(date.today(), utc_time)
                    .replace(tzinfo=UTC)
                    .astimezone(tz=None)
                    .strftime("%H:%M")
                )
            if timer.get("recurringTimer", None).get("startTimeLocal", None):
                start_date_time = timer.get("recurringTimer", None).get(
                    "startTimeLocal", None
                )
                start_time = datetime.strptime(start_date_time, "%H:%M").strftime(
                    "%H:%M"
                )
            if timer.get("recurringTimer", None).get("departureTimeLocal", None):
                start_date_time = timer.get("recurringTimer", None).get(
                    "departureTimeLocal", None
                )
                start_time = datetime.strptime(start_date_time, "%H:%M").strftime(
                    "%H:%M"
                )
            recurring_days = timer.get("recurringTimer", None).get("recurringOn", None)
            recurring_days = timer.get("recurringTimer", {}).get("recurringOn", {})
            recurring_on = [day for day in recurring_days if recurring_days.get(day)]
        data = {
            "timer_id": timer.get("id", None),
            "timer_type": timer_type,
            "start_time": start_time,
            "recurring_on": recurring_on,
        }
        if profile is not None:
            data["profile_id"] = profile.get("id", None)
            data["profile_name"] = profile.get("name", None)
            data["charging_enabled"] = profile.get("charging", False)
            data["climatisation_enabled"] = profile.get("climatisation", False)
            data["target_charge_level_pct"] = profile.get("targetSOC_pct", None)
            data["charger_max_ac_ampere"] = profile.get("maxChargeCurrentAC", None)
        if timer.get("charging", None) is not None:
            data["charging_enabled"] = timer.get("charging", False)
        if timer.get("climatisation", None) is not None:
            data["climatisation_enabled"] = timer.get("climatisation", False)
        if timer.get("preferredChargingTimes", None):
            preferred_charging_times = timer.get("preferredChargingTimes", None)[0]
            data["preferred_charging_times_enabled"] = preferred_charging_times.get(
                "enabled", None
            )
            data["preferred_charging_start_time"] = preferred_charging_times.get(
                "startTimeLocal", None
            )
            data["preferred_charging_end_time"] = preferred_charging_times.get(
                "endTimeLocal", None
            )
        return data

    def departure_timer(self, timer_id: str | int) -> dict[str, Any] | None:
        """Return departure timer."""
        if is_valid_path(self.attrs, Paths.DEPARTURE_PROFILES_TIMERS):
            timers = find_path(self.attrs, Paths.DEPARTURE_PROFILES_TIMERS)
            for timer in timers:
                if timer.get("id", 0) == timer_id:
                    return timer
        if is_valid_path(self.attrs, Paths.AUXILIARY_HEATING_TIMERS):
            timers = find_path(self.attrs, Paths.AUXILIARY_HEATING_TIMERS)
            for timer in timers:
                if timer.get("id", 0) == timer_id:
                    return timer
        if is_valid_path(self.attrs, Paths.DEPARTURE_TIMERS):
            timers = find_path(self.attrs, Paths.DEPARTURE_TIMERS)
            for timer in timers:
                if timer.get("id", 0) == timer_id:
                    return timer
        return None

    def departure_profile(self, profile_id: str | int) -> dict[str, Any] | None:
        """Return departure profile."""
        if is_valid_path(self.attrs, Paths.DEPARTURE_PROFILES_PROFILES):
            profiles = find_path(self.attrs, Paths.DEPARTURE_PROFILES_PROFILES)
            for profile in profiles:
                if profile.get("id", 0) == profile_id:
                    return profile
        return None

    # AC Departure timers
    @property
    def ac_departure_timer1(self) -> bool:
        """Return ac timer #1 status."""
        return self.ac_departure_timer_enabled(1)

    @property
    def ac_departure_timer2(self) -> bool:
        """Return ac timer #2 status."""
        return self.ac_departure_timer_enabled(2)

    @property
    def ac_departure_timer1_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, Paths.CLIMATISATION_TIMERS_TS)

    @property
    def ac_departure_timer2_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return self.ac_departure_timer1_last_updated

    @property
    def is_ac_departure_timer1_supported(self) -> bool:
        """Check if ac timer 1 is supported."""
        return self.is_ac_departure_timer_supported(1)

    @property
    def is_ac_departure_timer2_supported(self) -> bool:
        """Check if ac timer 2 is supported."""
        return self.is_ac_departure_timer_supported(2)

    def ac_departure_timer_enabled(self, timer_id: str | int) -> bool:
        """Return if departure timer is enabled."""
        timer = self.ac_departure_timer(timer_id)
        if timer is None:
            return False
        return timer.get("enabled", False)

    def is_ac_departure_timer_supported(self, timer_id: str | int) -> bool:
        """Return true if ac departure timer is supported."""
        return self.ac_departure_timer(timer_id) is not None

    def ac_departure_timer(self, timer_id: str | int) -> dict[str, Any] | None:
        """Return ac departure timer."""
        if is_valid_path(self.attrs, Paths.CLIMATISATION_TIMERS):
            timers = find_path(self.attrs, Paths.CLIMATISATION_TIMERS)
            for timer in timers:
                if timer.get("id", 0) == timer_id:
                    return timer
        return None

    def ac_timer_attributes(self, timer_id: str | int) -> dict[str, Any]:
        """Return ac departure timer attributes."""
        timer = self.ac_departure_timer(timer_id)
        if timer is None:
            return {}
        timer_type = None
        recurring_on = []
        start_time = None
        if timer.get("singleTimer", None):
            timer_type = "single"
            if timer.get("singleTimer", None).get("startDateTime", None):
                start_date_time = timer.get("singleTimer", None).get(
                    "startDateTime", None
                )
                start_time = (
                    start_date_time.replace(tzinfo=UTC)
                    .astimezone(tz=None)
                    .strftime("%Y-%m-%dT%H:%M:%S")
                )
            if timer.get("singleTimer", None).get("startDateTimeLocal", None):
                start_date_time = timer.get("singleTimer", None).get(
                    "startDateTimeLocal", None
                )
                if isinstance(start_date_time, str):
                    start_date_time = datetime.strptime(
                        start_date_time, "%Y-%m-%dT%H:%M:%S"
                    )
                start_time = start_date_time
        elif timer.get("recurringTimer", None):
            timer_type = "recurring"
            if timer.get("recurringTimer", None).get("startTime", None):
                start_date_time = timer.get("recurringTimer", None).get(
                    "startTime", None
                )
                utc_time = datetime.strptime(start_date_time, "%H:%M").time()
                start_time = (
                    datetime.combine(date.today(), utc_time)
                    .replace(tzinfo=UTC)
                    .astimezone(tz=None)
                    .strftime("%H:%M")
                )
                recurring_days = timer.get("recurringTimer", None).get(
                    "recurringOn", None
                )
                recurring_on = [
                    day for day in recurring_days if recurring_days.get(day)
                ]
            if timer.get("recurringTimer", None).get("startTimeLocal", None):
                start_date_time = timer.get("recurringTimer", None).get(
                    "startTimeLocal", None
                )
                start_time = datetime.strptime(start_date_time, "%H:%M").strftime(
                    "%H:%M"
                )
        return {
            "timer_id": timer.get("id", None),
            "timer_type": timer_type,
            "start_time": start_time,
            "recurring_on": recurring_on,
        }

    # Trip last data
    @property
    def last_trip_average_speed(self) -> Any:
        na_trip = self._states.get("na_trip")
        if na_trip is not None:
            return na_trip.get("averageSpeed")
        return self._get_trip_value(Services.TRIP_LAST, "averageSpeed_kmph")

    @property
    def last_trip_average_speed_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_average_speed_supported(self) -> bool:
        na_trip = self._states.get("na_trip")
        if na_trip is not None:
            return "averageSpeed" in na_trip
        return self._is_trip_supported(Services.TRIP_LAST, "averageSpeed_kmph")

    @property
    def last_trip_average_electric_engine_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "averageElectricConsumption")

    @property
    def last_trip_average_electric_engine_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_average_electric_engine_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LAST, "averageElectricConsumption")

    @property
    def last_trip_average_fuel_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "averageFuelConsumption")

    @property
    def last_trip_average_fuel_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_average_fuel_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LAST, "averageFuelConsumption")

    @property
    def last_trip_average_gas_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "averageGasConsumption")

    @property
    def last_trip_average_gas_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_average_gas_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LAST, "averageGasConsumption")

    @property
    def last_trip_average_auxillary_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "averageAuxConsumption")

    @property
    def last_trip_average_auxillary_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_average_auxillary_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LAST, "averageAuxConsumption")

    @property
    def last_trip_average_aux_consumer_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "averageAuxConsumerConsumption")

    @property
    def last_trip_average_aux_consumer_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_average_aux_consumer_consumption_supported(self) -> bool:
        return self._is_trip_supported(
            Services.TRIP_LAST, "averageAuxConsumerConsumption"
        )

    @property
    def last_trip_duration(self) -> Any:
        na_trip = self._states.get("na_trip")
        if na_trip is not None:
            return na_trip.get("tripDuration")
        return self._get_trip_value(Services.TRIP_LAST, "travelTime")

    @property
    def last_trip_duration_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_duration_supported(self) -> bool:
        if self._states.get("na_trip") is not None:
            return self._states["na_trip"].get("tripDuration") is not None
        return self._is_trip_supported(Services.TRIP_LAST, "travelTime")

    @property
    def last_trip_length(self) -> Any:
        na_trip = self._states.get("na_trip")
        if na_trip is not None:
            return na_trip.get("tripDistance")
        return self._get_trip_value(Services.TRIP_LAST, "mileage_km")

    @property
    def last_trip_length_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_length_supported(self) -> bool:
        if self._states.get("na_trip") is not None:
            return self._states["na_trip"].get("tripDistance") is not None
        return self._is_trip_supported(Services.TRIP_LAST, "mileage_km")

    @property
    def last_trip_start_km(self):
        return self._get_trip_value(Services.TRIP_LAST, "startMileage_km")

    @property
    def is_last_trip_start_km_supported(self):
        return self._is_trip_supported(Services.TRIP_LAST, "startMileage_km")

    @property
    def last_trip_average_recuperation(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "averageRecuperation")

    @property
    def last_trip_average_recuperation_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_average_recuperation_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LAST, "averageRecuperation")

    @property
    def last_trip_total_electric_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "totalElectricConsumption_kwh")

    @property
    def last_trip_total_electric_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_total_electric_consumption_supported(self) -> bool:
        return self._is_trip_supported(
            Services.TRIP_LAST, "totalElectricConsumption_kwh"
        )

    @property
    def last_trip_total_fuel_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "totalFuelConsumption_L")

    @property
    def last_trip_total_fuel_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LAST, "tripEndTimestamp")

    @property
    def is_last_trip_total_fuel_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LAST, "totalFuelConsumption_L")

    # Trip since last refuel data
    @property
    def refuel_trip_average_speed(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "averageSpeed_kmph")

    @property
    def refuel_trip_average_speed_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_average_speed_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_REFUEL, "averageSpeed_kmph")

    @property
    def refuel_trip_average_electric_engine_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "averageElectricConsumption")

    @property
    def refuel_trip_average_electric_engine_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_average_electric_engine_consumption_supported(self) -> bool:
        return self._is_trip_supported(
            Services.TRIP_REFUEL, "averageElectricConsumption"
        )

    @property
    def refuel_trip_average_fuel_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "averageFuelConsumption")

    @property
    def refuel_trip_average_fuel_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_average_fuel_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_REFUEL, "averageFuelConsumption")

    @property
    def refuel_trip_average_gas_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "averageGasConsumption")

    @property
    def refuel_trip_average_gas_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_average_gas_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_REFUEL, "averageGasConsumption")

    @property
    def refuel_trip_average_auxillary_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "averageAuxConsumption")

    @property
    def refuel_trip_average_auxillary_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_average_auxillary_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_REFUEL, "averageAuxConsumption")

    @property
    def refuel_trip_average_aux_consumer_consumption(self) -> Any:
        return self._get_trip_value(
            Services.TRIP_REFUEL, "averageAuxConsumerConsumption"
        )

    @property
    def refuel_trip_average_aux_consumer_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_average_aux_consumer_consumption_supported(self) -> bool:
        return self._is_trip_supported(
            Services.TRIP_REFUEL, "averageAuxConsumerConsumption"
        )

    @property
    def refuel_trip_duration(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "travelTime")

    @property
    def refuel_trip_duration_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_duration_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_REFUEL, "travelTime")

    @property
    def refuel_trip_length(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "mileage_km")

    @property
    def refuel_trip_length_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_length_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_REFUEL, "mileage_km")

    @property
    def refuel_trip_average_recuperation(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "averageRecuperation")

    @property
    def refuel_trip_average_recuperation_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_average_recuperation_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_REFUEL, "averageRecuperation")

    @property
    def refuel_trip_total_electric_consumption(self) -> Any:
        return self._get_trip_value(
            Services.TRIP_REFUEL, "totalElectricConsumption_kwh"
        )

    @property
    def refuel_trip_total_electric_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_total_electric_consumption_supported(self) -> bool:
        return self._is_trip_supported(
            Services.TRIP_REFUEL, "totalElectricConsumption_kwh"
        )

    @property
    def refuel_trip_total_fuel_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "totalFuelConsumption_L")

    @property
    def refuel_trip_total_fuel_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_REFUEL, "tripEndTimestamp")

    @property
    def is_refuel_trip_total_fuel_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_REFUEL, "totalFuelConsumption_L")

    # Trip longterm data
    @property
    def longterm_trip_average_speed(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "averageSpeed_kmph")

    @property
    def longterm_trip_average_speed_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_average_speed_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LONGTERM, "averageSpeed_kmph")

    @property
    def longterm_trip_average_electric_engine_consumption(self) -> Any:
        return self._get_trip_value(
            Services.TRIP_LONGTERM, "averageElectricConsumption"
        )

    @property
    def longterm_trip_average_electric_engine_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_average_electric_engine_consumption_supported(self) -> bool:
        return self._is_trip_supported(
            Services.TRIP_LONGTERM, "averageElectricConsumption"
        )

    @property
    def longterm_trip_average_fuel_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "averageFuelConsumption")

    @property
    def longterm_trip_average_fuel_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_average_fuel_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LONGTERM, "averageFuelConsumption")

    @property
    def longterm_trip_average_gas_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "averageGasConsumption")

    @property
    def longterm_trip_average_gas_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_average_gas_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LONGTERM, "averageGasConsumption")

    @property
    def longterm_trip_average_auxillary_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "averageAuxConsumption")

    @property
    def longterm_trip_average_auxillary_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_average_auxillary_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LONGTERM, "averageAuxConsumption")

    @property
    def longterm_trip_average_aux_consumer_consumption(self) -> Any:
        return self._get_trip_value(
            Services.TRIP_LONGTERM, "averageAuxConsumerConsumption"
        )

    @property
    def longterm_trip_average_aux_consumer_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_average_aux_consumer_consumption_supported(self) -> bool:
        return self._is_trip_supported(
            Services.TRIP_LONGTERM, "averageAuxConsumerConsumption"
        )

    @property
    def longterm_trip_duration(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "travelTime")

    @property
    def longterm_trip_duration_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_duration_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LONGTERM, "travelTime")

    @property
    def longterm_trip_length(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "mileage_km")

    @property
    def longterm_trip_length_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_length_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LONGTERM, "mileage_km")

    @property
    def longterm_trip_average_recuperation(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "averageRecuperation")

    @property
    def longterm_trip_average_recuperation_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_average_recuperation_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LONGTERM, "averageRecuperation")

    @property
    def longterm_trip_total_electric_consumption(self) -> Any:
        return self._get_trip_value(
            Services.TRIP_LONGTERM, "totalElectricConsumption_kwh"
        )

    @property
    def longterm_trip_total_electric_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_total_electric_consumption_supported(self) -> bool:
        return self._is_trip_supported(
            Services.TRIP_LONGTERM, "totalElectricConsumption_kwh"
        )

    @property
    def longterm_trip_total_fuel_consumption(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "totalFuelConsumption_L")

    @property
    def longterm_trip_total_fuel_consumption_last_updated(self) -> Any:
        return self._get_trip_value(Services.TRIP_LONGTERM, "tripEndTimestamp")

    @property
    def is_longterm_trip_total_fuel_consumption_supported(self) -> bool:
        return self._is_trip_supported(Services.TRIP_LONGTERM, "totalFuelConsumption_L")

    @property
    def honk_and_flash(self) -> Any:
        """Return state of automatic window heating."""
        return self._requests.get("honk_and_flash", {}).get("id", False)

    @property
    def honk_and_flash_last_updated(self) -> datetime:
        """Return state of automatic window heating last updated."""
        return self._requests.get("honk_and_flash", {}).get("timestamp")

    @property
    def is_honk_and_flash_supported(self) -> bool:
        """Return true if automatic window heating is supported."""
        if not self._services.get(Services.HONK_AND_FLASH, {}).get("active", False):
            return False
        return True

    # Status of set data requests
    @property
    def refresh_action_status(self) -> str | None:
        """Return latest status of data refresh request."""
        return self._requests.get("refresh", {}).get("status", "None")

    @property
    def charger_action_status(self) -> str | None:
        """Return latest status of charger request."""
        return self._requests.get("batterycharge", {}).get("status", "None")

    @property
    def climater_action_status(self) -> str | None:
        """Return latest status of climater request."""
        return self._requests.get("climatisation", {}).get("status", "None")

    @property
    def lock_action_status(self) -> str | None:
        """Return latest status of lock action request."""
        return self._requests.get("lock", {}).get("status", "None")

    @property
    def honk_and_flash_action_status(self) -> str | None:
        """Return latest status of honk and flash request."""
        return self._requests.get("honk_and_flash", {}).get("status", "None")

    # Requests data
    @property
    def refresh_data(self) -> Any:
        """Get state of data refresh."""
        return self._requests.get("refresh", {}).get("id", False)

    @property
    def refresh_data_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self._requests.get("refresh", {}).get("timestamp")

    @property
    def is_refresh_data_supported(self) -> bool:
        """Return true, as data refresh is always supported."""
        return True

    @property
    def request_in_progress(self) -> bool:
        """Check of any requests are currently in progress."""
        try:
            return any(
                isinstance(value, dict) and "id" in value and bool(value["id"])
                for value in self._requests.values()
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(e)
        return False

    @property
    def request_in_progress_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        try:
            # Get all timestamps in the dictionary
            timestamps = [
                item["timestamp"]
                for item in self._requests.values()
                if isinstance(item, dict) and "timestamp" in item
            ]

            # Return the most recent timestamp
            return max(timestamps) if timestamps else datetime.now(UTC)
        except Exception as e:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(e)
        return datetime.now(UTC)

    @property
    def is_request_in_progress_supported(self) -> bool:
        """Request in progress is always supported."""
        return True

    @property
    def request_results(self) -> dict:
        """Get last request result."""
        data = {
            "latest": self._requests.get("latest", None),
            "state": self._requests.get("state", None),
        }
        for section in self._requests:
            if section in [
                "departuretimer",
                "batterycharge",
                "climatisation",
                "refresh",
                "lock",
            ]:
                data[section] = self._requests[section].get("status", "Unknown")
        return data

    @property
    def request_results_last_updated(self) -> datetime | None:
        """Get last updated time."""
        if self._requests.get("latest", "") != "":
            return self._requests.get(str(self._requests.get("latest")), {}).get(
                "timestamp"
            )
        # all requests should have more or less the same timestamp anyway, so
        # just return the first one
        for section in [
            "departuretimer",
            "batterycharge",
            "climatisation",
            "refresh",
            "lock",
        ]:
            if section in self._requests:
                return self._requests[section].get("timestamp")
        return None

    @property
    def is_request_results_supported(self) -> bool:
        """Request results is supported if in progress is supported."""
        return self.is_request_in_progress_supported

    @property
    def requests_results_last_updated(self) -> None:
        """Return last updated timestamp for attribute."""
        return None

    # Helper functions #
    def __str__(self) -> str:
        """Return the vin."""
        return self.vin

    @property
    def json(self) -> str:
        """Return vehicle data in JSON format.

        :return:
        """

        def serialize(obj: Any) -> Any:
            """Convert datetime instances back to JSON compatible format.

            :param obj:
            :return:
            """
            return obj.isoformat() if isinstance(obj, datetime) else obj

        return to_json(
            OrderedDict(sorted(self.attrs.items())), indent=4, default=serialize
        )

    def is_primary_drive_electric(self) -> bool:
        """Check if primary engine is electric."""
        return (
            find_path(self.attrs, Paths.MEASUREMENTS_FUEL_PRIMARY_ENGINE)
            == ENGINE_TYPE_ELECTRIC
        )

    def is_secondary_drive_electric(self) -> bool:
        """Check if secondary engine is electric."""
        return (
            is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_SECONDARY_ENGINE)
            and find_path(self.attrs, Paths.MEASUREMENTS_FUEL_SECONDARY_ENGINE)
            == ENGINE_TYPE_ELECTRIC
        )

    def is_primary_drive_combustion(self) -> bool:
        """Check if primary engine is combustion."""
        engine_type = ""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_TYPE):
            engine_type = find_path(self.attrs, Paths.FUEL_STATUS_PRIMARY_TYPE)

        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_PRIMARY_ENGINE):
            engine_type = find_path(self.attrs, Paths.MEASUREMENTS_FUEL_PRIMARY_ENGINE)

        return engine_type in ENGINE_TYPE_COMBUSTION

    def is_secondary_drive_combustion(self) -> bool:
        """Check if secondary engine is combustion."""
        engine_type = ""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_SECONDARY_TYPE):
            engine_type = find_path(self.attrs, Paths.FUEL_STATUS_SECONDARY_TYPE)

        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_SECONDARY_ENGINE):
            engine_type = find_path(
                self.attrs, Paths.MEASUREMENTS_FUEL_SECONDARY_ENGINE
            )

        return engine_type in ENGINE_TYPE_COMBUSTION

    def is_primary_drive_gas(self) -> bool:
        """Check if primary engine is gas."""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE):
            return find_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE) == ENGINE_TYPE_GAS
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE):
            return (
                find_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE)
                == ENGINE_TYPE_GAS
            )
        return False

    @property
    def is_car_type_electric(self) -> bool:
        """Check if car type is electric."""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE):
            return (
                find_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE)
                == ENGINE_TYPE_ELECTRIC
            )
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE):
            return (
                find_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE)
                == ENGINE_TYPE_ELECTRIC
            )
        return False

    @property
    def is_car_type_diesel(self) -> bool:
        """Check if car type is diesel."""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE):
            return (
                find_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE) == ENGINE_TYPE_DIESEL
            )
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE):
            return (
                find_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE)
                == ENGINE_TYPE_DIESEL
            )
        return False

    @property
    def is_car_type_gasoline(self) -> bool:
        """Check if car type is gasoline."""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE):
            return (
                find_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE)
                == ENGINE_TYPE_GASOLINE
            )
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE):
            return (
                find_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE)
                == ENGINE_TYPE_GASOLINE
            )
        return False

    @property
    def is_car_type_hybrid(self) -> bool:
        """Check if car type is hybrid."""
        if is_valid_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE):
            return (
                find_path(self.attrs, Paths.FUEL_STATUS_CAR_TYPE) == ENGINE_TYPE_HYBRID
            )
        if is_valid_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE):
            return (
                find_path(self.attrs, Paths.MEASUREMENTS_FUEL_CAR_TYPE)
                == ENGINE_TYPE_HYBRID
            )
        return False

    @property
    def has_combustion_engine(self) -> bool:
        """Return true if car has a combustion engine."""
        return (
            self.is_primary_drive_combustion() or self.is_secondary_drive_combustion()
        )

    @property
    def api_vehicles_status(self) -> bool:
        """Check vehicles API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("vehicles", "Unknown")

    @property
    def api_vehicles_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(UTC)

    @property
    def is_api_vehicles_status_supported(self) -> bool:
        """Vehicles API status is always supported."""
        return True

    @property
    def api_capabilities_status(self) -> bool:
        """Check capabilities API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get(
            "capabilities", "Unknown"
        )

    @property
    def api_capabilities_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(UTC)

    @property
    def is_api_capabilities_status_supported(self) -> bool:
        """Capabilities API status is always supported."""
        return True

    @property
    def api_trips_status(self) -> bool:
        """Check trips API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("trips", "Unknown")

    @property
    def api_trips_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(UTC)

    @property
    def is_api_trips_status_supported(self) -> bool:
        """Check if Trips API status is supported."""
        if self._services.get(Services.TRIP_STATISTICS, {}).get("active", False):
            return True
        return False

    @property
    def api_selectivestatus_status(self) -> bool:
        """Check selectivestatus API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get(
            "selectivestatus", "Unknown"
        )

    @property
    def api_selectivestatus_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(UTC)

    @property
    def is_api_selectivestatus_status_supported(self) -> bool:
        """Selectivestatus API status is always supported."""
        return True

    @property
    def api_parkingposition_status(self) -> bool:
        """Check parkingposition API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get(
            "parkingposition", "Unknown"
        )

    @property
    def api_parkingposition_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(UTC)

    @property
    def is_api_parkingposition_status_supported(self) -> bool:
        """Check if Parkingposition API status is supported."""
        if self._services.get(Services.PARKING_POSITION, {}).get("active", False):
            return True
        return False

    @property
    def api_token_status(self) -> bool:
        """Check token API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("token", "Unknown")

    @property
    def api_token_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(UTC)

    @property
    def is_api_token_status_supported(self) -> bool:
        """Parkingposition API status is always supported."""
        return True

    @property
    def last_data_refresh(self) -> datetime | None:
        """Check when services were refreshed successfully for the last time."""
        last_data_refresh_path = "refreshTimestamp"
        if is_valid_path(self.attrs, last_data_refresh_path):
            return find_path(self.attrs, last_data_refresh_path)
        return None

    @property
    def last_data_refresh_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(UTC)

    @property
    def is_last_data_refresh_supported(self) -> bool:
        """Last data refresh is always supported."""
        return True
