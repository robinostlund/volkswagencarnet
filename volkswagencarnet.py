#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Communicate with Volkswagen Carnet."""
import re
import time
import logging
import asyncio

from sys import version_info, argv
from datetime import timedelta, datetime
from urllib.parse import urlsplit, urljoin, parse_qs, urlparse
from json import dumps as to_json
from collections import OrderedDict
from bs4 import BeautifulSoup
from vw_utilities import find_path, is_valid_path, read_config, json_loads

from aiohttp import ClientSession, ClientTimeout
from aiohttp.hdrs import METH_GET, METH_POST

version_info >= (3, 0) or exit('Python 3 required')

_LOGGER = logging.getLogger(__name__)


TIMEOUT = timedelta(seconds=30)

HEADERS_SESSION = {
    'Connection': 'keep-alive',
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/json;charset=UTF-8',
    'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) \
        AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'
}

HEADERS_AUTH = {
    'Connection': 'keep-alive',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,\
        image/apng,*/*;q=0.8,application/signed-exchange;v=b3',
    'Content-Type': 'application/x-www-form-urlencoded',
    'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) \
        AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'
}

BASE_SESSION = 'https://www.portal.volkswagen-we.com/'
BASE_AUTH = 'https://identity.vwgroup.io'


class Connection:
    """ Connection to Volkswagen Carnet """

    def __init__(self, session, username, password, guest_lang='en'):
        """ Initialize """
        self._session = session
        self._session_headers = HEADERS_SESSION.copy()
        self._session_base = BASE_SESSION
        self._session_auth_headers = HEADERS_AUTH.copy()
        self._session_auth_base = BASE_AUTH
        self._session_guest_language_id = guest_lang

        self._session_auth_ref_url = False
        self._session_logged_in = False
        self._session_first_update = False
        self._session_auth_username = username
        self._session_auth_password = password

        _LOGGER.debug('Using service <%s>', self._session_base)

        self._state = {}

    def _clear_cookies(self):
        self._session._cookie_jar._cookies.clear()

    async def _login(self):
        """ Reset session in case we would like to login again """
        self._session_headers = HEADERS_SESSION.copy()
        self._session_auth_headers = HEADERS_AUTH.copy()

        def extract_csrf(req):
            return re.compile('<meta name="_csrf" content="([^"]*)"/>').search(req).group(1)

        def extract_guest_language_id(req):
            return req.split('_')[1].lower()

        try:
            # remove cookies from session as we are doing a new login
            self._clear_cookies()

            # Request landing page and get CSFR:
            req = await self._session.get(
                url=self._session_base + '/portal/en_GB/web/guest/home',
                headers={'Connection': 'keep-alive'}
            )
            if req.status != 200:
                return ""
            csrf = extract_csrf(await req.text())

            # Request login page and get CSRF
            self._session_auth_headers['Referer'] = self._session_base + 'portal'
            req = await self._session.post(
                url=f'{self._session_base}portal/web/guest/home/-/csrftokenhandling/get-login-url',
                headers=self._session_auth_headers
            )
            if req.status != 200:
                return ""
            response_data = await req.json()
            lg_url = response_data.get("loginURL").get("path")

            # no redirect so we can get values we look for
            req = await self._session.get(lg_url, allow_redirects=False, headers=self._session_auth_headers)
            if req.status != 302:
                return ""
            ref_url_1 = req.headers.get("location")

            # now get actual login page and get session id and ViewState
            req = await self._session.get(ref_url_1, headers=self._session_auth_headers)
            if req.status != 200:
                return ""

            # get login variables
            bs = BeautifulSoup(await req.text(), 'html.parser')
            login_csrf = bs.select_one('input[name=_csrf]')['value']
            login_token = bs.select_one('input[name=relayState]')['value']
            login_hmac = bs.select_one('input[name=hmac]')['value']
            login_form_action = bs.find('form', id='emailPasswordForm').get('action')
            login_url = self._session_auth_base + login_form_action

            # post login
            self._session_auth_headers["Referer"] = ref_url_1

            post_data = {
                'email': self._session_auth_username,
                'password': self._session_auth_password,
                'relayState': login_token,
                'hmac': login_hmac,
                '_csrf': login_csrf,
            }
            # post: https://identity.vwgroup.io/signin-service/v1/xxx@apps_vw-dilab_com/login/identifier
            req = await self._session.post(
                url=login_url,
                data=post_data,
                allow_redirects=False,
                headers=self._session_auth_headers)

            if req.status != 303:
                return ""

            ref_url_2 = req.headers.get("location")
            auth_relay_url = self._session_auth_base + ref_url_2

            # get: https://identity.vwgroup.io/signin-service/v1/xxx@apps_vw-dilab_com/login/authenticate?relayState=xxx&email=xxx
            req = await self._session.get(
                url=auth_relay_url,
                allow_redirects=False,
                headers=self._session_auth_headers
            )
            if req.status != 200:
                return ""

            # post: https://identity.vwgroup.io/signin-service/v1/xxx@apps_vw-dilab_com/login/authenticate
            bs = BeautifulSoup(await req.text(), 'html.parser')
            auth_csrf = bs.select_one('input[name=_csrf]')['value']
            auth_token = bs.select_one('input[name=relayState]')['value']
            auth_hmac = bs.select_one('input[name=hmac]')['value']
            auth_form_action = bs.find('form', id='credentialsForm').get('action')
            auth_url = f'{self._session_auth_base}{auth_form_action}'

            # post login
            self._session_auth_headers['Referer'] = auth_relay_url

            post_data = {
                'email': self._session_auth_username,
                'password': self._session_auth_password,
                'relayState': auth_token,
                'hmac': auth_hmac,
                '_csrf': auth_csrf,
            }
            # post: https://identity.vwgroup.io/signin-service/v1/xxx@apps_vw-dilab_com/login/authenticate
            req = await self._session.post(
                url=auth_url,
                data=post_data,
                allow_redirects=False,
                headers=self._session_auth_headers
            )
            if req.status != 302:
                return ""

            # get: https://identity.vwgroup.io/oidc/v1/oauth/sso?clientId=xxx@apps_vw-dilab_com&relayState=xxx&userId=xxx&HMAC=xxx
            ref_url_3 = req.headers.get('location')
            req = await self._session.get(
                url=ref_url_3,
                allow_redirects=False,
                headers=self._session_auth_headers
            )
            if req.status != 302:
                return ""

            # get:
            # https://identity.vwgroup.io/consent/v1/users/xxx/xxx@apps_vw-dilab_com?scopes=openid%20profile%20birthdate%20nickname%20address%20email%20phone%20cars%20dealers%20mbb&relay_state=xxx&callback=https://identity.vwgroup.io/oidc/v1/oauth/client/callback&hmac=xxx
            ref_url_4 = req.headers.get('location')
            req = await self._session.get(
                url=ref_url_4,
                allow_redirects=False,
                headers=self._session_auth_headers
            )
            if req.status != 302:
                return ""

            # get:
            # https://identity.vwgroup.io/oidc/v1/oauth/client/callback/success?user_id=xxx&client_id=xxx@apps_vw-dilab_com&scopes=openid%20profile%20birthdate%20nickname%20address%20email%20phone%20cars%20dealers%20mbb&consentedScopes=openid%20profile%20birthdate%20nickname%20address%20email%20phone%20cars%20dealers%20mbb&relay_state=xxx&hmac=xxx
            ref_url_5 = req.headers.get('location')
            # user_id = parse_qs(urlparse(ref_url_5).query).get('user_id')[0]
            req = await self._session.get(
                url=ref_url_5,
                allow_redirects=False,
                headers=self._session_auth_headers
            )
            if req.status != 302:
                return ""

            # get: https://www.portal.volkswagen-we.com/portal/web/guest/complete-login?state=xxx&code=xxx
            ref_url_6 = req.headers.get('location')
            state = parse_qs(urlparse(ref_url_6).query).get('state')[0]
            code = parse_qs(urlparse(ref_url_6).query).get('code')[0]

            # post:
            # https://www.portal.volkswagen-we.com/portal/web/guest/complete-login?p_auth=xxx&p_p_id=33_WAR_cored5portlet&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view&p_p_col_id=column-1&p_p_col_count=1&_33_WAR_cored5portlet_javax.portlet.action=getLoginStatus
            self._session_auth_headers['Referer'] = ref_url_6
            post_data = {
                '_33_WAR_cored5portlet_code': code,
                '_33_WAR_cored5portlet_landingPageUrl': ''
            }
            url = self._session_base + urlsplit(ref_url_6).path
            req = await self._session.post(
                url=f'{url}?p_auth={state}&p_p_id=33_WAR_cored5portlet&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view&p_p_col_id=column-1&p_p_col_count=1&_33_WAR_cored5portlet_javax.portlet.action=getLoginStatus',
                data=post_data,
                allow_redirects=False,
                headers=self._session_auth_headers
            )
            if req.status != 302:
                return ""

            # get: https://www.portal.volkswagen-we.com/portal/user/xxx/v_8xxx
            ref_url_7 = req.headers.get('location')

            req = await self._session.get(
                url=ref_url_7,
                allow_redirects=False,
                headers=self._session_auth_headers
            )
            if req.status != 200:
                return ""

            # We have a new CSRF
            csrf = extract_csrf(await req.text())

            self._session_guest_language_id = extract_guest_language_id(req.cookies.get('GUEST_LANGUAGE_ID').value)

            # Update headers for requests
            self._session_headers['Referer'] = ref_url_7
            self._session_headers['X-CSRF-Token'] = csrf
            self._session_auth_ref_url = f'{ref_url_7}/'
            self._session_logged_in = True
            return True

        except Exception as error:
            _LOGGER.error('Failed to login to carnet, %s' % error)
            self._session_logged_in = False
            return False

    async def _request(self, method, url, **kwargs):
        """Perform a query to the vw carnet"""
        # try:
        _LOGGER.debug("Request for %s", url)

        async with self._session.request(
            method,
            url,
            headers=self._session_headers,
            timeout=ClientTimeout(total=TIMEOUT.seconds),
            **kwargs
        ) as response:
            response.raise_for_status()
            res = await response.json(loads=json_loads)
            _LOGGER.debug(f'Received [{response.status}] response: {res}')
            return res
        # except Exception as error:
        #     _LOGGER.warning(
        #         "Failure when communcating with the server: %s", error
        #     )
        #     raise

    async def _logout(self):
        await self.post('-/logout/revoke')
        # remove cookies from session as we have logged out
        self._clear_cookies()

    def _make_url(self, ref, rel=None):
        return urljoin(rel or self._session_auth_ref_url, ref)

    async def get(self, url, rel=None):
        """Perform a get query to the online service."""
        return await self._request(METH_GET, self._make_url(url, rel))

    async def post(self, url, rel=None, **data):
        """Perform a post query to the online service."""
        if data:
            return await self._request(METH_POST, self._make_url(url, rel), json=data)
        else:
            return await self._request(METH_POST, self._make_url(url, rel))

    async def update(self):
        """Update status."""
        try:
            if self._session_first_update:
                if not await self.validate_login:
                    _LOGGER.warning('Session expired, creating new login session to carnet.')
                    await self._login()
            else:
                self._session_first_update = True

            # fetch vehicles
            _LOGGER.debug('Fetching vehicles')

            # owners_verification = self.post(f'/portal/group/{self._session_guest_language_id}/edit-profile/-/profile/get-vehicles-owners-verification')
            # get vehicles
            loaded_vehicles = await self.post(
                url='-/mainnavigation/get-fully-loaded-cars'
            )

            # load all not loaded vehicles
            if loaded_vehicles.get('fullyLoadedVehiclesResponse', {}).get('vehiclesNotFullyLoaded', []):
                for vehicle in loaded_vehicles.get('fullyLoadedVehiclesResponse').get('vehiclesNotFullyLoaded'):
                    vehicle_vin = vehicle.get('vin')
                    await self.post(
                        url=f'-/mainnavigation/load-car-details/{vehicle_vin}'
                    )

                # update loaded cars
                loaded_vehicles = await self.post(
                    url='-/mainnavigation/get-fully-loaded-cars'
                )

            # update vehicles
            if loaded_vehicles.get('fullyLoadedVehiclesResponse', {}).get('completeVehicles', []):
                for vehicle in loaded_vehicles.get('fullyLoadedVehiclesResponse').get('completeVehicles'):
                    vehicle_url = self._session_base + vehicle.get('dashboardUrl') + '/'
                    if vehicle_url not in self._state:
                        self._state.update({vehicle_url: vehicle})
                    else:
                        for key, value in vehicle.items():
                            self._state[vehicle_url].update({key: value})

            # get vehicle data
            for vehicle in self.vehicles:
                # update data in all vehicles
                await vehicle.update()
            return True
        except (IOError, OSError, LookupError) as error:
            _LOGGER.warning(f'Could not update information from carnet: {error}')

    async def update_vehicle(self, vehicle):
        url = vehicle._url
        _LOGGER.debug(f'Updating vehicle status {vehicle.vin}')

        # get new messages, needs to be here fot be able to get latest vehicle status
        try:
            response = await self.post('-/msgc/get-new-messages', rel=url)
            # messageList
            if response.get('errorCode', {}) == '0':
                self._state[url].update(
                    {'vehicleMessagesNew': response.get('response', {})}
                )
            else:
                _LOGGER.debug(f'Could not fetch new messages: {response}')
        except Exception as err:
            _LOGGER.debug(f'Could not fetch new messages, error: {err}')

        # get latest messages
        try:
            response = await self.post('-/msgc/get-latest-messages', rel=url)
            # messageList
            if response.get('errorCode', {}) == '0':
                self._state[url].update(
                    {'vehicleMessagesLatest': response.get('messageList', {})}
                )
            else:
                _LOGGER.debug(f'Could not fetch latest messages: {response}')
        except Exception as err:
            _LOGGER.debug(f'Could not fetch latest messages, error: {err}')

        # fetch vehicle status data
        try:
            response = await self.post(url='-/vsr/get-vsr', rel=url)
            if response.get('errorCode', {}) == '0' and response.get('vehicleStatusData', {}):
                self._state[url].update(
                    {'vehicleStatus': response.get('vehicleStatusData', {})}
                )
            else:
                _LOGGER.debug(f'Could not fetch vsr data: {response}')
        except Exception as err:
            _LOGGER.debug(f'Could not fetch vsr data, error: {err}')

        # fetch vehicle emanage data
        try:
            if not vehicle.attrs.get('engineTypeCombustian'):
                response = await self.post('-/emanager/get-emanager', url)
                if response.get('errorCode', {}) == '0' and response.get('EManager', {}):
                    self._state[url].update(
                        {'vehicleEmanager': response.get('EManager', {})}
                    )
                else:
                    _LOGGER.debug(f'Could not fetch emanager data: {response}')
        except Exception as err:
            _LOGGER.debug(f'Could not fetch emanager data, error: {err}')

        # fetch vehicle location data
        try:
            response = await self.post('-/cf/get-location', url)
            if response.get('errorCode', {}) == '0' and response.get('position', {}):
                self._state[url].update(
                    {'vehiclePosition': response.get('position', {})}
                )
            else:
                _LOGGER.debug(f'Could not fetch location data: {response}')
        except Exception as err:
            _LOGGER.debug(f'Could not fetch location data, error: {err}')

        # fetch vehicle details data
        try:
            response = await self.post('-/vehicle-info/get-vehicle-details', url)
            if response.get('errorCode', {}) == '0' and response.get('vehicleDetails', {}):
                self._state[url].update(
                    {'vehicleDetails': response.get('vehicleDetails', {})}
                )
            else:
                _LOGGER.debug(f'Could not fetch details data: {response}')
        except Exception as err:
            _LOGGER.debug(f'Could not fetch details data, error: {err}')

        # fetch combustion engine remote auxiliary heating status data
        if vehicle.attrs.get('engineTypeCombustian', False):
            try:
                response = await self.post('-/rah/get-status', url)
                if response.get('errorCode', {}) == '0' and response.get('remoteAuxiliaryHeating', {}):
                    self._state[url].update(
                        {'vehicleRemoteAuxiliaryHeating': response.get('remoteAuxiliaryHeating', {})}
                    )
                else:
                    _LOGGER.debug(f'Could not fetch remote auxiliary heating data: {response}')
            except Exception as err:
                _LOGGER.debug(f'Could not fetch remote auxiliary heating data, error: {err}')

        # fetch latest trips
        try:
            response = await self.post('-/rts/get-latest-trip-statistics', url)
            if response.get('errorCode', {}) == '0' and response.get('rtsViewModel', {}):
                self._state[url].update(
                    {'vehicleLastTrips': response.get('rtsViewModel', {})}
                )
            else:
                _LOGGER.debug(f'Could not fetch last trips data: {response}')
        except Exception as err:
            _LOGGER.debug(f'Could not fetch last trips data, error: {err}')

        _LOGGER.debug(f'{vehicle.unique_id} data: {self._state[url]}')

    def vehicle(self, vin):
        """Return vehicle for given vin."""
        return next(
            (
                vehicle
                for vehicle in self.vehicles
                if vehicle.unique_id == vin.lower()
            ), None
        )

    @property
    def vehicles(self):
        """Return vehicle state."""
        return (Vehicle(self, url) for url in self._state)

    def vehicle_attrs(self, vehicle_url):
        return self._state.get(vehicle_url)

    @property
    async def validate_login(self):
        try:
            messages = await self.post('-/msgc/get-new-messages')
            if messages.get('errorCode', {}) == '0':
                return True
            else:
                return False
        except (IOError, OSError) as error:
            _LOGGER.warning('Could not validate login: %s', error)
            return False

    @property
    def logged_in(self):
        return self._session_logged_in


class Vehicle:
    def __init__(self, conn, url):
        self._connection = conn
        self._url = url

    async def update(self):
        # await self._connection.update(request_data=False)
        await self._connection.update_vehicle(self)

    async def get(self, query):
        """Perform a query to the online service."""
        req = await self._connection.get(query, self.url)
        return req

    async def post(self, query, **data):
        """Perform a query to the online service."""
        req = await self._connection.post(query, self._url, **data)
        return req

    async def call(self, method, **data):
        """Make remote method call."""
        try:
            if not await self._connection.validate_login:
                _LOGGER.warning('Session expired, reconnecting to carnet.')
                await self._connection._login()

            res = await self.post(method, **data)
            if res.get('errorCode') != '0':
                _LOGGER.warning(f'Failed to execute {method}')
                return
            else:
                _LOGGER.debug('Message delivered')
                return True

        except Exception as error:
            _LOGGER.warning(f'Failure to execute: {error}')

    @property
    def attrs(self):
        return self._connection.vehicle_attrs(self._url)

    def has_attr(self, attr):
        return is_valid_path(self.attrs, attr)

    def get_attr(self, attr):
        return find_path(self.attrs, attr)

    def dashboard(self, **config):
        from vw_dashboard import Dashboard
        return Dashboard(self, **config)

    @property
    def vin(self):
        return self.attrs.get('vin').lower()

    @property
    def unique_id(self):
        return self.vin

    @property
    def last_connected(self):
        """Return when vehicle was last connected to carnet"""
        last_connected = self.attrs.get('vehicleDetails').get('lastConnectionTimeStamp')
        if last_connected:
            last_connected = f'{last_connected[0]}  {last_connected[1]}'
            date_patterns = ["%d.%m.%Y %H:%M", "%d-%m-%Y %H:%M"]
            for date_pattern in date_patterns:
                try:
                    return datetime.strptime(last_connected, date_pattern).strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass
        return last_connected

    @property
    def is_last_connected_supported(self):
        """Return when vehicle was last connected to carnet"""
        if self.attrs.get('vehicleDetails', {}).get('lastConnectionTimeStamp', []):
            return True

    @property
    def climatisation_target_temperature(self):
        return self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('settings',{}).get('targetTemperature', 0)

    @property
    def is_climatisation_target_temperature_supported(self):
        return self.is_climatisation_supported

    @property
    def climatisation_without_external_power(self):
        return self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('settings', {}).get('climatisationWithoutHVPower', False)

    @property
    def is_climatisation_without_external_power_supported(self):
        return self.is_climatisation_supported

    @property
    def service_inspection(self):
        """Return time left for service inspection"""
        return self.attrs.get('vehicleDetails', {}).get('serviceInspectionData', '')

    @property
    def is_service_inspection_supported(self):
        if self.attrs.get('vehicleDetails', {}).get('serviceInspectionData', False):
            return True

    @property
    def oil_inspection(self):
        """Return time left for service inspection"""
        return self.attrs.get('vehicleDetails', {}).get('oilInspectionData', '')

    @property
    def is_oil_inspection_supported(self):
        if self.attrs.get('vehicleDetails', {}).get('oilInspectionData', False):
            return True

    @property
    def adblue_level(self):
        return self.attrs.get('vehicleStatus', {}).get('adBlueLevel', 0)

    @property
    def is_adblue_level_supported(self):
        if self.attrs.get('vehicleStatus', {}).get('adBlueEnabled', False):
            return True

    @property
    def battery_level(self):
        return self.attrs.get('vehicleStatus', {}).get('batteryLevel', 0)

    @property
    def is_battery_level_supported(self):
        if type(self.attrs.get('vehicleStatus', {}).get('batteryLevel', False)) in (float, int):
            return True

    @property
    def charge_max_ampere(self):
        """Return charge max ampere"""
        return self.attrs.get('vehicleEmanager').get('rbc').get('settings').get('chargerMaxCurrent')

    @property
    def is_charge_max_ampere_supported(self):
        if type(self.attrs.get('vehicleEmanager', {}).get('rbc', {}).get('settings', {}).get('chargerMaxCurrent', False)) in (float, int):
            return True

    @property
    def parking_light(self):
        """Return true if parking light is on"""
        response = self.attrs.get('vehicleStatus').get('carRenderData').get('parkingLights')
        if response != 2:
            return True
        else:
            return False

    @property
    def is_parking_light_supported(self):
        """Return true if parking light is supported"""
        if type(self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('parkingLights', False)) in (float, int):
            return True

    @property
    def distance(self):
        value = self.attrs.get('vehicleDetails').get('distanceCovered').replace('.', '').replace(',', '').replace('--', '')
        if value:
            return int(value)

    @property
    def is_distance_supported(self):
        """Return true if distance is supported"""
        if self.attrs.get('vehicleDetails', {}).get('distanceCovered', False):
            return True

    @property
    def position(self):
        """Return  position."""
        return self.attrs.get('vehiclePosition')

    @property
    def is_position_supported(self):
        """Return true if vehichle has position."""
        if self.attrs.get('vehiclePosition', {}).get('lng', False):
            return True

    @property
    def model(self):
        """Return model"""
        return self.attrs.get('model')

    @property
    def is_model_supported(self):
        if self.attrs.get('model', False):
            return True

    @property
    def model_year(self):
        """Return model year"""
        return self.attrs.get('modelYear')

    @property
    def is_model_year_supported(self):
        if self.attrs.get('modelYear', False):
            return True

    @property
    def model_image(self):
        """Return model image"""
        return self.attrs.get('imageUrl')

    @property
    def is_model_image_supported(self):
        if self.attrs.get('imageUrl', False):
            return True

    @property
    def charging(self):
        """Return status of charging."""
        response = self.attrs.get('vehicleEmanager', {}).get('rbc', {}).get('status', {}).get('chargingState', {})
        if response == 'CHARGING':
            return True
        else:
            return False

    @property
    def is_charging_supported(self):
        """Return true if vehichle has heater."""
        if type(self.attrs.get('vehicleEmanager', {}).get('rbc', {}).get('status', {}).get('batteryPercentage', False)) in (float, int):
            return True

    @property
    def electric_range(self):
        return self.attrs.get('vehicleStatus', {}).get('batteryRange', 0)

    @property
    def is_electric_range_supported(self):
        if type(self.attrs.get('vehicleStatus', {}).get('batteryRange', False)) in (float, int):
            return True

    @property
    def combustion_range(self):
        return self.attrs.get('vehicleStatus').get('fuelRange', 0)

    @property
    def is_combustion_range_supported(self):
        if type(self.attrs.get('vehicleStatus', {}).get('fuelRange', False)) in (float, int):
            return True

    @property
    def combined_range(self):
        return self.attrs.get('vehicleStatus', {}).get('totalRange', 0)

    @property
    def is_combined_range_supported(self):
        if type(self.attrs.get('vehicleStatus', {}).get('totalRange', False)) in (float, int):
            return True

    @property
    def fuel_level(self):
        return self.attrs.get('vehicleStatus', {}).get('fuelLevel', {})

    @property
    def is_fuel_level_supported(self):
        if type(self.attrs.get('vehicleStatus', {}).get('fuelLevel', {})) in (float, int):
            return True

    @property
    def external_power(self):
        """Return true if external power is connected."""
        check = self.attrs.get('vehicleEmanager', {}).get('rbc', {}).get('status', {}).get('pluginState', {})
        if check == 'CONNECTED':
            return True
        else:
            return False

    @property
    def is_external_power_supported(self):
        """External power supported."""
        if self.attrs.get('vehicleEmanager', {}).get('rbc', {}).get('status', {}).get('pluginState', False):
            return True

    @property
    def electric_climatisation(self):
        """Return status of climatisation."""
        climatisation_type = self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('settings', {}).get('electric', False)
        status = self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('status', {}).get('climatisationState', '')
        if status in ['HEATING', 'COOLING'] and climatisation_type is True:
            return True
        else:
            return False

    @property
    def is_climatisation_supported(self):
        """Return true if vehichle has heater."""
        response = self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('climaterActionState', '')
        if response == 'AVAILABLE' or response == 'NO_PLUGIN':
            return True

    @property
    def is_electric_climatisation_supported(self):
        """Return true if vehichle has heater."""
        return self.is_climatisation_supported

    @property
    def combustion_climatisation(self):
        """Return status of combustion climatisation."""
        climatisation_type = self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('settings', {}).get('electric', False)
        status = self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('status', {}).get('climatisationState', '')
        if status and status in ['HEATING', 'COOLING'] and climatisation_type is False:
            return True
        else:
            return False

    @property
    def is_combustion_climatisation_supported(self):
        """Return true if vehichle has combustion climatisation."""
        if self.is_climatisation_supported and self.attrs.get('vehicleEmanager', {}).get('rdt', {}).get('auxHeatingAllowed', False):
            return True

    @property
    def window_heater(self):
        """Return status of window heater."""
        ret = False
        status_front = self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('status', {}).get('windowHeatingStateFront', '')
        if status_front == 'ON':
            ret = True

        status_rear = self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('status', {}).get('windowHeatingStateRear', '')
        if status_rear == 'ON':
            ret = True
        return ret

    @property
    def is_window_heater_supported(self):
        """Return true if vehichle has heater."""
        if self.is_electric_climatisation_supported:
            if self.attrs.get('vehicleEmanager', {}).get('rpc', {}).get('status', {}).get('windowHeatingAvailable', False):
                return True

    @property
    def combustion_engine_heating(self):
        """Return status of combustion engine heating."""
        return self.attrs.get('vehicleRemoteAuxiliaryHeating', {}).get('status', {}).get('active', False)

    @property
    def is_combustion_engine_heating_supported(self):
        """Return true if vehichle has combustion engine heating."""
        if self.attrs.get('vehicleRemoteAuxiliaryHeating', False):
            return True

    @property
    def windows_closed(self):
        windows = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('windows', {})
        windows_closed = True
        for window in windows:
            if windows[window] != 3:
                windows_closed = False
        return windows_closed

    @property
    def is_windows_closed_supported(self):
        """Return true if window state is supported"""
        if self.attrs.get('vehicleStatus', {}).get('windowStatusSupported', False):
            return True

    @property
    def window_closed_left_front(self):
        windows = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('windows', {})
        if windows.get('left_front', 0) == 3:
            return True
        return False

    @property
    def is_window_closed_left_front_supported(self):
        """Return true if window state is supported"""
        return self.is_windows_closed_supported

    @property
    def window_closed_right_front(self):
        windows = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('windows', {})
        if windows.get('right_front', 0) == 3:
            return True
        return False

    @property
    def is_window_closed_right_front_supported(self):
        """Return true if window state is supported"""
        return self.is_windows_closed_supported

    @property
    def window_closed_left_back(self):
        windows = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('windows', {})
        if windows.get('left_back', 0) == 3:
            return True
        return False

    @property
    def is_window_closed_left_back_supported(self):
        """Return true if window state is supported"""
        return self.is_windows_closed_supported

    @property
    def window_closed_right_back(self):
        windows = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('windows', {})
        if windows.get('right_back', 0) == 3:
            return True
        return False

    @property
    def is_window_closed_right_back_supported(self):
        """Return true if window state is supported"""
        return self.is_windows_closed_supported

    @property
    def sunroof_closed(self):
        state_sunroof = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('sunroof', 0)
        sunroof_closed = state_sunroof == 3
        return sunroof_closed

    @property
    def is_sunroof_closed_supported(self):
        """Return true if sunroof state is supported"""
        if self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('sunroof', 0):
            return True

    @property
    def charging_time_left(self):
        if self.external_power:
            hours = self.attrs.get('vehicleEmanager', {}).get('rbc', {}).get('status', {}).get('chargingRemaningHour', 0)
            minutes = self.attrs.get('vehicleEmanager', {}).get('rbc', {}).get('status', {}).get('chargingRemaningMinute', 0)
            if hours and minutes:
                # return 0 if we are not able to convert the values we get from carnet.
                try:
                    return (int(hours) * 60) + int(minutes)
                except Exception:
                    pass
        return 0

    @property
    def is_charging_time_left_supported(self):
        return self.is_charging_supported

    @property
    def door_locked(self):
        lock_data = self.attrs.get('vehicleStatus', {}).get('lockData', [])
        vehicle_locked = True
        for lock in lock_data:
            if lock == 'trunk':
                continue
            if lock_data[lock] != 2:
                vehicle_locked = False
        return vehicle_locked

    @property
    def is_door_locked_supported(self):
        response = self.attrs.get('vehicleStatus', {}).get('lockData', [])
        if len(response) > 0:
            return True

    @property
    def door_closed_left_front(self):
        doors = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('doors', {})
        if doors.get('left_front', 0) == 3:
            return True
        return False

    @property
    def is_door_closed_left_front_supported(self):
        """Return true if window state is supported"""
        return self.is_windows_closed_supported

    @property
    def door_closed_right_front(self):
        doors = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('doors', {})
        if doors.get('right_front', 0) == 3:
            return True
        return False

    @property
    def is_door_closed_right_front_supported(self):
        """Return true if window state is supported"""
        return self.is_windows_closed_supported

    @property
    def door_closed_left_back(self):
        doors = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('doors', {})
        if doors.get('left_back', 0) == 3:
            return True
        return False

    @property
    def is_door_closed_left_back_supported(self):
        """Return true if window state is supported"""
        return self.is_windows_closed_supported

    @property
    def door_closed_right_back(self):
        doors = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('doors', {})
        if doors.get('right_back', 0) == 3:
            return True
        return False

    @property
    def is_door_closed_right_back_supported(self):
        """Return true if window state is supported"""
        return self.is_windows_closed_supported

    @property
    def trunk_closed(self):
        state = self.attrs.get('vehicleStatus', {}).get('carRenderData', {}).get('doors', {}).get('trunk', '')
        state_value = state == 3
        return state_value

    @property
    def is_trunk_closed_supported(self):
        """Return true if window state is supported"""
        return self.is_windows_closed_supported

    @property
    def trunk_locked(self):
        trunk_lock_data = self.attrs.get('vehicleStatus', {}).get('lockData', {}).get('trunk')
        if trunk_lock_data != 2:
            return False
        else:
            return True

    @property
    def is_trunk_locked_supported(self):
        if self.attrs.get('vehicleStatus', {}).get('lockData', {}).get('trunk', False):
            return True

    @property
    def request_in_progress(self):
        check = self.attrs.get('vehicleStatus', {}).get('requestStatus', {})
        if check == 'REQUEST_IN_PROGRESS':
            return True
        else:
            return False

    @property
    def is_request_in_progress_supported(self):
        response = self.attrs.get('vehicleStatus', {}).get('requestStatus', {})
        if response or response is None:
            return True

    # trips
    @property
    def trip_last_entry(self):
        last_trip = {}
        for trip in self.attrs.get('vehicleLastTrips', {}).get('tripStatistics', []):
            if isinstance(trip, dict) and 'tripStatistics' in trip:
                for trip_entry in trip.get('tripStatistics'):
                    last_trip = trip_entry
        return last_trip

    @property
    def trip_last_average_speed(self):
        return self.trip_last_entry.get('averageSpeed')

    @property
    def is_trip_last_average_speed_supported(self):
        # if self.attrs.get('vehicleLastTrips', {}).get('serviceConfiguration', {}).get('triptype_cyclic', False):
        #     return True
        response = self.trip_last_entry
        if response and type(response.get('averageSpeed')) in (float, int):
            return True

    @property
    def trip_last_average_electric_consumption(self):
        return self.trip_last_entry.get('averageElectricConsumption')

    @property
    def is_trip_last_average_electric_consumption_supported(self):
        response = self.trip_last_entry
        if response and type(response.get('averageElectricConsumption')) in (float, int):
            return True

    @property
    def trip_last_average_fuel_consumption(self):
        return self.trip_last_entry.get('averageFuelConsumption')

    @property
    def is_trip_last_average_fuel_consumption_supported(self):
        response = self.trip_last_entry
        if response and type(response.get('averageFuelConsumption')) in (float, int):
            return True

    @property
    def trip_last_average_auxillary_consumption(self):
        return self.trip_last_entry.get('averageAuxiliaryConsumption')

    @property
    def is_trip_last_average_auxillary_consumption_supported(self):
        response = self.trip_last_entry
        if response and type(response.get('averageAuxiliaryConsumption')) in (float, int):
            return True

    @property
    def trip_last_duration(self):
        return self.trip_last_entry.get('tripDuration')

    @property
    def is_trip_last_duration_supported(self):
        response = self.trip_last_entry
        if response and type(response.get('tripDuration')) in (float, int):
            return True

    @property
    def trip_last_length(self):
        return self.trip_last_entry.get('tripLength')

    @property
    def is_trip_last_length_supported(self):
        response = self.trip_last_entry
        if response and type(response.get('tripLength')) in (float, int):
            return True

    @property
    def trip_last_recuperation(self):
        return self.trip_last_entry.get('recuperation')

    @property
    def is_trip_last_recuperation_supported(self):
        response = self.trip_last_entry
        if response and type(response.get('recuperation')) in (float, int):
            return True

    @property
    def trip_last_total_electric_consumption(self):
        return self.trip_last_entry.get('totalElectricConsumption')

    @property
    def is_trip_last_total_electric_consumption_supported(self):
        response = self.trip_last_entry
        if response and type(response.get('totalElectricConsumption')) in (float, int):
            return True

    # actions
    async def trigger_request_update(self):
        if self.is_request_in_progress_supported:
            if not self.request_in_progress:
                resp = await self.call('-/vsr/request-vsr', dummy='data')
                if not resp or (isinstance(resp, dict) and resp.get('errorCode') != '0'):
                    _LOGGER.error('Failed to request vehicle update')
                else:
                    await self.update()
                    return resp
            else:
                _LOGGER.warning('Request update is already in progress')
        else:
            _LOGGER.error('No request update support.')

    async def lock_car(self, spin):
        if spin:
            resp = await self.call('-/vsr/remote-lock', spin=spin)
            if not resp:
                _LOGGER.warning('Failed to lock car')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('Invalid SPIN provided')

    async def unlock_car(self, spin):
        if spin:
            resp = await self.call('-/vsr/remote-unlock', spin=spin)
            if not resp:
                _LOGGER.warning('Failed to unlock car')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('Invalid SPIN provided')

    async def start_electric_climatisation(self):
        """Turn on/off climatisation."""
        if self.is_electric_climatisation_supported:
            resp = await self.call('-/emanager/trigger-climatisation', triggerAction=True, electricClima=True)
            if not resp:
                _LOGGER.warning('Failed to start climatisation')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('No climatization support.')

    async def stop_electric_climatisation(self):
        """Turn on/off climatisation."""
        if self.is_electric_climatisation_supported:
            resp = await self.call('-/emanager/trigger-climatisation', triggerAction=False, electricClima=True)
            if not resp:
                _LOGGER.warning('Failed to stop climatisation')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('No climatization support.')

    async def start_window_heater(self):
        """Turn on/off window heater."""
        if self.is_window_heater_supported:
            resp = await self.call('-/emanager/trigger-windowheating', triggerAction=True)
            if not resp:
                _LOGGER.warning('Failed to start window heater')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('No climatization support.')

    async def stop_window_heater(self):
        """Turn on/off window heater."""
        if self.is_window_heater_supported:
            resp = await self.call('-/emanager/trigger-windowheating', triggerAction=False)
            if not resp:
                _LOGGER.warning('Failed to stop window heater')
        else:
            await self.update()
            _LOGGER.error('No window heating support.')

    async def start_combustion_engine_heating(self, spin):
        if spin:
            if self.is_combustion_engine_heating_supported:
                resp = await self.call('-/rah/quick-start', startMode='HEATING', spin=spin)
                if not resp:
                    _LOGGER.warning('Failed to start combustion engine heating')
                else:
                    await self.update()
                    return resp
            else:
                _LOGGER.error('No combustion engine heating support.')
        else:
            _LOGGER.error('Invalid SPIN provided')

    async def stop_combustion_engine_heating(self):
        if self.is_combustion_engine_heating_supported:
            resp = await self.call('-/rah/quick-stop')
            if not resp:
                _LOGGER.warning('Failed to stop combustion engine heating')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('No combustion engine heating support.')

    async def start_combustion_climatisation(self, spin):
        """Turn on/off climatisation."""
        if self.is_combustion_climatisation_supported:
            resp = await self.call('-/emanager/trigger-climatisation', triggerAction=True, electricClima=False, spin=spin)
            if not resp:
                _LOGGER.warning('Failed to start combustion climatisation')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('No combution climatization support.')

    async def stop_combustion_climatisation(self, spin):
        """Turn on/off climatisation."""
        if self.is_combustion_climatisation_supported:
            resp = await self.call('-/emanager/trigger-climatisation', triggerAction=False, electricClima=False, spin=spin)
            if not resp:
                _LOGGER.warning('Failed to stop combustion climatisation')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('No combustion climatization support.')

    async def start_charging(self):
        """Turn on/off window heater."""
        if self.is_charging_supported:
            resp = await self.call('-/emanager/charge-battery', triggerAction=True, batteryPercent='100')
            if not resp:
                _LOGGER.warning('Failed to start charging')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('No charging support.')

    async def stop_charging(self):
        """Turn on/off window heater."""
        if self.is_charging_supported:
            resp = await self.call('-/emanager/charge-battery', triggerAction=False, batteryPercent='99')
            if not resp:
                _LOGGER.warning('Failed to stop charging')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('No charging support.')

    async def set_climatisation_target_temperature(self, target_temperature):
        """Turn on/off window heater."""
        if self.is_electric_climatisation_supported or self.is_combustion_climatisation_supported:
            resp = await self.call('-/emanager/set-settings', chargerMaxCurrent=None, climatisationWithoutHVPower=None, minChargeLimit=None, targetTemperature=target_temperature)
            if not resp:
                _LOGGER.warning('Failed to set target temperature for climatisation')
            else:
                await self.update()
                return resp
        else:
            _LOGGER.error('No climatisation support.')

    async def request_report(self):
        """Request car to report its state when connected."""
        resp = await self.call('-/vhr/create-report')
        if not resp:
            _LOGGER.warning('Failed to request new report generation')
        else:
            return resp

    async def get_status(self, timeout=10):
        """Check status from call"""
        retry_counter = 0
        while retry_counter < timeout:
            resp = await self.call('-/emanager/get-notifications', data='dummy')
            data = resp.get('actionNotificationList', {})
            if data:
                return data
            time.sleep(1)
            retry_counter += 1
        return False

    def __str__(self):
        return self.vin

    @property
    def json(self):
        def serialize(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
        return to_json(
            OrderedDict(sorted(self.attrs.items())),
            indent=4,
            default=serialize
        )


async def main():
    """Main method."""
    if "-v" in argv:
        logging.basicConfig(level=logging.INFO)
    elif "-vv" in argv:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.ERROR)

    async with ClientSession(headers={'Connection': 'keep-alive'}) as session:
        connection = Connection(session, **read_config())
        if await connection._login():
            if await connection.update():
                for vehicle in connection.vehicles:
                    print(f'Vehicle id: {vehicle}')
                    print('Supported sensors:')
                    for instrument in vehicle.dashboard().instruments:
                        print(f' - {instrument.name} (domain:{instrument.component})')
            await connection._logout()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    # loop.run(main())
    loop.run_until_complete(main())
