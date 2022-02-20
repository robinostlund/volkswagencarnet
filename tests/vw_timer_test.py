"""Depature timer tests."""
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
                    "chargeMinLimit": 20,
                    "targetTemperature": 2955,
                },
            },
            "status": {"timer": []},
        }
    }

    data_updated = {
        "timer": {
            "timersAndProfiles": {
                "timerProfileList": {
                    "timerProfile": [
                        {
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
                            "timerProgrammedStatus": "programmed",
                            "timerFrequency": "cyclic",
                            "departureTimeOfDay": "07:55",
                            "departureWeekdayMask": "nnnnnyn",
                        },
                    ]
                },
                "timerBasicSetting": {
                    "chargeMinLimit": 20,
                    "targetTemperature": 2955,
                },
            },
            "status": {"timer": []},
        }
    }

    def test_timer_serialization(self):
        """Test de- and serialization of timers."""
        timer = TimerData(**self.data["timer"])
        self.assertEqual(self.data, timer.json)
        self.assertNotEqual(timer.json, timer.json_updated)

    def test_update_serialization(self):
        """Check that updating a timer sets correct attributes."""
        timer = TimerData(**self.data["timer"])
        timer.get_schedule(3).enable()
        self.assertTrue(timer.get_schedule(3)._changed)
        self.assertEqual(self.data_updated, timer.json_updated)
