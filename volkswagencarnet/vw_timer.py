"""Class for departure timer basic settings."""
import json
import logging
from datetime import datetime
from typing import Union, List, Optional, Dict

from volkswagencarnet.vw_utilities import celsius_to_vw, fahrenheit_to_vw, vw_to_celsius

_LOGGER = logging.getLogger(__name__)


class DepartureTimerClass:
    """Base class for timer related classes."""

    _changed: bool = False

    @property
    def is_changed(self):
        """Something changed."""
        return self._changed

    @property
    def json(self):
        """Return JSON representation."""
        return json.loads(json.dumps({"timer": self}, default=self.serialize, indent=2))

    @property
    def json_updated(self):
        """Return JSON representation."""
        return json.loads(json.dumps({"timer": self}, default=self.serialize_updated, indent=2))

    def serialize_updated(self, o):
        """Serialize object into JSON format, skipping extra field for update call."""
        if isinstance(o, datetime):
            return o.strftime("%Y-%m-%dT%H:%M:%S%z")

        res = {
            # filter out properties starting with "_"
            i: o.__dict__[i]
            for i in o.__dict__
            if i[0] != "_"
        }
        if issubclass(type(o), DepartureTimerClass) and hasattr(o, "timestamp"):
            res.pop("timestamp", None)
        # Remove any None valued keys
        nones = []
        for k in res:
            if res[k] is None:
                nones.append(k)
        for k in nones:
            res.pop(k, None)
        return res

    def serialize(self, o):
        """Serialize timers as JSON."""
        res = {
            # filter out properties starting with "_"
            i: o.__dict__[i]
            for i in o.__dict__
            if i[0] != "_"
        }
        for k, v in res.items():
            if isinstance(v, datetime):
                res["k"] = v.strftime("%Y-%m-%dT%H:%M:%S%z")
        return res


# noinspection PyPep8Naming
class BasicSettings(DepartureTimerClass):
    """
    Basic settings.

    EV:s might have a target temperature, and PHEVs have a heaterSource...
    """

    def __init__(
        self,
        timestamp: str,
        chargeMinLimit: Union[str, int] = None,
        targetTemperature: Optional[Union[str, int]] = None,
        heaterSource: Optional[str] = None,
    ):
        """Init."""
        self.timestamp = timestamp
        self.chargeMinLimit: Optional[int] = int(chargeMinLimit) if chargeMinLimit is not None else None
        self.targetTemperature: Optional[int] = int(targetTemperature) if targetTemperature is not None else None
        self.heaterSource: Optional[str] = heaterSource

    @property
    def target_temperature_celsius(self):
        """Get target temperature in Celsius."""
        if self.targetTemperature is None:
            return None
        return vw_to_celsius(self.targetTemperature)

    def set_target_temperature_celsius(self, temp: float):
        """Set target temperature for departure timers with climatisation enabled."""
        if self.targetTemperature is None:
            raise ValueError("This vehicle does not support setting the target temperature using timer settings.")
        new_temp = celsius_to_vw(temp)
        if new_temp != self.targetTemperature:
            self.targetTemperature = new_temp
            self._changed = True

    def set_target_temperature_fahrenheit(self, temp: float):
        """Set target temperature for departure timers with climatisation enabled."""
        if self.targetTemperature is None:
            raise ValueError("This vehicle does not support setting the target temperature using timer settings.")
        new_temp = fahrenheit_to_vw(temp)
        if new_temp != self.targetTemperature:
            self.targetTemperature = new_temp
            self._changed = True

    def set_charge_min_limit(self, limit: int):
        """Set the global minimum charge limit."""
        self.chargeMinLimit = limit
        self._changed = True

    def set_heater_source(self, heater_source: str):
        """Set the heater source (electric or auxiliary)."""
        if self.heaterSource is None:
            raise ValueError("Looks like this vehicle does not support setting heater source")
        if heater_source in ["electric", "auxiliary"]:
            self.heaterSource = heater_source
        else:
            raise ValueError(
                f"Unknown heater source {heater_source}. If you believe it should be supported, please open a bug ticket."
            )


