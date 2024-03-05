"""Utilities for integration with Home Assistant."""

# Thanks to molobrakos

import logging
from datetime import datetime

from .vw_const import TEMP_CELSIUS, VWDeviceClass, VWStateClass
from .vw_utilities import camel2slug
from .vw_vehicle import Vehicle


_LOGGER = logging.getLogger(__name__)


class Instrument:
    """Base class for all components."""

    vehicle: Vehicle

    def __init__(
        self,
        component,
        attr: str,
        name: str,
        icon: str | None = None,
        entity_type: str | None = None,
        device_class: str | None = None,
        state_class: str | None = None,
    ):
        """Init."""
        self.attr = attr
        self.component = component
        self.name = name
        self.icon = icon
        self.entity_type = entity_type
        self.device_class = device_class
        self.state_class = state_class
        self.callback = None

    def __repr__(self) -> str:
        """Return string representation of class."""
        return self.full_name

    def configurate(self, **args):
        """Override in subclasses."""
        pass

    @property
    def slug_attr(self) -> str:
        """Return slugified attribute name."""
        return camel2slug(self.attr.replace(".", "_"))

    def setup(self, vehicle: Vehicle, **config) -> bool:
        """Set up entity if supported."""
        self.vehicle = vehicle
        if not self.is_supported:
            _LOGGER.debug("%s (%s:%s) is not supported", self, type(self).__name__, self.attr)
            return False

        _LOGGER.debug("%s is supported", self)
        self.configurate(**config)
        return True

    @property
    def vehicle_name(self) -> str:
        """Return vehicle name."""
        return self.vehicle.vin

    @property
    def full_name(self) -> str:
        """Return full device name."""
        return f"{self.vehicle_name} {self.name}"

    @property
    def is_mutable(self) -> bool:
        """Override in subclasses."""
        raise NotImplementedError("Must be set")

    @property
    def str_state(self) -> str:
        """Return current state as string."""
        return self.state

    @property
    def state(self) -> object:
        """Return current state."""
        if hasattr(self.vehicle, self.attr):
            return getattr(self.vehicle, self.attr)
        else:
            _LOGGER.debug(f'Could not find attribute "{self.attr}"')
        return self.vehicle.get_attr(self.attr)

    @property
    def attributes(self) -> dict:
        """Override in subclasses."""
        return {}

    @property
    def is_supported(self) -> bool:
        """Check entity support."""
        supported = "is_" + self.attr + "_supported"
        if hasattr(self.vehicle, supported):
            return getattr(self.vehicle, supported)
        else:
            return False

    @property
    def last_refresh(self) -> datetime | None:
        if hasattr(self.vehicle, self.attr + "_last_updated"):
            return getattr(self.vehicle, self.attr + "_last_updated")
        _LOGGER.warning(f"Implement in subclasses. {self.__class__}:{self.attr}_last_updated")
        if self.state_class is not None:
            raise NotImplementedError(f"Implement in subclasses. {self.__class__}:{self.attr}_last_updated")
        return None


class Sensor(Instrument):
    """Base class for sensor type entities."""

    def __init__(
        self,
        attr: str,
        name: str,
        icon: str | None,
        unit: str | None,
        entity_type: str | None = None,
        device_class: str | None = None,
        state_class: str | None = None,
    ):
        """Init."""
        super().__init__(
            component="sensor",
            attr=attr,
            name=name,
            icon=icon,
            entity_type=entity_type,
            device_class=device_class,
            state_class=state_class,
        )
        self.unit = unit
        self.convert = False

    def configurate(self, miles=False, scandinavian_miles=False, **config):
        if self.unit and miles:
            if "km" == self.unit:
                self.unit = "mi"
                self.convert = True
            elif "km/h" == self.unit:
                self.unit = "mi/h"
                self.convert = True
            elif "l/100 km" == self.unit:
                self.unit = "l/100 mi"
                self.convert = True
            elif "kWh/100 km" == self.unit:
                self.unit = "kWh/100 mi"
                self.convert = True
        elif self.unit and scandinavian_miles:
            if "km" == self.unit:
                self.unit = "mil"
            elif "km/h" == self.unit:
                self.unit = "mil/h"
            elif "l/100 km" == self.unit:
                self.unit = "l/100 mil"
            elif "kWh/100 km" == self.unit:
                self.unit = "kWh/100 mil"

    @property
    def is_mutable(self):
        return False

    @property
    def str_state(self):
        if self.unit:
            return f"{self.state} {self.unit}"
        else:
            return f"{self.state}"

    @property
    def state(self):
        val = super().state
        if val and self.unit and "mi" in self.unit and self.convert is True:
            return round(int(val) * 0.6213712)
        elif val and self.unit and "mi/h" in self.unit and self.convert is True:
            return round(int(val) * 0.6213712)
        elif val and self.unit and "gal/100 mi" in self.unit and self.convert is True:
            return round(val * 0.4251438, 1)
        elif val and self.unit and "kWh/100 mi" in self.unit and self.convert is True:
            return round(val * 0.4251438, 1)
        elif val and self.unit and "°F" in self.unit and self.convert is True:
            temp = round((val * 9 / 5) + 32, 1)
            return temp
        elif val and self.unit in ["mil", "mil/h"]:
            return val / 10
        else:
            return val


class BinarySensor(Instrument):
    def __init__(self, attr, name, device_class, icon="", entity_type=None, reverse_state=False):
        super().__init__(component="binary_sensor", attr=attr, name=name, icon=icon, entity_type=entity_type)
        self.device_class = device_class
        self.reverse_state = reverse_state

    @property
    def is_mutable(self):
        return False

    @property
    def str_state(self):
        if self.device_class in [VWDeviceClass.DOOR, VWDeviceClass.WINDOW]:
            return "Open" if self.state else "Closed"
        if self.device_class == VWDeviceClass.LOCK:
            return "Unlocked" if self.state else "Locked"
        if self.device_class == "safety":
            return "Warning!" if self.state else "OK"
        if self.device_class == VWDeviceClass.PLUG:
            return "Charging" if self.state else "Plug removed"
        if self.state is None:
            _LOGGER.error("Can not encode state %s:%s", self.attr, self.state)
            return "?"
        return "On" if self.state else "Off"

    @property
    def state(self):
        val = super().state

        if isinstance(val, (bool, list)):
            if self.reverse_state:
                if bool(val):
                    return False
                else:
                    return True
            else:
                return bool(val)
        elif isinstance(val, str):
            return val != "Normal"
        return val

    @property
    def is_on(self):
        return self.state


class Switch(Instrument):
    """Switch instrument."""

    def __init__(self, attr, name, icon, entity_type=None):
        super().__init__(component="switch", attr=attr, name=name, icon=icon, entity_type=entity_type)

    @property
    def is_mutable(self):
        return True

    @property
    def str_state(self):
        return "On" if self.state else "Off"

    def is_on(self):
        return self.state

    def turn_on(self):
        pass

    def turn_off(self):
        pass

    @property
    def assumed_state(self) -> bool:
        """Assume state."""
        return True


class Number(Instrument):
    def __init__(
        self,
        attr: str,
        name: str,
        icon: str | None,
        unit: str | None,
        entity_type: str | None = None,
        device_class: str | None = None,
        state_class: str | None = None,
    ):
        """Init."""
        super().__init__(
            component="number",
            attr=attr,
            name=name,
            icon=icon,
            entity_type=entity_type,
            device_class=device_class,
            state_class=state_class,
        )
        self.unit = unit

    @property
    def is_mutable(self):
        return False

    @property
    def state(self):
        raise NotImplementedError

    @property
    def min_value(self):
        raise NotImplementedError

    @property
    def max_value(self):
        raise NotImplementedError

    @property
    def native_step(self):
        raise NotImplementedError


class Position(Instrument):
    def __init__(self):
        super().__init__(component="device_tracker", attr="position", name="Position")

    @property
    def is_mutable(self):
        return False

    @property
    def state(self):
        state = super().state  # or {}
        return (
            state.get("lat", "?"),
            state.get("lng", "?"),
            state.get("timestamp", None),
        )

    @property
    def str_state(self):
        state = super().state  # or {}
        ts = state.get("timestamp", None)
        return (
            state.get("lat", "?"),
            state.get("lng", "?"),
            str(ts.astimezone(tz=None)) if ts else None,
        )


class DoorLock(Instrument):
    def __init__(self):
        super().__init__(component=VWDeviceClass.LOCK, attr="door_locked", name="Door locked")
        self.spin = ""

    def configurate(self, **config):
        self.spin = config.get("spin", "")

    @property
    def is_mutable(self):
        return True

    @property
    def str_state(self):
        return "Locked" if self.state else "Unlocked"

    @property
    def state(self):
        return self.vehicle.door_locked

    @property
    def is_locked(self):
        return self.state

    async def lock(self):
        try:
            response = await self.vehicle.set_lock(VWDeviceClass.LOCK, self.spin)
            await self.vehicle.update()
            if self.callback is not None:
                self.callback()
            return response
        except Exception as e:
            _LOGGER.error("Lock failed: %", e.args[0])
            return False

    async def unlock(self):
        try:
            response = await self.vehicle.set_lock("unlock", self.spin)
            await self.vehicle.update()
            if self.callback is not None:
                self.callback()
            return response
        except Exception as e:
            _LOGGER.error("Unlock failed: %", e.args[0])
            return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.lock_action_status)


class TrunkLock(Instrument):
    def __init__(self):
        super().__init__(component=VWDeviceClass.LOCK, attr="trunk_locked", name="Trunk locked")

    @property
    def is_mutable(self):
        return True

    @property
    def str_state(self):
        return "Locked" if self.state else "Unlocked"

    @property
    def state(self):
        return self.vehicle.trunk_locked

    @property
    def is_locked(self):
        return self.state

    async def lock(self):
        return None

    async def unlock(self):
        return None


# Numbers


class AuxiliaryDuration(Number):
    """Currently disabled due to the lack of auxiliary settings API."""

    def __init__(self):
        super().__init__(
            attr="auxiliary_duration",
            name="Auxiliary duration",
            icon="mdi:timer",
            unit="min",
            entity_type="config",
        )
        self.spin = ""

    def configurate(self, **config):
        self.spin = config.get("spin", "")

    @property
    def state(self):
        return self.vehicle.auxiliary_duration

    async def set_value(self, minutes: int):
        await self.vehicle.set_auxiliary_duration(minutes, self.spin)
        await self.vehicle.update()

    @property
    def min_value(self):
        return 5

    @property
    def max_value(self):
        return 30

    @property
    def native_step(self):
        return 5

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


class BatteryTargetSOC(Number):

    def __init__(self):
        super().__init__(
            attr="battery_target_charge_level",
            name="Battery target charge level",
            icon="mdi:battery-arrow-up",
            unit="%",
            entity_type="config",
        )

    @property
    def state(self):
        return self.vehicle.battery_target_charge_level

    async def set_value(self, value: int):
        await self.vehicle.set_charging_settings(setting="battery_target_charge_level", value=value)
        await self.vehicle.update()

    @property
    def min_value(self):
        return 50

    @property
    def max_value(self):
        return 100

    @property
    def native_step(self):
        return 10

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.charger_action_status)


class ClimatisationTargetTemperature(Number):

    def __init__(self):
        super().__init__(
            attr="climatisation_target_temperature",
            name="Climatisation target temperature",
            icon="mdi:thermometer",
            device_class=VWDeviceClass.TEMPERATURE,
            unit=TEMP_CELSIUS,
        )

    @property
    def state(self):
        return self.vehicle.climatisation_target_temperature

    async def set_value(self, value: float):
        await self.vehicle.set_climatisation_settings(setting="climatisation_target_temperature", value=value)
        await self.vehicle.update()

    @property
    def min_value(self):
        return 15.5

    @property
    def max_value(self):
        return 30

    @property
    def native_step(self):
        return 0.5

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


# Switches


