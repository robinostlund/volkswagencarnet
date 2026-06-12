"""Constants for Volkswagen Connect library."""

BASE_API = "https://emea.bff.cariad.digital"
BRAND = "VW"
COUNTRY = "DE"

# Data used in communication
CLIENT_ID = "a24fba63-34b3-4d43-b181-942111e6bda8@apps_vw-dilab_com"  # EMEA
CLIENT_ID_US = "59992128-69a9-42c3-8621-7942041ba824_MYVW_ANDROID"  # North America (confirmed from APK 2026)
CLIENT_SCOPE = "openid profile badge cars dealers vin offline_access"

# X-QMAuth HMAC-SHA256 shared secret (VW Group apps)
# Original obfuscated notation from decompiled app: [26, 256-74, 256-103, 37, ...]
XQMAUTH_SECRET = bytes(
    [
        26,
        182,
        153,
        37,
        172,
        23,
        154,
        170,
        78,
        131,
        171,
        230,
        113,
        169,
        71,
        109,
        23,
        100,
        24,
        184,
        91,
        215,
        6,
        241,
        67,
        108,
        161,
        91,
        230,
        71,
        152,
        156,
    ]
)
XQMAUTH_PREFIX = "v1:01da27b0:"
CLIENT_TOKEN_TYPES = "code"

APP_VERSION = "2025.12.10-8414"  # x-app-version header value (from APK manifest)
APP_VERSION_SHORT = "3.61.0"  # Short version for User-Agent and MBB registration
USER_AGENT = f"Volkswagen/{APP_VERSION_SHORT}-android/14"
APP_URI = "weconnect://authenticated"
ANDROID_PACKAGE_NAME = "com.volkswagen.weconnect"
MBB_BRAND_CONFIG = "myvw"  # VW brand identifier for Brand/MBB token exchanges
MAX_REDIRECT_DEPTH = 10  # Maximum redirects during OAuth login flow
COUNTRY_TO_LOCALE = {"US": "en-US", "CA": "en-CA", "GB": "en-GB"}

# Used when fetching data
HEADERS_SESSION = {
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Accept-charset": "UTF-8",
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
    "x-android-package-name": ANDROID_PACKAGE_NAME,
}

# Used for authentication
HEADERS_AUTH = {
    "Connection": "keep-alive",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": USER_AGENT,
    "x-android-package-name": ANDROID_PACKAGE_NAME,
}

# Region configuration
REGION_CONFIGS = {
    "EMEA": {
        "base_api": "https://emea.bff.cariad.digital",
        "homeregion": "https://msg.volkswagen.de",
        "client_id": CLIENT_ID,
        "response_type": "code id_token token",  # OIDC hybrid flow: code+id_token+access_token in callback
        "redirect_uri": APP_URI,
        "scope": CLIENT_SCOPE,
    },
    "NA": {  # North America
        "base_api": "https://b-h-s.spr.us00.p.con-veh.net",  # Confirmed working 2026
        "homeregion": None,  # Discovered during login
        "client_id": CLIENT_ID_US,
        # Auth/token flow (confirmed from APK decompilation + traffic analysis 2026):
        # - authorize URL is built from base_api (b-h-s...) in the app's WebView
        # - base_api/oidc/v1/authorize redirects → identity.na.vwgroup.io login form
        # - form POSTs must go to identity.na.vwgroup.io (relative form action paths live there)
        # - token exchange goes to base_api/oidc/v1/token as public PKCE client (no client_secret)
        "identity_endpoint": "https://identity.na.vwgroup.io",  # form POST base URL (relative paths)
        "auth_endpoint": "https://b-h-s.spr.us00.p.con-veh.net/oidc/v1/authorize",  # redirects to identity
        "token_endpoint": "https://b-h-s.spr.us00.p.con-veh.net/oidc/v1/token",  # public PKCE client
        "scope": "openid",  # Confirmed from LoginFragment.java: addQueryParameter("scope", "openid")
        "redirect_uri": "kombi:///login",  # Confirmed from LoginFragment.java (custom URI scheme)
        "response_type": "code",  # PKCE plain auth-code flow — NOT hybrid (no id_token in callback)
        "use_pkce": True,  # PKCE required: AzsChallenge holds code_verifier + code_challenge
        "mbb_oauth_base_url": "https://mbboauth-1d.prd.ece.vwg-connect.com/mbbcoauth",
        "brand_token_path": "/login/v1/volkswagen/token",
        # base_api_candidates left empty: b-h-s.spr.us00.p.con-veh.net does not respond to
        # /login/v1/idk/openid-configuration (the discovery endpoint), so discovery would
        # wrongly pick na.bff.cariad.digital which lacks the vehicle API. Hardcoded base_api
        # is the confirmed correct endpoint from APK decompilation 2026.
        "base_api_candidates": [],
        "homeregion_candidates": [
            "https://msg.vw.com",
            "https://msg.volkswagen.com",
            "https://msg.vw.us",
        ],
    },
}

# Country to region mapping
COUNTRY_TO_REGION = {
    # North America
    "US": "NA",
    "CA": "NA",
    # EMEA
    "DE": "EMEA",
    "FR": "EMEA",
    "UK": "EMEA",
    "GB": "EMEA",
    "IT": "EMEA",
    "ES": "EMEA",
    "NL": "EMEA",
    "BE": "EMEA",
    "AT": "EMEA",
    "CH": "EMEA",
    "SE": "EMEA",
    "NO": "EMEA",
    "DK": "EMEA",
    "FI": "EMEA",
    "PL": "EMEA",
    "CZ": "EMEA",
    "PT": "EMEA",
    "IE": "EMEA",
    "LU": "EMEA",
}

DEFAULT_REGION = "EMEA"


def get_region_from_country(country: str) -> str:
    """Get region identifier from country code.

    Args:
        country: Two-letter country code (e.g., 'US', 'DE')

    Returns:
        Region identifier ('EMEA', 'NA', etc.)
    """
    return COUNTRY_TO_REGION.get(country.upper(), DEFAULT_REGION)


def get_region_config(region: str) -> dict:
    """Get configuration for a specific region.

    Args:
        region: Region identifier ('EMEA', 'NA', etc.)

    Returns:
        Dictionary with region configuration
    """
    return REGION_CONFIGS.get(region, REGION_CONFIGS[DEFAULT_REGION])


