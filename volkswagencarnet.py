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

version_info >= (3, 0) or exit('Python 3 required')

__version__ = '1.0.0'

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

class Connection(object):
    """ Connection to Volkswagen Carnet """
    def __init__(self, username, password):
        """ Initialize """
        self._session = Session()
        self._session_headers = { 'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json;charset=UTF-8', 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36' }
        self._session_base = 'https://www.volkswagen-car-net.com/'
        self._session_auth_headers = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8', 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'}
        self._session_auth_base = 'https://security.volkswagen.com'

        self._session_auth_ref_url = False
        self._session_auth_username = username
        self._session_auth_password = password

        _LOGGER.debug('Using service <%s>', self._session_base)
        _LOGGER.debug('User: <%s>', self._session_auth_username)

        self._state = {}

    def _login(self):
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

        # Update headers for requests
        self._session_headers["Referer"] = ref_url3
        self._session_headers["X-CSRF-Token"] = csrf
        self._session_auth_ref_url = ref_url3 + '/'
        return True

    def _request(self, method, ref, rel=None):
        try:
            url = urljoin(rel or self._session_auth_ref_url, ref)
            _LOGGER.debug('Request for %s', url)
            res = method(url, headers = self._session_headers, timeout=TIMEOUT.seconds)
            res.raise_for_status()
            res = res.json(object_hook=_obj_parser)
            _LOGGER.debug('Received %s', res)
            return res
        except RequestException as error:
            _LOGGER.warning('Failure when communcating with the server: %s', error)
            raise

    def _logout(self):
        self.post('-/logout/revoke')
        del(self._session)

    def get(self, ref, rel=None):
        """Perform a get query to the online service."""
        return self._request(self._session.get, ref, rel)

    def post(self, ref, rel=None, **data):
        """Perform a post query to the online service."""
        return self._request(partial(self._session.post, json=data), ref, rel)

    def update(self, reset=False):
        """Update status."""
        try:
            _LOGGER.info('Updating')
            if not self._state or reset:
                _LOGGER.info('Querying vehicles')

                loaded_cars = self.get('-/mainnavigation/get-fully-loaded-cars')

                for key, vehicles in loaded_cars['fullyLoadedVehiclesResponse'].items():
                    for vehicle in vehicles:
                        self._state.update({vehicle['vin']: vehicle})

            for vin, vehicle in self._state.items():
                rel = self._session_base + vehicle['dashboardUrl'] + '/'
                # request update
                update_request = self.post('-/vsr/request-vsr', rel, dummy='data')
                if update_request.get('errorCode') != '0':
                    _LOGGER.debug('Failed to request vehicle update')

                vehicle_emanager = self.get('-/emanager/get-emanager', rel)
                vehicle_location = self.get('-/cf/get-location', rel)
                vehicle_details = self.get('-/vehicle-info/get-vehicle-details', rel)
                vehicle_data = self.get('-/vsr/get-vsr', rel)

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
            _LOGGER.warning('Could not query server: %s', error)


    @property
    def vehicles(self):
        """Return vehicle state."""
        return (Vehicle(self, vin, data) for vin, data in self._state.items())

    def vehicle(self, vin):
        """Return vehicle for given vin."""
        return next((vehicle for vehicle in self.vehicles if vehicle.vin.lower() == vin.lower()), None)

class Vehicle(object):
    def __init__(self, conn, vin, data):
        self.data = data
        self.vin = vin
        self._connection = conn

    def get(self, query):
        """Perform a query to the online service."""
        #self._connection._login()
        rel = self._connection._session_base + self.data.get('dashboardUrl') + '/'
        req = self._connection.get(query, rel)
        #self._connection._logout()
        return req

    def post(self, query, **data):
        """Perform a query to the online service."""
        #self._connection._login()
        rel = self._connection._session_base + self.data.get('dashboardUrl') + '/'
        req = self._connection.post(query, rel, **data)
        #self._connection._logout()
        return req

    def call(self, method, **data):
        """Make remote method call."""
        try:
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
        check = self.data.get('vehicle-details', {}).get('lastConnectionTimeStamp', {})
        if check:
            check = check[0] + check[1]
            return datetime.strptime(check, '%d.%m.%Y%H:%M').strftime("%Y-%m-%d %H:%M:%S")


    @property
    def service_inspection(self):
        """Return time left for service inspection"""
        check = self.data.get('vehicle-details', {}).get('serviceInspectionData', {})
        if check:
            return check

    @property
    def image(self):
        """Return picture"""
        return self.data.get('imageUrl')

    @property
    def distance(self):
        return int(self.data.get('vehicle-details',{}).get('distanceCovered').replace('.', ''))


    @property
    def position_supported(self):
        """Return true if vehichle has position."""
        return self.data.get('position')

    @property
    def climatisation_supported(self):
        """Return true if vehichle has heater."""
        check = self.data.get('emanager', {}).get('rpc', {}).get('climaterActionState', {})
        if check == 'AVAILABLE':
            return True

    @property
    def window_heater_supported(self):
        """Return true if vehichle has heater."""
        if self.climatisation_supported:
            check = self.data.get('emanager', {}).get('rpc', {}).get('status',{}).get('windowHeatingAvailable', {})
            if check:
                return True

    @property
    def charging_supported(self):
        """Return true if vehichle has heater."""
        check = self.data.get('emanager', {}).get('rbc', {}).get('status',{}).get('batteryPercentage', {})
        if check:
            return True

    @property
    def charging_time_left(self):
        if self.charging_supported:
            hours = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('chargingRemaningHour', {})
            minutes = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('chargingRemaningMinute', {})
            if hours and minutes:
                return (int(hours) * 60) + int(minutes)

    @property
    def window_state_supported(self):
        """Return true if window state is supported"""
        check = self.data.get('vsr', {}).get('windowstateSupported', {})
        if check:
            return True

    @property
    def parking_ligth_state_supported(self):
        """Return true if parking light is supported"""
        check = self.data.get('carRenderData', {}).get('parkingLights', {})
        if check:
            return True

    @property
    def is_parking_lights_on(self):
        """Parking light state"""
        if self.parking_ligth_state_supported:
            state = self.data.get('carRenderData', {}).get('parkingLights', {})
            if state != 2:
                return True

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
    def is_external_power_connected(self):
        """Lock status."""
        if self.charging_supported:
            check = self.data.get('emanager', {}).get('rpc', {}).get('status', {}).get('pluginState', {})
            if check == 'CONNECTED':
                return True

    @property
    def is_climatisation_on(self):
        """Return status of climatisation."""
        status = self.data.get('emanager', {}).get('rpc', {}).get('status',{}).get('climatisationState', {})
        if status == 'ON':
            return True

    @property
    def is_window_heater_on(self):
        """Return status of window heater."""
        status_front = self.data.get('emanager', {}).get('rpc', {}).get('status', {}).get('windowHeatingStateFront', {})
        if status_front == 'ON':
            return True

        status_rear = self.data.get('emanager', {}).get('rpc', {}).get('status', {}).get('windowHeatingStateReart', {})
        if status_rear == 'ON':
            return True

    @property
    def is_charging_on(self):
        """Return status of charging."""
        status = self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('chargingState', {})
        if status == 'CHARGING':
            return True

    def start_climatisation(self):
        """Turn on/off climatisation."""
        if self.climatisation_supported:
            self.call('-/emanager/trigger-climatisation', triggerAction=True, electricClima=True)
        else:
            _LOGGER.error('No climatization support.')

    def stop_climatisation(self):
        """Turn on/off climatisation."""
        if self.climatisation_supported:
            self.call('-/emanager/trigger-climatisation', triggerAction=False, electricClima=True)
        else:
            _LOGGER.error('No climatization support.')

    def start_window_heater(self):
        """Turn on/off window heater."""
        if self.climatisation_supported:
            self.call('-/emanager/trigger-climatisation', triggerAction=True)
        else:
            _LOGGER.error('No climatization support.')

    def stop_window_heater(self):
        """Turn on/off window heater."""
        if self.climatisation_supported:
            self.call('-/emanager/trigger-windowheating', triggerAction=False)
        else:
            _LOGGER.error('No window heating support.')

    def start_charging(self):
        """Turn on/off window heater."""
        if self.climatisation_supported:
            self.call('-/emanager/charge-battery', triggerAction=True, batteryPercent='100')
        else:
            _LOGGER.error('No charging support.')

    def stop_charging(self):
        """Turn on/off window heater."""
        if self.climatisation_supported:
            self.call('-/emanager/charge-battery', triggerAction=False, batteryPercent='99')
        else:
            _LOGGER.error('No charging support.')

    def __str__(self):
        return self.vin.lower()