class RequestUpdate(Switch):
    def __init__(self):
        super().__init__(attr="refresh_data", name="Force data refresh", icon="mdi:car-connected")

    @property
    def state(self):
        return self.vehicle.refresh_data

    async def turn_on(self):
        await self.vehicle.set_refresh()
        await self.vehicle.update()
        if self.callback is not None:
            self.callback()

    async def turn_off(self):
        pass

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.refresh_action_status)


class ElectricClimatisation(Switch):
    def __init__(self):
        super().__init__(attr="electric_climatisation", name="Electric Climatisation", icon="mdi:air-conditioner")

    @property
    def state(self):
        return self.vehicle.electric_climatisation

    async def turn_on(self):
        await self.vehicle.set_climatisation("start")
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_climatisation("stop")
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


class AuxiliaryClimatisation(Switch):
    def __init__(self):
        super().__init__(attr="auxiliary_climatisation", name="Auxiliary Climatisation", icon="mdi:radiator")
        self.spin = ""

    def configurate(self, **config):
        self.spin = config.get("spin", "")

    @property
    def state(self):
        return self.vehicle.auxiliary_climatisation

    async def turn_on(self):
        await self.vehicle.set_auxiliary_climatisation("start", self.spin)
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_auxiliary_climatisation("stop", self.spin)
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


class Charging(Switch):
    def __init__(self):
        super().__init__(attr="charging", name="Charging", icon="mdi:battery")

    @property
    def state(self):
        return self.vehicle.charging

    async def turn_on(self):
        await self.vehicle.set_charger("start")
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_charger("stop")
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.charger_action_status)


class ReducedACCharging(Switch):
    def __init__(self):
        super().__init__(
            attr="reduced_ac_charging", name="Reduced AC Charging", icon="mdi:ev-station", entity_type="config"
        )

    @property
    def state(self):
        return self.vehicle.reduced_ac_charging

    async def turn_on(self):
        await self.vehicle.set_charging_settings(setting="reduced_ac_charging", value="reduced")
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_charging_settings(setting="reduced_ac_charging", value="maximum")
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.charger_action_status)


class AutoReleaseACConnector(Switch):
    def __init__(self):
        super().__init__(
            attr="auto_release_ac_connector",
            name="Auto-release AC connector",
            icon="mdi:ev-plug-type2",
            entity_type="config",
        )

    @property
    def state(self):
        return self.vehicle.auto_release_ac_connector

    async def turn_on(self):
        await self.vehicle.set_charging_settings(setting="auto_release_ac_connector", value="permanent")
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_charging_settings(setting="auto_release_ac_connector", value="off")
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False


class BatteryCareMode(Switch):
    def __init__(self):
        super().__init__(
            attr="battery_care_mode",
            name="Battery care mode",
            icon="mdi:battery-heart-variant",
            entity_type="config",
        )

    @property
    def state(self):
        return self.vehicle.battery_care_mode

    async def turn_on(self):
        await self.vehicle.set_charging_care_settings(value="activated")
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_charging_care_settings(value="deactivated")
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.charger_action_status)


class OptimisedBatteryUse(Switch):
    def __init__(self):
        super().__init__(
            attr="optimised_battery_use",
            name="Optimised battery use",
            icon="mdi:battery-check",
            entity_type="config",
        )

    @property
    def state(self):
        return self.vehicle.optimised_battery_use

    async def turn_on(self):
        await self.vehicle.set_readiness_battery_support(value=True)
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_readiness_battery_support(value=False)
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.charger_action_status)


class DepartureTimer(Switch):
    """Departure timers."""

    def __init__(self, id: str | int):
        self._id = id
        super().__init__(
            attr=f"departure_timer{id}", name=f"Departure Timer {id}", icon="mdi:car-clock", entity_type="config"
        )
        self.spin = ""

    def configurate(self, **config):
        self.spin = config.get("spin", "")

    @property
    def state(self):
        """Return switch state."""
        return self.vehicle.departure_timer_enabled(self._id)

    async def turn_on(self):
        """Enable timer."""
        await self.vehicle.set_departure_timer(timer_id=self._id, spin=self.spin, enable=True)
        await self.vehicle.update()

    async def turn_off(self):
        """Disable timer."""
        await self.vehicle.set_departure_timer(timer_id=self._id, spin=self.spin, enable=False)
        await self.vehicle.update()

    @property
    def assumed_state(self):
        """Don't assume state info."""
        return False

    @property
    def attributes(self):
        """Timer attributes."""
        data = self.vehicle.timer_attributes(self._id)
        return dict(data)


