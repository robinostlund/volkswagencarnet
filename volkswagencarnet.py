#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Communicate with Volkswagen Carnet."""
import re
import time
import logging

from sys import version_info
from requests import Session, RequestException, packages
from requests.packages.urllib3 import disable_warnings
from datetime import timedelta, datetime
from urllib.parse import urlsplit, urljoin, parse_qs, urlparse
from functools import partial
from json import dumps as to_json
from collections import OrderedDict
from bs4 import BeautifulSoup
from utilities import find_path, is_valid_path

version_info >= (3, 0) or exit('Python 3 required')

__version__ = '4.1.6'

_LOGGER = logging.getLogger(__name__)

TIMEOUT = timedelta(seconds=30)

# disable request ssl verification due to vw having issues with ca.
disable_warnings()


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
        # ignore ssl verification due to vw having issues with ca.
        kwargs['verify'] = False
        return super(TimeoutRequestsSession, self).request(*args, **kwargs)


class Connection(object):
    """ Connection to Volkswagen Carnet """

    def __init__(self, username, password, guest_lang='en'):
        """ Initialize """
        self._session = TimeoutRequestsSession()
        self._session_headers = {'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json;charset=UTF-8',
                                 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'}
        self._session_base = 'https://www.portal.volkswagen-we.com/'
        self._session_auth_headers = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3', 'Content-Type': 'application/x-www-form-urlencoded',
                                      'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'}
        self._session_auth_base = 'https://identity.vwgroup.io'
        self._session_guest_language_id = guest_lang

        self._session_auth_ref_url = False
        self._session_logged_in = False
        self._session_first_update = False
        self._session_auth_username = username
        self._session_auth_password = password

        _LOGGER.debug('Using service <%s>', self._session_base)
        _LOGGER.debug('User: <%s>', self._session_auth_username)

        self._state = {}

    def _login(self):
        """ Reset session in case we would like to login again """
        self._session = TimeoutRequestsSession()
        self._session_headers = {'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json;charset=UTF-8',
                                 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'}
        self._session_auth_headers = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3', 'Content-Type': 'application/x-www-form-urlencoded',
                                      'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'}

        def extract_csrf(req):
            return re.compile('<meta name="_csrf" content="([^"]*)"/>').search(req.text).group(1)

        def extract_guest_language_id(req):
            return req.split('_')[1].lower()

        try:
            # Request landing page and get CSFR:
            req = self._session.get(
                self._session_base + '/portal/en_GB/web/guest/home')
            if req.status_code != 200:
                return ""
            csrf = extract_csrf(req)

            # Request login page and get CSRF
            self._session_auth_headers['Referer'] = self._session_base + 'portal'
            req = self._session.post(
                self._session_base + 'portal/web/guest/home/-/csrftokenhandling/get-login-url', headers=self._session_auth_headers)
            if req.status_code != 200:
                return ""

            response_data = req.json()
            lg_url = response_data.get("loginURL").get("path")

            # no redirect so we can get values we look for
            req = self._session.get(
                lg_url, allow_redirects=False, headers=self._session_auth_headers)
            if req.status_code != 302:
                return ""
            ref_url_1 = req.headers.get("location")

            # now get actual login page and get session id and ViewState
            req = self._session.get(
                ref_url_1, headers=self._session_auth_headers)
            if req.status_code != 200:
                return ""

            # get login variables
            bs = BeautifulSoup(req.text, 'html.parser')
            login_csrf = bs.select_one('input[name=_csrf]')['value']
            login_token = bs.select_one('input[name=relayState]')['value']
            login_hmac = bs.select_one('input[name=hmac]')['value']
            login_form_action = bs.find(
                'form', id='emailPasswordForm').get('action')
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
            req = self._session.post(
                login_url, data=post_data, allow_redirects=False, headers=self._session_auth_headers)

            if req.status_code != 303:
                return ""

            ref_url_2 = req.headers.get("location")
            auth_relay_url = self._session_auth_base + ref_url_2

            # get: https://identity.vwgroup.io/signin-service/v1/xxx@apps_vw-dilab_com/login/authenticate?relayState=xxx&email=xxx
            req = self._session.get(
                auth_relay_url, allow_redirects=False, headers=self._session_auth_headers)
            if req.status_code != 200:
                return ""

            # post: https://identity.vwgroup.io/signin-service/v1/xxx@apps_vw-dilab_com/login/authenticate
            bs = BeautifulSoup(req.text, 'html.parser')
            auth_csrf = bs.select_one('input[name=_csrf]')['value']
            auth_token = bs.select_one('input[name=relayState]')['value']
            auth_hmac = bs.select_one('input[name=hmac]')['value']
            auth_form_action = bs.find(
                'form', id='credentialsForm').get('action')
            auth_url = self._session_auth_base + auth_form_action

            # post login
            self._session_auth_headers["Referer"] = auth_relay_url

            post_data = {
                'email': self._session_auth_username,
                'password': self._session_auth_password,
                'relayState': auth_token,
                'hmac': auth_hmac,
                '_csrf': auth_csrf,
            }
            # post: https://identity.vwgroup.io/signin-service/v1/xxx@apps_vw-dilab_com/login/authenticate
            req = self._session.post(
                auth_url, data=post_data, allow_redirects=False, headers=self._session_auth_headers)
            if req.status_code != 302:
                return ""

            # get: https://identity.vwgroup.io/oidc/v1/oauth/sso?clientId=xxx@apps_vw-dilab_com&relayState=xxx&userId=xxx&HMAC=xxx
            ref_url_3 = req.headers.get("location")
            req = self._session.get(
                ref_url_3, allow_redirects=False, headers=self._session_auth_headers)
            if req.status_code != 302:
                return ""

            # get: https://identity.vwgroup.io/consent/v1/users/xxx/xxx@apps_vw-dilab_com?scopes=openid%20profile%20birthdate%20nickname%20address%20email%20phone%20cars%20dealers%20mbb&relay_state=xxx&callback=https://identity.vwgroup.io/oidc/v1/oauth/client/callback&hmac=xxx
            ref_url_4 = req.headers.get("location")
            req = self._session.get(
                ref_url_4, allow_redirects=False, headers=self._session_auth_headers)
            if req.status_code != 302:
                return ""

            # get: https://identity.vwgroup.io/oidc/v1/oauth/client/callback/success?user_id=xxx&client_id=xxx@apps_vw-dilab_com&scopes=openid%20profile%20birthdate%20nickname%20address%20email%20phone%20cars%20dealers%20mbb&consentedScopes=openid%20profile%20birthdate%20nickname%20address%20email%20phone%20cars%20dealers%20mbb&relay_state=xxx&hmac=xxx
            ref_url_5 = req.headers.get("location")
            user_id = parse_qs(urlparse(ref_url_5).query).get('user_id')[0]
            req = self._session.get(
                ref_url_5, allow_redirects=False, headers=self._session_auth_headers)
            if req.status_code != 302:
                return ""

            # get: https://www.portal.volkswagen-we.com/portal/web/guest/complete-login?state=xxx&code=xxx
            ref_url_6 = req.headers.get("location")
            state = parse_qs(urlparse(ref_url_6).query).get('state')[0]
            code = parse_qs(urlparse(ref_url_6).query).get('code')[0]

            # post: https://www.portal.volkswagen-we.com/portal/web/guest/complete-login?p_auth=xxx&p_p_id=33_WAR_cored5portlet&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view&p_p_col_id=column-1&p_p_col_count=1&_33_WAR_cored5portlet_javax.portlet.action=getLoginStatus
            self._session_auth_headers["Referer"] = ref_url_6
            post_data = {
                '_33_WAR_cored5portlet_code': code,
                '_33_WAR_cored5portlet_landingPageUrl': ''
            }
            req = self._session.post(self._session_base + urlsplit(ref_url_6).path + '?p_auth=' + state +
                                     '&p_p_id=33_WAR_cored5portlet&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view&p_p_col_id=column-1&p_p_col_count=1&_33_WAR_cored5portlet_javax.portlet.action=getLoginStatus', data=post_data, allow_redirects=False, headers=self._session_auth_headers)
            if req.status_code != 302:
                return ""

            # get: https://www.portal.volkswagen-we.com/portal/user/xxx/v_8xxx
            ref_url_7 = req.headers.get("location")
            req = self._session.get(
                ref_url_7, allow_redirects=False, headers=self._session_auth_headers)
            if req.status_code != 200:
                return ""

            # We have a new CSRF
            csrf = extract_csrf(req)

            cookie = self._session.cookies.get_dict()
            self._session_guest_language_id = extract_guest_language_id(
                cookie.get('GUEST_LANGUAGE_ID', {}))

            # Update headers for requests
            self._session_headers["Referer"] = ref_url_7
            self._session_headers["X-CSRF-Token"] = csrf
            self._session_auth_ref_url = ref_url_7 + '/'
            self._session_logged_in = True
            return True

        except Exception as error:
            _LOGGER.error('Failed to login to carnet, %s' % error)
            self._session_logged_in = False
            return False

    def _request(self, method, ref, rel=None):
        url = urljoin(rel or self._session_auth_ref_url, ref)
        try:
            _LOGGER.debug('Request for %s', url)
            res = method(url, headers=self._session_headers)
            res.raise_for_status()
            res = res.json(object_hook=_obj_parser)
            _LOGGER.debug('Received %s', res)
            return res
        except RequestException as error:
            _LOGGER.warning(
                'Failure when communcating with the server: %s, url: %s', error, url)
            raise

    def _logout(self):
        self.post('-/logout/revoke')

    def get(self, ref, rel=None):
        """Perform a get query to the online service."""
        return self._request(self._session.get, ref, rel)

    def post(self, ref, rel=None, **data):
        """Perform a post query to the online service."""
        return self._request(partial(self._session.post, json=data), ref, rel)

    def update(self, reset=False, request_data=True):
        """Update status."""
        try:
            if self._session_first_update:
                if not self.validate_login:
                    _LOGGER.warning(
                        'Session expired, creating new login session to carnet.')
                    self._login()
            else:
                self._session_first_update = True

            _LOGGER.debug('Updating vehicle status from carnet')
            if not self._state or reset:
                _LOGGER.debug('Querying vehicles')
                owners_verification = self.post(
                    '/portal/group/%s/edit-profile/-/profile/get-vehicles-owners-verification' % self._session_guest_language_id)

                # get vehicles
                loaded_vehicles = self.post(
                    '-/mainnavigation/get-fully-loaded-cars')

                # load all not loaded vehicles
                if loaded_vehicles.get('fullyLoadedVehiclesResponse', {}).get('vehiclesNotFullyLoaded', []):
                    for vehicle in loaded_vehicles.get('fullyLoadedVehiclesResponse').get('vehiclesNotFullyLoaded'):
                        self.post('-/mainnavigation/load-car-details/%s' %
                                  vehicle.get('vin'))

                    # update loaded cars
                    loaded_vehicles = self.post(
                        '-/mainnavigation/get-fully-loaded-cars')

                # update vehicles
                if loaded_vehicles.get('fullyLoadedVehiclesResponse', {}).get('completeVehicles', []):
                    for vehicle in loaded_vehicles.get('fullyLoadedVehiclesResponse').get('completeVehicles'):
                        self._state.update({vehicle['vin']: vehicle})

            for vin, vehicle in self._state.items():
                rel = self._session_base + vehicle['dashboardUrl'] + '/'

                # fetch vehicle vsr data
                try:
                    vehicle_data = self.post('-/vsr/get-vsr', rel)
                    if vehicle_data.get('errorCode', {}) == '0' and vehicle_data.get('vehicleStatusData', {}):
                        self._state[vin]['vsr'] = vehicle_data.get(
                            'vehicleStatusData', {})
                except Exception as err:
                    _LOGGER.debug('Could not fetch vsr data: %s' % err)

                # request update of vehicle status data if not in progress
                if request_data and vehicle_data.get('vehicleStatusData', {}).get('requestStatus', {}) != 'REQUEST_IN_PROGRESS':
                    update_request = self.post(
                        '-/vsr/request-vsr', rel, dummy='data')
                    if update_request.get('errorCode') != '0':
                        _LOGGER.error('Failed to request vehicle update')

                print(vehicle)

                #if not vehicle['engineTypeCombustian']:
                if vehicle.get('emanager', {}).get('rdtAvailable', False):
                    # fetch vehicle emanage data
                    try:
                        vehicle_emanager = self.post(
                            '-/emanager/get-emanager', rel)
                        if vehicle_emanager.get('errorCode', {}) == '0' and vehicle_emanager.get('EManager', {}):
                            self._state[vin]['emanager'] = vehicle_emanager.get(
                                'EManager', {})
                    except Exception as err:
                        _LOGGER.debug(
                            'Could not fetch emanager data: %s' % err)

                # fetch vehicle location data
                try:
                    vehicle_location = self.post('-/cf/get-location', rel)
                    if vehicle_location.get('errorCode', {}) == '0' and vehicle_location.get('position', {}):
                        self._state[vin]['position'] = vehicle_location.get(
                            'position', {})
                except Exception as err:
                    _LOGGER.debug('Could not fetch location data: %s' % err)

                # fetch vehicle details data
                try:
                    vehicle_details = self.post(
                        '-/vehicle-info/get-vehicle-details', rel)
                    if vehicle_details.get('errorCode', {}) == '0' and vehicle_details.get('vehicleDetails', {}):
                        self._state[vin]['vehicle-details'] = vehicle_details.get(
                            'vehicleDetails', {})
                except Exception as err:
                    _LOGGER.debug('Could not fetch details data: %s' % err)

                if vehicle['engineTypeCombustian']:
                    # fetch combustion engine remote auxiliary heating status data
                    try:
                        vehicle_status = self.post('-/rah/get-status')
                        if vehicle_status.get('errorCode', {}) == '0' and vehicle_status.get('remoteAuxiliaryHeating', {}):
                            self._state[vin]['remoteAuxiliaryHeating'] = vehicle_status.get(
                                'remoteAuxiliaryHeating', {})
                    except Exception as err:
                        _LOGGER.debug(
                            'Could not fetch remoteAuxiliaryHeating data: %s' % err)

                _LOGGER.debug('State: %s', self._state)

            return True
        except (IOError, OSError) as error:
            _LOGGER.warning(
                'Could not update information from carnet: %s', error)

    def vehicle(self, vin):
        """Return vehicle for given vin."""
        return next((vehicle for vehicle in self.vehicles if vehicle.vin.lower() == vin.lower()), None)

    @property
    def vehicles(self):
        """Return vehicle state."""
        return (Vehicle(self, vin, data) for vin, data in self._state.items())

    def vehicle_attrs(self, vin):
        return self._state.get(vin)

    @property
    def validate_login(self):
        try:
            messages = self.post('-/msgc/get-new-messages')
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


class Vehicle(object):
    def __init__(self, conn, vin, data):
        self.data = data
        self.vin = vin
        self._connection = conn

    def get(self, query):
        """Perform a query to the online service."""
        rel = self._connection._session_base + \
            self.data.get('dashboardUrl') + '/'
        req = self._connection.get(query, rel)
        return req

    def post(self, query, **data):
        """Perform a query to the online service."""
        rel = self._connection._session_base + \
            self.data.get('dashboardUrl') + '/'
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
                return False
            else:
                _LOGGER.debug('Message delivered')
                return res
        except RequestException as error:
            _LOGGER.warning('Failure to execute: %s', error)

    @property
    def attrs(self):
        return self._connection.vehicle_attrs(self.vin)

    def has_attr(self, attr):
        return is_valid_path(self.attrs, attr)

    def get_attr(self, attr):
        return find_path(self.attrs, attr)

    def dashboard(self, **config):
        from dashboard import Dashboard
        return Dashboard(self, **config)

    @property
    def last_connected(self):
        """Return when vehicle was last connected to carnet"""
        if self.last_connected_supported:
            last_connected = self.data.get(
                'vehicle-details', {}).get('lastConnectionTimeStamp', {})
            if last_connected:
                last_connected = last_connected[0] + last_connected[1]
                date_patterns = ["%d.%m.%Y%H:%M", "%d-%m-%Y%H:%M"]
                for date_pattern in date_patterns:
                    try:
                        return datetime.strptime(last_connected, date_pattern).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        pass

    @property
    def last_connected_supported(self):
        """Return when vehicle was last connected to carnet"""
        check = self.data.get('vehicle-details', {}
                              ).get('lastConnectionTimeStamp', {})
        if check:
            return True

    @property
    def climatisation_target_temperature(self):
        if self.climatisation_supported:
            temperature = self.data.get('emanager', {}).get(
                'rpc', {}).get('settings', {}).get('targetTemperature', {})
            if temperature:
                return temperature

    @property
    def climatisation_target_temperature_supported(self):
        if self.climatisation_supported:
            return True

    @property
    def climatisation_without_external_power(self):
        if self.climatisation_without_external_power_supported:
            return self.data.get('emanager', {}).get('rpc', {}).get('settings', {}).get('climatisationWithoutHVPower', {})

    @property
    def climatisation_without_external_power_supported(self):
        if self.climatisation_supported:
            return True

    @property
    def service_inspection(self):
        """Return time left for service inspection"""
        if self.service_inspection_supported:
            check = self.data.get('vehicle-details', {}
                                  ).get('serviceInspectionData', {})
            if check:
                return check

    @property
    def service_inspection_supported(self):
        check = self.data.get('vehicle-details', {}
                              ).get('serviceInspectionData', {})
        if check:
            return True

    @property
    def oil_inspection(self):
        """Return time left for service inspection"""
        if self.service_inspection_supported:
            check = self.data.get('vehicle-details', {}
                                  ).get('oilInspectionData', {})
            if check:
                return check

    @property
    def oil_inspection_supported(self):
        check = self.data.get('vehicle-details', {}
                              ).get('oilInspectionData', {})
        if check:
            return True

    @property
    def battery_level(self):
        """Return battery level"""
        if self.battery_level_supported:
            return self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('batteryPercentage', {})

    @property
    def battery_level_supported(self):
        check = self.data.get('emanager', {}).get('rbc', {}).get(
            'status', {}).get('batteryPercentage', {})
        if isinstance(check, int):
            return True

    @property
    def charge_max_ampere(self):
        """Return charge max ampere"""
        if self.charge_max_ampere_supported:
            return self.data.get('emanager', {}).get('rbc', {}).get('settings', {}).get('chargerMaxCurrent', {})

    @property
    def charge_max_ampere_supported(self):
        check = self.data.get('emanager', {}).get('rbc', {}).get(
            'settings', {}).get('chargerMaxCurrent', {})
        if isinstance(check, int):
            return True

    @property
    def parking_light(self):
        """Return true if parking light is on"""
        if self.parking_light_supported:
            check = self.data.get('vsr', {}).get(
                'carRenderData', {}).get('parkingLights', {})
            if check != 2:
                return True
            else:
                return False

    @property
    def parking_light_supported(self):
        """Return true if parking light is supported"""
        check = self.data.get('vsr', {}).get(
            'carRenderData', {}).get('parkingLights', {})
        if isinstance(check, int):
            return True

    @property
    def distance(self):
        if self.distance_supported:
            value = self.data.get('vehicle-details', {}).get('distanceCovered',
                                                             None).replace('.', '').replace(',', '').replace('--', '')
            if value:
                return int(value)

    @property
    def distance_supported(self):
        """Return true if distance is supported"""
        check = self.data.get('vehicle-details', {}).get('distanceCovered', {})
        if check:
            return True

    @property
    def position(self):
        """Return  position."""
        if self.position_supported:
            return self.data.get('position')

    @property
    def position_supported(self):
        """Return true if vehichle has position."""
        check = self.data.get('position', {}).get('lng', {})
        if check:
            return True

    @property
    def model(self):
        """Return model"""
        if self.model_supported:
            return self.data.get('model')

    @property
    def model_supported(self):
        check = self.data.get('model')
        if check:
            return True

    @property
    def model_year(self):
        """Return model year"""
        if self.model_year_supported:
            return self.data.get('modelYear', {})

    @property
    def model_year_supported(self):
        check = self.data.get('modelYear', {})
        if check:
            return True

    @property
    def model_image(self):
        """Return model image"""
        if self.model_image_supported:
            return self.data.get('imageUrl', {})

    @property
    def model_image_supported(self):
        check = self.data.get('imageUrl', {})
        if check:
            return True

    @property
    def charging(self):
        """Return status of charging."""
        if self.charging_supported:
            status = self.data.get('emanager', {}).get(
                'rbc', {}).get('status', {}).get('chargingState', {})
            if status == 'CHARGING':
                return True
            else:
                return False

    @property
    def charging_supported(self):
        """Return true if vehichle has heater."""
        check = self.data.get('emanager', {}).get('rbc', {}).get(
            'status', {}).get('batteryPercentage', {})
        if check:
            return True

    @property
    def electric_range(self):
        if self.electric_range_supported:
            return self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('electricRange', {})

    @property
    def electric_range_supported(self):
        check = self.data.get('emanager', {}).get(
            'rbc', {}).get('status', {}).get('electricRange', {})
        if isinstance(check, int):
            return True

    @property
    def combustion_range(self):
        if self.combustion_range_supported:
            return self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('combustionRange', {})

    @property
    def combustion_range_supported(self):
        check = self.data.get('emanager', {}).get('rbc', {}).get(
            'status', {}).get('combustionRange', {})
        if isinstance(check, int):
            return True

    @property
    def combined_range(self):
        if self.combustion_range_supported:
            return self.data.get('emanager', {}).get('rbc', {}).get('status', {}).get('combinedRange', {})

    @property
    def combined_range_supported(self):
        check = self.data.get('emanager', {}).get(
            'rbc', {}).get('status', {}).get('combinedRange', {})
        if isinstance(check, int):
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
            check = self.data.get('emanager', {}).get(
                'rbc', {}).get('status', {}).get('pluginState', {})
            if check == 'CONNECTED':
                return True
            else:
                return False

    @property
    def external_power_supported(self):
        """External power supported."""
        if self.charging_supported:
            check = self.data.get('emanager', {}).get(
                'rbc', {}).get('status', {}).get('pluginState', {})
            if check:
                return True

    @property
    def climatisation(self):
        """Return status of climatisation."""
        if self.climatisation_supported:
            status = self.data.get('emanager', {}).get('rpc', {}).get(
                'status', {}).get('climatisationState', {})
            if status == 'HEATING':
                return True
            else:
                return False

    @property
    def climatisation_supported(self):
        """Return true if vehichle has heater."""
        check = self.data.get('emanager', {}).get(
            'rpc', {}).get('climaterActionState', {})
        if check == 'AVAILABLE' or check == 'NO_PLUGIN':
            return True

    @property
    def window_heater(self):
        """Return status of window heater."""
        if self.window_heater_supported:
            ret = False
            status_front = self.data.get('emanager', {}).get('rpc', {}).get(
                'status', {}).get('windowHeatingStateFront', {})
            if status_front == 'ON':
                ret = True

            status_rear = self.data.get('emanager', {}).get('rpc', {}).get(
                'status', {}).get('windowHeatingStateRear', {})
            if status_rear == 'ON':
                ret = True
            return ret

    @property
    def window_heater_supported(self):
        """Return true if vehichle has heater."""
        if self.climatisation_supported:
            check = self.data.get('emanager', {}).get('rpc', {}).get(
                'status', {}).get('windowHeatingAvailable', {})
            if check:
                return True

    @property
    def combustion_engine_heating(self):
        """Return status of combustion engine heating."""
        if self.combustion_engine_heating_supported:
            status_combustion_engine_heating_active = self.data.get(
                'remoteAuxiliaryHeating').get('status').get('active', False)
            return status_combustion_engine_heating_active

    @property
    def combustion_engine_heating_supported(self):
        """Return true if vehichle has combustion engine heating."""
        check = self.data.get('remoteAuxiliaryHeating', {})
        if check:
            return True

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
                hours = self.data.get('emanager', {}).get('rbc', {}).get(
                    'status', {}).get('chargingRemaningHour', {})
                minutes = self.data.get('emanager', {}).get('rbc', {}).get(
                    'status', {}).get('chargingRemaningMinute', {})
                if hours and minutes:
                    # return 0 if we are not able to convert the values we get from carnet.
                    try:
                        return (int(hours) * 60) + int(minutes)
                    except Exception:
                        return 0

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
                if lock == 'trunk':
                    continue
                if lock_data[lock] != 2:
                    vehicle_locked = False
            return vehicle_locked

    @property
    def door_locked_supported(self):
        check = self.data.get('vsr', {}).get('lockData', {})
        if len(check) > 0:
            return True

    @property
    def trunk_locked(self):
        if self.trunk_locked_supported:
            trunk_lock_data = self.data.get(
                'vsr', {}).get('lockData', {}).get('trunk')
            if trunk_lock_data != 2:
                return False
            else:
                return True

    @property
    def trunk_locked_supported(self):
        check = self.data.get('vsr', {}).get('lockData', {}).get('trunk')
        if check:
            return True

    @property
    def request_in_progress(self):
        check = self.data.get('vsr', {}).get('requestStatus', {})
        if check == 'REQUEST_IN_PROGRESS':
            return True
        else:
            return False

    @property
    def request_in_progress_supported(self):
        check = self.data.get('vsr', {}).get('requestStatus', {})
        if check:
            return True

    # states
    @property
    def is_parking_lights_on(self):
        """Parking light state"""
        if self.parking_light_supported:
            state = self.data.get('carRenderData', {}).get('parkingLights', {})
            if state != 2:
                return False
            else:
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
    def is_climatisation_on(self):
        """Return status of climatisation."""
        if self.climatisation_supported:
            status = self.data.get('emanager', {}).get('rpc', {}).get(
                'status', {}).get('climatisationState', {})
            if status == 'HEATING':
                return True
            else:
                return False

    @property
    def is_window_heater_on(self):
        """Return status of window heater."""
        if self.window_heater_supported:
            ret = False
            status_front = self.data.get('emanager', {}).get('rpc', {}).get(
                'status', {}).get('windowHeatingStateFront', {})
            if status_front == 'ON':
                ret = True

            status_rear = self.data.get('emanager', {}).get('rpc', {}).get(
                'status', {}).get('windowHeatingStateRear', {})
            if status_rear == 'ON':
                ret = True
            return ret

    @property
    def is_charging_on(self):
        """Return status of charging."""
        if self.charging_supported:
            status = self.data.get('emanager', {}).get(
                'rbc', {}).get('status', {}).get('chargingState', {})
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
            resp = self.call('-/emanager/trigger-climatisation',
                             triggerAction=True, electricClima=True)
            if not resp:
                _LOGGER.warning('Failed to start climatisation')
            else:
                return resp
        else:
            _LOGGER.error('No climatization support.')

    def stop_climatisation(self):
        """Turn on/off climatisation."""
        if self.climatisation_supported:
            resp = self.call('-/emanager/trigger-climatisation',
                             triggerAction=False, electricClima=True)
            if not resp:
                _LOGGER.warning('Failed to stop climatisation')
            else:
                return resp
        else:
            _LOGGER.error('No climatization support.')

    def start_window_heater(self):
        """Turn on/off window heater."""
        if self.window_heater_supported:
            resp = self.call(
                '-/emanager/trigger-windowheating', triggerAction=True)
            if not resp:
                _LOGGER.warning('Failed to start window heater')
            else:
                return resp
        else:
            _LOGGER.error('No climatization support.')

    def stop_window_heater(self):
        """Turn on/off window heater."""
        if self.window_heater_supported:
            resp = self.call(
                '-/emanager/trigger-windowheating', triggerAction=False)
            if not resp:
                _LOGGER.warning('Failed to stop window heater')
        else:
            _LOGGER.error('No window heating support.')

    def start_combustion_engine_heating(self, spin):
        if spin:
            if self.combustion_engine_heating_supported:
                resp = self.call('-/rah/quick-start',
                                 startMode='HEATING', spin=spin)
                if not resp:
                    _LOGGER.warning('Failed to start combustion engine heating')
                else:
                    return resp
            else:
                _LOGGER.error('No combustion engine heating support.')
        else:
            _LOGGER.error('Invalid SPIN provided')

    def stop_combustion_engine_heating(self):
        if self.combustion_engine_heating_supported:
            resp = self.call('-/rah/quick-stop')
            if not resp:
                _LOGGER.warning('Failed to stop combustion engine heating')
            else:
                return resp
        else:
            _LOGGER.error('No combustion engine heating support.')

    def start_charging(self):
        """Turn on/off window heater."""
        if self.charging_supported:
            resp = self.call('-/emanager/charge-battery',
                             triggerAction=True, batteryPercent='100')
            if not resp:
                _LOGGER.warning('Failed to start charging')
            else:
                return resp
        else:
            _LOGGER.error('No charging support.')

    def stop_charging(self):
        """Turn on/off window heater."""
        if self.charging_supported:
            resp = self.call('-/emanager/charge-battery',
                             triggerAction=False, batteryPercent='99')
            if not resp:
                _LOGGER.warning('Failed to stop charging')
            else:
                return resp
        else:
            _LOGGER.error('No charging support.')

    def set_climatisation_target_temperature(self, target_temperature):
        """Turn on/off window heater."""
        if self.climatisation_supported:
            resp = self.call('-/emanager/set-settings', chargerMaxCurrent=None,
                             climatisationWithoutHVPower=None, minChargeLimit=None, targetTemperature=target_temperature)
            if not resp:
                _LOGGER.warning(
                    'Failed to set target temperature for climatisation')
            else:
                return resp
        else:
            _LOGGER.error('No climatisation support.')

    def get_status(self, timeout=10):
        """Check status from call"""
        retry_counter = 0
        while retry_counter < timeout:
            resp = self.call('-/emanager/get-notifications', data='dummy')
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
            OrderedDict(sorted(self.data.items())), indent=4, default=serialize)
