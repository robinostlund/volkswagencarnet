# Utilities for integration with Home Assistant
# Thanks to molobrakos

import logging
from utilities import camel2slug

_LOGGER = logging.getLogger(__name__)

class Instrument:
    def __init__(self, component, attr, name, icon = None):
        self.attr = attr
        self.component = component
        self.name = name
        self.vehicle = None
        self.icon = icon

    def __repr__(self):
        return self.full_name

    def configurate(self, **args):
        pass

    @property
    def slug_attr(self):
        return camel2slug(self.attr.replace(".", "_"))

    def setup(self, vehicle, **config):
        self.vehicle = vehicle
        if not self.is_supported:
            _LOGGER.debug("%s (%s:%s) is not supported", self, type(self).__name__, self.attr)
            return False

        _LOGGER.debug("%s is supported", self)
        self.configurate(**config)
        return True
        

    @property
    def vehicle_name(self):
        return self.vehicle.vin.lower()

    @property
    def full_name(self):
        return "%s %s" % (self.vehicle_name, self.name)

    @property
    def is_mutable(self):
        raise NotImplementedError("Must be set")

    @property
    def str_state(self):
        return self.state

    @property
    def state(self):
        if hasattr(self.vehicle, self.attr):
            return getattr(self.vehicle, self.attr)
        return self.vehicle.get_attr(self.attr)

    @property
    def attributes(self):
        return {}

    @property
    def is_supported(self):
        supported = "is_" + self.attr + "_supported"
        if hasattr(self.vehicle, supported):
            return getattr(self.vehicle, supported)
        if hasattr(self.vehicle, self.attr):
            return True
        return self.vehicle.has_attr(self.attr)

# instrument classes
class Sensor(Instrument):
    def __init__(self, attr, name, icon, unit):
        super().__init__(component = "sensor", attr = attr, name = name, icon = icon)
        self.unit = unit

    def configurate(self, scandinavian_miles=False, **config):
        if self.unit and scandinavian_miles and "km" in self.unit:
            self.unit = "mil"

    @property
    def is_mutable(self):
        return False

    @property
    def str_state(self):
        if self.unit:
            return "%s %s" % (self.state, self.unit)
        else:
            return "%s" % self.state

    @property
    def state(self):
        val = super().state
        if val and "mil" in self.unit:
            return val / 10
        else:
            return val

class BinarySensor(Instrument):
    def __init__(self, attr, name, device_class):
        super().__init__(component = "binary_sensor", attr = attr, name = name)
        self.device_class = device_class

    @property
    def is_mutable(self):
        return False

    @property
    def str_state(self):
        if self.device_class in ["door", "window"]:
            return "Open" if self.state else "Closed"
        if self.device_class == "safety":
            return "Warning!" if self.state else "OK"
        if self.device_class == "plug":
            return "Charging" if self.state else "Plug removed"
        if self.state is None:
            _LOGGER.error("Can not encode state %s:%s", self.attr, self.state)
            return "?"
        return "On" if self.state else "Off"

    @property
    def state(self):
        val = super().state
        if isinstance(val, (bool, list)):
            #  for list (e.g. bulb_failures):
            #  empty list (False) means no problem
            return bool(val)
        elif isinstance(val, str):
            return val != "Normal"
        return val

    @property
    def is_on(self):
        return self.state


class Switch(Instrument):
    def __init__(self, attr, name, icon):
        super().__init__(component = "switch", attr = attr, name = name, icon = icon)

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
    def assumed_state(self):
        return True

class Climate(Instrument):
    def __init__(self, attr, name, icon):
        super().__init__(component = "climate", attr = attr, name = name, icon = icon)

    @property
    def hvac_mode(self):
        pass

    @property
    def target_temperature(self):
        pass

    def set_temperature(self, **kwargs):
        pass

    def set_hvac_mode(self, hvac_mode):
        pass

class ClimatisationClimate(Climate):
    def __init__(self):
        super().__init__(attr="climatisation", name="Climatisation", icon="mdi:radiator")

    @property
    def hvac_mode(self):
        return self.vehicle.climatisation

    @property
    def target_temperature(self):
        return self.vehicle.climatisation_target_temperature

    def set_temperature(self, temperature):
        self.vehicle.set_climatisation_target_temperature(temperature)

    def set_hvac_mode(self, hvac_mode):
        if hvac_mode:
            self.vehicle.start_climatisation()
        else:
            self.vehicle.stop_climatisation()


class Position(Instrument):
    def __init__(self):
        super().__init__(component = "device_tracker", attr = "position", name = "Position")

    @property
    def is_mutable(self):
        return False

    @property
    def state(self):
        state = super().state or {}
        return (
            state.get("lat", "?"),
            state.get("lng", "?"),
            state.get("timestamp", None),
            state.get("speed", None),
            state.get("heading", None),
        )

    @property
    def str_state(self):
        state = super().state or {}
        ts = state.get("timestamp", None)
        return (
            state.get("lat", "?"),
            state.get("lng", "?"),
            str(ts.astimezone(tz=None)) if ts else None,
            state.get("speed", None),
            state.get("heading", None),
        )

