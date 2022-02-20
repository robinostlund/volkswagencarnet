"""Class for departure timer basic settings."""
import json
import logging
from datetime import datetime
from typing import Union, List, Optional, Dict

_LOGGER = logging.getLogger(__name__)


class DepartureTimerClass:
    _changed: bool = False

    @property
    def json(self):
        """Return JSON representation."""
        return json.loads(json.dumps({"timer": self}, default=self.serialize, indent=2))

    @property
    def json_updated(self):
        """Return JSON representation."""
        return json.loads(json.dumps({"timer": self}, default=self.serialize_updated, indent=2))

    def serialize_updated(self, o):
        if isinstance(o, datetime):
            return o.strftime("%Y-%m-%dT%H:%M:%S%z")

        res = {
            # filter out properties starting with "_"
            i: o.__dict__[i]
            for i in o.__dict__
            if i[0] != "_"
        }
        if hasattr(o, "timestamp") and not isinstance(o, datetime):
            if not o._changed:
                del res["timestamp"]
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
    """Basic settings."""

    def __init__(self, timestamp: str, chargeMinLimit: str, targetTemperature: str):
        """Init."""
        self.timestamp = timestamp
        self.chargeMinLimit = chargeMinLimit
        self.targetTemperature = targetTemperature


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
        else:
            self.departureTimeOfDay = departureTimeOfDay
            self.departureWeekdayMask = departureWeekdayMask

    @property
    def enabled(self):
        self._changed = True
        return self.timerProgrammedStatus == "programmed"

    def enable(self):
        self._changed = True
        self.timerProgrammedStatus = "programmed"

    def disable(self):
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
        profileName: str = "i-have-no-name",
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


# noinspection PyPep8Naming
class TimerProfileList(DepartureTimerClass):
    """FIXME."""

    def __init__(self, timerProfile: List[Union[dict, TimerProfile]]):
        """Init."""
        self.timerProfile = []
        for p in timerProfile:
            self.timerProfile.append(p if isinstance(p, TimerProfile) else TimerProfile(**p))


# noinspection PyPep8Naming
class TimerAndProfiles(DepartureTimerClass):
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

    def __init__(self, timersAndProfiles: Union[Dict, TimerAndProfiles], status: Optional[dict]):
        """Init."""
        try:
            self.timersAndProfiles = (
                timersAndProfiles
                if isinstance(timersAndProfiles, TimerAndProfiles)
                else TimerAndProfiles(**timersAndProfiles)
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
        """"""
        return self._valid and any(p.timerID == str(schedule_id) for p in self.timersAndProfiles.timerList.timer)

    def get_schedule(self, schedule_id: Union[str, int]):
        return next(filter(lambda p: p.timerID == str(schedule_id), self.timersAndProfiles.timerList.timer), None)
