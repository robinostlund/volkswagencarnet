# Example Responses

The sub-directories contain some examples for responses of VW's API for certain car types:

* [Arteon Diesel](arteon_2023_diesel)
* [eUP! Electric](eup_electric)
* [Golf GTE Hybrid](golf_gte_hybrid)

## Files

`capabilities.json`

Response to the GET request to https://emea.bff.cariad.digital/vehicle/v1/vehicles/{vin}/capabilities

`last_trip.json`

Response to the GET request to https://emea.bff.cariad.digital/vehicle/v1/trips/{vin}/shortterm/last

`parkingposition.json`

Response to the GET request to https://emea.bff.cariad.digital/vehicle/v1/vehicles/{vin}/parkingposition

`selectivestatus_by_app.json`

Response to the GET request to https://emea.bff.cariad.digital/vehicle/v1/vehicles/{vin}/selectivestatus?jobs=XXX.
The exact URL is the one the Volkswagen app fires for the respective car type.