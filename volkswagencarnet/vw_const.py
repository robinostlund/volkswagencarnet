"""Constants for Volkswagen Connect library."""

BASE_SESSION = "https://msg.volkswagen.de"
BASE_AUTH = "https://identity.vwgroup.io"
BASE_API = "https://emea.bff.cariad.digital"
BRAND = "VW"
COUNTRY = "DE"

# Data used in communication
CLIENT = {
    "Legacy": {
        "CLIENT_ID": "a24fba63-34b3-4d43-b181-942111e6bda8@apps_vw-dilab_com",
        "SCOPE": "openid profile badge cars dealers vin",
        "TOKEN_TYPES": "code",
    }
}

USER_AGENT = "Volkswagen/3.51.1-android/14"
APP_URI = "weconnect://authenticated"
ANDROID_PACKAGE_NAME = "com.volkswagen.weconnect"

# Used when fetching data
HEADERS_SESSION = {
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Accept-charset": "UTF-8",
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
    "tokentype": "IDK_TECHNICAL",
    "x-android-package-name": ANDROID_PACKAGE_NAME,
}

# Used for authentication
HEADERS_AUTH = {
    "Connection": "keep-alive",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Content-Type": "application/x-www-form-urlencoded",
    "x-android-package-name": ANDROID_PACKAGE_NAME,
}

TEMP_CELSIUS: str = "°C"
TEMP_FAHRENHEIT: str = "°F"


class VWStateClass:
    """Supported state classes."""

    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class VWDeviceClass:
    """Supported sensor entity device classes."""

    BATTERY = "battery"
    CONNECTIVITY = "connectivity"
    CURRENT = "current"
    DISTANCE = "distance"
    DOOR = "door"
    DURATION = "duration"
    ENERGY = "energy"
    ENERGY_DISTANCE = "energy_distance"
    LIGHT = "light"
    LOCK = "lock"
    MOVING = "moving"
    PLUG = "plug"
    POWER = "power"
    SPEED = "speed"
    TEMPERATURE = "temperature"
    TIMESTAMP = "timestamp"
    VOLUME = "volume"
    WINDOW = "window"


class VehicleStatusParameter:
    """Hex codes for vehicle status parameters."""

    FRONT_LEFT_DOOR_LOCK = "0x0301040001"
    FRONT_RIGHT_DOOR_LOCK = "0x0301040007"
    REAR_LEFT_DOOR_LOCK = "0x0301040004"
    READ_RIGHT_DOOR_LOCK = "0x030104000A"

    FRONT_LEFT_DOOR_CLOSED = "0x0301040002"
    FRONT_RIGHT_DOOR_CLOSED = "0x0301040008"
    REAR_LEFT_DOOR_CLOSED = "0x0301040005"
    REAR_RIGHT_DOOR_CLOSED = "0x030104000B"

    HOOD_CLOSED = "0x0301040011"

    TRUNK_LOCK = "0x030104000D"
    TRUNK_CLOSED = "0x030104000E"

    FRONT_LEFT_WINDOW_CLOSED = "0x0301050001"
    FRONT_RIGHT_WINDOW_CLOSED = "0x0301050005"
    REAR_LEFT_WINDOW_CLOSED = "0x0301050003"
    REAR_RIGHT_WINDOW_CLOSED = "0x0301050007"
    SUNROOF_CLOSED = "0x030105000B"

    PRIMARY_RANGE = "0x0301030006"
    SECONDARY_RANGE = "0x0301030008"

    PRIMARY_DRIVE = "0x0301030007"
    SECONDARY_DRIVE = "0x0301030009"
    COMBINED_RANGE = "0x0301030005"
    FUEL_LEVEL = "0x030103000A"

    PARKING_LIGHT = "0x0301010001"

    ODOMETER = "0x0101010002"

    DAYS_TO_SERVICE_INSPECTION = "0x0203010004"
    DISTANCE_TO_SERVICE_INSPECTION = "0x0203010003"

    DAYS_TO_OIL_INSPECTION = "0x0203010002"
    DISTANCE_TO_OIL_INSPECTION = "0x0203010001"

    ADBLUE_LEVEL = "0x02040C0001"

    OUTSIDE_TEMPERATURE = "0x0301020001"

    VALID_DOOR_STATUS = ["open", "closed"]
    VALID_WINDOW_STATUS = ["open", "closed"]


