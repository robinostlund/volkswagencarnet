#!/usr/bin/env python3
"""Vehicle class for We Connect."""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from json import dumps as to_json

from .vw_const import VehicleStatusParameter as P, Services
from .vw_utilities import find_path, is_valid_path

# TODO
# Images (https://emea.bff.cariad.digital/media/v2/vehicle-images/WVWZZZ3HZPK002581?resolution=3x)
# {"data":[{"id":"door_right_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_front_overlay.png","fileName":"image_door_right_front_overlay.png"},{"id":"light_right","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_light_right.png","fileName":"image_light_right.png"},{"id":"sunroof_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_sunroof_overlay.png","fileName":"image_sunroof_overlay.png"},{"id":"trunk_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_trunk_overlay.png","fileName":"image_trunk_overlay.png"},{"id":"car_birdview","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_car_birdview.png","fileName":"image_car_birdview.png"},{"id":"door_left_front","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_front.png","fileName":"image_door_left_front.png"},{"id":"door_right_front","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_front.png","fileName":"image_door_right_front.png"},{"id":"sunroof","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_sunroof.png","fileName":"image_sunroof.png"},{"id":"window_right_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_right_front_overlay.png","fileName":"image_window_right_front_overlay.png"},{"id":"car_34view","url":"https://media.volkswagen.com/Vilma/V/3H9/2023/Front_Right/c8ca31fcf999b04d42940620653c494215e0d49756615f3524499261d96ccdce.png?width=1163","fileName":""},{"id":"door_left_back","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_back.png","fileName":"image_door_left_back.png"},{"id":"door_right_back","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_back.png","fileName":"image_door_right_back.png"},{"id":"window_left_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_left_back_overlay.png","fileName":"image_window_left_back_overlay.png"},{"id":"window_right_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_right_back_overlay.png","fileName":"image_window_right_back_overlay.png"},{"id":"bonnet_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_bonnet_overlay.png","fileName":"image_bonnet_overlay.png"},{"id":"door_left_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_back_overlay.png","fileName":"image_door_left_back_overlay.png"},{"id":"door_left_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_front_overlay.png","fileName":"image_door_left_front_overlay.png"},{"id":"door_right_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_back_overlay.png","fileName":"image_door_right_back_overlay.png"},{"id":"light_left","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_light_left.png","fileName":"image_light_left.png"},{"id":"window_left_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_left_front_overlay.png","fileName":"image_window_left_front_overlay.png"}]}
#
# Model Year (unclear, seems to only be available via the web API, language dependent and with separate authentication)

BACKEND_RECEIVED_TIMESTAMP = "BACKEND_RECEIVED_TIMESTAMP"

_LOGGER = logging.getLogger(__name__)

ENGINE_TYPE_ELECTRIC = "electric"
ENGINE_TYPE_DIESEL = "diesel"
ENGINE_TYPE_GASOLINE = "gasoline"
ENGINE_TYPE_HYBRID = "hybrid"
ENGINE_TYPE_COMBUSTION = [ENGINE_TYPE_DIESEL, ENGINE_TYPE_GASOLINE]
DEFAULT_TARGET_TEMP = 24


class Vehicle:
    """Vehicle contains the state of sensors and methods for interacting with the car."""

    def __init__(self, conn, url):
        """Initialize the Vehicle with default values."""
        self._connection = conn
        self._url = url
        self._homeregion = "https://msg.volkswagen.de"
        self._discovered = False
        self._states = {}
        self._requests: dict[str, object] = {
            "departuretimer": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "batterycharge": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "climatisation": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "refresh": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "lock": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "latest": "",
            "state": "",
        }

        # API Endpoints that might be enabled for car (that we support)
        self._services: dict[str, dict[str, object]] = {
            # TODO needs a complete rework...
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
            Services.USER_CAPABILITIES: {"active": False},
            Services.PARAMETERS: {},
        }

    def _in_progress(self, topic: str, unknown_offset: int = 0) -> bool:
        """Check if request is already in progress."""
        if self._requests.get(topic, {}).get("id", False):
            timestamp = self._requests.get(topic, {}).get(
                "timestamp", datetime.now(timezone.utc) - timedelta(minutes=unknown_offset)
            )
            if timestamp + timedelta(minutes=3) < datetime.now(timezone.utc):
                self._requests.get(topic, {}).pop("id")
            else:
                _LOGGER.info(f"Action ({topic}) already in progress")
                return True
        return False

    async def _handle_response(self, response, topic: str, error_msg: str | None = None) -> bool:
        """Handle errors in response and get requests remaining."""
        if not response:
            self._requests[topic] = {"status": "Failed", "timestamp": datetime.now(timezone.utc)}
            _LOGGER.error(error_msg if error_msg is not None else f"Failed to perform {topic} action")
            raise Exception(error_msg if error_msg is not None else f"Failed to perform {topic} action")
        else:
            self._requests[topic] = {
                "timestamp": datetime.now(timezone.utc),
                "status": response.get("state", "Unknown"),
                "id": response.get("id", 0),
            }
            if response.get("state", None) == "Throttled":
                status = "Throttled"
                _LOGGER.warning(f"Request throttled ({topic}")
            else:
                status = await self.wait_for_request(request=response.get("id", 0))
            self._requests[topic] = {"status": status, "timestamp": datetime.now(timezone.utc)}
        return True

    # API get and set functions #
    # Init and update vehicle data
    async def discover(self):
        """Discover vehicle and initial data."""

        _LOGGER.debug("Attempting discovery of supported API endpoints for vehicle.")
        capabilities_response = await self._connection.getOperationList(self.vin)
        parameters_list = capabilities_response.get("parameters", {})
        capabilities_list = capabilities_response.get("capabilities", {})
        if parameters_list:
            self._services[Services.PARAMETERS].update(parameters_list)
        if capabilities_list:
            for service_id in capabilities_list.keys():
                try:
                    if service_id in self._services.keys():
                        service = capabilities_list[service_id]
                        data = {}
                        service_name = service.get("id", None)
                        if service.get("isEnabled", False):
                            _LOGGER.debug(f"Discovered enabled service: {service_name}")
                            data["active"] = True
                            if service.get("expirationDate", False):
                                data["expiration"] = service.get("expirationDate", None)
                            if service.get("operations", False):
                                data.update({"operations": []})
                                for operation_id in service.get("operations", []).keys():
                                    operation = service.get("operations").get(operation_id)
                                    data["operations"].append(operation.get("id", None))
                            if service.get("parameters", False):
                                data.update({"parameters": []})
                                for parameter in service.get("parameters", []):
                                    data["parameters"].append(parameter)
                        else:
                            reason = service.get("status", "Unknown")
                            _LOGGER.debug(f"Service: {service_name} is disabled because of reason: {reason}")
                            data["active"] = False
                        self._services[service_name].update(data)
                except Exception as error:
                    _LOGGER.warning(f'Encountered exception: "{error}" while parsing service item: {service}')
        else:
            _LOGGER.warning(f"Could not determine available API endpoints for {self.vin}")
        _LOGGER.debug(f"API endpoints: {self._services}")
        self._discovered = True

    async def update(self):
        """Try to fetch data for all known API endpoints."""
        if not self._discovered:
            await self.discover()
        if not self.deactivated:
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
                        Services.VEHICLE_LIGHTS,
                        Services.VEHICLE_HEALTH_INSPECTION,
                        Services.USER_CAPABILITIES,
                    ]
                ),
                self.get_vehicle(),
                self.get_parkingposition(),
                self.get_trip_last(),
            )
            await asyncio.gather(self.get_service_status())
        else:
            _LOGGER.info(f"Vehicle with VIN {self.vin} is deactivated.")

    # Data collection functions
    async def get_selectivestatus(self, services):
        """Fetch selective status for specified services."""
        data = await self._connection.getSelectiveStatus(self.vin, services)
        if data:
            self._states.update(data)

    async def get_vehicle(self):
        """Fetch car masterdata."""
        data = await self._connection.getVehicleData(self.vin)
        if data:
            self._states.update(data)

    async def get_parkingposition(self):
        """Fetch parking position if supported."""
        if self._services.get(Services.PARKING_POSITION, {}).get("active", False):
            data = await self._connection.getParkingPosition(self.vin)
            if data:
                self._states.update(data)

    async def get_trip_last(self):
        """Fetch last trip statistics if supported."""
        if self._services.get(Services.TRIP_STATISTICS, {}).get("active", False):
            data = await self._connection.getTripLast(self.vin)
            if data:
                self._states.update(data)

    async def get_service_status(self):
        """Fetch service status."""
        data = await self._connection.get_service_status()
        if data:
            self._states.update({Services.SERVICE_STATUS: data})

    async def wait_for_request(self, request, retry_count=18):
        """Update status of outstanding requests."""
        retry_count -= 1
        if retry_count == 0:
            _LOGGER.info(f"Timeout while waiting for result of {request.requestId}.")
            return "Timeout"
        try:
            status = await self._connection.get_request_status(self.vin, request)
            _LOGGER.debug(f"Request ID {request}: {status}")
            self._requests["state"] = status
            if status == "In Progress":
                await asyncio.sleep(10)
                return await self.wait_for_request(request, retry_count)
            else:
                return status
        except Exception as error:
            _LOGGER.warning(f"Exception encountered while waiting for request status: {error}")
            return "Exception"

    async def wait_for_data_refresh(self, retry_count=18):
        """Update status of outstanding requests."""
        retry_count -= 1
        if retry_count == 0:
            _LOGGER.info("Timeout while waiting for data refresh.")
            return "Timeout"
        try:
            await self.get_selectivestatus([Services.MEASUREMENTS])
            refresh_trigger_time = self._requests.get("refresh", {}).get("timestamp")
            if self.last_connected < refresh_trigger_time:
                await asyncio.sleep(10)
                return await self.wait_for_data_refresh(retry_count)
            else:
                return "successful"
        except Exception as error:
            _LOGGER.warning(f"Exception encountered while waiting for data refresh: {error}")
            return "Exception"

    # Data set functions
    # Charging (BATTERYCHARGE)
    async def set_charger_current(self, value):
        """Set charger current."""
        if self.is_charging_supported:
            if 1 <= int(value) <= 255:
                data = {"action": {"settings": {"maxChargeCurrent": int(value)}, "type": "setSettings"}}
            else:
                _LOGGER.error(f"Set charger maximum current to {value} is not supported.")
                raise Exception(f"Set charger maximum current to {value} is not supported.")
            return await self.set_charger(data)
        else:
            _LOGGER.error("No charger support.")
            raise Exception("No charger support.")

    async def set_charger(self, action) -> bool:
        """Turn on/off charging."""
        if self.is_charging_supported:
            if action not in ["start", "stop"]:
                _LOGGER.error(f'Charging action "{action}" is not supported.')
                raise Exception(f'Charging action "{action}" is not supported.')
            self._requests["latest"] = "Batterycharge"
            response = await self._connection.setCharging(self.vin, (action == "start"))
            return await self._handle_response(
                response=response, topic="charging", error_msg=f"Failed to {action} charging"
            )
        else:
            _LOGGER.error("No charging support.")
            raise Exception("No charging support.")

    async def set_charging_settings(self, setting, value):
        """Set charging settings."""
        if (
            self.is_charge_max_ac_setting_supported
            or self.is_auto_release_ac_connector_supported
            or self.is_battery_target_charge_level_supported
        ):
            if setting == "reduced_ac_charging" and value not in ["reduced", "maximum"]:
                _LOGGER.error(f'Charging setting "{value}" is not supported.')
                raise Exception(f'Charging setting "{value}" is not supported.')
            data = {}
            if self.is_charge_max_ac_setting_supported:
                data["maxChargeCurrentAC"] = value if setting == "reduced_ac_charging" else self.charge_max_ac_setting
            if self.is_auto_release_ac_connector_supported:
                data["autoUnlockPlugWhenChargedAC"] = (
                    value if setting == "auto_release_ac_connector" else self.auto_release_ac_connector_state
                )
            if self.is_battery_target_charge_level_supported:
                self._battery_target_charge_level = value
                data["targetSOC_pct"] = (
                    value if setting == "battery_target_charge_level" else self.battery_target_charge_level
                )
            self._requests["latest"] = "Batterycharge"
            response = await self._connection.setChargingSettings(self.vin, data)
            return await self._handle_response(
                response=response, topic="charging", error_msg="Failed to change charging settings"
            )
        else:
            _LOGGER.error("Charging settings are not supported.")
            raise Exception("Charging settings are not supported.")

    async def set_charging_care_settings(self, value):
        """Set charging care settings."""
        if self.is_battery_care_mode_supported:
            if value not in ["activated", "deactivated"]:
                _LOGGER.error(f'Charging care mode "{value}" is not supported.')
                raise Exception(f'Charging care mode "{value}" is not supported.')
            data = {"batteryCareMode": value}
            self._requests["latest"] = "Batterycharge"
            response = await self._connection.setChargingCareModeSettings(self.vin, data)
            return await self._handle_response(
                response=response, topic="charging", error_msg="Failed to change charging care settings"
            )
        else:
            _LOGGER.error("Charging care settings are not supported.")
            raise Exception("Charging care settings are not supported.")

    async def set_readiness_battery_support(self, value):
        """Set readiness battery support settings."""
        if self.is_optimised_battery_use_supported:
            if value not in [True, False]:
                _LOGGER.error(f'Battery support mode "{value}" is not supported.')
                raise Exception(f'Battery support mode "{value}" is not supported.')
            data = {"batterySupportEnabled": value}
            self._requests["latest"] = "Batterycharge"
            response = await self._connection.setReadinessBatterySupport(self.vin, data)
            return await self._handle_response(
                response=response, topic="charging", error_msg="Failed to change battery support settings"
            )
        else:
            _LOGGER.error("Battery support settings are not supported.")
            raise Exception("Battery support settings are not supported.")

    # Climatisation electric/auxiliary/windows (CLIMATISATION)
    async def set_climatisation_settings(self, setting, value):
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
                        value if setting == "auxiliary_air_conditioning" else self.auxiliary_air_conditioning
                    )
                if self.is_automatic_window_heating_supported:
                    data["windowHeatingEnabled"] = (
                        value if setting == "automatic_window_heating" else self.automatic_window_heating
                    )
                if self.is_zone_front_left_supported:
                    data["zoneFrontLeftEnabled"] = value if setting == "zone_front_left" else self.zone_front_left
                if self.is_zone_front_right_supported:
                    data["zoneFrontRightEnabled"] = value if setting == "zone_front_right" else self.zone_front_right
                self._requests["latest"] = "Climatisation"
                response = await self._connection.setClimaterSettings(self.vin, data)
                return await self._handle_response(
                    response=response,
                    topic="climatisation",
                    error_msg="Failed to set climatisation settings",
                )
            else:
                _LOGGER.error(f'Set climatisation setting to "{value}" is not supported.')
                raise Exception(f'Set climatisation setting to "{value}" is not supported.')
        else:
            _LOGGER.error("Climatisation settings are not supported.")
            raise Exception("Climatisation settings are not supported.")

    async def set_window_heating(self, action="stop"):
        """Turn on/off window heater."""
        if self.is_window_heater_supported:
            if action not in ["start", "stop"]:
                _LOGGER.error(f'Window heater action "{action}" is not supported.')
                raise Exception(f'Window heater action "{action}" is not supported.')
            self._requests["latest"] = "Climatisation"
            response = await self._connection.setWindowHeater(self.vin, (action == "start"))
            return await self._handle_response(
                response=response, topic="climatisation", error_msg=f"Failed to {action} window heating"
            )
        else:
            _LOGGER.error("No climatisation support.")
            raise Exception("No climatisation support.")

    async def set_climatisation(self, action="stop"):
        """Turn on/off climatisation with electric heater."""
        if self.is_electric_climatisation_supported:
            if action == "start":
                data = {
                    "targetTemperature": self.climatisation_target_temperature,
                    "targetTemperatureUnit": "celsius",
                }
                if self.is_climatisation_without_external_power_supported:
                    data["climatisationWithoutExternalPower"] = self.climatisation_without_external_power
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
                _LOGGER.error(f"Invalid climatisation action: {action}")
                raise Exception(f"Invalid climatisation action: {action}")
            self._requests["latest"] = "Climatisation"
            response = await self._connection.setClimater(self.vin, data, (action == "start"))
            return await self._handle_response(
                response=response,
                topic="climatisation",
                error_msg=f"Failed to {action} climatisation with electric heater.",
            )
        else:
            _LOGGER.error("No climatisation support.")
            raise Exception("No climatisation support.")

    async def set_auxiliary_climatisation(self, action, spin):
        """Turn on/off climatisation with auxiliary heater."""
        if self.is_auxiliary_climatisation_supported:
            if action == "start":
                data = {"spin": spin}
                if self.is_auxiliary_duration_supported:
                    data["duration_min"] = self.auxiliary_duration
            elif action == "stop":
                data = {}
            else:
                _LOGGER.error(f"Invalid auxiliary heater action: {action}")
                raise Exception(f"Invalid auxiliary heater action: {action}")
            self._requests["latest"] = "Climatisation"
            response = await self._connection.setAuxiliary(self.vin, data, (action == "start"))
            return await self._handle_response(
                response=response,
                topic="climatisation",
                error_msg=f"Failed to {action} climatisation with auxiliary heater.",
            )
        else:
            _LOGGER.error("No climatisation support.")
            raise Exception("No climatisation support.")

    async def set_departure_timer(self, timer_id, spin, enable) -> bool:
        """Turn on/off departure timer."""
        if self.is_departure_timer_supported(timer_id):
            if type(enable) is not bool:
                _LOGGER.error("Charging departure timers setting is not supported.")
                raise Exception("Charging departure timers setting is not supported.")
            data = None
            response = None
            if is_valid_path(
                self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.timers"
            ) and is_valid_path(self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.profiles"):
                timers = find_path(self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.timers")
                profiles = find_path(
                    self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.profiles"
                )
                for i in range(len(timers)):
                    if timers[i].get("id", 0) == timer_id:
                        timers[i]["enabled"] = enable
                data = {"timers": timers, "profiles": profiles}
                response = await self._connection.setDepartureProfiles(self.vin, data)
            if is_valid_path(self.attrs, f"{Services.CLIMATISATION_TIMERS}.auxiliaryHeatingTimersStatus.value.timers"):
                timers = find_path(
                    self.attrs, f"{Services.CLIMATISATION_TIMERS}.auxiliaryHeatingTimersStatus.value.timers"
                )
                for i in range(len(timers)):
                    if timers[i].get("id", 0) == timer_id:
                        timers[i]["enabled"] = enable
                data = {"spin": spin, "timers": timers}
                response = await self._connection.setAuxiliaryHeatingTimers(self.vin, data)
            if is_valid_path(self.attrs, f"{Services.DEPARTURE_TIMERS}.departureTimersStatus.value.timers"):
                timers = find_path(self.attrs, f"{Services.DEPARTURE_TIMERS}.departureTimersStatus.value.timers")
                for i in range(len(timers)):
                    if timers[i].get("id", 0) == timer_id:
                        timers[i]["enabled"] = enable
                data = {"timers": timers}
                response = await self._connection.setDepartureTimers(self.vin, data)
            return await self._handle_response(
                response=response, topic="departuretimer", error_msg="Failed to change departure timers setting."
            )
        else:
            _LOGGER.error("Departure timers are not supported.")
            raise Exception("Departure timers are not supported.")

    async def set_ac_departure_timer(self, timer_id, enable) -> bool:
        """Turn on/off ac departure timer."""
        if self.is_ac_departure_timer_supported(timer_id):
            if type(enable) is not bool:
                _LOGGER.error("Charging climatisation departure timers setting is not supported.")
                raise Exception("Charging climatisation departure timers setting is not supported.")
            timers = find_path(self.attrs, f"{Services.CLIMATISATION_TIMERS}.climatisationTimersStatus.value.timers")
            for i in range(len(timers)):
                if timers[i].get("id", 0) == timer_id:
                    timers[i]["enabled"] = enable
            data = {"timers": timers}
            response = await self._connection.setClimatisationTimers(self.vin, data)
            return await self._handle_response(
                response=response,
                topic="departuretimer",
                error_msg="Failed to change climatisation departure timers setting.",
            )
        else:
            _LOGGER.error("Climatisation departure timers are not supported.")
            raise Exception("Climatisation departure timers are not supported.")

    # Lock (RLU)
    async def set_lock(self, action, spin):
        """Remote lock and unlock actions."""
        if not self._services.get(Services.ACCESS, {}).get("active", False):
            _LOGGER.info("Remote lock/unlock is not supported.")
            raise Exception("Remote lock/unlock is not supported.")
        if self._in_progress("lock", unknown_offset=-5):
            return False
        if action not in ["lock", "unlock"]:
            _LOGGER.error(f"Invalid lock action: {action}")
            raise Exception(f"Invalid lock action: {action}")

        try:
            self._requests["latest"] = "Lock"
            response = await self._connection.setLock(self.vin, (action == "lock"), spin)
            return await self._handle_response(
                response=response, topic="access", error_msg=f"Failed to {action} vehicle"
            )
        except Exception as error:
            _LOGGER.warning(f"Failed to {action} vehicle - {error}")
            self._requests["lock"] = {"status": "Exception", "timestamp": datetime.now(timezone.utc)}
        raise Exception("Lock action failed")

    # Refresh vehicle data (VSR)
    async def set_refresh(self):
        """Wake up vehicle and update status data."""
        if self._in_progress("refresh", unknown_offset=-5):
            return False
        try:
            self._requests["latest"] = "Refresh"
            response = await self._connection.wakeUpVehicle(self.vin)
            if response:
                if response.status == 204:
                    self._requests["state"] = "in_progress"
                    self._requests["refresh"] = {
                        "timestamp": datetime.now(timezone.utc),
                        "status": "in_progress",
                        "id": 0,
                    }
                    status = await self.wait_for_data_refresh()
                elif response.status == 429:
                    status = "Throttled"
                    _LOGGER.debug("Server side throttled. Try again later.")
                else:
                    _LOGGER.debug(f"Unable to refresh the data. Incorrect response code: {response.status}")
                self._requests["state"] = status
                self._requests["refresh"] = {"status": status, "timestamp": datetime.now(timezone.utc)}
                return True
            else:
                _LOGGER.debug("Unable to refresh the data.")
        except Exception as error:
            _LOGGER.warning(f"Failed to execute data refresh - {error}")
            self._requests["refresh"] = {"status": "Exception", "timestamp": datetime.now(timezone.utc)}
        raise Exception("Data refresh failed")

    # Vehicle class helpers #
    # Vehicle info
    @property
    def attrs(self):
        """
        Return all attributes.

        :return:
        """
        return self._states

    def has_attr(self, attr) -> bool:
        """
        Return true if attribute exists.

        :param attr:
        :return:
        """
        return is_valid_path(self.attrs, attr)

    def get_attr(self, attr):
        """
        Return a specific attribute.

        :param attr:
        :return:
        """
        return find_path(self.attrs, attr)

    async def expired(self, service):
        """Check if access to service has expired."""
        try:
            now = datetime.utcnow()
            if self._services.get(service, {}).get("expiration", False):
                expiration = self._services.get(service, {}).get("expiration", False)
                if not expiration:
                    expiration = datetime.utcnow() + timedelta(days=1)
            else:
                _LOGGER.debug(f"Could not determine end of access for service {service}, assuming it is valid")
                expiration = datetime.utcnow() + timedelta(days=1)
            expiration = expiration.replace(tzinfo=None)
            if now >= expiration:
                _LOGGER.warning(f"Access to {service} has expired!")
                self._discovered = False
                return True
            else:
                return False
        except Exception:
            _LOGGER.debug(f"Exception. Could not determine end of access for service {service}, assuming it is valid")
            return False

    def dashboard(self, **config):
        """
        Return dashboard with specified configuration.

        :param config:
        :return:
        """
        # Classic python notation
        from .vw_dashboard import Dashboard

        return Dashboard(self, **config)

    @property
    def vin(self) -> str:
        """
        Vehicle identification number.

        :return:
        """
        return self._url

    @property
    def unique_id(self) -> str:
        """
        Return unique id for the vehicle (vin).

        :return:
        """
        return self.vin

    # Information from vehicle states #
    # Car information
    @property
    def nickname(self) -> str | None:
        """
        Return nickname of the vehicle.

        :return:
        """
        return self.attrs.get("vehicle", {}).get("nickname", None)

    @property
    def is_nickname_supported(self) -> bool:
        """
        Return true if naming the vehicle is supported.

        :return:
        """
        return self.attrs.get("vehicle", {}).get("nickname", False) is not False

    @property
    def deactivated(self) -> bool | None:
        """
        Return true if service is deactivated.

        :return:
        """
        return self.attrs.get("carData", {}).get("deactivated", None)

    @property
    def is_deactivated_supported(self) -> bool:
        """
        Return true if service deactivation status is supported.

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
    def model_year(self) -> bool | None:
        """Return model year."""
        return self.attrs.get("vehicle", {}).get("modelYear", None)

    @property
    def is_model_year_supported(self) -> bool:
        """Return true if model year is supported."""
        return self.attrs.get("vehicle", {}).get("modelYear", False) is not False

    @property
    def model_image(self) -> str:
        # Not implemented
        """Return vehicle model image."""
        return self.attrs.get("imageUrl")

    @property
    def is_model_image_supported(self) -> bool:
        """
        Return true if vehicle model image is supported.

        :return:
        """
        # Not implemented
        return self.attrs.get("imageUrl", False) is not False

    # Lights
    @property
    def parking_light(self) -> bool:
        """Return true if parking light is on."""
        lights = self.attrs.get(Services.VEHICLE_LIGHTS).get("lightsStatus").get("value").get("lights")
        lights_on_count = 0
        for light in lights:
            if light["status"] == "on":
                lights_on_count = lights_on_count + 1
        return lights_on_count == 1

    @property
    def parking_light_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.attrs.get(Services.VEHICLE_LIGHTS).get("lightsStatus").get("value").get("carCapturedTimestamp")

    @property
    def is_parking_light_supported(self) -> bool:
        """Return true if parking light is supported."""
        return self.attrs.get(Services.VEHICLE_LIGHTS, False) and is_valid_path(
            self.attrs, f"{Services.VEHICLE_LIGHTS}.lightsStatus.value.lights"
        )

    # Connection status
    @property
    def last_connected(self) -> datetime:
        """Return when vehicle was last connected to connect servers in local time."""
        # this field is only a dirty hack, because there is no overarching information for the car anymore,
        # only information per service, so we just use the one for fuelStatus.rangeStatus when car is ideling
        # and charing.batteryStatus when electic car is charging
        """Return attribute last updated timestamp."""
        if self.is_battery_level_supported and self.charging:
            return self.battery_level_last_updated
        elif self.is_distance_supported:
            if type(self.distance_last_updated) is str:
                return (
                    datetime.strptime(self.distance_last_updated, "%Y-%m-%dT%H:%M:%S.%fZ")
                    .replace(microsecond=0)
                    .replace(tzinfo=timezone.utc)
                )
            else:
                return self.distance_last_updated

    @property
    def last_connected_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        if self.is_battery_level_supported and self.charging:
            return self.battery_level_last_updated
        elif self.is_distance_supported:
            if type(self.distance_last_updated) is str:
                return (
                    datetime.strptime(self.distance_last_updated, "%Y-%m-%dT%H:%M:%S.%fZ")
                    .replace(microsecond=0)
                    .replace(tzinfo=timezone.utc)
                )
            else:
                return self.distance_last_updated

    @property
    def is_last_connected_supported(self) -> bool:
        """Return if when vehicle was last connected to connect servers is supported."""
        return self.is_battery_level_supported or self.is_distance_supported

    # Service information
    @property
    def distance(self) -> int | None:
        """Return vehicle odometer."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.odometerStatus.value.odometer")

    @property
    def distance_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.odometerStatus.value.carCapturedTimestamp")

    @property
    def is_distance_supported(self) -> bool:
        """Return true if odometer is supported."""
        return is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.odometerStatus.value.odometer")

    @property
    def service_inspection(self):
        """Return time left for service inspection."""
        return int(
            find_path(self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_days")
        )

    @property
    def service_inspection_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
        )

    @property
    def is_service_inspection_supported(self) -> bool:
        """
        Return true if days to service inspection is supported.

        :return:
        """
        return is_valid_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_days"
        )

    @property
    def service_inspection_distance(self):
        """Return distance left for service inspection."""
        return int(
            find_path(self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_km")
        )

    @property
    def service_inspection_distance_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
        )

    @property
    def is_service_inspection_distance_supported(self) -> bool:
        """
        Return true if distance to service inspection is supported.

        :return:
        """
        return is_valid_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_km"
        )

    @property
    def oil_inspection(self):
        """Return time left for oil inspection."""
        return int(
            find_path(self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.oilServiceDue_days")
        )

    @property
    def oil_inspection_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
        )

    @property
    def is_oil_inspection_supported(self) -> bool:
        """
        Return true if days to oil inspection is supported.

        :return:
        """
        if not self.has_combustion_engine:
            return False
        return is_valid_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.oilServiceDue_days"
        )

    @property
    def oil_inspection_distance(self):
        """Return distance left for oil inspection."""
        return int(
            find_path(self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.oilServiceDue_km")
        )

    @property
    def oil_inspection_distance_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
        )

    @property
    def is_oil_inspection_distance_supported(self) -> bool:
        """
        Return true if oil inspection distance is supported.

        :return:
        """
        if not self.has_combustion_engine:
            return False
        return is_valid_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.oilServiceDue_km"
        )

    @property
    def adblue_level(self) -> int:
        """Return adblue level."""
        return int(find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.adBlueRange"))

    @property
    def adblue_level_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp")

    @property
    def is_adblue_level_supported(self) -> bool:
        """Return true if adblue level is supported."""
        return is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.adBlueRange")

    # Charger related states for EV and PHEV
    @property
    def charging(self) -> bool:
        """Return charging state."""
        cstate = find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargingState")
        return cstate == "charging"

    @property
    def charging_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charging_supported(self) -> bool:
        """Return true if charging is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargingState")

    @property
    def charging_power(self) -> int:
        """Return charging power."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargePower_kW")

    @property
    def charging_power_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charging_power_supported(self) -> bool:
        """Return true if charging power is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargePower_kW")

    @property
    def charging_rate(self) -> int:
        """Return charging rate."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargeRate_kmph")

    @property
    def charging_rate_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charging_rate_supported(self) -> bool:
        """Return true if charging rate is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargeRate_kmph")

    @property
    def charger_type(self) -> str:
        """Return charger type."""
        charger_type = find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargeType")
        if charger_type == "ac":
            return "AC"
        elif charger_type == "dc":
            return "DC"
        return "Unknown"

    @property
    def charger_type_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charger_type_supported(self) -> bool:
        """Return true if charger type is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargeType")

    @property
    def battery_level(self) -> int:
        """Return battery level."""
        return int(find_path(self.attrs, f"{Services.CHARGING}.batteryStatus.value.currentSOC_pct"))

    @property
    def battery_level_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.batteryStatus.value.carCapturedTimestamp")

    @property
    def is_battery_level_supported(self) -> bool:
        """Return true if battery level is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.batteryStatus.value.currentSOC_pct")

    @property
    def battery_target_charge_level(self) -> int:
        """Return target charge level."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.targetSOC_pct")

    @property
    def battery_target_charge_level_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.carCapturedTimestamp")

    @property
    def is_battery_target_charge_level_supported(self) -> bool:
        """Return true if target charge level is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.targetSOC_pct")

    @property
    def charge_max_ac_setting(self) -> str | int:
        """Return charger max ampere setting."""
        value = find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC")
        return value

    @property
    def charge_max_ac_setting_last_updated(self) -> datetime:
        """Return charger max ampere last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.carCapturedTimestamp")

    @property
    def is_charge_max_ac_setting_supported(self) -> bool:
        """Return true if Charger Max Ampere is supported."""
        if is_valid_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC"):
            value = find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC")
            return value in ["reduced", "maximum", "invalid"]
        return False

    @property
    def charge_max_ac_ampere(self) -> str | int:
        """Return charger max ampere setting."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC_A")

    @property
    def charge_max_ac_ampere_last_updated(self) -> datetime:
        """Return charger max ampere last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.carCapturedTimestamp")

    @property
    def is_charge_max_ac_ampere_supported(self) -> bool:
        """Return true if Charger Max Ampere is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC_A")

    @property
    def charging_cable_locked(self) -> bool:
        """Return plug locked state."""
        response = find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.plugLockState")
        return response == "locked"

    @property
    def charging_cable_locked_last_updated(self) -> datetime:
        """Return plug locked state."""
        return find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.carCapturedTimestamp")

    @property
    def is_charging_cable_locked_supported(self) -> bool:
        """Return true if plug locked state is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.plugLockState")

    @property
    def charging_cable_connected(self) -> bool:
        """Return plug connected state."""
        response = find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.plugConnectionState")
        return response == "connected"

    @property
    def charging_cable_connected_last_updated(self) -> datetime:
        """Return plug connected state last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.carCapturedTimestamp")

    @property
    def is_charging_cable_connected_supported(self) -> bool:
        """Return true if supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.plugConnectionState")

    @property
    def charging_time_left(self) -> int:
        """Return minutes to charging complete."""
        if is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.remainingChargingTimeToComplete_min"):
            return int(
                find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.remainingChargingTimeToComplete_min")
            )
        return None

    @property
    def charging_time_left_last_updated(self) -> datetime:
        """Return minutes to charging complete last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charging_time_left_supported(self) -> bool:
        """Return true if charging is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargingState")

    @property
    def external_power(self) -> bool:
        """Return true if external power is connected."""
        check = find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.externalPower")
        return check in ["stationConnected", "available", "ready"]

    @property
    def external_power_last_updated(self) -> datetime:
        """Return external power last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.carCapturedTimestamp")

    @property
    def is_external_power_supported(self) -> bool:
        """External power supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.externalPower")

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
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.autoUnlockPlugWhenChargedAC")

    @property
    def auto_release_ac_connector(self) -> bool:
        """Return auto release ac connector state."""
        return (
            find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.autoUnlockPlugWhenChargedAC")
            == "permanent"
        )

    @property
    def auto_release_ac_connector_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.carCapturedTimestamp")

    @property
    def is_auto_release_ac_connector_supported(self) -> bool:
        """Return true if auto release ac connector is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.autoUnlockPlugWhenChargedAC")

    @property
    def battery_care_mode(self) -> bool:
        """Return battery care mode state."""
        return (
            find_path(self.attrs, f"{Services.BATTERY_CHARGING_CARE}.chargingCareSettings.value.batteryCareMode")
            == "activated"
        )

    @property
    def battery_care_mode_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_battery_care_mode_supported(self) -> bool:
        """Return true if battery care mode is supported."""
        return is_valid_path(self.attrs, f"{Services.BATTERY_CHARGING_CARE}.chargingCareSettings.value.batteryCareMode")

    @property
    def optimised_battery_use(self) -> bool:
        """Return optimised battery use state."""
        return (
            find_path(self.attrs, f"{Services.BATTERY_SUPPORT}.batterySupportStatus.value.batterySupport") == "enabled"
        )

    @property
    def optimised_battery_use_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_optimised_battery_use_supported(self) -> bool:
        """Return true if optimised battery use is supported."""
        return is_valid_path(self.attrs, f"{Services.BATTERY_SUPPORT}.batterySupportStatus.value.batterySupport")

    @property
    def energy_flow(self):
        # TODO untouched
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
        # TODO untouched
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
        # TODO untouched
        """Energy flow supported."""
        return self.attrs.get("charger", {}).get("status", {}).get("chargingStatusData", {}).get("energyFlow", False)

    # Vehicle location states
    @property
    def position(self) -> dict[str, str | float | None]:
        """Return  position."""
        output: dict[str, str | float | None]
        try:
            if self.vehicle_moving:
                output = {"lat": None, "lng": None, "timestamp": None}
            else:
                lat = float(find_path(self.attrs, "parkingposition.lat"))
                lng = float(find_path(self.attrs, "parkingposition.lon"))
                parking_time = find_path(self.attrs, "parkingposition.carCapturedTimestamp")
                output = {"lat": lat, "lng": lng, "timestamp": parking_time}
        except Exception:
            output = {
                "lat": "?",
                "lng": "?",
            }
        return output

    @property
    def position_last_updated(self) -> datetime:
        """Return  position last updated."""
        return self.attrs.get("parkingposition", {}).get("carCapturedTimestamp", "Unknown")

    @property
    def is_position_supported(self) -> bool:
        """Return true if position is available."""
        return is_valid_path(self.attrs, "parkingposition.carCapturedTimestamp") or self.attrs.get("isMoving", False)

    @property
    def vehicle_moving(self) -> bool:
        """Return true if vehicle is moving."""
        return self.attrs.get("isMoving", False)

    @property
    def vehicle_moving_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.position_last_updated

    @property
    def is_vehicle_moving_supported(self) -> bool:
        """Return true if vehicle supports position."""
        return self.is_position_supported

    @property
    def parking_time(self) -> datetime:
        """Return timestamp of last parking time."""
        parking_time_path = "parkingposition.carCapturedTimestamp"
        if is_valid_path(self.attrs, parking_time_path):
            return find_path(self.attrs, parking_time_path)
        return None

    @property
    def parking_time_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.position_last_updated

    @property
    def is_parking_time_supported(self) -> bool:
        """Return true if vehicle parking timestamp is supported."""
        return self.is_position_supported

    # Vehicle fuel level and range
    @property
    def electric_range(self) -> int:
        """
        Return electric range.

        :return:
        """
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.electricRange"):
            return int(find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.electricRange"))
        return int(find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.remainingRange_km"))

    @property
    def electric_range_last_updated(self) -> datetime:
        """Return electric range last updated."""
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp"):
            return find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp")
        return find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carCapturedTimestamp")

    @property
    def is_electric_range_supported(self) -> bool:
        """
        Return true if electric range is supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.electricRange") or (
            self.is_car_type_electric
            and is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.remainingRange_km")
        )

    @property
    def combustion_range(self) -> int:
        """
        Return combustion engine range.

        :return:
        """
        DIESEL_RANGE = f"{Services.MEASUREMENTS}.rangeStatus.value.dieselRange"
        GASOLINE_RANGE = f"{Services.MEASUREMENTS}.rangeStatus.value.gasolineRange"
        if is_valid_path(self.attrs, DIESEL_RANGE):
            return int(find_path(self.attrs, DIESEL_RANGE))
        if is_valid_path(self.attrs, GASOLINE_RANGE):
            return int(find_path(self.attrs, GASOLINE_RANGE))
        return -1

    @property
    def combustion_range_last_updated(self) -> datetime | None:
        """Return combustion engine range last updated."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp")

    @property
    def is_combustion_range_supported(self) -> bool:
        """
        Return true if combustion range is supported, i.e. false for EVs.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.dieselRange") or is_valid_path(
            self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.gasolineRange"
        )

    @property
    def combined_range(self) -> int:
        """
        Return combined range.

        :return:
        """
        return int(find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.totalRange_km"))

    @property
    def combined_range_last_updated(self) -> datetime | None:
        """Return combined range last updated."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp")

    @property
    def is_combined_range_supported(self) -> bool:
        """
        Return true if combined range is supported.

        :return:
        """
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.totalRange_km"):
            return self.is_electric_range_supported and self.is_combustion_range_supported
        return False

    @property
    def battery_cruising_range(self) -> int:
        """
        Return battery cruising range.

        :return:
        """
        return int(find_path(self.attrs, f"{Services.CHARGING}.batteryStatus.value.cruisingRangeElectric_km"))

    @property
    def battery_cruising_range_last_updated(self) -> datetime | None:
        """Return battery cruising range last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.batteryStatus.value.carCapturedTimestamp")

    @property
    def is_battery_cruising_range_supported(self) -> bool:
        """
        Return true if battery cruising range is supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.CHARGING}.batteryStatus.value.cruisingRangeElectric_km")

    @property
    def fuel_level(self) -> int:
        """
        Return fuel level.

        :return:
        """
        fuel_level_pct = ""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.currentFuelLevel_pct"):
            fuel_level_pct = find_path(
                self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.currentFuelLevel_pct"
            )

        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.currentFuelLevel_pct"):
            fuel_level_pct = find_path(
                self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.currentFuelLevel_pct"
            )
        return int(fuel_level_pct)

    @property
    def fuel_level_last_updated(self) -> datetime:
        """Return fuel level last updated."""
        fuel_level_lastupdated = ""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carCapturedTimestamp"):
            fuel_level_lastupdated = find_path(
                self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carCapturedTimestamp"
            )

        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carCapturedTimestamp"):
            fuel_level_lastupdated = find_path(
                self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carCapturedTimestamp"
            )
        return fuel_level_lastupdated

    @property
    def is_fuel_level_supported(self) -> bool:
        """
        Return true if fuel level reporting is supported.

        :return:
        """
        return is_valid_path(
            self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.currentFuelLevel_pct"
        ) or is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.currentFuelLevel_pct")

    @property
    def car_type(self) -> str:
        """
        Return car type.

        :return:
        """
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType"):
            return find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType").capitalize()
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType"):
            return find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType").capitalize()
        return "Unknown"

    @property
    def car_type_last_updated(self) -> datetime | None:
        """Return car type last updated."""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carCapturedTimestamp"):
            return find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carCapturedTimestamp")
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carCapturedTimestamp"):
            return find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carCapturedTimestamp")
        return None

    @property
    def is_car_type_supported(self) -> bool:
        """
        Return true if car type is supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType") or is_valid_path(
            self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType"
        )

    # Climatisation settings
    @property
    def climatisation_target_temperature(self) -> float | None:
        """Return the target temperature from climater."""
        # TODO should we handle Fahrenheit??
        return float(find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.targetTemperature_C"))

    @property
    def climatisation_target_temperature_last_updated(self) -> datetime:
        """Return the target temperature from climater last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp")

    @property
    def is_climatisation_target_temperature_supported(self) -> bool:
        """Return true if climatisation target temperature is supported."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.targetTemperature_C")

    @property
    def climatisation_without_external_power(self):
        """Return state of climatisation from battery power."""
        return find_path(
            self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.climatisationWithoutExternalPower"
        )

    @property
    def climatisation_without_external_power_last_updated(self) -> datetime:
        """Return state of climatisation from battery power last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp")

    @property
    def is_climatisation_without_external_power_supported(self) -> bool:
        """Return true if climatisation on battery power is supported."""
        return is_valid_path(
            self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.climatisationWithoutExternalPower"
        )

    @property
    def auxiliary_air_conditioning(self):
        """Return state of auxiliary air conditioning."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.climatizationAtUnlock")

    @property
    def auxiliary_air_conditioning_last_updated(self) -> datetime:
        """Return state of auxiliary air conditioning last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp")

    @property
    def is_auxiliary_air_conditioning_supported(self) -> bool:
        """Return true if auxiliary air conditioning is supported."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.climatizationAtUnlock")

    @property
    def automatic_window_heating(self):
        """Return state of automatic window heating."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.windowHeatingEnabled")

    @property
    def automatic_window_heating_last_updated(self) -> datetime:
        """Return state of automatic window heating last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp")

    @property
    def is_automatic_window_heating_supported(self) -> bool:
        """Return true if automatic window heating is supported."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.windowHeatingEnabled")

    @property
    def zone_front_left(self):
        """Return state of zone front left."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.zoneFrontLeftEnabled")

    @property
    def zone_front_left_last_updated(self) -> datetime:
        """Return state of zone front left last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp")

    @property
    def is_zone_front_left_supported(self) -> bool:
        """Return true if zone front left is supported."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.zoneFrontLeftEnabled")

    @property
    def zone_front_right(self):
        """Return state of zone front left."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.zoneFrontRightEnabled")

    @property
    def zone_front_right_last_updated(self) -> datetime:
        """Return state of zone front left last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp")

    @property
    def is_zone_front_right_supported(self) -> bool:
        """Return true if zone front left is supported."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.zoneFrontRightEnabled")

    # Climatisation, electric
    @property
    def electric_climatisation(self) -> bool:
        """Return status of climatisation."""
        status = find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.climatisationState")
        return status in ["ventilation", "heating", "cooling", "on"]

    @property
    def electric_climatisation_last_updated(self) -> datetime:
        """Return status of climatisation last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.carCapturedTimestamp")

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
        return find_path(
            self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.remainingClimatisationTime_min"
        )

    @property
    def electric_remaining_climatisation_time_last_updated(self) -> bool:
        """Return status of electric climatisation remaining climatisation time last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.carCapturedTimestamp")

    @property
    def is_electric_remaining_climatisation_time_supported(self) -> bool:
        """Return true if electric climatisation remaining climatisation time is supported."""
        return is_valid_path(
            self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.remainingClimatisationTime_min"
        )

    @property
    def auxiliary_climatisation(self) -> bool:
        """Return status of auxiliary climatisation."""
        climatisation_state = None
        if is_valid_path(self.attrs, f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.climatisationState"):
            climatisation_state = find_path(
                self.attrs, f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.climatisationState"
            )
        if is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.climatisationState"):
            climatisation_state = find_path(
                self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.climatisationState"
            )
        if climatisation_state in ["heating", "heatingAuxiliary", "on"]:
            return True
        return False

    @property
    def auxiliary_climatisation_last_updated(self) -> datetime:
        """Return status of auxiliary climatisation last updated."""
        if is_valid_path(self.attrs, f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.carCapturedTimestamp"):
            return find_path(self.attrs, f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.carCapturedTimestamp")
        if is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.carCapturedTimestamp"):
            return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.carCapturedTimestamp")
        return None

    @property
    def is_auxiliary_climatisation_supported(self) -> bool:
        """Return true if vehicle has auxiliary climatisation."""
        if is_valid_path(self.attrs, f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.climatisationState"):
            return True
        if is_valid_path(self.attrs, f"{Services.USER_CAPABILITIES}.capabilitiesStatus.value"):
            capabilities = find_path(self.attrs, f"{Services.USER_CAPABILITIES}.capabilitiesStatus.value")
            for capability in capabilities:
                if capability.get("id", None) == "hybridCarAuxiliaryHeating":
                    if 1007 in capability.get("status", []):
                        return False
                    else:
                        return True
        return False

    @property
    def auxiliary_duration(self) -> int:
        """Return heating duration for auxiliary heater."""
        return find_path(
            self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.auxiliaryHeatingSettings.duration_min"
        )

    @property
    def auxiliary_duration_last_updated(self) -> bool:
        """Return status of auxiliary heater last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp")

    @property
    def is_auxiliary_duration_supported(self) -> bool:
        """Return true if auxiliary heater is supported."""
        return is_valid_path(
            self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.auxiliaryHeatingSettings.duration_min"
        )

    @property
    def auxiliary_remaining_climatisation_time(self) -> int:
        """Return remaining climatisation time for auxiliary heater."""
        return find_path(
            self.attrs, f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.remainingClimatisationTime_min"
        )

    @property
    def auxiliary_remaining_climatisation_time_last_updated(self) -> bool:
        """Return status of auxiliary heater remaining climatisation time last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.carCapturedTimestamp")

    @property
    def is_auxiliary_remaining_climatisation_time_supported(self) -> bool:
        """Return true if auxiliary heater remaining climatisation time is supported."""
        return is_valid_path(
            self.attrs, f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.remainingClimatisationTime_min"
        )

    @property
    def is_climatisation_supported(self) -> bool:
        """Return true if climatisation has State."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.climatisationState")

    @property
    def is_climatisation_supported_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.carCapturedTimestamp")

    @property
    def window_heater_front(self) -> bool:
        """Return status of front window heater."""
        window_heating_status = find_path(
            self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.windowHeatingStatus"
        )
        for window_heating_state in window_heating_status:
            if window_heating_state["windowLocation"] == "front":
                return window_heating_state["windowHeatingState"] == "on"

        return False

    @property
    def window_heater_front_last_updated(self) -> datetime:
        """Return front window heater last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.carCapturedTimestamp")

    @property
    def is_window_heater_front_supported(self) -> bool:
        """Return true if vehicle has heater."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.windowHeatingStatus")

    @property
    def window_heater_back(self) -> bool:
        """Return status of rear window heater."""
        window_heating_status = find_path(
            self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.windowHeatingStatus"
        )
        for window_heating_state in window_heating_status:
            if window_heating_state["windowLocation"] == "rear":
                return window_heating_state["windowHeatingState"] == "on"

        return False

    @property
    def window_heater_back_last_updated(self) -> datetime:
        """Return front window heater last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.carCapturedTimestamp")

    @property
    def is_window_heater_back_supported(self) -> bool:
        """Return true if vehicle has heater."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.windowHeatingStatus")

    @property
    def window_heater(self) -> bool:
        """Return status of window heater."""
        return self.window_heater_front or self.window_heater_back

    @property
    def window_heater_last_updated(self) -> datetime:
        """Return front window heater last updated."""
        return self.window_heater_front_last_updated

    @property
    def is_window_heater_supported(self) -> bool:
        """Return true if vehicle has heater."""
        # ID models detection
        if self._services.get(Services.PARAMETERS, {}).get("supportsStartWindowHeating", "false") == "true":
            return True
        # "Legacy" models detection
        parameters = self._services.get(Services.CLIMATISATION, {}).get("parameters", None)
        if parameters:
            for parameter in parameters:
                if parameter["key"] == "supportsStartWindowHeating" and parameter["value"] == "true":
                    return True
        return False

    # Windows
    @property
    def windows_closed(self) -> bool:
        """
        Return true if all supported windows are closed.

        :return:
        """
        return (
            (not self.is_window_closed_left_front_supported or self.window_closed_left_front)
            and (not self.is_window_closed_left_back_supported or self.window_closed_left_back)
            and (not self.is_window_closed_right_front_supported or self.window_closed_right_front)
            and (not self.is_window_closed_right_back_supported or self.window_closed_right_back)
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

    @property
    def window_closed_left_front(self) -> bool:
        """
        Return left front window closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "frontLeft":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def window_closed_left_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_window_closed_left_front_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "frontLeft" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def window_closed_right_front(self) -> bool:
        """
        Return right front window closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "frontRight":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def window_closed_right_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_window_closed_right_front_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "frontRight" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def window_closed_left_back(self) -> bool:
        """
        Return left back window closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "rearLeft":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def window_closed_left_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_window_closed_left_back_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "rearLeft" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def window_closed_right_back(self) -> bool:
        """
        Return right back window closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "rearRight":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def window_closed_right_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_window_closed_right_back_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "rearRight" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def sunroof_closed(self) -> bool:
        """
        Return sunroof closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "sunRoof":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def sunroof_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_sunroof_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "sunRoof" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def sunroof_rear_closed(self) -> bool:
        """
        Return sunroof rear closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "sunRoofRear":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def sunroof_rear_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_sunroof_rear_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "sunRoofRear" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def roof_cover_closed(self) -> bool:
        """
        Return roof cover closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "roofCover":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def roof_cover_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_roof_cover_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "roofCover" and "unsupported" not in window["status"]:
                    return True
        return False

    # Locks
    @property
    def door_locked_sensor(self) -> bool:
        """Return same state as lock entity, since they are mutually exclusive."""
        return self.door_locked

    @property
    def door_locked(self) -> bool:
        """
        Return true if all doors are locked.

        :return:
        """
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doorLockStatus") == "locked"

    @property
    def door_locked_last_updated(self) -> datetime:
        """Return door lock last updated."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def door_locked_sensor_last_updated(self) -> datetime:
        """Return door lock last updated."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_locked_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        # First check that the service is actually enabled
        if not self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        return is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doorLockStatus")

    @property
    def is_door_locked_sensor_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        # Use real lock if the service is actually enabled
        if self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        return is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doorLockStatus")

    @property
    def trunk_locked(self) -> bool:
        """
        Return trunk locked state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "trunk":
                return "locked" in door["status"]
        return False

    @property
    def trunk_locked_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_trunk_locked_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        if not self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "trunk" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def trunk_locked_sensor(self) -> bool:
        """
        Return trunk locked state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "trunk":
                return "locked" in door["status"]
        return False

    @property
    def trunk_locked_sensor_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_trunk_locked_sensor_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        if self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "trunk" and "unsupported" not in door["status"]:
                    return True
        return False

    # Doors, hood and trunk
    @property
    def hood_closed(self) -> bool:
        """
        Return hood closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "bonnet":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def hood_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_hood_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "bonnet" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def door_closed_left_front(self) -> bool:
        """
        Return left front door closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "frontLeft":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def door_closed_left_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_closed_left_front_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "frontLeft" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def door_closed_right_front(self) -> bool:
        """
        Return right front door closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "frontRight":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def door_closed_right_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_closed_right_front_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "frontRight" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def door_closed_left_back(self) -> bool:
        """
        Return left back door closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "rearLeft":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def door_closed_left_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_closed_left_back_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "rearLeft" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def door_closed_right_back(self) -> bool:
        """
        Return right back door closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "rearRight":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def door_closed_right_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_closed_right_back_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "rearRight" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def trunk_closed(self) -> bool:
        """
        Return trunk closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "trunk":
                return "closed" in door["status"]
        return False

    @property
    def trunk_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_trunk_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "trunk" and "unsupported" not in door["status"]:
                    return True
        return False

    # Departure timers
    @property
    def departure_timer1(self):
        """Return timer #1 status."""
        return self.departure_timer_enabled(1)

    @property
    def departure_timer2(self):
        """Return timer #2 status."""
        return self.departure_timer_enabled(2)

    @property
    def departure_timer3(self):
        """Return timer #3 status."""
        return self.departure_timer_enabled(3)

    @property
    def departure_timer1_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        if is_valid_path(
            self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.carCapturedTimestamp"
        ):
            return find_path(
                self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.carCapturedTimestamp"
            )
        if is_valid_path(
            self.attrs, f"{Services.CLIMATISATION_TIMERS}.auxiliaryHeatingTimersStatus.value.carCapturedTimestamp"
        ):
            return find_path(
                self.attrs, f"{Services.CLIMATISATION_TIMERS}.auxiliaryHeatingTimersStatus.value.carCapturedTimestamp"
            )
        if is_valid_path(self.attrs, f"{Services.DEPARTURE_TIMERS}.departureTimersStatus.value.carCapturedTimestamp"):
            return find_path(
                self.attrs, f"{Services.DEPARTURE_TIMERS}.departureTimersStatus.value.carCapturedTimestamp"
            )
        return None

    @property
    def departure_timer2_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return self.departure_timer1_last_updated

    @property
    def departure_timer3_last_updated(self) -> datetime:
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
        return self.departure_timer(timer_id).get("enabled", False)

    def is_departure_timer_supported(self, timer_id: str | int) -> bool:
        """Return true if departure timer is supported."""
        return self.departure_timer(timer_id) is not None

    def timer_attributes(self, timer_id: str | int):
        """Return departure timer attributes."""
        timer = self.departure_timer(timer_id)
        profile = self.departure_profile(timer.get("profileIDs", [0])[0])
        timer_type = None
        recurring_on = []
        start_time = None
        if timer.get("singleTimer", None):
            timer_type = "single"
            if timer.get("singleTimer", None).get("startDateTime", None):
                start_date_time = timer.get("singleTimer", None).get("startDateTime", None)
                start_time = (
                    start_date_time.replace(tzinfo=timezone.utc).astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S")
                )
            if timer.get("singleTimer", None).get("startDateTimeLocal", None):
                start_date_time = timer.get("singleTimer", None).get("startDateTimeLocal", None)
                if type(start_date_time) is str:
                    start_date_time = datetime.strptime(start_date_time, "%Y-%m-%dT%H:%M:%S")
                start_time = start_date_time
            if timer.get("singleTimer", None).get("departureDateTimeLocal", None):
                start_date_time = timer.get("singleTimer", None).get("departureDateTimeLocal", None)
                if type(start_date_time) is str:
                    start_date_time = datetime.strptime(start_date_time, "%Y-%m-%dT%H:%M:%S")
                start_time = start_date_time
        elif timer.get("recurringTimer", None):
            timer_type = "recurring"
            if timer.get("recurringTimer", None).get("startTime", None):
                start_date_time = timer.get("recurringTimer", None).get("startTime", None)
                start_time = (
                    datetime.strptime(start_date_time, "%H:%M")
                    .replace(tzinfo=timezone.utc)
                    .astimezone(tz=None)
                    .strftime("%H:%M")
                )
            if timer.get("recurringTimer", None).get("startTimeLocal", None):
                start_date_time = timer.get("recurringTimer", None).get("startTimeLocal", None)
                start_time = datetime.strptime(start_date_time, "%H:%M").strftime("%H:%M")
            if timer.get("recurringTimer", None).get("departureTimeLocal", None):
                start_date_time = timer.get("recurringTimer", None).get("departureTimeLocal", None)
                start_time = datetime.strptime(start_date_time, "%H:%M").strftime("%H:%M")
            recurring_days = timer.get("recurringTimer", None).get("recurringOn", None)
            for day in recurring_days:
                if recurring_days.get(day) is True:
                    recurring_on.append(day)
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
            data["preferred_charging_start_time"] = preferred_charging_times.get("startTimeLocal", None)
            data["preferred_charging_end_time"] = preferred_charging_times.get("endTimeLocal", None)
        return data

    def departure_timer(self, timer_id: str | int):
        """Return departure timer."""
        if is_valid_path(self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.timers"):
            timers = find_path(self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.timers")
            for timer in timers:
                if timer.get("id", 0) == timer_id:
                    return timer
        if is_valid_path(self.attrs, f"{Services.CLIMATISATION_TIMERS}.auxiliaryHeatingTimersStatus.value.timers"):
            timers = find_path(self.attrs, f"{Services.CLIMATISATION_TIMERS}.auxiliaryHeatingTimersStatus.value.timers")
            for timer in timers:
                if timer.get("id", 0) == timer_id:
                    return timer
        if is_valid_path(self.attrs, f"{Services.DEPARTURE_TIMERS}.departureTimersStatus.value.timers"):
            timers = find_path(self.attrs, f"{Services.DEPARTURE_TIMERS}.departureTimersStatus.value.timers")
            for timer in timers:
                if timer.get("id", 0) == timer_id:
                    return timer
        return None

    def departure_profile(self, profile_id: str | int):
        """Return departure profile."""
        if is_valid_path(self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.profiles"):
            profiles = find_path(self.attrs, f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.profiles")
            for profile in profiles:
                if profile.get("id", 0) == profile_id:
                    return profile
        return None

    # AC Departure timers
    @property
    def ac_departure_timer1(self):
        """Return ac timer #1 status."""
        return self.ac_departure_timer_enabled(1)

    @property
    def ac_departure_timer2(self):
        """Return ac timer #2 status."""
        return self.ac_departure_timer_enabled(2)

    @property
    def ac_departure_timer1_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(
            self.attrs, f"{Services.CLIMATISATION_TIMERS}.climatisationTimersStatus.value.carCapturedTimestamp"
        )

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
        return self.ac_departure_timer(timer_id).get("enabled", False)

    def is_ac_departure_timer_supported(self, timer_id: str | int) -> bool:
        """Return true if ac departure timer is supported."""
        return self.ac_departure_timer(timer_id) is not None

    def ac_departure_timer(self, timer_id: str | int):
        """Return ac departure timer."""
        if is_valid_path(self.attrs, f"{Services.CLIMATISATION_TIMERS}.climatisationTimersStatus.value.timers"):
            timers = find_path(self.attrs, f"{Services.CLIMATISATION_TIMERS}.climatisationTimersStatus.value.timers")
            for timer in timers:
                if timer.get("id", 0) == timer_id:
                    return timer
        return None

    def ac_timer_attributes(self, timer_id: str | int):
        """Return ac departure timer attributes."""
        timer = self.ac_departure_timer(timer_id)
        timer_type = None
        recurring_on = []
        start_time = None
        if timer.get("singleTimer", None):
            timer_type = "single"
            start_date_time = timer.get("singleTimer", None).get("startDateTime", None)
            start_time = start_date_time.replace(tzinfo=timezone.utc).astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S")
        elif timer.get("recurringTimer", None):
            timer_type = "recurring"
            start_date_time = timer.get("recurringTimer", None).get("startTime", None)
            start_time = (
                datetime.strptime(start_date_time, "%H:%M")
                .replace(tzinfo=timezone.utc)
                .astimezone(tz=None)
                .strftime("%H:%M")
            )
            recurring_days = timer.get("recurringTimer", None).get("recurringOn", None)
            for day in recurring_days:
                if recurring_days.get(day) is True:
                    recurring_on.append(day)
        data = {
            "timer_id": timer.get("id", None),
            "timer_type": timer_type,
            "start_time": start_time,
            "recurring_on": recurring_on,
        }
        return data

    # Trip data
    @property
    def trip_last_entry(self):
        """
        Return last trip data entry.

        :return:
        """
        return self.attrs.get(Services.TRIP_LAST, {})

    @property
    def trip_last_average_speed(self):
        """
        Return last trip average speed.

        :return:
        """
        return find_path(self.attrs, f"{Services.TRIP_LAST}.averageSpeed_kmph")

    @property
    def trip_last_average_speed_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_speed_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageSpeed_kmph") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageSpeed_kmph")
        ) in (float, int)

    @property
    def trip_last_average_electric_engine_consumption(self):
        """
        Return last trip average electric consumption.

        :return:
        """
        return float(find_path(self.attrs, f"{Services.TRIP_LAST}.averageElectricConsumption"))

    @property
    def trip_last_average_electric_engine_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_electric_engine_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageElectricConsumption") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageElectricConsumption")
        ) in (float, int)

    @property
    def trip_last_average_fuel_consumption(self):
        """
        Return last trip average fuel consumption.

        :return:
        """
        return float(find_path(self.attrs, f"{Services.TRIP_LAST}.averageFuelConsumption"))

    @property
    def trip_last_average_fuel_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_fuel_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageFuelConsumption") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageFuelConsumption")
        ) in (float, int)

    @property
    def trip_last_average_auxillary_consumption(self):
        """
        Return last trip average auxiliary consumption.

        :return:
        """
        # no example verified yet
        return self.trip_last_entry.get("averageAuxiliaryConsumption")

    @property
    def trip_last_average_auxillary_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_auxillary_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageAuxiliaryConsumption") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageAuxiliaryConsumption")
        ) in (float, int)

    @property
    def trip_last_average_aux_consumer_consumption(self):
        """
        Return last trip average auxiliary consumer consumption.

        :return:
        """
        # no example verified yet
        return self.trip_last_entry.get("averageAuxConsumerConsumption")

    @property
    def trip_last_average_aux_consumer_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_aux_consumer_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageAuxConsumerConsumption") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageAuxConsumerConsumption")
        ) in (float, int)

    @property
    def trip_last_duration(self):
        """
        Return last trip duration in minutes(?).

        :return:
        """
        return find_path(self.attrs, f"{Services.TRIP_LAST}.travelTime")

    @property
    def trip_last_duration_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_duration_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.travelTime") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.travelTime")
        ) in (float, int)

    @property
    def trip_last_length(self):
        """
        Return last trip length.

        :return:
        """
        return find_path(self.attrs, f"{Services.TRIP_LAST}.mileage_km")

    @property
    def trip_last_length_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_length_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.mileage_km") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.mileage_km")
        ) in (float, int)

    @property
    def trip_last_recuperation(self):
        """
        Return last trip recuperation.

        :return:
        """
        # Not implemented
        return self.trip_last_entry.get("recuperation")

    @property
    def trip_last_recuperation_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_recuperation_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        # Not implemented
        response = self.trip_last_entry
        return response and type(response.get("recuperation", None)) in (float, int)

    @property
    def trip_last_average_recuperation(self):
        """
        Return last trip total recuperation.

        :return:
        """
        return self.trip_last_entry.get("averageRecuperation")

    @property
    def trip_last_average_recuperation_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_recuperation_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        response = self.trip_last_entry
        return response and type(response.get("averageRecuperation", None)) in (float, int)

    @property
    def trip_last_total_electric_consumption(self):
        """
        Return last trip total electric consumption.

        :return:
        """
        # Not implemented
        return self.trip_last_entry.get("totalElectricConsumption")

    @property
    def trip_last_total_electric_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_total_electric_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        # Not implemented
        response = self.trip_last_entry
        return response and type(response.get("totalElectricConsumption", None)) in (float, int)

    # Status of set data requests
    @property
    def refresh_action_status(self):
        """Return latest status of data refresh request."""
        return self._requests.get("refresh", {}).get("status", "None")

    @property
    def charger_action_status(self):
        """Return latest status of charger request."""
        return self._requests.get("batterycharge", {}).get("status", "None")

    @property
    def climater_action_status(self):
        """Return latest status of climater request."""
        return self._requests.get("climatisation", {}).get("status", "None")

    @property
    def lock_action_status(self):
        """Return latest status of lock action request."""
        return self._requests.get("lock", {}).get("status", "None")

    # Requests data
    @property
    def refresh_data(self):
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
            for section in self._requests:
                return self._requests[section].get("id", False)
        except Exception as e:
            _LOGGER.warning(e)
        return False

    @property
    def request_in_progress_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        try:
            for section in self._requests:
                return self._requests[section].get("timestamp")
        except Exception as e:
            _LOGGER.warning(e)
        return datetime.now(timezone.utc)

    @property
    def is_request_in_progress_supported(self):
        """Request in progress is always supported."""
        return True

    @property
    def request_results(self) -> dict:
        """Get last request result."""
        data = {"latest": self._requests.get("latest", None), "state": self._requests.get("state", None)}
        for section in self._requests:
            if section in ["departuretimer", "batterycharge", "climatisation", "refresh", "lock"]:
                data[section] = self._requests[section].get("status", "Unknown")
        return data

    @property
    def request_results_last_updated(self) -> datetime | None:
        """Get last updated time."""
        if self._requests.get("latest", "") != "":
            return self._requests.get(str(self._requests.get("latest")), {}).get("timestamp")
        # all requests should have more or less the same timestamp anyway, so
        # just return the first one
        for section in ["departuretimer", "batterycharge", "climatisation", "refresh", "lock"]:
            if section in self._requests:
                return self._requests[section].get("timestamp")
        return None

    @property
    def is_request_results_supported(self):
        """Request results is supported if in progress is supported."""
        return self.is_request_in_progress_supported

    @property
    def requests_results_last_updated(self):
        """Return last updated timestamp for attribute."""
        return None

    # Helper functions #
    def __str__(self):
        """Return the vin."""
        return self.vin

    @property
    def json(self):
        """
        Return vehicle data in JSON format.

        :return:
        """

        def serialize(obj):
            """
            Convert datetime instances back to JSON compatible format.

            :param obj:
            :return:
            """
            return obj.isoformat() if isinstance(obj, datetime) else obj

        return to_json(OrderedDict(sorted(self.attrs.items())), indent=4, default=serialize)

    def is_primary_drive_electric(self):
        """Check if primary engine is electric."""
        return (
            find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType")
            == ENGINE_TYPE_ELECTRIC
        )

    def is_secondary_drive_electric(self):
        """Check if secondary engine is electric."""
        return (
            is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType")
            and find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType")
            == ENGINE_TYPE_ELECTRIC
        )

    def is_primary_drive_combustion(self):
        """Check if primary engine is combustion."""
        engine_type = ""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.type"):
            engine_type = find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.type")

        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType"):
            engine_type = find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType")

        return engine_type in ENGINE_TYPE_COMBUSTION

    def is_secondary_drive_combustion(self):
        """Check if secondary engine is combustion."""
        engine_type = ""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.secondaryEngine.type"):
            engine_type = find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.secondaryEngine.type")

        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.secondaryEngineType"):
            engine_type = find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.secondaryEngineType")

        return engine_type in ENGINE_TYPE_COMBUSTION

    @property
    def is_car_type_electric(self):
        """Check if car type is electric."""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType"):
            return find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType") == ENGINE_TYPE_ELECTRIC
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType"):
            return (
                find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType") == ENGINE_TYPE_ELECTRIC
            )
        return False

    @property
    def is_car_type_diesel(self):
        """Check if car type is diesel."""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType"):
            return find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType") == ENGINE_TYPE_DIESEL
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType"):
            return find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType") == ENGINE_TYPE_DIESEL
        return False

    @property
    def is_car_type_gasoline(self):
        """Check if car type is gasoline."""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType"):
            return find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType") == ENGINE_TYPE_GASOLINE
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType"):
            return (
                find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType") == ENGINE_TYPE_GASOLINE
            )
        return False

    @property
    def is_car_type_hybrid(self):
        """Check if car type is hybrid."""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType"):
            return find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carType") == ENGINE_TYPE_HYBRID
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType"):
            return find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType") == ENGINE_TYPE_HYBRID
        return False

    @property
    def has_combustion_engine(self):
        """Return true if car has a combustion engine."""
        return self.is_primary_drive_combustion() or self.is_secondary_drive_combustion()

    @property
    def api_vehicles_status(self) -> bool:
        """Check vehicles API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("vehicles", "Unknown")

    @property
    def api_vehicles_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_vehicles_status_supported(self):
        """Vehicles API status is always supported."""
        return True

    @property
    def api_capabilities_status(self) -> bool:
        """Check capabilities API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("capabilities", "Unknown")

    @property
    def api_capabilities_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_capabilities_status_supported(self):
        """Capabilities API status is always supported."""
        return True

    @property
    def api_trips_status(self) -> bool:
        """Check trips API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("trips", "Unknown")

    @property
    def api_trips_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_trips_status_supported(self):
        """Check if Trips API status is supported."""
        if self._services.get(Services.TRIP_STATISTICS, {}).get("active", False):
            return True
        return False

    @property
    def api_selectivestatus_status(self) -> bool:
        """Check selectivestatus API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("selectivestatus", "Unknown")

    @property
    def api_selectivestatus_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_selectivestatus_status_supported(self):
        """Selectivestatus API status is always supported."""
        return True

    @property
    def api_parkingposition_status(self) -> bool:
        """Check parkingposition API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("parkingposition", "Unknown")

    @property
    def api_parkingposition_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_parkingposition_status_supported(self):
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
        return datetime.now(timezone.utc)

    @property
    def is_api_token_status_supported(self):
        """Parkingposition API status is always supported."""
        return True

    @property
    def last_data_refresh(self) -> datetime:
        """Check when services were refreshed successfully for the last time."""
        last_data_refresh_path = "refreshTimestamp"
        if is_valid_path(self.attrs, last_data_refresh_path):
            return find_path(self.attrs, last_data_refresh_path)
        return None

    @property
    def last_data_refresh_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_last_data_refresh_supported(self):
        """Last data refresh is always supported."""
        return True
