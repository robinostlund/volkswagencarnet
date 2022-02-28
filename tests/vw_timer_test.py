"""Depature timer tests."""
from unittest import TestCase

from volkswagencarnet.vw_timer import TimerData, TimerProfile


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
                            "heaterSource": None,
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
                            "currentCalendarProvider": {},
                        },
                    ]
                },
                "timerBasicSetting": {
                    "timestamp": "2022-02-22T20:00:22Z",
                    "chargeMinLimit": 20,
                    "targetTemperature": 2955,
                    "heaterSource": None,
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
                            # "timestamp": "2022-02-22T20:00:22Z",
                            "timerID": "3",
                            "profileID": "3",
                            "timerProgrammedStatus": "programmed",
                            "timerFrequency": "cyclic",
                            "departureTimeOfDay": "07:55",
                            "departureWeekdayMask": "nnnnnyn",
                            "currentCalendarProvider": {},
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

    def test_get_profile(self):
        """Test profile getter."""
        timer = TimerData(**self.data["timer"])
        self.assertIsNone(timer.get_profile(42))
        self.assertIsNone(timer.get_profile("42"))

        profile = timer.get_profile(1)
        self.assertIsInstance(profile, TimerProfile)

        self.assertEqual("21:00", profile.nightRateTimeStart)

    def test_update_profile(self):
        """Test updating profiles."""
        timer = TimerData(**self.data["timer"])
        profile = timer.get_profile(1)

        self.assertNotEqual("unit test profile 42", profile.profileName)
        profile.profileName = "unit test profile 42"

        timer.update_profile(profile)

        profile = timer.get_profile(1)

        self.assertEqual("unit test profile 42", profile.profileName)

        profile.profileID = 42
        self.assertRaises(Exception, timer.update_profile(profile))