class Services:
    """Service names that are used in `capabilities` and `selectivestatus` calls."""

    # callable via `selectivestatus`
    ACCESS = "access"
    AUTOMATION = "automation"
    BATTERY_CHARGING_CARE = "batteryChargingCare"
    BATTERY_SUPPORT = "batterySupport"
    CHARGING = "charging"
    CLIMATISATION = "climatisation"
    CLIMATISATION_TIMERS = "climatisationTimers"
    DEPARTURE_PROFILES = "departureProfiles"
    DEPARTURE_TIMERS = "departureTimers"
    FUEL_STATUS = "fuelStatus"
    MEASUREMENTS = "measurements"
    USER_CAPABILITIES = "userCapabilities"
    VEHICLE_LIGHTS = "vehicleLights"
    VEHICLE_HEALTH_INSPECTION = "vehicleHealthInspection"

    # callable via explicit endpoints
    HONK_AND_FLASH = "honkAndFlash"
    PARKING_POSITION = "parkingPosition"
    TRIP_STATISTICS = "tripStatistics"

    # internally used services names
    PARAMETERS = "parameters"
    SERVICE_STATUS = "service_status"
    TRIP_LAST = "trip_last"
    TRIP_LONGTERM = "trip_longterm"
    TRIP_REFUEL = "trip_refuel"


class Paths:
    # Access
    ACCESS_TS = f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp"
    ACCESS_DOOR_LOCK = f"{Services.ACCESS}.accessStatus.value.doorLockStatus"
    ACCESS_DOORS = f"{Services.ACCESS}.accessStatus.value.doors"
    ACCESS_WINDOWS = f"{Services.ACCESS}.accessStatus.value.windows"

    # Lights
    LIGHTS = f"{Services.VEHICLE_LIGHTS}.lightsStatus.value.lights"
    LIGHTS_TS = f"{Services.VEHICLE_LIGHTS}.lightsStatus.value.carCapturedTimestamp"

    # Charging status
    CHARGING_STATE = f"{Services.CHARGING}.chargingStatus.value.chargingState"
    CHARGING_TS = f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp"
    CHARGING_POWER = f"{Services.CHARGING}.chargingStatus.value.chargePower_kW"
    CHARGING_RATE = f"{Services.CHARGING}.chargingStatus.value.chargeRate_kmph"
    CHARGING_TYPE = f"{Services.CHARGING}.chargingStatus.value.chargeType"
    CHARGING_TIME_LEFT = (
        f"{Services.CHARGING}.chargingStatus.value.remainingChargingTimeToComplete_min"
    )

    # Plug
    PLUG_LOCK = f"{Services.CHARGING}.plugStatus.value.plugLockState"
    PLUG_CONN = f"{Services.CHARGING}.plugStatus.value.plugConnectionState"
    PLUG_EXT_PWR = f"{Services.CHARGING}.plugStatus.value.externalPower"
    PLUG_TS = f"{Services.CHARGING}.plugStatus.value.carCapturedTimestamp"

    # Battery status/settings
    BATTERY_SOC = f"{Services.CHARGING}.batteryStatus.value.currentSOC_pct"
    BATTERY_RANGE_E = (
        f"{Services.CHARGING}.batteryStatus.value.cruisingRangeElectric_km"
    )
    BATTERY_TS = f"{Services.CHARGING}.batteryStatus.value.carCapturedTimestamp"

    CHARGING_SET_TS = f"{Services.CHARGING}.chargingSettings.value.carCapturedTimestamp"
    CHARGING_SET_TARGET_SOC = (
        f"{Services.CHARGING}.chargingSettings.value.targetSOC_pct"
    )
    CHARGING_SET_MAX_CHARGE_AC = (
        f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC"
    )
    CHARGING_SET_MAX_CHARGE_AC_A = (
        f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC_A"
    )
    CHARGING_SET_AUTO_UNLOCK_PLUG = (
        f"{Services.CHARGING}.chargingSettings.value.autoUnlockPlugWhenChargedAC"
    )
