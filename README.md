# Volkswagen Carnet

[![buy me a coffee](https://www.buymeacoffee.com/assets/img/custom_images/yellow_img.png)](https://www.buymeacoffee.com/robinostlund)

![Release](https://img.shields.io/github/workflow/status/robinostlund/volkswagencarnet/Release)
![PyPi](https://img.shields.io/pypi/v/volkswagencarnet)
![Version](https://img.shields.io/github/v/release/robinostlund/volkswagencarnet)
![CodeStyle](https://img.shields.io/badge/code%20style-black-black)
![Known Vulnerabilities](https://snyk.io/test/github/robinostlund/volkswagencarnet/badge.svg)
[![CodeQL](https://github.com/robinostlund/volkswagencarnet/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/robinostlund/volkswagencarnet/actions/workflows/codeql-analysis.yml)
[![codecov](https://codecov.io/gh/robinostlund/volkswagencarnet/branch/master/graph/badge.svg?token=NH1Q1GH4I3)](https://codecov.io/gh/robinostlund/volkswagencarnet)


![Downloads a day](https://img.shields.io/pypi/dd/volkswagencarnet)
![Downloads a week](https://img.shields.io/pypi/dw/volkswagencarnet)
![Downloads a month](https://img.shields.io/pypi/dm/volkswagencarnet)

## Information

Retrieve statistics about your Volkswagen from the Volkswagen Carnet online service

No licence, public domain, no guarantees, feel free to use for anything. Please contribute improvements/bugfixes etc.

## Thanks to

- [Wez3](https://github.com/wez3)
- [Reneboer](https://github.com/reneboer)
- [Tubalainen](https://github.com/tubalainen)
- [JohNan](https://github.com/JohNan)
- [milkboy](https://github.com/milkboy)

For supporting and helping in this project.

## Other related repositories

- [HomeAssistant Component](https://github.com/robinostlund/homeassistant-volkswagencarnet) a custom component for Home Assistant

## Installation

```sh
[venv-python3] user@localhost:~
$ pip install volkswagencarnet
```

### Example

```python
#!/usr/bin/env python3
from volkswagencarnet.vw_connection import Connection
import pprint
import asyncio
import logging

from aiohttp import ClientSession

logging.basicConfig(level=logging.DEBUG)

VW_USERNAME='test@example.com'
VW_PASSWORD='mysecretpassword'


COMPONENTS = {
    'sensor': 'sensor',
    'binary_sensor': 'binary_sensor',
    'lock': 'lock',
    'device_tracker': 'device_tracker',
    'switch': 'switch',
    'climate': 'climate'
}

RESOURCES = [
    'position',
    'distance',
    'electric_climatisation',
    'combustion_climatisation',
    'window_heater',
    'combustion_engine_heating',
    'charging',
    'adblue_level',
    'battery_level',
    'fuel_level',
    'service_inspection',
    'oil_inspection',
    'last_connected',
    'charging_time_left',
    'electric_range',
    'combustion_range',
    'combined_range',
    'charge_max_ampere',
    'climatisation_target_temperature',
    'external_power',
    'parking_light',
    'climatisation_without_external_power',
    'door_locked',
    'trunk_locked',
    'request_in_progress',
    'windows_closed',
    'sunroof_closed',
    'trip_last_average_speed',
    'trip_last_average_electric_consumption',
    'trip_last_average_fuel_consumption',
    'trip_last_duration',
    'trip_last_length'
]

def is_enabled(attr):
    """Return true if the user has enabled the resource."""
    return attr in RESOURCES

async def main():
    """Main method."""
    async with ClientSession(headers={'Connection': 'keep-alive'}) as session:
        connection = Connection(session, VW_USERNAME, VW_PASSWORD)
        if await connection.doLogin():
            if await connection.update():
                # Print overall state
                pprint.pprint(connection._state)

                # Print vehicles
                for vehicle in connection.vehicles:
                    pprint.pprint(vehicle)

                # get all instruments
                instruments = set()
                for vehicle in connection.vehicles:
                    dashboard = vehicle.dashboard(mutable=True)

                    for instrument in (
                            instrument
                            for instrument in dashboard.instruments
                            if instrument.component in COMPONENTS
                            and is_enabled(instrument.slug_attr)):

                        instruments.add(instrument)

                # Output all supported instruments
                for instrument in instruments:
                    print(f'name: {instrument.full_name}')
                    print(f'str_state: {instrument.str_state}')
                    print(f'state: {instrument.state}')
                    print(f'supported: {instrument.is_supported}')
                    print(f'attr: {instrument.attr}')
                    print(f'attributes: {instrument.attributes}')

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    # loop.run(main())
    loop.run_until_complete(main())
```

## Development
I'd strongly advise installing the git pre-commit hook using `pre-commit install`. See [pre-commit.com](https://pre-commit.com/) for details.
Some basic checks are performed before you commit the code, so code style issues
will be visible and fixable before creating the PR. Git pre-commit hooks can
always be skipped using the `--no-verify` flag to `git commit`, if there
is something preventing you from actually fixing the reported (and non-auto-fixed) issues.

Decent test coverage for any new or changed code is also much appreciated :)
