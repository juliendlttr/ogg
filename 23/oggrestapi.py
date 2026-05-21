#!/usr/bin/env python3
"""
Oracle GoldenGate REST API Client
Author: Julien DELATTRE
"""

import requests
import urllib3
from pprint import pprint


class OGGRestAPI:
    def __init__(self, url, username=None, password=None, deployment=None, ca_cert=None,
                 reverse_proxy=False, verify_ssl=True, test_connection=True, timeout=None):
        """
        Initialize Oracle GoldenGate REST API client.

        :param url: Base URL of the OGG REST API. It can be:
                    'http(s)://hostname:port' without NGINX reverse proxy,
                    'https://nginx_host:nginx_port' with NGINX reverse proxy.
        :param username: service username
        :param password: service password
        :param deployment: when reverse proxy is used, the deployment name to use (e.g. 'ogg_test_01')
        :param ca_cert: path to a trusted CA cert (for self-signed certs)
        :param reverse_proxy: bool, whether to use NGINX reverse proxy
        :param verify_ssl: bool, whether to verify SSL certs
        :param test_connection: if True, will attempt to retrieve API versions on init
        :param timeout: request timeout in seconds
        """
        self.swagger_version = '2025.01.20'
        self.base_url = url
        self.username = username
        self.auth = (self.username, password)
        self.headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        self.deployment = deployment
        self.reverse_proxy = reverse_proxy
        self.verify_ssl = ca_cert if ca_cert else verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update(self.headers)

        if not verify_ssl and self.base_url.startswith('https://'):
            # Disable InsecureRequestWarning if verification is off
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Test connection
        if test_connection:
            try:
                self.list_api_versions()
                print(f'Connected to OGG REST API at {self.base_url}')
            except Exception as e:
                print(f'Error connecting to OGG REST API: {e}')
                raise

    def _request(self, method, path, *, params=None, data=None, raw_response=False):
        url = f'{self.base_url}{path}'
        response = self.session.request(
            method,
            url,
            auth=self.auth,
            headers=self.headers,
            params=params,
            json=data,
            verify=self.verify_ssl,
            timeout=self.timeout
        )

        if raw_response:
            return response
        else:
            result = self._parse(response)
            self._check_response(response, url)
            return self._extract_main(result)

    def _build_path(self, template, ogg_service=None, path_params=None):
        path_params = path_params or {}
        # If reverse proxy is enabled, the full service must be added before /v2/
        #   - /services/ServiceManager/v2/... for Service Manager
        #   - /services/deployment_name/ogg_service/v2/... for other services when a deployment is specified
        if self.reverse_proxy and template != '/services':
            if ogg_service == 'ServiceManager' or not self.deployment:
                template = f'/services/ServiceManager/{template.lstrip("/services")}'
            else:
                template = f'/services/{self.deployment}/{ogg_service}/{template.lstrip("/services")}'
        return template.format(**path_params)

    def _call(self, method, template, *, ogg_service=None, path_params=None, params=None,
              data=None, body_params=None, raw_response=False, if_exists='fail'):
        if self.reverse_proxy and ogg_service == '' and self.deployment:
            # This is a common endpoint and a deployment is specified. Choosing adminsrvr service by default.
            ogg_service = "adminsrvr"
        path = self._build_path(template, ogg_service=ogg_service, path_params=path_params)
        url = f'{self.base_url}{path}'

        # Merge body_params into data when provided. body_params is a dict mapping
        # payload field names to values (the generated methods pass their
        # explicit body params here). Only merge when `data` is a dict or None.
        if body_params:
            if data is None:
                data = {}
            if isinstance(data, dict):
                for k, v in body_params.items():
                    if v is not None:
                        data[k] = v
            if not data:
                data = None

        # If caller asked to skip on existing resource, perform a raw request and handle 409 specially
        if if_exists == 'skip':
            response = self.session.request(
                method,
                url,
                auth=self.auth,
                headers=self.headers,
                params=params,
                json=data,
                verify=self.verify_ssl,
                timeout=self.timeout
            )

            try:
                parsed = response.json()
            except ValueError:
                parsed = response.text

            if response.status_code == 409:
                titles = []
                try:
                    msgs = parsed.get('messages', []) if isinstance(parsed, dict) else []
                    for m in msgs:
                        if isinstance(m, dict) and 'title' in m:
                            titles.append(m['title'])
                except Exception:
                    pass
                message = '; '.join(titles) if titles else 'Resource exists'
                print(f"{message} (if_exists set to skip)")
                return {'status': 'skipped', 'message': message, 'http_status': 409, 'raw': parsed}

            # Otherwise behave like normal _call: raise on errors, return parsed or extracted
            self._check_response(response, url)
            if raw_response:
                return parsed
            return self._extract_main(parsed)

        # Default behavior: use existing request flow
        result = self._request(method, path, params=params, data=data, raw_response=raw_response)
        return result

    def _get(self, path, params=None, raw_response=False):
        return self._request('GET', path, params=params, raw_response=raw_response)

    def _post(self, path, data=None, raw_response=False):
        return self._request('POST', path, data=data, raw_response=raw_response)

    def _put(self, path, data=None, raw_response=False):
        return self._request('PUT', path, data=data, raw_response=raw_response)

    def _patch(self, path, data=None, raw_response=False):
        return self._request('PATCH', path, data=data, raw_response=raw_response)

    def _delete(self, path, raw_response=False):
        return self._request('DELETE', path, raw_response=raw_response)

    def _check_response(self, response, url):
        if not response.ok:
            try:
                if 'messages' in response.json():
                    messages = response.json().get('messages', [])
                    raise Exception(
                        ' ; '.join([f"{message['severity']} (code {response.status_code}) - {url}: {message['title']}" for message in messages])
                    )
                else:
                    print(f'HTTP {response.status_code}: {response.text}')
                    response.raise_for_status()
            except ValueError:
                print(f'HTTP {response.status_code}: {response.text}')
                response.raise_for_status()

    def _parse(self, response):
        try:
            return response.json()
        except ValueError:
            return response.text

    def close(self):
        self.session.close()

    def _extract_main(self, result):
        if not isinstance(result, dict):
            return result

        resp = result.get('response', result)
        if 'items' not in resp:
            return resp

        exclude = {'links', '$schema'}
        return [{k: v for k, v in i.items() if k not in exclude} for i in resp['items']]

    def pretty_print(self, result):
        pprint(result)

    # Endpoint: /services
    def list_api_versions(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/REST API Catalog
        GET /services
        Required Role: Any
        Each Oracle GoldenGate service exposes one or more versions of the REST API for backward compatibility.
            Retrieve the collection of available API versions using this endpoint.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_api_versions(
                ogg_service='adminsrvr'
            )
        """
        return self._call("GET", "/services", raw_response=raw_response)

    # Endpoint: /services/{version}
    def get_api_version(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/REST API Catalog
        GET /services/{version}
        Required Role: Any
        Use this endpoint to obtain details of a specific version of an Oracle GoldenGate Service REST API.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_api_version(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorization
    def exchange_auth_code_for_token(
        self,
        version='v2',
        raw_response=False
    ):
        """
        OAuth redirect URL
        GET /services/{version}/authorization
        Required Role: Any
        Receives the authorization code and exchanges it for an access and id token

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.exchange_auth_code_for_token()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/authorization",
            path_params=path_params,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations
    def list_roles(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        GET /services/{version}/authorizations
        Required Role: Security
        Get the collection of roles in this deployment.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_roles(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/authorizations",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}
    def list_users(
        self,
        role,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        GET /services/{version}/authorizations/{role}
        Required Role: Security
        Get the collection of Authorized Users associated with the Authorization Role.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_users(
                role='User',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "role": role,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/authorizations/{role}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}
    def bulk_create_users(
        self,
        role,
        users=None,
        data=None,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        POST /services/{version}/authorizations/{role}
        Required Role: Security
        Create multiple users associated with the given role.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            users (list): Required if not included in `data`. Example: users_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.bulk_create_users(
                role='User',
                ogg_service='adminsrvr',
                data={
                    "users": [
                        {
                            "type": "Basic",
                            "user": "ggmsa",
                            "credential": "password-A1"
                        },
                        {
                            "type": "Basic",
                            "user": "ggadmin",
                            "credential": "password-A2"
                        }
                    ]
                }
            )

            client.bulk_create_users(
                role='User',
                ogg_service='adminsrvr',
                users=[
                    {
                        "type": "Basic",
                        "user": "ggmsa",
                        "credential": "password-A1"
                    },
                    {
                        "type": "Basic",
                        "user": "ggadmin",
                        "credential": "password-A2"
                    }
                ]
            )
        """
        path_params = {
            "role": role,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/authorizations/{role}",
            path_params=path_params,
            data=data,
            body_params={
                "users": users,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def get_user(
        self,
        role,
        user,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        GET /services/{version}/authorizations/{role}/{user}
        Required Role: User
        Get Authorization User Resource information.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            user (str): User Resource Name. Required. Example: user_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_user(
                role='User',
                user='user_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "role": role,
            "user": user,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/authorizations/{role}/{user}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def create_user(
        self,
        role,
        user,
        data=None,
        version='v2',
        ogg_service='',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Common/User Management
        POST /services/{version}/authorizations/{role}/{user}
        Required Role: Security
        Create a new Authorization User Resource.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            user (str): User Resource Name. Required. Example: user_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_user(
                role='User',
                user='user_example',
                ogg_service='adminsrvr',
                data={
                    "credential": "password-A1",
                    "info": "Credential Information"
                }
            )
        """
        path_params = {
            "role": role,
            "user": user,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/authorizations/{role}/{user}",
            path_params=path_params,
            data=data,
            ogg_service=ogg_service,
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def update_user(
        self,
        role,
        user,
        data=None,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        PATCH /services/{version}/authorizations/{role}/{user}
        Required Role: User
        Update an existing Authorization User Resource.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            user (str): User Resource Name. Required. Example: user_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_user(
                role='User',
                user='user_example',
                ogg_service='adminsrvr',
                data={
                    "credential": "NewPassword-A1"
                }
            )
        """
        path_params = {
            "role": role,
            "user": user,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/authorizations/{role}/{user}",
            path_params=path_params,
            data=data,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def delete_user(
        self,
        role,
        user,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        DELETE /services/{version}/authorizations/{role}/{user}
        Required Role: Security
        Delete an existing Authorization user role. To completely remove a user from the deployment, use a value
            of "all" for {role}.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            user (str): User Resource Name. Required. Example: user_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_user(
                role='User',
                user='user_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "role": role,
            "user": user,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/authorizations/{role}/{user}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}/info
    def get_user_info(
        self,
        role,
        user,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        GET /services/{version}/authorizations/{role}/{user}/info
        Required Role: Security
        Retrieve any additional information for the deployment user.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            user (str): User Resource Name. Required. Example: user_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_user_info(
                role='User',
                user='user_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "role": role,
            "user": user,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/authorizations/{role}/{user}/info",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/certificates
    def list_certificate_types(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Certificates
        GET /services/{version}/certificates
        Required Role: Administrator
        Retrieve the collection of certificate types.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_certificate_types(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/certificates",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/certificates/{type}
    def list_certificates(
        self,
        type,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Certificates
        GET /services/{version}/certificates/{type}
        Required Role: Administrator
        Retrieve the certificate type names.

        Parameters:
            type (str): Required. Example: type_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_certificates(
                type='type_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "type": type,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/certificates/{type}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/certificates/{type}/{certificate}
    def get_certificate(
        self,
        type,
        certificate,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Certificates
        GET /services/{version}/certificates/{type}/{certificate}
        Required Role: Administrator
        Retrieve the certificate information for the named certificate.

        Parameters:
            type (str): Required. Example: type_example
            certificate (str): Certificate name. Required. Example: certificate_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_certificate(
                type='type_example',
                certificate='certificate_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "type": type,
            "certificate": certificate,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/certificates/{type}/{certificate}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/certificates/{type}/{certificate}/info
    def get_certificate_info(
        self,
        type,
        certificate,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Certificates
        GET /services/{version}/certificates/{type}/{certificate}/info
        Required Role: Administrator
        Retrieve the certificate information for the named certificate in the deployment.

        Parameters:
            type (str): Required. Example: type_example
            certificate (str): Certificate name. Required. Example: certificate_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_certificate_info(
                type='type_example',
                certificate='certificate_example'
            )
        """
        path_params = {
            "type": type,
            "certificate": certificate,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/certificates/{type}/{certificate}/info",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/commands/execute
    def execute_command(
        self,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Commands
        POST /services/{version}/commands/execute
        Required Role: User
        Execute a command. Reporting commands are accessible for users with the 'User' role. Other commands
            require the 'Operator' role.

        Parameters:
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.execute_command(
                data={
                    "name": "report",
                    "reportType": "lag",
                    "thresholds": [
                        {
                            "type": "info",
                            "units": "seconds",
                            "value": "0"
                        },
                        {
                            "type": "critical",
                            "units": "seconds",
                            "value": "5"
                        }
                    ]
                }
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/commands/execute",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files
    def list_configuration_files(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        GET /services/{version}/config/files
        Required Role: User
        Retrieve the collection of configuration files.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_configuration_files(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/config/files",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def get_configuration_file(
        self,
        file,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        GET /services/{version}/config/files/{file}
        Required Role: User
        Retrieve the contents of a configuration file.

        Parameters:
            file (str): The name of a configuration file. Required. Example: file_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_configuration_file(
                file='file_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "file": file,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/config/files/{file}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def create_configuration_file(
        self,
        file,
        lines=None,
        data=None,
        version='v2',
        ogg_service='',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Common/Configuration Settings
        POST /services/{version}/config/files/{file}
        Required Role: Administrator
        Create a new configuration file.

        Parameters:
            file (str): The name of a configuration file. Required. Example: file_example
            lines (list): Required if not included in `data`. Example: lines_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_configuration_file(
                file='file_example',
                ogg_service='adminsrvr',
                data={
                    "lines": [
                        "UseridAlias oggadmin",
                        "ReportCount Every 1000 Records"
                    ]
                }
            )

            client.create_configuration_file(
                file='file_example',
                ogg_service='adminsrvr',
                lines=[
                    "UseridAlias oggadmin",
                    "ReportCount Every 1000 Records"
                ]
            )
        """
        path_params = {
            "file": file,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/config/files/{file}",
            path_params=path_params,
            data=data,
            body_params={
                "lines": lines,
            },
            ogg_service=ogg_service,
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def delete_configuration_file(
        self,
        file,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        DELETE /services/{version}/config/files/{file}
        Required Role: Administrator
        Delete a configuration file.

        Parameters:
            file (str): The name of a configuration file. Required. Example: file_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_configuration_file(
                file='file_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "file": file,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/config/files/{file}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def update_configuration_file(
        self,
        file,
        lines=None,
        data=None,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        PUT /services/{version}/config/files/{file}
        Required Role: Administrator
        Modify an existing configuration file.

        Parameters:
            file (str): The name of a configuration file. Required. Example: file_example
            lines (list): Required if not included in `data`. Example: lines_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_configuration_file(
                file='file_example',
                ogg_service='adminsrvr',
                data={
                    "lines": [
                        "UseridAlias oggadmin",
                        "ReportCount Every 100000 Records"
                    ]
                }
            )

            client.update_configuration_file(
                file='file_example',
                ogg_service='adminsrvr',
                lines=[
                    "UseridAlias oggadmin",
                    "ReportCount Every 100000 Records"
                ]
            )
        """
        path_params = {
            "file": file,
            "version": version,
        }
        return self._call(
            "PUT",
            "/services/{version}/config/files/{file}",
            path_params=path_params,
            data=data,
            body_params={
                "lines": lines,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/health
    def get_service_health(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration
        GET /services/{version}/config/health
        Required Role: User
        Retrieve detailed information for the service health.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_service_health(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/config/health",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/health/check
    def get_service_health_check(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration
        GET /services/{version}/config/health/check
        Required Role: Any
        Retrieve summary information for the service health.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_service_health_check(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/config/health/check",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/summary
    def get_config_summary(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration
        GET /services/{version}/config/summary
        Required Role: User
        Retrieve summary information for the service.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_config_summary(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/config/summary",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types
    def list_config_types(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        GET /services/{version}/config/types
        Required Role: User
        Retrieve the collection of configuration variable data types.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_config_types(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/config/types",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def get_config_type(
        self,
        type,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        GET /services/{version}/config/types/{type}
        Required Role: User
        Retrieve a configuration data type.

        Parameters:
            type (str): Required. Example: type_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_config_type(
                type='type_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "type": type,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/config/types/{type}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def create_config_type(
        self,
        type,
        data=None,
        version='v2',
        ogg_service='',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Common/Configuration Settings
        POST /services/{version}/config/types/{type}
        Required Role: Administrator
        Create a new configuration data type.

        Parameters:
            type (str): Required. Example: type_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_config_type(
                type='type_example',
                ogg_service='adminsrvr',
                data={
                    "id": "custom:config",
                    "title": "Custom Configuration Data",
                    "$schema": "http://json-schema.org/draft-04/schema#",
                    "type": "object",
                    "properties": {
                        "$schema": {
                            "enum": [
                                "custom:config"
                            ]
                        },
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": "0",
                                "maxLength": "4095"
                            },
                            "minItems": "0",
                            "maxItems": "32767"
                        }
                    },
                    "required": [
                        "lines"
                    ],
                    "additionalProperties": False
                }
            )
        """
        path_params = {
            "type": type,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/config/types/{type}",
            path_params=path_params,
            data=data,
            ogg_service=ogg_service,
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def delete_config_type(
        self,
        type,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        DELETE /services/{version}/config/types/{type}
        Required Role: Administrator
        Delete a configuration data type.

        Parameters:
            type (str): Required. Example: type_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_config_type(
                type='type_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "type": type,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/config/types/{type}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values
    def list_config_values(
        self,
        type,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        GET /services/{version}/config/types/{type}/values
        Required Role: User
        Retrieve the collection of names of the configuration values for a data type.

        Parameters:
            type (str): Required. Example: type_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_config_values(
                type='type_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "type": type,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/config/types/{type}/values",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def get_config_value(
        self,
        type,
        value,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        GET /services/{version}/config/types/{type}/values/{value}
        Required Role: User
        Retrieve a configuration value.

        Parameters:
            type (str): Required. Example: type_example
            value (str): Value name, an alpha-numeric character followed by up to 95 alpha-numeric
                characters, '_', ':' or '-'. Required. Example: value_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_config_value(
                type='type_example',
                value='value_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "type": type,
            "value": value,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def create_config_value(
        self,
        type,
        value,
        data=None,
        version='v2',
        ogg_service='',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Common/Configuration Settings
        POST /services/{version}/config/types/{type}/values/{value}
        Required Role: Administrator
        Create a new configuration value.

        Parameters:
            type (str): Required. Example: type_example
            value (str): Value name, an alpha-numeric character followed by up to 95 alpha-numeric
                characters, '_', ':' or '-'. Required. Example: value_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_config_value(
                type='type_example',
                value='value_example',
                ogg_service='adminsrvr',
                data={
                    "$schema": "custom:config",
                    "lines": [
                        "--",
                        "--  Example Configuration Data",
                        "--"
                    ]
                }
            )
        """
        path_params = {
            "type": type,
            "value": value,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params=path_params,
            data=data,
            ogg_service=ogg_service,
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def delete_config_value(
        self,
        type,
        value,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        DELETE /services/{version}/config/types/{type}/values/{value}
        Required Role: Administrator
        Delete a configuration value.

        Parameters:
            type (str): Required. Example: type_example
            value (str): Value name, an alpha-numeric character followed by up to 95 alpha-numeric
                characters, '_', ':' or '-'. Required. Example: value_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_config_value(
                type='type_example',
                value='value_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "type": type,
            "value": value,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def update_config_value(
        self,
        type,
        value,
        data=None,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        PUT /services/{version}/config/types/{type}/values/{value}
        Required Role: Administrator
        Replace an existing configuration value.

        Parameters:
            type (str): Required. Example: type_example
            value (str): Value name, an alpha-numeric character followed by up to 95 alpha-numeric
                characters, '_', ':' or '-'. Required. Example: value_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_config_value(
                type='type_example',
                value='value_example',
                ogg_service='adminsrvr',
                data={
                    "$schema": "custom:config",
                    "lines": [
                        "--",
                        "--  Example Configuration Data",
                        "--",
                        "Include core.inc"
                    ]
                }
            )
        """
        path_params = {
            "type": type,
            "value": value,
            "version": version,
        }
        return self._call(
            "PUT",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params=path_params,
            data=data,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections
    def list_connections(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections
        Required Role: User
        Retrieve the list of known database connections. For each item in the credential store, a database
            connection of the form 'domain.alias' is created.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_connections()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def get_connection(
        self,
        connection,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}
        Required Role: User
        Retrieve the database connection details.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_connection(
                connection='MYCONN'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def create_connection(
        self,
        connection,
        credentials=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}
        Required Role: Administrator
        Create a new database connection. Connections are automatically created for aliases in the credential
            store.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            credentials (dict): Credentials for database. Required if not included in `data`. Example:
                credentials_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_connection(
                connection='MYCONN',
                data={
                    "credentials": {
                        "domain": "OracleGoldenGate",
                        "alias": "ggnorth"
                    }
                }
            )

            client.create_connection(
                connection='MYCONN',
                credentials={
                    "domain": "OracleGoldenGate",
                    "alias": "ggnorth"
                }
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}",
            path_params=path_params,
            data=data,
            body_params={
                "credentials": credentials,
            },
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def delete_connection(
        self,
        connection,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        DELETE /services/{version}/connections/{connection}
        Required Role: Administrator
        Remove a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_connection(
                connection='MYCONN'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/connections/{connection}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def update_connection(
        self,
        connection,
        credentials=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        PUT /services/{version}/connections/{connection}
        Required Role: Administrator
        Update a database connection. Connections created for aliases in the credential store cannot be updated.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            credentials (dict): Credentials for database. Required if not included in `data`. Example:
                credentials_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_connection(
                connection='MYCONN',
                data={
                    "credentials": {
                        "alias": "ggnorth"
                    }
                }
            )

            client.update_connection(
                connection='MYCONN',
                credentials={
                    "alias": "ggnorth"
                }
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "PUT",
            "/services/{version}/connections/{connection}",
            path_params=path_params,
            data=data,
            body_params={
                "credentials": credentials,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/activeTransactions
    def get_active_transactions(
        self,
        connection,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/activeTransactions
        Required Role: User
        Retrieve details of the active transactions for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_active_transactions(
                connection='MYCONN'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/activeTransactions",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases
    def list_database_names(
        self,
        connection,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases
        Required Role: User
        Retrieve names of databases.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_database_names(
                connection='MYCONN'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/databases",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}
    def list_database_schemas(
        self,
        connection,
        database,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases/{database}
        Required Role: User
        Retrieve names of schemas in the database.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            database (str): Database name. Required. Example: database_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_database_schemas(
                connection='MYCONN',
                database='database_example'
            )
        """
        path_params = {
            "connection": connection,
            "database": database,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/databases/{database}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}/{schema}
    def list_database_tables(
        self,
        connection,
        database,
        schema,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases/{database}/{schema}
        Required Role: User
        Retrieve names of tables in the schema.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            database (str): Database name. Required. Example: database_example
            schema (str): Schema name in the database. Required. Example: schema_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_database_tables(
                connection='MYCONN',
                database='database_example',
                schema='schema_example'
            )
        """
        path_params = {
            "connection": connection,
            "database": database,
            "schema": schema,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/databases/{database}/{schema}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}
    def get_database_table(
        self,
        connection,
        database,
        schema,
        table,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}
        Required Role: User
        Retrieve details for a table in the schema.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            database (str): Database name. Required. Example: database_example
            schema (str): Schema name in the database. Required. Example: schema_example
            table (str): Table name in the database. Required. Example: table_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_database_table(
                connection='MYCONN',
                database='database_example',
                schema='schema_example',
                table='table_example'
            )
        """
        path_params = {
            "connection": connection,
            "database": database,
            "schema": schema,
            "table": table,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/databases/{database}/{schema}/{table}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}/instantiationCsn
    def update_instantiation_csn(
        self,
        connection,
        database,
        schema,
        table,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}/instantiationCsn
        Required Role: Administrator
        Manage the instantiation CSN for filtering.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            database (str): Database name. Required. Example: database_example
            schema (str): Schema name in the database. Required. Example: schema_example
            table (str): Table name in the database. Required. Example: table_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_instantiation_csn(
                connection='MYCONN',
                database='database_example',
                schema='schema_example',
                table='table_example',
                data={
                    "command": "set",
                    "csn": "32036323",
                    "source": "DBNORTH_PDB1"
                }
            )
        """
        path_params = {
            "connection": connection,
            "database": database,
            "schema": schema,
            "table": table,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/databases/{database}/{schema}/{table}/instantiationCsn",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/checkpoint
    def update_checkpoint_table(
        self,
        connection,
        operation=None,
        name=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/tables/checkpoint
        Required Role: Administrator
        Manage Oracle GoldenGate Checkpoint table

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            operation (str): Required if not included in `data`. Example: operation_example
            name (str): Required if not included in `data`. Example: name_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.update_checkpoint_table(
                connection='MYCONN',
                data={
                    "operation": "add",
                    "name": "ggadmin.ggs_checkpoint"
                }
            )

            client.update_checkpoint_table(
                connection='MYCONN',
                operation='add',
                name='ggadmin.ggs_checkpoint'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/tables/checkpoint",
            path_params=path_params,
            data=data,
            body_params={
                "operation": operation,
                "name": name,
            },
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def get_heartbeat_table(
        self,
        connection,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/tables/heartbeat
        Required Role: User
        Retrieve details of the heartbeat table for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_heartbeat_table(
                connection='MYCONN'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/tables/heartbeat",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def create_heartbeat_table(
        self,
        connection,
        upgrade=None,
        trackingExtractRestart=None,
        purgeFrequency=None,
        retentionTime=None,
        dbUniqueName=None,
        partitioned=None,
        targetOnly=None,
        frequency=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/tables/heartbeat
        Required Role: Administrator
        Create the heartbeat table for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            upgrade (bool): Boolean value to detect when to upgrade the heartbeat tables. Example:
                upgrade_example
            trackingExtractRestart (bool): Whether current heartbeat table setup is tracking extract restart
                position or not. Example: trackingExtractRestart_example
            purgeFrequency (int): Interval, in days, at which the heartbeat history table is purged.
                Example: purgeFrequency_example
            retentionTime (int): Heartbeats older than this retention time (in days) will be deleted from
                the heartbeat table. Example: retentionTime_example
            dbUniqueName (bool): Whether current heartbeat table setup has db_unique_name column or not.
                Example: dbUniqueName_example
            partitioned (bool): Whether the heartbeat history table is partitioned or not. Example:
                partitioned_example
            targetOnly (bool): Boolean value to enable or disable supplemental logging and the scheduler job
                for updating heartbeat seed and heartbeat tables. Example: targetOnly_example
            frequency (int): Interval, in seconds, at which the heartbeat table is updated. Example:
                frequency_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_heartbeat_table(
                connection='MYCONN',
                data={
                    "frequency": "30"
                }
            )

            client.create_heartbeat_table(
                connection='MYCONN',
                upgrade=None,
                trackingExtractRestart=None,
                purgeFrequency=None,
                retentionTime=None,
                dbUniqueName=None,
                partitioned=None,
                targetOnly=None,
                frequency='30'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/tables/heartbeat",
            path_params=path_params,
            data=data,
            body_params={
                "upgrade": upgrade,
                "trackingExtractRestart": trackingExtractRestart,
                "purgeFrequency": purgeFrequency,
                "retentionTime": retentionTime,
                "dbUniqueName": dbUniqueName,
                "partitioned": partitioned,
                "targetOnly": targetOnly,
                "frequency": frequency,
            },
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def update_heartbeat_table(
        self,
        connection,
        upgrade=None,
        trackingExtractRestart=None,
        purgeFrequency=None,
        retentionTime=None,
        dbUniqueName=None,
        partitioned=None,
        targetOnly=None,
        frequency=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        PATCH /services/{version}/connections/{connection}/tables/heartbeat
        Required Role: Administrator
        Modify the heartbeat table parameters for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            upgrade (bool): Boolean value to detect when to upgrade the heartbeat tables. Example:
                upgrade_example
            trackingExtractRestart (bool): Whether current heartbeat table setup is tracking extract restart
                position or not. Example: trackingExtractRestart_example
            purgeFrequency (int): Interval, in days, at which the heartbeat history table is purged.
                Example: purgeFrequency_example
            retentionTime (int): Heartbeats older than this retention time (in days) will be deleted from
                the heartbeat table. Example: retentionTime_example
            dbUniqueName (bool): Whether current heartbeat table setup has db_unique_name column or not.
                Example: dbUniqueName_example
            partitioned (bool): Whether the heartbeat history table is partitioned or not. Example:
                partitioned_example
            targetOnly (bool): Boolean value to enable or disable supplemental logging and the scheduler job
                for updating heartbeat seed and heartbeat tables. Example: targetOnly_example
            frequency (int): Interval, in seconds, at which the heartbeat table is updated. Example:
                frequency_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_heartbeat_table(
                connection='MYCONN',
                data={
                    "purgeFrequency": "7"
                }
            )

            client.update_heartbeat_table(
                connection='MYCONN',
                upgrade=None,
                trackingExtractRestart=None,
                purgeFrequency='7',
                retentionTime=None,
                dbUniqueName=None,
                partitioned=None,
                targetOnly=None,
                frequency=None
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/connections/{connection}/tables/heartbeat",
            path_params=path_params,
            data=data,
            body_params={
                "upgrade": upgrade,
                "trackingExtractRestart": trackingExtractRestart,
                "purgeFrequency": purgeFrequency,
                "retentionTime": retentionTime,
                "dbUniqueName": dbUniqueName,
                "partitioned": partitioned,
                "targetOnly": targetOnly,
                "frequency": frequency,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def delete_heartbeat_table(
        self,
        connection,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        DELETE /services/{version}/connections/{connection}/tables/heartbeat
        Required Role: Administrator
        Remove heartbeat resources from a database.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_heartbeat_table(
                connection='MYCONN'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/connections/{connection}/tables/heartbeat",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat/{process}
    def get_process_heartbeat_records(
        self,
        connection,
        process,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Required Role: User
        Retrieve heartbeat table entries for an extract or replicat group.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            process (str): The name of the extract or replicat process. Required. Example: process_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_heartbeat_records(
                connection='MYCONN',
                process='process_example'
            )
        """
        path_params = {
            "connection": connection,
            "process": process,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/tables/heartbeat/{process}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat/{process}
    def delete_process_heartbeat_records(
        self,
        connection,
        process,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        DELETE /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Required Role: Administrator
        Delete heartbeat table entries for an extract or replicat group.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            process (str): The name of the extract or replicat process. Required. Example: process_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_process_heartbeat_records(
                connection='MYCONN',
                process='process_example'
            )
        """
        path_params = {
            "connection": connection,
            "process": process,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/connections/{connection}/tables/heartbeat/{process}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeatData
    def get_heartbeat_data(
        self,
        connection,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/tables/heartbeatData
        Required Role: User
        Retrieve heartbeat/lag entries from a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_heartbeat_data(
                connection='MYCONN'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/tables/heartbeatData",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/procedure
    def update_procedural_supplemental_logging(
        self,
        connection,
        operation=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/trandata/procedure
        Required Role: Administrator
        Manage Supplemental Logging for Database Procedures

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            operation (str): Required if not included in `data`. Example: operation_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_procedural_supplemental_logging(
                connection='MYCONN',
                data={
                    "operation": "info"
                }
            )

            client.update_procedural_supplemental_logging(
                connection='MYCONN',
                operation='info'
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/trandata/procedure",
            path_params=path_params,
            data=data,
            body_params={
                "operation": operation,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/schema
    def update_schema_supplemental_logging(
        self,
        connection,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/trandata/schema
        Required Role: Administrator
        Manage Supplemental Logging for Database Schemas

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_schema_supplemental_logging(
                connection='MYCONN',
                data={
                    "operation": "info",
                    "schemaName": "DBNORTH_PDB1.hr"
                }
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/trandata/schema",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/table
    def update_table_supplemental_logging(
        self,
        connection,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/trandata/table
        Required Role: Administrator
        Manage Supplemental Logging for Database Tables

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_table_supplemental_logging(
                connection='MYCONN',
                data={
                    "$schema": "ogg:trandataTable",
                    "operation": "add",
                    "tableName": "hr.employees"
                }
            )
        """
        path_params = {
            "connection": connection,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/trandata/table",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/content
    def get_content(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Content Requests
        GET /services/{version}/content
        Required Role: Any
        Top level file list.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_content(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/content",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials
    def list_domains(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Credentials
        GET /services/{version}/credentials
        Required Role: User
        Retrieve the list of domains in the credential store.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_domains()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/credentials",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}
    def list_credentials(
        self,
        domain,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Credentials
        GET /services/{version}/credentials/{domain}
        Required Role: User
        Retrieve the list of aliases for a domain in the credential store.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_credentials(
                domain='OracleGoldenGate'
            )
        """
        path_params = {
            "domain": domain,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/credentials/{domain}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def get_alias(
        self,
        domain,
        alias,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Credentials
        GET /services/{version}/credentials/{domain}/{alias}
        Required Role: User
        Retrieve the available information for an alias in a credential store domain. The password for an alias
            will not be returned.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_alias(
                domain='OracleGoldenGate',
                alias='ggnorth'
            )
        """
        path_params = {
            "domain": domain,
            "alias": alias,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def create_alias(
        self,
        domain,
        alias,
        userid=None,
        password=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Credentials
        POST /services/{version}/credentials/{domain}/{alias}
        Required Role: Administrator
        Create a new alias in the credential store.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            userid (str):  Example: userid_example
            password (str):  Example: password_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_alias(
                domain='OracleGoldenGate',
                alias='ggnorth',
                data={
                    "userid": "c##ggadmin@//server1.dc1.north.example.com:1521/ORCLCDB",
                    "password": "password-DB_A1"
                }
            )

            client.create_alias(
                domain='OracleGoldenGate',
                alias='ggnorth',
                userid='c##ggadmin@//server1.dc1.north.example.com:1521/ORCLCDB',
                password='password-DB_A1'
            )
        """
        path_params = {
            "domain": domain,
            "alias": alias,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params=path_params,
            data=data,
            body_params={
                "userid": userid,
                "password": password,
            },
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def delete_alias(
        self,
        domain,
        alias,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Credentials
        DELETE /services/{version}/credentials/{domain}/{alias}
        Required Role: Administrator
        Delete an alias from the credential store.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_alias(
                domain='OracleGoldenGate',
                alias='ggnorth'
            )
        """
        path_params = {
            "domain": domain,
            "alias": alias,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def update_alias(
        self,
        domain,
        alias,
        userid=None,
        password=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Credentials
        PUT /services/{version}/credentials/{domain}/{alias}
        Required Role: Administrator
        Update an alias in the credential store.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            userid (str):  Example: userid_example
            password (str):  Example: password_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_alias(
                domain='OracleGoldenGate',
                alias='ggnorth',
                data={
                    "userid": "ggadmin@//server1.dc1.west.example.com:1521/dbwest_pdb1",
                    "password": "password-DB_A1"
                }
            )

            client.update_alias(
                domain='OracleGoldenGate',
                alias='ggnorth',
                userid='ggadmin@//server1.dc1.west.example.com:1521/dbwest_pdb1',
                password='password-DB_A1'
            )
        """
        path_params = {
            "domain": domain,
            "alias": alias,
            "version": version,
        }
        return self._call(
            "PUT",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params=path_params,
            data=data,
            body_params={
                "userid": userid,
                "password": password,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}/valid
    def is_credential_valid(
        self,
        domain,
        alias,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Credentials
        GET /services/{version}/credentials/{domain}/{alias}/valid
        Required Role: User
        Check validity of credentials and return database credentials details.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.is_credential_valid(
                domain='OracleGoldenGate',
                alias='ggnorth'
            )
        """
        path_params = {
            "domain": domain,
            "alias": alias,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/credentials/{domain}/{alias}/valid",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/currentuser
    def get_current_user(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Information
        GET /services/{version}/currentuser
        Required Role: User
        Return the current user's identity information encoded in the request.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_current_user(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/currentuser",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/currentuser
    def delete_current_user(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Information
        DELETE /services/{version}/currentuser
        Required Role: User
        Remove the current user's identity information encoded in the request.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_current_user(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/currentuser",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/currentuser/reauthorize
    def reauthorize_current_user(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Reauthorize current user
        POST /services/{version}/currentuser/reauthorize
        Required Role: User
        Use this endpoint to reauthorize the current user

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.reauthorize_current_user()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/currentuser/reauthorize",
            path_params=path_params,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/dataTargetTypes
    def list_data_target_types(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service/Data Target
        GET /services/{version}/dataTargetTypes
        Required Role: User
        Retrieve supported data target types from the Distribution Service

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_data_target_types()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/dataTargetTypes",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/dataTargetTypes/{dataTargetType}
    def get_data_target_type(
        self,
        dataTargetType,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service/Data Target
        GET /services/{version}/dataTargetTypes/{dataTargetType}
        Required Role: User
        Retrieve the json schema of a supported data target.

        Parameters:
            dataTargetType (str): The name of a supported data target. Required. Example:
                dataTargetType_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_data_target_type(
                dataTargetType='dataTargetType_example'
            )
        """
        path_params = {
            "dataTargetType": dataTargetType,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/dataTargetTypes/{dataTargetType}",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/datastore
    def get_datastore(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Datastore
        GET /services/{version}/datastore
        Required Role: User
        Retrieve the details of the datastore

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_datastore()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/datastore",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/datastore
    def update_datastore(
        self,
        retentionDays=None,
        collectorWorkerThreads=None,
        path=None,
        collectorWorkerQueueLimit=None,
        monitorHeartBeatTimeout=None,
        dataStoreMaxDBs=None,
        reinitialize=None,
        type=None,
        repair=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Datastore
        PATCH /services/{version}/datastore
        Required Role: Administrator
        Change the datastore configuration used by the Performance Metrics Service. Changes to the datastore
            configuration will cause the Performance Metrics Service to restart.

        Parameters:
            retentionDays (int): The number of days to retain performance metrics data. If zero, data will
                be retained indefinitely. Example: retentionDays_example
            collectorWorkerThreads (int): Mpoint Collector Number of Worker Threads. Example:
                collectorWorkerThreads_example
            path (str): The path for the datastore storage. If not set, the datastore will be created in a
                default directory. Example: path_example
            collectorWorkerQueueLimit (int): Mpoint Collector Queue max size. Example:
                collectorWorkerQueueLimit_example
            monitorHeartBeatTimeout (int): Process monitoring heartbeat timeout in seconds. Example:
                monitorHeartBeatTimeout_example
            dataStoreMaxDBs (int): Max Databases. Example: dataStoreMaxDBs_example
            reinitialize (bool): If set to true, the datastore will be reinitialized upon restart. Example:
                reinitialize_example
            type (str): The type of datastore storage, either Berkeley Database (BDB) or Lightning
                Memory-Mapped Database (LMDB). Required if not included in `data`. Example: type_example
            repair (bool): If set to true, the datastore will be repaired upon restart. Example:
                repair_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_datastore(
                data={
                    "type": "LMDB",
                    "retentionDays": "30",
                    "collectorWorkerThreads": "5",
                    "collectorWorkerQueueLimit": "10000",
                    "monitorHeartBeatTimeout": "10",
                    "dataStoreMaxDBs": "5000"
                }
            )

            client.update_datastore(
                retentionDays='30',
                collectorWorkerThreads='5',
                path=None,
                collectorWorkerQueueLimit='10000',
                monitorHeartBeatTimeout='10',
                dataStoreMaxDBs='5000',
                reinitialize=None,
                type='LMDB',
                repair=None
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/datastore",
            path_params=path_params,
            data=data,
            body_params={
                "retentionDays": retentionDays,
                "collectorWorkerThreads": collectorWorkerThreads,
                "path": path,
                "collectorWorkerQueueLimit": collectorWorkerQueueLimit,
                "monitorHeartBeatTimeout": monitorHeartBeatTimeout,
                "dataStoreMaxDBs": dataStoreMaxDBs,
                "reinitialize": reinitialize,
                "type": type,
                "repair": repair,
            },
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments
    def list_deployments(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Deployments
        GET /services/{version}/deployments
        Required Role: User
        Retrieve the collection of Oracle GoldenGate Deployments.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_deployments()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def get_deployment(
        self,
        deployment,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Deployments
        GET /services/{version}/deployments/{deployment}
        Required Role: User
        Retrieve the details of a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_deployment(
                deployment='deployment_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def create_deployment(
        self,
        deployment,
        oggHome=None,
        oggDataHome=None,
        oggConfHome=None,
        oggArchiveHome=None,
        enabled=None,
        id=None,
        configuration=None,
        oggSslHome=None,
        status=None,
        oggEtcHome=None,
        oggVarHome=None,
        environment=None,
        passwordRegex=None,
        metrics=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Deployments
        POST /services/{version}/deployments/{deployment}
        Required Role: Administrator
        Create a new Oracle GoldenGate deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            oggHome (str): The deployment's home directory. Example: oggHome_example
            oggDataHome (str): The deployment's trail data directory. Example: oggDataHome_example
            oggConfHome (str): The deployment's configuration directory. Example: oggConfHome_example
            oggArchiveHome (str): The deployment's archived trail data directory. Example:
                oggArchiveHome_example
            enabled (bool): Indicates the deployment is managed by the Service Manager. Example:
                enabled_example
            id (str): An identifier that uniquely identifies this deployment. Example: id_example
            configuration (dict): Configuration Service settings for the deployment. Example:
                configuration_example
            oggSslHome (str): The deployment's SSL configuration directory. Example: oggSslHome_example
            status (str): Indicates the status of the deployment. Example: status_example
            oggEtcHome (str): The deployment's etc configuration directory. Example: oggEtcHome_example
            oggVarHome (str): The deployment's var user data directory. Example: oggVarHome_example
            environment (list): Additional environment variables for the deployment. Example:
                environment_example
            passwordRegex (str): The regular expression that new user passwords must match. Example:
                passwordRegex_example
            metrics (dict): External servers for sending performance metrics. Example: metrics_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_deployment(
                deployment='deployment_example',
                data={
                    "oggHome": "/u01/ogg",
                    "oggEtcHome": "/home/ogg/ogg/etc",
                    "oggVarHome": "/home/ogg/ogg/var",
                    "enabled": False
                }
            )

            client.create_deployment(
                deployment='deployment_example',
                oggHome='/u01/ogg',
                oggDataHome=None,
                oggConfHome=None,
                oggArchiveHome=None,
                enabled=False,
                id=None,
                configuration={
                    "backends": {
                        "standard": None,
                        "secure": None
                    }
                },
                oggSslHome=None,
                status=None,
                oggEtcHome='/home/ogg/ogg/etc',
                oggVarHome='/home/ogg/ogg/var',
                environment=[
                    {
                        "name": None,
                        "value": None
                    }
                ],
                passwordRegex=None,
                metrics={
                    "enabled": None,
                    "servers": [
                        None
                    ]
                }
            )
        """
        path_params = {
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}",
            path_params=path_params,
            data=data,
            body_params={
                "oggHome": oggHome,
                "oggDataHome": oggDataHome,
                "oggConfHome": oggConfHome,
                "oggArchiveHome": oggArchiveHome,
                "enabled": enabled,
                "id": id,
                "configuration": configuration,
                "oggSslHome": oggSslHome,
                "status": status,
                "oggEtcHome": oggEtcHome,
                "oggVarHome": oggVarHome,
                "environment": environment,
                "passwordRegex": passwordRegex,
                "metrics": metrics,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def update_deployment(
        self,
        deployment,
        oggHome=None,
        oggDataHome=None,
        oggConfHome=None,
        oggArchiveHome=None,
        enabled=None,
        id=None,
        configuration=None,
        oggSslHome=None,
        status=None,
        oggEtcHome=None,
        oggVarHome=None,
        environment=None,
        passwordRegex=None,
        metrics=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Deployments
        PATCH /services/{version}/deployments/{deployment}
        Required Role: Administrator
        Update the properties of a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            oggHome (str): The deployment's home directory. Example: oggHome_example
            oggDataHome (str): The deployment's trail data directory. Example: oggDataHome_example
            oggConfHome (str): The deployment's configuration directory. Example: oggConfHome_example
            oggArchiveHome (str): The deployment's archived trail data directory. Example:
                oggArchiveHome_example
            enabled (bool): Indicates the deployment is managed by the Service Manager. Example:
                enabled_example
            id (str): An identifier that uniquely identifies this deployment. Example: id_example
            configuration (dict): Configuration Service settings for the deployment. Example:
                configuration_example
            oggSslHome (str): The deployment's SSL configuration directory. Example: oggSslHome_example
            status (str): Indicates the status of the deployment. Example: status_example
            oggEtcHome (str): The deployment's etc configuration directory. Example: oggEtcHome_example
            oggVarHome (str): The deployment's var user data directory. Example: oggVarHome_example
            environment (list): Additional environment variables for the deployment. Example:
                environment_example
            passwordRegex (str): The regular expression that new user passwords must match. Example:
                passwordRegex_example
            metrics (dict): External servers for sending performance metrics. Example: metrics_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_deployment(
                deployment='deployment_example',
                data={
                    "enabled": True
                }
            )

            client.update_deployment(
                deployment='deployment_example',
                oggHome=None,
                oggDataHome=None,
                oggConfHome=None,
                oggArchiveHome=None,
                enabled=True,
                id=None,
                configuration={
                    "backends": {
                        "standard": None,
                        "secure": None
                    }
                },
                oggSslHome=None,
                status=None,
                oggEtcHome=None,
                oggVarHome=None,
                environment=[
                    {
                        "name": None,
                        "value": None
                    }
                ],
                passwordRegex=None,
                metrics={
                    "enabled": None,
                    "servers": [
                        None
                    ]
                }
            )
        """
        path_params = {
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/deployments/{deployment}",
            path_params=path_params,
            data=data,
            body_params={
                "oggHome": oggHome,
                "oggDataHome": oggDataHome,
                "oggConfHome": oggConfHome,
                "oggArchiveHome": oggArchiveHome,
                "enabled": enabled,
                "id": id,
                "configuration": configuration,
                "oggSslHome": oggSslHome,
                "status": status,
                "oggEtcHome": oggEtcHome,
                "oggVarHome": oggVarHome,
                "environment": environment,
                "passwordRegex": passwordRegex,
                "metrics": metrics,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def delete_deployment(
        self,
        deployment,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Deployments
        DELETE /services/{version}/deployments/{deployment}
        Required Role: Administrator
        Delete a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_deployment(
                deployment='deployment_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/deployments/{deployment}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles
    def list_authorization_profiles(
        self,
        deployment,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Authorization Profiles
        GET /services/{version}/deployments/{deployment}/authorization/profiles
        Required Role: Security
        Retrieve the collection of Authorization profiles in a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_authorization_profiles(
                deployment='deployment_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/authorization/profiles",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
    def get_authorization_profile(
        self,
        deployment,
        profile,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Authorization Profiles
        GET /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
        Required Role: Security
        Get the content of a specific Authorization profile in a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            profile (str): Name of Authorization profile. Required. Example: profile_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_authorization_profile(
                deployment='deployment_example',
                profile='profile_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "profile": profile,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/authorization/profiles/{profile}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
    def create_authorization_profile(
        self,
        deployment,
        profile,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Authorization Profiles
        POST /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
        Required Role: Security
        Create an Authorization profile in a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            profile (str): Name of Authorization profile. Required. Example: profile_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_authorization_profile(
                deployment='deployment_example',
                profile='profile_example',
                data={
                    "type": "idcs",
                    "clientID": "4a33ef81bf1642689ac83742a27b8a94",
                    "clientSecret": "166155e9-884d-4eb3-9733-21f98f0698bc",
                    "tenantDiscoveryURI": "https://your.tenantDiscoveryURI.domain",
                    "groupToRoles": {
                        "securityGroup": "Demo-source-security"
                    }
                }
            )
        """
        path_params = {
            "deployment": deployment,
            "profile": profile,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}/authorization/profiles/{profile}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
    def update_authorization_profile(
        self,
        deployment,
        profile,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Authorization Profiles
        PATCH /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
        Required Role: Security
        Patch the content of a given profile

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            profile (str): Name of Authorization profile. Required. Example: profile_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_authorization_profile(
                deployment='deployment_example',
                profile='profile_example',
                data={
                    "clientID": "4a33ef81bf1642689ac83742a27b8a94",
                    "clientSecret": "166155e9-884d-4eb3-9733-21f98f0698bc",
                    "tenantDiscoveryURI": "https://your.tenantDiscoveryURI.domain",
                    "groupToRoles": {
                        "securityGroup": "Demo-source-security",
                        "administratorGroup": "Demo-source-admin"
                    },
                    "enabled": True
                }
            )
        """
        path_params = {
            "deployment": deployment,
            "profile": profile,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/deployments/{deployment}/authorization/profiles/{profile}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
    def delete_authorization_profile(
        self,
        deployment,
        profile,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Authorization Profiles
        DELETE /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
        Required Role: Security
        Delete an Authorization profile from a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            profile (str): Name of Authorization profile. Required. Example: profile_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_authorization_profile(
                deployment='deployment_example',
                profile='profile_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "profile": profile,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/deployments/{deployment}/authorization/profiles/{profile}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}/valid
    def is_authorization_profile_valid(
        self,
        deployment,
        profile,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Authorization Profiles
        GET /services/{version}/deployments/{deployment}/authorization/profiles/{profile}/valid
        Required Role: Security
        Test the connection to the Authorization Tenant

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            profile (str): Name of Authorization profile. Required. Example: profile_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.is_authorization_profile_valid(
                deployment='deployment_example',
                profile='profile_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "profile": profile,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/authorization/profiles/{profile}/valid",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates
    def list_deployment_certificates_types(
        self,
        deployment,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Certificates
        GET /services/{version}/deployments/{deployment}/certificates
        Required Role: Administrator
        Retrieve the collection of certificate types.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_deployment_certificates_types(
                deployment='deployment_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/certificates",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}
    def list_deployment_certificates(
        self,
        deployment,
        type,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Certificates
        GET /services/{version}/deployments/{deployment}/certificates/{type}
        Required Role: Administrator
        Retrieve the certificate type names.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            type (str): Required. Example: type_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_deployment_certificates(
                deployment='deployment_example',
                type='type_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "type": type,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/certificates/{type}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
    def get_deployment_certificate(
        self,
        deployment,
        type,
        certificate,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Certificates
        GET /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
        Required Role: Administrator
        Retrieve the certificate PEM data for the named certificate in the deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            type (str): Required. Example: type_example
            certificate (str): Deployment certificate name. Required. Example: certificate_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_deployment_certificate(
                deployment='deployment_example',
                type='type_example',
                certificate='certificate_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "type": type,
            "certificate": certificate,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
    def create_deployment_certificate(
        self,
        deployment,
        type,
        certificate,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Certificates
        POST /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
        Required Role: Security
        Add a named certificate to a deployment. The certificate name must be unique and not exist in the
            deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            type (str): Required. Example: type_example
            certificate (str): Deployment certificate name. Required. Example: certificate_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_deployment_certificate(
                deployment='deployment_example',
                type='type_example',
                certificate='certificate_example',
                data={
                    "certificateBundle": {
                        "caCertificates": [
                            "-----BEGIN CERTIFICATE-----...truncated...-----END CERTIFICATE-----\n"
                        ],
                        "certificatePem": "-----BEGIN CERTIFICATE-----...truncated...-----END CERTIFICATE-----\n",
                        "privateKeyPem": "-----BEGIN PRIVATE KEY-----...truncated...-----END PRIVATE KEY-----\n"
                    }
                }
            )
        """
        path_params = {
            "deployment": deployment,
            "type": type,
            "certificate": certificate,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
    def delete_deployment_certificate(
        self,
        deployment,
        type,
        certificate,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Certificates
        DELETE /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
        Required Role: Security
        Delete a named certificate from a deployment. The certificate name must exist in the deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            type (str): Required. Example: type_example
            certificate (str): Deployment certificate name. Required. Example: certificate_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_deployment_certificate(
                deployment='deployment_example',
                type='type_example',
                certificate='certificate_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "type": type,
            "certificate": certificate,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
    def update_deployment_certificate(
        self,
        deployment,
        type,
        certificate,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Certificates
        PUT /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
        Required Role: Security
        Replace a named certificate in a deployment. The certificate name must exist in the deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            type (str): Required. Example: type_example
            certificate (str): Deployment certificate name. Required. Example: certificate_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_deployment_certificate(
                deployment='deployment_example',
                type='type_example',
                certificate='certificate_example',
                data={
                    "certificateBundle": {
                        "caCertificates": [
                            "-----BEGIN CERTIFICATE-----...truncated...-----END CERTIFICATE-----\n"
                        ],
                        "certificatePem": "-----BEGIN CERTIFICATE-----...truncated...-----END CERTIFICATE-----\n",
                        "privateKeyPem": "-----BEGIN PRIVATE KEY-----...truncated...-----END PRIVATE KEY-----\n"
                    }
                }
            )
        """
        path_params = {
            "deployment": deployment,
            "type": type,
            "certificate": certificate,
            "version": version,
        }
        return self._call(
            "PUT",
            "/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}/info
    def get_deployment_certificate_info(
        self,
        deployment,
        type,
        certificate,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Certificates
        GET /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}/info
        Required Role: Administrator
        Retrieve the certificate information for the named certificate in the deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            type (str): Required. Example: type_example
            certificate (str): Deployment certificate name. Required. Example: certificate_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_deployment_certificate_info(
                deployment='deployment_example',
                type='type_example',
                certificate='certificate_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "type": type,
            "certificate": certificate,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}/info",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/plugin/templates
    def list_plugin_templates(
        self,
        deployment,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Plugin Templates
        GET /services/{version}/deployments/{deployment}/plugin/templates
        Required Role: Security
        Retrieve the collection of plugin templates in a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_plugin_templates(
                deployment='deployment_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/plugin/templates",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
    def get_plugin_template(
        self,
        deployment,
        plugin,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Plugin Templates
        GET /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
        Required Role: Security
        Get the content of a specific plugin template in a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            plugin (str): Name of plugin for the template. Required. Example: plugin_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_plugin_template(
                deployment='deployment_example',
                plugin='plugin_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "plugin": plugin,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
    def create_plugin_template(
        self,
        deployment,
        plugin,
        metadata=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Plugin Templates
        POST /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
        Required Role: Security
        Create a plugin template in a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            plugin (str): Name of plugin for the template. Required. Example: plugin_example
            metadata (list): Array of metadata key/value pairs. Required if not included in `data`. Example:
                metadata_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_plugin_template(
                deployment='deployment_example',
                plugin='plugin_example',
                data={
                    "$schema": "ogg:pluginMetadata",
                    "metadata": [
                        {
                            "name": "OCI_VAULTKEY_OCID",
                            "value": "OCI Vault Key OCID"
                        },
                        {
                            "name": "OCI_CRYPTO_ENDPOINT",
                            "value": "Cryptographic endpoint to use"
                        },
                        {
                            "name": "OCI_AUTH",
                            "value": "OCI authentication method"
                        }
                    ]
                }
            )

            client.create_plugin_template(
                deployment='deployment_example',
                plugin='plugin_example',
                metadata=[
                    {
                        "name": "OCI_VAULTKEY_OCID",
                        "value": "OCI Vault Key OCID"
                    },
                    {
                        "name": "OCI_CRYPTO_ENDPOINT",
                        "value": "Cryptographic endpoint to use"
                    },
                    {
                        "name": "OCI_AUTH",
                        "value": "OCI authentication method"
                    }
                ]
            )
        """
        path_params = {
            "deployment": deployment,
            "plugin": plugin,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params=path_params,
            data=data,
            body_params={
                "metadata": metadata,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
    def delete_plugin_template(
        self,
        deployment,
        plugin,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Plugin Templates
        DELETE /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
        Required Role: Security
        Delete a plugin template from a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            plugin (str): Name of plugin for the template. Required. Example: plugin_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_plugin_template(
                deployment='deployment_example',
                plugin='plugin_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "plugin": plugin,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
    def update_plugin_template(
        self,
        deployment,
        plugin,
        metadata=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Plugin Templates
        PUT /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
        Required Role: Security
        Update the content of a given plugin template

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            plugin (str): Name of plugin for the template. Required. Example: plugin_example
            metadata (list): Array of metadata key/value pairs. Required if not included in `data`. Example:
                metadata_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_plugin_template(
                deployment='deployment_example',
                plugin='plugin_example',
                data={
                    "$schema": "ogg:pluginMetadata",
                    "metadata": [
                        {
                            "name": "OCI_VAULTKEY_OCID",
                            "value": "OCI Vault Key OCID"
                        },
                        {
                            "name": "OCI_CRYPTO_ENDPOINT",
                            "value": "Cryptographic endpoint to use"
                        }
                    ]
                }
            )

            client.update_plugin_template(
                deployment='deployment_example',
                plugin='plugin_example',
                metadata=[
                    {
                        "name": "OCI_VAULTKEY_OCID",
                        "value": "OCI Vault Key OCID"
                    },
                    {
                        "name": "OCI_CRYPTO_ENDPOINT",
                        "value": "Cryptographic endpoint to use"
                    }
                ]
            )
        """
        path_params = {
            "deployment": deployment,
            "plugin": plugin,
            "version": version,
        }
        return self._call(
            "PUT",
            "/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params=path_params,
            data=data,
            body_params={
                "metadata": metadata,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services
    def list_services(
        self,
        deployment,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services
        Required Role: User
        Retrieve the collection of Oracle GoldenGate Services in a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_services(
                deployment='deployment_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/services",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def get_service(
        self,
        deployment,
        service,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services/{service}
        Required Role: User
        Retrieve the details of a service in an Oracle GoldenGate deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_service(
                deployment='deployment_example',
                service='service_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "service": service,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def create_service(
        self,
        deployment,
        service,
        config=None,
        quiet=None,
        enabled=None,
        id=None,
        status=None,
        critical=None,
        restart=None,
        locked=None,
        configForce=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Services
        POST /services/{version}/deployments/{deployment}/services/{service}
        Required Role: Administrator
        Add a new service to a deployment. An application with the service name must exist for this request to
            succeed.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            config (dict): Service configuration data. Example: config_example
            quiet (bool): Start the service in quiet mode. Example: quiet_example
            enabled (bool): Indicates the service is managed by the Service Manager. Example:
                enabled_example
            id (str): An identifier that uniquely identifies this service. Example: id_example
            status (str): Indicates the status of the service. Example: status_example
            critical (bool): Indicates the service is critical to the deployment. Example: critical_example
            restart (dict): Control how the service is restarted if it terminates. Example: restart_example
            locked (bool): Indicates the service is locked by a security administrator and cannot be
                started. Example: locked_example
            configForce (bool): Force the configuration data (NO LONGER USED). Example: configForce_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_service(
                deployment='deployment_example',
                service='service_example',
                data={
                    "$schema": "ogg:service",
                    "config": {
                        "network": {
                            "serviceListeningPort": "19012"
                        },
                        "security": False,
                        "authorizationEnabled": True,
                        "defaultSynchronousWait": "30",
                        "asynchronousOperationEnabled": True,
                        "legacyProtocolEnabled": True,
                        "taskManagerEnabled": True
                    },
                    "enabled": False
                }
            )

            client.create_service(
                deployment='deployment_example',
                service='service_example',
                config={
                    "network": {
                        "serviceListeningPort": "19012"
                    },
                    "security": False,
                    "authorizationEnabled": True,
                    "defaultSynchronousWait": "30",
                    "asynchronousOperationEnabled": True,
                    "legacyProtocolEnabled": True,
                    "taskManagerEnabled": True
                },
                quiet=None,
                enabled=False,
                id=None,
                status=None,
                critical=None,
                restart={
                    "enabled": None,
                    "onSuccess": None,
                    "delay": None,
                    "retries": None,
                    "window": None,
                    "disableOnFailure": None,
                    "failures": None
                },
                locked=None,
                configForce=None
            )
        """
        path_params = {
            "deployment": deployment,
            "service": service,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params=path_params,
            data=data,
            body_params={
                "config": config,
                "quiet": quiet,
                "enabled": enabled,
                "id": id,
                "status": status,
                "critical": critical,
                "restart": restart,
                "locked": locked,
                "configForce": configForce,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def update_service(
        self,
        deployment,
        service,
        config=None,
        quiet=None,
        enabled=None,
        id=None,
        status=None,
        critical=None,
        restart=None,
        locked=None,
        configForce=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Services
        PATCH /services/{version}/deployments/{deployment}/services/{service}
        Required Role: Administrator
        Update the properties of a service.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            config (dict): Service configuration data. Example: config_example
            quiet (bool): Start the service in quiet mode. Example: quiet_example
            enabled (bool): Indicates the service is managed by the Service Manager. Example:
                enabled_example
            id (str): An identifier that uniquely identifies this service. Example: id_example
            status (str): Indicates the status of the service. Example: status_example
            critical (bool): Indicates the service is critical to the deployment. Example: critical_example
            restart (dict): Control how the service is restarted if it terminates. Example: restart_example
            locked (bool): Indicates the service is locked by a security administrator and cannot be
                started. Example: locked_example
            configForce (bool): Force the configuration data (NO LONGER USED). Example: configForce_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_service(
                deployment='deployment_example',
                service='service_example',
                data={
                    "enabled": True,
                    "status": "running"
                }
            )

            client.update_service(
                deployment='deployment_example',
                service='service_example',
                config=None,
                quiet=None,
                enabled=True,
                id=None,
                status='running',
                critical=None,
                restart={
                    "enabled": None,
                    "onSuccess": None,
                    "delay": None,
                    "retries": None,
                    "window": None,
                    "disableOnFailure": None,
                    "failures": None
                },
                locked=None,
                configForce=None
            )
        """
        path_params = {
            "deployment": deployment,
            "service": service,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params=path_params,
            data=data,
            body_params={
                "config": config,
                "quiet": quiet,
                "enabled": enabled,
                "id": id,
                "status": status,
                "critical": critical,
                "restart": restart,
                "locked": locked,
                "configForce": configForce,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def delete_service(
        self,
        deployment,
        service,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Services
        DELETE /services/{version}/deployments/{deployment}/services/{service}
        Required Role: Administrator
        Remove a service from an Oracle GoldenGate deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_service(
                deployment='deployment_example',
                service='service_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "service": service,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}/logs
    def list_service_logs(
        self,
        deployment,
        service,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services/{service}/logs
        Required Role: User
        Retrieve the set of logs for the service

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_service_logs(
                deployment='deployment_example',
                service='service_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "service": service,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/services/{service}/logs",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}/logs/default
    def get_service_log(
        self,
        deployment,
        service,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services/{service}/logs/default
        Required Role: Administrator
        Retrieve the service log

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_service_log(
                deployment='deployment_example',
                service='service_example'
            )
        """
        path_params = {
            "deployment": deployment,
            "service": service,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/services/{service}/logs/default",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys
    def list_encryption_keys(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Encryption Keys
        GET /services/{version}/enckeys
        Required Role: User
        Retrieve the names of all encryption keys

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_encryption_keys()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/enckeys",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def get_encryption_key(
        self,
        keyName,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Encryption Keys
        GET /services/{version}/enckeys/{keyName}
        Required Role: User
        Retrieve details for an Encryption Key.

        Parameters:
            keyName (str): The name of the Encryption Key. Required. Example: keyName_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_encryption_key(
                keyName='keyName_example'
            )
        """
        path_params = {
            "keyName": keyName,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/enckeys/{keyName}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def create_encryption_key(
        self,
        keyName,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Encryption Keys
        POST /services/{version}/enckeys/{keyName}
        Required Role: Administrator
        Create an Encryption Key.

        Parameters:
            keyName (str): The name of the Encryption Key. Required. Example: keyName_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_encryption_key(
                keyName='keyName_example',
                data={
                    "bitLength": "128"
                }
            )
        """
        path_params = {
            "keyName": keyName,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/enckeys/{keyName}",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def delete_encryption_key(
        self,
        keyName,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Encryption Keys
        DELETE /services/{version}/enckeys/{keyName}
        Required Role: Administrator
        Delete an Encryption Key

        Parameters:
            keyName (str): The name of the Encryption Key. Required. Example: keyName_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_encryption_key(
                keyName='keyName_example'
            )
        """
        path_params = {
            "keyName": keyName,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/enckeys/{keyName}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}/encrypt
    def encrypt_data(
        self,
        keyName,
        encoding=None,
        data_1=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Encryption Keys
        POST /services/{version}/enckeys/{keyName}/encrypt
        Required Role: User
        Encrypt data using the Encryption Key.

        Parameters:
            keyName (str): The name of the Encryption Key. Required. Example: keyName_example
            encoding (str): Encoding to use for encrypted data in response. Example: encoding_example
            data (str): Data to be encrypted
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.encrypt_data(
                keyName='keyName_example',
                data={
                    "data": "plaintext-password"
                }
            )

            client.encrypt_data(
                keyName='keyName_example',
                encoding=None,
                data_1='plaintext-password'
            )
        """
        path_params = {
            "keyName": keyName,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/enckeys/{keyName}/encrypt",
            path_params=path_params,
            data=data,
            body_params={
                "encoding": encoding,
                "data": data_1,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles
    def list_encryption_profiles(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Encryption Profiles
        GET /services/{version}/encryption/profiles
        Required Role: Any
        Retrieve names of all existing Encryption Profiles.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_encryption_profiles()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/encryption/profiles",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}
    def get_encryption_profile(
        self,
        profile,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Encryption Profiles
        GET /services/{version}/encryption/profiles/{profile}
        Required Role: Any
        Retrieve details for an Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Required. Example: profile_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_encryption_profile(
                profile='profile_example'
            )
        """
        path_params = {
            "profile": profile,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/encryption/profiles/{profile}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}
    def create_encryption_profile(
        self,
        profile,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Encryption Profiles
        POST /services/{version}/encryption/profiles/{profile}
        Required Role: Administrator
        Create an Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Required. Example: profile_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_encryption_profile(
                profile='profile_example',
                data={
                    "$schema": "ogg:encryptionProfile",
                    "type": "okv",
                    "okvVersion": "18.1",
                    "okvPath": "/tmp/okvSample",
                    "keyNameAttribute": "x-OGG-KeyName",
                    "keyVersionAttribute": "x-OGG-KeyVersion",
                    "masterkey": {
                        "name": "OGGMK_A1",
                        "version": "LATEST",
                        "ttl": "86400"
                    }
                }
            )
        """
        path_params = {
            "profile": profile,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/encryption/profiles/{profile}",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}
    def update_encryption_profile(
        self,
        profile,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Encryption Profiles
        PATCH /services/{version}/encryption/profiles/{profile}
        Required Role: Administrator
        Modify an existing Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Required. Example: profile_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_encryption_profile(
                profile='profile_example',
                data={
                    "type": "okv",
                    "isDefault": True
                }
            )
        """
        path_params = {
            "profile": profile,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/encryption/profiles/{profile}",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}
    def delete_encryption_profile(
        self,
        profile,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Encryption Profiles
        DELETE /services/{version}/encryption/profiles/{profile}
        Required Role: Administrator
        Delete an Encryption Profile

        Parameters:
            profile (str): Name of the Encryption Profile. Required. Example: profile_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_encryption_profile(
                profile='profile_example'
            )
        """
        path_params = {
            "profile": profile,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/encryption/profiles/{profile}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}/valid
    def is_encryption_profile_valid(
        self,
        profile,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Encryption Profiles
        GET /services/{version}/encryption/profiles/{profile}/valid
        Required Role: Administrator
        Validate an Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Required. Example: profile_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.is_encryption_profile_valid(
                profile='profile_example'
            )
        """
        path_params = {
            "profile": profile,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/encryption/profiles/{profile}/valid",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts
    def list_extracts(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts
        Required Role: User
        Retrieve the collection of Extract processes

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extracts()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def get_extract(
        self,
        extract,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}
        Required Role: User
        Retrieve the details of an extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract(
                extract='extract_example'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def create_extract(
        self,
        extract,
        begin=None,
        passive=None,
        config=None,
        encryptionProfile=None,
        status=None,
        critical=None,
        rollover=None,
        targets=None,
        managedProcessSettings=None,
        replicationSlot=None,
        intent=None,
        registration=None,
        source=None,
        type=None,
        miningCredentials=None,
        alias=None,
        credentials=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Extracts
        POST /services/{version}/extracts/{extract}
        Required Role: Administrator
        Create a new extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            begin (dict): Starting point for data processing. Example: begin_example
            passive (bool): Passive extract controlled by an alias on the target. Example: passive_example
            config (list):  Example: config_example
            encryptionProfile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the extract is critical to the deployment. Example: critical_example
            rollover (str): Causes Extract to increment to the next file in the trail sequence when
                restarting. Example: rollover_example
            targets (list): Targets for captured data. Example: targets_example
            managedProcessSettings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            replicationSlot (str): Replication slot which needs to be used for MIGRATE command in
                PostgreSQL. Example: replicationSlot_example
            intent (str): Intent for data capture workflow. Example: intent_example
            registration (dict): Registration with the source database. Example: registration_example
            source (dict): Source of data to process. Example: source_example
            type (str): OGG Extract process type (read-only). Example: type_example
            miningCredentials (dict): Credentials for downstream mining database. Example:
                miningCredentials_example
            alias (dict):  Example: ggnorth
            credentials (dict): Credentials for source database. Example: credentials_example
            description (str): Description for the process. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_extract(
                extract='extract_example',
                data={
                    "description": "Region North",
                    "config": [
                        "EXTRACT extn",
                        "EXTTRAIL north/ea",
                        "USERIDALIAS ggnorth",
                        "SOURCECATALOG dbnorth_pdb1",
                        "TABLE hr.*;"
                    ],
                    "source": "tranlogs",
                    "credentials": {
                        "alias": "ggnorth"
                    },
                    "registration": {
                        "optimized": False,
                        "containers": [
                            "dbnorth_pdb1"
                        ],
                        "replace": True
                    },
                    "begin": "now",
                    "targets": [
                        {
                            "name": "ea",
                            "path": "north/"
                        }
                    ]
                }
            )

            client.create_extract(
                extract='extract_example',
                begin='now',
                passive=None,
                config=[
                    "EXTRACT extn",
                    "EXTTRAIL north/ea",
                    "USERIDALIAS ggnorth",
                    "SOURCECATALOG dbnorth_pdb1",
                    "TABLE hr.*;"
                ],
                encryptionProfile=None,
                status=None,
                critical=None,
                rollover=None,
                targets=[
                    {
                        "name": "ea",
                        "path": "north/"
                    }
                ],
                managedProcessSettings=None,
                replicationSlot=None,
                intent=None,
                registration={
                    "optimized": False,
                    "containers": [
                        "dbnorth_pdb1"
                    ],
                    "replace": True
                },
                source='tranlogs',
                type=None,
                miningCredentials=None,
                alias={
                    "name": None,
                    "manager": {
                        "host": None,
                        "port": None
                    },
                    "proxy": {
                        "host": None,
                        "port": None,
                        "credentials": None
                    }
                },
                credentials={
                    "alias": "ggnorth"
                },
                description='Region North'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/extracts/{extract}",
            path_params=path_params,
            data=data,
            body_params={
                "begin": begin,
                "passive": passive,
                "config": config,
                "encryptionProfile": encryptionProfile,
                "status": status,
                "critical": critical,
                "rollover": rollover,
                "targets": targets,
                "managedProcessSettings": managedProcessSettings,
                "replicationSlot": replicationSlot,
                "intent": intent,
                "registration": registration,
                "source": source,
                "type": type,
                "miningCredentials": miningCredentials,
                "alias": alias,
                "credentials": credentials,
                "description": description,
            },
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def update_extract(
        self,
        extract,
        begin=None,
        passive=None,
        config=None,
        encryptionProfile=None,
        status=None,
        critical=None,
        rollover=None,
        targets=None,
        managedProcessSettings=None,
        replicationSlot=None,
        intent=None,
        registration=None,
        source=None,
        type=None,
        miningCredentials=None,
        alias=None,
        credentials=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        PATCH /services/{version}/extracts/{extract}
        Required Role: Operator
        Update an existing extract process. A user with the 'Operator' role may change the "status" property.
            Any other changes require the 'Administrator' role.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            begin (dict): Starting point for data processing. Example: begin_example
            passive (bool): Passive extract controlled by an alias on the target. Example: passive_example
            config (list):  Example: config_example
            encryptionProfile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the extract is critical to the deployment. Example: critical_example
            rollover (str): Causes Extract to increment to the next file in the trail sequence when
                restarting. Example: rollover_example
            targets (list): Targets for captured data. Example: targets_example
            managedProcessSettings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            replicationSlot (str): Replication slot which needs to be used for MIGRATE command in
                PostgreSQL. Example: replicationSlot_example
            intent (str): Intent for data capture workflow. Example: intent_example
            registration (dict): Registration with the source database. Example: registration_example
            source (dict): Source of data to process. Example: source_example
            type (str): OGG Extract process type (read-only). Example: type_example
            miningCredentials (dict): Credentials for downstream mining database. Example:
                miningCredentials_example
            alias (dict):  Example: ggnorth
            credentials (dict): Credentials for source database. Example: credentials_example
            description (str): Description for the process. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_extract(
                extract='extract_example',
                data={
                    "status": "running"
                }
            )

            client.update_extract(
                extract='extract_example',
                begin=None,
                passive=None,
                config=[
                    None
                ],
                encryptionProfile=None,
                status='running',
                critical=None,
                rollover=None,
                targets=[
                    None
                ],
                managedProcessSettings=None,
                replicationSlot=None,
                intent=None,
                registration=None,
                source=None,
                type=None,
                miningCredentials=None,
                alias={
                    "name": None,
                    "manager": {
                        "host": None,
                        "port": None
                    },
                    "proxy": {
                        "host": None,
                        "port": None,
                        "credentials": None
                    }
                },
                credentials=None,
                description=None
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/extracts/{extract}",
            path_params=path_params,
            data=data,
            body_params={
                "begin": begin,
                "passive": passive,
                "config": config,
                "encryptionProfile": encryptionProfile,
                "status": status,
                "critical": critical,
                "rollover": rollover,
                "targets": targets,
                "managedProcessSettings": managedProcessSettings,
                "replicationSlot": replicationSlot,
                "intent": intent,
                "registration": registration,
                "source": source,
                "type": type,
                "miningCredentials": miningCredentials,
                "alias": alias,
                "credentials": credentials,
                "description": description,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def delete_extract(
        self,
        extract,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        DELETE /services/{version}/extracts/{extract}
        Required Role: Administrator
        Delete an extract process. If the extract process is currently running, it is stopped first.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_extract(
                extract='extract_example'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/extracts/{extract}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/command
    def execute_command_extract(
        self,
        extract,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        POST /services/{version}/extracts/{extract}/command
        Required Role: User
        Execute an Extract process command

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.execute_command_extract(
                extract='extract_example',
                data={
                    "command": "STATS",
                    "arguments": "HOURLY"
                }
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/extracts/{extract}/command",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info
    def get_extract_info_types(
        self,
        extract,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info
        Required Role: User
        Retrieve types of information available for an extract.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_info_types(
                extract='extract_example'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/checkpoints
    def get_extract_checkpoint(
        self,
        extract,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/checkpoints
        Required Role: User
        Retrieve the checkpoint information for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_checkpoint(
                extract='extract_example'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/checkpoints",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/diagnostics
    def list_extract_diagnostics(
        self,
        extract,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/diagnostics
        Required Role: User
        Retrieve the list of diagnostic results available for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extract_diagnostics(
                extract='extract_example'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/diagnostics",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/diagnostics/{diagnostic}
    def get_extract_diagnostic(
        self,
        extract,
        diagnostic,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/diagnostics/{diagnostic}
        Required Role: User
        Retrieve a diagnostics result for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            diagnostic (str): The name of the diagnostic results, which is the extract name and
                '.diagnostics', followed by an optional revision number. Required. Example:
                diagnostic_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_diagnostic(
                extract='extract_example',
                diagnostic='diagnostic_example'
            )
        """
        path_params = {
            "extract": extract,
            "diagnostic": diagnostic,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/diagnostics/{diagnostic}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/history
    def get_extract_history(
        self,
        extract,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/history
        Required Role: User
        Retrieve the execution history of a managed extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_history(
                extract='extract_example'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/history",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/logs
    def list_extract_logs(
        self,
        extract,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/logs
        Required Role: User
        Retrieve the list of logs available for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extract_logs(
                extract='extract_example'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/logs",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/logs/{log}
    def get_extract_log(
        self,
        extract,
        log,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/logs/{log}
        Required Role: Administrator
        Retrieve a log from the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            log (str): The name of the log, which is the extract name, followed by an optional revision
                number(as -number) and '.log'. Required. Example: log_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_log(
                extract='extract_example',
                log='log_example'
            )
        """
        path_params = {
            "extract": extract,
            "log": log,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/logs/{log}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/reports
    def list_extract_reports(
        self,
        extract,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/reports
        Required Role: User
        Retrieve the list of reports available for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extract_reports(
                extract='extract_example'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/reports",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/reports/{report}
    def get_extract_report(
        self,
        extract,
        report,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/reports/{report}
        Required Role: User
        Retrieve a report from the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            report (str): The name of the report, which is the extract name, followed by an optional
                revision number and '.rpt'. Required. Example: report_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_report(
                extract='extract_example',
                report='report_example'
            )
        """
        path_params = {
            "extract": extract,
            "report": report,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/reports/{report}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/status
    def get_extract_status(
        self,
        extract,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/status
        Required Role: User
        Retrieve the current status of the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_status(
                extract='extract_example'
            )
        """
        path_params = {
            "extract": extract,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/status",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/exttrails
    def list_extract_trails(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/exttrails
        Required Role: User
        Get a list of the deployment extracts with their trail files

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extract_trails()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/exttrails",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster
    def get_cluster(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        GET /services/{version}/installation/cluster
        Required Role: Administrator
        Retrieve the details for the installation's GoldenGate cluster.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_cluster()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/installation/cluster",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster
    def create_cluster(
        self,
        region=None,
        backPlane=None,
        dataPlane=None,
        members=None,
        join=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Cluster Management
        POST /services/{version}/installation/cluster
        Required Role: Security
        Add the GoldenGate installation to an existing cluster or create a new cluster.

        Parameters:
            region (str): The region of the cluster member. Required if not included in `data`. Example:
                region_example
            backPlane (dict): The listener on the local installation for intra-cluster member communication.
                Required if not included in `data`. Example: backPlane_example
            dataPlane (dict): The listener on the local installation for serving cluster data requests.
                Required if not included in `data`. Example: dataPlane_example
            members (list): Cluster members. Example: members_example
            join (dict): Properties for joining an existing GoldenGate cluster. Example: join_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_cluster(
                data={
                    "dataPlane": {
                        "host": "127.0.0.1",
                        "port": "5512"
                    },
                    "backPlane": {
                        "host": "0.0.0.0",
                        "port": "5511"
                    }
                }
            )

            client.create_cluster(
                region=None,
                backPlane={
                    "host": "0.0.0.0",
                    "port": "5511"
                },
                dataPlane={
                    "host": "127.0.0.1",
                    "port": "5512"
                },
                members=[
                    {
                        "memberName": None,
                        "region": None,
                        "backPlane": {
                            "host": None,
                            "port": None
                        },
                        "dataPlane": {
                            "host": None,
                            "port": None
                        },
                        "current": None,
                        "target": None
                    }
                ],
                join={
                    "url": "https://remote-host.example.com:9011/services/v2",
                    "user": None,
                    "password": None
                }
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/installation/cluster",
            path_params=path_params,
            data=data,
            body_params={
                "region": region,
                "backPlane": backPlane,
                "dataPlane": dataPlane,
                "members": members,
                "join": join,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster
    def delete_cluster(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        DELETE /services/{version}/installation/cluster
        Required Role: Security
        Remove the installation from the GoldenGate cluster.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_cluster()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/installation/cluster",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster/actions/memberAdd
    def add_cluster_member(
        self,
        memberName=None,
        region=None,
        backPlane=None,
        dataPlane=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Cluster Management
        POST /services/{version}/installation/cluster/actions/memberAdd
        Required Role: Security
        Internal API for adding a remote GoldenGate installation to the cluster.

        Parameters:
            memberName (str): The name of the member to add to the cluster. Required if not included in
                `data`. Example: memberName_example
            region (str): The region of the new cluster member. Required if not included in `data`. Example:
                region_example
            backPlane (dict): The address of the listener on the new member for intra-cluster member
                communication. Required if not included in `data`. Example: backPlane_example
            dataPlane (dict): The listener on the new member for serving cluster data requests. Example:
                dataPlane_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.add_cluster_member(
                data={
                    "$schema": "internal:clusterMemberAdd",
                    "memberName": "oggdev-2",
                    "backPlane": {
                        "host": "0.0.0.0",
                        "port": "5511"
                    },
                    "dataPlane": {
                        "host": "127.0.0.1",
                        "port": "5512"
                    }
                }
            )

            client.add_cluster_member(
                memberName='oggdev-2',
                region=None,
                backPlane={
                    "host": "0.0.0.0",
                    "port": "5511"
                },
                dataPlane={
                    "host": "127.0.0.1",
                    "port": "5512"
                }
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/installation/cluster/actions/memberAdd",
            path_params=path_params,
            data=data,
            body_params={
                "memberName": memberName,
                "region": region,
                "backPlane": backPlane,
                "dataPlane": dataPlane,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster/role/{member}
    def get_cluster_member(
        self,
        member,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        GET /services/{version}/installation/cluster/role/{member}
        Required Role: Security
        Retrieve a member's role in the OGG cluster

        Parameters:
            member (str): Name of the OGG Cluster member. Required. Example: member_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_cluster_member(
                member='member_example'
            )
        """
        path_params = {
            "member": member,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/installation/cluster/role/{member}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster/role/{member}
    def update_cluster_member(
        self,
        member,
        memberName=None,
        current=None,
        target=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        PATCH /services/{version}/installation/cluster/role/{member}
        Required Role: Security
        Update a member's role in the OGG cluster

        Parameters:
            member (str): Name of the OGG Cluster member. Required. Example: member_example
            memberName (str): The name of the cluster member. Example: memberName_example
            current (str): Member role. Example: current_example
            target (str): Member role. Example: target_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_cluster_member(
                member='member_example',
                data={
                    "target": "backup"
                }
            )

            client.update_cluster_member(
                member='member_example',
                memberName=None,
                current=None,
                target='backup'
            )
        """
        path_params = {
            "member": member,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/installation/cluster/role/{member}",
            path_params=path_params,
            data=data,
            body_params={
                "memberName": memberName,
                "current": current,
                "target": target,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster/role/{member}
    def delete_cluster_member(
        self,
        member,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        DELETE /services/{version}/installation/cluster/role/{member}
        Required Role: Security
        Delete a member from the OGG Cluster

        Parameters:
            member (str): Name of the OGG Cluster member. Required. Example: member_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_cluster_member(
                member='member_example'
            )
        """
        path_params = {
            "member": member,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/installation/cluster/role/{member}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration
    def get_configuration_service(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Installation
        GET /services/{version}/installation/configuration
        Required Role: Administrator
        Retrieve the configuration details for the GoldenGate installation.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_configuration_service()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/installation/configuration",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration
    def update_configuration_service(
        self,
        installationId=None,
        configurationServiceEnabled=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Installation
        PATCH /services/{version}/installation/configuration
        Required Role: Security
        Update the configuration details for the GoldenGate installation.

        Parameters:
            installationId (str): Unique Identifier for the installation. Example: installationId_example
            configurationServiceEnabled (bool): Indicates the Configuration Service is enabled for the
                installation. Required if not included in `data`. Example:
                configurationServiceEnabled_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_configuration_service(
                data={
                    "$schema": "ogg:installationConfiguration",
                    "installationId": "5b5bee89-6e93-4920-9ac7-0a5582623a2d",
                    "configurationServiceEnabled": True
                }
            )

            client.update_configuration_service(
                installationId='5b5bee89-6e93-4920-9ac7-0a5582623a2d',
                configurationServiceEnabled=True
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/installation/configuration",
            path_params=path_params,
            data=data,
            body_params={
                "installationId": installationId,
                "configurationServiceEnabled": configurationServiceEnabled,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends
    def list_configuration_service_backends(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Installation
        GET /services/{version}/installation/configuration/backends
        Required Role: Administrator
        Retrieve a list of Backends known to the Configuration Service.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_configuration_service_backends()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/installation/configuration/backends",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends
    def create_configuration_service_backend(
        self,
        id=None,
        configuration=None,
        name=None,
        replacedBy=None,
        encrypted=None,
        encryptionKey=None,
        readOnly=None,
        type=None,
        messages=None,
        locked=None,
        options=None,
        replaced=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Installation
        POST /services/{version}/installation/configuration/backends
        Required Role: Security
        Create a new Configuration Service Backend.

        Parameters:
            id (str): Unique identifier for the Backend. Example: id_example
            configuration (dict): Additional configuration data needed by the Backend. Example:
                configuration_example
            name (str): Human-friendly name for the Backend. Example: name_example
            replacedBy (str): The Backend that replaced this backend. Example: replacedBy_example
            encrypted (bool): If true, data is encrypted at rest in the Backend. Example: encrypted_example
            encryptionKey (str): The key to use for encrypting data in the Backend; if not specified, a
                random key will be generated. Example: encryptionKey_example
            readOnly (bool): This Backend does not accept any requests that modify data. Example:
                readOnly_example
            type (str): The type of the Backend. Example: type_example
            messages (list): Oracle GoldenGate messages issued during the request. Example: messages_example
            locked (bool): This Backend does not accept any requests. Example: locked_example
            options (list): Configuration options for the Backend. Example: options_example
            replaced (list): The Backends that this backend replaced. Example: replaced_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_configuration_service_backend(
                data={
                    "$schema": "config:backend",
                    "id": "24d9565c-3f4d-49ea-9b1e-61df05c368c3",
                    "name": "Temporary",
                    "type": "Memory"
                }
            )

            client.create_configuration_service_backend(
                id='24d9565c-3f4d-49ea-9b1e-61df05c368c3',
                configuration=None,
                name='Temporary',
                replacedBy=None,
                encrypted=None,
                encryptionKey=None,
                readOnly=None,
                type='Memory',
                messages=[
                    {
                        "type": None,
                        "title": None,
                        "code": None,
                        "severity": None,
                        "issued": None
                    }
                ],
                locked=None,
                options=[
                    None
                ],
                replaced=[
                    None
                ]
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/installation/configuration/backends",
            path_params=path_params,
            data=data,
            body_params={
                "id": id,
                "configuration": configuration,
                "name": name,
                "replacedBy": replacedBy,
                "encrypted": encrypted,
                "encryptionKey": encryptionKey,
                "readOnly": readOnly,
                "type": type,
                "messages": messages,
                "locked": locked,
                "options": options,
                "replaced": replaced,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends/{backend}
    def get_configuration_service_backend(
        self,
        backend,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Installation
        GET /services/{version}/installation/configuration/backends/{backend}
        Required Role: Administrator
        Retrieve the details for the Backend identified by {backend}

        Parameters:
            backend (str): Identifier for a Configuration Service Backend. Required. Example:
                backend_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_configuration_service_backend(
                backend='backend_example'
            )
        """
        path_params = {
            "backend": backend,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/installation/configuration/backends/{backend}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends/{backend}
    def update_configuration_service_backend(
        self,
        backend,
        patches=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Installation
        PATCH /services/{version}/installation/configuration/backends/{backend}
        Required Role: Security
        Update the Configuration Service Backend with one or more JSON Patch operations.

        Parameters:
            backend (str): Identifier for a Configuration Service Backend. Required. Example:
                backend_example
            patches (list): Required if not included in `data`. Example: patches_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_configuration_service_backend(
                backend='backend_example',
                data={
                    "$schema": "type:jsonPatch",
                    "patches": [
                        {
                            "op": "replace",
                            "path": "/name",
                            "value": "In-Memory"
                        }
                    ]
                }
            )

            client.update_configuration_service_backend(
                backend='backend_example',
                patches=[
                    {
                        "op": "replace",
                        "path": "/name",
                        "value": "In-Memory"
                    }
                ]
            )
        """
        path_params = {
            "backend": backend,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/installation/configuration/backends/{backend}",
            path_params=path_params,
            data=data,
            body_params={
                "patches": patches,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends/{backend}
    def delete_configuration_service_backend(
        self,
        backend,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Installation
        DELETE /services/{version}/installation/configuration/backends/{backend}
        Required Role: Security
        The DELETE operation will remove the reference to the Backend identified by {backend}.

        Parameters:
            backend (str): Identifier for a Configuration Service Backend. Required. Example:
                backend_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_configuration_service_backend(
                backend='backend_example'
            )
        """
        path_params = {
            "backend": backend,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/installation/configuration/backends/{backend}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends/{backend}/actions/replaces
    def replace_configuration_service_backend(
        self,
        backend,
        id=None,
        configuration=None,
        name=None,
        replacedBy=None,
        encrypted=None,
        encryptionKey=None,
        readOnly=None,
        type=None,
        messages=None,
        locked=None,
        options=None,
        replaced=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Installation
        POST /services/{version}/installation/configuration/backends/{backend}/actions/replaces
        Required Role: Security
        Replace another backend with this backend.

        Parameters:
            backend (str): Identifier for a Configuration Service Backend. Required. Example:
                backend_example
            id (str): Unique identifier for the Backend. Example: id_example
            configuration (dict): Additional configuration data needed by the Backend. Example:
                configuration_example
            name (str): Human-friendly name for the Backend. Example: name_example
            replacedBy (str): The Backend that replaced this backend. Example: replacedBy_example
            encrypted (bool): If true, data is encrypted at rest in the Backend. Example: encrypted_example
            encryptionKey (str): The key to use for encrypting data in the Backend; if not specified, a
                random key will be generated. Example: encryptionKey_example
            readOnly (bool): This Backend does not accept any requests that modify data. Example:
                readOnly_example
            type (str): The type of the Backend. Example: type_example
            messages (list): Oracle GoldenGate messages issued during the request. Example: messages_example
            locked (bool): This Backend does not accept any requests. Example: locked_example
            options (list): Configuration options for the Backend. Example: options_example
            replaced (list): The Backends that this backend replaced. Example: replaced_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.replace_configuration_service_backend(
                backend='backend_example',
                data={
                    "$schema": "config:backend",
                    "id": "47ce3867-b4d3-413b-aafa-42649872fe54"
                }
            )

            client.replace_configuration_service_backend(
                backend='backend_example',
                id='47ce3867-b4d3-413b-aafa-42649872fe54',
                configuration=None,
                name=None,
                replacedBy=None,
                encrypted=None,
                encryptionKey=None,
                readOnly=None,
                type=None,
                messages=[
                    {
                        "type": None,
                        "title": None,
                        "code": None,
                        "severity": None,
                        "issued": None
                    }
                ],
                locked=None,
                options=[
                    None
                ],
                replaced=[
                    None
                ]
            )
        """
        path_params = {
            "backend": backend,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/installation/configuration/backends/{backend}/actions/replaces",
            path_params=path_params,
            data=data,
            body_params={
                "id": id,
                "configuration": configuration,
                "name": name,
                "replacedBy": replacedBy,
                "encrypted": encrypted,
                "encryptionKey": encryptionKey,
                "readOnly": readOnly,
                "type": type,
                "messages": messages,
                "locked": locked,
                "options": options,
                "replaced": replaced,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/deployments
    def list_installation_deployments(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Installation
        GET /services/{version}/installation/deployments
        Required Role: Any
        Retrieve a list of all Oracle GoldenGate deployments for the installation.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_deployments(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/installation/deployments",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/plugins
    def list_installation_plugins(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Plugin Management
        GET /services/{version}/installation/plugins
        Required Role: Administrator
        Retrieve the collection of plugins available to this installation.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_plugins()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/installation/plugins",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/plugins/{plugin}
    def get_installation_plugin(
        self,
        plugin,
        version='v2',
        raw_response=False
    ):
        """
        Service Manager/Plugin Management
        GET /services/{version}/installation/plugins/{plugin}
        Required Role: Administrator
        Retrieve the details for an installation plugin.

        Parameters:
            plugin (str): Name of the plugin. Required. Example: plugin_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_installation_plugin(
                plugin='plugin_example'
            )
        """
        path_params = {
            "plugin": plugin,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/installation/plugins/{plugin}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/services
    def list_installation_services(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Installation
        GET /services/{version}/installation/services
        Required Role: User
        Retrieve a list of all Oracle GoldenGate services for the installation.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_services(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/installation/services",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs
    def list_logs(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Logs
        GET /services/{version}/logs
        Required Role: User
        Retrieve the collection of available logs.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_logs(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/logs",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/events
    def list_log_events(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Logs
        GET /services/{version}/logs/events
        Required Role: Administrator
        This endpoint provides a log of all critical events that occur in replication processes.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_log_events()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/logs/events",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/{log}
    def get_log(
        self,
        log,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Logs
        GET /services/{version}/logs/{log}
        Required Role: Administrator
        Retrieve an application log

        Parameters:
            log (str): Name of the log. Required. Example: log_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_log(
                log='log_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "log": log,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/logs/{log}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/{log}
    def update_log(
        self,
        log,
        enabled=None,
        dataExists=None,
        data=None,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Logs
        PATCH /services/{version}/logs/{log}
        Required Role: Administrator
        Update application log properties.
        Not all logs can be modified, and if a PATCH operation is issued for a read-only log a status code of
            400 Bad Request is returned.

        Parameters:
            log (str): Name of the log. Required. Example: log_example
            enabled (bool): True if the application log is enabled. Required if not included in `data`.
                Example: enabled_example
            dataExists (bool): True if data exists for the application log. Example: dataExists_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_log(
                log='log_example',
                ogg_service='adminsrvr',
                data={
                    "enabled": True
                }
            )

            client.update_log(
                log='log_example',
                ogg_service='adminsrvr',
                enabled=True,
                dataExists=None
            )
        """
        path_params = {
            "log": log,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/logs/{log}",
            path_params=path_params,
            data=data,
            body_params={
                "enabled": enabled,
                "dataExists": dataExists,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/{log}
    def delete_log(
        self,
        log,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Logs
        DELETE /services/{version}/logs/{log}
        Required Role: Administrator
        Clear the contents of an application log.
        Not all logs can be modified, and if a DELETE operation is issued for a read-only log a status code of
            400 Bad Request is returned.

        Parameters:
            log (str): Name of the log. Required. Example: log_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_log(
                log='log_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "log": log,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/logs/{log}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey
    def list_master_key_versions(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Master Keys
        GET /services/{version}/masterkey
        Required Role: User
        Retrieve all versions of the Master Key

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_master_key_versions()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/masterkey",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey
    def create_master_key_version(
        self,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Master Keys
        POST /services/{version}/masterkey
        Required Role: Administrator
        Create a new Master Key version

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_master_key_version()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/masterkey",
            path_params=path_params,
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def get_master_key_version(
        self,
        keyVersion,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Master Keys
        GET /services/{version}/masterkey/{keyVersion}
        Required Role: User
        Retrieve a Master Key by version.

        Parameters:
            keyVersion (int): The Master Key version number, 1 to 32767. Required. Example: 1
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_master_key_version(
                keyVersion=1
            )
        """
        path_params = {
            "keyVersion": keyVersion,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/masterkey/{keyVersion}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def update_master_key_version(
        self,
        keyVersion,
        created=None,
        status=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Master Keys
        PATCH /services/{version}/masterkey/{keyVersion}
        Required Role: Administrator
        Update a Master Key version

        Parameters:
            keyVersion (int): The Master Key version number, 1 to 32767. Required. Example: 1
            created (str):  Example: created_example
            status (str): Required if not included in `data`. Example: status_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_master_key_version(
                keyVersion=1,
                data={
                    "status": "unavailable"
                }
            )

            client.update_master_key_version(
                keyVersion=1,
                created=None,
                status='unavailable'
            )
        """
        path_params = {
            "keyVersion": keyVersion,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/masterkey/{keyVersion}",
            path_params=path_params,
            data=data,
            body_params={
                "created": created,
                "status": status,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def delete_master_key_version(
        self,
        keyVersion,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Master Keys
        DELETE /services/{version}/masterkey/{keyVersion}
        Required Role: Administrator
        Delete a Master Key version

        Parameters:
            keyVersion (int): The Master Key version number, 1 to 32767. Required. Example: 1
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_master_key_version(
                keyVersion=1
            )
        """
        path_params = {
            "keyVersion": keyVersion,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/masterkey/{keyVersion}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/messages
    def list_messages(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Messages
        GET /services/{version}/messages
        Required Role: User
        Retrieve messages from the Oracle GoldenGate deployment.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_messages()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/messages",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/metadata-catalog
    def get_metadata_catalog(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/REST API Catalog
        GET /services/{version}/metadata-catalog
        Required Role: Any
        The REST API catalog contains information about resources provided by each Oracle GoldenGate Service.
            Use this endpoint to retrieve a collection of all items in the catalog.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_metadata_catalog(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/metadata-catalog",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/metadata-catalog/{resource}
    def get_metadata_catalog_resource(
        self,
        resource,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/REST API Catalog
        GET /services/{version}/metadata-catalog/{resource}
        Required Role: Any
        Use this endpoint to describe a single item in the metadata catalog. A list of items in the metadata
            catalog is obtained using the Retrieve Catalog endpoint.

        Parameters:
            resource (str): Name of the item in the metadata catalog. Required. Example: resource_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_metadata_catalog_resource(
                resource='resource_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "resource": resource,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/metadata-catalog/{resource}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/commands
    def list_monitoring_commands(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Commands
        GET /services/{version}/monitoring/commands
        Required Role: User
        Retrieve the list of commands

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_monitoring_commands()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/monitoring/commands",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/commands/execute
    def execute_monitoring_command(
        self,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Commands
        POST /services/{version}/monitoring/commands/execute
        Required Role: Operator
        Execute a command

        Parameters:
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.execute_monitoring_command(
                data={
                    "name": "purgeDatastore",
                    "daysValue": "90"
                }
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/monitoring/commands/execute",
            path_params=path_params,
            data=data,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/lastMessageId
    def get_last_monitoring_message_id(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Last Message Number
        GET /services/{version}/monitoring/lastMessageId
        Required Role: User
        Retrieve an existing Last message id number

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_last_monitoring_message_id()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/monitoring/lastMessageId",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/lastStatusChangeId
    def get_last_status_change_id(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Last Status Change Id Number
        GET /services/{version}/monitoring/lastStatusChangeId
        Required Role: User
        Retrieve an existing Last status change id number

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_last_status_change_id()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/monitoring/lastStatusChangeId",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/messages
    def get_monitoring_messages(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Messages
        GET /services/{version}/monitoring/messages
        Required Role: User
        Retrieve an existing Process Messages

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_monitoring_messages()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/monitoring/messages",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/statusChanges
    def list_status_changes(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Status Changes
        GET /services/{version}/monitoring/statusChanges
        Required Role: User
        Retrieve an existing Process Status Changes

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_status_changes()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/monitoring/statusChanges",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/{item}/messages
    def list_process_messages(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Messages
        GET /services/{version}/monitoring/{item}/messages
        Required Role: User
        Retrieve an existing Process Messages

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_process_messages(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/monitoring/{item}/messages",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/{item}/statusChanges
    def list_process_status_changes(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Status Changes
        GET /services/{version}/monitoring/{item}/statusChanges
        Required Role: User
        Retrieve an existing Process Status Changes

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_process_status_changes(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/monitoring/{item}/statusChanges",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/processes
    def list_processes(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/processes
        Required Role: User
        Retrieve an existing Process Information

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_processes()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/processes",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/batchSqlStatistics
    def get_process_batch_sql_statistics(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/batchSqlStatistics
        Required Role: User
        Retrieve an existing Integrated Replicat Batch SQL Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_batch_sql_statistics(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/batchSqlStatistics",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brExtantObjectAges
    def get_process_br_extant_object_ages(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brExtantObjectAges
        Required Role: User
        Retrieve an existing Bounded Recovery Extant Object Ages Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_extant_object_ages(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/brExtantObjectAges",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brExtantObjectSizes
    def get_process_br_extant_object_sizes(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brExtantObjectSizes
        Required Role: User
        Retrieve an existing Bounded Recovery Extant Object Sizes Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_extant_object_sizes(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/brExtantObjectSizes",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brObjectAges
    def get_process_br_object_ages(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brObjectAges
        Required Role: User
        Retrieve an existing Bounded Recovery Object Ages Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_object_ages(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/brObjectAges",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brObjectSizes
    def get_process_br_object_sizes(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brObjectSizes
        Required Role: User
        Retrieve an existing Bounded Recovery Object Sizes Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_object_sizes(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/brObjectSizes",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brPoolsInfo
    def get_process_br_pools_info(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brPoolsInfo
        Required Role: User
        Retrieve an existing Bounded Recovery Object Pool Information

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_pools_info(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/brPoolsInfo",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brStatus
    def get_process_br_status(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brStatus
        Required Role: User
        Retrieve an existing Bounded Recovery Status

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_status(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/brStatus",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/cacheStatistics
    def get_process_cache_statistics(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/cacheStatistics
        Required Role: User
        Retrieve an existing Cache Manager Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_cache_statistics(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/cacheStatistics",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/configurationEr
    def get_er_configuration(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/ER Metrics
        GET /services/{version}/mpoints/{item}/configurationEr
        Required Role: User
        Retrieve an existing Basic Configuration Information for Extract and Replicat

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_er_configuration(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/configurationEr",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/configurationManager
    def get_manager_configuration(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/ER Metrics
        GET /services/{version}/mpoints/{item}/configurationManager
        Required Role: User
        Retrieve an existing Basic Configuration Information for Manager and Services

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_manager_configuration(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/configurationManager",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/coordinationReplicat
    def get_process_coordination_replicat(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/coordinationReplicat
        Required Role: User
        Retrieve an existing Coordinated Replicat Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_coordination_replicat(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/coordinationReplicat",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/currentInflightTransactions
    def get_current_inflight_transactions(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/currentInflightTransactions
        Required Role: User
        Retrieve an existing In Flight Transaction Information

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_current_inflight_transactions(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/currentInflightTransactions",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/databaseInOut
    def get_process_database_in_out(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/databaseInOut
        Required Role: User
        Retrieve an existing Database Information

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_database_in_out(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/databaseInOut",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/dependencyStats
    def get_process_dependency_stats(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/dependencyStats
        Required Role: User
        Retrieve an existing Statistics about dependencies

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_dependency_stats(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/dependencyStats",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrChunkStats
    def get_process_distsrvr_chunk_stats(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrChunkStats
        Required Role: User
        Retrieve an existing Distribution Service Chunk Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_distsrvr_chunk_stats(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/distsrvrChunkStats",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrNetworkStats
    def get_process_distsrvr_network_stats(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrNetworkStats
        Required Role: User
        Retrieve an existing Distribution Service Network Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_distsrvr_network_stats(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/distsrvrNetworkStats",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrPathStats
    def get_process_distsrvr_path_stats(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrPathStats
        Required Role: User
        Retrieve an existing Distribution Service Path Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_distsrvr_path_stats(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/distsrvrPathStats",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrTableStats
    def get_process_distsrvr_table_stats(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrTableStats
        Required Role: User
        Retrieve an existing Distribution Service Table Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_distsrvr_table_stats(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/distsrvrTableStats",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/heartbeat
    def get_process_heartbeat(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Heartbeat Metrics
        GET /services/{version}/mpoints/{item}/heartbeat
        Required Role: User
        Retrieve an existing Heartbeat timings

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_heartbeat(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/heartbeat",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/networkStatistics
    def get_process_network_statistics(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/networkStatistics
        Required Role: User
        Retrieve an existing Network Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_network_statistics(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/networkStatistics",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/parallelReplicat
    def get_process_parallel_replicat(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/parallelReplicat
        Required Role: User
        Retrieve an existing Parallel Replicat Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_parallel_replicat(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/parallelReplicat",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrProcStats
    def get_process_pmsrvr_proc_stats(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrProcStats
        Required Role: User
        Retrieve an existing Performance Metrics Service Monitored Process Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_pmsrvr_proc_stats(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/pmsrvrProcStats",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrStats
    def get_process_pmsrvr_stats(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrStats
        Required Role: User
        Retrieve an existing Performance Metrics Service Collector Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_pmsrvr_stats(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/pmsrvrStats",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrWorkerStats
    def get_process_pmsrvr_worker_stats(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrWorkerStats
        Required Role: User
        Retrieve an existing Performance Metrics Service Worker Thread Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_pmsrvr_worker_stats(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/pmsrvrWorkerStats",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/positionEr
    def get_process_position_er(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/ER Metrics
        GET /services/{version}/mpoints/{item}/positionEr
        Required Role: User
        Retrieve an existing Checkpoint Position Information

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_position_er(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/positionEr",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/process
    def get_process_info(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/process
        Required Role: User
        Retrieve an existing Process Information

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_info(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/process",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/processPerformance
    def get_process_performance(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/processPerformance
        Required Role: User
        Retrieve an existing Process Performance Resource Utilization Information

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_performance(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/processPerformance",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/queueBucketStatistics
    def get_process_queue_bucket_statistics(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/queueBucketStatistics
        Required Role: User
        Retrieve an existing Queue Bucket Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_queue_bucket_statistics(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/queueBucketStatistics",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/queueStatistics
    def get_process_queue_statistics(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/queueStatistics
        Required Role: User
        Retrieve an existing Queue Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_queue_statistics(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/queueStatistics",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/recvsrvrStats
    def get_process_recvsrvr_stats(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/recvsrvrStats
        Required Role: User
        Retrieve an existing Receiver Service Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_recvsrvr_stats(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/recvsrvrStats",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/serviceHealth
    def get_process_service_health(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/serviceHealth
        Required Role: User
        Retrieve an existing Service Health

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_service_health(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/serviceHealth",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsExtract
    def get_process_statistics_extract(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsExtract
        Required Role: User
        Retrieve an existing Extract Database Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_extract(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsExtract",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsProcedureExtract
    def get_process_statistics_procedure_extract(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsProcedureExtract
        Required Role: User
        Retrieve an existing Extract Database Statistics by Procedure Feature

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_procedure_extract(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsProcedureExtract",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsProcedureReplicat
    def get_process_statistics_procedure_replicat(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsProcedureReplicat
        Required Role: User
        Retrieve an existing Database Statistics by Procedure Feature

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_procedure_replicat(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsProcedureReplicat",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsReplicat
    def get_process_statistics_replicat(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsReplicat
        Required Role: User
        Retrieve an existing Replicat Database Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_replicat(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsReplicat",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsTableExtract
    def get_process_statistics_table_extract(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsTableExtract
        Required Role: User
        Retrieve an existing Extract Database Statistics by Table

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_table_extract(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsTableExtract",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsTableReplicat
    def get_process_statistics_table_replicat(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsTableReplicat
        Required Role: User
        Retrieve an existing Replicat Database Statistics by Table

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_table_replicat(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsTableReplicat",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/superpoolStatistics
    def get_process_superpool_statistics(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/superpoolStatistics
        Required Role: User
        Retrieve an existing Super Pool Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_superpool_statistics(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/superpoolStatistics",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/threadPerformance
    def get_process_thread_performance(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/threadPerformance
        Required Role: User
        Retrieve an existing Process Thread Resource Utilization Information

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_thread_performance(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/threadPerformance",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/trailInput
    def get_process_trail_input(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/trailInput
        Required Role: User
        Retrieve an existing Input Trail File Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_trail_input(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/trailInput",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/trailOutput
    def get_process_trail_output(
        self,
        item,
        version='v2',
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/trailOutput
        Required Role: User
        Retrieve an existing Output Trail File Statistics

        Parameters:
            item (str): Required. Example: item_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_trail_output(
                item='item_example'
            )
        """
        path_params = {
            "item": item,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/trailOutput",
            path_params=path_params,
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/oggerr
    def list_ogg_errors(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Message Codes
        GET /services/{version}/oggerr
        Required Role: Any
        Retrieve all message codes from the Oracle GoldenGate deployment.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_ogg_errors(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/oggerr",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/oggerr/{message}
    def get_ogg_error_info(
        self,
        message,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Message Codes
        GET /services/{version}/oggerr/{message}
        Required Role: Any
        Retrieve a detailed explanation for an Oracle GoldenGate message.

        Parameters:
            message (str): The Oracle GoldenGate Message Code, OGG-99999. Required. Example: message_example
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_ogg_error_info(
                message='message_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "message": message,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/oggerr/{message}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/parameters
    def list_parameters(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Parameters
        GET /services/{version}/parameters
        Required Role: Any
        Retrieve names of all known OGG parameters.

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_parameters()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/parameters",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/parameters/{parameter}
    def get_parameter_info(
        self,
        parameter,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Parameters
        GET /services/{version}/parameters/{parameter}
        Required Role: Any
        Retrieve details for a parameter.

        Parameters:
            parameter (str): Name of parameter for information request. Required. Example: parameter_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_parameter_info(
                parameter='parameter_example'
            )
        """
        path_params = {
            "parameter": parameter,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/parameters/{parameter}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats
    def list_replicats(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats
        Required Role: User
        Retrieve the collection of Replicat processes

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_replicats()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def get_replicat(
        self,
        replicat,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}
        Required Role: User
        Retrieve the details of an replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat(
                replicat='replicat_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def create_replicat(
        self,
        replicat,
        begin=None,
        config=None,
        synchronized=None,
        mode=None,
        encryptionProfile=None,
        status=None,
        critical=None,
        managedProcessSettings=None,
        intent=None,
        checkpoint=None,
        registration=None,
        source=None,
        credentials=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Replicats
        POST /services/{version}/replicats/{replicat}
        Required Role: Administrator
        Create a new replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            begin (dict): Starting point for data processing. Example: begin_example
            config (list):  Example: config_example
            synchronized (bool): Indicates that the Replicat is stopped in a synchronized state. Example:
                synchronized_example
            mode (dict): Mode of replication. Example: mode_example
            encryptionProfile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the replicat is critical to the deployment. Example: critical_example
            managedProcessSettings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            intent (str): Intent for data capture workflow. Example: intent_example
            checkpoint (dict): Location for checkpoint data. Example: checkpoint_example
            registration (str): Registration with the target database. Example: registration_example
            source (dict): Source of data to process. Example: source_example
            credentials (dict): Credentials for target database. Example: credentials_example
            description (str): Description for the process. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_replicat(
                replicat='replicat_example',
                data={
                    "mode": {
                        "type": "integrated"
                    },
                    "credentials": {
                        "alias": "ggsouth"
                    },
                    "config": [
                        "Replicat    reps",
                        "UseridAlias ggsouth",
                        "Map         hr.*,",
                        "  Target    hr.*;"
                    ],
                    "source": {
                        "name": "ea",
                        "path": "ggnorth/"
                    },
                    "checkpoint": {
                        "table": "ggadmin.ggs_checkpoint"
                    }
                }
            )

            client.create_replicat(
                replicat='replicat_example',
                begin=None,
                config=[
                    "Replicat    reps",
                    "UseridAlias ggsouth",
                    "Map         hr.*,",
                    "  Target    hr.*;"
                ],
                synchronized=None,
                mode={
                    "type": "integrated"
                },
                encryptionProfile=None,
                status=None,
                critical=None,
                managedProcessSettings=None,
                intent=None,
                checkpoint={
                    "table": "ggadmin.ggs_checkpoint"
                },
                registration=None,
                source={
                    "name": "ea",
                    "path": "ggnorth/"
                },
                credentials={
                    "alias": "ggsouth"
                },
                description=None
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/replicats/{replicat}",
            path_params=path_params,
            data=data,
            body_params={
                "begin": begin,
                "config": config,
                "synchronized": synchronized,
                "mode": mode,
                "encryptionProfile": encryptionProfile,
                "status": status,
                "critical": critical,
                "managedProcessSettings": managedProcessSettings,
                "intent": intent,
                "checkpoint": checkpoint,
                "registration": registration,
                "source": source,
                "credentials": credentials,
                "description": description,
            },
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def update_replicat(
        self,
        replicat,
        begin=None,
        config=None,
        synchronized=None,
        mode=None,
        encryptionProfile=None,
        status=None,
        critical=None,
        managedProcessSettings=None,
        intent=None,
        checkpoint=None,
        registration=None,
        source=None,
        credentials=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        PATCH /services/{version}/replicats/{replicat}
        Required Role: Operator
        Update an existing replicat process. A user with the 'Operator' role may change the "status" property.
            Any other changes require the 'Administrator' role.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            begin (dict): Starting point for data processing. Example: begin_example
            config (list):  Example: config_example
            synchronized (bool): Indicates that the Replicat is stopped in a synchronized state. Example:
                synchronized_example
            mode (dict): Mode of replication. Example: mode_example
            encryptionProfile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the replicat is critical to the deployment. Example: critical_example
            managedProcessSettings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            intent (str): Intent for data capture workflow. Example: intent_example
            checkpoint (dict): Location for checkpoint data. Example: checkpoint_example
            registration (str): Registration with the target database. Example: registration_example
            source (dict): Source of data to process. Example: source_example
            credentials (dict): Credentials for target database. Example: credentials_example
            description (str): Description for the process. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_replicat(
                replicat='replicat_example',
                data={
                    "status": "running"
                }
            )

            client.update_replicat(
                replicat='replicat_example',
                begin=None,
                config=[
                    None
                ],
                synchronized=None,
                mode=None,
                encryptionProfile=None,
                status='running',
                critical=None,
                managedProcessSettings=None,
                intent=None,
                checkpoint=None,
                registration=None,
                source=None,
                credentials=None,
                description=None
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/replicats/{replicat}",
            path_params=path_params,
            data=data,
            body_params={
                "begin": begin,
                "config": config,
                "synchronized": synchronized,
                "mode": mode,
                "encryptionProfile": encryptionProfile,
                "status": status,
                "critical": critical,
                "managedProcessSettings": managedProcessSettings,
                "intent": intent,
                "checkpoint": checkpoint,
                "registration": registration,
                "source": source,
                "credentials": credentials,
                "description": description,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def delete_replicat(
        self,
        replicat,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        DELETE /services/{version}/replicats/{replicat}
        Required Role: Administrator
        Delete a replicat process. If the replicat process is currently running, it is stopped first.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_replicat(
                replicat='replicat_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/replicats/{replicat}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/command
    def execute_command_replicat(
        self,
        replicat,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        POST /services/{version}/replicats/{replicat}/command
        Required Role: User
        Execute a Replicat process command

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.execute_command_replicat(
                replicat='replicat_example',
                data={
                    "command": "STATS",
                    "arguments": "HOURLY"
                }
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/replicats/{replicat}/command",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info
    def get_replicat_info(
        self,
        replicat,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info
        Required Role: User
        Retrieve types of information available for a replicat.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_info(
                replicat='replicat_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/checkpoints
    def get_replicat_checkpoint(
        self,
        replicat,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/checkpoints
        Required Role: User
        Retrieve the checkpoint information for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_checkpoint(
                replicat='replicat_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/checkpoints",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/diagnostics
    def list_replicat_diagnostics(
        self,
        replicat,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/diagnostics
        Required Role: User
        Retrieve the list of diagnostic results available for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_replicat_diagnostics(
                replicat='replicat_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/diagnostics",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/diagnostics/{diagnostic}
    def get_replicat_diagnostic(
        self,
        replicat,
        diagnostic,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/diagnostics/{diagnostic}
        Required Role: User
        Retrieve a diagnostics result for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            diagnostic (str): The name of the diagnostic results, which is the replicat name and
                '.diagnostics', followed by an optional revision number. Required. Example:
                diagnostic_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_diagnostic(
                replicat='replicat_example',
                diagnostic='diagnostic_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "diagnostic": diagnostic,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/diagnostics/{diagnostic}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/history
    def get_replicat_history(
        self,
        replicat,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/history
        Required Role: User
        Retrieve the execution history of a managed replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_history(
                replicat='replicat_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/history",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/logs
    def list_replicat_logs(
        self,
        replicat,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/logs
        Required Role: User
        Retrieve the list of logs available for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_replicat_logs(
                replicat='replicat_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/logs",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/logs/{log}
    def get_replicat_log(
        self,
        replicat,
        log,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/logs/{log}
        Required Role: Administrator
        Retrieve a log from the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            log (str): The name of the log, which is the replicat name, followed by an optional revision
                number(as -number) and '.log'. Required. Example: log_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_log(
                replicat='replicat_example',
                log='log_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "log": log,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/logs/{log}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/reports
    def list_replicat_reports(
        self,
        replicat,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/reports
        Required Role: User
        Retrieve the list of reports available for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_replicat_reports(
                replicat='replicat_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/reports",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/reports/{report}
    def get_replicat_report(
        self,
        replicat,
        report,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/reports/{report}
        Required Role: User
        Retrieve a report from the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            report (str): The name of the report, which is the replicat name, followed by an optional
                revision number and '.rpt'. Required. Example: report_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_report(
                replicat='replicat_example',
                report='report_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "report": report,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/reports/{report}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/status
    def get_replicat_status(
        self,
        replicat,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/status
        Required Role: User
        Retrieve the current status of the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_status(
                replicat='replicat_example'
            )
        """
        path_params = {
            "replicat": replicat,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/status",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/requests
    def list_restapi_requests(
        self,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Requests
        GET /services/{version}/requests
        Required Role: Administrator
        Retrieve the collection of background REST API requests.

        Parameters:
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_restapi_requests(
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/requests",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/requests/{request}
    def get_restapi_request_status(
        self,
        request,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Requests
        GET /services/{version}/requests/{request}
        Required Role: User
        Retrieve the background request status.

        Parameters:
            request (int): Identifier for background request. Required. Example: 1
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_restapi_request_status(
                request=1,
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "request": request,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/requests/{request}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/requests/{request}/result
    def get_restapi_request_result(
        self,
        request,
        version='v2',
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Requests
        GET /services/{version}/requests/{request}/result
        Required Role: User
        Retrieve the background request result.

        Parameters:
            request (int): Identifier for background request. Required. Example: 1
            version (str): Defaults to v2. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_restapi_request_result(
                request=1,
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "request": request,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/requests/{request}/result",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources
    def list_distribution_paths(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources
        Required Role: User
        Get a list of distribution paths

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_distribution_paths()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/sources",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def get_distribution_paths(
        self,
        distpath,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path

        Parameters:
            distpath (str): Required. Example: distpath_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_distribution_paths(
                distpath='distpath_example'
            )
        """
        path_params = {
            "distpath": distpath,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/sources/{distpath}",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def create_distribution_paths(
        self,
        distpath,
        begin=None,
        name=None,
        encryptionProfile=None,
        status=None,
        targetInitiated=None,
        ruleset=None,
        source=None,
        target=None,
        options=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Distribution Service
        POST /services/{version}/sources/{distpath}
        Required Role: Administrator
        Create a new Oracle GoldenGate Distribution Path

        Parameters:
            distpath (str): Required. Example: distpath_example
            begin (dict): Starting point for data processing. Example: begin_example
            name (str): distribution path name. Example: name_example
            encryptionProfile (str): Name of 'ogg:encryptionProfile' value. Example:
                encryptionProfile_example
            status (dict): Oracle GoldenGate Distribution Path Status. Example: status_example
            targetInitiated (bool): Whether the target endpoint initiates the path. If true, the path needs
                to be created and modified through Receiver Server, who initiates the connection with
                Distribution Server. Otherwise, this behavior is reversed. Example: targetInitiated_example
            ruleset (dict):  Example: ruleset_example
            source (dict): source endpoint of the path. Example: source_example
            target (dict): target endpoint of the path. Example: target_example
            options (dict): options for the distribution path. Example: options_example
            description (str): Description for the path. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_distribution_paths(
                distpath='distpath_example',
                data={
                    "$schema": "ogg:distPath",
                    "name": "path1",
                    "description": "my test distPath",
                    "source": {
                        "uri": "trail://localhost:9012/dirdat/a1"
                    },
                    "target": {
                        "uri": "ogg://adc00oye:9013/dirdat/t1"
                    },
                    "begin": {
                        "sequence": "0",
                        "offset": "0"
                    },
                    "status": "running"
                }
            )

            client.create_distribution_paths(
                distpath='distpath_example',
                begin={
                    "sequence": "0",
                    "offset": "0"
                },
                name='path1',
                encryptionProfile=None,
                status='running',
                targetInitiated=None,
                ruleset=None,
                source={
                    "uri": "trail://localhost:9012/dirdat/a1"
                },
                target={
                    "uri": "ogg://adc00oye:9013/dirdat/t1"
                },
                options={
                    "tcpSourceTimer": None,
                    "reportCount": {
                        "measurementUnit": None,
                        "count": None,
                        "rate": None
                    },
                    "network": {
                        "socketOptions": None,
                        "appOptions": {
                            "appFlushBytes": None,
                            "appFlushSecs": None
                        }
                    },
                    "streaming": None,
                    "critical": None,
                    "autoRestart": {
                        "retries": None,
                        "delay": None
                    },
                    "eofDelayCSecs": None,
                    "checkpointFrequency": None
                },
                description='my test distPath'
            )
        """
        path_params = {
            "distpath": distpath,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/sources/{distpath}",
            path_params=path_params,
            data=data,
            body_params={
                "begin": begin,
                "name": name,
                "encryptionProfile": encryptionProfile,
                "status": status,
                "targetInitiated": targetInitiated,
                "ruleset": ruleset,
                "source": source,
                "target": target,
                "options": options,
                "description": description,
            },
            ogg_service="distsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def update_distribution_paths(
        self,
        distpath,
        begin=None,
        name=None,
        encryptionProfile=None,
        status=None,
        targetInitiated=None,
        ruleset=None,
        source=None,
        target=None,
        options=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        PATCH /services/{version}/sources/{distpath}
        Required Role: Operator
        Update an Existing Distribution PathTo update an existing distribution path a user needs the
            Administrator role. However, a user with the Operator role is allowed change the status property.

        Parameters:
            distpath (str): Required. Example: distpath_example
            begin (dict): Starting point for data processing. Example: begin_example
            name (str): distribution path name. Example: name_example
            encryptionProfile (str): Name of 'ogg:encryptionProfile' value. Example:
                encryptionProfile_example
            status (dict): Oracle GoldenGate Distribution Path Status. Example: status_example
            targetInitiated (bool): Whether the target endpoint initiates the path. If true, the path needs
                to be created and modified through Receiver Server, who initiates the connection with
                Distribution Server. Otherwise, this behavior is reversed. Example: targetInitiated_example
            ruleset (dict):  Example: ruleset_example
            source (dict): source endpoint of the path. Example: source_example
            target (dict): target endpoint of the path. Example: target_example
            options (dict): options for the distribution path. Example: options_example
            description (str): Description for the path. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_distribution_paths(
                distpath='distpath_example',
                data={
                    "$schema": "ogg:distPath",
                    "status": "stopped"
                }
            )

            client.update_distribution_paths(
                distpath='distpath_example',
                begin=None,
                name=None,
                encryptionProfile=None,
                status='stopped',
                targetInitiated=None,
                ruleset=None,
                source={
                    "description": None,
                    "uri": None,
                    "proxy": {
                        "uri": None,
                        "type": None,
                        "csAlias": None,
                        "csDomain": None
                    },
                    "details": {},
                    "isDynamicOggPort": None,
                    "authenticationMethod": None
                },
                target={
                    "description": None,
                    "uri": None,
                    "proxy": {
                        "uri": None,
                        "type": None,
                        "csAlias": None,
                        "csDomain": None
                    },
                    "details": {},
                    "isDynamicOggPort": None,
                    "authenticationMethod": None
                },
                options={
                    "tcpSourceTimer": None,
                    "reportCount": {
                        "measurementUnit": None,
                        "count": None,
                        "rate": None
                    },
                    "network": {
                        "socketOptions": None,
                        "appOptions": {
                            "appFlushBytes": None,
                            "appFlushSecs": None
                        }
                    },
                    "streaming": None,
                    "critical": None,
                    "autoRestart": {
                        "retries": None,
                        "delay": None
                    },
                    "eofDelayCSecs": None,
                    "checkpointFrequency": None
                },
                description=None
            )
        """
        path_params = {
            "distpath": distpath,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/sources/{distpath}",
            path_params=path_params,
            data=data,
            body_params={
                "begin": begin,
                "name": name,
                "encryptionProfile": encryptionProfile,
                "status": status,
                "targetInitiated": targetInitiated,
                "ruleset": ruleset,
                "source": source,
                "target": target,
                "options": options,
                "description": description,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def delete_distribution_paths(
        self,
        distpath,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        DELETE /services/{version}/sources/{distpath}
        Required Role: Administrator
        Delete an existing Oracle GoldenGate Distribution Path

        Parameters:
            distpath (str): Required. Example: distpath_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_distribution_paths(
                distpath='distpath_example'
            )
        """
        path_params = {
            "distpath": distpath,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/sources/{distpath}",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}/checkpoints
    def get_distribution_path_checkpoint(
        self,
        distpath,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}/checkpoints
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path Checkpoints

        Parameters:
            distpath (str): Required. Example: distpath_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_distribution_path_checkpoint(
                distpath='distpath_example'
            )
        """
        path_params = {
            "distpath": distpath,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/sources/{distpath}/checkpoints",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}/info
    def get_distribution_path_info(
        self,
        distpath,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}/info
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path Information

        Parameters:
            distpath (str): Required. Example: distpath_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_distribution_path_info(
                distpath='distpath_example'
            )
        """
        path_params = {
            "distpath": distpath,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/sources/{distpath}/info",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}/stats
    def get_distribution_path_stats(
        self,
        distpath,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}/stats
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path Statistics

        Parameters:
            distpath (str): Required. Example: distpath_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_distribution_path_stats(
                distpath='distpath_example'
            )
        """
        path_params = {
            "distpath": distpath,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/sources/{distpath}/stats",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream
    def list_data_streams(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/stream
        Required Role: User
        Get a list of data stream resources

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_data_streams()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/stream",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def get_data_stream(
        self,
        streamName,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/stream/{streamName}
        Required Role: Operator
        Retrieve an existing Oracle GoldenGate Data Stream configuration

        Parameters:
            streamName (str): Required. Example: streamName_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_data_stream(
                streamName='streamName_example'
            )
        """
        path_params = {
            "streamName": streamName,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/stream/{streamName}",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def create_data_stream(
        self,
        streamName,
        tcpKeepAliveTimeout=None,
        qualityOfService=None,
        encoding=None,
        rules=None,
        source=None,
        bufferSize=None,
        cloudEventsFormat=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Distribution Service
        POST /services/{version}/stream/{streamName}
        Required Role: Administrator
        Create a new Oracle GoldenGate Data Stream configuration

        Parameters:
            streamName (str): Required. Example: streamName_example
            tcpKeepAliveTimeout (int): Timeout (seconds) for keep-alive. Example:
                tcpKeepAliveTimeout_example
            qualityOfService (str): The quality level of the data streaming service. Example:
                qualityOfService_example
            encoding (dict): data encoding method. Example: encoding_example
            rules (list):  Example: rules_example
            source (dict): source endpoint of the data stream. Required if not included in `data`. Example:
                source_example
            bufferSize (int): data buffer size in bytes before flush. Example: bufferSize_example
            cloudEventsFormat (bool): data records conform to cloudEvents format. Example:
                cloudEventsFormat_example
            description (str): Description for the data stream. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_data_stream(
                streamName='streamName_example',
                data={
                    "source": "trail://localhost:9012/services/v2/sources?trail=a1",
                    "begin": "now",
                    "$schema": "ogg:dataStream"
                }
            )

            client.create_data_stream(
                streamName='streamName_example',
                tcpKeepAliveTimeout=None,
                qualityOfService=None,
                encoding=None,
                rules=[
                    {
                        "description": None,
                        "filter": None,
                        "action": None
                    }
                ],
                source='trail://localhost:9012/services/v2/sources?trail=a1',
                bufferSize=None,
                cloudEventsFormat=None,
                description=None
            )
        """
        path_params = {
            "streamName": streamName,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/stream/{streamName}",
            path_params=path_params,
            data=data,
            body_params={
                "tcpKeepAliveTimeout": tcpKeepAliveTimeout,
                "qualityOfService": qualityOfService,
                "encoding": encoding,
                "rules": rules,
                "source": source,
                "bufferSize": bufferSize,
                "cloudEventsFormat": cloudEventsFormat,
                "description": description,
            },
            ogg_service="distsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def update_data_stream(
        self,
        streamName,
        tcpKeepAliveTimeout=None,
        qualityOfService=None,
        encoding=None,
        rules=None,
        source=None,
        bufferSize=None,
        cloudEventsFormat=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        PATCH /services/{version}/stream/{streamName}
        Required Role: Administrator
        Update an existing Oracle GoldenGate Data Stream configuration

        Parameters:
            streamName (str): Required. Example: streamName_example
            tcpKeepAliveTimeout (int): Timeout (seconds) for keep-alive. Example:
                tcpKeepAliveTimeout_example
            qualityOfService (str): The quality level of the data streaming service. Example:
                qualityOfService_example
            encoding (dict): data encoding method. Example: encoding_example
            rules (list):  Example: rules_example
            source (dict): source endpoint of the data stream. Required if not included in `data`. Example:
                source_example
            bufferSize (int): data buffer size in bytes before flush. Example: bufferSize_example
            cloudEventsFormat (bool): data records conform to cloudEvents format. Example:
                cloudEventsFormat_example
            description (str): Description for the data stream. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_data_stream(
                streamName='streamName_example',
                data={
                    "source": "trail://localhost:9012/services/v2/sources?trail=a1",
                    "begin": "earliest",
                    "$schema": "ogg:dataStream"
                }
            )

            client.update_data_stream(
                streamName='streamName_example',
                tcpKeepAliveTimeout=None,
                qualityOfService=None,
                encoding=None,
                rules=[
                    {
                        "description": None,
                        "filter": None,
                        "action": None
                    }
                ],
                source='trail://localhost:9012/services/v2/sources?trail=a1',
                bufferSize=None,
                cloudEventsFormat=None,
                description=None
            )
        """
        path_params = {
            "streamName": streamName,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/stream/{streamName}",
            path_params=path_params,
            data=data,
            body_params={
                "tcpKeepAliveTimeout": tcpKeepAliveTimeout,
                "qualityOfService": qualityOfService,
                "encoding": encoding,
                "rules": rules,
                "source": source,
                "bufferSize": bufferSize,
                "cloudEventsFormat": cloudEventsFormat,
                "description": description,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def delete_data_stream(
        self,
        streamName,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        DELETE /services/{version}/stream/{streamName}
        Required Role: Administrator
        Delete an existing Oracle GoldenGate Data Stream configuration

        Parameters:
            streamName (str): Required. Example: streamName_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_data_stream(
                streamName='streamName_example'
            )
        """
        path_params = {
            "streamName": streamName,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/stream/{streamName}",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}/info
    def get_data_stream_info(
        self,
        streamName,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/stream/{streamName}/info
        Required Role: User
        Retrieve an existing Oracle GoldenGate Data Stream Information

        Parameters:
            streamName (str): Required. Example: streamName_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_data_stream_info(
                streamName='streamName_example'
            )
        """
        path_params = {
            "streamName": streamName,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/stream/{streamName}/info",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}/info/errors
    def list_data_stream_errors(
        self,
        streamName,
        version='v2',
        raw_response=False
    ):
        """
        Data Stream Service error messages
        GET /services/{version}/stream/{streamName}/info/errors
        Required Role: User
        Retrieve the data stream service error messages if applicable

        Parameters:
            streamName (str): Required. Example: streamName_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_data_stream_errors(
                streamName='streamName_example'
            )
        """
        path_params = {
            "streamName": streamName,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/stream/{streamName}/info/errors",
            path_params=path_params,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}/yaml
    def get_data_stream_yaml(
        self,
        streamName,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/stream/{streamName}/yaml
        Required Role: User
        Retrieve the asyncapi yaml specification

        Parameters:
            streamName (str): Required. Example: streamName_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_data_stream_yaml(
                streamName='streamName_example'
            )
        """
        path_params = {
            "streamName": streamName,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/stream/{streamName}/yaml",
            path_params=path_params,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}/yaml
    def update_data_stream_yaml(
        self,
        streamName,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Distribution Service
        PATCH /services/{version}/stream/{streamName}/yaml
        Required Role: Administrator
        Update the AsyncAPI YAML specification

        Parameters:
            streamName (str): Required. Example: streamName_example
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_data_stream_yaml(
                streamName='streamName_example',
                data={})
        """
        path_params = {
            "streamName": streamName,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/stream/{streamName}/yaml",
            path_params=path_params,
            data=data,
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets
    def list_receiver_paths(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets
        Required Role: User
        Get a list of distribution paths

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_receiver_paths()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/targets",
            path_params=path_params,
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}
    def get_receiver_paths(
        self,
        path,
        version='v2',
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}
        Required Role: User
        Retrieve an existing Oracle GoldenGate Collector Path

        Parameters:
            path (str): Required. Example: path_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_paths(
                path='path_example'
            )
        """
        path_params = {
            "path": path,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/targets/{path}",
            path_params=path_params,
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}
    def create_receiver_paths(
        self,
        path,
        begin=None,
        name=None,
        encryptionProfile=None,
        status=None,
        targetInitiated=None,
        ruleset=None,
        source=None,
        target=None,
        options=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Receiver Service
        POST /services/{version}/targets/{path}
        Required Role: Administrator
        Create a new Oracle GoldenGate Collector Path

        Parameters:
            path (str): Required. Example: path_example
            begin (dict): Starting point for data processing. Example: begin_example
            name (str): distribution path name. Example: name_example
            encryptionProfile (str): Name of 'ogg:encryptionProfile' value. Example:
                encryptionProfile_example
            status (dict): Oracle GoldenGate Distribution Path Status. Example: status_example
            targetInitiated (bool): Whether the target endpoint initiates the path. If true, the path needs
                to be created and modified through Receiver Server, who initiates the connection with
                Distribution Server. Otherwise, this behavior is reversed. Example: targetInitiated_example
            ruleset (dict):  Example: ruleset_example
            source (dict): source endpoint of the path. Example: source_example
            target (dict): target endpoint of the path. Example: target_example
            options (dict): options for the distribution path. Example: options_example
            description (str): Description for the path. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_receiver_paths(
                path='path_example',
                data={
                    "$schema": "ogg:distPath",
                    "name": "path1",
                    "description": "my test distPath",
                    "source": {
                        "uri": "trail://localhost:9012/dirdat/a1"
                    },
                    "target": {
                        "uri": "ogg://adc00oye:9013/dirdat/t1"
                    },
                    "begin": {
                        "sequence": "0",
                        "offset": "0"
                    },
                    "status": "running"
                }
            )

            client.create_receiver_paths(
                path='path_example',
                begin={
                    "sequence": "0",
                    "offset": "0"
                },
                name='path1',
                encryptionProfile=None,
                status='running',
                targetInitiated=None,
                ruleset=None,
                source={
                    "uri": "trail://localhost:9012/dirdat/a1"
                },
                target={
                    "uri": "ogg://adc00oye:9013/dirdat/t1"
                },
                options={
                    "tcpSourceTimer": None,
                    "reportCount": {
                        "measurementUnit": None,
                        "count": None,
                        "rate": None
                    },
                    "network": {
                        "socketOptions": None,
                        "appOptions": {
                            "appFlushBytes": None,
                            "appFlushSecs": None
                        }
                    },
                    "streaming": None,
                    "critical": None,
                    "autoRestart": {
                        "retries": None,
                        "delay": None
                    },
                    "eofDelayCSecs": None,
                    "checkpointFrequency": None
                },
                description='my test distPath'
            )
        """
        path_params = {
            "path": path,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/targets/{path}",
            path_params=path_params,
            data=data,
            body_params={
                "begin": begin,
                "name": name,
                "encryptionProfile": encryptionProfile,
                "status": status,
                "targetInitiated": targetInitiated,
                "ruleset": ruleset,
                "source": source,
                "target": target,
                "options": options,
                "description": description,
            },
            ogg_service="recvsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}
    def update_receiver_paths(
        self,
        path,
        begin=None,
        name=None,
        encryptionProfile=None,
        status=None,
        targetInitiated=None,
        ruleset=None,
        source=None,
        target=None,
        options=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Receiver Service
        PATCH /services/{version}/targets/{path}
        Required Role: Operator
        Update an existing Oracle GoldenGate Collector Path

        Parameters:
            path (str): Required. Example: path_example
            begin (dict): Starting point for data processing. Example: begin_example
            name (str): distribution path name. Example: name_example
            encryptionProfile (str): Name of 'ogg:encryptionProfile' value. Example:
                encryptionProfile_example
            status (dict): Oracle GoldenGate Distribution Path Status. Example: status_example
            targetInitiated (bool): Whether the target endpoint initiates the path. If true, the path needs
                to be created and modified through Receiver Server, who initiates the connection with
                Distribution Server. Otherwise, this behavior is reversed. Example: targetInitiated_example
            ruleset (dict):  Example: ruleset_example
            source (dict): source endpoint of the path. Example: source_example
            target (dict): target endpoint of the path. Example: target_example
            options (dict): options for the distribution path. Example: options_example
            description (str): Description for the path. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_receiver_paths(
                path='path_example',
                data={
                    "options": {
                        "network": {
                            "appOptions": {
                                "appFlushBytes": "24859",
                                "appFlushSecs": "2"
                            }
                        }
                    }
                }
            )

            client.update_receiver_paths(
                path='path_example',
                begin=None,
                name=None,
                encryptionProfile=None,
                status=None,
                targetInitiated=None,
                ruleset=None,
                source={
                    "description": None,
                    "uri": None,
                    "proxy": {
                        "uri": None,
                        "type": None,
                        "csAlias": None,
                        "csDomain": None
                    },
                    "details": {},
                    "isDynamicOggPort": None,
                    "authenticationMethod": None
                },
                target={
                    "description": None,
                    "uri": None,
                    "proxy": {
                        "uri": None,
                        "type": None,
                        "csAlias": None,
                        "csDomain": None
                    },
                    "details": {},
                    "isDynamicOggPort": None,
                    "authenticationMethod": None
                },
                options={
                    "network": {
                        "appOptions": {
                            "appFlushBytes": "24859",
                            "appFlushSecs": "2"
                        }
                    }
                },
                description=None
            )
        """
        path_params = {
            "path": path,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/targets/{path}",
            path_params=path_params,
            data=data,
            body_params={
                "begin": begin,
                "name": name,
                "encryptionProfile": encryptionProfile,
                "status": status,
                "targetInitiated": targetInitiated,
                "ruleset": ruleset,
                "source": source,
                "target": target,
                "options": options,
                "description": description,
            },
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}
    def delete_receiver_paths(
        self,
        path,
        version='v2',
        raw_response=False
    ):
        """
        Receiver Service
        DELETE /services/{version}/targets/{path}
        Required Role: Administrator
        Delete an existing Oracle GoldenGate Collector Path

        Parameters:
            path (str): Required. Example: path_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_receiver_paths(
                path='path_example'
            )
        """
        path_params = {
            "path": path,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/targets/{path}",
            path_params=path_params,
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}/checkpoints
    def get_receiver_path_checkpoint(
        self,
        path,
        version='v2',
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/checkpoints
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Path Checkpoints

        Parameters:
            path (str): Required. Example: path_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_path_checkpoint(
                path='path_example'
            )
        """
        path_params = {
            "path": path,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/targets/{path}/checkpoints",
            path_params=path_params,
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}/info
    def get_receiver_path_info(
        self,
        path,
        version='v2',
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/info
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Path Information

        Parameters:
            path (str): Required. Example: path_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_path_info(
                path='path_example'
            )
        """
        path_params = {
            "path": path,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/targets/{path}/info",
            path_params=path_params,
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}/progress
    def get_receiver_path_progress(
        self,
        path,
        version='v2',
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/progress
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Progress

        Parameters:
            path (str): Required. Example: path_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_path_progress(
                path='path_example'
            )
        """
        path_params = {
            "path": path,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/targets/{path}/progress",
            path_params=path_params,
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}/stats
    def get_receiver_path_stats(
        self,
        path,
        version='v2',
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/stats
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Path Stats

        Parameters:
            path (str): Required. Example: path_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_path_stats(
                path='path_example'
            )
        """
        path_params = {
            "path": path,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/targets/{path}/stats",
            path_params=path_params,
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks
    def list_tasks(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks
        Required Role: User
        Retrieve the list of tasks

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_tasks()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/tasks",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def get_task(
        self,
        task,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks/{task}
        Required Role: User
        Retrieve the details for a task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_task(
                task='task_example'
            )
        """
        path_params = {
            "task": task,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/tasks/{task}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def create_task(
        self,
        task,
        maxHistory=None,
        command=None,
        enabled=None,
        schedule=None,
        status=None,
        timeout=None,
        critical=None,
        restart=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Tasks
        POST /services/{version}/tasks/{task}
        Required Role: Administrator
        Create a new administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            maxHistory (int): Number of task executions to maintain history for. Example: maxHistory_example
            command (dict):  Example: command_example
            enabled (bool): Indicates if the task is enabled for execution. Example: enabled_example
            schedule (dict):  Example: schedule_example
            status (str): Task Status. Example: status_example
            timeout (int): Amount of time in seconds before a running task is cancelled. Example:
                timeout_example
            critical (bool): Indicates the task is critical to the deployment. Example: critical_example
            restart (dict): Control how the task is restarted if it terminates. Example: restart_example
            description (str): A description of the task. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_task(
                task='task_example',
                data={
                    "description": "Check critical lag every hour",
                    "enabled": False,
                    "schedule": {
                        "every": {
                            "units": "hours",
                            "value": "1"
                        }
                    },
                    "command": {
                        "name": "report",
                        "reportType": "lag",
                        "thresholds": [
                            {
                                "type": "critical",
                                "units": "seconds",
                                "value": "5"
                            }
                        ]
                    }
                }
            )

            client.create_task(
                task='task_example',
                maxHistory=None,
                command={
                    "name": "report",
                    "reportType": "lag",
                    "thresholds": [
                        {
                            "type": "critical",
                            "units": "seconds",
                            "value": "5"
                        }
                    ]
                },
                enabled=False,
                schedule={
                    "every": {
                        "units": "hours",
                        "value": "1"
                    }
                },
                status=None,
                timeout=None,
                critical=None,
                restart={
                    "enabled": None,
                    "onSuccess": None,
                    "delay": None,
                    "retries": None,
                    "window": None,
                    "disableOnFailure": None,
                    "failures": None
                },
                description='Check critical lag every hour'
            )
        """
        path_params = {
            "task": task,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/tasks/{task}",
            path_params=path_params,
            data=data,
            body_params={
                "maxHistory": maxHistory,
                "command": command,
                "enabled": enabled,
                "schedule": schedule,
                "status": status,
                "timeout": timeout,
                "critical": critical,
                "restart": restart,
                "description": description,
            },
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def update_task(
        self,
        task,
        maxHistory=None,
        command=None,
        enabled=None,
        schedule=None,
        status=None,
        timeout=None,
        critical=None,
        restart=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Tasks
        PATCH /services/{version}/tasks/{task}
        Required Role: Administrator
        Update an existing administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            maxHistory (int): Number of task executions to maintain history for. Example: maxHistory_example
            command (dict):  Example: command_example
            enabled (bool): Indicates if the task is enabled for execution. Example: enabled_example
            schedule (dict):  Example: schedule_example
            status (str): Task Status. Example: status_example
            timeout (int): Amount of time in seconds before a running task is cancelled. Example:
                timeout_example
            critical (bool): Indicates the task is critical to the deployment. Example: critical_example
            restart (dict): Control how the task is restarted if it terminates. Example: restart_example
            description (str): A description of the task. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_task(
                task='task_example',
                data={
                    "enabled": True
                }
            )

            client.update_task(
                task='task_example',
                maxHistory=None,
                command=None,
                enabled=True,
                schedule=None,
                status=None,
                timeout=None,
                critical=None,
                restart={
                    "enabled": None,
                    "onSuccess": None,
                    "delay": None,
                    "retries": None,
                    "window": None,
                    "disableOnFailure": None,
                    "failures": None
                },
                description=None
            )
        """
        path_params = {
            "task": task,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/tasks/{task}",
            path_params=path_params,
            data=data,
            body_params={
                "maxHistory": maxHistory,
                "command": command,
                "enabled": enabled,
                "schedule": schedule,
                "status": status,
                "timeout": timeout,
                "critical": critical,
                "restart": restart,
                "description": description,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def delete_task(
        self,
        task,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Tasks
        DELETE /services/{version}/tasks/{task}
        Required Role: Administrator
        Delete an administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_task(
                task='task_example'
            )
        """
        path_params = {
            "task": task,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/tasks/{task}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}/info
    def list_task_info_types(
        self,
        task,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks/{task}/info
        Required Role: User
        Retrieve the collection of information types available for a task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_task_info_types(
                task='task_example'
            )
        """
        path_params = {
            "task": task,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/tasks/{task}/info",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}/info/history
    def get_task_history(
        self,
        task,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks/{task}/info/history
        Required Role: User
        Retrieve the execution history of an administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_task_history(
                task='task_example'
            )
        """
        path_params = {
            "task": task,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/tasks/{task}/info/history",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}/info/status
    def get_task_status(
        self,
        task,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks/{task}/info/status
        Required Role: User
        Retrieve the current status of an administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_task_status(
                task='task_example'
            )
        """
        path_params = {
            "task": task,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/tasks/{task}/info/status",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails
    def list_trails(
        self,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Trails
        GET /services/{version}/trails
        Required Role: User
        Retrieve a collection of all known trails

        Parameters:
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_trails()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/trails",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}
    def get_trail(
        self,
        trail,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Trails
        GET /services/{version}/trails/{trail}
        Required Role: User
        Retrieve details for a Trail.

        Parameters:
            trail (str): The name of the Trail. This corresponds to the trailName property in the ogg:trail
                resource or the trail filesystem path.
                A trail name can be either a human-friendly name like HumanResources or a two-character name
                plus a query parameter called 'path' whose value is the URI-encoded trail filesystem path,
                like ea?path=north%2Femployees. When a short name and a URI-encoded path is used for the
                trail name, it must match the name and path properties in the corresponding ogg:trail
                resource.
                A trail called HumanResources with the path/name set to north/employees/ea can be referred to as
                either HumanResources or ea?path=north%2Femployees, but the canonical name is always the
                human-friendly name.
                POST operations accept only the human-friendly name. Required. Example: trail_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_trail(
                trail='trail_example'
            )
        """
        path_params = {
            "trail": trail,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/trails/{trail}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}
    def create_trail(
        self,
        trail,
        spaceUsed=None,
        sizeMB=None,
        offset=None,
        sequenceMaxInUse=None,
        trailName=None,
        path=None,
        remote=None,
        sequenceLastArchived=None,
        name=None,
        sequence=None,
        sequenceMinInUse=None,
        sequenceLength=None,
        sequenceMin=None,
        sequenceLengthFlip=None,
        processRef=None,
        sequenceMax=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Trails
        POST /services/{version}/trails/{trail}
        Required Role: Administrator
        Create a Trail.

        Parameters:
            trail (str): The name of the Trail. This corresponds to the trailName property in the ogg:trail
                resource or the trail filesystem path.
                A trail name can be either a human-friendly name like HumanResources or a two-character name
                plus a query parameter called 'path' whose value is the URI-encoded trail filesystem path,
                like ea?path=north%2Femployees. When a short name and a URI-encoded path is used for the
                trail name, it must match the name and path properties in the corresponding ogg:trail
                resource.
                A trail called HumanResources with the path/name set to north/employees/ea can be referred to as
                either HumanResources or ea?path=north%2Femployees, but the canonical name is always the
                human-friendly name.
                POST operations accept only the human-friendly name. Required. Example: trail_example
            spaceUsed (int): Bytes consumed by all trail sequences. Example: spaceUsed_example
            sizeMB (int): The maximum size, in megabytes, of a file in the trail. Example: sizeMB_example
            offset (int): Offset in trail sequence file. Example: offset_example
            sequenceMaxInUse (int): Maximum trail sequence number in use. Example: sequenceMaxInUse_example
            trailName (str): The optional 'user-friendly' name for the trail. Example: trailName_example
            path (str): The path where trail data is stored. Example: path_example
            remote (bool): Indicates if trail is local or remote. Example: remote_example
            sequenceLastArchived (list): Last sequence number archived (Managed Trails only). Example:
                sequenceLastArchived_example
            name (str): The two-character name of the trail. Example: name_example
            sequence (int): Trail beginning sequence number. Example: sequence_example
            sequenceMinInUse (int): Minimum trail sequence number in use. Example: sequenceMinInUse_example
            sequenceLength (str): Number of digits in sequence file name. Example: sequenceLength_example
            sequenceMin (int): Minimum trail sequence number that exists in the deployment. Example:
                sequenceMin_example
            sequenceLengthFlip (bool): Indicates sequence number length will change. Example:
                sequenceLengthFlip_example
            processRef (list): List of all processes associated with this trail. Example: processRef_example
            sequenceMax (int): Maximum trail sequence number that exists in the deployment. Example:
                sequenceMax_example
            description (str): Description for the trail. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_trail(
                trail='trail_example',
                data={
                    "$schema": "ogg:trail",
                    "trailName": "HumanResources",
                    "name": "ea",
                    "path": "north",
                    "sizeMB": "2000"
                }
            )

            client.create_trail(
                trail='trail_example',
                spaceUsed=None,
                sizeMB='2000',
                offset=None,
                sequenceMaxInUse=None,
                trailName='HumanResources',
                path='north',
                remote=None,
                sequenceLastArchived=[
                    {
                        "taskName": None,
                        "archiveTarget": None,
                        "sequence": None
                    }
                ],
                name='ea',
                sequence=None,
                sequenceMinInUse=None,
                sequenceLength=None,
                sequenceMin=None,
                sequenceLengthFlip=None,
                processRef=[
                    {
                        "processType": None,
                        "processName": None
                    }
                ],
                sequenceMax=None,
                description=None
            )
        """
        path_params = {
            "trail": trail,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/trails/{trail}",
            path_params=path_params,
            data=data,
            body_params={
                "spaceUsed": spaceUsed,
                "sizeMB": sizeMB,
                "offset": offset,
                "sequenceMaxInUse": sequenceMaxInUse,
                "trailName": trailName,
                "path": path,
                "remote": remote,
                "sequenceLastArchived": sequenceLastArchived,
                "name": name,
                "sequence": sequence,
                "sequenceMinInUse": sequenceMinInUse,
                "sequenceLength": sequenceLength,
                "sequenceMin": sequenceMin,
                "sequenceLengthFlip": sequenceLengthFlip,
                "processRef": processRef,
                "sequenceMax": sequenceMax,
                "description": description,
            },
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}
    def update_trail(
        self,
        trail,
        spaceUsed=None,
        sizeMB=None,
        offset=None,
        sequenceMaxInUse=None,
        trailName=None,
        path=None,
        remote=None,
        sequenceLastArchived=None,
        name=None,
        sequence=None,
        sequenceMinInUse=None,
        sequenceLength=None,
        sequenceMin=None,
        sequenceLengthFlip=None,
        processRef=None,
        sequenceMax=None,
        description=None,
        data=None,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Trails
        PATCH /services/{version}/trails/{trail}
        Required Role: Administrator
        Update a Trail.

        Parameters:
            trail (str): The name of the Trail. This corresponds to the trailName property in the ogg:trail
                resource or the trail filesystem path.
                A trail name can be either a human-friendly name like HumanResources or a two-character name
                plus a query parameter called 'path' whose value is the URI-encoded trail filesystem path,
                like ea?path=north%2Femployees. When a short name and a URI-encoded path is used for the
                trail name, it must match the name and path properties in the corresponding ogg:trail
                resource.
                A trail called HumanResources with the path/name set to north/employees/ea can be referred to as
                either HumanResources or ea?path=north%2Femployees, but the canonical name is always the
                human-friendly name.
                POST operations accept only the human-friendly name. Required. Example: trail_example
            spaceUsed (int): Bytes consumed by all trail sequences. Example: spaceUsed_example
            sizeMB (int): The maximum size, in megabytes, of a file in the trail. Example: sizeMB_example
            offset (int): Offset in trail sequence file. Example: offset_example
            sequenceMaxInUse (int): Maximum trail sequence number in use. Example: sequenceMaxInUse_example
            trailName (str): The optional 'user-friendly' name for the trail. Example: trailName_example
            path (str): The path where trail data is stored. Example: path_example
            remote (bool): Indicates if trail is local or remote. Example: remote_example
            sequenceLastArchived (list): Last sequence number archived (Managed Trails only). Example:
                sequenceLastArchived_example
            name (str): The two-character name of the trail. Example: name_example
            sequence (int): Trail beginning sequence number. Example: sequence_example
            sequenceMinInUse (int): Minimum trail sequence number in use. Example: sequenceMinInUse_example
            sequenceLength (str): Number of digits in sequence file name. Example: sequenceLength_example
            sequenceMin (int): Minimum trail sequence number that exists in the deployment. Example:
                sequenceMin_example
            sequenceLengthFlip (bool): Indicates sequence number length will change. Example:
                sequenceLengthFlip_example
            processRef (list): List of all processes associated with this trail. Example: processRef_example
            sequenceMax (int): Maximum trail sequence number that exists in the deployment. Example:
                sequenceMax_example
            description (str): Description for the trail. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_trail(
                trail='trail_example',
                data={
                    "$schema": "ogg:trail",
                    "description": "Trail for employee tables from Human Resources"
                }
            )

            client.update_trail(
                trail='trail_example',
                spaceUsed=None,
                sizeMB=None,
                offset=None,
                sequenceMaxInUse=None,
                trailName=None,
                path=None,
                remote=None,
                sequenceLastArchived=[
                    {
                        "taskName": None,
                        "archiveTarget": None,
                        "sequence": None
                    }
                ],
                name=None,
                sequence=None,
                sequenceMinInUse=None,
                sequenceLength=None,
                sequenceMin=None,
                sequenceLengthFlip=None,
                processRef=[
                    {
                        "processType": None,
                        "processName": None
                    }
                ],
                sequenceMax=None,
                description='Trail for employee tables from Human Resources'
            )
        """
        path_params = {
            "trail": trail,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/trails/{trail}",
            path_params=path_params,
            data=data,
            body_params={
                "spaceUsed": spaceUsed,
                "sizeMB": sizeMB,
                "offset": offset,
                "sequenceMaxInUse": sequenceMaxInUse,
                "trailName": trailName,
                "path": path,
                "remote": remote,
                "sequenceLastArchived": sequenceLastArchived,
                "name": name,
                "sequence": sequence,
                "sequenceMinInUse": sequenceMinInUse,
                "sequenceLength": sequenceLength,
                "sequenceMin": sequenceMin,
                "sequenceLengthFlip": sequenceLengthFlip,
                "processRef": processRef,
                "sequenceMax": sequenceMax,
                "description": description,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}
    def delete_trail(
        self,
        trail,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Trails
        DELETE /services/{version}/trails/{trail}
        Required Role: Administrator
        Delete a Trail

        Parameters:
            trail (str): The name of the Trail. This corresponds to the trailName property in the ogg:trail
                resource or the trail filesystem path.
                A trail name can be either a human-friendly name like HumanResources or a two-character name
                plus a query parameter called 'path' whose value is the URI-encoded trail filesystem path,
                like ea?path=north%2Femployees. When a short name and a URI-encoded path is used for the
                trail name, it must match the name and path properties in the corresponding ogg:trail
                resource.
                A trail called HumanResources with the path/name set to north/employees/ea can be referred to as
                either HumanResources or ea?path=north%2Femployees, but the canonical name is always the
                human-friendly name.
                POST operations accept only the human-friendly name. Required. Example: trail_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_trail(
                trail='trail_example'
            )
        """
        path_params = {
            "trail": trail,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/trails/{trail}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences
    def list_trail_sequences(
        self,
        trail,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Trails
        GET /services/{version}/trails/{trail}/sequences
        Required Role: User
        Retrieve a collection of all sequences that exist for a specific trail.

        Parameters:
            trail (str): The name of the Trail. This corresponds to the trailName property in the ogg:trail
                resource or the trail filesystem path.
                A trail name can be either a human-friendly name like HumanResources or a two-character name
                plus a query parameter called 'path' whose value is the URI-encoded trail filesystem path,
                like ea?path=north%2Femployees. When a short name and a URI-encoded path is used for the
                trail name, it must match the name and path properties in the corresponding ogg:trail
                resource.
                A trail called HumanResources with the path/name set to north/employees/ea can be referred to as
                either HumanResources or ea?path=north%2Femployees, but the canonical name is always the
                human-friendly name.
                POST operations accept only the human-friendly name. Required. Example: trail_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_trail_sequences(
                trail='trail_example'
            )
        """
        path_params = {
            "trail": trail,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/trails/{trail}/sequences",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences
    def delete_trail_sequence_collection(
        self,
        trail,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Trails
        DELETE /services/{version}/trails/{trail}/sequences
        Required Role: Administrator
        Delete a collection of trail sequences from a trail

        Parameters:
            trail (str): The name of the Trail. This corresponds to the trailName property in the ogg:trail
                resource or the trail filesystem path.
                A trail name can be either a human-friendly name like HumanResources or a two-character name
                plus a query parameter called 'path' whose value is the URI-encoded trail filesystem path,
                like ea?path=north%2Femployees. When a short name and a URI-encoded path is used for the
                trail name, it must match the name and path properties in the corresponding ogg:trail
                resource.
                A trail called HumanResources with the path/name set to north/employees/ea can be referred to as
                either HumanResources or ea?path=north%2Femployees, but the canonical name is always the
                human-friendly name.
                POST operations accept only the human-friendly name. Required. Example: trail_example
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_trail_sequence_collection(
                trail='trail_example'
            )
        """
        path_params = {
            "trail": trail,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/trails/{trail}/sequences",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences/{sequence}
    def get_trail_sequence(
        self,
        trail,
        sequence,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Trails
        GET /services/{version}/trails/{trail}/sequences/{sequence}
        Required Role: Administrator
        Retrieve a trail sequence

        Parameters:
            trail (str): The name of the Trail. This corresponds to the trailName property in the ogg:trail
                resource or the trail filesystem path.
                A trail name can be either a human-friendly name like HumanResources or a two-character name
                plus a query parameter called 'path' whose value is the URI-encoded trail filesystem path,
                like ea?path=north%2Femployees. When a short name and a URI-encoded path is used for the
                trail name, it must match the name and path properties in the corresponding ogg:trail
                resource.
                A trail called HumanResources with the path/name set to north/employees/ea can be referred to as
                either HumanResources or ea?path=north%2Femployees, but the canonical name is always the
                human-friendly name.
                POST operations accept only the human-friendly name. Required. Example: trail_example
            sequence (int): The trail sequence number. Required. Example: 1
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_trail_sequence(
                trail='trail_example',
                sequence=1
            )
        """
        path_params = {
            "trail": trail,
            "sequence": sequence,
            "version": version,
        }
        return self._call(
            "GET",
            "/services/{version}/trails/{trail}/sequences/{sequence}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences/{sequence}
    def create_trail_sequence(
        self,
        trail,
        sequence,
        data=None,
        version='v2',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Trails
        POST /services/{version}/trails/{trail}/sequences/{sequence}
        Required Role: Administrator
        Create a new trail sequence in a trail by uploading file content

        Parameters:
            trail (str): The name of the Trail. This corresponds to the trailName property in the ogg:trail
                resource or the trail filesystem path.
                A trail name can be either a human-friendly name like HumanResources or a two-character name
                plus a query parameter called 'path' whose value is the URI-encoded trail filesystem path,
                like ea?path=north%2Femployees. When a short name and a URI-encoded path is used for the
                trail name, it must match the name and path properties in the corresponding ogg:trail
                resource.
                A trail called HumanResources with the path/name set to north/employees/ea can be referred to as
                either HumanResources or ea?path=north%2Femployees, but the canonical name is always the
                human-friendly name.
                POST operations accept only the human-friendly name. Required. Example: trail_example
            sequence (int): The trail sequence number. Required. Example: 1
            data (dict): Data payload. See call example below for more details.
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_trail_sequence(
                trail='trail_example',
                sequence=1,
                data={})
        """
        path_params = {
            "trail": trail,
            "sequence": sequence,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/trails/{trail}/sequences/{sequence}",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences/{sequence}
    def delete_trail_sequence(
        self,
        trail,
        sequence,
        version='v2',
        raw_response=False
    ):
        """
        Administration Service/Trails
        DELETE /services/{version}/trails/{trail}/sequences/{sequence}
        Required Role: Administrator
        Delete a trail sequence from a trail

        Parameters:
            trail (str): The name of the Trail. This corresponds to the trailName property in the ogg:trail
                resource or the trail filesystem path.
                A trail name can be either a human-friendly name like HumanResources or a two-character name
                plus a query parameter called 'path' whose value is the URI-encoded trail filesystem path,
                like ea?path=north%2Femployees. When a short name and a URI-encoded path is used for the
                trail name, it must match the name and path properties in the corresponding ogg:trail
                resource.
                A trail called HumanResources with the path/name set to north/employees/ea can be referred to as
                either HumanResources or ea?path=north%2Femployees, but the canonical name is always the
                human-friendly name.
                POST operations accept only the human-friendly name. Required. Example: trail_example
            sequence (int): The trail sequence number. Required. Example: 1
            version (str): Defaults to v2. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_trail_sequence(
                trail='trail_example',
                sequence=1
            )
        """
        path_params = {
            "trail": trail,
            "sequence": sequence,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/trails/{trail}/sequences/{sequence}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    """
    Custom API methods appended to the OGGRestAPI client.
    These methods are not endpoints of the original swagger.json but are
    commonly used operations that combine one or more API calls for convenience.
    """

    def start_deployment(self, deployment, version='v2', raw_response=False):
        return self.update_deployment(
            deployment,
            data={'status': 'running'},
            version=version,
            raw_response=raw_response
        )

    def stop_deployment(self, deployment, version='v2', raw_response=False):
        return self.update_deployment(
            deployment,
            data={'status': 'stopped'},
            version=version,
            raw_response=raw_response
        )

    def start_extract(self, extract, version='v2', raw_response=False):
        return self.update_extract(
            extract,
            data={'status': 'running'},
            version=version,
            raw_response=raw_response
        )

    def stop_extract(self, extract, version='v2', raw_response=False):
        return self.update_extract(
            extract,
            data={'status': 'stopped'},
            version=version,
            raw_response=raw_response
        )

    def start_replicat(self, replicat, version='v2', raw_response=False):
        return self.update_replicat(
            replicat,
            data={'status': 'running'},
            version=version,
            raw_response=raw_response
        )

    def stop_replicat(self, replicat, version='v2', raw_response=False):
        return self.update_replicat(
            replicat,
            data={'status': 'stopped'},
            version=version,
            raw_response=raw_response
        )

    def start_distribution_path(self, distpath, version='v2', raw_response=False):
        return self.update_existing_distribution_path(
            distpath,
            data={'status': 'running'},
            version=version,
            raw_response=raw_response
        )

    def stop_distribution_path(self, distpath, version='v2', raw_response=False):
        return self.update_existing_distribution_path(
            distpath,
            data={'status': 'stopped'},
            version=version,
            raw_response=raw_response
        )

    def start_service(self, service, deployment, version='v2', raw_response=False):
        return self.update_service_properties(
            service,
            deployment,
            data={'status': 'running'},
            version=version,
            raw_response=raw_response
        )

    def stop_service(self, service, deployment, version='v2', raw_response=False):
        return self.update_service_properties(
            service,
            deployment,
            data={'status': 'stopped'},
            version=version,
            raw_response=raw_response
        )