# noinspection PyPep8Naming
class Timer(DepartureTimerClass):
    """FIXME."""

    def __init__(
        self,
        timestamp: str,
        timerID: str,
        profileID: str,
        timerProgrammedStatus: str,
        timerFrequency: str,
        departureTimeOfDay: str = None,
        departureWeekdayMask: str = None,
        departureDateTime: str = None,
        **kw,
    ):
        """Init."""
        self.timestamp = timestamp
        self.timerID = timerID
        self.profileID = profileID
        self.timerProgrammedStatus = timerProgrammedStatus
        self.timerFrequency = timerFrequency
        # single timers have a specific date, cyclic have time and day mask
        if timerFrequency == "single":
            self.departureDateTime = departureDateTime
            self.departureTimeOfDay = "00:00"
        else:
            self.departureTimeOfDay = departureTimeOfDay if departureTimeOfDay else "00:00"
            self.departureWeekdayMask = departureWeekdayMask
        self.currentCalendarProvider: dict = {}
        for k in kw:
            _LOGGER.debug(f"Timer: Got unhandled property {k} with value {kw[k]}")

    @property
    def enabled(self):
        """Check if departure timer is enabled."""
        self._changed = True
        return self.timerProgrammedStatus == "programmed"

    def enable(self):
        """Turn departure timer on."""
        self._changed = True
        self.timerProgrammedStatus = "programmed"

    def disable(self):
        """Turn departure timer off."""
        self.timerProgrammedStatus = "notProgrammed"


class TimerList(DepartureTimerClass):
    """FIXME."""

    def __init__(self, timer: List[Union[dict, Timer]]):
        """Init."""
        self.timer = []
        for t in timer:
            self.timer.append(t if isinstance(t, Timer) else Timer(**t))


# noinspection PyPep8Naming
class TimerProfile(DepartureTimerClass):
    """Timer profile."""

    def __init__(
        self,
        timestamp: str,
        profileID: str,
        operationCharging: bool,
        operationClimatisation: bool,
        targetChargeLevel: str,
        nightRateActive: bool,
        nightRateTimeStart: str,
        nightRateTimeEnd: str,
        chargeMaxCurrent: str,
        profileName: str = "",
        heaterSource: Optional[str] = None,
    ):
        """Init."""
        self.timestamp = timestamp
        self.profileName = profileName
        self.profileID = profileID
        self.operationCharging = operationCharging
        self.operationClimatisation = operationClimatisation
        self.targetChargeLevel = targetChargeLevel
        self.nightRateActive = nightRateActive
        self.nightRateTimeStart = nightRateTimeStart
        self.nightRateTimeEnd = nightRateTimeEnd
        self.chargeMaxCurrent = chargeMaxCurrent
        self.heaterSource: Optional[str] = heaterSource


# noinspection PyPep8Naming
class TimerProfileList(DepartureTimerClass):
    """Holder for timers and profiles array."""

    def __init__(self, timerProfile: List[Union[dict, TimerProfile]]):
        """Init."""
        self.timerProfile = []
        for p in timerProfile:
            self.timerProfile.append(p if isinstance(p, TimerProfile) else TimerProfile(**p))


# noinspection PyPep8Naming
class TimersAndProfiles(DepartureTimerClass):
    """Timer and profile object."""

    def __init__(
        self,
        timerProfileList: Union[dict, TimerProfileList],
        timerList: Union[dict, TimerList],
        timerBasicSetting: Union[dict, BasicSettings],
    ):
        """Init."""
        self.timerProfileList = (
            timerProfileList if isinstance(timerProfileList, TimerProfileList) else TimerProfileList(**timerProfileList)
        )
        self.timerList = timerList if isinstance(timerList, TimerList) else TimerList(**timerList)
        self.timerBasicSetting = (
            timerBasicSetting if isinstance(timerBasicSetting, BasicSettings) else BasicSettings(**timerBasicSetting)
        )


# noinspection PyPep8Naming
class TimerData(DepartureTimerClass):
    """Top level timer object."""

    def __init__(self, timersAndProfiles: Union[Dict, TimersAndProfiles], status: Optional[dict] = None):
        """Init."""
        try:
            self.timersAndProfiles = (
                timersAndProfiles
                if isinstance(timersAndProfiles, TimersAndProfiles)
                else TimersAndProfiles(**timersAndProfiles)
            )
            self.status = status
            self._valid = True
        except Exception as e:
            _LOGGER.error(e)
            self._valid = False

    @property
    def valid(self):
        """Values have been loaded."""
        return self._valid

    def has_schedule(self, schedule_id: Union[str, int]):
        """Check if timer exists by id."""
        return self._valid and any(p.timerID == str(schedule_id) for p in self.timersAndProfiles.timerList.timer)

    def get_schedule(self, id: Union[str, int]):
        """Find timer by id."""
        return next(filter(lambda p: p.timerID == str(id), self.timersAndProfiles.timerList.timer), None)

    def get_profile(self, id: Union[str, int]):
        """Find profile by id."""
        return next(
            filter(lambda p: p.profileID == str(id), self.timersAndProfiles.timerProfileList.timerProfile), None
        )

    def update_profile(self, profile: TimerProfile) -> None:
        """Replace a profile with given input."""
        for p in self.timersAndProfiles.timerProfileList.timerProfile:
            if str(p.profileID) == str(profile.profileID):
                # hackish way to update all properties, but easier than replacing
                # the actual object
                p.__dict__.update(profile.__dict__)
                return
        raise Exception("Profile not found")
