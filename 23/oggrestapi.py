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
        self.base_url = url
        self.username = username
        self.swagger_version = '2025.01.20'
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
                self.retrieve_api_versions()
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
        self._check_response(response, url)
        result = self._parse(response)
        return result if raw_response else self._extract_main(result)

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

    def _call(self, method, template, *, ogg_service=None, path_params=None, params=None, data=None, raw_response=False):
        if self.reverse_proxy and ogg_service == '' and self.deployment:
            # This is a common endpoint and a deployment is specified. Choosing adminsrvr service by default.
            ogg_service = "adminsrvr"
        path = self._build_path(template, ogg_service=ogg_service, path_params=path_params)
        result = self._request(method, path, params=params, data=data, raw_response=True)
        return result if raw_response else self._extract_main(result)

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
            if 'messages' in response.json():
                messages = response.json().get('messages', [])
                raise Exception(
                    ' ; '.join([f"{message['severity']} - {url}: {message['title']}" for message in messages])
                )
            else:
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
    def retrieve_api_versions(self, ogg_service='', raw_response=False):
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
            client.retrieve_api_versions(
                ogg_service='adminsrvr'
            )
        """
        return self._call("GET", "/services", raw_response=raw_response)

    # Endpoint: /services/{version}
    def describe_api_version(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/REST API Catalog
        GET /services/{version}
        Required Role: Any
        Use this endpoint to obtain details of a specific version of an Oracle GoldenGate Service REST API.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.describe_api_version(
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
    def receives_authorization_code_and_exchanges_it_for_access_and_id_token(self, version='v2', raw_response=False):
        """
        OAuth redirect URL
        GET /services/{version}/authorization
        Required Role: Any
        Receives the authorization code and exchanges it for an access and id token

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.receives_authorization_code_and_exchanges_it_for_access_and_id_token()

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
    def list_user_roles(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Management
        GET /services/{version}/authorizations
        Required Role: Security
        Get the collection of roles in this deployment.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_user_roles(
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
    def list_users(self, role, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Management
        GET /services/{version}/authorizations/{role}
        Required Role: Security
        Get the collection of Authorized Users associated with the Authorization Role.

        Parameters:
            role (str): Authorization Role Resource Name. Example: User
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def bulk_create_users_for_role(self, role, data=None, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Management
        POST /services/{version}/authorizations/{role}
        Required Role: Security
        Create multiple users associated with the given role.

        Parameters:
            role (str): Authorization Role Resource Name. Example: User
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.bulk_create_users_for_role(
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
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def retrieve_user(self, user, role, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Management
        GET /services/{version}/authorizations/{role}/{user}
        Required Role: User
        Get Authorization User Resource information.

        Parameters:
            user (str): User Resource Name. Example: user_example
            role (str): Authorization Role Resource Name. Example: User
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_user(
                user='user_example',
                role='User',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "user": user,
            "role": role,
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
    def create_user(self, user, role, data=None, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Management
        POST /services/{version}/authorizations/{role}/{user}
        Required Role: Security
        Create a new Authorization User Resource.

        Parameters:
            user (str): User Resource Name. Example: user_example
            role (str): Authorization Role Resource Name. Example: User
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_user(
                user='user_example',
                role='User',
                ogg_service='adminsrvr',
                data={
                    "credential": "password-A1",
                    "info": "Credential Information"
                }
            )
        """
        path_params = {
            "user": user,
            "role": role,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/authorizations/{role}/{user}",
            path_params=path_params,
            data=data,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def update_user(self, user, role, data=None, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Management
        PATCH /services/{version}/authorizations/{role}/{user}
        Required Role: User
        Update an existing Authorization User Resource.

        Parameters:
            user (str): User Resource Name. Example: user_example
            role (str): Authorization Role Resource Name. Example: User
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_user(
                user='user_example',
                role='User',
                ogg_service='adminsrvr',
                data={
                    "credential": "NewPassword-A1"
                }
            )
        """
        path_params = {
            "user": user,
            "role": role,
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
    def delete_user(self, user, role, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Management
        DELETE /services/{version}/authorizations/{role}/{user}
        Required Role: Security
        Delete an existing Authorization user role. To completely remove a user from the deployment, use a value
            of "all" for {role}.

        Parameters:
            user (str): User Resource Name. Example: user_example
            role (str): Authorization Role Resource Name. Example: User
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_user(
                user='user_example',
                role='User',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "user": user,
            "role": role,
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
    def retrieve_additional_user_information(self, user, role, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Management
        GET /services/{version}/authorizations/{role}/{user}/info
        Required Role: Security
        Retrieve any additional information for the deployment user.

        Parameters:
            user (str): User Resource Name. Example: user_example
            role (str): Authorization Role Resource Name. Example: User
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_additional_user_information(
                user='user_example',
                role='User',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "user": user,
            "role": role,
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
    def retrieve_available_certificate_types(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Certificates
        GET /services/{version}/certificates
        Required Role: Administrator
        Retrieve the collection of certificate types.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_available_certificate_types(
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
    def retrieve_certificate_names(self, type, version='v2', ogg_service='', raw_response=False):
        """
        Common/Certificates
        GET /services/{version}/certificates/{type}
        Required Role: Administrator
        Retrieve the certificate type names.

        Parameters:
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_certificate_names(
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
    def retrieve_certificate(self, certificate, type, version='v2', ogg_service='', raw_response=False):
        """
        Common/Certificates
        GET /services/{version}/certificates/{type}/{certificate}
        Required Role: Administrator
        Retrieve the certificate information for the named certificate.

        Parameters:
            certificate (str): Certificate name. Example: certificate_example
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_certificate(
                certificate='certificate_example',
                type='type_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "certificate": certificate,
            "type": type,
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
    def retrieve_certificate_information(self, certificate, type, version='v2', raw_response=False):
        """
        Service Manager/Certificates
        GET /services/{version}/certificates/{type}/{certificate}/info
        Required Role: Administrator
        Retrieve the certificate information for the named certificate in the deployment.

        Parameters:
            certificate (str): Certificate name. Example: certificate_example
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_certificate_information(
                certificate='certificate_example',
                type='type_example'
            )
        """
        path_params = {
            "certificate": certificate,
            "type": type,
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
    def execute_command(self, data=None, version='v2', raw_response=False):
        """
        Administration Service/Commands
        POST /services/{version}/commands/execute
        Required Role: User
        Execute a command. Reporting commands are accessible for users with the 'User' role. Other commands
            require the 'Operator' role.

        Parameters:
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def list_configuration_files(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        GET /services/{version}/config/files
        Required Role: User
        Retrieve the collection of configuration files.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_configuration_file(self, file, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        GET /services/{version}/config/files/{file}
        Required Role: User
        Retrieve the contents of a configuration file.

        Parameters:
            file (str): The name of a configuration file. Example: file_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_configuration_file(
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
    def create_configuration_file(self, file, data=None, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        POST /services/{version}/config/files/{file}
        Required Role: Administrator
        Create a new configuration file.

        Parameters:
            file (str): The name of a configuration file. Example: file_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

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
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def replace_configuration_file(self, file, data=None, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        PUT /services/{version}/config/files/{file}
        Required Role: Administrator
        Modify an existing configuration file.

        Parameters:
            file (str): The name of a configuration file. Example: file_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.replace_configuration_file(
                file='file_example',
                ogg_service='adminsrvr',
                data={
                    "lines": [
                        "UseridAlias oggadmin",
                        "ReportCount Every 100000 Records"
                    ]
                }
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
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def delete_configuration_file(self, file, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        DELETE /services/{version}/config/files/{file}
        Required Role: Administrator
        Delete a configuration file.

        Parameters:
            file (str): The name of a configuration file. Example: file_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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

    # Endpoint: /services/{version}/config/health
    def service_health_details(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration
        GET /services/{version}/config/health
        Required Role: User
        Retrieve detailed information for the service health.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.service_health_details(
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
    def service_health_summary(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration
        GET /services/{version}/config/health/check
        Required Role: Any
        Retrieve summary information for the service health.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.service_health_summary(
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
    def service_configuration_summary(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration
        GET /services/{version}/config/summary
        Required Role: User
        Retrieve summary information for the service.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.service_configuration_summary(
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
    def list_configuration_data_types(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        GET /services/{version}/config/types
        Required Role: User
        Retrieve the collection of configuration variable data types.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_configuration_data_types(
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
    def retrieve_configuration_data_type(self, type, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        GET /services/{version}/config/types/{type}
        Required Role: User
        Retrieve a configuration data type.

        Parameters:
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_configuration_data_type(
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
    def create_configuration_data_type(self, type, data=None, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        POST /services/{version}/config/types/{type}
        Required Role: Administrator
        Create a new configuration data type.

        Parameters:
            type (str):  Example: type_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_configuration_data_type(
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
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def delete_configuration_data_type(self, type, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        DELETE /services/{version}/config/types/{type}
        Required Role: Administrator
        Delete a configuration data type.

        Parameters:
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_configuration_data_type(
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
    def list_configuration_values(self, type, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        GET /services/{version}/config/types/{type}/values
        Required Role: User
        Retrieve the collection of names of the configuration values for a data type.

        Parameters:
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_configuration_values(
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
    def retrieve_configuration_value(self, value, type, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        GET /services/{version}/config/types/{type}/values/{value}
        Required Role: User
        Retrieve a configuration value.

        Parameters:
            value (str): Value name, an alpha-numeric character followed by up to 95 alpha-numeric
                characters, '_', ':' or '-'. Example: value_example
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_configuration_value(
                value='value_example',
                type='type_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "value": value,
            "type": type,
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
    def create_configuration_value(self, value, type, data=None, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        POST /services/{version}/config/types/{type}/values/{value}
        Required Role: Administrator
        Create a new configuration value.

        Parameters:
            value (str): Value name, an alpha-numeric character followed by up to 95 alpha-numeric
                characters, '_', ':' or '-'. Example: value_example
            type (str):  Example: type_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_configuration_value(
                value='value_example',
                type='type_example',
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
            "value": value,
            "type": type,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params=path_params,
            data=data,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def replace_configuration_value(self, value, type, data=None, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        PUT /services/{version}/config/types/{type}/values/{value}
        Required Role: Administrator
        Replace an existing configuration value.

        Parameters:
            value (str): Value name, an alpha-numeric character followed by up to 95 alpha-numeric
                characters, '_', ':' or '-'. Example: value_example
            type (str):  Example: type_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.replace_configuration_value(
                value='value_example',
                type='type_example',
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
            "value": value,
            "type": type,
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

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def delete_configuration_value(self, value, type, version='v2', ogg_service='', raw_response=False):
        """
        Common/Configuration Settings
        DELETE /services/{version}/config/types/{type}/values/{value}
        Required Role: Administrator
        Delete a configuration value.

        Parameters:
            value (str): Value name, an alpha-numeric character followed by up to 95 alpha-numeric
                characters, '_', ':' or '-'. Example: value_example
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_configuration_value(
                value='value_example',
                type='type_example',
                ogg_service='adminsrvr'
            )
        """
        path_params = {
            "value": value,
            "type": type,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params=path_params,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections
    def list_connections(self, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections
        Required Role: User
        Retrieve the list of known database connections. For each item in the credential store, a database
            connection of the form 'domain.alias' is created.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def delete_connection(self, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        DELETE /services/{version}/connections/{connection}
        Required Role: Administrator
        Remove a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def replace_connection(self, connection, data=None, version='v2', raw_response=False):
        """
        Administration Service/Database
        PUT /services/{version}/connections/{connection}
        Required Role: Administrator
        Update a database connection. Connections created for aliases in the credential store cannot be updated.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.replace_connection(
                connection='MYCONN',
                data={
                    "credentials": {
                        "alias": "ggnorth"
                    }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def create_connection(self, connection, data=None, version='v2', raw_response=False):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}
        Required Role: Administrator
        Create a new database connection. Connections are automatically created for aliases in the credential
            store.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def retrieve_connection(self, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}
        Required Role: User
        Retrieve the database connection details.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_connection(
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

    # Endpoint: /services/{version}/connections/{connection}/activeTransactions
    def retrieve_active_transaction_details(self, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/activeTransactions
        Required Role: User
        Retrieve details of the active transactions for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_active_transaction_details(
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
    def retrieve_database_names(self, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases
        Required Role: User
        Retrieve names of databases.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_database_names(
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
    def retrieve_database_schemas(self, database, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases/{database}
        Required Role: User
        Retrieve names of schemas in the database.

        Parameters:
            database (str): Database name. Example: database_example
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_database_schemas(
                database='database_example',
                connection='MYCONN'
            )
        """
        path_params = {
            "database": database,
            "connection": connection,
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
    def retrieve_database_tables(self, schema, database, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases/{database}/{schema}
        Required Role: User
        Retrieve names of tables in the schema.

        Parameters:
            schema (str): Schema name in the database. Example: schema_example
            database (str): Database name. Example: database_example
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_database_tables(
                schema='schema_example',
                database='database_example',
                connection='MYCONN'
            )
        """
        path_params = {
            "schema": schema,
            "database": database,
            "connection": connection,
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
    def retrieve_database_table_details(self, table, schema, database, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}
        Required Role: User
        Retrieve details for a table in the schema.

        Parameters:
            table (str): Table name in the database. Example: table_example
            schema (str): Schema name in the database. Example: schema_example
            database (str): Database name. Example: database_example
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_database_table_details(
                table='table_example',
                schema='schema_example',
                database='database_example',
                connection='MYCONN'
            )
        """
        path_params = {
            "table": table,
            "schema": schema,
            "database": database,
            "connection": connection,
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
    def manage_instantiation_csn(self, table, schema, database, connection, data=None, version='v2', raw_response=False):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}/instantiationCsn
        Required Role: Administrator
        Manage the instantiation CSN for filtering.

        Parameters:
            table (str): Table name in the database. Example: table_example
            schema (str): Schema name in the database. Example: schema_example
            database (str): Database name. Example: database_example
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.manage_instantiation_csn(
                table='table_example',
                schema='schema_example',
                database='database_example',
                connection='MYCONN',
                data={
                    "command": "set",
                    "csn": "32036323",
                    "source": "DBNORTH_PDB1"
                }
            )
        """
        path_params = {
            "table": table,
            "schema": schema,
            "database": database,
            "connection": connection,
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
    def manage_checkpoint_tables(self, connection, data=None, version='v2', raw_response=False):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/tables/checkpoint
        Required Role: Administrator
        Manage Oracle GoldenGate Checkpoint table

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.manage_checkpoint_tables(
                connection='MYCONN',
                data={
                    "operation": "add",
                    "name": "ggadmin.ggs_checkpoint"
                }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def retrieve_heartbeat_table(self, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/tables/heartbeat
        Required Role: User
        Retrieve details of the heartbeat table for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_heartbeat_table(
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
    def create_heartbeat_table(self, connection, data=None, version='v2', raw_response=False):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/tables/heartbeat
        Required Role: Administrator
        Create the heartbeat table for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_heartbeat_table(
                connection='MYCONN',
                data={
                    "frequency": "30"
                }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def update_heartbeat_table(self, connection, data=None, version='v2', raw_response=False):
        """
        Administration Service/Database
        PATCH /services/{version}/connections/{connection}/tables/heartbeat
        Required Role: Administrator
        Modify the heartbeat table parameters for a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_heartbeat_table(
                connection='MYCONN',
                data={
                    "purgeFrequency": "7"
                }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def delete_heartbeat_table(self, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        DELETE /services/{version}/connections/{connection}/tables/heartbeat
        Required Role: Administrator
        Remove heartbeat resources from a database.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_process_heartbeat_records(self, process, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Required Role: User
        Retrieve heartbeat table entries for an extract or replicat group.

        Parameters:
            process (str): The name of the extract or replicat process. Example: process_example
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_process_heartbeat_records(
                process='process_example',
                connection='MYCONN'
            )
        """
        path_params = {
            "process": process,
            "connection": connection,
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
    def delete_process_heartbeat_records(self, process, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        DELETE /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Required Role: Administrator
        Delete heartbeat table entries for an extract or replicat group.

        Parameters:
            process (str): The name of the extract or replicat process. Example: process_example
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_process_heartbeat_records(
                process='process_example',
                connection='MYCONN'
            )
        """
        path_params = {
            "process": process,
            "connection": connection,
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
    def retrieve_heartbeat_table_entries(self, connection, version='v2', raw_response=False):
        """
        Administration Service/Database
        GET /services/{version}/connections/{connection}/tables/heartbeatData
        Required Role: User
        Retrieve heartbeat/lag entries from a database connection.

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_heartbeat_table_entries(
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
    def manage_procedural_supplemental_logging(self, connection, data=None, version='v2', raw_response=False):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/trandata/procedure
        Required Role: Administrator
        Manage Supplemental Logging for Database Procedures

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.manage_procedural_supplemental_logging(
                connection='MYCONN',
                data={
                    "operation": "info"
                }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/schema
    def manage_schema_supplemental_logging(self, connection, data=None, version='v2', raw_response=False):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/trandata/schema
        Required Role: Administrator
        Manage Supplemental Logging for Database Schemas

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.manage_schema_supplemental_logging(
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
    def manage_table_supplemental_logging(self, connection, data=None, version='v2', raw_response=False):
        """
        Administration Service/Database
        POST /services/{version}/connections/{connection}/trandata/table
        Required Role: Administrator
        Manage Supplemental Logging for Database Tables

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Example: MYCONN
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.manage_table_supplemental_logging(
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
    def static_files(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Content Requests
        GET /services/{version}/content
        Required Role: Any
        Top level file list.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.static_files(
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
    def list_domains(self, version='v2', raw_response=False):
        """
        Administration Service/Credentials
        GET /services/{version}/credentials
        Required Role: User
        Retrieve the list of domains in the credential store.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def list_domain_aliases(self, domain, version='v2', raw_response=False):
        """
        Administration Service/Credentials
        GET /services/{version}/credentials/{domain}
        Required Role: User
        Retrieve the list of aliases for a domain in the credential store.

        Parameters:
            domain (str): Credential store domain name. Example: OracleGoldenGate
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_domain_aliases(
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
    def retrieve_alias(self, alias, domain, version='v2', raw_response=False):
        """
        Administration Service/Credentials
        GET /services/{version}/credentials/{domain}/{alias}
        Required Role: User
        Retrieve the available information for an alias in a credential store domain. The password for an alias
            will not be returned.

        Parameters:
            alias (str): Credential store alias. Example: ggnorth
            domain (str): Credential store domain name. Example: OracleGoldenGate
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_alias(
                alias='ggnorth',
                domain='OracleGoldenGate'
            )
        """
        path_params = {
            "alias": alias,
            "domain": domain,
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
    def create_alias(self, alias, domain, data=None, version='v2', raw_response=False):
        """
        Administration Service/Credentials
        POST /services/{version}/credentials/{domain}/{alias}
        Required Role: Administrator
        Create a new alias in the credential store.

        Parameters:
            alias (str): Credential store alias. Example: ggnorth
            domain (str): Credential store domain name. Example: OracleGoldenGate
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_alias(
                alias='ggnorth',
                domain='OracleGoldenGate',
                data={
                    "userid": "c##ggadmin@//server1.dc1.north.example.com:1521/ORCLCDB",
                    "password": "password-DB_A1"
                }
            )
        """
        path_params = {
            "alias": alias,
            "domain": domain,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def replace_alias(self, alias, domain, data=None, version='v2', raw_response=False):
        """
        Administration Service/Credentials
        PUT /services/{version}/credentials/{domain}/{alias}
        Required Role: Administrator
        Update an alias in the credential store.

        Parameters:
            alias (str): Credential store alias. Example: ggnorth
            domain (str): Credential store domain name. Example: OracleGoldenGate
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.replace_alias(
                alias='ggnorth',
                domain='OracleGoldenGate',
                data={
                    "userid": "ggadmin@//server1.dc1.west.example.com:1521/dbwest_pdb1",
                    "password": "password-DB_A1"
                }
            )
        """
        path_params = {
            "alias": alias,
            "domain": domain,
            "version": version,
        }
        return self._call(
            "PUT",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def delete_alias(self, alias, domain, version='v2', raw_response=False):
        """
        Administration Service/Credentials
        DELETE /services/{version}/credentials/{domain}/{alias}
        Required Role: Administrator
        Delete an alias from the credential store.

        Parameters:
            alias (str): Credential store alias. Example: ggnorth
            domain (str): Credential store domain name. Example: OracleGoldenGate
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_alias(
                alias='ggnorth',
                domain='OracleGoldenGate'
            )
        """
        path_params = {
            "alias": alias,
            "domain": domain,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}/valid
    def validate(self, alias, domain, version='v2', raw_response=False):
        """
        Administration Service/Credentials
        GET /services/{version}/credentials/{domain}/{alias}/valid
        Required Role: User
        Check validity of credentials and return database credentials details.

        Parameters:
            alias (str): Credential store alias. Example: ggnorth
            domain (str): Credential store domain name. Example: OracleGoldenGate
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.validate(
                alias='ggnorth',
                domain='OracleGoldenGate'
            )
        """
        path_params = {
            "alias": alias,
            "domain": domain,
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
    def retrieve_information(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Information
        GET /services/{version}/currentuser
        Required Role: User
        Return the current user's identity information encoded in the request.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_information(
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
    def reset_information(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/User Information
        DELETE /services/{version}/currentuser
        Required Role: User
        Remove the current user's identity information encoded in the request.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.reset_information(
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
    def use_this_endpoint_to_reauthorize_current_user(self, version='v2', raw_response=False):
        """
        Reauthorize current user
        POST /services/{version}/currentuser/reauthorize
        Required Role: User
        Use this endpoint to reauthorize the current user

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.use_this_endpoint_to_reauthorize_current_user()

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
    def types(self, version='v2', raw_response=False):
        """
        Distribution Service/Data Target
        GET /services/{version}/dataTargetTypes
        Required Role: User
        Retrieve supported data target types from the Distribution Service

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.types()

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
    def json_schema(self, dataTargetType, version='v2', raw_response=False):
        """
        Distribution Service/Data Target
        GET /services/{version}/dataTargetTypes/{dataTargetType}
        Required Role: User
        Retrieve the json schema of a supported data target.

        Parameters:
            dataTargetType (str): The name of a supported data target. Example: dataTargetType_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.json_schema(
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
    def retrieve(self, version='v2', raw_response=False):
        """
        Performance Metrics Service/Datastore
        GET /services/{version}/datastore
        Required Role: User
        Retrieve the details of the datastore

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve()

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
    def update(self, data=None, version='v2', raw_response=False):
        """
        Performance Metrics Service/Datastore
        PATCH /services/{version}/datastore
        Required Role: Administrator
        Change the datastore configuration used by the Performance Metrics Service. Changes to the datastore
            configuration will cause the Performance Metrics Service to restart.

        Parameters:
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update(
                data={
                    "type": "LMDB",
                    "retentionDays": "30",
                    "collectorWorkerThreads": "5",
                    "collectorWorkerQueueLimit": "10000",
                    "monitorHeartBeatTimeout": "10",
                    "dataStoreMaxDBs": "5000"
                }
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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments
    def list_deployments(self, version='v2', raw_response=False):
        """
        Service Manager/Deployments
        GET /services/{version}/deployments
        Required Role: User
        Retrieve the collection of Oracle GoldenGate Deployments.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def remove_deployment(self, deployment, version='v2', raw_response=False):
        """
        Service Manager/Deployments
        DELETE /services/{version}/deployments/{deployment}
        Required Role: Administrator
        Delete a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.remove_deployment(
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

    # Endpoint: /services/{version}/deployments/{deployment}
    def create_deployment(self, deployment, data=None, version='v2', raw_response=False):
        """
        Service Manager/Deployments
        POST /services/{version}/deployments/{deployment}
        Required Role: Administrator
        Create a new Oracle GoldenGate deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

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
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def update_deployment(self, deployment, data=None, version='v2', raw_response=False):
        """
        Service Manager/Deployments
        PATCH /services/{version}/deployments/{deployment}
        Required Role: Administrator
        Update the properties of a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_deployment(
                deployment='deployment_example',
                data={
                    "enabled": True
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
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def retrieve_deployment(self, deployment, version='v2', raw_response=False):
        """
        Service Manager/Deployments
        GET /services/{version}/deployments/{deployment}
        Required Role: User
        Retrieve the details of a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_deployment(
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

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles
    def get_authorization_profiles(self, deployment, version='v2', raw_response=False):
        """
        Service Manager/Authorization Profiles
        GET /services/{version}/deployments/{deployment}/authorization/profiles
        Required Role: Security
        Retrieve the collection of Authorization profiles in a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_authorization_profiles(
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
    def get_authorization_profile(self, profile, deployment, version='v2', raw_response=False):
        """
        Service Manager/Authorization Profiles
        GET /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
        Required Role: Security
        Get the content of a specific Authorization profile in a given deployment

        Parameters:
            profile (str): Name of Authorization profile. Example: profile_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_authorization_profile(
                profile='profile_example',
                deployment='deployment_example'
            )
        """
        path_params = {
            "profile": profile,
            "deployment": deployment,
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
    def create_authorization_profile(self, profile, deployment, data=None, version='v2', raw_response=False):
        """
        Service Manager/Authorization Profiles
        POST /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
        Required Role: Security
        Create an Authorization profile in a given deployment

        Parameters:
            profile (str): Name of Authorization profile. Example: profile_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_authorization_profile(
                profile='profile_example',
                deployment='deployment_example',
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
            "profile": profile,
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}/authorization/profiles/{profile}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
    def patch_authorization_profile(self, profile, deployment, data=None, version='v2', raw_response=False):
        """
        Service Manager/Authorization Profiles
        PATCH /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
        Required Role: Security
        Patch the content of a given profile

        Parameters:
            profile (str): Name of Authorization profile. Example: profile_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.patch_authorization_profile(
                profile='profile_example',
                deployment='deployment_example',
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
            "profile": profile,
            "deployment": deployment,
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
    def delete_authorization_profile(self, profile, deployment, version='v2', raw_response=False):
        """
        Service Manager/Authorization Profiles
        DELETE /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
        Required Role: Security
        Delete an Authorization profile from a given deployment

        Parameters:
            profile (str): Name of Authorization profile. Example: profile_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_authorization_profile(
                profile='profile_example',
                deployment='deployment_example'
            )
        """
        path_params = {
            "profile": profile,
            "deployment": deployment,
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
    def test_authorization_profile(self, profile, deployment, version='v2', raw_response=False):
        """
        Service Manager/Authorization Profiles
        GET /services/{version}/deployments/{deployment}/authorization/profiles/{profile}/valid
        Required Role: Security
        Test the connection to the Authorization Tenant

        Parameters:
            profile (str): Name of Authorization profile. Example: profile_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.test_authorization_profile(
                profile='profile_example',
                deployment='deployment_example'
            )
        """
        path_params = {
            "profile": profile,
            "deployment": deployment,
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
    def retrieve_available_certificate_types_deployment(self, deployment, version='v2', raw_response=False):
        """
        Service Manager/Certificates
        GET /services/{version}/deployments/{deployment}/certificates
        Required Role: Administrator
        Retrieve the collection of certificate types.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_available_certificate_types_deployment(
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
    def retrieve_certificate_types(self, deployment, type, version='v2', raw_response=False):
        """
        Service Manager/Certificates
        GET /services/{version}/deployments/{deployment}/certificates/{type}
        Required Role: Administrator
        Retrieve the certificate type names.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_certificate_types(
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
    def retrieve_certificate_deployment(self, certificate, deployment, type, version='v2', raw_response=False):
        """
        Service Manager/Certificates
        GET /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
        Required Role: Administrator
        Retrieve the certificate PEM data for the named certificate in the deployment.

        Parameters:
            certificate (str): Deployment certificate name. Example: certificate_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_certificate_deployment(
                certificate='certificate_example',
                deployment='deployment_example',
                type='type_example'
            )
        """
        path_params = {
            "certificate": certificate,
            "deployment": deployment,
            "type": type,
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
    def add_named_certificate(self, certificate, deployment, type, data=None, version='v2', raw_response=False):
        """
        Service Manager/Certificates
        POST /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
        Required Role: Security
        Add a named certificate to a deployment. The certificate name must be unique and not exist in the
            deployment.

        Parameters:
            certificate (str): Deployment certificate name. Example: certificate_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            type (str):  Example: type_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.add_named_certificate(
                certificate='certificate_example',
                deployment='deployment_example',
                type='type_example',
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
            "certificate": certificate,
            "deployment": deployment,
            "type": type,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
    def replace_named_certificate(self, certificate, deployment, type, data=None, version='v2', raw_response=False):
        """
        Service Manager/Certificates
        PUT /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
        Required Role: Security
        Replace a named certificate in a deployment. The certificate name must exist in the deployment.

        Parameters:
            certificate (str): Deployment certificate name. Example: certificate_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            type (str):  Example: type_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.replace_named_certificate(
                certificate='certificate_example',
                deployment='deployment_example',
                type='type_example',
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
            "certificate": certificate,
            "deployment": deployment,
            "type": type,
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

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
    def delete_named_certificate(self, certificate, deployment, type, version='v2', raw_response=False):
        """
        Service Manager/Certificates
        DELETE /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
        Required Role: Security
        Delete a named certificate from a deployment. The certificate name must exist in the deployment.

        Parameters:
            certificate (str): Deployment certificate name. Example: certificate_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_named_certificate(
                certificate='certificate_example',
                deployment='deployment_example',
                type='type_example'
            )
        """
        path_params = {
            "certificate": certificate,
            "deployment": deployment,
            "type": type,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}/info
    def retrieve_certificate_information_deployment(self, certificate, deployment, type, version='v2', raw_response=False):
        """
        Service Manager/Certificates
        GET /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}/info
        Required Role: Administrator
        Retrieve the certificate information for the named certificate in the deployment.

        Parameters:
            certificate (str): Deployment certificate name. Example: certificate_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            type (str):  Example: type_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_certificate_information_deployment(
                certificate='certificate_example',
                deployment='deployment_example',
                type='type_example'
            )
        """
        path_params = {
            "certificate": certificate,
            "deployment": deployment,
            "type": type,
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
    def get_plugin_templates(self, deployment, version='v2', raw_response=False):
        """
        Service Manager/Plugin Templates
        GET /services/{version}/deployments/{deployment}/plugin/templates
        Required Role: Security
        Retrieve the collection of plugin templates in a given deployment

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_plugin_templates(
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
    def get_plugin_template(self, plugin, deployment, version='v2', raw_response=False):
        """
        Service Manager/Plugin Templates
        GET /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
        Required Role: Security
        Get the content of a specific plugin template in a given deployment

        Parameters:
            plugin (str): Name of plugin for the template. Example: plugin_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_plugin_template(
                plugin='plugin_example',
                deployment='deployment_example'
            )
        """
        path_params = {
            "plugin": plugin,
            "deployment": deployment,
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
    def create_plugin_template(self, plugin, deployment, data=None, version='v2', raw_response=False):
        """
        Service Manager/Plugin Templates
        POST /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
        Required Role: Security
        Create a plugin template in a given deployment

        Parameters:
            plugin (str): Name of plugin for the template. Example: plugin_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_plugin_template(
                plugin='plugin_example',
                deployment='deployment_example',
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
        """
        path_params = {
            "plugin": plugin,
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
    def update_plugin_template(self, plugin, deployment, data=None, version='v2', raw_response=False):
        """
        Service Manager/Plugin Templates
        PUT /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
        Required Role: Security
        Update the content of a given plugin template

        Parameters:
            plugin (str): Name of plugin for the template. Example: plugin_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_plugin_template(
                plugin='plugin_example',
                deployment='deployment_example',
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
        """
        path_params = {
            "plugin": plugin,
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "PUT",
            "/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
    def delete_plugin_template(self, plugin, deployment, version='v2', raw_response=False):
        """
        Service Manager/Plugin Templates
        DELETE /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
        Required Role: Security
        Delete a plugin template from a given deployment

        Parameters:
            plugin (str): Name of plugin for the template. Example: plugin_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_plugin_template(
                plugin='plugin_example',
                deployment='deployment_example'
            )
        """
        path_params = {
            "plugin": plugin,
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params=path_params,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services
    def list_services(self, deployment, version='v2', raw_response=False):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services
        Required Role: User
        Retrieve the collection of Oracle GoldenGate Services in a deployment.

        Parameters:
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_service(self, service, deployment, version='v2', raw_response=False):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services/{service}
        Required Role: User
        Retrieve the details of a service in an Oracle GoldenGate deployment.

        Parameters:
            service (str): Name of the service. Example: service_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_service(
                service='service_example',
                deployment='deployment_example'
            )
        """
        path_params = {
            "service": service,
            "deployment": deployment,
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
    def create_service(self, service, deployment, data=None, version='v2', raw_response=False):
        """
        Service Manager/Services
        POST /services/{version}/deployments/{deployment}/services/{service}
        Required Role: Administrator
        Add a new service to a deployment. An application with the service name must exist for this request to
            succeed.

        Parameters:
            service (str): Name of the service. Example: service_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_service(
                service='service_example',
                deployment='deployment_example',
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
        """
        path_params = {
            "service": service,
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def update_service_properties(self, service, deployment, data=None, version='v2', raw_response=False):
        """
        Service Manager/Services
        PATCH /services/{version}/deployments/{deployment}/services/{service}
        Required Role: Administrator
        Update the properties of a service.

        Parameters:
            service (str): Name of the service. Example: service_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_service_properties(
                service='service_example',
                deployment='deployment_example',
                data={
                    "enabled": True,
                    "status": "running"
                }
            )
        """
        path_params = {
            "service": service,
            "deployment": deployment,
            "version": version,
        }
        return self._call(
            "PATCH",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def remove_service(self, service, deployment, version='v2', raw_response=False):
        """
        Service Manager/Services
        DELETE /services/{version}/deployments/{deployment}/services/{service}
        Required Role: Administrator
        Remove a service from an Oracle GoldenGate deployment.

        Parameters:
            service (str): Name of the service. Example: service_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.remove_service(
                service='service_example',
                deployment='deployment_example'
            )
        """
        path_params = {
            "service": service,
            "deployment": deployment,
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
    def list_service_logs(self, service, deployment, version='v2', raw_response=False):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services/{service}/logs
        Required Role: User
        Retrieve the set of logs for the service

        Parameters:
            service (str): Name of the service. Example: service_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_service_logs(
                service='service_example',
                deployment='deployment_example'
            )
        """
        path_params = {
            "service": service,
            "deployment": deployment,
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
    def default_log(self, service, deployment, version='v2', raw_response=False):
        """
        Service Manager/Services
        GET /services/{version}/deployments/{deployment}/services/{service}/logs/default
        Required Role: Administrator
        Retrieve the service log

        Parameters:
            service (str): Name of the service. Example: service_example
            deployment (str): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.default_log(
                service='service_example',
                deployment='deployment_example'
            )
        """
        path_params = {
            "service": service,
            "deployment": deployment,
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
    def list_encryption_keys(self, version='v2', raw_response=False):
        """
        Administration Service/Encryption Keys
        GET /services/{version}/enckeys
        Required Role: User
        Retrieve the names of all encryption keys

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_encryption_key(self, keyName, version='v2', raw_response=False):
        """
        Administration Service/Encryption Keys
        GET /services/{version}/enckeys/{keyName}
        Required Role: User
        Retrieve details for an Encryption Key.

        Parameters:
            keyName (str): The name of the Encryption Key. Example: keyName_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_encryption_key(
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
    def create_encryption_key(self, keyName, data=None, version='v2', raw_response=False):
        """
        Administration Service/Encryption Keys
        POST /services/{version}/enckeys/{keyName}
        Required Role: Administrator
        Create an Encryption Key.

        Parameters:
            keyName (str): The name of the Encryption Key. Example: keyName_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

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
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def delete_encryption_key(self, keyName, version='v2', raw_response=False):
        """
        Administration Service/Encryption Keys
        DELETE /services/{version}/enckeys/{keyName}
        Required Role: Administrator
        Delete an Encryption Key

        Parameters:
            keyName (str): The name of the Encryption Key. Example: keyName_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def encrypt_data(self, keyName, data=None, version='v2', raw_response=False):
        """
        Administration Service/Encryption Keys
        POST /services/{version}/enckeys/{keyName}/encrypt
        Required Role: User
        Encrypt data using the Encryption Key.

        Parameters:
            keyName (str): The name of the Encryption Key. Example: keyName_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.encrypt_data(
                keyName='keyName_example',
                data={
                    "data": "plaintext-password"
                }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles
    def list_encryption_profiles(self, version='v2', raw_response=False):
        """
        Administration Service/Encryption Profiles
        GET /services/{version}/encryption/profiles
        Required Role: Any
        Retrieve names of all existing Encryption Profiles.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_encryption_profile(self, profile, version='v2', raw_response=False):
        """
        Administration Service/Encryption Profiles
        GET /services/{version}/encryption/profiles/{profile}
        Required Role: Any
        Retrieve details for an Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Example: profile_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_encryption_profile(
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
    def create_encryption_profile(self, profile, data=None, version='v2', raw_response=False):
        """
        Administration Service/Encryption Profiles
        POST /services/{version}/encryption/profiles/{profile}
        Required Role: Administrator
        Create an Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Example: profile_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

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
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}
    def replace_encryption_profile(self, profile, data=None, version='v2', raw_response=False):
        """
        Administration Service/Encryption Profiles
        PATCH /services/{version}/encryption/profiles/{profile}
        Required Role: Administrator
        Modify an existing Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Example: profile_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.replace_encryption_profile(
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
    def delete_encryption_profile(self, profile, version='v2', raw_response=False):
        """
        Administration Service/Encryption Profiles
        DELETE /services/{version}/encryption/profiles/{profile}
        Required Role: Administrator
        Delete an Encryption Profile

        Parameters:
            profile (str): Name of the Encryption Profile. Example: profile_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def validate_encryption_profile(self, profile, version='v2', raw_response=False):
        """
        Administration Service/Encryption Profiles
        GET /services/{version}/encryption/profiles/{profile}/valid
        Required Role: Administrator
        Validate an Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Example: profile_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.validate_encryption_profile(
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
    def list_extracts(self, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts
        Required Role: User
        Retrieve the collection of Extract processes

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_extract(self, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}
        Required Role: User
        Retrieve the details of an extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_extract(
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
    def create_extract(self, extract, data=None, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        POST /services/{version}/extracts/{extract}
        Required Role: Administrator
        Create a new extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def update_extract(self, extract, data=None, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        PATCH /services/{version}/extracts/{extract}
        Required Role: Operator
        Update an existing extract process. A user with the 'Operator' role may change the "status" property.
            Any other changes require the 'Administrator' role.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_extract(
                extract='extract_example',
                data={
                    "status": "running"
                }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def delete_extract(self, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        DELETE /services/{version}/extracts/{extract}
        Required Role: Administrator
        Delete an extract process. If the extract process is currently running, it is stopped first.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def issue_command_extract(self, extract, data=None, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        POST /services/{version}/extracts/{extract}/command
        Required Role: User
        Execute an Extract process command

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.issue_command_extract(
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
    def list_information_types_extract(self, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info
        Required Role: User
        Retrieve types of information available for an extract.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_information_types_extract(
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
    def retrieve_checkpoints_extract(self, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/checkpoints
        Required Role: User
        Retrieve the checkpoint information for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_checkpoints_extract(
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
    def list_diagnostics_extract(self, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/diagnostics
        Required Role: User
        Retrieve the list of diagnostic results available for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_diagnostics_extract(
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
    def retrieve_diagnostics_extract(self, diagnostic, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/diagnostics/{diagnostic}
        Required Role: User
        Retrieve a diagnostics result for the extract process.

        Parameters:
            diagnostic (str): The name of the diagnostic results, which is the extract name and
                '.diagnostics', followed by an optional revision number. Example: diagnostic_example
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_diagnostics_extract(
                diagnostic='diagnostic_example',
                extract='extract_example'
            )
        """
        path_params = {
            "diagnostic": diagnostic,
            "extract": extract,
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
    def retrieve_history_extract(self, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/history
        Required Role: User
        Retrieve the execution history of a managed extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_history_extract(
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
    def list_logs_extract(self, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/logs
        Required Role: User
        Retrieve the list of logs available for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_logs_extract(
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
    def retrieve_log_extract(self, log, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/logs/{log}
        Required Role: Administrator
        Retrieve a log from the extract process.

        Parameters:
            log (str): The name of the log, which is the extract name, followed by an optional revision
                number(as -number) and '.log'. Example: log_example
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_log_extract(
                log='log_example',
                extract='extract_example'
            )
        """
        path_params = {
            "log": log,
            "extract": extract,
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
    def list_reports_extract(self, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/reports
        Required Role: User
        Retrieve the list of reports available for the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_reports_extract(
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
    def retrieve_report_extract(self, report, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/reports/{report}
        Required Role: User
        Retrieve a report from the extract process.

        Parameters:
            report (str): The name of the report, which is the extract name, followed by an optional
                revision number and '.rpt'. Example: report_example
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_report_extract(
                report='report_example',
                extract='extract_example'
            )
        """
        path_params = {
            "report": report,
            "extract": extract,
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
    def retrieve_status_extract(self, extract, version='v2', raw_response=False):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts/{extract}/info/status
        Required Role: User
        Retrieve the current status of the extract process.

        Parameters:
            extract (str): The name of the extract. Extract names are upper case, begin with an alphabetic
                character followed by up to seven alpha-numeric characters. Example: extract_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_status_extract(
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
    def get_list_deployment_extracts_with_their_trail_files(self, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/exttrails
        Required Role: User
        Get a list of the deployment extracts with their trail files

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_list_deployment_extracts_with_their_trail_files()

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
    def get_cluster_details(self, version='v2', raw_response=False):
        """
        Service Manager/Cluster Management
        GET /services/{version}/installation/cluster
        Required Role: Administrator
        Retrieve the details for the installation's GoldenGate cluster.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_cluster_details()

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
    def add_installation_to_cluster(self, data=None, version='v2', raw_response=False):
        """
        Service Manager/Cluster Management
        POST /services/{version}/installation/cluster
        Required Role: Security
        Add the GoldenGate installation to an existing cluster or create a new cluster.

        Parameters:
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.add_installation_to_cluster(
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
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/installation/cluster",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster
    def remove_installation_from_cluster(self, version='v2', raw_response=False):
        """
        Service Manager/Cluster Management
        DELETE /services/{version}/installation/cluster
        Required Role: Security
        Remove the installation from the GoldenGate cluster.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.remove_installation_from_cluster()

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
    def add_remote_installation_to_cluster(self, data=None, version='v2', raw_response=False):
        """
        Service Manager/Cluster Management
        POST /services/{version}/installation/cluster/actions/memberAdd
        Required Role: Security
        Internal API for adding a remote GoldenGate installation to the cluster.

        Parameters:
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.add_remote_installation_to_cluster(
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
        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/installation/cluster/actions/memberAdd",
            path_params=path_params,
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster/role/{member}
    def retrieve_cluster_role(self, member, version='v2', raw_response=False):
        """
        Service Manager/Cluster Management
        GET /services/{version}/installation/cluster/role/{member}
        Required Role: Security
        Retrieve a member's role in the OGG cluster

        Parameters:
            member (str): Name of the OGG Cluster member. Example: member_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_cluster_role(
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
    def update_cluster_role(self, member, data=None, version='v2', raw_response=False):
        """
        Service Manager/Cluster Management
        PATCH /services/{version}/installation/cluster/role/{member}
        Required Role: Security
        Update a member's role in the OGG cluster

        Parameters:
            member (str): Name of the OGG Cluster member. Example: member_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_cluster_role(
                member='member_example',
                data={
                    "target": "backup"
                }
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
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster/role/{member}
    def delete_cluster_member(self, member, version='v2', raw_response=False):
        """
        Service Manager/Cluster Management
        DELETE /services/{version}/installation/cluster/role/{member}
        Required Role: Security
        Delete a member from the OGG Cluster

        Parameters:
            member (str): Name of the OGG Cluster member. Example: member_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def get_configuration(self, version='v2', raw_response=False):
        """
        Service Manager/Installation
        GET /services/{version}/installation/configuration
        Required Role: Administrator
        Retrieve the configuration details for the GoldenGate installation.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_configuration()

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
    def update_configuration(self, data=None, version='v2', raw_response=False):
        """
        Service Manager/Installation
        PATCH /services/{version}/installation/configuration
        Required Role: Security
        Update the configuration details for the GoldenGate installation.

        Parameters:
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_configuration(
                data={
                    "$schema": "ogg:installationConfiguration",
                    "installationId": "5b5bee89-6e93-4920-9ac7-0a5582623a2d",
                    "configurationServiceEnabled": True
                }
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
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends
    def get_backend_list(self, version='v2', raw_response=False):
        """
        Service Manager/Installation
        GET /services/{version}/installation/configuration/backends
        Required Role: Administrator
        Retrieve a list of Backends known to the Configuration Service.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_backend_list()

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
    def create_backend(self, data=None, version='v2', raw_response=False):
        """
        Service Manager/Installation
        POST /services/{version}/installation/configuration/backends
        Required Role: Security
        Create a new Configuration Service Backend.

        Parameters:
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_backend(
                data={
                    "$schema": "config:backend",
                    "id": "24d9565c-3f4d-49ea-9b1e-61df05c368c3",
                    "name": "Temporary",
                    "type": "Memory"
                }
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
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends/{backend}
    def get_backend(self, backend, version='v2', raw_response=False):
        """
        Service Manager/Installation
        GET /services/{version}/installation/configuration/backends/{backend}
        Required Role: Administrator
        Retrieve the details for the Backend identified by {backend}

        Parameters:
            backend (str): Identifier for a Configuration Service Backend. Example: backend_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_backend(
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
    def delete_backend(self, backend, version='v2', raw_response=False):
        """
        Service Manager/Installation
        DELETE /services/{version}/installation/configuration/backends/{backend}
        Required Role: Security
        The DELETE operation will remove the reference to the Backend identified by {backend}.

        Parameters:
            backend (str): Identifier for a Configuration Service Backend. Example: backend_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_backend(
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

    # Endpoint: /services/{version}/installation/configuration/backends/{backend}
    def update_backend(self, backend, data=None, version='v2', raw_response=False):
        """
        Service Manager/Installation
        PATCH /services/{version}/installation/configuration/backends/{backend}
        Required Role: Security
        Update the Configuration Service Backend with one or more JSON Patch operations.

        Parameters:
            backend (str): Identifier for a Configuration Service Backend. Example: backend_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_backend(
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
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends/{backend}/actions/replaces
    def replace_backend(self, backend, data=None, version='v2', raw_response=False):
        """
        Service Manager/Installation
        POST /services/{version}/installation/configuration/backends/{backend}/actions/replaces
        Required Role: Security
        Replace another backend with this backend.

        Parameters:
            backend (str): Identifier for a Configuration Service Backend. Example: backend_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.replace_backend(
                backend='backend_example',
                data={
                    "$schema": "config:backend",
                    "id": "47ce3867-b4d3-413b-aafa-42649872fe54"
                }
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
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/deployments
    def retrieve_deployment_list(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Installation
        GET /services/{version}/installation/deployments
        Required Role: Any
        Retrieve a list of all Oracle GoldenGate deployments for the installation.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_deployment_list(
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
    def list_plugins(self, version='v2', raw_response=False):
        """
        Service Manager/Plugin Management
        GET /services/{version}/installation/plugins
        Required Role: Administrator
        Retrieve the collection of plugins available to this installation.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_plugins()

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
    def get_plugin_details(self, plugin, version='v2', raw_response=False):
        """
        Service Manager/Plugin Management
        GET /services/{version}/installation/plugins/{plugin}
        Required Role: Administrator
        Retrieve the details for an installation plugin.

        Parameters:
            plugin (str): Name of the plugin. Example: plugin_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_plugin_details(
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
    def retrieve_service_list(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Installation
        GET /services/{version}/installation/services
        Required Role: User
        Retrieve a list of all Oracle GoldenGate services for the installation.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_service_list(
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
    def retrieve_available_logs(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Logs
        GET /services/{version}/logs
        Required Role: User
        Retrieve the collection of available logs.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_available_logs(
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
    def critical_events(self, version='v2', raw_response=False):
        """
        Administration Service/Logs
        GET /services/{version}/logs/events
        Required Role: Administrator
        This endpoint provides a log of all critical events that occur in replication processes.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.critical_events()

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
    def retrieve_log(self, log, version='v2', ogg_service='', raw_response=False):
        """
        Common/Logs
        GET /services/{version}/logs/{log}
        Required Role: Administrator
        Retrieve an application log

        Parameters:
            log (str): Name of the log. Example: log_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_log(
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
    def modify_log_properties(self, log, data=None, version='v2', ogg_service='', raw_response=False):
        """
        Common/Logs
        PATCH /services/{version}/logs/{log}
        Required Role: Administrator
        Update application log properties.
        Not all logs can be modified, and if a PATCH operation is issued for a read-only log a status code of
            400 Bad Request is returned.

        Parameters:
            log (str): Name of the log. Example: log_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.modify_log_properties(
                log='log_example',
                ogg_service='adminsrvr',
                data={
                    "enabled": True
                }
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
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/{log}
    def reset_log_data(self, log, version='v2', ogg_service='', raw_response=False):
        """
        Common/Logs
        DELETE /services/{version}/logs/{log}
        Required Role: Administrator
        Clear the contents of an application log.
        Not all logs can be modified, and if a DELETE operation is issued for a read-only log a status code of
            400 Bad Request is returned.

        Parameters:
            log (str): Name of the log. Example: log_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.reset_log_data(
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
    def list_versions(self, version='v2', raw_response=False):
        """
        Administration Service/Master Keys
        GET /services/{version}/masterkey
        Required Role: User
        Retrieve all versions of the Master Key

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_versions()

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
    def create_version(self, version='v2', raw_response=False):
        """
        Administration Service/Master Keys
        POST /services/{version}/masterkey
        Required Role: Administrator
        Create a new Master Key version

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_version()

        """
        path_params = {
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/masterkey",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def retrieve_version(self, keyVersion, version='v2', raw_response=False):
        """
        Administration Service/Master Keys
        GET /services/{version}/masterkey/{keyVersion}
        Required Role: User
        Retrieve a Master Key by version.

        Parameters:
            keyVersion (int): The Master Key version number, 1 to 32767. Example: 1
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_version(
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
    def update_version(self, keyVersion, data=None, version='v2', raw_response=False):
        """
        Administration Service/Master Keys
        PATCH /services/{version}/masterkey/{keyVersion}
        Required Role: Administrator
        Update a Master Key version

        Parameters:
            keyVersion (int): The Master Key version number, 1 to 32767. Example: 1
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_version(
                keyVersion=1,
                data={
                    "status": "unavailable"
                }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def delete_version(self, keyVersion, version='v2', raw_response=False):
        """
        Administration Service/Master Keys
        DELETE /services/{version}/masterkey/{keyVersion}
        Required Role: Administrator
        Delete a Master Key version

        Parameters:
            keyVersion (int): The Master Key version number, 1 to 32767. Example: 1
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_version(
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
    def retrieve_messages(self, version='v2', raw_response=False):
        """
        Administration Service/Messages
        GET /services/{version}/messages
        Required Role: User
        Retrieve messages from the Oracle GoldenGate deployment.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_messages()

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
    def retrieve_catalog(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/REST API Catalog
        GET /services/{version}/metadata-catalog
        Required Role: Any
        The REST API catalog contains information about resources provided by each Oracle GoldenGate Service.
            Use this endpoint to retrieve a collection of all items in the catalog.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_catalog(
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
    def describe_catalog_item(self, resource, version='v2', ogg_service='', raw_response=False):
        """
        Common/REST API Catalog
        GET /services/{version}/metadata-catalog/{resource}
        Required Role: Any
        Use this endpoint to describe a single item in the metadata catalog. A list of items in the metadata
            catalog is obtained using the Retrieve Catalog endpoint.

        Parameters:
            resource (str): Name of the item in the metadata catalog. Example: resource_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.describe_catalog_item(
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
    def retrieve_list_commands(self, version='v2', raw_response=False):
        """
        Performance Metrics Service/Commands
        GET /services/{version}/monitoring/commands
        Required Role: User
        Retrieve the list of commands

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_list_commands()

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
    def execute_command_monitoring(self, data=None, version='v2', raw_response=False):
        """
        Performance Metrics Service/Commands
        POST /services/{version}/monitoring/commands/execute
        Required Role: Operator
        Execute a command

        Parameters:
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.execute_command_monitoring(
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
    def retrieve_existing_last_message_id_number(self, version='v2', raw_response=False):
        """
        Performance Metrics Service/Last Message Number
        GET /services/{version}/monitoring/lastMessageId
        Required Role: User
        Retrieve an existing Last message id number

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_last_message_id_number()

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
    def retrieve_existing_last_status_change_id_number(self, version='v2', raw_response=False):
        """
        Performance Metrics Service/Last Status Change Id Number
        GET /services/{version}/monitoring/lastStatusChangeId
        Required Role: User
        Retrieve an existing Last status change id number

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_last_status_change_id_number()

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
    def retrieve_existing_process_messages(self, version='v2', raw_response=False):
        """
        Performance Metrics Service/Messages
        GET /services/{version}/monitoring/messages
        Required Role: User
        Retrieve an existing Process Messages

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_process_messages()

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
    def retrieve_existing_process_status_changes(self, version='v2', raw_response=False):
        """
        Performance Metrics Service/Status Changes
        GET /services/{version}/monitoring/statusChanges
        Required Role: User
        Retrieve an existing Process Status Changes

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_process_status_changes()

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
    def retrieve_existing_process_messages_item(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Messages
        GET /services/{version}/monitoring/{item}/messages
        Required Role: User
        Retrieve an existing Process Messages

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_process_messages_item(
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
    def retrieve_existing_process_status_changes_item(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Status Changes
        GET /services/{version}/monitoring/{item}/statusChanges
        Required Role: User
        Retrieve an existing Process Status Changes

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_process_status_changes_item(
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
    def retrieve_existing_process_information_processes(self, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/processes
        Required Role: User
        Retrieve an existing Process Information

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_process_information_processes()

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
    def retrieve_existing_integrated_replicat_batch_sql_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/batchSqlStatistics
        Required Role: User
        Retrieve an existing Integrated Replicat Batch SQL Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_integrated_replicat_batch_sql_statistics(
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
    def retrieve_existing_bounded_recovery_extant_object_ages_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brExtantObjectAges
        Required Role: User
        Retrieve an existing Bounded Recovery Extant Object Ages Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_bounded_recovery_extant_object_ages_statistics(
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
    def retrieve_existing_bounded_recovery_extant_object_sizes_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brExtantObjectSizes
        Required Role: User
        Retrieve an existing Bounded Recovery Extant Object Sizes Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_bounded_recovery_extant_object_sizes_statistics(
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
    def retrieve_existing_bounded_recovery_object_ages_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brObjectAges
        Required Role: User
        Retrieve an existing Bounded Recovery Object Ages Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_bounded_recovery_object_ages_statistics(
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
    def retrieve_existing_bounded_recovery_object_sizes_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brObjectSizes
        Required Role: User
        Retrieve an existing Bounded Recovery Object Sizes Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_bounded_recovery_object_sizes_statistics(
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
    def retrieve_existing_bounded_recovery_object_pool_information(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brPoolsInfo
        Required Role: User
        Retrieve an existing Bounded Recovery Object Pool Information

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_bounded_recovery_object_pool_information(
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
    def retrieve_existing_bounded_recovery_status(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brStatus
        Required Role: User
        Retrieve an existing Bounded Recovery Status

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_bounded_recovery_status(
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
    def retrieve_existing_cache_manager_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/cacheStatistics
        Required Role: User
        Retrieve an existing Cache Manager Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_cache_manager_statistics(
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
    def retrieve_existing_basic_configuration_information_for_extract_and_replicat(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/ER Metrics
        GET /services/{version}/mpoints/{item}/configurationEr
        Required Role: User
        Retrieve an existing Basic Configuration Information for Extract and Replicat

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_basic_configuration_information_for_extract_and_replicat(
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
    def retrieve_existing_basic_configuration_information_for_manager_and_services(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/ER Metrics
        GET /services/{version}/mpoints/{item}/configurationManager
        Required Role: User
        Retrieve an existing Basic Configuration Information for Manager and Services

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_basic_configuration_information_for_manager_and_services(
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
    def retrieve_existing_coordinated_replicat_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/coordinationReplicat
        Required Role: User
        Retrieve an existing Coordinated Replicat Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_coordinated_replicat_statistics(
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
    def retrieve_existing_in_flight_transaction_information(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/currentInflightTransactions
        Required Role: User
        Retrieve an existing In Flight Transaction Information

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_in_flight_transaction_information(
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
    def retrieve_existing_database_information(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/databaseInOut
        Required Role: User
        Retrieve an existing Database Information

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_database_information(
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
    def retrieve_existing_statistics_about_dependencies(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/dependencyStats
        Required Role: User
        Retrieve an existing Statistics about dependencies

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_statistics_about_dependencies(
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
    def retrieve_existing_distribution_service_chunk_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrChunkStats
        Required Role: User
        Retrieve an existing Distribution Service Chunk Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_distribution_service_chunk_statistics(
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
    def retrieve_existing_distribution_service_network_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrNetworkStats
        Required Role: User
        Retrieve an existing Distribution Service Network Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_distribution_service_network_statistics(
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
    def retrieve_existing_distribution_service_path_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrPathStats
        Required Role: User
        Retrieve an existing Distribution Service Path Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_distribution_service_path_statistics(
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
    def retrieve_existing_distribution_service_table_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrTableStats
        Required Role: User
        Retrieve an existing Distribution Service Table Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_distribution_service_table_statistics(
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
    def retrieve_existing_heartbeat_timings(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Heartbeat Metrics
        GET /services/{version}/mpoints/{item}/heartbeat
        Required Role: User
        Retrieve an existing Heartbeat timings

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_heartbeat_timings(
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
    def retrieve_existing_network_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/networkStatistics
        Required Role: User
        Retrieve an existing Network Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_network_statistics(
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
    def retrieve_existing_parallel_replicat_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/parallelReplicat
        Required Role: User
        Retrieve an existing Parallel Replicat Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_parallel_replicat_statistics(
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
    def retrieve_existing_performance_metrics_service_monitored_process_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrProcStats
        Required Role: User
        Retrieve an existing Performance Metrics Service Monitored Process Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_performance_metrics_service_monitored_process_statistics(
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
    def retrieve_existing_performance_metrics_service_collector_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrStats
        Required Role: User
        Retrieve an existing Performance Metrics Service Collector Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_performance_metrics_service_collector_statistics(
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
    def retrieve_existing_performance_metrics_service_worker_thread_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrWorkerStats
        Required Role: User
        Retrieve an existing Performance Metrics Service Worker Thread Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_performance_metrics_service_worker_thread_statistics(
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
    def retrieve_existing_checkpoint_position_information(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/ER Metrics
        GET /services/{version}/mpoints/{item}/positionEr
        Required Role: User
        Retrieve an existing Checkpoint Position Information

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_checkpoint_position_information(
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
    def retrieve_existing_process_information_item(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/process
        Required Role: User
        Retrieve an existing Process Information

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_process_information_item(
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
    def retrieve_existing_process_performance_resource_utilization_information(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/processPerformance
        Required Role: User
        Retrieve an existing Process Performance Resource Utilization Information

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_process_performance_resource_utilization_information(
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
    def retrieve_existing_queue_bucket_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/queueBucketStatistics
        Required Role: User
        Retrieve an existing Queue Bucket Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_queue_bucket_statistics(
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
    def retrieve_existing_queue_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/queueStatistics
        Required Role: User
        Retrieve an existing Queue Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_queue_statistics(
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
    def retrieve_existing_receiver_service_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/recvsrvrStats
        Required Role: User
        Retrieve an existing Receiver Service Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_receiver_service_statistics(
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
    def retrieve_existing_service_health(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/serviceHealth
        Required Role: User
        Retrieve an existing Service Health

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_service_health(
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
    def retrieve_existing_extract_database_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsExtract
        Required Role: User
        Retrieve an existing Extract Database Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_extract_database_statistics(
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
    def retrieve_existing_extract_database_statistics_by_procedure_feature(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsProcedureExtract
        Required Role: User
        Retrieve an existing Extract Database Statistics by Procedure Feature

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_extract_database_statistics_by_procedure_feature(
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
    def retrieve_existing_database_statistics_by_procedure_feature(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsProcedureReplicat
        Required Role: User
        Retrieve an existing Database Statistics by Procedure Feature

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_database_statistics_by_procedure_feature(
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
    def retrieve_existing_replicat_database_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsReplicat
        Required Role: User
        Retrieve an existing Replicat Database Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_replicat_database_statistics(
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
    def retrieve_existing_extract_database_statistics_by_table(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsTableExtract
        Required Role: User
        Retrieve an existing Extract Database Statistics by Table

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_extract_database_statistics_by_table(
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
    def retrieve_existing_replicat_database_statistics_by_table(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsTableReplicat
        Required Role: User
        Retrieve an existing Replicat Database Statistics by Table

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_replicat_database_statistics_by_table(
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
    def retrieve_existing_super_pool_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/superpoolStatistics
        Required Role: User
        Retrieve an existing Super Pool Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_super_pool_statistics(
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
    def retrieve_existing_process_thread_resource_utilization_information(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/threadPerformance
        Required Role: User
        Retrieve an existing Process Thread Resource Utilization Information

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_process_thread_resource_utilization_information(
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
    def retrieve_existing_input_trail_file_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/trailInput
        Required Role: User
        Retrieve an existing Input Trail File Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_input_trail_file_statistics(
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
    def retrieve_existing_output_trail_file_statistics(self, item, version='v2', raw_response=False):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/trailOutput
        Required Role: User
        Retrieve an existing Output Trail File Statistics

        Parameters:
            item (str):  Example: item_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_output_trail_file_statistics(
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
    def retrieve_list_message_codes(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Message Codes
        GET /services/{version}/oggerr
        Required Role: Any
        Retrieve all message codes from the Oracle GoldenGate deployment.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_list_message_codes(
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
    def retrieve_message_explanation(self, message, version='v2', ogg_service='', raw_response=False):
        """
        Common/Message Codes
        GET /services/{version}/oggerr/{message}
        Required Role: Any
        Retrieve a detailed explanation for an Oracle GoldenGate message.

        Parameters:
            message (str): The Oracle GoldenGate Message Code, OGG-99999. Example: message_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_message_explanation(
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
    def list_parameter_names(self, version='v2', raw_response=False):
        """
        Administration Service/Parameters
        GET /services/{version}/parameters
        Required Role: Any
        Retrieve names of all known OGG parameters.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_parameter_names()

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
    def retrieve_parameter_info(self, parameter, version='v2', raw_response=False):
        """
        Administration Service/Parameters
        GET /services/{version}/parameters/{parameter}
        Required Role: Any
        Retrieve details for a parameter.

        Parameters:
            parameter (str): Name of parameter for information request. Example: parameter_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_parameter_info(
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
    def list_replicats(self, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats
        Required Role: User
        Retrieve the collection of Replicat processes

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_replicat(self, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}
        Required Role: User
        Retrieve the details of an replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_replicat(
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
    def create_replicat(self, replicat, data=None, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        POST /services/{version}/replicats/{replicat}
        Required Role: Administrator
        Create a new replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def update_replicat(self, replicat, data=None, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        PATCH /services/{version}/replicats/{replicat}
        Required Role: Operator
        Update an existing replicat process. A user with the 'Operator' role may change the "status" property.
            Any other changes require the 'Administrator' role.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_replicat(
                replicat='replicat_example',
                data={
                    "status": "running"
                }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def delete_replicat(self, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        DELETE /services/{version}/replicats/{replicat}
        Required Role: Administrator
        Delete a replicat process. If the replicat process is currently running, it is stopped first.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def issue_command_replicat(self, replicat, data=None, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        POST /services/{version}/replicats/{replicat}/command
        Required Role: User
        Execute a Replicat process command

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.issue_command_replicat(
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
    def list_information_types_replicat(self, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info
        Required Role: User
        Retrieve types of information available for a replicat.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_information_types_replicat(
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
    def retrieve_checkpoints_replicat(self, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/checkpoints
        Required Role: User
        Retrieve the checkpoint information for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_checkpoints_replicat(
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
    def list_diagnostics_replicat(self, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/diagnostics
        Required Role: User
        Retrieve the list of diagnostic results available for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_diagnostics_replicat(
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
    def retrieve_diagnostics_replicat(self, diagnostic, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/diagnostics/{diagnostic}
        Required Role: User
        Retrieve a diagnostics result for the replicat process.

        Parameters:
            diagnostic (str): The name of the diagnostic results, which is the replicat name and
                '.diagnostics', followed by an optional revision number. Example: diagnostic_example
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_diagnostics_replicat(
                diagnostic='diagnostic_example',
                replicat='replicat_example'
            )
        """
        path_params = {
            "diagnostic": diagnostic,
            "replicat": replicat,
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
    def retrieve_history_replicat(self, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/history
        Required Role: User
        Retrieve the execution history of a managed replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_history_replicat(
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
    def list_logs_replicat(self, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/logs
        Required Role: User
        Retrieve the list of logs available for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_logs_replicat(
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
    def retrieve_log_replicat(self, log, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/logs/{log}
        Required Role: Administrator
        Retrieve a log from the replicat process.

        Parameters:
            log (str): The name of the log, which is the replicat name, followed by an optional revision
                number(as -number) and '.log'. Example: log_example
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_log_replicat(
                log='log_example',
                replicat='replicat_example'
            )
        """
        path_params = {
            "log": log,
            "replicat": replicat,
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
    def list_reports_replicat(self, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/reports
        Required Role: User
        Retrieve the list of reports available for the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_reports_replicat(
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
    def retrieve_report_replicat(self, report, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/reports/{report}
        Required Role: User
        Retrieve a report from the replicat process.

        Parameters:
            report (str): The name of the report, which is the replicat name, followed by an optional
                revision number and '.rpt'. Example: report_example
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_report_replicat(
                report='report_example',
                replicat='replicat_example'
            )
        """
        path_params = {
            "report": report,
            "replicat": replicat,
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
    def retrieve_status_replicat(self, replicat, version='v2', raw_response=False):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats/{replicat}/info/status
        Required Role: User
        Retrieve the current status of the replicat process.

        Parameters:
            replicat (str): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_status_replicat(
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
    def retrieve_background_requests(self, version='v2', ogg_service='', raw_response=False):
        """
        Common/Requests
        GET /services/{version}/requests
        Required Role: Administrator
        Retrieve the collection of background REST API requests.

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_background_requests(
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
    def retrieve_request_status(self, request, version='v2', ogg_service='', raw_response=False):
        """
        Common/Requests
        GET /services/{version}/requests/{request}
        Required Role: User
        Retrieve the background request status.

        Parameters:
            request (int): Identifier for background request. Example: 1
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_request_status(
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
    def retrieve_request_result(self, request, version='v2', ogg_service='', raw_response=False):
        """
        Common/Requests
        GET /services/{version}/requests/{request}/result
        Required Role: User
        Retrieve the background request result.

        Parameters:
            request (int): Identifier for background request. Example: 1
            version (str): Oracle GoldenGate Service API version. Example: v2
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_request_result(
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
    def get_list_distribution_paths_sources(self, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/sources
        Required Role: User
        Get a list of distribution paths

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_list_distribution_paths_sources()

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
    def delete_existing_oracle_goldengate_distribution_path(self, distpath, version='v2', raw_response=False):
        """
        Distribution Service
        DELETE /services/{version}/sources/{distpath}
        Required Role: Administrator
        Delete an existing Oracle GoldenGate Distribution Path

        Parameters:
            distpath (str):  Example: distpath_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_existing_oracle_goldengate_distribution_path(
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

    # Endpoint: /services/{version}/sources/{distpath}
    def create_new_oracle_goldengate_distribution_path(self, distpath, data=None, version='v2', raw_response=False):
        """
        Distribution Service
        POST /services/{version}/sources/{distpath}
        Required Role: Administrator
        Create a new Oracle GoldenGate Distribution Path

        Parameters:
            distpath (str):  Example: distpath_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_new_oracle_goldengate_distribution_path(
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
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def update_existing_distribution_path(self, distpath, data=None, version='v2', raw_response=False):
        """
        Distribution Service
        PATCH /services/{version}/sources/{distpath}
        Required Role: Operator
        Update an Existing Distribution PathTo update an existing distribution path a user needs the
            Administrator role. However, a user with the Operator role is allowed change the status property.

        Parameters:
            distpath (str):  Example: distpath_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_existing_distribution_path(
                distpath='distpath_example',
                data={
                    "$schema": "ogg:distPath",
                    "status": "stopped"
                }
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
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def retrieve_existing_oracle_goldengate_distribution_path(self, distpath, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path

        Parameters:
            distpath (str):  Example: distpath_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_distribution_path(
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

    # Endpoint: /services/{version}/sources/{distpath}/checkpoints
    def retrieve_existing_oracle_goldengate_distribution_path_checkpoints(self, distpath, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}/checkpoints
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path Checkpoints

        Parameters:
            distpath (str):  Example: distpath_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_distribution_path_checkpoints(
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
    def retrieve_existing_oracle_goldengate_distribution_path_information(self, distpath, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}/info
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path Information

        Parameters:
            distpath (str):  Example: distpath_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_distribution_path_information(
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
    def retrieve_existing_oracle_goldengate_distribution_path_statistics(self, distpath, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/sources/{distpath}/stats
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path Statistics

        Parameters:
            distpath (str):  Example: distpath_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_distribution_path_statistics(
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
    def get_list_data_stream_resources(self, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/stream
        Required Role: User
        Get a list of data stream resources

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_list_data_stream_resources()

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
    def delete_existing_oracle_goldengate_data_stream_configuration(self, streamName, version='v2', raw_response=False):
        """
        Distribution Service
        DELETE /services/{version}/stream/{streamName}
        Required Role: Administrator
        Delete an existing Oracle GoldenGate Data Stream configuration

        Parameters:
            streamName (str):  Example: streamName_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_existing_oracle_goldengate_data_stream_configuration(
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

    # Endpoint: /services/{version}/stream/{streamName}
    def create_new_oracle_goldengate_data_stream_configuration(self, streamName, data=None, version='v2', raw_response=False):
        """
        Distribution Service
        POST /services/{version}/stream/{streamName}
        Required Role: Administrator
        Create a new Oracle GoldenGate Data Stream configuration

        Parameters:
            streamName (str):  Example: streamName_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_new_oracle_goldengate_data_stream_configuration(
                streamName='streamName_example',
                data={
                    "source": "trail://localhost:9012/services/v2/sources?trail=a1",
                    "begin": "now",
                    "$schema": "ogg:dataStream"
                }
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
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def update_existing_oracle_goldengate_data_stream_configuration(self, streamName, data=None, version='v2', raw_response=False):
        """
        Distribution Service
        PATCH /services/{version}/stream/{streamName}
        Required Role: Administrator
        Update an existing Oracle GoldenGate Data Stream configuration

        Parameters:
            streamName (str):  Example: streamName_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_existing_oracle_goldengate_data_stream_configuration(
                streamName='streamName_example',
                data={
                    "source": "trail://localhost:9012/services/v2/sources?trail=a1",
                    "begin": "earliest",
                    "$schema": "ogg:dataStream"
                }
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
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def retrieve_existing_oracle_goldengate_data_stream_configuration(self, streamName, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/stream/{streamName}
        Required Role: Operator
        Retrieve an existing Oracle GoldenGate Data Stream configuration

        Parameters:
            streamName (str):  Example: streamName_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_data_stream_configuration(
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

    # Endpoint: /services/{version}/stream/{streamName}/info
    def retrieve_existing_oracle_goldengate_data_stream_information(self, streamName, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/stream/{streamName}/info
        Required Role: User
        Retrieve an existing Oracle GoldenGate Data Stream Information

        Parameters:
            streamName (str):  Example: streamName_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_data_stream_information(
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
    def retrieve_data_stream_service_error_messages_if_applicable(self, streamName, version='v2', raw_response=False):
        """
        Data Stream Service error messages
        GET /services/{version}/stream/{streamName}/info/errors
        Required Role: User
        Retrieve the data stream service error messages if applicable

        Parameters:
            streamName (str):  Example: streamName_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_data_stream_service_error_messages_if_applicable(
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
    def retrieve_asyncapi_yaml_specification(self, streamName, version='v2', raw_response=False):
        """
        Distribution Service
        GET /services/{version}/stream/{streamName}/yaml
        Required Role: User
        Retrieve the asyncapi yaml specification

        Parameters:
            streamName (str):  Example: streamName_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_asyncapi_yaml_specification(
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
    def update_asyncapi_yaml_specification(self, streamName, data=None, version='v2', raw_response=False):
        """
        Distribution Service
        PATCH /services/{version}/stream/{streamName}/yaml
        Required Role: Administrator
        Update the AsyncAPI YAML specification

        Parameters:
            streamName (str):  Example: streamName_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_asyncapi_yaml_specification(
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
    def get_list_distribution_paths_targets(self, version='v2', raw_response=False):
        """
        Receiver Service
        GET /services/{version}/targets
        Required Role: User
        Get a list of distribution paths

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_list_distribution_paths_targets()

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
    def delete_existing_oracle_goldengate_collector_path(self, path, version='v2', raw_response=False):
        """
        Receiver Service
        DELETE /services/{version}/targets/{path}
        Required Role: Administrator
        Delete an existing Oracle GoldenGate Collector Path

        Parameters:
            path (str):  Example: path_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_existing_oracle_goldengate_collector_path(
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

    # Endpoint: /services/{version}/targets/{path}
    def create_new_oracle_goldengate_collector_path(self, path, data=None, version='v2', raw_response=False):
        """
        Receiver Service
        POST /services/{version}/targets/{path}
        Required Role: Administrator
        Create a new Oracle GoldenGate Collector Path

        Parameters:
            path (str):  Example: path_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_new_oracle_goldengate_collector_path(
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
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}
    def update_existing_oracle_goldengate_collector_path(self, path, data=None, version='v2', raw_response=False):
        """
        Receiver Service
        PATCH /services/{version}/targets/{path}
        Required Role: Operator
        Update an existing Oracle GoldenGate Collector Path

        Parameters:
            path (str):  Example: path_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_existing_oracle_goldengate_collector_path(
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
            ogg_service="recvsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/targets/{path}
    def retrieve_existing_oracle_goldengate_collector_path(self, path, version='v2', raw_response=False):
        """
        Receiver Service
        GET /services/{version}/targets/{path}
        Required Role: User
        Retrieve an existing Oracle GoldenGate Collector Path

        Parameters:
            path (str):  Example: path_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_collector_path(
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

    # Endpoint: /services/{version}/targets/{path}/checkpoints
    def retrieve_existing_oracle_goldengate_receiver_service_path_checkpoints(self, path, version='v2', raw_response=False):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/checkpoints
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Path Checkpoints

        Parameters:
            path (str):  Example: path_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_receiver_service_path_checkpoints(
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
    def retrieve_existing_oracle_goldengate_receiver_service_path_information(self, path, version='v2', raw_response=False):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/info
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Path Information

        Parameters:
            path (str):  Example: path_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_receiver_service_path_information(
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
    def retrieve_existing_oracle_goldengate_receiver_service_progress(self, path, version='v2', raw_response=False):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/progress
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Progress

        Parameters:
            path (str):  Example: path_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_receiver_service_progress(
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
    def retrieve_existing_oracle_goldengate_receiver_service_path_stats(self, path, version='v2', raw_response=False):
        """
        Receiver Service
        GET /services/{version}/targets/{path}/stats
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Path Stats

        Parameters:
            path (str):  Example: path_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_existing_oracle_goldengate_receiver_service_path_stats(
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
    def list_tasks(self, version='v2', raw_response=False):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks
        Required Role: User
        Retrieve the list of tasks

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_task(self, task, version='v2', raw_response=False):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks/{task}
        Required Role: User
        Retrieve the details for a task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Example: task_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_task(
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
    def create_task(self, task, data=None, version='v2', raw_response=False):
        """
        Administration Service/Tasks
        POST /services/{version}/tasks/{task}
        Required Role: Administrator
        Create a new administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Example: task_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def update_task(self, task, data=None, version='v2', raw_response=False):
        """
        Administration Service/Tasks
        PATCH /services/{version}/tasks/{task}
        Required Role: Administrator
        Update an existing administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Example: task_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_task(
                task='task_example',
                data={
                    "enabled": True
                }
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def delete_task(self, task, version='v2', raw_response=False):
        """
        Administration Service/Tasks
        DELETE /services/{version}/tasks/{task}
        Required Role: Administrator
        Delete an administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Example: task_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def list_information_types_task(self, task, version='v2', raw_response=False):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks/{task}/info
        Required Role: User
        Retrieve the collection of information types available for a task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Example: task_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_information_types_task(
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
    def retrieve_task_history(self, task, version='v2', raw_response=False):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks/{task}/info/history
        Required Role: User
        Retrieve the execution history of an administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Example: task_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_task_history(
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
    def retrieve_task_status(self, task, version='v2', raw_response=False):
        """
        Administration Service/Tasks
        GET /services/{version}/tasks/{task}/info/status
        Required Role: User
        Retrieve the current status of an administrative task.

        Parameters:
            task (str): Task name, an alpha-numeric character followed by up to 63 alpha-numeric characters,
                '_' or '-'. Example: task_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_task_status(
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
    def list_trails(self, version='v2', raw_response=False):
        """
        Administration Service/Trails
        GET /services/{version}/trails
        Required Role: User
        Retrieve a collection of all known trails

        Parameters:
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_trail(self, trail, version='v2', raw_response=False):
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
                POST operations accept only the human-friendly name. Example: trail_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_trail(
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
    def create_trail(self, trail, data=None, version='v2', raw_response=False):
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
                POST operations accept only the human-friendly name. Example: trail_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}
    def update_trail(self, trail, data=None, version='v2', raw_response=False):
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
                POST operations accept only the human-friendly name. Example: trail_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}
    def delete_trail(self, trail, version='v2', raw_response=False):
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
                POST operations accept only the human-friendly name. Example: trail_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def list_trail_sequences(self, trail, version='v2', raw_response=False):
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
                POST operations accept only the human-friendly name. Example: trail_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def delete_trail_sequence_collection(self, trail, version='v2', raw_response=False):
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
                POST operations accept only the human-friendly name. Example: trail_example
            version (str): Oracle GoldenGate Service API version. Example: v2
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
    def retrieve_trail_sequence(self, sequence, trail, version='v2', raw_response=False):
        """
        Administration Service/Trails
        GET /services/{version}/trails/{trail}/sequences/{sequence}
        Required Role: Administrator
        Retrieve a trail sequence

        Parameters:
            sequence (int): The trail sequence number. Example: 1
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
                POST operations accept only the human-friendly name. Example: trail_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.retrieve_trail_sequence(
                sequence=1,
                trail='trail_example'
            )
        """
        path_params = {
            "sequence": sequence,
            "trail": trail,
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
    def create_trail_sequence(self, sequence, trail, data=None, version='v2', raw_response=False):
        """
        Administration Service/Trails
        POST /services/{version}/trails/{trail}/sequences/{sequence}
        Required Role: Administrator
        Create a new trail sequence in a trail by uploading file content

        Parameters:
            sequence (int): The trail sequence number. Example: 1
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
                POST operations accept only the human-friendly name. Example: trail_example
            data (dict): Data payload. See call example below for more details.
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.create_trail_sequence(
                sequence=1,
                trail='trail_example',
                data={})
        """
        path_params = {
            "sequence": sequence,
            "trail": trail,
            "version": version,
        }
        return self._call(
            "POST",
            "/services/{version}/trails/{trail}/sequences/{sequence}",
            path_params=path_params,
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences/{sequence}
    def delete_trail_sequence(self, sequence, trail, version='v2', raw_response=False):
        """
        Administration Service/Trails
        DELETE /services/{version}/trails/{trail}/sequences/{sequence}
        Required Role: Administrator
        Delete a trail sequence from a trail

        Parameters:
            sequence (int): The trail sequence number. Example: 1
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
                POST operations accept only the human-friendly name. Example: trail_example
            version (str): Oracle GoldenGate Service API version. Example: v2
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_trail_sequence(
                sequence=1,
                trail='trail_example'
            )
        """
        path_params = {
            "sequence": sequence,
            "trail": trail,
            "version": version,
        }
        return self._call(
            "DELETE",
            "/services/{version}/trails/{trail}/sequences/{sequence}",
            path_params=path_params,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )
