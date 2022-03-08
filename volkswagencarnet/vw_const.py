"""Constants for We Connect library."""

BASE_SESSION = "https://msg.volkswagen.de"
BASE_AUTH = "https://identity.vwgroup.io"
BRAND = "VW"
COUNTRY = "DE"

# Data used in communication
CLIENT = {
    "Legacy": {
        "CLIENT_ID": "9496332b-ea03-4091-a224-8c746b885068@apps_vw-dilab_com",
        # client id for VWG API, legacy Skoda Connect/MySkoda
        "SCOPE": "openid mbb profile cars address email birthdate nickname phone",
        # 'SCOPE': 'openid mbb profile cars address email birthdate badge phone driversLicense dealers profession vin',
        "TOKEN_TYPES": "code id_token token",
    },
    "New": {
        "CLIENT_ID": "f9a2359a-b776-46d9-bd0c-db1904343117@apps_vw-dilab_com",
        # Provides access to new API? tokentype=IDK_TECHNICAL..
        "SCOPE": "openid mbb profile",
        "TOKEN_TYPES": "code id_token",
    },
    "Unknown": {
        "CLIENT_ID": "72f9d29d-aa2b-40c1-bebe-4c7683681d4c@apps_vw-dilab_com",  # gives tokentype=IDK_SMARTLINK ?
        "SCOPE": "openid dealers profile email cars address",
        "TOKEN_TYPES": "code id_token",
    },
}


XCLIENT_ID = "85fa2187-5b5c-4c35-adba-1471d0c4ea60"
XAPPVERSION = "5.3.2"
XAPPNAME = "We Connect"
USER_AGENT = "okhttp/3.14.7"
APP_URI = "carnet://identity-kit/login"

# Used when fetching data
HEADERS_SESSION = {
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Accept-charset": "UTF-8",
    "Accept": "application/json",
    "X-Client-Id": XCLIENT_ID,
    "X-App-Version": XAPPVERSION,
    "X-App-Name": XAPPNAME,
    "User-Agent": USER_AGENT,
    "tokentype": "IDK_TECHNICAL",
}

# Used for authentication
HEADERS_AUTH = {
    "Connection": "keep-alive",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Content-Type": "application/x-www-form-urlencoded",
    "x-requested-with": XAPPNAME,
    "User-Agent": USER_AGENT,
    "X-App-Name": XAPPNAME,
}

TEMP_CELSIUS: str = "°C"
TEMP_FAHRENHEIT: str = "°F"


class VWStateClass:
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class VWDeviceClass:
    BATTERY = "battery"
    CONNECTIVITY = "connectivity"
    DOOR = "door"
    LIGHT = "light"
    LOCK = "lock"
    MOVING = "moving"
    PLUG = "plug"
    POWER = "power"
    TEMPERATURE = "temperature"
    WINDOW = "window"