class DoorLock(Instrument):
    def __init__(self):
        super().__init__(component = "lock", attr = "door_locked", name = "Door locked")

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

    def lock(self):
        return None

    def unlock(self):
        return None

    @property
    def assumed_state(self):
        return True

class TrunkLock(Instrument):
    def __init__(self):
        super().__init__(component = "lock", attr = "trunk_locked", name = "Trunk locked")

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

    def lock(self):
        return None

    def unlock(self):
        return None

    @property
    def assumed_state(self):
        return True

# Switches
class Climatisation(Switch):
    def __init__(self):
        super().__init__(attr="climatisation", name="Climatisation", icon="mdi:radiator")

    @property
    def state(self):
        return self.vehicle.climatisation

    def turn_on(self):
        self.vehicle.start_climatisation()

    def turn_off(self):
        self.vehicle.stop_climatisation()

    @property
    def assumed_state(self):
        return False

class Charging(Switch):
    def __init__(self):
        super().__init__(attr="charging", name="Charging", icon="mdi:battery")

    @property
    def state(self):
        return self.vehicle.charging

    def turn_on(self):
        self.vehicle.start_charging()

    def turn_off(self):
        self.vehicle.stop_charging()

    @property
    def assumed_state(self):
        return False

class WindowHeater(Switch):
    def __init__(self):
        super().__init__(attr="window_heater", name="Window Heater", icon="mdi:car-defrost-rear")

    @property
    def state(self):
        return self.vehicle.window_heater

    def turn_on(self):
        self.vehicle.start_window_heater()

    def turn_off(self):
        self.vehicle.stop_window_heater()

    @property
    def assumed_state(self):
        return False

class CombustionEngineHeating(Switch):
    def __init__(self):
        super().__init__(attr="combustion_engine_heating", name="Combustion Engine Heating", icon="mdi:radiator")

    def configurate(self, **config):
        self.spin = config.get('spin', '')

    @property
    def state(self):
        return self.vehicle.combustion_engine_heating

    def turn_on(self):
        self.vehicle.start_combustion_engine_heating(self.spin)

    def turn_off(self):
        self.vehicle.stop_combustion_engine_heating()

    @property
    def assumed_state(self):
        return False

def create_instruments():
    return [
        Position(),
        DoorLock(),
        TrunkLock(),
        Climatisation(),
        ClimatisationClimate(),
        Charging(),
        WindowHeater(),
        CombustionEngineHeating(),
        Sensor(
            attr="distance",
            name="Odometer",
            icon="mdi:speedometer",
            unit="km",
        ),
        Sensor(
            attr="battery_level",
            name="Battery level",
            icon="mdi:battery",
            unit="%",
        ),
        Sensor(
            attr="fuel_level",
            name="Fuel level",
            icon="mdi:fuel",
            unit="%",
        ),
        Sensor(
            attr="service_inspection",
            name="Service inspection",
            icon="mdi:garage",
            unit="",
        ),
        Sensor(
            attr="oil_inspection",
            name="Oil inspection",
            icon="mdi:garage",
            unit="",
        ),
        Sensor(
            attr="last_connected",
            name="Last connected",
            icon="mdi:clock",
            unit="",
        ),
        Sensor(
            attr="charging_time_left",
            name="Charging time left",
            icon="mdi:battery-charging-100",
            unit="min",
        ),
        Sensor(
            attr="electric_range",
            name="Electric range",
            icon="mdi:car",
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
            attr="charge_max_ampere",
            name="Charger max ampere",
            icon="mdi:flash",
            unit="A",
        ),
        Sensor(
            attr="climatisation_target_temperature",
            name="Climatisation target temperature",
            icon="mdi:thermometer",
            unit="°C",
        ),
        BinarySensor(
            attr="external_power",
            name="External power",
            device_class="power"
        ),
        BinarySensor(
            attr="parking_light",
            name="Parking light",
            device_class="light"
        ),
        BinarySensor(
            attr="climatisation_without_external_power",
            name="Climatisation without external power",
            device_class="power"
        ),
        #BinarySensor(
        #    attr="door_locked",
        #    name="Doors locked",
        #    device_class="lock"
        #),
        #BinarySensor(
        #    attr="trunk_locked",
        #    name="Trunk locked",
        #    device_class="lock"
        #),
        BinarySensor(
            attr="request_in_progress",
            name="Request in progress",
            device_class="connectivity"
        ),
    ]

class Dashboard:
    def __init__(self, vehicle, **config):
        _LOGGER.debug("Setting up dashboard with config :%s", config)
        self.instruments = [
            instrument
            for instrument in create_instruments()
            if instrument.setup(vehicle, **config)
        ]