### IN DEVELOPMENT
Volkswagen Carnet
============================================================
Retrieve statistics about your Volkswagen from the Volkswagen Carnet online service
No licence, public domain, no guarantees, feel free to use for anything. Please contribute improvements/bugfixes etc.

Installation
--------------
```sh
[venv-python3] user@localhost:~
$ pip install volkswagencarnet
```

### Example
```python
#!/usr/bin/env python3
import volkswagencarnet

vw = volkswagencarnet.Connection('username', 'password')
# login to carnet
vw._login()
# get vehicles from carnet
vw.update()

# parse vehicles
vehicles = vw.vehicles
for vehicle in vehicles:
    # output vehicle id
    print(vehicle)
    # check if position is supported
    print(vehicle.position_supported)
    # check if climatisation is supported
    print(vehicle.climatisation_supported)
    # and more

# action: start climatisation
vw.vehicle('my vehicle id').start_climatisation()
# action: stop climatisation
vw.vehicle('my vehicle id').stop_climatisation()

```