# Volkswagen Carnet

![Python package](https://github.com/robinostlund/volkswagencarnet/workflows/Python%20package/badge.svg)
![Upload Python Package](https://github.com/robinostlund/volkswagencarnet/workflows/Upload%20Python%20Package/badge.svg)

![Downloads a day](https://img.shields.io/pypi/dd/volkswagencarnet?label=Downloads)
![Downloads a week](https://img.shields.io/pypi/dw/volkswagencarnet?label=Downloads%20)
![Downloads a month](https://img.shields.io/pypi/dm/volkswagencarnet?label=Downloads%20)

![Latest PyPi Version](https://img.shields.io/pypi/v/volkswagencarnet?label=Latest%20PyPi%20Version)

## Information

Retrieve statistics about your Volkswagen from the Volkswagen Carnet online service

No licence, public domain, no guarantees, feel free to use for anything. Please contribute improvements/bugfixes etc.

## Thanks to

- [Wez3](https://github.com/wez3)
- [Reneboer](https://github.com/reneboer)
- [Tubalainen](https://github.com/tubalainen)

For supporting and helping in this project.

## Other related repositories

- [HomeAssistant Component](https://github.com/robinostlund/homeassistant-volkswagencarnet) a custom component for Home Assistant
- [VolkswagenCarnetClient](https://github.com/robinostlund/volkswagencarnet-client) a cli version of this library

## Installation

```sh
[venv-python3] user@localhost:~
$ pip install volkswagencarnet
```

### Example

```python
#!/usr/bin/env python3
import sys
import volkswagencarnet

vw = volkswagencarnet.Connection('username', 'password')
# login to carnet
vw._login()
if not vw.logged_in:
    print('Could not login to carnet')
    sys.exit(1)

# get vehicles from carnet
vw.update()

# parse vehicles
vehicles = vw.vehicles
for vehicle in vehicles:
    print('Vehicle VIN: %s' % vehicle.vin)

    print('This vehicle supports:')
    print(' Position: %s' % vehicle.position_supported)
    print(' Climatisation: %s' % vehicle.climatisation_supported)
    print(' Service Inspection: %s' % vehicle.service_inspection_supported)
    print(' Battery Level: %s' % vehicle.battery_level_supported)
    print(' Parking Light: %s' % vehicle.parking_light_supported)
    print(' Distance: %s' % vehicle.distance_supported)
    print(' Model: %s' % vehicle.model_supported)
    print(' Model Year: %s' % vehicle.model_year_supported)
    print(' Model Image: %s' % vehicle.model_image_supported)
    print(' Charging: %s' % vehicle.charging_supported)
    print(' External Power: %s' % vehicle.external_power_supported)
    print(' Window Heater: %s' % vehicle.window_heater_supported)
    print(' Charging time left: %s' % vehicle.charging_time_left_supported)
    print(' Door Locked: %s' % vehicle.door_locked_supported)
    print(' Electric Range: %s' % vehicle.electric_range_supported)
    print(' Combustion Engine Heating: %s' % vehicle.combustion_engine_heating_supported)

    print('Vehicle information:')
    print(' Distance: %s' % vehicle.distance)
    print(' Last Connected: %s' % vehicle.last_connected)
    print(' Next Service: %s' % vehicle.service_inspection)
    print(' Charging Time Left: %s' % vehicle.charging_time_left)
    print(' Electric Range: %s' % vehicle.electric_range)

    print('Vehicle States:')
    print(' Is Doors Locked: %s' % vehicle.is_doors_locked)
    print(' Is Climatisation On: %s' % vehicle.is_climatisation_on)
    print(' Is Parking Lights On: %s' % vehicle.is_parking_lights_on)
    print(' Is Window Heater On: %s' % vehicle.is_window_heater_on)
    print(' Is Charging On: %s' % vehicle.is_charging_on)
    print(' Is Request in progress: %s' % vehicle.is_request_in_progress)

    # and more

# action: start climatisation
vw.vehicle('my vehicle id').start_climatisation()
# action: stop climatisation
vw.vehicle('my vehicle id').stop_climatisation()

```
