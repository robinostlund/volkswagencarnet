#!/usr/bin/env python3
from volkswagencarnet.vw_connection import Connection
import pprint
import asyncio
import logging

from aiohttp import ClientSession

logging.basicConfig(level=logging.DEBUG)


#VW_USERNAME='oliver@rahner.me'
#VW_PASSWORD='85-C3-A1-7D-7F-D2'

VW_USERNAME='michi@mischit.de'
VW_PASSWORD='!xdAqRA2Z5oBXr'

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
    'door_closed_left_front',
    'door_closed_right_front',
    'door_closed_left_back',
    'door_closed_right_back',
    'trunk_closed',
    'hood_closed',
    'trunk_locked',
    'request_in_progress',
    'window_closed_left_front',
    'window_closed_right_front',
    'window_closed_left_back',
    'window_closed_right_back',
    'sunroof_closed',
    'roof_cover_closed',
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

                    for instrument in dashboard.instruments:
                        if instrument.component not in COMPONENTS:
                            print(f'instrument {instrument.attr} is not in COMPONENTS')
                            continue
                        if not is_enabled(instrument.slug_attr):
                            print(f'instrument {instrument.attr} is not enabled')
                            continue

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