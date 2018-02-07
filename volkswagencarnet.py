#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Communicate with Volkswagen Carnet."""

import re
import logging
from sys import version_info
from requests import Session, RequestException
from datetime import timedelta, datetime
from urllib.parse import urlsplit, urljoin
from functools import partial
from json import dumps as to_json
from collections import OrderedDict

version_info >= (3, 0) or exit('Python 3 required')

__version__ = '2.0.11'

_LOGGER = logging.getLogger(__name__)

TIMEOUT = timedelta(seconds=30)

def _obj_parser(obj):
    """Parse datetime (only Python3 because of timezone)."""
    for key, val in obj.items():
        try:
            obj[key] = datetime.strptime(val, '%Y-%m-%dT%H:%M:%S%z')
        except (TypeError, ValueError):
            pass
    return obj

class TimeoutRequestsSession(Session):
    def request(self, *args, **kwargs):
        if kwargs.get('timeout') is None:
            kwargs['timeout'] = 60
        return super(TimeoutRequestsSession, self).request(*args, **kwargs)

class Connection(object):
    """ Connection to Volkswagen Carnet """
    def __init__(self, username, password):
        """ Initialize """
        self._session = TimeoutRequestsSession()
        self._session_headers = { 'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json;charset=UTF-8', 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36' }
        self._session_base = 'https://www.volkswagen-car-net.com/'
        self._session_auth_headers = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8', 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'}
        self._session_auth_base = 'https://security.volkswagen.com'
        self._session_guest_language_id = 'se'

        self._session_auth_ref_url = False
        self._session_auth_username = username
        self._session_auth_password = password

        _LOGGER.debug('Using service <%s>', self._session_base)
        _LOGGER.debug('User: <%s>', self._session_auth_username)

        self._state = {}

    def _login(self):
        """ Reset session in case we would like to login again """
        self._session = TimeoutRequestsSession()
        self._session_headers = { 'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json;charset=UTF-8', 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36' }
        self._session_auth_headers = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8', 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'}

        # Regular expressions to extract data
        re_csrf = re.compile('<meta name="_csrf" content="([^"]*)"/>')
        re_redurl = re.compile('<redirect url="([^"]*)"></redirect>')
        re_viewstate = re.compile('name="javax.faces.ViewState" id="j_id1:javax.faces.ViewState:0" value="([^"]*)"')
        re_authcode = re.compile('code=([^"]*)&')
        re_authstate = re.compile('state=([^"]*)')

        def extract_csrf(req):
            return re_csrf.search(req.text).group(1)

        def extract_redirect_url(req):
            return re_redurl.search(req.text).group(1)

        def extract_view_state(req):
            return re_viewstate.search(req.text).group(1)

        def extract_code(req):
            return re_authcode.search(req).group(1)

        def extract_state(req):
            return re_authstate.search(req).group(1)

        def extract_guest_language_id(req):
            return req.split('_')[1].lower()

        # Request landing page and get CSFR:
        req = self._session.get(self._session_base + '/portal/en_GB/web/guest/home')
        if req.status_code != 200:
            return ""
        csrf = extract_csrf(req)

        # Request login page and get CSRF
        self._session_auth_headers['Referer'] = self._session_base + 'portal'
        self._session_auth_headers["X-CSRF-Token"] = csrf
        req = self._session.post(self._session_base + 'portal/web/guest/home/-/csrftokenhandling/get-login-url', headers = self._session_auth_headers)
        if req.status_code != 200:
            return ""
        response_data = req.json()
        lg_url = response_data.get("loginURL").get("path")

        # no redirect so we can get values we look for
        req = self._session.get(lg_url, allow_redirects=False, headers = self._session_auth_headers)
        if req.status_code != 302:
            return ""
        ref_url = req.headers.get("location")

        # now get actual login page and get session id and ViewState
        req = self._session.get(ref_url, headers = self._session_auth_headers)
        if req.status_code != 200:
            return ""

        view_state = extract_view_state(req)

        # login with user details
        self._session_auth_headers["Faces-Request"] = "partial/ajax"
        self._session_auth_headers["Referer"] = ref_url
        self._session_auth_headers["X-CSRF-Token"] = ''

        post_data = {
            'loginForm': 'loginForm',
            'loginForm:email': self._session_auth_username,
            'loginForm:password': self._session_auth_password,
            'loginForm:j_idt19': '',
            'javax.faces.ViewState': view_state,
            'javax.faces.source': 'loginForm:submit',
            'javax.faces.partial.event': 'click',
            'javax.faces.partial.execute': 'loginForm:submit loginForm',
            'javax.faces.partial.render': 'loginForm',
            'javax.faces.behavior.event': 'action',
            'javax.faces.partial.ajax': 'true'
        }

        req = self._session.post(self._session_auth_base + '/ap-login/jsf/login.jsf', data = post_data, headers = self._session_auth_headers)
        if req.status_code != 200:
            return ""
        ref_url = extract_redirect_url(req).replace('&amp;', '&')

        # redirect to link from login and extract state and code values
        req = self._session.get(ref_url, allow_redirects=False, headers = self._session_auth_headers)
        if req.status_code != 302:
            return ""
        ref_url2 = req.headers.get("location")

        code = extract_code(ref_url2)
        state = extract_state(ref_url2)

        # load ref page
        req = self._session.get(ref_url2, headers = self._session_auth_headers)
        if req.status_code != 200:
            return ""

        self._session_auth_headers["Faces-Request"] = ""
        self._session_auth_headers["Referer"] = ref_url2
        post_data = {
            '_33_WAR_cored5portlet_code': code,
            '_33_WAR_cored5portlet_landingPageUrl': ''
        }
        req = self._session.post(self._session_base + urlsplit(ref_url2).path + '?p_auth=' + state + '&p_p_id=33_WAR_cored5portlet&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view&p_p_col_id=column-1&p_p_col_count=1&_33_WAR_cored5portlet_javax.portlet.action=getLoginStatus', data = post_data, allow_redirects = False, headers = self._session_auth_headers)
        if req.status_code != 302:
            return ""

        ref_url3 = req.headers.get("location")
        req = self._session.get(ref_url3, headers = self._session_auth_headers)

        # We have a new CSRF
        csrf = extract_csrf(req)

        # Request country code
        req = self._session.get(self._session_base + '/portal/en_GB/web/guest/complete-login/-/mainnavigation/get-countries')
        if req.status_code != 200:
            return ""
        cookie = self._session.cookies.get_dict()
        self._session_guest_language_id = extract_guest_language_id(cookie.get('GUEST_LANGUAGE_ID', {}))

        # Update headers for requests
        self._session_headers["Referer"] = ref_url3
        self._session_headers["X-CSRF-Token"] = csrf
        self._session_auth_ref_url = ref_url3 + '/'
        return True

    def _request(self, method, ref, rel=None):
        try:
            url = urljoin(rel or self._session_auth_ref_url, ref)
            _LOGGER.debug('Request for %s', url)
            #res = method(url, headers = self._session_headers, timeout=TIMEOUT.seconds)
            res = method(url, headers=self._session_headers)
            res.raise_for_status()
            res = res.json(object_hook=_obj_parser)
            _LOGGER.debug('Received %s', res)
            return res
        except RequestException as error:
            _LOGGER.warning('Failure when communcating with the server: %s', error)
            raise

    def _logout(self):
        self.post('-/logout/revoke')

    def get(self, ref, rel=None):
        """Perform a get query to the online service."""
        return self._request(self._session.get, ref, rel)

    def post(self, ref, rel=None, **data):
        """Perform a post query to the online service."""
        return self._request(partial(self._session.post, json=data), ref, rel)

    def update(self, reset=False):
        """Update status."""
        try:
            if not self.validate_login:
                _LOGGER.warning('Session expired, creating new login session to carnet.')
                self._login()
            _LOGGER.debug('Updating vehicle status from carnet')
            if not self._state or reset:
                _LOGGER.debug('Querying vehicles')
                owners_verification = self.get('/portal/group/%s/edit-profile/-/profile/get-vehicles-owners-verification' % self._session_guest_language_id)

                loaded_cars = self.get('-/mainnavigation/get-fully-loaded-cars')

                for key, vehicles in loaded_cars['fullyLoadedVehiclesResponse'].items():
                    for vehicle in vehicles:
                        self._state.update({vehicle['vin']: vehicle})

            for vin, vehicle in self._state.items():
                rel = self._session_base + vehicle['dashboardUrl'] + '/'

                # fetch vehicle status data
                vehicle_data = self.get('-/vsr/get-vsr', rel)

                # request update of vehicle status data if not in progress
                if vehicle_data.get('vehicleStatusData', {}).get('requestStatus', {}) != 'REQUEST_IN_PROGRESS':
                    update_request = self.post('-/vsr/request-vsr', rel, dummy='data')
                    if update_request.get('errorCode') != '0':
                        _LOGGER.error('Failed to request vehicle update')
                    else:
                        vehicle_data = self.get('-/vsr/get-vsr', rel)

                # fetch vehicle emanage data
                vehicle_emanager = self.get('-/emanager/get-emanager', rel)
                # fetch vehicle location data
                vehicle_location = self.get('-/cf/get-location', rel)
                # fetch vehicle details data
                vehicle_details = self.get('-/vehicle-info/get-vehicle-details', rel)

                if vehicle_emanager.get('errorCode') == '0':
                    self._state[vin]['emanager'] = vehicle_emanager['EManager']
                if vehicle_location.get('errorCode') == '0':
                    self._state[vin]['position'] = vehicle_location['position']
                if vehicle_details.get('errorCode') == '0':
                    self._state[vin]['vehicle-details'] = vehicle_details['vehicleDetails']
                if vehicle_data.get('errorCode') == '0':
                    self._state[vin]['vsr'] = vehicle_data['vehicleStatusData']

                _LOGGER.debug('State: %s', self._state)

            return True
        except (IOError, OSError) as error:
            _LOGGER.warning('Could not update information from carnet: %s', error)

    def vehicle(self, vin):
        """Return vehicle for given vin."""
        return next((vehicle for vehicle in self.vehicles if vehicle.vin.lower() == vin.lower()), None)

    @property
    def vehicles(self):
        """Return vehicle state."""
        return (Vehicle(self, vin, data) for vin, data in self._state.items())

    @property
    def validate_login(self):
        try:
            messages = self.get('-/msgc/get-new-messages')
            if messages.get('errorCode', {}) == '0':
                return True
            else:
                return False
        except (IOError, OSError) as error:
            _LOGGER.warning('Could not validate login: %s', error)
            return False

class Vehicle(object):
    def __init__(self, conn, vin, data):
        self.data = data
        self.vin = vin
        self._connection = conn

    def get(self, query):
        """Perform a query to the online service."""
        rel = self._connection._session_base + self.data.get('dashboardUrl') + '/'
        req = self._connection.get(query, rel)
        return req

    def post(self, query, **data):
        """Perform a query to the online service."""
        rel = self._connection._session_base + self.data.get('dashboardUrl') + '/'
        req = self._connection.post(query, rel, **data)
        return req

    def call(self, method, **data):
        """Make remote method call."""
        try:
            if not self._connection.validate_login:
                _LOGGER.warning('Session expired, logging in again to carnet.')
                self._connection._login()
            res = self.post(method, **data)
            if res.get('errorCode') != '0':
                _LOGGER.warning('Failed to execute')
            else:
                _LOGGER.debug('Message delivered')
                return True
        except RequestException as error:
            _LOGGER.warning('Failure to execute: %s', error)

    @property
    def last_connected(self):
        """Return when vehicle was last connected to carnet"""
        if self.last_connected_supported:
            last_connected = self.data.get('vehicle-details', {}).get('lastConnectionTimeStamp', {})
            if last_connected:
                last_connected = last_connected[0] + last_connected[1]
                return datetime.strptime(last_connected, '%d.%m.%Y%H:%M').strftime("%Y-%m-%d %H:%M:%S")

    @property
    def last_connected_supported(self):
        """Return when vehicle was last connected to carnet"""
        check = self.data.get('vehicle-details', {}).get('lastConnectionTimeStamp', {})
        if check:
            return True

    @property
    def climatisation_target_temperature(self):
        if self.climatisation_supported:
            temperature = self.data.get('emanager', {}).get('rpc', {}).get('settings',{}).get('targetTemperature', {})
            if temperature:
                return temperature

    @property
    def climatisation_target_temperature_supported(self):
        if self.climatisation_supported:
            return True

    @property
    def service_inspection(self):
        """Return time left for service inspection"""
        if self.service_inspection_supported:
            check = self.data.get('vehicle-details', {}).get('serviceInspectionData', {})
            if check:
                return check

    @property
    def service_inspection_supported(self):
        check = self.data.get('vehicle-details', {}).get('serviceInspectionData', {})
        if check:
            return True

    @property
    def battery_level(self):
        """Return battery level"""
        if self.battery_level_supported:
            return self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('batteryPercentage', {})

    @property
    def battery_level_supported(self):
        check = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('batteryPercentage', {})
        if isinstance(check, int):
            return True

    @property
    def charge_max_ampere(self):
        """Return charge max ampere"""
        if self.charge_max_ampere_supported:
            return self.data.get('emanager', {}).get('rbc', {}).get('settings', {}).get('chargerMaxCurrent', {})

    @property
    def charge_max_ampere_supported(self):
        check = self.data.get('emanager', {}).get('rbc', {}).get('settings', {}).get('chargerMaxCurrent', {})
        if isinstance(check, int):
            return True

    @property
    def parking_light(self):
        """Return true if parking light is on"""
        if self.parking_light_supported:
            check = self.data.get('vsr', {}).get('carRenderData', {}).get('parkingLights', {})
            if check != 2:
                return True
            else:
                return False

    @property
    def parking_light_supported(self):
        """Return true if parking light is supported"""
        check = self.data.get('vsr', {}).get('carRenderData',{}).get('parkingLights', {})
        if isinstance(check, int):
            return True

    @property
    def distance(self):
        if self.distance_supported:
            return int(self.data.get('vehicle-details',{}).get('distanceCovered').replace('.', ''))

    @property
    def distance_supported(self):
        """Return true if distance is supported"""
        check = self.data.get('vehicle-details',{}).get('distanceCovered')
        if check: return True

    @property
    def position(self):
        """Return  position."""
        if self.position_supported:
            return self.data.get('position')

    @property
    def position_supported(self):
        """Return true if vehichle has position."""
        check = self.data.get('position', {}).get('lng', {})
        if check: return True

    @property
    def model(self):
        """Return model"""
        if self.model_supported:
            return self.data.get('model')

    @property
    def model_supported(self):
        check = self.data.get('model')
        if check: return True

    @property
    def model_year(self):
        """Return model year"""
        if self.model_year_supported:
            return self.data.get('modelYear', {})

    @property
    def model_year_supported(self):
        check = self.data.get('modelYear', {})
        if check: return True

    @property
    def model_image(self):
        """Return model image"""
        if self.model_image_supported:
            return self.data.get('imageUrl', {})

    @property
    def model_image_supported(self):
        check = self.data.get('imageUrl', {})
        if check: return True

    @property
    def charging_supported(self):
        """Return true if vehichle has heater."""
        check = self.data.get('emanager', {}).get('rbc', {}).get('status',{}).get('batteryPercentage', {})
        if check:
            return True

    @property
    def electric_range(self):
        if self.electric_range_supported:
            return self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('electricRange', {})

    @property
    def electric_range_supported(self):
        check = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('electricRange', {})
        if isinstance(check, int):
            return True

    @property
    def combustion_range(self):
        if self.combustion_range_supported:
            return self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('combustionRange', {})

    @property
    def combustion_range_supported(self):
        check = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('combustionRange', {})
        if isinstance(check, int):
            return True

    @property
    def total_range(self):
        if self.total_range_supported:
            return self.electric_range + self.combustion_range

    @property
    def total_range_supported(self):
        if self.combustion_range_supported and self.electric_range_supported:
            return True

    @property
    def fuel_level(self):
        if self.fuel_level_supported:
            return self.data.get('vsr', {}).get('fuelLevel', {})

    @property
    def fuel_level_supported(self):
        check = self.data.get('vsr', {}).get('fuelLevel', {})
        if isinstance(check, int):
            return True

    @property
    def external_power(self):
        """Return true if external power is connected."""
        if self.external_power_supported:
            check = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('pluginState', {})
            if check == 'CONNECTED':
                return True
            else:
                return False

    @property
    def external_power_supported(self):
        """External power supported."""
        if self.charging_supported:
            check = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('pluginState', {})
            if check: return True

    @property
    def climatisation_supported(self):
        """Return true if vehichle has heater."""
        check = self.data.get('emanager', {}).get('rpc', {}).get('climaterActionState', {})
        if check == 'AVAILABLE': return True

    @property
    def window_heater_supported(self):
        """Return true if vehichle has heater."""
        if self.climatisation_supported:
            check = self.data.get('emanager', {}).get('rpc', {}).get('status',{}).get('windowHeatingAvailable', {})
            if check: return True

    @property
    def window_supported(self):
        """Return true if window state is supported"""
        check = self.data.get('vsr', {}).get('windowstateSupported', {})
        if check:
            return True

    @property
    def charging_time_left(self):
        if self.charging_supported:
            if self.external_power:
                hours = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('chargingRemaningHour', {})
                minutes = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('chargingRemaningMinute', {})
                if hours and minutes:
                    return (int(hours) * 60) + int(minutes)

    @property
    def charging_time_left_supported(self):
        if self.charging_supported:
            return True

    @property
    def door_locked(self):
        if self.door_locked_supported:
            lock_data = self.data.get('vsr', {}).get('lockData', {})
            vehicle_locked = True
            for lock in lock_data:
                if lock_data[lock] != 2:
                    vehicle_locked = False
            return vehicle_locked

    @property
    def door_locked_supported(self):
        check = self.data.get('vsr', {}).get('lockData', {})
        if len(check) > 0:
            return True

    # states
    @property
    def is_parking_lights_on(self):
        """Parking light state"""
        if self.parking_light_supported:
            state = self.data.get('carRenderData', {}).get('parkingLights', {})
            if state != 2:
                return True
            else:
                return False

    @property
    def is_doors_locked(self):
        """Door lock status."""
        state = True
        check = self.data.get('vsr', {}).get('lockData', {})
        for lock in check:
            if check[lock] != 2:
                state = False
        return state

    @property
    def is_climatisation_on(self):
        """Return status of climatisation."""
        if self.climatisation_supported:
            status = self.data.get('emanager', {}).get('rpc', {}).get('status',{}).get('climatisationState', {})
            if status == 'ON':
                return True
            else:
                return False

    @property
    def is_window_heater_on(self):
        """Return status of window heater."""
        if self.window_heater_supported:
            ret = False
            status_front = self.data.get('emanager', {}).get('rpc', {}).get('status', {}).get('windowHeatingStateFront', {})
            if status_front == 'ON':
                ret = True

            status_rear = self.data.get('emanager', {}).get('rpc', {}).get('status', {}).get('windowHeatingStateReart', {})
            if status_rear == 'ON':
                ret = True
            return ret

    @property
    def is_charging_on(self):
        """Return status of charging."""
        if self.charging_supported:
            status = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('chargingState', {})
            if status == 'CHARGING':
                return True
            else:
                return False

    @property
    def is_request_in_progress(self):
        check = self.data.get('vsr', {}).get('requestStatus', {})
        if check == 'REQUEST_IN_PROGRESS':
            return True
        else:
            return False

    # actions
    def start_climatisation(self):
        """Turn on/off climatisation."""
        if self.climatisation_supported:
            if not self.call('-/emanager/trigger-climatisation', triggerAction=True, electricClima=True):
                _LOGGER.warning('Failed to start climatisation')
        else:
            _LOGGER.error('No climatization support.')

    def stop_climatisation(self):
        """Turn on/off climatisation."""
        if self.climatisation_supported:
            if not self.call('-/emanager/trigger-climatisation', triggerAction=False, electricClima=True):
                _LOGGER.warning('Failed to stop climatisation')
        else:
            _LOGGER.error('No climatization support.')

    def start_window_heater(self):
        """Turn on/off window heater."""
        if self.climatisation_supported:
            if not self.call('-/emanager/trigger-windowheating', triggerAction=True):
                _LOGGER.warning('Failed to start window heater')
        else:
            _LOGGER.error('No climatization support.')

    def stop_window_heater(self):
        """Turn on/off window heater."""
        if self.climatisation_supported:
            if not self.call('-/emanager/trigger-windowheating', triggerAction=False):
                _LOGGER.warning('Failed to stop window heater')
        else:
            _LOGGER.error('No window heating support.')

    def start_charging(self):
        """Turn on/off window heater."""
        if self.climatisation_supported:
            if not self.call('-/emanager/charge-battery', triggerAction=True, batteryPercent='100'):
                _LOGGER.warning('Failed to start charging')
        else:
            _LOGGER.error('No charging support.')

    def stop_charging(self):
        """Turn on/off window heater."""
        if self.climatisation_supported:
            if not self.call('-/emanager/charge-battery', triggerAction=False, batteryPercent='99'):
                _LOGGER.warning('Failed to stop charging')
        else:
            _LOGGER.error('No charging support.')

    def __str__(self):
        return self.vin

    @property
    def json(self):
        def serialize(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
        return to_json(
            OrderedDict(sorted(self.data.items())), indent=4, default=serialize)