class ACDepartureTimer(Switch):
    """Air conditioning departure timers."""

    def __init__(self, id: str | int):
        self._id = id
        super().__init__(
            attr=f"ac_departure_timer{id}", name=f"AC Departure Timer {id}", icon="mdi:fan-clock", entity_type="config"
        )

    @property
    def state(self):
        """Return switch state."""
        return self.vehicle.ac_departure_timer_enabled(self._id)

    async def turn_on(self):
        """Enable timer."""
        await self.vehicle.set_ac_departure_timer(timer_id=self._id, enable=True)
        await self.vehicle.update()

    async def turn_off(self):
        """Disable timer."""
        await self.vehicle.set_ac_departure_timer(timer_id=self._id, enable=False)
        await self.vehicle.update()

    @property
    def assumed_state(self):
        """Don't assume state info."""
        return False

    @property
    def attributes(self):
        """Timer attributes."""
        data = self.vehicle.ac_timer_attributes(self._id)
        return dict(data)


class WindowHeater(Switch):
    def __init__(self):
        super().__init__(attr="window_heater", name="Window Heater", icon="mdi:car-defrost-rear")

    @property
    def state(self):
        return self.vehicle.window_heater

    async def turn_on(self):
        await self.vehicle.set_window_heating("start")
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_window_heating("stop")
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


class BatteryClimatisation(Switch):
    def __init__(self):
        super().__init__(
            attr="climatisation_without_external_power",
            name="Climatisation from battery",
            icon="mdi:power-plug",
            entity_type="config",
        )

    @property
    def state(self):
        return self.vehicle.climatisation_without_external_power

    async def turn_on(self):
        await self.vehicle.set_climatisation_settings(setting="climatisation_without_external_power", value=True)
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_climatisation_settings(setting="climatisation_without_external_power", value=False)
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


class AuxiliaryAC(Switch):
    def __init__(self):
        super().__init__(
            attr="auxiliary_air_conditioning",
            name="Auxiliary air conditioning",
            icon="mdi:air-filter",
            entity_type="config",
        )

    @property
    def state(self):
        return self.vehicle.auxiliary_air_conditioning

    async def turn_on(self):
        await self.vehicle.set_climatisation_settings(setting="auxiliary_air_conditioning", value=True)
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_climatisation_settings(setting="auxiliary_air_conditioning", value=False)
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


class AutomaticWindowHeating(Switch):
    def __init__(self):
        super().__init__(
            attr="automatic_window_heating",
            name="Automatic window heating",
            icon="mdi:car-defrost-rear",
            entity_type="config",
        )

    @property
    def state(self):
        return self.vehicle.automatic_window_heating

    async def turn_on(self):
        await self.vehicle.set_climatisation_settings(setting="automatic_window_heating", value=True)
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_climatisation_settings(setting="automatic_window_heating", value=False)
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


class ZoneFrontLeft(Switch):
    def __init__(self):
        super().__init__(
            attr="zone_front_left",
            name="Zone Front Left",
            icon="mdi:account-arrow-left",
            entity_type="config",
        )

    @property
    def state(self):
        return self.vehicle.zone_front_left

    async def turn_on(self):
        await self.vehicle.set_climatisation_settings(setting="zone_front_left", value=True)
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_climatisation_settings(setting="zone_front_left", value=False)
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


class ZoneFrontRight(Switch):
    def __init__(self):
        super().__init__(
            attr="zone_front_right",
            name="Zone Front Right",
            icon="mdi:account-arrow-right",
            entity_type="config",
        )

    @property
    def state(self):
        return self.vehicle.zone_front_right

    async def turn_on(self):
        await self.vehicle.set_climatisation_settings(setting="zone_front_right", value=True)
        await self.vehicle.update()

    async def turn_off(self):
        await self.vehicle.set_climatisation_settings(setting="zone_front_right", value=False)
        await self.vehicle.update()

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(last_result=self.vehicle.climater_action_status)


