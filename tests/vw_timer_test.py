"""Depature timer tests."""
from json import loads
from unittest import TestCase

from volkswagencarnet.vw_timer import TimerData


class TimerTest(TestCase):
    """Top level tests."""

    data = {
        "timer": {
            "timersAndProfiles": {
                "timerProfileList": {
                    "timerProfile": [
                        {
                            "timestamp": "2022-02-22T20:00:22Z",
                            "profileName": "Profile 1",
                            "profileID": "1",
                            "operationCharging": True,
                            "operationClimatisation": False,
                            "targetChargeLevel": "75",
                            "nightRateActive": True,
                            "nightRateTimeStart": "21:00",
                            "nightRateTimeEnd": "05:00",
                            "chargeMaxCurrent": "10",
                        }
                    ]
                },
                "timerList": {
                    "timer": [
                        {
                            "timestamp": "2022-02-22T20:00:22Z",
                            "timerID": "3",
                            "profileID": "3",
                            "timerProgrammedStatus": "notProgrammed",
                            "timerFrequency": "cyclic",
                            "departureTimeOfDay": "07:55",
                            "departureWeekdayMask": "nnnnnyn",
                        },
                    ]
                },
                "timerBasicSetting": {
                    "timestamp": "2022-02-22T20:00:22Z",
                    "chargeMinLimit": "20",
                    "targetTemperature": "2955",
                },
            },
            "status": {"timer": []},
        }
    }

    def test_timer_serialization(self):
        """Test de- ans serialization of timers."""
        timer = TimerData(**self.data["timer"])
        self.assertEqual(self.data, loads(timer.json))