TEMP_CELSIUS: str = "°C"


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
    SAFETY = "safety"
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
    REAR_RIGHT_DOOR_LOCK = "0x030104000A"

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
    READINESS = "readiness"
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
    ACCESS_OVERALL_STATUS = f"{Services.ACCESS}.accessStatus.value.overallStatus"

    # Lights
    LIGHTS = f"{Services.VEHICLE_LIGHTS}.lightsStatus.value.lights"
    LIGHTS_TS = f"{Services.VEHICLE_LIGHTS}.lightsStatus.value.carCapturedTimestamp"

    # Charging status
    CHARGING_STATE = f"{Services.CHARGING}.chargingStatus.value.chargingState"
    CHARGING_TS = f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp"
    CHARGING_POWER = f"{Services.CHARGING}.chargingStatus.value.chargePower_kW"
    CHARGING_RATE = f"{Services.CHARGING}.chargingStatus.value.chargeRate_kmph"
    CHARGING_TYPE = f"{Services.CHARGING}.chargingStatus.value.chargeType"
    CHARGING_STATUS_CHARGE_MODE = f"{Services.CHARGING}.chargingStatus.value.chargeMode"
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

    CHARGE_MODE = f"{Services.CHARGING}.chargeMode.value"
    CHARGE_MODE_PREFERRED = f"{Services.CHARGING}.chargeMode.value.preferredChargeMode"
    CHARGE_MODE_AVAILABLE = f"{Services.CHARGING}.chargeMode.value.availableChargeModes"

    # Measurements - Odometer
    MEASUREMENTS_ODO = f"{Services.MEASUREMENTS}.odometerStatus.value.odometer"
    MEASUREMENTS_ODO_TS = (
        f"{Services.MEASUREMENTS}.odometerStatus.value.carCapturedTimestamp"
    )

    # Measurements - Ranges
    MEASUREMENTS_RNG_TS = (
        f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp"
    )
    MEASUREMENTS_RNG_ELECTRIC = (
        f"{Services.MEASUREMENTS}.rangeStatus.value.electricRange"
    )
    MEASUREMENTS_RNG_DIESEL = f"{Services.MEASUREMENTS}.rangeStatus.value.dieselRange"
    MEASUREMENTS_RNG_GASOLINE = (
        f"{Services.MEASUREMENTS}.rangeStatus.value.gasolineRange"
    )
    MEASUREMENTS_RNG_CNG = f"{Services.MEASUREMENTS}.rangeStatus.value.cngRange"
    MEASUREMENTS_RNG_TOTAL = f"{Services.MEASUREMENTS}.rangeStatus.value.totalRange_km"
    MEASUREMENTS_RNG_ADBLUE = f"{Services.MEASUREMENTS}.rangeStatus.value.adBlueRange"

    # Measurements - Fuel Level
    MEASUREMENTS_FUEL_TS = (
        f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carCapturedTimestamp"
    )
    MEASUREMENTS_FUEL_LVL = (
        f"{Services.MEASUREMENTS}.fuelLevelStatus.value.currentFuelLevel_pct"
    )
    MEASUREMENTS_FUEL_GAS_LVL = (
        f"{Services.MEASUREMENTS}.fuelLevelStatus.value.currentCngLevel_pct"
    )
    MEASUREMENTS_FUEL_CAR_TYPE = (
        f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carType"
    )
    MEASUREMENTS_FUEL_PRIMARY_ENGINE = (
        f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType"
    )
    MEASUREMENTS_FUEL_SECONDARY_ENGINE = (
        f"{Services.MEASUREMENTS}.fuelLevelStatus.value.secondaryEngineType"
    )

    # Measurements - Battery Temperature
    MEASUREMENTS_BAT_TEMP_TS = (
        f"{Services.MEASUREMENTS}.temperatureBatteryStatus.value.carCapturedTimestamp"
    )
    MEASUREMENTS_BAT_TEMP_MIN_K = f"{Services.MEASUREMENTS}.temperatureBatteryStatus.value.temperatureHvBatteryMin_K"
    MEASUREMENTS_BAT_TEMP_MAX_K = f"{Services.MEASUREMENTS}.temperatureBatteryStatus.value.temperatureHvBatteryMax_K"

    # Vehicle Health Inspection
    VEHICLE_HEALTH_TS = f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
    VEHICLE_HEALTH_INSPECTION_DAYS = f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_days"
    VEHICLE_HEALTH_INSPECTION_KM = (
        f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_km"
    )
    VEHICLE_HEALTH_OIL_DAYS = f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.oilServiceDue_days"
    VEHICLE_HEALTH_OIL_KM = (
        f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.oilServiceDue_km"
    )

    # Fuel Status
    FUEL_STATUS_TS = f"{Services.FUEL_STATUS}.rangeStatus.value.carCapturedTimestamp"
    FUEL_STATUS_CAR_TYPE = f"{Services.FUEL_STATUS}.rangeStatus.value.carType"
    FUEL_STATUS_PRIMARY_TYPE = (
        f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.type"
    )
    FUEL_STATUS_PRIMARY_LVL = (
        f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.currentFuelLevel_pct"
    )
    FUEL_STATUS_PRIMARY_RNG = (
        f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.remainingRange_km"
    )
    FUEL_STATUS_SECONDARY_TYPE = (
        f"{Services.FUEL_STATUS}.rangeStatus.value.secondaryEngine.type"
    )

    # Climatisation Status
    CLIMATISATION_STATE = (
        f"{Services.CLIMATISATION}.climatisationStatus.value.climatisationState"
    )
    CLIMATISATION_STATUS_TS = (
        f"{Services.CLIMATISATION}.climatisationStatus.value.carCapturedTimestamp"
    )
    CLIMATISATION_REM_TIME = f"{Services.CLIMATISATION}.climatisationStatus.value.remainingClimatisationTime_min"

    # Active ventilation (combustion cars without auxiliary heating)
    ACTIVE_VENTILATION_STATE = (
        f"{Services.CLIMATISATION}.activeVentilationStatus.value.climatisationState"
    )
    ACTIVE_VENTILATION_TS = (
        f"{Services.CLIMATISATION}.activeVentilationStatus.value.carCapturedTimestamp"
    )
    ACTIVE_VENTILATION_REM_TIME = f"{Services.CLIMATISATION}.activeVentilationStatus.value.remainingClimatisationTime_min"

    # Climatisation Settings
    CLIMATISATION_SETTINGS_TS = (
        f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp"
    )
    CLIMATISATION_TARGET_TEMP = (
        f"{Services.CLIMATISATION}.climatisationSettings.value.targetTemperature_C"
    )
    CLIMATISATION_WITHOUT_EXT_PWR = f"{Services.CLIMATISATION}.climatisationSettings.value.climatisationWithoutExternalPower"
    CLIMATISATION_AT_UNLOCK = (
        f"{Services.CLIMATISATION}.climatisationSettings.value.climatizationAtUnlock"
    )
    CLIMATISATION_WINDOW_HEATING = (
        f"{Services.CLIMATISATION}.climatisationSettings.value.windowHeatingEnabled"
    )
    CLIMATISATION_ZONE_FRONT_LEFT = (
        f"{Services.CLIMATISATION}.climatisationSettings.value.zoneFrontLeftEnabled"
    )
    CLIMATISATION_ZONE_FRONT_RIGHT = (
        f"{Services.CLIMATISATION}.climatisationSettings.value.zoneFrontRightEnabled"
    )
    CLIMATISATION_AUX_DURATION = f"{Services.CLIMATISATION}.climatisationSettings.value.auxiliaryHeatingSettings.duration_min"

    # Window Heating Status
    CLIMATISATION_WINDOW_HEATING_STATUS = (
        f"{Services.CLIMATISATION}.windowHeatingStatus.value.windowHeatingStatus"
    )
    CLIMATISATION_WINDOW_HEATING_TS = (
        f"{Services.CLIMATISATION}.windowHeatingStatus.value.carCapturedTimestamp"
    )

    # Auxiliary Heating Status
    CLIMATISATION_AUX_STATE = (
        f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.climatisationState"
    )
    CLIMATISATION_AUX_TS = (
        f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.carCapturedTimestamp"
    )
    CLIMATISATION_AUX_REM_TIME = f"{Services.CLIMATISATION}.auxiliaryHeatingStatus.value.remainingClimatisationTime_min"

    # Departure Timers
    DEPARTURE_TIMERS = f"{Services.DEPARTURE_TIMERS}.departureTimersStatus.value.timers"
    DEPARTURE_TIMERS_TS = (
        f"{Services.DEPARTURE_TIMERS}.departureTimersStatus.value.carCapturedTimestamp"
    )

    # Departure Profiles
    DEPARTURE_PROFILES_TIMERS = (
        f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.timers"
    )
    DEPARTURE_PROFILES_PROFILES = (
        f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.profiles"
    )
    DEPARTURE_PROFILES_TS = f"{Services.DEPARTURE_PROFILES}.departureProfilesStatus.value.carCapturedTimestamp"

    # Climatisation Timers
    CLIMATISATION_TIMERS = (
        f"{Services.CLIMATISATION_TIMERS}.climatisationTimersStatus.value.timers"
    )
    CLIMATISATION_TIMERS_TS = f"{Services.CLIMATISATION_TIMERS}.climatisationTimersStatus.value.carCapturedTimestamp"

    # Auxiliary Heating Timers
    AUXILIARY_HEATING_TIMERS = (
        f"{Services.CLIMATISATION_TIMERS}.auxiliaryHeatingTimersStatus.value.timers"
    )
    AUXILIARY_HEATING_TIMERS_TS = f"{Services.CLIMATISATION_TIMERS}.auxiliaryHeatingTimersStatus.value.carCapturedTimestamp"

    # Battery Charging Care (legacy)
    BATTERY_CARE_MODE = (
        f"{Services.BATTERY_CHARGING_CARE}.chargingCareSettings.value.batteryCareMode"
    )

    # Battery Support (legacy)
    BATTERY_SUPPORT = (
        f"{Services.BATTERY_SUPPORT}.batterySupportStatus.value.batterySupport"
    )

    # User Capabilities (for auxiliary climatisation detection)
    USER_CAPABILITIES = f"{Services.USER_CAPABILITIES}.capabilitiesStatus.value"

    # Battery Charging Care Settings (add after BATTERY_CARE_MODE)
    BATTERY_CARE_MODE_TS = f"{Services.BATTERY_CHARGING_CARE}.chargingCareSettings.value.carCapturedTimestamp"

    # Battery Support Settings (add after BATTERY_SUPPORT)
    BATTERY_SUPPORT_TS = (
        f"{Services.BATTERY_SUPPORT}.batterySupportStatus.value.carCapturedTimestamp"
    )

    # Parking Position
    PARKING_LAT = "parkingposition.lat"
    PARKING_LON = "parkingposition.lon"
    PARKING_TS = "parkingposition.carCapturedTimestamp"

    # Readiness
    READINESS_IS_ONLINE = (
        f"{Services.READINESS}.readinessStatus.value.connectionState.isOnline"
    )
    READINESS_IS_ACTIVE = (
        f"{Services.READINESS}.readinessStatus.value.connectionState.isActive"
    )
    READINESS_BATTERY_POWER_LEVEL = (
        f"{Services.READINESS}.readinessStatus.value.connectionState.batteryPowerLevel"
    )
    READINESS_DAILY_POWER_BUDGET_AVAILABLE = f"{Services.READINESS}.readinessStatus.value.connectionState.dailyPowerBudgetAvailable"
    READINESS_INSUFFICIENT_BATTERY_LEVEL_WARNING = f"{Services.READINESS}.readinessStatus.value.connectionWarning.insufficientBatteryLevelWarning"
    READINESS_DAILY_POWER_BUDGET_WARNING = f"{Services.READINESS}.readinessStatus.value.connectionWarning.dailyPowerBudgetWarning"
