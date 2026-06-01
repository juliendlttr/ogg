#!/usr/bin/env python3
"""
Oracle GoldenGate REST API Client
Author: Julien DELATTRE
"""

import getpass
import requests
import time
import urllib3
from pprint import pprint


class OGGRestAPI:
    """Oracle GoldenGate REST API client."""

    def __init__(self, url, username=None, password=None, deployment=None, ca_cert=None,
                 reverse_proxy=False, verify_ssl=True, test_connection=True, timeout=None, version='v2'):
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
        self.swagger_version = '2023.12.12'
        self.version = version
        self.base_url = url
        self.username = username
        self.auth = (self.username, password)
        self.headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        self.deployment = deployment
        self.reverse_proxy = reverse_proxy
        self.verify_ssl = ca_cert if ca_cert else verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        if not verify_ssl and self.base_url.startswith('https://'):
            # Disable InsecureRequestWarning if verification is off
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Test connection
        if test_connection:
            # Testing connection by listing roles, which is a simple authenticated GET request that
            # works on all versions of the API. If this fails, it will raise an exception that can
            # be caught by the caller to handle connection issues gracefully.
            resp = self.list_roles(raw_response=True)
            if resp.status_code == 200:
                print(f'Connected to OGG REST API at {self.base_url}')
            elif resp.status_code == 403:
                print(
                    f"Authentication failed when connecting to OGG REST API at {self.base_url}. "
                    "Please check your credentials."
                )
                raise RuntimeError(
                    f"Authentication failed with user {self.username}. Please check your credentials.")
            else:
                print(f"Failed to connect to OGG REST API at {self.base_url}. HTTP {resp.status_code}: {resp.text}")
                raise RuntimeError(f"Connection failed with status code {resp.status_code}")

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
        path_params = dict(path_params or {})
        if "{version}" in template and "version" not in path_params:
            path_params["version"] = self.version

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
                    error_messages = [
                        f"{message['severity']} (code {response.status_code}) - "
                        f"{url}: {message['title']}"
                        for message in messages
                    ]
                    raise RuntimeError(' ; '.join(error_messages))
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
        return self._call(
            method="GET",
            template="/services",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}
    def get_api_version(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/REST API Catalog
        GET /services/{version}
        Use this endpoint to obtain details of a specific version of an Oracle GoldenGate Service REST API.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_api_version(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations
    def list_roles(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        GET /services/{version}/authorizations
        Get the collection of roles in this deployment.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_roles(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/authorizations",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}
    def list_users(
        self,
        role,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        GET /services/{version}/authorizations/{role}
        Get the collection of Authorized Users associated with the Authorization Role.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
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
        return self._call(
            method="GET",
            template="/services/{version}/authorizations/{role}",
            path_params={
                "role": role,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}
    def bulk_create_users(
        self,
        role,
        users=None,
        data=None,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        POST /services/{version}/authorizations/{role}
        Create multiple users associated with the given role.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            users (list): Required if not included in `data`. Example: users_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                            "username": "tkgguser01",
                            "credential": "password-A1"
                        },
                        {
                            "username": "tkgguser02",
                            "credential": "password-B2"
                        }
                    ]
                }
            )

            client.bulk_create_users(
                role='User',
                ogg_service='adminsrvr',
                users=[
                    {
                        "username": "tkgguser01",
                        "credential": "password-A1"
                    },
                    {
                        "username": "tkgguser02",
                        "credential": "password-B2"
                    }
                ]
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/authorizations/{role}",
            path_params={
                "role": role,
            },
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
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        GET /services/{version}/authorizations/{role}/{user}
        Get Authorization User Resource information.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            user (str): User Resource Name. Required. Example: user_example
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
        return self._call(
            method="GET",
            template="/services/{version}/authorizations/{role}/{user}",
            path_params={
                "role": role,
                "user": user,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def create_user(
        self,
        role,
        user,
        role_1=None,
        user_1=None,
        credential=None,
        info=None,
        type=None,
        data=None,
        ogg_service='',
        raw_response=False,
        if_exists='fail'
    ):
        """
        Common/User Management
        POST /services/{version}/authorizations/{role}/{user}
        Create a new Authorization User Resource.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            user (str): User Resource Name. Required. Example: user_example
            role_1 (str):  Example: role_example
            user_1 (str):  Example: user_example
            credential (str):  Example: credential_example
            info (str):  Example: info_example
            type (str):  Example: type_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                    "credential": "password-A1z",
                    "info": "Example user #3"
                }
            )

            client.create_user(
                role='User',
                user='user_example',
                ogg_service='adminsrvr',
                role_1=None,
                user_1=None,
                credential='password-A1z',
                info='Example user #3',
                type=None
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/authorizations/{role}/{user}",
            path_params={
                "role": role,
                "user": user,
            },
            data=data,
            body_params={
                "role": role_1,
                "user": user_1,
                "credential": credential,
                "info": info,
                "type": type,
            },
            ogg_service=ogg_service,
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def update_user(
        self,
        role,
        user,
        role_1=None,
        user_1=None,
        credential=None,
        info=None,
        type=None,
        data=None,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        PATCH /services/{version}/authorizations/{role}/{user}
        Update an existing Authorization User Resource.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            user (str): User Resource Name. Required. Example: user_example
            role_1 (str):  Example: role_example
            user_1 (str):  Example: user_example
            credential (str):  Example: credential_example
            info (str):  Example: info_example
            type (str):  Example: type_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                    "credential": "NewPassword-Z1a"
                }
            )

            client.update_user(
                role='User',
                user='user_example',
                ogg_service='adminsrvr',
                role_1=None,
                user_1=None,
                credential='NewPassword-Z1a',
                info=None,
                type=None
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/authorizations/{role}/{user}",
            path_params={
                "role": role,
                "user": user,
            },
            data=data,
            body_params={
                "role": role_1,
                "user": user_1,
                "credential": credential,
                "info": info,
                "type": type,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def delete_user(
        self,
        role,
        user,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Management
        DELETE /services/{version}/authorizations/{role}/{user}
        Delete an existing Authorization user role. To completely remove a user from the deployment, use a value
            of "all" for {role}.

        Parameters:
            role (str): Authorization Role Resource Name. Required. Example: User
            user (str): User Resource Name. Required. Example: user_example
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
        return self._call(
            method="DELETE",
            template="/services/{version}/authorizations/{role}/{user}",
            path_params={
                "role": role,
                "user": user,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/commands/execute
    def execute_command(
        self,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Commands
        POST /services/{version}/commands/execute
        Execute a command. Reporting commands are accessible for users with the 'User' role. Other commands
            require the 'Operator' role.

        Parameters:
            data (dict): Data payload. See call example below for more details.
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
                            "value": 0
                        },
                        {
                            "type": "critical",
                            "units": "seconds",
                            "value": 5
                        }
                    ]
                }
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/commands/execute",
            data=data,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files
    def list_configuration_files(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        GET /services/{version}/config/files
        Retrieve the collection of configuration files.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_configuration_files()

        """
        return self._call(
            method="GET",
            template="/services/{version}/config/files",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def get_configuration_file(
        self,
        file,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        GET /services/{version}/config/files/{file}
        Retrieve the contents of a configuration file.

        Parameters:
            file (str): The name of a configuration file. Required. Example: file_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_configuration_file(
                file='file_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/config/files/{file}",
            path_params={
                "file": file,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def create_configuration_file(
        self,
        file,
        lines=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Configuration Settings
        POST /services/{version}/config/files/{file}
        Create a new configuration file.

        Parameters:
            file (str): The name of a configuration file. Required. Example: file_example
            lines (list): Required if not included in `data`. Example: lines_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_configuration_file(
                file='file_example',
                data={
                    "lines": [
                        "UseridAlias oggadmin",
                        "ReportCount Every 1000 Records"
                    ]
                }
            )

            client.create_configuration_file(
                file='file_example',
                lines=[
                    "UseridAlias oggadmin",
                    "ReportCount Every 1000 Records"
                ]
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/config/files/{file}",
            path_params={
                "file": file,
            },
            data=data,
            body_params={
                "lines": lines,
            },
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def delete_configuration_file(
        self,
        file,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        DELETE /services/{version}/config/files/{file}
        Delete a configuration file.

        Parameters:
            file (str): The name of a configuration file. Required. Example: file_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_configuration_file(
                file='file_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/config/files/{file}",
            path_params={
                "file": file,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def update_configuration_file(
        self,
        file,
        lines=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        PUT /services/{version}/config/files/{file}
        Modify an existing configuration file.

        Parameters:
            file (str): The name of a configuration file. Required. Example: file_example
            lines (list): Required if not included in `data`. Example: lines_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_configuration_file(
                file='file_example',
                data={
                    "lines": [
                        "UseridAlias oggadmin",
                        "ReportCount Every 100000 Records"
                    ]
                }
            )

            client.update_configuration_file(
                file='file_example',
                lines=[
                    "UseridAlias oggadmin",
                    "ReportCount Every 100000 Records"
                ]
            )
        """
        return self._call(
            method="PUT",
            template="/services/{version}/config/files/{file}",
            path_params={
                "file": file,
            },
            data=data,
            body_params={
                "lines": lines,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/health
    def get_service_health(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration
        GET /services/{version}/config/health
        Retrieve detailed information for the service health.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_service_health(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/config/health",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/health/check
    def get_service_health_check(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration
        GET /services/{version}/config/health/check
        Retrieve summary information for the service health.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_service_health_check(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/config/health/check",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/summary
    def get_config_summary(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration
        GET /services/{version}/config/summary
        Retrieve summary information for the service.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_config_summary(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/config/summary",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types
    def list_config_types(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        GET /services/{version}/config/types
        Retrieve the collection of configuration variable data types.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_config_types()

        """
        return self._call(
            method="GET",
            template="/services/{version}/config/types",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def get_config_type(
        self,
        type,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        GET /services/{version}/config/types/{type}
        Retrieve a configuration data type.

        Parameters:
            type (str): Required. Example: type_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_config_type(
                type='type_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/config/types/{type}",
            path_params={
                "type": type,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def create_config_type(
        self,
        type,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Configuration Settings
        POST /services/{version}/config/types/{type}
        Create a new configuration data type.

        Parameters:
            type (str): Required. Example: type_example
            data (dict): Data payload. See call example below for more details.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_config_type(
                type='type_example',
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
                                "minLength": 0,
                                "maxLength": 4095
                            },
                            "minItems": 0,
                            "maxItems": 32767
                        }
                    },
                    "required": [
                        "lines"
                    ],
                    "additionalProperties": False
                }
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/config/types/{type}",
            path_params={
                "type": type,
            },
            data=data,
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def delete_config_type(
        self,
        type,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        DELETE /services/{version}/config/types/{type}
        Delete a configuration data type.

        Parameters:
            type (str): Required. Example: type_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_config_type(
                type='type_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/config/types/{type}",
            path_params={
                "type": type,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values
    def list_config_values(
        self,
        type,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        GET /services/{version}/config/types/{type}/values
        Retrieve the collection of names of the configuration values for a data type.

        Parameters:
            type (str): Required. Example: type_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_config_values(
                type='type_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/config/types/{type}/values",
            path_params={
                "type": type,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def get_config_value(
        self,
        type,
        value,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        GET /services/{version}/config/types/{type}/values/{value}
        Retrieve a configuration value.

        Parameters:
            type (str): Required. Example: type_example
            value (str): Value name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_', ':' or '-'. Required. Example: value_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_config_value(
                type='type_example',
                value='value_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/config/types/{type}/values/{value}",
            path_params={
                "type": type,
                "value": value,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def create_config_value(
        self,
        type,
        value,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Configuration Settings
        POST /services/{version}/config/types/{type}/values/{value}
        Create a new configuration value.

        Parameters:
            type (str): Required. Example: type_example
            value (str): Value name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_', ':' or '-'. Required. Example: value_example
            data (dict): Data payload. See call example below for more details.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_config_value(
                type='type_example',
                value='value_example',
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
        return self._call(
            method="POST",
            template="/services/{version}/config/types/{type}/values/{value}",
            path_params={
                "type": type,
                "value": value,
            },
            data=data,
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def delete_config_value(
        self,
        type,
        value,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        DELETE /services/{version}/config/types/{type}/values/{value}
        Delete a configuration value.

        Parameters:
            type (str): Required. Example: type_example
            value (str): Value name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_', ':' or '-'. Required. Example: value_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_config_value(
                type='type_example',
                value='value_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/config/types/{type}/values/{value}",
            path_params={
                "type": type,
                "value": value,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def update_config_value(
        self,
        type,
        value,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Configuration Settings
        PUT /services/{version}/config/types/{type}/values/{value}
        Replace an existing configuration value.

        Parameters:
            type (str): Required. Example: type_example
            value (str): Value name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_', ':' or '-'. Required. Example: value_example
            data (dict): Data payload. See call example below for more details.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_config_value(
                type='type_example',
                value='value_example',
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
        return self._call(
            method="PUT",
            template="/services/{version}/config/types/{type}/values/{value}",
            path_params={
                "type": type,
                "value": value,
            },
            data=data,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections
    def list_connections(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Database
        GET /services/{version}/connections
        Retrieve the list of known database connections. For each item in the credential store, a database
            connection of the form 'domain.alias' is created.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_connections()

        """
        return self._call(
            method="GET",
            template="/services/{version}/connections",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def get_connection(
        self,
        connection,
        raw_response=False
    ):
        """
        Administrative Server/Database
        GET /services/{version}/connections/{connection}
        Retrieve the database connection details.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_connection(
                connection='MYCONN'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/connections/{connection}",
            path_params={
                "connection": connection,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def create_connection(
        self,
        connection,
        credentials=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Database
        POST /services/{version}/connections/{connection}
        Create a new database connection. Connections are automatically created for aliases in the credential
            store.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            credentials (dict): Credentials for database. Required if not included in `data`. Example:
                credentials_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                        "alias": "oggadmin"
                    }
                }
            )

            client.create_connection(
                connection='MYCONN',
                credentials={
                    "domain": "OracleGoldenGate",
                    "alias": "oggadmin"
                }
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/connections/{connection}",
            path_params={
                "connection": connection,
            },
            data=data,
            body_params={
                "credentials": credentials,
            },
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def delete_connection(
        self,
        connection,
        raw_response=False
    ):
        """
        Administrative Server/Database
        DELETE /services/{version}/connections/{connection}
        Remove a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_connection(
                connection='MYCONN'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/connections/{connection}",
            path_params={
                "connection": connection,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def update_connection(
        self,
        connection,
        credentials=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Database
        PUT /services/{version}/connections/{connection}
        Update a database connection. Connections created for aliases in the credential store cannot be updated.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            credentials (dict): Credentials for database. Required if not included in `data`. Example:
                credentials_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_connection(
                connection='MYCONN',
                data={
                    "credentials": {
                        "alias": "oggadmin"
                    }
                }
            )

            client.update_connection(
                connection='MYCONN',
                credentials={
                    "alias": "oggadmin"
                }
            )
        """
        return self._call(
            method="PUT",
            template="/services/{version}/connections/{connection}",
            path_params={
                "connection": connection,
            },
            data=data,
            body_params={
                "credentials": credentials,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/activeTransactions
    def get_active_transactions(
        self,
        connection,
        raw_response=False
    ):
        """
        Administrative Server/Database
        GET /services/{version}/connections/{connection}/activeTransactions
        Retrieve details of the active transactions for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_active_transactions(
                connection='MYCONN'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/connections/{connection}/activeTransactions",
            path_params={
                "connection": connection,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases
    def list_database_names(
        self,
        connection,
        raw_response=False
    ):
        """
        Administrative Server/Database
        GET /services/{version}/connections/{connection}/databases
        Retrieve names of databases.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_database_names(
                connection='MYCONN'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/connections/{connection}/databases",
            path_params={
                "connection": connection,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}
    def list_database_schemas(
        self,
        connection,
        database,
        raw_response=False
    ):
        """
        Administrative Server/Database
        GET /services/{version}/connections/{connection}/databases/{database}
        Retrieve names of schemas in the database.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            database (str): Database name. Required. Example: database_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_database_schemas(
                connection='MYCONN',
                database='database_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/connections/{connection}/databases/{database}",
            path_params={
                "connection": connection,
                "database": database,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}/{schema}
    def list_database_tables(
        self,
        connection,
        database,
        schema,
        raw_response=False
    ):
        """
        Administrative Server/Database
        GET /services/{version}/connections/{connection}/databases/{database}/{schema}
        Retrieve names of tables in the schema.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            database (str): Database name. Required. Example: database_example
            schema (str): Schema name in the database. Required. Example: schema_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_database_tables(
                connection='MYCONN',
                database='database_example',
                schema='schema_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/connections/{connection}/databases/{database}/{schema}",
            path_params={
                "connection": connection,
                "database": database,
                "schema": schema,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}
    def get_database_table(
        self,
        connection,
        database,
        schema,
        table,
        raw_response=False
    ):
        """
        Administrative Server/Database
        GET /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}
        Retrieve details for a table in the schema.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            database (str): Database name. Required. Example: database_example
            schema (str): Schema name in the database. Required. Example: schema_example
            table (str): Table name in the database. Required. Example: table_example
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
        return self._call(
            method="GET",
            template="/services/{version}/connections/{connection}/databases/{database}/{schema}/{table}",
            path_params={
                "connection": connection,
                "database": database,
                "schema": schema,
                "table": table,
            },
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
        raw_response=False
    ):
        """
        Administrative Server/Database
        POST /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}/instantiationCsn
        Manage the instantiation CSN for filtering.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            database (str): Database name. Required. Example: database_example
            schema (str): Schema name in the database. Required. Example: schema_example
            table (str): Table name in the database. Required. Example: table_example
            data (dict): Data payload. See call example below for more details.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_instantiation_csn(
                connection='MYCONN',
                database='database_example',
                schema='schema_example',
                table='table_example',
                data={
                    "command": "clear",
                    "source": "source.table"
                }
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/connections/{connection}/databases/{database}/{schema}/{table}/instantiationCsn",
            path_params={
                "connection": connection,
                "database": database,
                "schema": schema,
                "table": table,
            },
            data=data,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/checkpoint
    def manage_checkpoint_table(
        self,
        connection,
        operation=None,
        name=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Database
        POST /services/{version}/connections/{connection}/tables/checkpoint
        Manage Oracle GoldenGate Checkpoint table

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            operation (str): Required if not included in `data`. Example: operation_example
            name (str): Required if not included in `data`. Example: name_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.manage_checkpoint_table(
                connection='MYCONN',
                data={
                    "operation": "add",
                    "name": "oggadmin.checkpoints"
                }
            )

            client.manage_checkpoint_table(
                connection='MYCONN',
                operation='add',
                name='oggadmin.checkpoints'
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/connections/{connection}/tables/checkpoint",
            path_params={
                "connection": connection,
            },
            data=data,
            body_params={
                "operation": operation,
                "name": name,
            },
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def get_heartbeat_table(
        self,
        connection,
        raw_response=False
    ):
        """
        Administrative Server/Database
        GET /services/{version}/connections/{connection}/tables/heartbeat
        Retrieve details of the heartbeat table for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_heartbeat_table(
                connection='MYCONN'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/connections/{connection}/tables/heartbeat",
            path_params={
                "connection": connection,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def create_heartbeat_table(
        self,
        connection,
        frequency=None,
        retention_time=None,
        purge_frequency=None,
        partitioned=None,
        target_only=None,
        tracking_extract_restart=None,
        upgrade=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Database
        POST /services/{version}/connections/{connection}/tables/heartbeat
        Create the heartbeat table for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            frequency (int): Interval, in seconds, at which the heartbeat table is updated. Example:
                frequency_example
            retention_time (int): Heartbeats older than this retention time (in days) will be deleted from
                the heartbeat table. Example: retentionTime_example
            purge_frequency (int): Interval, in days, at which the heartbeat history table is purged.
                Example: purgeFrequency_example
            partitioned (bool): Whether the heartbeat history table is partitioned or not. Example:
                partitioned_example
            target_only (bool): Boolean value to enable or disable supplemental logging and the scheduler
                job for updating heartbeat seed and heartbeat tables. Example: targetOnly_example
            tracking_extract_restart (bool): Whether current heartbeat table setup is tracking extract
                restart position or not. Example: trackingExtractRestart_example
            upgrade (bool): Boolean value to detect when to upgrade the heartbeat tables. Example:
                upgrade_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_heartbeat_table(
                connection='MYCONN',
                data={
                    "frequency": 60
                }
            )

            client.create_heartbeat_table(
                connection='MYCONN',
                frequency=60,
                retention_time=None,
                purge_frequency=None,
                partitioned=None,
                target_only=None,
                tracking_extract_restart=None,
                upgrade=None
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/connections/{connection}/tables/heartbeat",
            path_params={
                "connection": connection,
            },
            data=data,
            body_params={
                "frequency": frequency,
                "retentionTime": retention_time,
                "purgeFrequency": purge_frequency,
                "partitioned": partitioned,
                "targetOnly": target_only,
                "trackingExtractRestart": tracking_extract_restart,
                "upgrade": upgrade,
            },
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def update_heartbeat_table(
        self,
        connection,
        frequency=None,
        retention_time=None,
        purge_frequency=None,
        partitioned=None,
        target_only=None,
        tracking_extract_restart=None,
        upgrade=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Database
        PATCH /services/{version}/connections/{connection}/tables/heartbeat
        Modify the heartbeat table parameters for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            frequency (int): Interval, in seconds, at which the heartbeat table is updated. Example:
                frequency_example
            retention_time (int): Heartbeats older than this retention time (in days) will be deleted from
                the heartbeat table. Example: retentionTime_example
            purge_frequency (int): Interval, in days, at which the heartbeat history table is purged.
                Example: purgeFrequency_example
            partitioned (bool): Whether the heartbeat history table is partitioned or not. Example:
                partitioned_example
            target_only (bool): Boolean value to enable or disable supplemental logging and the scheduler
                job for updating heartbeat seed and heartbeat tables. Example: targetOnly_example
            tracking_extract_restart (bool): Whether current heartbeat table setup is tracking extract
                restart position or not. Example: trackingExtractRestart_example
            upgrade (bool): Boolean value to detect when to upgrade the heartbeat tables. Example:
                upgrade_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_heartbeat_table(
                connection='MYCONN',
                data={
                    "purgeFrequency": 2
                }
            )

            client.update_heartbeat_table(
                connection='MYCONN',
                frequency=None,
                retention_time=None,
                purge_frequency=2,
                partitioned=None,
                target_only=None,
                tracking_extract_restart=None,
                upgrade=None
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/connections/{connection}/tables/heartbeat",
            path_params={
                "connection": connection,
            },
            data=data,
            body_params={
                "frequency": frequency,
                "retentionTime": retention_time,
                "purgeFrequency": purge_frequency,
                "partitioned": partitioned,
                "targetOnly": target_only,
                "trackingExtractRestart": tracking_extract_restart,
                "upgrade": upgrade,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def delete_heartbeat_table(
        self,
        connection,
        raw_response=False
    ):
        """
        Administrative Server/Database
        DELETE /services/{version}/connections/{connection}/tables/heartbeat
        Remove heartbeat resources from a database.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_heartbeat_table(
                connection='MYCONN'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/connections/{connection}/tables/heartbeat",
            path_params={
                "connection": connection,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat/{process}
    def get_process_heartbeat_records(
        self,
        connection,
        process,
        raw_response=False
    ):
        """
        Administrative Server/Database
        GET /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Retrieve heartbeat table entries for an extract or replicat group.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            process (str): The name of the extract or replicat process. Required. Example: process_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_heartbeat_records(
                connection='MYCONN',
                process='process_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/connections/{connection}/tables/heartbeat/{process}",
            path_params={
                "connection": connection,
                "process": process,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat/{process}
    def delete_process_heartbeat_records(
        self,
        connection,
        process,
        raw_response=False
    ):
        """
        Administrative Server/Database
        DELETE /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Delete heartbeat table entries for an extract or replicat group.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            process (str): The name of the extract or replicat process. Required. Example: process_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_process_heartbeat_records(
                connection='MYCONN',
                process='process_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/connections/{connection}/tables/heartbeat/{process}",
            path_params={
                "connection": connection,
                "process": process,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/trace
    def update_trace_table(
        self,
        connection,
        operation=None,
        name=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Database
        POST /services/{version}/connections/{connection}/tables/trace
        Manage Oracle GoldenGate Trace table

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            operation (str): Required if not included in `data`. Example: operation_example
            name (str): Required if not included in `data`. Example: name_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_trace_table(
                connection='MYCONN',
                data={
                    "operation": "add",
                    "name": "oggadmin.trace01"
                }
            )

            client.update_trace_table(
                connection='MYCONN',
                operation='add',
                name='oggadmin.trace01'
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/connections/{connection}/tables/trace",
            path_params={
                "connection": connection,
            },
            data=data,
            body_params={
                "operation": operation,
                "name": name,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/procedure
    def manage_procedure_supplemental_logging(
        self,
        connection,
        operation=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Database
        POST /services/{version}/connections/{connection}/trandata/procedure
        Manage Supplemental Logging for Database Procedures

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            operation (str): Required if not included in `data`. Example: operation_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.manage_procedure_supplemental_logging(
                connection='MYCONN',
                data={
                    "operation": "info"
                }
            )

            client.manage_procedure_supplemental_logging(
                connection='MYCONN',
                operation='info'
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/connections/{connection}/trandata/procedure",
            path_params={
                "connection": connection,
            },
            data=data,
            body_params={
                "operation": operation,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/schema
    def manage_schema_supplemental_logging(
        self,
        connection,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Database
        POST /services/{version}/connections/{connection}/trandata/schema
        Manage Supplemental Logging for Database Schemas

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.manage_schema_supplemental_logging(
                connection='MYCONN',
                data={
                    "operation": "info",
                    "schemaName": "oggadmin"
                }
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/connections/{connection}/trandata/schema",
            path_params={
                "connection": connection,
            },
            data=data,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/table
    def manage_table_supplemental_logging(
        self,
        connection,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Database
        POST /services/{version}/connections/{connection}/trandata/table
        Manage Supplemental Logging for Database Tables

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.manage_table_supplemental_logging(
                connection='MYCONN',
                data={
                    "operation": "add",
                    "tableName": "oggadmin.table01"
                }
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/connections/{connection}/trandata/table",
            path_params={
                "connection": connection,
            },
            data=data,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/content
    def get_content(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Content Requests
        GET /services/{version}/content
        Top level file list.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_content(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/content",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials
    def list_domains(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Credentials
        GET /services/{version}/credentials
        Retrieve the list of domains in the credential store.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_domains()

        """
        return self._call(
            method="GET",
            template="/services/{version}/credentials",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}
    def list_credentials(
        self,
        domain,
        raw_response=False
    ):
        """
        Administrative Server/Credentials
        GET /services/{version}/credentials/{domain}
        Retrieve the list of aliases for a domain in the credential store.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_credentials(
                domain='OracleGoldenGate'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/credentials/{domain}",
            path_params={
                "domain": domain,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def get_alias(
        self,
        domain,
        alias,
        raw_response=False
    ):
        """
        Administrative Server/Credentials
        GET /services/{version}/credentials/{domain}/{alias}
        Retrieve the available information for an alias in a credential store domain. The password for an alias
            will not be returned.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_alias(
                domain='OracleGoldenGate',
                alias='ggnorth'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/credentials/{domain}/{alias}",
            path_params={
                "domain": domain,
                "alias": alias,
            },
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
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Credentials
        POST /services/{version}/credentials/{domain}/{alias}
        Create a new alias in the credential store.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            userid (str):  Example: userid_example
            password (str):  Example: password_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_alias(
                domain='OracleGoldenGate',
                alias='ggnorth',
                data={
                    "userid": "oggadmin",
                    "password": "oggadmin"
                }
            )

            client.create_alias(
                domain='OracleGoldenGate',
                alias='ggnorth',
                userid='oggadmin',
                password='oggadmin'
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/credentials/{domain}/{alias}",
            path_params={
                "domain": domain,
                "alias": alias,
            },
            data=data,
            body_params={
                "userid": userid,
                "password": password,
            },
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def delete_alias(
        self,
        domain,
        alias,
        raw_response=False
    ):
        """
        Administrative Server/Credentials
        DELETE /services/{version}/credentials/{domain}/{alias}
        Delete an alias from the credential store.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_alias(
                domain='OracleGoldenGate',
                alias='ggnorth'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/credentials/{domain}/{alias}",
            path_params={
                "domain": domain,
                "alias": alias,
            },
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
        raw_response=False
    ):
        """
        Administrative Server/Credentials
        PUT /services/{version}/credentials/{domain}/{alias}
        Update an alias in the credential store.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            userid (str):  Example: userid_example
            password (str):  Example: password_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_alias(
                domain='OracleGoldenGate',
                alias='ggnorth',
                data={
                    "userid": "oggadmin",
                    "password": "newPassword"
                }
            )

            client.update_alias(
                domain='OracleGoldenGate',
                alias='ggnorth',
                userid='oggadmin',
                password='newPassword'
            )
        """
        return self._call(
            method="PUT",
            template="/services/{version}/credentials/{domain}/{alias}",
            path_params={
                "domain": domain,
                "alias": alias,
            },
            data=data,
            body_params={
                "userid": userid,
                "password": password,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}/valid
    def is_credential_valid(
        self,
        domain,
        alias,
        raw_response=False
    ):
        """
        Administrative Server/Credentials
        GET /services/{version}/credentials/{domain}/{alias}/valid
        Check validity of credentials and return database credentials details.

        Parameters:
            domain (str): Credential store domain name. Required. Example: OracleGoldenGate
            alias (str): Credential store alias. Required. Example: ggnorth
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.is_credential_valid(
                domain='OracleGoldenGate',
                alias='ggnorth'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/credentials/{domain}/{alias}/valid",
            path_params={
                "domain": domain,
                "alias": alias,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/currentuser
    def get_current_user(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Information
        GET /services/{version}/currentuser
        Return the current user's identity information encoded in the request.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_current_user(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/currentuser",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/currentuser
    def delete_current_user(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/User Information
        DELETE /services/{version}/currentuser
        Remove the current user's identity information encoded in the request.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_current_user(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/currentuser",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments
    def list_deployments(
        self,
        raw_response=False
    ):
        """
        Service Manager/Deployments
        GET /services/{version}/deployments
        Retrieve the collection of Oracle GoldenGate Deployments.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_deployments()

        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def get_deployment(
        self,
        deployment,
        raw_response=False
    ):
        """
        Service Manager/Deployments
        GET /services/{version}/deployments/{deployment}
        Retrieve the details of a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_deployment(
                deployment='deployment_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}",
            path_params={
                "deployment": deployment,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def create_deployment(
        self,
        deployment,
        ogg_home=None,
        ogg_data_home=None,
        ogg_conf_home=None,
        enabled=None,
        id=None,
        ogg_ssl_home=None,
        status=None,
        ogg_etc_home=None,
        ogg_var_home=None,
        environment=None,
        password_regex=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Deployments
        POST /services/{version}/deployments/{deployment}
        Create a new Oracle GoldenGate deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            ogg_home (str): The deployment's home directory. Example: oggHome_example
            ogg_data_home (str): The deployment's var/data user data directory. Example: oggDataHome_example
            ogg_conf_home (str): The deployment's configuration directory. Example: oggConfHome_example
            enabled (bool): Indicates the deployment is managed by the Service Manager. Example:
                enabled_example
            id (str): An identifier that uniquely identifies this deployment. Example: id_example
            ogg_ssl_home (str): The deployment's SSL configuration directory. Example: oggSslHome_example
            status (str): Indicates the status of the deployment. Example: status_example
            ogg_etc_home (str): The deployment's etc configuration directory. Example: oggEtcHome_example
            ogg_var_home (str): The deployment's var user data directory. Example: oggVarHome_example
            environment (list): Additional environment variables for the deployment. Example:
                environment_example
            password_regex (str): The regular expression that new user passwords must match. Example:
                passwordRegex_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_deployment(
                deployment='deployment_example',
                data={
                    "oggHome": "/home/oracle/oggSecondary",
                    "oggEtcHome": "/home/oracle/ogg/etc",
                    "enabled": False
                }
            )

            client.create_deployment(
                deployment='deployment_example',
                ogg_home='/home/oracle/oggSecondary',
                ogg_data_home=None,
                ogg_conf_home=None,
                enabled=False,
                id=None,
                ogg_ssl_home=None,
                status=None,
                ogg_etc_home='/home/oracle/ogg/etc',
                ogg_var_home=None,
                environment=[
                    {
                        "name": None,
                        "value": None
                    }
                ],
                password_regex=None
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/deployments/{deployment}",
            path_params={
                "deployment": deployment,
            },
            data=data,
            body_params={
                "oggHome": ogg_home,
                "oggDataHome": ogg_data_home,
                "oggConfHome": ogg_conf_home,
                "enabled": enabled,
                "id": id,
                "oggSslHome": ogg_ssl_home,
                "status": status,
                "oggEtcHome": ogg_etc_home,
                "oggVarHome": ogg_var_home,
                "environment": environment,
                "passwordRegex": password_regex,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def update_deployment(
        self,
        deployment,
        ogg_home=None,
        ogg_data_home=None,
        ogg_conf_home=None,
        enabled=None,
        id=None,
        ogg_ssl_home=None,
        status=None,
        ogg_etc_home=None,
        ogg_var_home=None,
        environment=None,
        password_regex=None,
        data=None,
        raw_response=False
    ):
        """
        Service Manager/Deployments
        PATCH /services/{version}/deployments/{deployment}
        Update the properties of a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            ogg_home (str): The deployment's home directory. Example: oggHome_example
            ogg_data_home (str): The deployment's var/data user data directory. Example: oggDataHome_example
            ogg_conf_home (str): The deployment's configuration directory. Example: oggConfHome_example
            enabled (bool): Indicates the deployment is managed by the Service Manager. Example:
                enabled_example
            id (str): An identifier that uniquely identifies this deployment. Example: id_example
            ogg_ssl_home (str): The deployment's SSL configuration directory. Example: oggSslHome_example
            status (str): Indicates the status of the deployment. Example: status_example
            ogg_etc_home (str): The deployment's etc configuration directory. Example: oggEtcHome_example
            ogg_var_home (str): The deployment's var user data directory. Example: oggVarHome_example
            environment (list): Additional environment variables for the deployment. Example:
                environment_example
            password_regex (str): The regular expression that new user passwords must match. Example:
                passwordRegex_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                ogg_home=None,
                ogg_data_home=None,
                ogg_conf_home=None,
                enabled=True,
                id=None,
                ogg_ssl_home=None,
                status=None,
                ogg_etc_home=None,
                ogg_var_home=None,
                environment=[
                    {
                        "name": None,
                        "value": None
                    }
                ],
                password_regex=None
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/deployments/{deployment}",
            path_params={
                "deployment": deployment,
            },
            data=data,
            body_params={
                "oggHome": ogg_home,
                "oggDataHome": ogg_data_home,
                "oggConfHome": ogg_conf_home,
                "enabled": enabled,
                "id": id,
                "oggSslHome": ogg_ssl_home,
                "status": status,
                "oggEtcHome": ogg_etc_home,
                "oggVarHome": ogg_var_home,
                "environment": environment,
                "passwordRegex": password_regex,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def delete_deployment(
        self,
        deployment,
        raw_response=False
    ):
        """
        Service Manager/Deployments
        DELETE /services/{version}/deployments/{deployment}
        Delete a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_deployment(
                deployment='deployment_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/deployments/{deployment}",
            path_params={
                "deployment": deployment,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services
    def list_services(
        self,
        deployment,
        raw_response=False
    ):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services
        Retrieve the collection of Oracle GoldenGate Services in a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_services(
                deployment='deployment_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/services",
            path_params={
                "deployment": deployment,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def get_service(
        self,
        deployment,
        service,
        raw_response=False
    ):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services/{service}
        Retrieve the details of a service in an Oracle GoldenGate deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_service(
                deployment='deployment_example',
                service='service_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/services/{service}",
            path_params={
                "deployment": deployment,
                "service": service,
            },
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
        config_force=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Services
        POST /services/{version}/deployments/{deployment}/services/{service}
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
            config_force (bool): Force the configuration data. Example: configForce_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                            "serviceListeningPort": 11001
                        },
                        "security": False,
                        "authorizationEnabled": True,
                        "defaultSynchronousWait": 30,
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
                        "serviceListeningPort": 11001
                    },
                    "security": False,
                    "authorizationEnabled": True,
                    "defaultSynchronousWait": 30,
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
                config_force=None
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/deployments/{deployment}/services/{service}",
            path_params={
                "deployment": deployment,
                "service": service,
            },
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
                "configForce": config_force,
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
        config_force=None,
        data=None,
        raw_response=False
    ):
        """
        Service Manager/Services
        PATCH /services/{version}/deployments/{deployment}/services/{service}
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
            config_force (bool): Force the configuration data. Example: configForce_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                config={
                    "security": None,
                    "umask": None,
                    "asynchronousOperationEnabled": None,
                    "serviceDiscoveryEnabled": None,
                    "hstsEnabled": None,
                    "network": {
                        "serviceListeningPort": None
                    },
                    "legacyProtocolEnabled": None,
                    "authorizationEnabled": None,
                    "securityDetails": {
                        "network": {
                            "common": {
                                "id": None,
                                "fipsEnabled": None
                            },
                            "inbound": None,
                            "outbound": None
                        }
                    },
                    "csrfHeaderProtectionEnabled": None,
                    "contentUrlRewrite": None,
                    "authorizationDetails": {
                        "sessionDurationSecs": None,
                        "useMovingExpirationWindow": None,
                        "movingExpirationWindowSecs": None,
                        "common": {
                            "allow": [
                                None
                            ],
                            "customAuthorizationEnabled": None
                        }
                    },
                    "taskManagerEnabled": None,
                    "hstsDetails": None,
                    "cors": None,
                    "defaultSynchronousWait": None,
                    "csrfTokenProtectionEnabled": None
                },
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
                config_force=None
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/deployments/{deployment}/services/{service}",
            path_params={
                "deployment": deployment,
                "service": service,
            },
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
                "configForce": config_force,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def delete_service(
        self,
        deployment,
        service,
        raw_response=False
    ):
        """
        Service Manager/Services
        DELETE /services/{version}/deployments/{deployment}/services/{service}
        Remove a service from an Oracle GoldenGate deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_service(
                deployment='deployment_example',
                service='service_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/deployments/{deployment}/services/{service}",
            path_params={
                "deployment": deployment,
                "service": service,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}/logs
    def list_service_logs(
        self,
        deployment,
        service,
        raw_response=False
    ):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services/{service}/logs
        Retrieve the set of logs for the service

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_service_logs(
                deployment='deployment_example',
                service='service_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/services/{service}/logs",
            path_params={
                "deployment": deployment,
                "service": service,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}/logs/default
    def get_service_log(
        self,
        deployment,
        service,
        raw_response=False
    ):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services/{service}/logs/default
        Retrieve the service log

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Required. Example:
                deployment_example
            service (str): Name of the service. Required. Example: service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_service_log(
                deployment='deployment_example',
                service='service_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/services/{service}/logs/default",
            path_params={
                "deployment": deployment,
                "service": service,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys
    def list_encryption_keys(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Encryption Keys
        GET /services/{version}/enckeys
        Retrieve the names of all encryption keys

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_encryption_keys()

        """
        return self._call(
            method="GET",
            template="/services/{version}/enckeys",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def get_encryption_key(
        self,
        key_name,
        raw_response=False
    ):
        """
        Administrative Server/Encryption Keys
        GET /services/{version}/enckeys/{keyName}
        Retrieve details for an Encryption Key.

        Parameters:
            key_name (str): The name of the Encryption Key. Required. Example: keyName_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_encryption_key(
                key_name='keyName_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/enckeys/{key_name}",
            path_params={
                "key_name": key_name,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def create_encryption_key(
        self,
        key_name,
        bit_length=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Encryption Keys
        POST /services/{version}/enckeys/{keyName}
        Create an Encryption Key.

        Parameters:
            key_name (str): The name of the Encryption Key. Required. Example: keyName_example
            bit_length (str): Length of the encryption key, in bits. Required if not included in `data`.
                Example: bitLength_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_encryption_key(
                key_name='keyName_example',
                data={
                    "bitLength": 128
                }
            )

            client.create_encryption_key(
                key_name='keyName_example',
                bit_length=128
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/enckeys/{key_name}",
            path_params={
                "key_name": key_name,
            },
            data=data,
            body_params={
                "bitLength": bit_length,
            },
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def delete_encryption_key(
        self,
        key_name,
        raw_response=False
    ):
        """
        Administrative Server/Encryption Keys
        DELETE /services/{version}/enckeys/{keyName}
        Delete an Encryption Key

        Parameters:
            key_name (str): The name of the Encryption Key. Required. Example: keyName_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_encryption_key(
                key_name='keyName_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/enckeys/{key_name}",
            path_params={
                "key_name": key_name,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}/encrypt
    def encrypt_data(
        self,
        key_name,
        encoding=None,
        data_1=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Encryption Keys
        POST /services/{version}/enckeys/{keyName}/encrypt
        Encrypt data using the Encryption Key.

        Parameters:
            key_name (str): The name of the Encryption Key. Required. Example: keyName_example
            encoding (str): Encoding to use for encrypted data in response. Example: encoding_example
            data (str): Data to be encrypted
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.encrypt_data(
                key_name='keyName_example',
                data={
                    "data": "plaintext-password"
                }
            )

            client.encrypt_data(
                key_name='keyName_example',
                encoding=None,
                data_1='plaintext-password'
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/enckeys/{key_name}/encrypt",
            path_params={
                "key_name": key_name,
            },
            data=data,
            body_params={
                "encoding": encoding,
                "data": data_1,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts
    def list_extracts(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        GET /services/{version}/extracts
        Retrieve the collection of Extract processes

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extracts()

        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def get_extract(
        self,
        extract,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        GET /services/{version}/extracts/{extract}
        Retrieve the details of an extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract(
                extract='extract_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}",
            path_params={
                "extract": extract,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def create_extract(
        self,
        extract,
        begin=None,
        passive=None,
        config=None,
        encryption_profile=None,
        status=None,
        critical=None,
        rollover=None,
        targets=None,
        managed_process_settings=None,
        intent=None,
        registration=None,
        source=None,
        type=None,
        mining_credentials=None,
        alias=None,
        credentials=None,
        description=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Extracts
        POST /services/{version}/extracts/{extract}
        Create a new extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            begin (dict): Starting point for data processing. Example: begin_example
            passive (bool): Passive extract controlled by an alias on the target. Example: passive_example
            config (list):  Example: config_example
            encryption_profile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the extract is critical to the deployment. Example: critical_example
            rollover (str): Causes Extract to increment to the next file in the trail sequence when
                restarting. Example: rollover_example
            targets (list): Targets for captured data. Example: targets_example
            managed_process_settings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            intent (str): Intent for data capture workflow. Example: intent_example
            registration (dict): Registration with the source database. Example: registration_example
            source (dict): Source of data to process. Example: source_example
            type (str): OGG Extract process type (read-only). Example: type_example
            mining_credentials (dict): Credentials for downstream mining database. Example:
                miningCredentials_example
            alias (dict):  Example: ggnorth
            credentials (dict): Credentials for source database. Example: credentials_example
            description (str): Description for the process. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_extract(
                extract='extract_example',
                data={
                    "config": [
                        "Extract     EXT2",
                        "ExtTrail    X2 Format Release 12.3",
                        "UseridAlias oggadmin",
                        "Table       oggadmin.*;"
                    ],
                    "source": {
                        "tranlogs": "integrated"
                    },
                    "credentials": {
                        "alias": "oggadmin"
                    },
                    "registration": "default",
                    "begin": "now",
                    "targets": [
                        {
                            "name": "X2"
                        }
                    ]
                }
            )

            client.create_extract(
                extract='extract_example',
                begin='now',
                passive=None,
                config=[
                    "Extract     EXT2",
                    "ExtTrail    X2 Format Release 12.3",
                    "UseridAlias oggadmin",
                    "Table       oggadmin.*;"
                ],
                encryption_profile=None,
                status=None,
                critical=None,
                rollover=None,
                targets=[
                    {
                        "name": "X2"
                    }
                ],
                managed_process_settings=None,
                intent=None,
                registration='default',
                source={
                    "tranlogs": "integrated"
                },
                type=None,
                mining_credentials=None,
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
                    "alias": "oggadmin"
                },
                description=None
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/extracts/{extract}",
            path_params={
                "extract": extract,
            },
            data=data,
            body_params={
                "begin": begin,
                "passive": passive,
                "config": config,
                "encryptionProfile": encryption_profile,
                "status": status,
                "critical": critical,
                "rollover": rollover,
                "targets": targets,
                "managedProcessSettings": managed_process_settings,
                "intent": intent,
                "registration": registration,
                "source": source,
                "type": type,
                "miningCredentials": mining_credentials,
                "alias": alias,
                "credentials": credentials,
                "description": description,
            },
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
        encryption_profile=None,
        status=None,
        critical=None,
        rollover=None,
        targets=None,
        managed_process_settings=None,
        intent=None,
        registration=None,
        source=None,
        type=None,
        mining_credentials=None,
        alias=None,
        credentials=None,
        description=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        PATCH /services/{version}/extracts/{extract}
        Update an existing extract process. A user with the 'Operator' role may change the "status" property.
            Any other changes require the 'Administrator' role.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            begin (dict): Starting point for data processing. Example: begin_example
            passive (bool): Passive extract controlled by an alias on the target. Example: passive_example
            config (list):  Example: config_example
            encryption_profile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the extract is critical to the deployment. Example: critical_example
            rollover (str): Causes Extract to increment to the next file in the trail sequence when
                restarting. Example: rollover_example
            targets (list): Targets for captured data. Example: targets_example
            managed_process_settings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            intent (str): Intent for data capture workflow. Example: intent_example
            registration (dict): Registration with the source database. Example: registration_example
            source (dict): Source of data to process. Example: source_example
            type (str): OGG Extract process type (read-only). Example: type_example
            mining_credentials (dict): Credentials for downstream mining database. Example:
                miningCredentials_example
            alias (dict):  Example: ggnorth
            credentials (dict): Credentials for source database. Example: credentials_example
            description (str): Description for the process. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                encryption_profile=None,
                status='running',
                critical=None,
                rollover=None,
                targets=[
                    None
                ],
                managed_process_settings=None,
                intent=None,
                registration=None,
                source=None,
                type=None,
                mining_credentials=None,
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
        return self._call(
            method="PATCH",
            template="/services/{version}/extracts/{extract}",
            path_params={
                "extract": extract,
            },
            data=data,
            body_params={
                "begin": begin,
                "passive": passive,
                "config": config,
                "encryptionProfile": encryption_profile,
                "status": status,
                "critical": critical,
                "rollover": rollover,
                "targets": targets,
                "managedProcessSettings": managed_process_settings,
                "intent": intent,
                "registration": registration,
                "source": source,
                "type": type,
                "miningCredentials": mining_credentials,
                "alias": alias,
                "credentials": credentials,
                "description": description,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def delete_extract(
        self,
        extract,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        DELETE /services/{version}/extracts/{extract}
        Delete an extract process. If the extract process is currently running, it is stopped first.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_extract(
                extract='extract_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/extracts/{extract}",
            path_params={
                "extract": extract,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/command
    def execute_command_extract(
        self,
        extract,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        POST /services/{version}/extracts/{extract}/command
        Execute an Extract process command

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            data (dict): Data payload. See call example below for more details.
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
        return self._call(
            method="POST",
            template="/services/{version}/extracts/{extract}/command",
            path_params={
                "extract": extract,
            },
            data=data,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info
    def get_extract_info_types(
        self,
        extract,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        GET /services/{version}/extracts/{extract}/info
        Retrieve types of information available for an extract.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_info_types(
                extract='extract_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info",
            path_params={
                "extract": extract,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/checkpoints
    def get_extract_checkpoint(
        self,
        extract,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        GET /services/{version}/extracts/{extract}/info/checkpoints
        Retrieve the checkpoint information for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_checkpoint(
                extract='extract_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info/checkpoints",
            path_params={
                "extract": extract,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/history
    def get_extract_history(
        self,
        extract,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        GET /services/{version}/extracts/{extract}/info/history
        Retrieve the execution history of a managed extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_history(
                extract='extract_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info/history",
            path_params={
                "extract": extract,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/reports
    def list_extract_reports(
        self,
        extract,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        GET /services/{version}/extracts/{extract}/info/reports
        Retrieve the list of reports available for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extract_reports(
                extract='extract_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info/reports",
            path_params={
                "extract": extract,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/reports/{report}
    def get_extract_report(
        self,
        extract,
        report,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        GET /services/{version}/extracts/{extract}/info/reports/{report}
        Retrieve a report from the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            report (str): The name of the report, which is the extract name, followed by an optional
                revision number and '.rpt'. Required. Example: report_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_report(
                extract='extract_example',
                report='report_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info/reports/{report}",
            path_params={
                "extract": extract,
                "report": report,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/status
    def get_extract_status(
        self,
        extract,
        raw_response=False
    ):
        """
        Administrative Server/Extracts
        GET /services/{version}/extracts/{extract}/info/status
        Retrieve the current status of the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Required. Example:
                extract_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_status(
                extract='extract_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info/status",
            path_params={
                "extract": extract,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/exttrails
    def list_extract_trails(
        self,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/exttrails

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extract_trails()

        """
        return self._call(
            method="GET",
            template="/services/{version}/exttrails",
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/deployments
    def list_installation_deployments(
        self,
        raw_response=False
    ):
        """
        Service Manager/Installation
        GET /services/{version}/installation/deployments
        Retrieve a list of all Oracle GoldenGate deployments for the installation.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_deployments()

        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/deployments",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/services
    def list_installation_services(
        self,
        raw_response=False
    ):
        """
        Service Manager/Installation
        GET /services/{version}/installation/services
        Retrieve a list of all Oracle GoldenGate services for the installation.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_services()

        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/services",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs
    def list_logs(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Logs
        GET /services/{version}/logs
        Retrieve the set of logs for ER processes

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_logs()

        """
        return self._call(
            method="GET",
            template="/services/{version}/logs",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/events
    def list_log_events(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Logs
        GET /services/{version}/logs/events
        This endpoint provides a log of all critical events that occur in replication processes.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_log_events()

        """
        return self._call(
            method="GET",
            template="/services/{version}/logs/events",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/{log}
    def get_log(
        self,
        log,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Logs
        GET /services/{version}/logs/{log}
        Retrieve an application log

        Parameters:
            log (str): Name of the log. Required. Example: log_example
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
        return self._call(
            method="GET",
            template="/services/{version}/logs/{log}",
            path_params={
                "log": log,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/{log}
    def update_log(
        self,
        log,
        enabled=None,
        data=None,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Logs
        PATCH /services/{version}/logs/{log}
        Update application log properties.
        Not all logs can be modified, and if a PATCH operation is issued for a read-only log a status code of
            400 Bad Request is returned.

        Parameters:
            log (str): Name of the log. Required. Example: log_example
            enabled (bool): Required if not included in `data`. Example: enabled_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                enabled=True
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/logs/{log}",
            path_params={
                "log": log,
            },
            data=data,
            body_params={
                "enabled": enabled,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/{log}
    def delete_log(
        self,
        log,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Logs
        DELETE /services/{version}/logs/{log}
        Clear the contents of an application log.
        Not all logs can be modified, and if a DELETE operation is issued for a read-only log a status code of
            400 Bad Request is returned.

        Parameters:
            log (str): Name of the log. Required. Example: log_example
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
        return self._call(
            method="DELETE",
            template="/services/{version}/logs/{log}",
            path_params={
                "log": log,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey
    def list_master_key_versions(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Master Keys
        GET /services/{version}/masterkey
        Retrieve all versions of the Master Key

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_master_key_versions()

        """
        return self._call(
            method="GET",
            template="/services/{version}/masterkey",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey
    def create_master_key_version(
        self,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Master Keys
        POST /services/{version}/masterkey
        Create a new Master Key version

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_master_key_version()

        """
        return self._call(
            method="POST",
            template="/services/{version}/masterkey",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def get_master_key_version(
        self,
        key_version,
        raw_response=False
    ):
        """
        Administrative Server/Master Keys
        GET /services/{version}/masterkey/{keyVersion}
        Retrieve a Master Key by version.

        Parameters:
            key_version (int): The Master Key version number, 1 to 32767. Required. Example: 1
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_master_key_version(
                key_version=1
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/masterkey/{key_version}",
            path_params={
                "key_version": key_version,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def update_master_key_version(
        self,
        key_version,
        created=None,
        status=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Master Keys
        PATCH /services/{version}/masterkey/{keyVersion}
        Update a Master Key version

        Parameters:
            key_version (int): The Master Key version number, 1 to 32767. Required. Example: 1
            created (str):  Example: created_example
            status (str): Required if not included in `data`. Example: status_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_master_key_version(
                key_version=1,
                data={
                    "status": "unavailable"
                }
            )

            client.update_master_key_version(
                key_version=1,
                created=None,
                status='unavailable'
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/masterkey/{key_version}",
            path_params={
                "key_version": key_version,
            },
            data=data,
            body_params={
                "created": created,
                "status": status,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def delete_master_key_version(
        self,
        key_version,
        raw_response=False
    ):
        """
        Administrative Server/Master Keys
        DELETE /services/{version}/masterkey/{keyVersion}
        Delete a Master Key version

        Parameters:
            key_version (int): The Master Key version number, 1 to 32767. Required. Example: 1
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_master_key_version(
                key_version=1
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/masterkey/{key_version}",
            path_params={
                "key_version": key_version,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/messages
    def list_messages(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Messages
        GET /services/{version}/messages
        Retrieve messages from the Oracle GoldenGate deployment.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_messages()

        """
        return self._call(
            method="GET",
            template="/services/{version}/messages",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/metadata-catalog
    def get_metadata_catalog(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/REST API Catalog
        GET /services/{version}/metadata-catalog
        The REST API catalog contains information about resources provided by each Oracle GoldenGate Service.
            Use this endpoint to retrieve a collection of all items in the catalog.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_metadata_catalog(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/metadata-catalog",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/metadata-catalog/{resource}
    def get_metadata_catalog_resource(
        self,
        resource,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/REST API Catalog
        GET /services/{version}/metadata-catalog/{resource}
        Use this endpoint to describe a single item in the metadata catalog. A list of items in the metadata
            catalog is obtained using the Retrieve Catalog endpoint.

        Parameters:
            resource (str): Name of the item in the metadata catalog. Required. Example: resource_example
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
        return self._call(
            method="GET",
            template="/services/{version}/metadata-catalog/{resource}",
            path_params={
                "resource": resource,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/commands
    def list_monitoring_commands(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Server/Commands
        GET /services/{version}/monitoring/commands

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_monitoring_commands()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/commands",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/commands/execute
    def execute_monitoring_command(
        self,
        data=None,
        raw_response=False
    ):
        """
        Performance Metrics Server/Commands
        POST /services/{version}/monitoring/commands/execute

        Parameters:
            data (dict): Data payload. See call example below for more details.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.execute_monitoring_command(
                data={
                    "name": "purgeDatastore",
                    "daysValue": 90
                }
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/monitoring/commands/execute",
            data=data,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/lastMessageId
    def get_last_monitoring_message_id(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Server/Last Message Number
        GET /services/{version}/monitoring/lastMessageId

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_last_monitoring_message_id()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/lastMessageId",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/lastStatusChangeId
    def get_last_status_change_id(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Server/Last Status Change Id Number
        GET /services/{version}/monitoring/lastStatusChangeId

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_last_status_change_id()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/lastStatusChangeId",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/messages
    def get_monitoring_messages(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Server/Messages
        GET /services/{version}/monitoring/messages

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_monitoring_messages()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/messages",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/statusChanges
    def list_status_changes(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Server/Status Changes
        GET /services/{version}/monitoring/statusChanges

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_status_changes()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/statusChanges",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/{item}/messages
    def list_process_messages(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Messages
        GET /services/{version}/monitoring/{item}/messages

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_process_messages(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/{item}/messages",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/{item}/statusChanges
    def list_process_status_changes(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Status Changes
        GET /services/{version}/monitoring/{item}/statusChanges

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_process_status_changes(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/{item}/statusChanges",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/processes
    def list_processes(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/processes

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_processes()

        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/processes",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/batchSqlStatistics
    def get_process_batch_sql_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Replicat Metrics
        GET /services/{version}/mpoints/{item}/batchSqlStatistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_batch_sql_statistics(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/batchSqlStatistics",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/cacheStatistics
    def get_process_cache_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/cacheStatistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_cache_statistics(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/cacheStatistics",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/configurationEr
    def get_er_configuration(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/ER Metrics
        GET /services/{version}/mpoints/{item}/configurationEr

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_er_configuration(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/configurationEr",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/configurationManager
    def get_manager_configuration(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/ER Metrics
        GET /services/{version}/mpoints/{item}/configurationManager

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_manager_configuration(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/configurationManager",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/coordinationReplicat
    def get_process_coordination_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Replicat Metrics
        GET /services/{version}/mpoints/{item}/coordinationReplicat

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_coordination_replicat(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/coordinationReplicat",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/currentInflightTransactions
    def get_current_inflight_transactions(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Extract Metrics
        GET /services/{version}/mpoints/{item}/currentInflightTransactions

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_current_inflight_transactions(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/currentInflightTransactions",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/databaseInOut
    def get_process_database_in_out(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/databaseInOut

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_database_in_out(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/databaseInOut",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/dependencyStats
    def get_process_dependency_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Replicat Metrics
        GET /services/{version}/mpoints/{item}/dependencyStats

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_dependency_stats(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/dependencyStats",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrChunkStats
    def get_process_distsrvr_chunk_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrChunkStats

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_distsrvr_chunk_stats(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/distsrvrChunkStats",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrNetworkStats
    def get_process_distsrvr_network_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrNetworkStats

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_distsrvr_network_stats(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/distsrvrNetworkStats",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrPathStats
    def get_process_distsrvr_path_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrPathStats

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_distsrvr_path_stats(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/distsrvrPathStats",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrTableStats
    def get_process_distsrvr_table_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrTableStats

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_distsrvr_table_stats(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/distsrvrTableStats",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/networkStatistics
    def get_process_network_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/networkStatistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_network_statistics(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/networkStatistics",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/parallelReplicat
    def get_process_parallel_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Replicat Metrics
        GET /services/{version}/mpoints/{item}/parallelReplicat

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_parallel_replicat(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/parallelReplicat",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrProcStats
    def get_process_pmsrvr_proc_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrProcStats

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_pmsrvr_proc_stats(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/pmsrvrProcStats",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrStats
    def get_process_pmsrvr_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrStats

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_pmsrvr_stats(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/pmsrvrStats",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrWorkerStats
    def get_process_pmsrvr_worker_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrWorkerStats

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_pmsrvr_worker_stats(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/pmsrvrWorkerStats",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/positionEr
    def get_process_position_er(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/ER Metrics
        GET /services/{version}/mpoints/{item}/positionEr

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_position_er(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/positionEr",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/process
    def get_process_info(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/process

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_info(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/process",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/processPerformance
    def get_process_performance(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/processPerformance

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_performance(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/processPerformance",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/queueBucketStatistics
    def get_process_queue_bucket_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/queueBucketStatistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_queue_bucket_statistics(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/queueBucketStatistics",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/queueStatistics
    def get_process_queue_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/queueStatistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_queue_statistics(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/queueStatistics",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/recvsrvrStats
    def get_process_recvsrvr_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Service Metrics
        GET /services/{version}/mpoints/{item}/recvsrvrStats

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_recvsrvr_stats(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/recvsrvrStats",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsExtract
    def get_process_statistics_extract(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsExtract

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_extract(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/statisticsExtract",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsProcedureExtract
    def get_process_statistics_procedure_extract(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsProcedureExtract

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_procedure_extract(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/statisticsProcedureExtract",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsProcedureReplicat
    def get_process_statistics_procedure_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsProcedureReplicat

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_procedure_replicat(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/statisticsProcedureReplicat",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsReplicat
    def get_process_statistics_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsReplicat

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_replicat(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/statisticsReplicat",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsTableExtract
    def get_process_statistics_table_extract(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsTableExtract

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_table_extract(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/statisticsTableExtract",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsTableReplicat
    def get_process_statistics_table_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsTableReplicat

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_statistics_table_replicat(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/statisticsTableReplicat",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/superpoolStatistics
    def get_process_superpool_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/superpoolStatistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_superpool_statistics(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/superpoolStatistics",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/threadPerformance
    def get_process_thread_performance(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/threadPerformance

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_thread_performance(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/threadPerformance",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/trailInput
    def get_process_trail_input(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/trailInput

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_trail_input(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/trailInput",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/trailOutput
    def get_process_trail_output(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Server/Process Metrics
        GET /services/{version}/mpoints/{item}/trailOutput

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_trail_output(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/trailOutput",
            path_params={
                "item": item,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/oggerr
    def list_ogg_errors(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Message Codes
        GET /services/{version}/oggerr
        Retrieve all message codes from the Oracle GoldenGate deployment.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_ogg_errors(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/oggerr",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/oggerr/{message}
    def get_ogg_error_info(
        self,
        message,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Message Codes
        GET /services/{version}/oggerr/{message}
        Retrieve a detailed explanation for an Oracle GoldenGate message.

        Parameters:
            message (str): The Oracle GoldenGate Message Code, OGG-99999. Required. Example: message_example
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
        return self._call(
            method="GET",
            template="/services/{version}/oggerr/{message}",
            path_params={
                "message": message,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/parameters
    def list_parameters(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Parameters
        GET /services/{version}/parameters
        Retrieve names of all known OGG parameters.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_parameters()

        """
        return self._call(
            method="GET",
            template="/services/{version}/parameters",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/parameters/{parameter}
    def get_parameter_info(
        self,
        parameter,
        raw_response=False
    ):
        """
        Administrative Server/Parameters
        GET /services/{version}/parameters/{parameter}
        Retrieve details for a parameter.

        Parameters:
            parameter (str): Name of parameter for information request. Required. Example: parameter_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_parameter_info(
                parameter='parameter_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/parameters/{parameter}",
            path_params={
                "parameter": parameter,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats
    def list_replicats(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        GET /services/{version}/replicats
        Retrieve the collection of Replicat processes

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_replicats()

        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def get_replicat(
        self,
        replicat,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        GET /services/{version}/replicats/{replicat}
        Retrieve the details of an replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat(
                replicat='replicat_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}",
            path_params={
                "replicat": replicat,
            },
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
        encryption_profile=None,
        status=None,
        critical=None,
        managed_process_settings=None,
        intent=None,
        checkpoint=None,
        registration=None,
        source=None,
        credentials=None,
        description=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Replicats
        POST /services/{version}/replicats/{replicat}
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
            encryption_profile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the replicat is critical to the deployment. Example: critical_example
            managed_process_settings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            intent (str): Intent for data capture workflow. Example: intent_example
            checkpoint (dict): Location for checkpoint data. Example: checkpoint_example
            registration (str): Registration with the target database. Example: registration_example
            source (dict): Source of data to process. Example: source_example
            credentials (dict): Credentials for target database. Example: credentials_example
            description (str): Description for the process. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_replicat(
                replicat='replicat_example',
                data={
                    "config": [
                        "Replicat    REP2",
                        "UseridAlias oggadmin",
                        "Map         oggadmin.*,",
                        "  Target    oggadmin.*;"
                    ],
                    "source": {
                        "name": "X2"
                    },
                    "credentials": {
                        "alias": "oggadmin"
                    },
                    "checkpoint": {
                        "table": "oggadmin.checkpoints"
                    }
                }
            )

            client.create_replicat(
                replicat='replicat_example',
                begin=None,
                config=[
                    "Replicat    REP2",
                    "UseridAlias oggadmin",
                    "Map         oggadmin.*,",
                    "  Target    oggadmin.*;"
                ],
                synchronized=None,
                mode=None,
                encryption_profile=None,
                status=None,
                critical=None,
                managed_process_settings=None,
                intent=None,
                checkpoint={
                    "table": "oggadmin.checkpoints"
                },
                registration=None,
                source={
                    "name": "X2"
                },
                credentials={
                    "alias": "oggadmin"
                },
                description=None
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/replicats/{replicat}",
            path_params={
                "replicat": replicat,
            },
            data=data,
            body_params={
                "begin": begin,
                "config": config,
                "synchronized": synchronized,
                "mode": mode,
                "encryptionProfile": encryption_profile,
                "status": status,
                "critical": critical,
                "managedProcessSettings": managed_process_settings,
                "intent": intent,
                "checkpoint": checkpoint,
                "registration": registration,
                "source": source,
                "credentials": credentials,
                "description": description,
            },
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
        encryption_profile=None,
        status=None,
        critical=None,
        managed_process_settings=None,
        intent=None,
        checkpoint=None,
        registration=None,
        source=None,
        credentials=None,
        description=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        PATCH /services/{version}/replicats/{replicat}
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
            encryption_profile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the replicat is critical to the deployment. Example: critical_example
            managed_process_settings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            intent (str): Intent for data capture workflow. Example: intent_example
            checkpoint (dict): Location for checkpoint data. Example: checkpoint_example
            registration (str): Registration with the target database. Example: registration_example
            source (dict): Source of data to process. Example: source_example
            credentials (dict): Credentials for target database. Example: credentials_example
            description (str): Description for the process. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                encryption_profile=None,
                status='running',
                critical=None,
                managed_process_settings=None,
                intent=None,
                checkpoint=None,
                registration=None,
                source=None,
                credentials=None,
                description=None
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/replicats/{replicat}",
            path_params={
                "replicat": replicat,
            },
            data=data,
            body_params={
                "begin": begin,
                "config": config,
                "synchronized": synchronized,
                "mode": mode,
                "encryptionProfile": encryption_profile,
                "status": status,
                "critical": critical,
                "managedProcessSettings": managed_process_settings,
                "intent": intent,
                "checkpoint": checkpoint,
                "registration": registration,
                "source": source,
                "credentials": credentials,
                "description": description,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def delete_replicat(
        self,
        replicat,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        DELETE /services/{version}/replicats/{replicat}
        Delete a replicat process. If the replicat process is currently running, it is stopped first.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_replicat(
                replicat='replicat_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/replicats/{replicat}",
            path_params={
                "replicat": replicat,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/command
    def execute_command_replicat(
        self,
        replicat,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        POST /services/{version}/replicats/{replicat}/command
        Execute a Replicat process command

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            data (dict): Data payload. See call example below for more details.
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
        return self._call(
            method="POST",
            template="/services/{version}/replicats/{replicat}/command",
            path_params={
                "replicat": replicat,
            },
            data=data,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info
    def get_replicat_info(
        self,
        replicat,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        GET /services/{version}/replicats/{replicat}/info
        Retrieve types of information available for a replicat.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_info(
                replicat='replicat_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info",
            path_params={
                "replicat": replicat,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/checkpoints
    def get_replicat_checkpoint(
        self,
        replicat,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        GET /services/{version}/replicats/{replicat}/info/checkpoints
        Retrieve the checkpoint information for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_checkpoint(
                replicat='replicat_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info/checkpoints",
            path_params={
                "replicat": replicat,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/history
    def get_replicat_history(
        self,
        replicat,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        GET /services/{version}/replicats/{replicat}/info/history
        Retrieve the execution history of a managed replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_history(
                replicat='replicat_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info/history",
            path_params={
                "replicat": replicat,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/reports
    def list_replicat_reports(
        self,
        replicat,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        GET /services/{version}/replicats/{replicat}/info/reports
        Retrieve the list of reports available for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_replicat_reports(
                replicat='replicat_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info/reports",
            path_params={
                "replicat": replicat,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/reports/{report}
    def get_replicat_report(
        self,
        replicat,
        report,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        GET /services/{version}/replicats/{replicat}/info/reports/{report}
        Retrieve a report from the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            report (str): The name of the report, which is the replicat name, followed by an optional
                revision number and '.rpt'. Required. Example: report_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_report(
                replicat='replicat_example',
                report='report_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info/reports/{report}",
            path_params={
                "replicat": replicat,
                "report": report,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/status
    def get_replicat_status(
        self,
        replicat,
        raw_response=False
    ):
        """
        Administrative Server/Replicats
        GET /services/{version}/replicats/{replicat}/info/status
        Retrieve the current status of the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Required. Example:
                replicat_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_status(
                replicat='replicat_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info/status",
            path_params={
                "replicat": replicat,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/requests
    def list_restapi_requests(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Requests
        GET /services/{version}/requests
        Retrieve the collection of background REST API requests.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_restapi_requests(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/requests",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/requests/{request}
    def get_restapi_request_status(
        self,
        request,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Requests
        GET /services/{version}/requests/{request}
        Retrieve the background request status.

        Parameters:
            request (int): Identifier for background request. Required. Example: 1
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
        return self._call(
            method="GET",
            template="/services/{version}/requests/{request}",
            path_params={
                "request": request,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/requests/{request}/result
    def get_restapi_request_result(
        self,
        request,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Requests
        GET /services/{version}/requests/{request}/result
        Retrieve the background request result.

        Parameters:
            request (int): Identifier for background request. Required. Example: 1
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
        return self._call(
            method="GET",
            template="/services/{version}/requests/{request}/result",
            path_params={
                "request": request,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources
    def list_distribution_paths(
        self,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_distribution_paths()

        """
        return self._call(
            method="GET",
            template="/services/{version}/sources",
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def get_distribution_path(
        self,
        distpath,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}

        Parameters:
            distpath (str): Required. Example: distpath_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_distribution_path(
                distpath='distpath_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/sources/{distpath}",
            path_params={
                "distpath": distpath,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def create_distribution_path(
        self,
        distpath,
        begin=None,
        name=None,
        encryption_profile=None,
        status=None,
        target_initiated=None,
        ruleset=None,
        source=None,
        target=None,
        options=None,
        description=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Distribution Service
        POST /services/{version}/sources/{distpath}

        Parameters:
            distpath (str): Required. Example: distpath_example
            begin (dict): Starting point for data processing. Example: begin_example
            name (str): distribution path name. Example: name_example
            encryption_profile (str): Name of 'ogg:encryptionProfile' value. Example:
                encryptionProfile_example
            status (dict): Oracle GoldenGate Distribution Path Status. Example: status_example
            target_initiated (bool): Whether the target endpoint initiates the path. If true, the path needs
                to be created and modified through Receiver Server, who initiates the connection with
                Distribution Server. Otherwise, this behavior is reversed. Example: targetInitiated_example
            ruleset (dict):  Example: ruleset_example
            source (dict): source endpoint of the path. Example: source_example
            target (dict): target endpoint of the path. Example: target_example
            options (dict): options for the distribution path. Example: options_example
            description (str): Description for the path. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_distribution_path(
                distpath='distpath_example',
                data={
                    "$schema": "ogg:distPath",
                    "name": "path1",
                    "description": "my test distPath",
                    "source": {
                        "uri": "trail://localhost:7999/dirdat/a1"
                    },
                    "target": {
                        "uri": "ogg://adc00oye:7999/dirdat/t1"
                    },
                    "begin": {
                        "sequence": 0,
                        "offset": 0
                    },
                    "status": "running"
                }
            )

            client.create_distribution_path(
                distpath='distpath_example',
                begin={
                    "sequence": 0,
                    "offset": 0
                },
                name='path1',
                encryption_profile=None,
                status='running',
                target_initiated=None,
                ruleset=None,
                source={
                    "uri": "trail://localhost:7999/dirdat/a1"
                },
                target={
                    "uri": "ogg://adc00oye:7999/dirdat/t1"
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
        return self._call(
            method="POST",
            template="/services/{version}/sources/{distpath}",
            path_params={
                "distpath": distpath,
            },
            data=data,
            body_params={
                "begin": begin,
                "name": name,
                "encryptionProfile": encryption_profile,
                "status": status,
                "targetInitiated": target_initiated,
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
    def update_distribution_path(
        self,
        distpath,
        begin=None,
        name=None,
        encryption_profile=None,
        status=None,
        target_initiated=None,
        ruleset=None,
        source=None,
        target=None,
        options=None,
        description=None,
        data=None,
        raw_response=False
    ):
        """
        Distribution Service
        PATCH /services/{version}/sources/{distpath}
        Update an existing distribution path. A user with the Operator role may change the status property. Any
            other changes require the Administrator role.

        Parameters:
            distpath (str): Required. Example: distpath_example
            begin (dict): Starting point for data processing. Example: begin_example
            name (str): distribution path name. Example: name_example
            encryption_profile (str): Name of 'ogg:encryptionProfile' value. Example:
                encryptionProfile_example
            status (dict): Oracle GoldenGate Distribution Path Status. Example: status_example
            target_initiated (bool): Whether the target endpoint initiates the path. If true, the path needs
                to be created and modified through Receiver Server, who initiates the connection with
                Distribution Server. Otherwise, this behavior is reversed. Example: targetInitiated_example
            ruleset (dict):  Example: ruleset_example
            source (dict): source endpoint of the path. Example: source_example
            target (dict): target endpoint of the path. Example: target_example
            options (dict): options for the distribution path. Example: options_example
            description (str): Description for the path. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_distribution_path(
                distpath='distpath_example',
                data={
                    "$schema": "ogg:distPath",
                    "status": "stopped"
                }
            )

            client.update_distribution_path(
                distpath='distpath_example',
                begin=None,
                name=None,
                encryption_profile=None,
                status='stopped',
                target_initiated=None,
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
                    "details": {
                        "trail": {
                            "name": None,
                            "path": None,
                            "format": None,
                            "sizeMB": None,
                            "seqLength": None
                        },
                        "encryption": {
                            "algorithm": None,
                            "keyname": None
                        },
                        "compression": {
                            "enabled": None,
                            "threshold": None
                        }
                    },
                    "isDynamicOggPort": None
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
                    "details": {
                        "trail": {
                            "name": None,
                            "path": None,
                            "format": None,
                            "sizeMB": None,
                            "seqLength": None
                        },
                        "encryption": {
                            "algorithm": None,
                            "keyname": None
                        },
                        "compression": {
                            "enabled": None,
                            "threshold": None
                        }
                    },
                    "isDynamicOggPort": None
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
        return self._call(
            method="PATCH",
            template="/services/{version}/sources/{distpath}",
            path_params={
                "distpath": distpath,
            },
            data=data,
            body_params={
                "begin": begin,
                "name": name,
                "encryptionProfile": encryption_profile,
                "status": status,
                "targetInitiated": target_initiated,
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
    def delete_distribution_path(
        self,
        distpath,
        raw_response=False
    ):
        """
        Distribution Service
        DELETE /services/{version}/sources/{distpath}

        Parameters:
            distpath (str): Required. Example: distpath_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_distribution_path(
                distpath='distpath_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/sources/{distpath}",
            path_params={
                "distpath": distpath,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}/checkpoints
    def get_distribution_path_checkpoint(
        self,
        distpath,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}/checkpoints

        Parameters:
            distpath (str): Required. Example: distpath_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_distribution_path_checkpoint(
                distpath='distpath_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/sources/{distpath}/checkpoints",
            path_params={
                "distpath": distpath,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}/info
    def get_distribution_path_info(
        self,
        distpath,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}/info

        Parameters:
            distpath (str): Required. Example: distpath_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_distribution_path_info(
                distpath='distpath_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/sources/{distpath}/info",
            path_params={
                "distpath": distpath,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}/stats
    def get_distribution_path_stats(
        self,
        distpath,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}/stats

        Parameters:
            distpath (str): Required. Example: distpath_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_distribution_path_stats(
                distpath='distpath_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/sources/{distpath}/stats",
            path_params={
                "distpath": distpath,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets
    def list_receiver_paths(
        self,
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_receiver_paths()

        """
        return self._call(
            method="GET",
            template="/services/{version}/targets",
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}
    def get_receiver_path(
        self,
        path,
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}

        Parameters:
            path (str): Required. Example: path_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_path(
                path='path_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/targets/{path}",
            path_params={
                "path": path,
            },
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}
    def create_receiver_path(
        self,
        path,
        begin=None,
        name=None,
        encryption_profile=None,
        status=None,
        target_initiated=None,
        ruleset=None,
        source=None,
        target=None,
        options=None,
        description=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Receiver Service
        POST /services/{version}/targets/{path}

        Parameters:
            path (str): Required. Example: path_example
            begin (dict): Starting point for data processing. Example: begin_example
            name (str): distribution path name. Example: name_example
            encryption_profile (str): Name of 'ogg:encryptionProfile' value. Example:
                encryptionProfile_example
            status (dict): Oracle GoldenGate Distribution Path Status. Example: status_example
            target_initiated (bool): Whether the target endpoint initiates the path. If true, the path needs
                to be created and modified through Receiver Server, who initiates the connection with
                Distribution Server. Otherwise, this behavior is reversed. Example: targetInitiated_example
            ruleset (dict):  Example: ruleset_example
            source (dict): source endpoint of the path. Example: source_example
            target (dict): target endpoint of the path. Example: target_example
            options (dict): options for the distribution path. Example: options_example
            description (str): Description for the path. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_receiver_path(
                path='path_example',
                data={
                    "$schema": "ogg:distPath",
                    "name": "path1",
                    "description": "my test distPath",
                    "source": {
                        "uri": "trail://localhost:7999/dirdat/a1"
                    },
                    "target": {
                        "uri": "ogg://adc00oye:7999/dirdat/t1"
                    },
                    "begin": {
                        "sequence": 0,
                        "offset": 0
                    },
                    "status": "running"
                }
            )

            client.create_receiver_path(
                path='path_example',
                begin={
                    "sequence": 0,
                    "offset": 0
                },
                name='path1',
                encryption_profile=None,
                status='running',
                target_initiated=None,
                ruleset=None,
                source={
                    "uri": "trail://localhost:7999/dirdat/a1"
                },
                target={
                    "uri": "ogg://adc00oye:7999/dirdat/t1"
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
        return self._call(
            method="POST",
            template="/services/{version}/targets/{path}",
            path_params={
                "path": path,
            },
            data=data,
            body_params={
                "begin": begin,
                "name": name,
                "encryptionProfile": encryption_profile,
                "status": status,
                "targetInitiated": target_initiated,
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
    def update_receiver_path(
        self,
        path,
        begin=None,
        name=None,
        encryption_profile=None,
        status=None,
        target_initiated=None,
        ruleset=None,
        source=None,
        target=None,
        options=None,
        description=None,
        data=None,
        raw_response=False
    ):
        """
        Receiver Service
        PATCH /services/{version}/targets/{path}

        Parameters:
            path (str): Required. Example: path_example
            begin (dict): Starting point for data processing. Example: begin_example
            name (str): distribution path name. Example: name_example
            encryption_profile (str): Name of 'ogg:encryptionProfile' value. Example:
                encryptionProfile_example
            status (dict): Oracle GoldenGate Distribution Path Status. Example: status_example
            target_initiated (bool): Whether the target endpoint initiates the path. If true, the path needs
                to be created and modified through Receiver Server, who initiates the connection with
                Distribution Server. Otherwise, this behavior is reversed. Example: targetInitiated_example
            ruleset (dict):  Example: ruleset_example
            source (dict): source endpoint of the path. Example: source_example
            target (dict): target endpoint of the path. Example: target_example
            options (dict): options for the distribution path. Example: options_example
            description (str): Description for the path. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_receiver_path(
                path='path_example',
                data={
                    "options": {
                        "network": {
                            "appOptions": {
                                "appFlushBytes": 24859,
                                "appFlushSecs": 2
                            }
                        }
                    }
                }
            )

            client.update_receiver_path(
                path='path_example',
                begin=None,
                name=None,
                encryption_profile=None,
                status=None,
                target_initiated=None,
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
                    "details": {
                        "trail": {
                            "name": None,
                            "path": None,
                            "format": None,
                            "sizeMB": None,
                            "seqLength": None
                        },
                        "encryption": {
                            "algorithm": None,
                            "keyname": None
                        },
                        "compression": {
                            "enabled": None,
                            "threshold": None
                        }
                    },
                    "isDynamicOggPort": None
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
                    "details": {
                        "trail": {
                            "name": None,
                            "path": None,
                            "format": None,
                            "sizeMB": None,
                            "seqLength": None
                        },
                        "encryption": {
                            "algorithm": None,
                            "keyname": None
                        },
                        "compression": {
                            "enabled": None,
                            "threshold": None
                        }
                    },
                    "isDynamicOggPort": None
                },
                options={
                    "network": {
                        "appOptions": {
                            "appFlushBytes": 24859,
                            "appFlushSecs": 2
                        }
                    }
                },
                description=None
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/targets/{path}",
            path_params={
                "path": path,
            },
            data=data,
            body_params={
                "begin": begin,
                "name": name,
                "encryptionProfile": encryption_profile,
                "status": status,
                "targetInitiated": target_initiated,
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
    def delete_receiver_path(
        self,
        path,
        raw_response=False
    ):
        """
        Receiver Service
        DELETE /services/{version}/targets/{path}

        Parameters:
            path (str): Required. Example: path_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_receiver_path(
                path='path_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/targets/{path}",
            path_params={
                "path": path,
            },
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}/checkpoints
    def get_receiver_path_checkpoint(
        self,
        path,
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/checkpoints

        Parameters:
            path (str): Required. Example: path_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_path_checkpoint(
                path='path_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/targets/{path}/checkpoints",
            path_params={
                "path": path,
            },
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}/info
    def get_receiver_path_info(
        self,
        path,
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/info

        Parameters:
            path (str): Required. Example: path_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_path_info(
                path='path_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/targets/{path}/info",
            path_params={
                "path": path,
            },
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}/progress
    def get_receiver_path_progress(
        self,
        path,
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/progress

        Parameters:
            path (str): Required. Example: path_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_path_progress(
                path='path_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/targets/{path}/progress",
            path_params={
                "path": path,
            },
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}/stats
    def get_receiver_path_stats(
        self,
        path,
        raw_response=False
    ):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/stats

        Parameters:
            path (str): Required. Example: path_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_receiver_path_stats(
                path='path_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/targets/{path}/stats",
            path_params={
                "path": path,
            },
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks
    def list_tasks(
        self,
        raw_response=False
    ):
        """
        Administrative Server/Tasks
        GET /services/{version}/tasks
        Retrieve the list of tasks

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_tasks()

        """
        return self._call(
            method="GET",
            template="/services/{version}/tasks",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def get_task(
        self,
        task,
        raw_response=False
    ):
        """
        Administrative Server/Tasks
        GET /services/{version}/tasks/{task}
        Retrieve the details for a task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_task(
                task='task_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/tasks/{task}",
            path_params={
                "task": task,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def create_task(
        self,
        task,
        max_history=None,
        command=None,
        enabled=None,
        schedule=None,
        status=None,
        timeout=None,
        critical=None,
        restart=None,
        description=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administrative Server/Tasks
        POST /services/{version}/tasks/{task}
        Create a new administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            max_history (int): Number of task executions to maintain history for. Example:
                maxHistory_example
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
                            "value": 1
                        }
                    },
                    "command": {
                        "name": "report",
                        "reportType": "lag",
                        "thresholds": [
                            {
                                "type": "critical",
                                "units": "seconds",
                                "value": 5
                            }
                        ]
                    }
                }
            )

            client.create_task(
                task='task_example',
                max_history=None,
                command={
                    "name": "report",
                    "reportType": "lag",
                    "thresholds": [
                        {
                            "type": "critical",
                            "units": "seconds",
                            "value": 5
                        }
                    ]
                },
                enabled=False,
                schedule={
                    "every": {
                        "units": "hours",
                        "value": 1
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
        return self._call(
            method="POST",
            template="/services/{version}/tasks/{task}",
            path_params={
                "task": task,
            },
            data=data,
            body_params={
                "maxHistory": max_history,
                "command": command,
                "enabled": enabled,
                "schedule": schedule,
                "status": status,
                "timeout": timeout,
                "critical": critical,
                "restart": restart,
                "description": description,
            },
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def update_task(
        self,
        task,
        max_history=None,
        command=None,
        enabled=None,
        schedule=None,
        status=None,
        timeout=None,
        critical=None,
        restart=None,
        description=None,
        data=None,
        raw_response=False
    ):
        """
        Administrative Server/Tasks
        PATCH /services/{version}/tasks/{task}
        Update an existing administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            max_history (int): Number of task executions to maintain history for. Example:
                maxHistory_example
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
                max_history=None,
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
        return self._call(
            method="PATCH",
            template="/services/{version}/tasks/{task}",
            path_params={
                "task": task,
            },
            data=data,
            body_params={
                "maxHistory": max_history,
                "command": command,
                "enabled": enabled,
                "schedule": schedule,
                "status": status,
                "timeout": timeout,
                "critical": critical,
                "restart": restart,
                "description": description,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def delete_task(
        self,
        task,
        raw_response=False
    ):
        """
        Administrative Server/Tasks
        DELETE /services/{version}/tasks/{task}
        Delete an administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_task(
                task='task_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/tasks/{task}",
            path_params={
                "task": task,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}/info
    def list_task_info_types(
        self,
        task,
        raw_response=False
    ):
        """
        Administrative Server/Tasks
        GET /services/{version}/tasks/{task}/info
        Retrieve the collection of information types available for a task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_task_info_types(
                task='task_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/tasks/{task}/info",
            path_params={
                "task": task,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}/info/history
    def get_task_history(
        self,
        task,
        raw_response=False
    ):
        """
        Administrative Server/Tasks
        GET /services/{version}/tasks/{task}/info/history
        Retrieve the execution history of an administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_task_history(
                task='task_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/tasks/{task}/info/history",
            path_params={
                "task": task,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}/info/status
    def get_task_status(
        self,
        task,
        raw_response=False
    ):
        """
        Administrative Server/Tasks
        GET /services/{version}/tasks/{task}/info/status
        Retrieve the current status of an administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Required. Example: task_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_task_status(
                task='task_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/tasks/{task}/info/status",
            path_params={
                "task": task,
            },
            raw_response=raw_response
        )

    """
    Custom API methods appended to the OGGRestAPI client.
    These methods are not endpoints of the original swagger.json but are
    commonly used operations that combine one or more API calls for convenience.
    """

    def start_deployment(
        self,
        deployment,
        raw_response=False,
    ):
        """Start a deployment by updating its status to running.

        Args:
            deployment (str): Name of the deployment to start.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_deployment API call.
        """
        return self.update_deployment(
            deployment=deployment,
            data={'status': 'running'},
            raw_response=raw_response
        )

    def stop_deployment(
        self,
        deployment,
        raw_response=False,
    ):
        """Stop a deployment by updating its status to stopped.

        Args:
            deployment (str): Name of the deployment to stop.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_deployment API call.
        """
        return self.update_deployment(
            deployment=deployment,
            data={'status': 'stopped'},
            raw_response=raw_response
        )

    def start_extract(
        self,
        extract,
        raw_response=False,
    ):
        """Start an extract by updating its status to running.

        Args:
            extract (str): Name of the extract to start.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_extract API call.
        """
        return self.update_extract(
            extract=extract,
            data={'status': 'running'},
            raw_response=raw_response
        )

    def stop_extract(
        self,
        extract,
        raw_response=False,
    ):
        """Stop an extract by updating its status to stopped.

        Args:
            extract (str): Name of the extract to stop.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_extract API call.
        """
        return self.update_extract(
            extract=extract,
            data={'status': 'stopped'},
            raw_response=raw_response
        )

    def restart_extract(
        self,
        extract,
        only_if_running=True
    ):
        """Restart an extract by updating its status to restart

        Args:
            extract (str): Name of the extract to restart.
            only_if_running (bool, optional): If True, only restart the extract if it is currently running.
                Defaults to True.
        """
        if only_if_running:
            extract_status = self.get_extract(extract).get("status")
            if extract_status != "running":
                print(
                    f"Skipping restart of extract {extract} because it is not running (status={extract_status})."
                )
                return

        print(f"Restarting extract '{extract}'...")
        self.stop_extract(extract)
        self.start_extract(extract)

    def restart_all_extracts(
        self,
        only_if_running=True
    ):
        """Restart all extracts by updating their status to restart

        Args:
            only_if_running (bool, optional): If True, only restart extracts that are currently running.
                Defaults to True.
        """
        try:
            extracts = self.list_extracts()
            if len(extracts) == 0:
                print("No extracts found.")
                return []
        except Exception as e:
            print(f"Error fetching extracts: {e}")
            raise RuntimeError("Failed to fetch extracts, cannot restart extracts.") from e

        for extract in extracts:
            extract_name = extract.get("name")
            self.restart_extract(
                extract_name,
                only_if_running=only_if_running
            )

    def start_replicat(
        self,
        replicat,
        raw_response=False,
    ):
        """Start a replicat by updating its status to running.

        Args:
            replicat (str): Name of the replicat to start.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_replicat API call.
        """
        return self.update_replicat(
            replicat=replicat,
            data={'status': 'running'},
            raw_response=raw_response
        )

    def stop_replicat(
        self,
        replicat,
        raw_response=False,
    ):
        """Stop a replicat by updating its status to stopped.

        Args:
            replicat (str): Name of the replicat to stop.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_replicat API call.
        """
        return self.update_replicat(
            replicat=replicat,
            data={'status': 'stopped'},
            raw_response=raw_response
        )

    def restart_replicat(
        self,
        replicat,
        only_if_running=True
    ):
        """Restart a replicat by updating its status to restart

        Args:
            replicat (str): Name of the replicat to restart.
            only_if_running (bool, optional): If True, only restart the replicat if it is currently running.
                Defaults to True.
        """
        if only_if_running:
            replicat_status = self.get_replicat(replicat).get("status")
            if replicat_status != "running":
                print(
                    f"Skipping restart of replicat {replicat} because it is not running (status={replicat_status})."
                )
                return

        print(f"Restarting replicat '{replicat}'...")
        self.stop_replicat(replicat)
        self.start_replicat(replicat)

    def restart_all_replicats(
        self,
        only_if_running=True
    ):
        """Restart all replicats by updating their status to restart

        Args:
            only_if_running (bool, optional): If True, only restart replicats that are currently running.
                Defaults to True.
        """
        try:
            replicats = self.list_replicats()
            if len(replicats) == 0:
                print("No replicats found.")
                return []
        except Exception as e:
            print(f"Error fetching replicats: {e}")
            raise RuntimeError("Failed to fetch replicats, cannot restart replicats.") from e

        for replicat in replicats:
            replicat_name = replicat.get("name")
            self.restart_replicat(
                replicat_name,
                only_if_running=only_if_running
            )

    def start_distribution_path(
        self,
        distpath,
        raw_response=False,
    ):
        """Start a distribution path by updating its status to running.

        Args:
            distpath (str): Name of the distribution path to start.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_distribution_path API call.
        """
        return self.update_distribution_path(
            distpath=distpath,
            data={'status': 'running'},
            raw_response=raw_response
        )

    def stop_distribution_path(
        self,
        distpath,
        raw_response=False,
    ):
        """Stop a distribution path by updating its status to stopped.

        Args:
            distpath (str): Name of the distribution path to stop.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_distribution_path API call.
        """
        return self.update_distribution_path(
            distpath=distpath,
            data={'status': 'stopped'},
            raw_response=raw_response
        )

    def start_service(
        self,
        deployment,
        service,
        raw_response=False,
    ):
        """Start a service by updating its status to running.

        Args:
            deployment (str): Name of the deployment owning the service.
            service (str): Name of the service to start.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_service API call.
        """
        return self.update_service(
            deployment=deployment,
            service=service,
            data={'status': 'running'},
            raw_response=raw_response
        )

    def stop_service(
        self,
        deployment,
        service,
        raw_response=False,
    ):
        """Stop a service by updating its status to stopped.

        Args:
            deployment (str): Name of the deployment owning the service.
            service (str): Name of the service to stop.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_service API call.
        """
        return self.update_service(
            deployment=deployment,
            service=service,
            data={'status': 'stopped'},
            raw_response=raw_response
        )

    def restart_service(
        self,
        deployment,
        service,
        only_if_running=True,
        raw_response=False
    ):
        """Restart a service by updating its status to restart

        Args:
            deployment (str): Name of the deployment owning the service.
            service (str): Name of the service to restart.
            only_if_running (bool, optional): If True, only restart the service if it is currently running.
                Defaults to True.
            raw_response (bool, optional): If True, return the raw API response.
                Defaults to False.

        Returns:
            The result of the update_service API call, or None if the service was not restarted because
            it was not running and only_if_running is True.
        """
        if only_if_running:
            service_status = self.get_service(deployment, service).get("status")
            if service_status != "running":
                print(
                    f"Skipping restart of service '{service}' in deployment '{deployment}' because it is not running (status={service_status})."
                )
                return

        return self.update_service(
            deployment=deployment,
            service=service,
            data={'status': 'restart'},
            raw_response=raw_response
        )

    def patch_deployment(
        self,
        deployment,
        new_home,
        restart_deployment=True,
        restart_processes=True,
        ask_credentials=False,
    ):
        """Patch GoldenGate deployment with new home and optionally restart services and processes.
        For ServiceManager deployment, only patch the home and restart services, but do not restart extracts/replicats.

        Args:
            deployment (str): Name of the deployment to patch.
            new_home (str): Path to the new GoldenGate home.
            restart_deployment (bool, optional): Whether to restart the deployment. Defaults to True.
            restart_processes (bool, optional): Whether to restart extracts and replicats. Defaults to True.
            ask_credentials (bool, optional): Whether to ask for credentials for the deployment. Defaults to False.
        """
        print(f"Fetching deployment '{deployment}'...")
        deployment_info = self.get_deployment(deployment)
        current_home = deployment_info.get("oggHome")

        print(f"Updating home from '{current_home}' to '{new_home}' for deployment '{deployment}'...")
        self.update_deployment(
            deployment,
            data={
                'oggHome': new_home,
            }
        )
        print(f"Successfully updated home for deployment '{deployment}'.")

        if restart_deployment:
            print(f"Restarting deployment '{deployment}' to apply new home...")
            self.update_deployment(
                deployment=deployment,
                data={'status': 'restart'}
            )

            while True:
                print(f"Checking if deployment '{deployment}' is running before continuing...")
                try:
                    deployment_status = self.get_deployment(deployment).get("status")
                    if deployment_status == "running":
                        print(f"Deployment '{deployment}' is running. Continuing...")
                        break
                    else:
                        print(
                            f"Waiting for deployment '{deployment}' to be running before continuing..."
                            f" Current status: {deployment_status}"
                        )
                except Exception as e:
                    print(f"Error fetching deployment status: {e}. Retrying...")
                time.sleep(5)

            # For the Service Manager deployment, we restart all services except the Service Manager service itself.
            # The reason is that the services like the AIService do not pick up the new home automatically.
            if deployment == "ServiceManager":
                services = self.list_services("ServiceManager")
                for service in services:
                    service_name = service.get("name")
                    if service_name == "ServiceManager":
                        continue

                    service_status = service.get("status")
                    if service_status != "running":
                        print(
                            f"Skipping restart of service '{service_name}' in deployment 'ServiceManager'"
                            f" because it is not running (status={service_status})."
                        )
                        continue
                    else:
                        print(f"Restarting service '{service_name}' in deployment 'ServiceManager'...")
                        self.restart_service("ServiceManager", service_name)

        else:
            print(
                f"Skipping deployment restart for deployment '{deployment}' because restart_deployment=False. "
                "You should restart the deployment manually to use the new home.")

        if deployment != "ServiceManager":
            if restart_processes:
                if ask_credentials:
                    deployment_username = input(f"Enter username for deployment '{deployment}': ")
                    deployment_password = getpass.getpass(prompt=f"Enter password for deployment '{deployment}': ")
                    old_auth = self.auth
                    self.auth = (deployment_username, deployment_password)

                old_deployment = self.deployment
                self.deployment = deployment  # Set deployment for reverse proxy auth

                try:
                    extracts = self.list_extracts()
                    if len(extracts) == 0:
                        print(f"No extracts found for deployment '{deployment}'.")
                    else:
                        print(f"Restarting extracts for deployment '{deployment}'...")
                except Exception as e:
                    print(f"Error fetching extracts: {e}")
                    extracts = []
                    print(f"Skipping extract restarts for deployment '{deployment}' due to error fetching extracts. "
                          "You should restart the extracts manually to use the new home.")
                for extract in extracts:
                    print(f"Restarting extract '{extract.get('name')}' for deployment '{deployment}'...")
                    self.restart_extract(extract.get("name"), only_if_running=True)

                try:
                    replicats = self.list_replicats()
                    if len(replicats) == 0:
                        print(f"No replicats found for deployment '{deployment}'.")
                    else:
                        print(f"Restarting replicats for deployment '{deployment}'...")
                except Exception as e:
                    print(f"Error fetching replicats: {e}")
                    replicats = []
                    print(f"Skipping replicat restarts for deployment '{deployment}' due to error fetching replicats. "
                          "You should restart the replicats manually to use the new home.")
                for replicat in replicats:
                    print(f"Restarting replicat '{replicat.get('name')}' for deployment '{deployment}'...")
                    self.restart_replicat(replicat.get("name"), only_if_running=True)

                if ask_credentials:
                    # Restore original auth and deployment after patching each deployment
                    # to avoid issues with next API calls.
                    self.auth = old_auth
                    self.deployment = old_deployment

            else:
                print(
                    f"Skipping process restarts for deployment '{deployment}' because restart_processes=False. "
                    "You should restart the processes manually to use the new home.")

        print(f"Finished patching deployment '{deployment}'.")

    def patch_deployments(
        self,
        new_home,
        restart_deployment=True,
        restart_processes=True,
        ask_credentials=None,
    ):
        """Patch GoldenGate deployments

        Args:
            new_home (str): Path to the new GoldenGate home.
            restart_deployment (bool, optional): Whether to restart the deployment. Defaults to True.
            restart_processes (bool, optional): Whether to restart extracts and replicats. Defaults to True.
            ask_credentials (bool, optional): Whether to ask for credentials for each deployment. Defaults to None.
        """
        print("Listing deployments...")
        deployments = self.list_deployments()

        if restart_processes:
            if not self.reverse_proxy and restart_processes:
                raise ValueError(
                    "Cannot restart extracts and replicats when reverse_proxy is False because the API client "
                    "is not aware of the port information for all deployments. Please set restart_processes=False,"
                    " try again and restart the extracts and replicats manually with another client connection to "
                    "the deployments after patching the homes."
                )

            if len(deployments) > 2:
                if ask_credentials is None:
                    raise ValueError(
                        "More than two deployments detected and restart_processes is True."
                        " It is not possible to restart extracts and replicats without knowing "
                        "credentials for all the deployments. Either set ask_credentials=True "
                        "to be prompted for credentials for each deployment, or ask_credentials=False"
                        " to use the same credentials for all deployments. If you are not using a reverse "
                        "proxy setup, you cannot restart extracts and replicats automatically with this "
                        "method, and will need to open connections to each deployment separately to restart "
                        "the processes after patching the homes."
                    )
                else:
                    print(
                        "More than two deployments detected and restart_processes is True. "
                        f"ask_credentials is set to {ask_credentials}. "
                        "Proceeding with patching deployments and restarting processes using "
                        "individual credentials for each deployment."
                        if ask_credentials else
                        "the same credentials for all deployments."
                    )

        # Always patch ServiceManager first.
        self.patch_deployment(
            deployment="ServiceManager",
            new_home=new_home,
            restart_deployment=restart_deployment,
            restart_processes=False
        )

        for deployment in deployments:
            deployment_name = deployment.get("name")

            if deployment_name == "ServiceManager":
                continue

            print("\n\n")
            self.patch_deployment(
                deployment=deployment_name,
                new_home=new_home,
                restart_deployment=restart_deployment,
                restart_processes=restart_processes,
                ask_credentials=ask_credentials
            )
