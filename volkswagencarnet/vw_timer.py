"""Class for departure timer basic settings."""
import json
from typing import Union, List


# noinspection PyPep8Naming
class BasicSettings:
    """Basic settings."""

    def __init__(self, timestamp: str, chargeMinLimit: str, targetTemperature: str):
        """Init."""
        self.timestamp = timestamp
        self.chargeMinLimit = chargeMinLimit
        self.targetTemperature = targetTemperature


# noinspection PyPep8Naming
class Timer:
    """FIXME."""

    def __init__(
        self,
        timestamp: str,
        timerID: str,
        profileID: str,
        timerProgrammedStatus: str,
        timerFrequency: str,
        departureTimeOfDay: str,
        departureWeekdayMask: str,
    ):
        """Init."""
        self.timestamp = timestamp
        self.timerID = timerID
        self.profileID = profileID
        self.timerProgrammedStatus = timerProgrammedStatus
        self.timerFrequency = timerFrequency
        self.departureTimeOfDay = departureTimeOfDay
        self.departureWeekdayMask = departureWeekdayMask


class TimerList:
    """FIXME."""

    def __init__(self, timer: List[Union[dict, Timer]]):
        """Init."""
        self.timer = []
        for t in timer:
            self.timer.append(t if isinstance(t, Timer) else Timer(**t))


# noinspection PyPep8Naming
class TimerProfile:
    """Timer profile."""

    def __init__(
        self,
        timestamp: str,
        profileName: str,
        profileID: str,
        operationCharging: bool,
        operationClimatisation: bool,
        targetChargeLevel: str,
        nightRateActive: bool,
        nightRateTimeStart: str,
        nightRateTimeEnd: str,
        chargeMaxCurrent: str,
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
class TimerProfileList:
    """FIXME."""

    def __init__(self, timerProfile: List[Union[dict, TimerProfile]]):
        """Init."""
        self.timerProfile = []
        for p in timerProfile:
            self.timerProfile.append(p if isinstance(p, TimerProfile) else TimerProfile(**p))


# noinspection PyPep8Naming
class TimerAndProfiles:
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
class TimerData:
    """Top level timer object."""

    def __init__(self, timersAndProfiles: Union[dict, TimerAndProfiles], status: dict):
        """Init."""
        self.timersAndProfiles = (
            timersAndProfiles
            if isinstance(timersAndProfiles, TimerAndProfiles)
            else TimerAndProfiles(**timersAndProfiles)
        )
        self.status = status

    @property
    def json(self):
        """Return JSON representation."""
        return json.dumps({"timer": self}, default=lambda o: o.__dict__, indent=2)
