"""
VW CarNet NA Vehicle Demo

Displays real GPS coordinates and lock status for your VW Car-Net (NA) vehicle.

Usage:
    export VW_USERNAME='your-email@example.com'
    export VW_PASSWORD='your-password'
    export VW_SPIN='1234'       # 4-digit Security PIN (required for vehicle data)

    python examples/demo_na_vehicle.py

Requirements: Set VW_USERNAME and VW_PASSWORD at minimum. VW_SPIN is required
to retrieve GPS location and door lock status -- without it, vehicle data endpoints
return 500 from SpinService.
"""

import asyncio
import os
import sys

from aiohttp import ClientSession, CookieJar

from volkswagencarnet.vw_connection import Connection


async def main() -> None:
    username = os.environ.get("VW_USERNAME")
    password = os.environ.get("VW_PASSWORD")
    spin = os.environ.get("VW_SPIN")

    if not username or not password:
        print("ERROR: VW_USERNAME and VW_PASSWORD environment variables are required.")
        print("")
        print("Example:")
        print("  export VW_USERNAME='your-email@example.com'")
        print("  export VW_PASSWORD='your-password'")
        print("  export VW_SPIN='1234'")
        print("  python examples/demo_na_vehicle.py")
        sys.exit(1)

    if not spin:
        print("WARNING: VW_SPIN is not set. Vehicle data endpoints (GPS, lock status)")
        print("         may return errors without the 4-digit Security PIN.")
        print("")

    print("Connecting to VW CarNet (North America)...")
    print(f"  Username: {username}")
    print(f"  SPIN: {'set' if spin else 'NOT SET'}")
    print("")

    jar = CookieJar()
    async with ClientSession(cookie_jar=jar) as session:
        conn = Connection(session, username, password, country="US", spin=spin)

        print("Logging in...")
        success = await conn.doLogin()
        if not success:
            print("ERROR: Login failed. Check your credentials and try again.")
            sys.exit(1)

        print("Login successful.")
        print("")

        print("Fetching vehicle data...")
        await conn.update()
        print("")

        vehicles = conn.vehicles
        if not vehicles:
            print("No vehicles found in your garage.")
            sys.exit(0)

        print(f"Found {len(vehicles)} vehicle(s):")
        print("")

        for vehicle in vehicles:
            print(f"VIN: {vehicle.vin}")

            # Position / GPS
            position = vehicle.position
            if position is not None:
                lat = position.get("lat")
                lng = position.get("lng")
                timestamp = position.get("timestamp")
                print(f"  Position: lat={lat}, lng={lng}")
                print(f"  Timestamp: {timestamp}")
            else:
                print("  Position: not available")

            # Door lock status
            if vehicle.is_door_locked_supported:
                locked = vehicle.door_locked
                print(f"  Doors locked: {locked}")
            else:
                # Try reading door_locked directly (NA vehicles may not set the support flag)
                try:
                    locked = vehicle.door_locked
                    print(f"  Doors locked: {locked}")
                except Exception:
                    print("  Doors locked: not available")

            print("")


if __name__ == "__main__":
    asyncio.run(main())
