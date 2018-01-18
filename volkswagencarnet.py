#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Communicate with Volkswagen Carnet."""

import re
import time
import logging
from sys import version_info
from requests import Session, RequestException
from datetime import timedelta, datetime
from urllib.parse import urlsplit
from urllib.error import HTTPError

version_info >= (3, 0) or exit('Python 3 required')

__version__ = '0.3.1'

_LOGGER = logging.getLogger(__name__)


TIMEOUT = timedelta(seconds=30)

class Connection(object):
    """ Connection to Volkswagen Carnet """
    def __init__(self, username, password):
        """ Initialize """
        self._session = Session()
        self._session_headers = { 'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json;charset=UTF-8', 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36' }
        self._session_base = 'https://www.volkswagen-car-net.com'
        self._session_auth_headers = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8', 'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; D5803 Build/23.5.A.1.291; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/63.0.3239.111 Mobile Safari/537.36'}
        self._session_auth_base = 'https://security.volkswagen.com'
        self._session_auth_username = username
        self._session_auth_password = password

        _LOGGER.debug('Using service <%s>', self._session_base)
        _LOGGER.debug('User: <%s>', username)
        self._state = {}

    def _connect(self):
        # Regular expressions to extract data
        re_csrf = re.compile('<meta name="_csrf" content="([^"]*)"/>')
        re_redurl = re.compile('<redirect url="([^"]*)"></redirect>')
        re_viewstate = re.compile('name="javax.faces.ViewState" id="j_id1:javax.faces.ViewState:0" value="([^"]*)"')
        re_authcode = re.compile('code=([^"]*)&')
        re_authstate = re.compile('state=([^"]*)')

        def extract_csrf(r):
            return re_csrf.search(r.text).group(1)

        def extract_redirect_url(r):
            return re_redurl.search(r.text).group(1)

        def extract_view_state(r):
            return re_viewstate.search(r.text).group(1)

        def extract_code(r):
            return re_authcode.search(r).group(1)

        def extract_state(r):
            return re_authstate.search(r).group(1)

        # Request landing page and get CSFR:
        r = login_session['session'].get(login_session['base'] + '/portal/en_GB/web/guest/home')
        if r.status_code != 200:
            return ""
        csrf = extract_csrf(r)

        # Request login page and get CSRF
        login_session['auth_headers']['Referer'] = login_session['base'] + '/portal'
        login_session['auth_headers']["X-CSRF-Token"] = csrf
        r = login_session['session'].post(
            login_session['base'] + '/portal/web/guest/home/-/csrftokenhandling/get-login-url',
            headers=login_session['auth_headers'])
        if r.status_code != 200:
            return ""
        responseData = r.json()
        lg_url = responseData.get("loginURL").get("path")

        # no redirect so we can get values we look for
        r = login_session['session'].get(lg_url, allow_redirects=False, headers=login_session['auth_headers'])
        if r.status_code != 302:
            return ""
        ref_url = r.headers.get("location")

        # now get actual login page and get session id and ViewState
        r = login_session['session'].get(ref_url, headers=login_session['auth_headers'])
        if r.status_code != 200:
            return ""
        view_state = extract_view_state(r)

        # Login with user details
        login_session['auth_headers']["Faces-Request"] = "partial/ajax"
        login_session['auth_headers']["Referer"] = ref_url
        login_session['auth_headers']["X-CSRF-Token"] = ''

        post_data = {
            'loginForm': 'loginForm',
            'loginForm:email': self.carnet_username,
            'loginForm:password': self.carnet_password,
            'loginForm:j_idt19': '',
            'javax.faces.ViewState': view_state,
            'javax.faces.source': 'loginForm:submit',
            'javax.faces.partial.event': 'click',
            'javax.faces.partial.execute': 'loginForm:submit loginForm',
            'javax.faces.partial.render': 'loginForm',
            'javax.faces.behavior.event': 'action',
            'javax.faces.partial.ajax': 'true'
        }

        r = login_session['session'].post(login_session['auth_base'] + '/ap-login/jsf/login.jsf', data=post_data,
                                          headers=login_session['auth_headers'])
        if r.status_code != 200:
            return ""
        ref_url = extract_redirect_url(r).replace('&amp;', '&')

        # redirect to link from login and extract state and code values
        r = login_session['session'].get(ref_url, allow_redirects=False, headers=login_session['auth_headers'])
        if r.status_code != 302:
            return ""
        ref_url2 = r.headers.get("location")

        code = extract_code(ref_url2)
        state = extract_state(ref_url2)

        # load ref page
        r = login_session['session'].get(ref_url2, headers=login_session['auth_headers'])
        if r.status_code != 200:
            return ""

        login_session['auth_headers']["Faces-Request"] = ""
        login_session['auth_headers']["Referer"] = ref_url2
        post_data = {
            '_33_WAR_cored5portlet_code': code,
            '_33_WAR_cored5portlet_landingPageUrl': ''
        }
        r = login_session['session'].post(login_session['base'] + urlsplit(
            ref_url2).path + '?p_auth=' + state + '&p_p_id=33_WAR_cored5portlet&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view&p_p_col_id=column-1&p_p_col_count=1&_33_WAR_cored5portlet_javax.portlet.action=getLoginStatus',
                                          data=post_data, allow_redirects=False, headers=login_session['auth_headers'])
        if r.status_code != 302:
            return ""

        ref_url3 = r.headers.get("location")
        r = login_session['session'].get(ref_url3, headers=login_session['auth_headers'])

        # We have a new CSRF
        csrf = extract_csrf(r)

        # Update headers for requests
        login_session['headers']["Referer"] = ref_url3
        login_session['headers']["X-CSRF-Token"] = csrf
        login_session['url'] = ref_url3
        _LOGGER.debug("Login session created")
        return login_session


    def get(self):
        print('get')

    def post(self):
        print('post')



def main():
    """Main method."""
    print('test')

if __name__ == '__main__':
    main()
