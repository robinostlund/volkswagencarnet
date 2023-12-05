from volkswagencarnet.vw_connection import Connection
import volkswagencarnet.vw_const as const
from tests.credentials import username, password

from aiohttp import ClientSession
import pprint
import asyncio
import logging

logging.basicConfig(level=logging.DEBUG)

VW_USERNAME=username
VW_PASSWORD=password

SERVICES = ["access",
            "fuelStatus",
            "vehicleLights",
            "vehicleHealthInspection",
            "measurements",
            "charging",
            "climatisation",
            "automation"]

async def main():
    """Main method."""
    async with ClientSession(headers={'Connection': 'keep-alive'}) as session:
        connection = Connection(session, VW_USERNAME, VW_PASSWORD)
        request_count = 0
        if await connection.doLogin():
            logging.info(f"Logged in to account {VW_USERNAME}")
            logging.info("Tokens:")
            logging.info(pprint.pformat(connection._session_tokens))

            vehicle = connection.vehicles[0].vin
            error_count = 0
            while True:
                await connection.validate_tokens
                #response = await connection.get(
                #    f"{const.BASE_API}/vehicle/v1/vehicles/{vehicle}/selectivestatus?jobs={','.join(SERVICES)}", ""
                #)ng
                response = await connection.get(
                    f"{const.BASE_API}/vehicle/v1/trips/{vehicle}/shortterm/last", ""
                )


                request_count += 1
                logging.info(f"Request count is {request_count} with {len(SERVICES)} services, response: {pprint.pformat(response)[1:100]}")
                if "status_code" in response:
                    if response["status_code"] != 403:
                        logging.error(f"Something went wrong, received status code {response.get('status_code')}, bailing out")
                        exit(-1)
                    error_count += 1
                    if error_count > 3:
                        logging.error("More than 3 errors in a row, bailing out")
                else:
                    error_count = 0

                await asyncio.sleep(1)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    # loop.run(main())
    loop.run_until_complete(main())