class RequestResults(Sensor):
    """Request results sensor class."""

    def __init__(self):
        """Init."""
        super().__init__(
            attr="request_results", name="Request results", icon="mdi:chat-alert", unit="", entity_type="diag"
        )

    @property
    def state(self) -> object:
        """Return current state."""
        if self.vehicle.request_results.get("state", False):
            return self.vehicle.request_results.get("state")
        return "Unknown"

    @property
    def assumed_state(self) -> bool:
        """Don't assume state."""
        return False

    @property
    def attributes(self) -> dict:
        """Return attributes."""
        return dict(self.vehicle.request_results)


def create_instruments():
    """Return list of all entities."""
    return [
        Position(),
        # AuxiliaryDuration(),
        BatteryTargetSOC(),
        ClimatisationTargetTemperature(),
        DoorLock(),
        TrunkLock(),
        RequestUpdate(),
        WindowHeater(),
        BatteryClimatisation(),
        AuxiliaryAC(),
        AutomaticWindowHeating(),
        ZoneFrontLeft(),
        ZoneFrontRight(),
        ElectricClimatisation(),
        AuxiliaryClimatisation(),
        Charging(),
        ReducedACCharging(),
        AutoReleaseACConnector(),
        BatteryCareMode(),
        OptimisedBatteryUse(),
        DepartureTimer(1),
        DepartureTimer(2),
        DepartureTimer(3),
        ACDepartureTimer(1),
        ACDepartureTimer(2),
        RequestResults(),
        Sensor(
            attr="distance",
            name="Odometer",
            icon="mdi:speedometer",
            unit="km",
            state_class=VWStateClass.TOTAL_INCREASING,
        ),
        Sensor(
            attr="battery_level",
            name="Battery level",
            icon="mdi:battery",
            unit="%",
            device_class=VWDeviceClass.BATTERY,
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="battery_target_charge_level",
            name="Battery target charge level",
            icon="mdi:battery-arrow-up",
            unit="%",
        ),
        Sensor(
            attr="adblue_level",
            name="Adblue level",
            icon="mdi:fuel",
            unit="km",
        ),
        Sensor(
            attr="fuel_level",
            name="Fuel level",
            icon="mdi:fuel",
            unit="%",
        ),
        Sensor(
            attr="service_inspection",
            name="Service inspection days",
            icon="mdi:garage",
            unit="days",
        ),
        Sensor(
            attr="service_inspection_distance",
            name="Service inspection distance",
            icon="mdi:garage",
            unit="km",
        ),
        Sensor(
            attr="oil_inspection",
            name="Oil inspection days",
            icon="mdi:oil",
            unit="days",
        ),
        Sensor(
            attr="oil_inspection_distance",
            name="Oil inspection distance",
            icon="mdi:oil",
            unit="km",
        ),
        Sensor(
            attr="last_connected",
            name="Last connected",
            icon="mdi:clock",
            unit="",
            device_class=VWDeviceClass.TIMESTAMP,
            entity_type="diag",
        ),
        Sensor(
            attr="parking_time",
            name="Parking time",
            icon="mdi:clock",
            unit="",
            device_class=VWDeviceClass.TIMESTAMP,
        ),
        Sensor(
            attr="charging_time_left",
            name="Charging time left",
            icon="mdi:battery-charging-100",
            unit="min",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="electric_range",
            name="Electric range",
            icon="mdi:car-electric",
            unit="km",
        ),
        Sensor(
            attr="combustion_range",
            name="Combustion range",
            icon="mdi:car",
            unit="km",
        ),
        Sensor(
            attr="combined_range",
            name="Combined range",
            icon="mdi:car",
            unit="km",
        ),
        Sensor(
            attr="battery_cruising_range",
            name="Battery cruising range",
            icon="mdi:car-settings",
            unit="km",
        ),
        Sensor(
            attr="charge_max_ac_setting",
            name="Charger max AC setting",
            icon="mdi:flash",
            unit="",
        ),
        Sensor(
            attr="charge_max_ac_ampere",
            name="Charger max AC ampere",
            icon="mdi:flash-auto",
            unit="A",
        ),
        Sensor(
            attr="charging_power",
            name="Charging Power",
            icon="mdi:transmission-tower",
            unit="kW",
        ),
        Sensor(
            attr="charging_rate",
            name="Charging Rate",
            icon="mdi:ev-station",
            unit="km/h",
        ),
        Sensor(
            attr="charger_type",
            name="Charger Type",
            icon="mdi:ev-plug-type1",
            unit="",
        ),
        Sensor(
            attr="climatisation_target_temperature",
            name="Climatisation target temperature",
            icon="mdi:thermometer",
            unit=TEMP_CELSIUS,
            device_class=VWDeviceClass.TEMPERATURE,
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_average_speed",
            name="Last trip average speed",
            icon="mdi:speedometer",
            unit="km/h",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_average_electric_engine_consumption",
            name="Last trip average electric engine consumption",
            icon="mdi:car-battery",
            unit="kWh/100 km",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_average_fuel_consumption",
            name="Last trip average fuel consumption",
            icon="mdi:fuel",
            unit="l/100 km",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_duration",
            name="Last trip duration",
            icon="mdi:clock",
            unit="min",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_length",
            name="Last trip length",
            icon="mdi:map-marker-distance",
            unit="km",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_recuperation",
            name="Last trip recuperation",
            icon="mdi:battery-plus",
            unit="kWh/100 km",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_average_recuperation",
            name="Last trip average recuperation",
            icon="mdi:battery-plus",
            unit="kWh/100 km",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_average_auxillary_consumption",
            name="Last trip average auxillary consumption",
            icon="mdi:flash",
            unit="kWh/100 km",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_average_aux_consumer_consumption",
            name="Last trip average auxillary consumer consumption",
            icon="mdi:flash",
            unit="kWh/100 km",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="trip_last_total_electric_consumption",
            name="Last trip total electric consumption",
            icon="mdi:car-battery",
            unit="kWh/100 km",
            state_class=VWStateClass.MEASUREMENT,
        ),
        Sensor(
            attr="auxiliary_duration",
            name="Auxiliary Heater heating/ventilation duration",
            icon="mdi:timer",
            unit="minutes",
        ),
        Sensor(
            attr="auxiliary_remaining_climatisation_time",
            name="Auxiliary remaining climatisation time",
            icon="mdi:fan-clock",
            unit="minutes",
        ),
        Sensor(
            attr="electric_remaining_climatisation_time",
            name="Electric remaining climatisation time",
            icon="mdi:fan-clock",
            unit="minutes",
        ),
        Sensor(
            attr="car_type",
            name="Car Type",
            icon="mdi:car-select",
            unit="",
        ),
        Sensor(
            attr="api_vehicles_status",
            name="API vehicles",
            icon="mdi:api",
            unit="",
            entity_type="diag",
        ),
        Sensor(
            attr="api_capabilities_status",
            name="API capabilities",
            icon="mdi:api",
            unit="",
            entity_type="diag",
        ),
        Sensor(
            attr="api_trips_status",
            name="API trips",
            icon="mdi:api",
            unit="",
            entity_type="diag",
        ),
        Sensor(
            attr="api_selectivestatus_status",
            name="API selectivestatus",
            icon="mdi:api",
            unit="",
            entity_type="diag",
        ),
        Sensor(
            attr="api_parkingposition_status",
            name="API parkingposition",
            icon="mdi:api",
            unit="",
            entity_type="diag",
        ),
        Sensor(
            attr="api_token_status",
            name="API token",
            icon="mdi:api",
            unit="",
            entity_type="diag",
        ),
        Sensor(
            attr="last_data_refresh",
            name="Last data refresh",
            icon="mdi:clock",
            unit="",
            device_class=VWDeviceClass.TIMESTAMP,
            entity_type="diag",
        ),
        BinarySensor(attr="external_power", name="External power", device_class=VWDeviceClass.POWER),
        BinarySensor(attr="energy_flow", name="Energy flow", device_class=VWDeviceClass.POWER),
        BinarySensor(
            attr="parking_light", name="Parking light", device_class=VWDeviceClass.LIGHT, icon="mdi:car-parking-lights"
        ),
        BinarySensor(attr="door_locked", name="Doors locked", device_class=VWDeviceClass.LOCK, reverse_state=True),
        BinarySensor(
            attr="door_locked_sensor", name="Doors locked", device_class=VWDeviceClass.LOCK, reverse_state=True
        ),
        BinarySensor(
            attr="door_closed_left_front",
            name="Door closed left front",
            device_class=VWDeviceClass.DOOR,
            reverse_state=True,
            icon="mdi:car-door",
        ),
        BinarySensor(
            attr="door_closed_right_front",
            name="Door closed right front",
            device_class=VWDeviceClass.DOOR,
            reverse_state=True,
            icon="mdi:car-door",
        ),
        BinarySensor(
            attr="door_closed_left_back",
            name="Door closed left back",
            device_class=VWDeviceClass.DOOR,
            reverse_state=True,
            icon="mdi:car-door",
        ),
        BinarySensor(
            attr="door_closed_right_back",
            name="Door closed right back",
            device_class=VWDeviceClass.DOOR,
            reverse_state=True,
            icon="mdi:car-door",
        ),
        BinarySensor(attr="trunk_locked", name="Trunk locked", device_class=VWDeviceClass.LOCK, reverse_state=True),
        BinarySensor(
            attr="trunk_locked_sensor", name="Trunk locked", device_class=VWDeviceClass.LOCK, reverse_state=True
        ),
        BinarySensor(attr="trunk_closed", name="Trunk closed", device_class=VWDeviceClass.DOOR, reverse_state=True),
        BinarySensor(attr="hood_closed", name="Hood closed", device_class=VWDeviceClass.DOOR, reverse_state=True),
        BinarySensor(
            attr="charging_cable_connected",
            name="Charging cable connected",
            device_class=VWDeviceClass.PLUG,
            reverse_state=False,
        ),
        BinarySensor(
            attr="charging_cable_locked",
            name="Charging cable locked",
            device_class=VWDeviceClass.LOCK,
            reverse_state=True,
        ),
        BinarySensor(
            attr="sunroof_closed", name="Sunroof closed", device_class=VWDeviceClass.WINDOW, reverse_state=True
        ),
        BinarySensor(
            attr="sunroof_rear_closed",
            name="Sunroof Rear closed",
            device_class=VWDeviceClass.WINDOW,
            reverse_state=True,
        ),
        BinarySensor(
            attr="roof_cover_closed", name="Roof cover closed", device_class=VWDeviceClass.WINDOW, reverse_state=True
        ),
        BinarySensor(
            attr="windows_closed", name="Windows closed", device_class=VWDeviceClass.WINDOW, reverse_state=True
        ),
        BinarySensor(
            attr="window_closed_left_front",
            name="Window closed left front",
            device_class=VWDeviceClass.WINDOW,
            reverse_state=True,
        ),
        BinarySensor(
            attr="window_closed_left_back",
            name="Window closed left back",
            device_class=VWDeviceClass.WINDOW,
            reverse_state=True,
        ),
        BinarySensor(
            attr="window_closed_right_front",
            name="Window closed right front",
            device_class=VWDeviceClass.WINDOW,
            reverse_state=True,
        ),
        BinarySensor(
            attr="window_closed_right_back",
            name="Window closed right back",
            device_class=VWDeviceClass.WINDOW,
            reverse_state=True,
        ),
        BinarySensor(attr="vehicle_moving", name="Vehicle Moving", device_class=VWDeviceClass.MOVING),
        BinarySensor(
            attr="request_in_progress",
            name="Request in progress",
            device_class=VWDeviceClass.CONNECTIVITY,
            entity_type="diag",
        ),
    ]


class Dashboard:
    """Helper for accessing the instruments."""

    def __init__(self, vehicle, **config):
        """Initialize instruments."""
        _LOGGER.debug("Setting up dashboard with config :%s", config)
        self.instruments = [instrument for instrument in create_instruments() if instrument.setup(vehicle, **config)]
