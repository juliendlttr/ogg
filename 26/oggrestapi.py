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

    # HTTP statuses worth retrying (transient / server-side).
    _RETRY_STATUSES = frozenset({429, 502, 503, 504})
    # Exceptions worth retrying (transient network issues).
    _RETRY_EXCEPTIONS = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    )

    def __init__(self, url, username=None, password=None, deployment=None, ca_cert=None,
                 reverse_proxy=False, verify_ssl=True, test_connection=True, timeout=None, version='v2'):
        """
        Initialize Oracle GoldenGate REST API client.

        :param url: Base URL of the OGG REST API. It can be:
                    'http(s)://hostname:port' without NGINX reverse proxy,
                    'https://nginx_host:nginx_port' with NGINX reverse proxy.
        :param username: service username
        :param password: service password. If omitted, the user is prompted to
                         enter it securely (input is not echoed).
        :param deployment: when reverse proxy is used, the deployment name to use (e.g. 'ogg_test_01')
        :param ca_cert: path to a trusted CA cert (for self-signed certs)
        :param reverse_proxy: bool, whether to use NGINX reverse proxy
        :param verify_ssl: bool, whether to verify SSL certs
        :param test_connection: if True, will attempt to retrieve API versions on init
        :param timeout: request timeout in seconds
        """
        self.swagger_version = '2026.01.27'
        self.version = version
        self.base_url = url
        self.username = username
        if password is None:
            password = getpass.getpass(f'Password for {username or "OGG REST API"}: ')
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

    def _request(self, method, path, *, params=None, data=None, max_retries=3,
                 backoff_factor=1.0, raw_response=False):
        """Make an HTTP request, retrying transient failures, then parse the response.

        Retries are attempted for transient network exceptions (connection errors,
        timeouts, chunked-encoding errors) and for retryable HTTP statuses
        (429, 502, 503, 504), using exponential backoff. When the server sends a
        ``Retry-After`` header, that value is honored instead of the computed delay.

        Args:
            method (str): The HTTP method to use.
            path (str): The API endpoint path.
            params (dict, optional): Query parameters for the request. Defaults to None.
            data (dict, optional): The request body data. Defaults to None.
            max_retries (int, optional): Maximum number of attempts. Defaults to 3.
            backoff_factor (float, optional): Base delay (seconds) for exponential
                backoff. Delay for attempt n is backoff_factor * 2**(n-1). Defaults to 1.0.
            raw_response (bool, optional): Whether to return the raw response object. Defaults to False.

        Returns:
            dict or requests.Response: The parsed response or the raw response object.
        """
        url = f'{self.base_url}{path}'
        response = None
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
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
            except self._RETRY_EXCEPTIONS as exc:
                last_exc = exc
                if attempt >= max_retries:
                    raise
                delay = self._retry_delay(attempt, backoff_factor)
                print(f"Request to {url} failed ({exc.__class__.__name__}: {exc}); "
                      f"retrying in {delay:.1f}s (attempt {attempt}/{max_retries})...")
                time.sleep(delay)
                continue

            # Retry transient server-side statuses while attempts remain.
            if response.status_code in self._RETRY_STATUSES and attempt < max_retries:
                delay = self._retry_delay(attempt, backoff_factor, response=response)
                print(f"Request to {url} returned HTTP {response.status_code}; "
                      f"retrying in {delay:.1f}s (attempt {attempt}/{max_retries})...")
                time.sleep(delay)
                continue

            break

        if response is None:
            # Every attempt raised a network exception; surface the last one.
            raise last_exc

        if raw_response:
            return response
        result = self._parse(response)
        self._check_response(response, url)
        return self._extract_main(result)

    def _retry_delay(self, attempt, backoff_factor, response=None):
        """Compute the delay before the next retry.

        Honors a ``Retry-After`` response header (seconds) when present, otherwise
        falls back to exponential backoff: backoff_factor * 2**(attempt - 1).
        """
        if response is not None:
            retry_after = response.headers.get('Retry-After')
            if retry_after:
                try:
                    return float(retry_after)
                except (TypeError, ValueError):
                    pass
        return backoff_factor * (2 ** (attempt - 1))

    def _build_path(self, template, ogg_service=None, path_params=None):
        path_params = dict(path_params or {})
        if "{version}" in template and "version" not in path_params:
            path_params["version"] = self.version

        # If reverse proxy is enabled, the full service must be added before /v2/
        #   - /services/ServiceManager/v2/... for Service Manager
        #   - /services/deployment_name/ogg_service/v2/... for other services when a deployment is specified
        if self.reverse_proxy and template != '/services':
            if ogg_service == 'ServiceManager' or not self.deployment:
                template = f'/services/ServiceManager/{template.removeprefix("/services/")}'
            else:
                template = f'/services/{self.deployment}/{ogg_service}/{template.removeprefix("/services/")}'
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
                # Copy first so the caller's dict is never mutated.
                data = dict(data)
                for k, v in body_params.items():
                    if v is not None:
                        data[k] = v
            if not data:
                data = None

        # If caller asked to skip on existing resource, inspect the raw response and
        # treat a 409 (already exists) as a no-op instead of an error. Routing through
        # _request means this path inherits the same retry handling as normal calls.
        if if_exists == 'skip':
            response = self._request(method, path, params=params, data=data, raw_response=True)
            parsed = self._parse(response)

            if response.status_code == 409:
                titles = []
                if isinstance(parsed, dict):
                    for m in parsed.get('messages', []):
                        if isinstance(m, dict) and m.get('title'):
                            titles.append(m['title'])
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
        Required Role: Any
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

    # Endpoint: /services/{version}/aiservice/models
    def list_ai_service_models(
        self,
        raw_response=False
    ):
        """
        Admin Server/AI Management
        GET /services/{version}/aiservice/models
        Required Role: Operator
        Retrieve the AI Service Models.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_ai_service_models()

        """
        return self._call(
            method="GET",
            template="/services/{version}/aiservice/models",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/aiservice/models/{model}
    def get_ai_service_model(
        self,
        model,
        raw_response=False
    ):
        """
        Admin Server/AI Management
        GET /services/{version}/aiservice/models/{model}
        Required Role: Operator
        Retrieve the details of an AI Model.

        Parameters:
            model (str): Name of the Model. Required. Example: model_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_ai_service_model(
                model='model_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/aiservice/models/{model}",
            path_params={
                "model": model,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/authorization
    def exchange_auth_code_for_token(
        self,
        raw_response=False
    ):
        """
        OAuth redirect URL
        GET /services/{version}/authorization
        Required Role: Any
        Receives the authorization code and exchanges it for an access and id token

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.exchange_auth_code_for_token()

        """
        return self._call(
            method="GET",
            template="/services/{version}/authorization",
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
        Required Role: Security
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
        Required Role: Security
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
        Required Role: Security
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
        Required Role: User
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
        data=None,
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
        return self._call(
            method="POST",
            template="/services/{version}/authorizations/{role}/{user}",
            path_params={
                "role": role,
                "user": user,
            },
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
        return self._call(
            method="PATCH",
            template="/services/{version}/authorizations/{role}/{user}",
            path_params={
                "role": role,
                "user": user,
            },
            data=data,
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
        Required Role: Security
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

    # Endpoint: /services/{version}/authorizations/{role}/{user}/info
    def get_user_info(
        self,
        role,
        user,
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
        return self._call(
            method="GET",
            template="/services/{version}/authorizations/{role}/{user}/info",
            path_params={
                "role": role,
                "user": user,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/certificates
    def list_certificate_types(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Certificates
        GET /services/{version}/certificates
        Required Role: Administrator
        Retrieve the collection of certificate types.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_certificate_types(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/certificates",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/certificates/{type}
    def list_certificates(
        self,
        type,
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
        return self._call(
            method="GET",
            template="/services/{version}/certificates/{type}",
            path_params={
                "type": type,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/certificates/{type}/{certificate}
    def get_certificate(
        self,
        type,
        certificate,
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
        return self._call(
            method="GET",
            template="/services/{version}/certificates/{type}/{certificate}",
            path_params={
                "type": type,
                "certificate": certificate,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/certificates/{type}/{certificate}/info
    def get_certificate_info(
        self,
        type,
        certificate,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_certificate_info(
                type='type_example',
                certificate='certificate_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/certificates/{type}/{certificate}/info",
            path_params={
                "type": type,
                "certificate": certificate,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/commands/execute
    def execute_command(
        self,
        data=None,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files
    def list_configuration_files(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        GET /services/{version}/config/files
        Required Role: User
        Retrieve the collection of configuration files.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_configuration_files(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/config/files",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def get_configuration_file(
        self,
        file,
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
        return self._call(
            method="GET",
            template="/services/{version}/config/files/{file}",
            path_params={
                "file": file,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def create_configuration_file(
        self,
        file,
        lines=None,
        data=None,
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
            ogg_service=ogg_service,
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def delete_configuration_file(
        self,
        file,
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
        return self._call(
            method="DELETE",
            template="/services/{version}/config/files/{file}",
            path_params={
                "file": file,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/files/{file}
    def update_configuration_file(
        self,
        file,
        lines=None,
        data=None,
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
            ogg_service=ogg_service,
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
        Required Role: User
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
        Required Role: Any
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
        Required Role: User
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
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Configuration Settings
        GET /services/{version}/config/types
        Required Role: User
        Retrieve the collection of configuration variable data types.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_config_types(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/config/types",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def get_config_type(
        self,
        type,
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
        return self._call(
            method="GET",
            template="/services/{version}/config/types/{type}",
            path_params={
                "type": type,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def create_config_type(
        self,
        type,
        data=None,
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
            ogg_service=ogg_service,
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}
    def delete_config_type(
        self,
        type,
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
        return self._call(
            method="DELETE",
            template="/services/{version}/config/types/{type}",
            path_params={
                "type": type,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values
    def list_config_values(
        self,
        type,
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
        return self._call(
            method="GET",
            template="/services/{version}/config/types/{type}/values",
            path_params={
                "type": type,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def get_config_value(
        self,
        type,
        value,
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
        return self._call(
            method="GET",
            template="/services/{version}/config/types/{type}/values/{value}",
            path_params={
                "type": type,
                "value": value,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def create_config_value(
        self,
        type,
        value,
        data=None,
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
        return self._call(
            method="POST",
            template="/services/{version}/config/types/{type}/values/{value}",
            path_params={
                "type": type,
                "value": value,
            },
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
        return self._call(
            method="DELETE",
            template="/services/{version}/config/types/{type}/values/{value}",
            path_params={
                "type": type,
                "value": value,
            },
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def update_config_value(
        self,
        type,
        value,
        data=None,
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
        return self._call(
            method="PUT",
            template="/services/{version}/config/types/{type}/values/{value}",
            path_params={
                "type": type,
                "value": value,
            },
            data=data,
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections
    def list_connections(
        self,
        raw_response=False
    ):
        """
        Administration Service/Database
        GET /services/{version}/connections
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}
    def get_connection(
        self,
        connection,
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
            ogg_service="adminsrvr",
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
            ogg_service="adminsrvr",
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
        Administration Service/Database
        DELETE /services/{version}/connections/{connection}
        Required Role: Administrator
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
            ogg_service="adminsrvr",
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/activeTransactions
    def get_active_transactions(
        self,
        connection,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/databases
    def list_database_names(
        self,
        connection,
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
            ogg_service="adminsrvr",
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
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases/{database}
        Required Role: User
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
            ogg_service="adminsrvr",
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
        Administration Service/Database
        GET /services/{version}/connections/{connection}/databases/{database}/{schema}
        Required Role: User
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
                    "csn": 32036323,
                    "source": "DBNORTH_PDB1"
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
            ogg_service="adminsrvr",
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
        Administration Service/Database
        POST /services/{version}/connections/{connection}/tables/checkpoint
        Required Role: Administrator
        Manage Oracle GoldenGate Checkpoint table

        Parameters:
            connection (str): Connection name. For each alias in the credential store, a connection with the
                name 'domain.alias' exists. Required. Example: MYCONN
            operation (str): Required if not included in `data`. Example: operation_example
            name (str):  Example: name_example
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
                    "name": "ggadmin.ggs_checkpoint"
                }
            )

            client.manage_checkpoint_table(
                connection='MYCONN',
                operation='add',
                name='ggadmin.ggs_checkpoint'
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
            ogg_service="adminsrvr",
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
        Administration Service/Database
        GET /services/{version}/connections/{connection}/tables/heartbeat
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def create_heartbeat_table(
        self,
        connection,
        upgrade=None,
        tracking_extract_restart=None,
        purge_frequency=None,
        retention_time=None,
        db_unique_name=None,
        partitioned=None,
        target_only=None,
        frequency=None,
        data=None,
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
            tracking_extract_restart (bool): Whether current heartbeat table setup is tracking extract
                restart position or not. Example: trackingExtractRestart_example
            purge_frequency (int): Interval, in days, at which the heartbeat history table is purged.
                Example: purgeFrequency_example
            retention_time (int): Heartbeats older than this retention time (in days) will be deleted from
                the heartbeat table. Example: retentionTime_example
            db_unique_name (bool): Whether current heartbeat table setup has db_unique_name column or not.
                Example: dbUniqueName_example
            partitioned (bool): Whether the heartbeat history table is partitioned or not. Example:
                partitioned_example
            target_only (bool): Boolean value to enable or disable supplemental logging and the scheduler
                job for updating heartbeat seed and heartbeat tables. Example: targetOnly_example
            frequency (int): Interval, in seconds, at which the heartbeat table is updated. Example:
                frequency_example
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
                    "frequency": 30
                }
            )

            client.create_heartbeat_table(
                connection='MYCONN',
                upgrade=None,
                tracking_extract_restart=None,
                purge_frequency=None,
                retention_time=None,
                db_unique_name=None,
                partitioned=None,
                target_only=None,
                frequency=30
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
                "upgrade": upgrade,
                "trackingExtractRestart": tracking_extract_restart,
                "purgeFrequency": purge_frequency,
                "retentionTime": retention_time,
                "dbUniqueName": db_unique_name,
                "partitioned": partitioned,
                "targetOnly": target_only,
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
        tracking_extract_restart=None,
        purge_frequency=None,
        retention_time=None,
        db_unique_name=None,
        partitioned=None,
        target_only=None,
        frequency=None,
        data=None,
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
            tracking_extract_restart (bool): Whether current heartbeat table setup is tracking extract
                restart position or not. Example: trackingExtractRestart_example
            purge_frequency (int): Interval, in days, at which the heartbeat history table is purged.
                Example: purgeFrequency_example
            retention_time (int): Heartbeats older than this retention time (in days) will be deleted from
                the heartbeat table. Example: retentionTime_example
            db_unique_name (bool): Whether current heartbeat table setup has db_unique_name column or not.
                Example: dbUniqueName_example
            partitioned (bool): Whether the heartbeat history table is partitioned or not. Example:
                partitioned_example
            target_only (bool): Boolean value to enable or disable supplemental logging and the scheduler
                job for updating heartbeat seed and heartbeat tables. Example: targetOnly_example
            frequency (int): Interval, in seconds, at which the heartbeat table is updated. Example:
                frequency_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_heartbeat_table(
                connection='MYCONN',
                data={
                    "purgeFrequency": 7
                }
            )

            client.update_heartbeat_table(
                connection='MYCONN',
                upgrade=None,
                tracking_extract_restart=None,
                purge_frequency=7,
                retention_time=None,
                db_unique_name=None,
                partitioned=None,
                target_only=None,
                frequency=None
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
                "upgrade": upgrade,
                "trackingExtractRestart": tracking_extract_restart,
                "purgeFrequency": purge_frequency,
                "retentionTime": retention_time,
                "dbUniqueName": db_unique_name,
                "partitioned": partitioned,
                "targetOnly": target_only,
                "frequency": frequency,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def delete_heartbeat_table(
        self,
        connection,
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
            ogg_service="adminsrvr",
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
        Administration Service/Database
        GET /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Required Role: User
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
            ogg_service="adminsrvr",
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
        Administration Service/Database
        DELETE /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Required Role: Administrator
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeatData
    def get_heartbeat_data(
        self,
        connection,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_heartbeat_data(
                connection='MYCONN'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/connections/{connection}/tables/heartbeatData",
            path_params={
                "connection": connection,
            },
            ogg_service="adminsrvr",
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
            ogg_service="adminsrvr",
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
        Administration Service/Database
        POST /services/{version}/connections/{connection}/trandata/schema
        Required Role: Administrator
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
                    "schemaName": "DBNORTH_PDB1.hr"
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
            ogg_service="adminsrvr",
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
        Administration Service/Database
        POST /services/{version}/connections/{connection}/trandata/table
        Required Role: Administrator
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
                    "$schema": "ogg:trandataTable",
                    "operation": "add",
                    "tableName": "hr.employees"
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
            ogg_service="adminsrvr",
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
        Required Role: Any
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
        Administration Service/Credentials
        GET /services/{version}/credentials
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/credentials/{domain}
    def list_credentials(
        self,
        domain,
        raw_response=False
    ):
        """
        Administration Service/Credentials
        GET /services/{version}/credentials/{domain}
        Required Role: User
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
            ogg_service="adminsrvr",
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
        Administration Service/Credentials
        GET /services/{version}/credentials/{domain}/{alias}
        Required Role: User
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
            ogg_service="adminsrvr",
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
        Administration Service/Credentials
        DELETE /services/{version}/credentials/{domain}/{alias}
        Required Role: Administrator
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
            ogg_service="adminsrvr",
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
        Administration Service/Credentials
        GET /services/{version}/credentials/{domain}/{alias}/valid
        Required Role: User
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
            ogg_service="adminsrvr",
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
        Required Role: User
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
        Required Role: User
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

    # Endpoint: /services/{version}/currentuser/reauthorize
    def reauthorize_current_user(
        self,
        raw_response=False
    ):
        """
        Reauthorize current user
        POST /services/{version}/currentuser/reauthorize
        Required Role: User
        Use this endpoint to reauthorize the current user

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.reauthorize_current_user()

        """
        return self._call(
            method="POST",
            template="/services/{version}/currentuser/reauthorize",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/dataTargetTypes
    def list_data_target_types(
        self,
        raw_response=False
    ):
        """
        Distribution Service/Data Target
        GET /services/{version}/dataTargetTypes
        Required Role: User
        Retrieve supported data target types from the Distribution Service

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_data_target_types()

        """
        return self._call(
            method="GET",
            template="/services/{version}/dataTargetTypes",
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/dataTargetTypes/{dataTargetType}
    def get_data_target_type(
        self,
        data_target_type,
        raw_response=False
    ):
        """
        Distribution Service/Data Target
        GET /services/{version}/dataTargetTypes/{dataTargetType}
        Required Role: User
        Retrieve the json schema of a supported data target.

        Parameters:
            data_target_type (str): The name of a supported data target. Required. Example:
                dataTargetType_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_data_target_type(
                data_target_type='dataTargetType_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/dataTargetTypes/{data_target_type}",
            path_params={
                "data_target_type": data_target_type,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/datastore
    def get_datastore(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Service/Datastore
        GET /services/{version}/datastore
        Required Role: User
        Retrieve the details of the datastore

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_datastore()

        """
        return self._call(
            method="GET",
            template="/services/{version}/datastore",
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/datastore
    def update_datastore(
        self,
        retention_days=None,
        collector_worker_threads=None,
        path=None,
        collector_worker_queue_limit=None,
        monitor_heart_beat_timeout=None,
        data_store_max_dbs=None,
        reinitialize=None,
        type=None,
        repair=None,
        data=None,
        raw_response=False
    ):
        """
        Performance Metrics Service/Datastore
        PATCH /services/{version}/datastore
        Required Role: Administrator
        Change the datastore configuration used by the Performance Metrics Service. Changes to the datastore
            configuration will cause the Performance Metrics Service to restart.

        Parameters:
            retention_days (int): The number of days to retain performance metrics data. If zero, data will
                be retained indefinitely. Example: retentionDays_example
            collector_worker_threads (int): Mpoint Collector Number of Worker Threads. Example:
                collectorWorkerThreads_example
            path (str): The path for the datastore storage. If not set, the datastore will be created in a
                default directory. Example: path_example
            collector_worker_queue_limit (int): Mpoint Collector Queue max size. Example:
                collectorWorkerQueueLimit_example
            monitor_heart_beat_timeout (int): Process monitoring heartbeat timeout in seconds. Example:
                monitorHeartBeatTimeout_example
            data_store_max_dbs (int): Max Databases. Example: dataStoreMaxDBs_example
            reinitialize (bool): If set to true, the datastore will be reinitialized upon restart. Example:
                reinitialize_example
            type (str): The type of datastore storage, either Berkeley Database (BDB) or Lightning
                Memory-Mapped Database (LMDB). Required if not included in `data`. Example: type_example
            repair (bool): If set to true, the datastore will be repaired upon restart. Example:
                repair_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_datastore(
                data={
                    "type": "LMDB",
                    "retentionDays": 30,
                    "collectorWorkerThreads": 5,
                    "collectorWorkerQueueLimit": 10000,
                    "monitorHeartBeatTimeout": 10,
                    "dataStoreMaxDBs": 5000
                }
            )

            client.update_datastore(
                retention_days=30,
                collector_worker_threads=5,
                path=None,
                collector_worker_queue_limit=10000,
                monitor_heart_beat_timeout=10,
                data_store_max_dbs=5000,
                reinitialize=None,
                type='LMDB',
                repair=None
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/datastore",
            data=data,
            body_params={
                "retentionDays": retention_days,
                "collectorWorkerThreads": collector_worker_threads,
                "path": path,
                "collectorWorkerQueueLimit": collector_worker_queue_limit,
                "monitorHeartBeatTimeout": monitor_heart_beat_timeout,
                "dataStoreMaxDBs": data_store_max_dbs,
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
        raw_response=False
    ):
        """
        Service Manager/Deployments
        GET /services/{version}/deployments
        Required Role: User
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
        Required Role: User
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
        cluster=None,
        ogg_data_home=None,
        ogg_conf_home=None,
        ogg_archive_home=None,
        enabled=None,
        id=None,
        configuration=None,
        ogg_ssl_home=None,
        status=None,
        ogg_etc_home=None,
        ogg_var_home=None,
        environment=None,
        password_regex=None,
        metrics=None,
        data=None,
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
            ogg_home (str): The deployment's home directory. Example: oggHome_example
            cluster (list): array that contains the roles of this deployment in each Oracle GoldenGate
                installation. Example: cluster_example
            ogg_data_home (str): The deployment's trail data directory. Example: oggDataHome_example
            ogg_conf_home (str): The deployment's configuration directory. Example: oggConfHome_example
            ogg_archive_home (str): The deployment's archived trail data directory. Example:
                oggArchiveHome_example
            enabled (bool): Indicates the deployment is managed by the Service Manager. Example:
                enabled_example
            id (str): An identifier that uniquely identifies this deployment. Example: id_example
            configuration (dict): Configuration Service settings for the deployment. Example:
                configuration_example
            ogg_ssl_home (str): The deployment's SSL configuration directory. Example: oggSslHome_example
            status (str): Indicates the status of the deployment. Example: status_example
            ogg_etc_home (str): The deployment's etc configuration directory. Example: oggEtcHome_example
            ogg_var_home (str): The deployment's var user data directory. Example: oggVarHome_example
            environment (list): Additional environment variables for the deployment. Example:
                environment_example
            password_regex (str): The regular expression that new user passwords must match. Example:
                passwordRegex_example
            metrics (dict): External servers for sending performance metrics. Example: metrics_example
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
                    "oggHome": "/u01/ogg",
                    "oggEtcHome": "/home/ogg/ogg/etc",
                    "oggVarHome": "/home/ogg/ogg/var",
                    "enabled": False
                }
            )

            client.create_deployment(
                deployment='deployment_example',
                ogg_home='/u01/ogg',
                cluster=[
                    {
                        "memberName": None,
                        "role": None
                    }
                ],
                ogg_data_home=None,
                ogg_conf_home=None,
                ogg_archive_home=None,
                enabled=False,
                id=None,
                configuration={
                    "backends": {
                        "standard": None,
                        "secure": None
                    }
                },
                ogg_ssl_home=None,
                status=None,
                ogg_etc_home='/home/ogg/ogg/etc',
                ogg_var_home='/home/ogg/ogg/var',
                environment=[
                    {
                        "name": None,
                        "value": None
                    }
                ],
                password_regex=None,
                metrics={
                    "enabled": None,
                    "servers": [
                        None
                    ]
                }
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
                "cluster": cluster,
                "oggDataHome": ogg_data_home,
                "oggConfHome": ogg_conf_home,
                "oggArchiveHome": ogg_archive_home,
                "enabled": enabled,
                "id": id,
                "configuration": configuration,
                "oggSslHome": ogg_ssl_home,
                "status": status,
                "oggEtcHome": ogg_etc_home,
                "oggVarHome": ogg_var_home,
                "environment": environment,
                "passwordRegex": password_regex,
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
        ogg_home=None,
        cluster=None,
        ogg_data_home=None,
        ogg_conf_home=None,
        ogg_archive_home=None,
        enabled=None,
        id=None,
        configuration=None,
        ogg_ssl_home=None,
        status=None,
        ogg_etc_home=None,
        ogg_var_home=None,
        environment=None,
        password_regex=None,
        metrics=None,
        data=None,
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
            ogg_home (str): The deployment's home directory. Example: oggHome_example
            cluster (list): array that contains the roles of this deployment in each Oracle GoldenGate
                installation. Example: cluster_example
            ogg_data_home (str): The deployment's trail data directory. Example: oggDataHome_example
            ogg_conf_home (str): The deployment's configuration directory. Example: oggConfHome_example
            ogg_archive_home (str): The deployment's archived trail data directory. Example:
                oggArchiveHome_example
            enabled (bool): Indicates the deployment is managed by the Service Manager. Example:
                enabled_example
            id (str): An identifier that uniquely identifies this deployment. Example: id_example
            configuration (dict): Configuration Service settings for the deployment. Example:
                configuration_example
            ogg_ssl_home (str): The deployment's SSL configuration directory. Example: oggSslHome_example
            status (str): Indicates the status of the deployment. Example: status_example
            ogg_etc_home (str): The deployment's etc configuration directory. Example: oggEtcHome_example
            ogg_var_home (str): The deployment's var user data directory. Example: oggVarHome_example
            environment (list): Additional environment variables for the deployment. Example:
                environment_example
            password_regex (str): The regular expression that new user passwords must match. Example:
                passwordRegex_example
            metrics (dict): External servers for sending performance metrics. Example: metrics_example
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
                cluster=[
                    {
                        "memberName": None,
                        "role": None
                    }
                ],
                ogg_data_home=None,
                ogg_conf_home=None,
                ogg_archive_home=None,
                enabled=True,
                id=None,
                configuration={
                    "backends": {
                        "standard": None,
                        "secure": None
                    }
                },
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
                password_regex=None,
                metrics={
                    "enabled": None,
                    "servers": [
                        None
                    ]
                }
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
                "cluster": cluster,
                "oggDataHome": ogg_data_home,
                "oggConfHome": ogg_conf_home,
                "oggArchiveHome": ogg_archive_home,
                "enabled": enabled,
                "id": id,
                "configuration": configuration,
                "oggSslHome": ogg_ssl_home,
                "status": status,
                "oggEtcHome": ogg_etc_home,
                "oggVarHome": ogg_var_home,
                "environment": environment,
                "passwordRegex": password_regex,
                "metrics": metrics,
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
        Required Role: Administrator
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

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles
    def list_authorization_profiles(
        self,
        deployment,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_authorization_profiles(
                deployment='deployment_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/authorization/profiles",
            path_params={
                "deployment": deployment,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
    def get_authorization_profile(
        self,
        deployment,
        profile,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_authorization_profile(
                deployment='deployment_example',
                profile='profile_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/authorization/profiles/{profile}",
            path_params={
                "deployment": deployment,
                "profile": profile,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
    def create_authorization_profile(
        self,
        deployment,
        profile,
        data=None,
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
        return self._call(
            method="POST",
            template="/services/{version}/deployments/{deployment}/authorization/profiles/{profile}",
            path_params={
                "deployment": deployment,
                "profile": profile,
            },
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
        return self._call(
            method="PATCH",
            template="/services/{version}/deployments/{deployment}/authorization/profiles/{profile}",
            path_params={
                "deployment": deployment,
                "profile": profile,
            },
            data=data,
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}
    def delete_authorization_profile(
        self,
        deployment,
        profile,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_authorization_profile(
                deployment='deployment_example',
                profile='profile_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/deployments/{deployment}/authorization/profiles/{profile}",
            path_params={
                "deployment": deployment,
                "profile": profile,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/authorization/profiles/{profile}/valid
    def is_authorization_profile_valid(
        self,
        deployment,
        profile,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.is_authorization_profile_valid(
                deployment='deployment_example',
                profile='profile_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/authorization/profiles/{profile}/valid",
            path_params={
                "deployment": deployment,
                "profile": profile,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates
    def list_deployment_certificates_types(
        self,
        deployment,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_deployment_certificates_types(
                deployment='deployment_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/certificates",
            path_params={
                "deployment": deployment,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}
    def list_deployment_certificates(
        self,
        deployment,
        type,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_deployment_certificates(
                deployment='deployment_example',
                type='type_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/certificates/{type}",
            path_params={
                "deployment": deployment,
                "type": type,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/certificates/{type}/{certificate}
    def get_deployment_certificate(
        self,
        deployment,
        type,
        certificate,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_deployment_certificate(
                deployment='deployment_example',
                type='type_example',
                certificate='certificate_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params={
                "deployment": deployment,
                "type": type,
                "certificate": certificate,
            },
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
        return self._call(
            method="POST",
            template="/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params={
                "deployment": deployment,
                "type": type,
                "certificate": certificate,
            },
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_deployment_certificate(
                deployment='deployment_example',
                type='type_example',
                certificate='certificate_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params={
                "deployment": deployment,
                "type": type,
                "certificate": certificate,
            },
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
        return self._call(
            method="PUT",
            template="/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}",
            path_params={
                "deployment": deployment,
                "type": type,
                "certificate": certificate,
            },
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_deployment_certificate_info(
                deployment='deployment_example',
                type='type_example',
                certificate='certificate_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/certificates/{type}/{certificate}/info",
            path_params={
                "deployment": deployment,
                "type": type,
                "certificate": certificate,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/plugin/templates
    def list_plugin_templates(
        self,
        deployment,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_plugin_templates(
                deployment='deployment_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/plugin/templates",
            path_params={
                "deployment": deployment,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/deployments/{deployment}/plugin/templates/{plugin}
    def get_plugin_template(
        self,
        deployment,
        plugin,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_plugin_template(
                deployment='deployment_example',
                plugin='plugin_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params={
                "deployment": deployment,
                "plugin": plugin,
            },
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
        return self._call(
            method="POST",
            template="/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params={
                "deployment": deployment,
                "plugin": plugin,
            },
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_plugin_template(
                deployment='deployment_example',
                plugin='plugin_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params={
                "deployment": deployment,
                "plugin": plugin,
            },
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
        return self._call(
            method="PUT",
            template="/services/{version}/deployments/{deployment}/plugin/templates/{plugin}",
            path_params={
                "deployment": deployment,
                "plugin": plugin,
            },
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
        Required Role: User
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
            config_force (bool): Force the configuration data (NO LONGER USED). Example: configForce_example
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
                            "serviceListeningPort": 19012
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
                        "serviceListeningPort": 19012
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
            config_force (bool): Force the configuration data (NO LONGER USED). Example: configForce_example
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
        Required Role: Administrator
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
        Required Role: User
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
        Required Role: Administrator
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
        Administration Service/Encryption Keys
        GET /services/{version}/enckeys
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def get_encryption_key(
        self,
        key_name,
        raw_response=False
    ):
        """
        Administration Service/Encryption Keys
        GET /services/{version}/enckeys/{keyName}
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def create_encryption_key(
        self,
        key_name,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Encryption Keys
        POST /services/{version}/enckeys/{keyName}
        Required Role: Administrator
        Create an Encryption Key.

        Parameters:
            key_name (str): The name of the Encryption Key. Required. Example: keyName_example
            data (dict): Data payload. See call example below for more details.
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
        """
        return self._call(
            method="POST",
            template="/services/{version}/enckeys/{key_name}",
            path_params={
                "key_name": key_name,
            },
            data=data,
            ogg_service="adminsrvr",
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
        Administration Service/Encryption Keys
        DELETE /services/{version}/enckeys/{keyName}
        Required Role: Administrator
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
            ogg_service="adminsrvr",
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
        Administration Service/Encryption Keys
        POST /services/{version}/enckeys/{keyName}/encrypt
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles
    def list_encryption_profiles(
        self,
        raw_response=False
    ):
        """
        Administration Service/Encryption Profiles
        GET /services/{version}/encryption/profiles
        Required Role: Any
        Retrieve names of all existing Encryption Profiles.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_encryption_profiles()

        """
        return self._call(
            method="GET",
            template="/services/{version}/encryption/profiles",
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}
    def get_encryption_profile(
        self,
        profile,
        raw_response=False
    ):
        """
        Administration Service/Encryption Profiles
        GET /services/{version}/encryption/profiles/{profile}
        Required Role: Any
        Retrieve details for an Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Required. Example: profile_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_encryption_profile(
                profile='profile_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/encryption/profiles/{profile}",
            path_params={
                "profile": profile,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}
    def create_encryption_profile(
        self,
        profile,
        data=None,
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
                        "ttl": 86400
                    }
                }
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/encryption/profiles/{profile}",
            path_params={
                "profile": profile,
            },
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
        return self._call(
            method="PATCH",
            template="/services/{version}/encryption/profiles/{profile}",
            path_params={
                "profile": profile,
            },
            data=data,
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}
    def delete_encryption_profile(
        self,
        profile,
        raw_response=False
    ):
        """
        Administration Service/Encryption Profiles
        DELETE /services/{version}/encryption/profiles/{profile}
        Required Role: Administrator
        Delete an Encryption Profile

        Parameters:
            profile (str): Name of the Encryption Profile. Required. Example: profile_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_encryption_profile(
                profile='profile_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/encryption/profiles/{profile}",
            path_params={
                "profile": profile,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/encryption/profiles/{profile}/valid
    def is_encryption_profile_valid(
        self,
        profile,
        raw_response=False
    ):
        """
        Administration Service/Encryption Profiles
        GET /services/{version}/encryption/profiles/{profile}/valid
        Required Role: Administrator
        Validate an Encryption Profile.

        Parameters:
            profile (str): Name of the Encryption Profile. Required. Example: profile_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.is_encryption_profile_valid(
                profile='profile_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/encryption/profiles/{profile}/valid",
            path_params={
                "profile": profile,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts
    def list_extracts(
        self,
        raw_response=False
    ):
        """
        Administration Service/Extracts
        GET /services/{version}/extracts
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def get_extract(
        self,
        extract,
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
        plugin_type=None,
        encryption_profile=None,
        status=None,
        critical=None,
        rollover=None,
        targets=None,
        managed_process_settings=None,
        replication_slot=None,
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
            plugin_type (str): Plugin type for creation of replication slot in PostgreSQL. Example:
                pluginType_example
            encryption_profile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the extract is critical to the deployment. Example: critical_example
            rollover (str): Causes Extract to increment to the next file in the trail sequence when
                restarting. Example: rollover_example
            targets (list): Targets for captured data. Example: targets_example
            managed_process_settings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            replication_slot (str): Replication slot which needs to be used for MIGRATE command in
                PostgreSQL. Example: replicationSlot_example
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
                plugin_type=None,
                encryption_profile=None,
                status=None,
                critical=None,
                rollover=None,
                targets=[
                    {
                        "name": "ea",
                        "path": "north/"
                    }
                ],
                managed_process_settings=None,
                replication_slot=None,
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
                    "alias": "ggnorth"
                },
                description='Region North'
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
                "pluginType": plugin_type,
                "encryptionProfile": encryption_profile,
                "status": status,
                "critical": critical,
                "rollover": rollover,
                "targets": targets,
                "managedProcessSettings": managed_process_settings,
                "replicationSlot": replication_slot,
                "intent": intent,
                "registration": registration,
                "source": source,
                "type": type,
                "miningCredentials": mining_credentials,
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
        plugin_type=None,
        encryption_profile=None,
        status=None,
        critical=None,
        rollover=None,
        targets=None,
        managed_process_settings=None,
        replication_slot=None,
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
            plugin_type (str): Plugin type for creation of replication slot in PostgreSQL. Example:
                pluginType_example
            encryption_profile (dict):  Example: encryptionProfile_example
            status (str): Oracle GoldenGate Process Status. Example: status_example
            critical (bool): Indicates the extract is critical to the deployment. Example: critical_example
            rollover (str): Causes Extract to increment to the next file in the trail sequence when
                restarting. Example: rollover_example
            targets (list): Targets for captured data. Example: targets_example
            managed_process_settings (dict): Control how the ER process is managed by the Administration
                Server. Example: managedProcessSettings_example
            replication_slot (str): Replication slot which needs to be used for MIGRATE command in
                PostgreSQL. Example: replicationSlot_example
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
                plugin_type=None,
                encryption_profile=None,
                status='running',
                critical=None,
                rollover=None,
                targets=[
                    None
                ],
                managed_process_settings=None,
                replication_slot=None,
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
                "pluginType": plugin_type,
                "encryptionProfile": encryption_profile,
                "status": status,
                "critical": critical,
                "rollover": rollover,
                "targets": targets,
                "managedProcessSettings": managed_process_settings,
                "replicationSlot": replication_slot,
                "intent": intent,
                "registration": registration,
                "source": source,
                "type": type,
                "miningCredentials": mining_credentials,
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
            ogg_service="adminsrvr",
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
        Administration Service/Extracts
        POST /services/{version}/extracts/{extract}/command
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info
    def get_extract_info_types(
        self,
        extract,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/checkpoints
    def get_extract_checkpoint(
        self,
        extract,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/diagnostics
    def list_extract_diagnostics(
        self,
        extract,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extract_diagnostics(
                extract='extract_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info/diagnostics",
            path_params={
                "extract": extract,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/diagnostics/{diagnostic}
    def get_extract_diagnostic(
        self,
        extract,
        diagnostic,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_diagnostic(
                extract='extract_example',
                diagnostic='diagnostic_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info/diagnostics/{diagnostic}",
            path_params={
                "extract": extract,
                "diagnostic": diagnostic,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/history
    def get_extract_history(
        self,
        extract,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/logs
    def list_extract_logs(
        self,
        extract,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_extract_logs(
                extract='extract_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info/logs",
            path_params={
                "extract": extract,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/logs/{log}
    def get_extract_log(
        self,
        extract,
        log,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_extract_log(
                extract='extract_example',
                log='log_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/extracts/{extract}/info/logs/{log}",
            path_params={
                "extract": extract,
                "log": log,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/reports
    def list_extract_reports(
        self,
        extract,
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
            ogg_service="adminsrvr",
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/status
    def get_extract_status(
        self,
        extract,
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
            ogg_service="adminsrvr",
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
        Required Role: User
        Get a list of the deployment extracts with their trail files

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

    # Endpoint: /services/{version}/installation/aiservice/health
    def get_installation_ai_service_health(
        self,
        raw_response=False
    ):
        """
        Service Manager/AI Management
        GET /services/{version}/installation/aiservice/health
        Required Role: Operator
        Retrieve the AI Service Health.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_installation_ai_service_health()

        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/aiservice/health",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/models
    def list_installation_ai_service_models(
        self,
        raw_response=False
    ):
        """
        Service Manager/AI Management
        GET /services/{version}/installation/aiservice/models
        Required Role: Operator
        Retrieve the AI Service Models.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_ai_service_models()

        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/aiservice/models",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/models/{model}
    def get_installation_ai_service_model(
        self,
        model,
        raw_response=False
    ):
        """
        Service Manager/AI Management
        GET /services/{version}/installation/aiservice/models/{model}
        Required Role: Operator
        Retrieve the details of an AI Model.

        Parameters:
            model (str): Name of the Model. Required. Example: model_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_installation_ai_service_model(
                model='model_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/aiservice/models/{model}",
            path_params={
                "model": model,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/models/{model}
    def create_installation_ai_service_model(
        self,
        model,
        capabilities=None,
        priority=None,
        tasks=None,
        loaded=None,
        provider_id=None,
        enabled=None,
        id=None,
        name=None,
        remote_model_name=None,
        type=None,
        limits=None,
        parameters=None,
        description=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/AI Management
        POST /services/{version}/installation/aiservice/models/{model}
        Required Role: Security
        Create an AI Model.

        Parameters:
            model (str): Name of the Model. Required. Example: model_example
            capabilities (list):  Example: capabilities_example
            priority (int):  Example: priority_example
            tasks (list):  Example: tasks_example
            loaded (bool):  Example: loaded_example
            provider_id (str):  Example: providerId_example
            enabled (bool):  Example: enabled_example
            id (str):  Example: id_example
            name (str):  Example: name_example
            remote_model_name (str):  Example: remoteModelName_example
            type (str):  Example: type_example
            limits (dict):  Example: limits_example
            parameters (dict):  Example: parameters_example
            description (str):  Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_installation_ai_service_model(
                model='model_example',
                data={
                    "name": "Voyage 2",
                    "description": "Voyage embedding model for regression",
                    "capabilities": [
                        "embed"
                    ],
                    "providerId": "voyage1",
                    "remoteModelName": "voyage-2",
                    "limits": {
                        "maxInputCharacters": 20000
                    }
                }
            )

            client.create_installation_ai_service_model(
                model='model_example',
                capabilities=[
                    "embed"
                ],
                priority=None,
                tasks=[
                    None
                ],
                loaded=None,
                provider_id='voyage1',
                enabled=None,
                id=None,
                name='Voyage 2',
                remote_model_name='voyage-2',
                type=None,
                limits={
                    "maxInputCharacters": 20000
                },
                parameters={},
                description='Voyage embedding model for regression'
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/installation/aiservice/models/{model}",
            path_params={
                "model": model,
            },
            data=data,
            body_params={
                "capabilities": capabilities,
                "priority": priority,
                "tasks": tasks,
                "loaded": loaded,
                "providerId": provider_id,
                "enabled": enabled,
                "id": id,
                "name": name,
                "remoteModelName": remote_model_name,
                "type": type,
                "limits": limits,
                "parameters": parameters,
                "description": description,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/models/{model}
    def update_installation_ai_service_model(
        self,
        model,
        capabilities=None,
        priority=None,
        tasks=None,
        loaded=None,
        provider_id=None,
        enabled=None,
        id=None,
        name=None,
        remote_model_name=None,
        type=None,
        limits=None,
        parameters=None,
        description=None,
        data=None,
        raw_response=False
    ):
        """
        Service Manager/AI Management
        PATCH /services/{version}/installation/aiservice/models/{model}
        Required Role: Security
        Modify an AI Model.

        Parameters:
            model (str): Name of the Model. Required. Example: model_example
            capabilities (list):  Example: capabilities_example
            priority (int):  Example: priority_example
            tasks (list):  Example: tasks_example
            loaded (bool):  Example: loaded_example
            provider_id (str):  Example: providerId_example
            enabled (bool):  Example: enabled_example
            id (str):  Example: id_example
            name (str):  Example: name_example
            remote_model_name (str):  Example: remoteModelName_example
            type (str):  Example: type_example
            limits (dict):  Example: limits_example
            parameters (dict):  Example: parameters_example
            description (str):  Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_installation_ai_service_model(
                model='model_example',
                data={
                    "name": "Voyage 2",
                    "description": "Voyage embedding model for regression",
                    "capabilities": [
                        "embed"
                    ],
                    "providerId": "voyage1",
                    "remoteModelName": "voyage-2",
                    "limits": {
                        "maxInputCharacters": 20000
                    }
                }
            )

            client.update_installation_ai_service_model(
                model='model_example',
                capabilities=[
                    "embed"
                ],
                priority=None,
                tasks=[
                    None
                ],
                loaded=None,
                provider_id='voyage1',
                enabled=None,
                id=None,
                name='Voyage 2',
                remote_model_name='voyage-2',
                type=None,
                limits={
                    "maxInputCharacters": 20000
                },
                parameters={},
                description='Voyage embedding model for regression'
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/installation/aiservice/models/{model}",
            path_params={
                "model": model,
            },
            data=data,
            body_params={
                "capabilities": capabilities,
                "priority": priority,
                "tasks": tasks,
                "loaded": loaded,
                "providerId": provider_id,
                "enabled": enabled,
                "id": id,
                "name": name,
                "remoteModelName": remote_model_name,
                "type": type,
                "limits": limits,
                "parameters": parameters,
                "description": description,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/models/{model}
    def delete_installation_ai_service_model(
        self,
        model,
        raw_response=False
    ):
        """
        Service Manager/AI Management
        DELETE /services/{version}/installation/aiservice/models/{model}
        Required Role: Security
        Delete an AI Model.

        Parameters:
            model (str): Name of the Model. Required. Example: model_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_installation_ai_service_model(
                model='model_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/installation/aiservice/models/{model}",
            path_params={
                "model": model,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/providers
    def list_installation_ai_service_providers(
        self,
        raw_response=False
    ):
        """
        Service Manager/AI Management
        GET /services/{version}/installation/aiservice/providers
        Required Role: Operator
        Retrieve the AI Service Providers.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_ai_service_providers()

        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/aiservice/providers",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/providers/{provider}
    def get_installation_ai_service_provider(
        self,
        provider,
        raw_response=False
    ):
        """
        Service Manager/AI Management
        GET /services/{version}/installation/aiservice/providers/{provider}
        Required Role: Security
        Retrieve the details of an AI Provider.

        Parameters:
            provider (str): Name of the Provider. Required. Example: provider_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_installation_ai_service_provider(
                provider='provider_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/aiservice/providers/{provider}",
            path_params={
                "provider": provider,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/providers/{provider}
    def create_installation_ai_service_provider(
        self,
        provider,
        capabilities=None,
        retry=None,
        authentication=None,
        enabled=None,
        id=None,
        tasks_types=None,
        name=None,
        base_url=None,
        metadata=None,
        type=None,
        regions=None,
        timeouts=None,
        description=None,
        headers=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/AI Management
        POST /services/{version}/installation/aiservice/providers/{provider}
        Required Role: Security
        Create an AI Provider.

        Parameters:
            provider (str): Name of the Provider. Required. Example: provider_example
            capabilities (list):  Example: capabilities_example
            retry (dict):  Example: retry_example
            authentication (dict):  Example: authentication_example
            enabled (bool):  Example: enabled_example
            id (str):  Example: id_example
            tasks_types (list):  Example: tasksTypes_example
            name (str):  Example: name_example
            base_url (str):  Example: baseUrl_example
            metadata (dict):  Example: metadata_example
            type (str):  Example: type_example
            regions (list):  Example: regions_example
            timeouts (dict):  Example: timeouts_example
            description (str):  Example: description_example
            headers (dict):  Example: headers_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_installation_ai_service_provider(
                provider='provider_example',
                data={
                    "name": "Voyage AI",
                    "description": "Voyage AI embedding provider",
                    "type": "voyage",
                    "baseUrl": "https://api.voyageai.com/v1",
                    "authentication": {
                        "type": "api_key",
                        "secret": "abcdefghijklmnopqrstuvwxyz0123456789"
                    }
                }
            )

            client.create_installation_ai_service_provider(
                provider='provider_example',
                capabilities=[
                    None
                ],
                retry={
                    "maxRetries": None,
                    "initialBackoffMs": None,
                    "maxBackoffMs": None,
                    "backoffMultiplier": None
                },
                authentication={
                    "type": "api_key",
                    "secret": "abcdefghijklmnopqrstuvwxyz0123456789"
                },
                enabled=None,
                id=None,
                tasks_types=[
                    None
                ],
                name='Voyage AI',
                base_url='https://api.voyageai.com/v1',
                metadata={},
                type='voyage',
                regions=[
                    None
                ],
                timeouts={
                    "requestTimeoutMs": None,
                    "connectTimeoutMs": None,
                    "totalTimeoutMs": None
                },
                description='Voyage AI embedding provider',
                headers={}
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/installation/aiservice/providers/{provider}",
            path_params={
                "provider": provider,
            },
            data=data,
            body_params={
                "capabilities": capabilities,
                "retry": retry,
                "authentication": authentication,
                "enabled": enabled,
                "id": id,
                "tasksTypes": tasks_types,
                "name": name,
                "baseUrl": base_url,
                "metadata": metadata,
                "type": type,
                "regions": regions,
                "timeouts": timeouts,
                "description": description,
                "headers": headers,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/providers/{provider}
    def update_installation_ai_service_provider(
        self,
        provider,
        capabilities=None,
        retry=None,
        authentication=None,
        enabled=None,
        id=None,
        tasks_types=None,
        name=None,
        base_url=None,
        metadata=None,
        type=None,
        regions=None,
        timeouts=None,
        description=None,
        headers=None,
        data=None,
        raw_response=False
    ):
        """
        Service Manager/AI Management
        PATCH /services/{version}/installation/aiservice/providers/{provider}
        Required Role: Security
        Patch an AI Provider.

        Parameters:
            provider (str): Name of the Provider. Required. Example: provider_example
            capabilities (list):  Example: capabilities_example
            retry (dict):  Example: retry_example
            authentication (dict):  Example: authentication_example
            enabled (bool):  Example: enabled_example
            id (str):  Example: id_example
            tasks_types (list):  Example: tasksTypes_example
            name (str):  Example: name_example
            base_url (str):  Example: baseUrl_example
            metadata (dict):  Example: metadata_example
            type (str):  Example: type_example
            regions (list):  Example: regions_example
            timeouts (dict):  Example: timeouts_example
            description (str):  Example: description_example
            headers (dict):  Example: headers_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_installation_ai_service_provider(
                provider='provider_example',
                data={
                    "authentication": {
                        "type": "api_key",
                        "secret": "abcdefghijklmnopqrstuvwxyz0123456789"
                    }
                }
            )

            client.update_installation_ai_service_provider(
                provider='provider_example',
                capabilities=[
                    None
                ],
                retry={
                    "maxRetries": None,
                    "initialBackoffMs": None,
                    "maxBackoffMs": None,
                    "backoffMultiplier": None
                },
                authentication={
                    "type": "api_key",
                    "secret": "abcdefghijklmnopqrstuvwxyz0123456789"
                },
                enabled=None,
                id=None,
                tasks_types=[
                    None
                ],
                name=None,
                base_url=None,
                metadata={},
                type=None,
                regions=[
                    None
                ],
                timeouts={
                    "requestTimeoutMs": None,
                    "connectTimeoutMs": None,
                    "totalTimeoutMs": None
                },
                description=None,
                headers={}
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/installation/aiservice/providers/{provider}",
            path_params={
                "provider": provider,
            },
            data=data,
            body_params={
                "capabilities": capabilities,
                "retry": retry,
                "authentication": authentication,
                "enabled": enabled,
                "id": id,
                "tasksTypes": tasks_types,
                "name": name,
                "baseUrl": base_url,
                "metadata": metadata,
                "type": type,
                "regions": regions,
                "timeouts": timeouts,
                "description": description,
                "headers": headers,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/aiservice/providers/{provider}
    def delete_installation_ai_service_provider(
        self,
        provider,
        raw_response=False
    ):
        """
        Service Manager/AI Management
        DELETE /services/{version}/installation/aiservice/providers/{provider}
        Required Role: Security
        Delete an AI Provider.

        Parameters:
            provider (str): Name of the Provider. Required. Example: provider_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_installation_ai_service_provider(
                provider='provider_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/installation/aiservice/providers/{provider}",
            path_params={
                "provider": provider,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster
    def get_cluster(
        self,
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        GET /services/{version}/installation/cluster
        Required Role: Administrator
        Retrieve the details for the installation's GoldenGate cluster.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_cluster()

        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/cluster",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster
    def create_cluster(
        self,
        availability_domain=None,
        members=None,
        fqdn=None,
        data_plane=None,
        region=None,
        join=None,
        back_plane=None,
        uses_reverse_proxy=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Cluster Management
        POST /services/{version}/installation/cluster
        Required Role: Security
        Add the GoldenGate installation to an existing cluster or create a new cluster.

        Parameters:
            availability_domain (str): The availability domain of the cluster member. Example:
                availabilityDomain_example
            members (list): Cluster members. Example: members_example
            fqdn (dict): The FQDN of the host. Example: fqdn_example
            data_plane (dict): The listener on the local installation for serving cluster data requests.
                Required if not included in `data`. Example: dataPlane_example
            region (str): The region of the cluster member. Required if not included in `data`. Example:
                region_example
            join (dict): Properties for joining an existing GoldenGate cluster. Example: join_example
            back_plane (dict): The listener on the local installation for intra-cluster member
                communication. Required if not included in `data`. Example: backPlane_example
            uses_reverse_proxy (bool): Whether the installation is behind a reverse proxy or not. Example:
                usesReverseProxy_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_cluster(
                data={
                    "dataPlane": {
                        "host": "127.0.0.1",
                        "port": 5512
                    },
                    "backPlane": {
                        "host": "0.0.0.0",
                        "port": 5511
                    }
                }
            )

            client.create_cluster(
                availability_domain=None,
                members=[
                    {
                        "availabilityDomain": None,
                        "fqdn": None,
                        "dataPlane": {
                            "host": None,
                            "port": None
                        },
                        "region": None,
                        "current": None,
                        "memberName": None,
                        "target": None,
                        "backPlane": {
                            "host": None,
                            "port": None
                        },
                        "usesReverseProxy": None
                    }
                ],
                fqdn=None,
                data_plane={
                    "host": "127.0.0.1",
                    "port": 5512
                },
                region=None,
                join={
                    "url": "https://remote-host.example.com:9011/services/v2",
                    "user": None,
                    "password": None
                },
                back_plane={
                    "host": "0.0.0.0",
                    "port": 5511
                },
                uses_reverse_proxy=None
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/installation/cluster",
            data=data,
            body_params={
                "availabilityDomain": availability_domain,
                "members": members,
                "fqdn": fqdn,
                "dataPlane": data_plane,
                "region": region,
                "join": join,
                "backPlane": back_plane,
                "usesReverseProxy": uses_reverse_proxy,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster
    def delete_cluster(
        self,
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        DELETE /services/{version}/installation/cluster
        Required Role: Security
        Remove the installation from the GoldenGate cluster.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_cluster()

        """
        return self._call(
            method="DELETE",
            template="/services/{version}/installation/cluster",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster/actions/memberAdd
    def add_cluster_member(
        self,
        member_name=None,
        region=None,
        availability_domain=None,
        fqdn=None,
        uses_reverse_proxy=None,
        back_plane=None,
        data_plane=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Service Manager/Cluster Management
        POST /services/{version}/installation/cluster/actions/memberAdd
        Required Role: Security
        Internal API for adding a remote GoldenGate installation to the cluster.

        Parameters:
            member_name (str): The name of the member to add to the cluster. Required if not included in
                `data`. Example: memberName_example
            region (str): The region of the new cluster member. Required if not included in `data`. Example:
                region_example
            availability_domain (str): The availability domain of the cluster member. Required if not
                included in `data`. Example: availabilityDomain_example
            fqdn (dict): The FQDN of the host. Required if not included in `data`. Example: fqdn_example
            uses_reverse_proxy (bool): Whether the installation is behind a reverse proxy or not. Required
                if not included in `data`. Example: usesReverseProxy_example
            back_plane (dict): The address of the listener on the new member for intra-cluster member
                communication. Required if not included in `data`. Example: backPlane_example
            data_plane (dict): The listener on the new member for serving cluster data requests. Example:
                dataPlane_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                        "port": 5511
                    },
                    "dataPlane": {
                        "host": "127.0.0.1",
                        "port": 5512
                    }
                }
            )

            client.add_cluster_member(
                member_name='oggdev-2',
                region=None,
                availability_domain=None,
                fqdn=None,
                uses_reverse_proxy=None,
                back_plane={
                    "host": "0.0.0.0",
                    "port": 5511
                },
                data_plane={
                    "host": "127.0.0.1",
                    "port": 5512
                }
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/installation/cluster/actions/memberAdd",
            data=data,
            body_params={
                "memberName": member_name,
                "region": region,
                "availabilityDomain": availability_domain,
                "fqdn": fqdn,
                "usesReverseProxy": uses_reverse_proxy,
                "backPlane": back_plane,
                "dataPlane": data_plane,
            },
            ogg_service="ServiceManager",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster/role/{member}
    def get_cluster_member(
        self,
        member,
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        GET /services/{version}/installation/cluster/role/{member}
        Required Role: Security
        Retrieve a member's role in the OGG cluster

        Parameters:
            member (str): Name of the OGG Cluster member. Required. Example: member_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_cluster_member(
                member='member_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/cluster/role/{member}",
            path_params={
                "member": member,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/cluster/role/{member}
    def update_cluster_member(
        self,
        member,
        member_name=None,
        current=None,
        target=None,
        data=None,
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        PATCH /services/{version}/installation/cluster/role/{member}
        Required Role: Security
        Update a member's role in the OGG cluster

        Parameters:
            member (str): Name of the OGG Cluster member. Required. Example: member_example
            member_name (str): The name of the cluster member. Example: memberName_example
            current (str): Member role. Example: current_example
            target (str): Member role. Example: target_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                member_name=None,
                current=None,
                target='backup'
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/installation/cluster/role/{member}",
            path_params={
                "member": member,
            },
            data=data,
            body_params={
                "memberName": member_name,
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
        raw_response=False
    ):
        """
        Service Manager/Cluster Management
        DELETE /services/{version}/installation/cluster/role/{member}
        Required Role: Security
        Delete a member from the OGG Cluster

        Parameters:
            member (str): Name of the OGG Cluster member. Required. Example: member_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_cluster_member(
                member='member_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/installation/cluster/role/{member}",
            path_params={
                "member": member,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration
    def get_configuration_service(
        self,
        raw_response=False
    ):
        """
        Service Manager/Installation
        GET /services/{version}/installation/configuration
        Required Role: Administrator
        Retrieve the configuration details for the GoldenGate installation.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_configuration_service()

        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/configuration",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration
    def update_configuration_service(
        self,
        installation_id=None,
        configuration_service_enabled=None,
        data=None,
        raw_response=False
    ):
        """
        Service Manager/Installation
        PATCH /services/{version}/installation/configuration
        Required Role: Security
        Update the configuration details for the GoldenGate installation.

        Parameters:
            installation_id (str): Unique Identifier for the installation. Example: installationId_example
            configuration_service_enabled (bool): Indicates the Configuration Service is enabled for the
                installation. Required if not included in `data`. Example:
                configurationServiceEnabled_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                installation_id='5b5bee89-6e93-4920-9ac7-0a5582623a2d',
                configuration_service_enabled=True
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/installation/configuration",
            data=data,
            body_params={
                "installationId": installation_id,
                "configurationServiceEnabled": configuration_service_enabled,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends
    def list_configuration_service_backends(
        self,
        raw_response=False
    ):
        """
        Service Manager/Installation
        GET /services/{version}/installation/configuration/backends
        Required Role: Administrator
        Retrieve a list of Backends known to the Configuration Service.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_configuration_service_backends()

        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/configuration/backends",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends
    def create_configuration_service_backend(
        self,
        id=None,
        configuration=None,
        name=None,
        replaced_by=None,
        encrypted=None,
        encryption_key=None,
        read_only=None,
        type=None,
        messages=None,
        locked=None,
        options=None,
        replaced=None,
        data=None,
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
            replaced_by (str): The Backend that replaced this backend. Example: replacedBy_example
            encrypted (bool): If true, data is encrypted at rest in the Backend. Example: encrypted_example
            encryption_key (str): The key to use for encrypting data in the Backend; if not specified, a
                random key will be generated. Example: encryptionKey_example
            read_only (bool): This Backend does not accept any requests that modify data. Example:
                readOnly_example
            type (str): The type of the Backend. Example: type_example
            messages (list): Oracle GoldenGate messages issued during the request. Example: messages_example
            locked (bool): This Backend does not accept any requests. Example: locked_example
            options (list): Configuration options for the Backend. Example: options_example
            replaced (list): The Backends that this backend replaced. Example: replaced_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                replaced_by=None,
                encrypted=None,
                encryption_key=None,
                read_only=None,
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
        return self._call(
            method="POST",
            template="/services/{version}/installation/configuration/backends",
            data=data,
            body_params={
                "id": id,
                "configuration": configuration,
                "name": name,
                "replacedBy": replaced_by,
                "encrypted": encrypted,
                "encryptionKey": encryption_key,
                "readOnly": read_only,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_configuration_service_backend(
                backend='backend_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/configuration/backends/{backend}",
            path_params={
                "backend": backend,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/configuration/backends/{backend}
    def update_configuration_service_backend(
        self,
        backend,
        patches=None,
        data=None,
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
        return self._call(
            method="PATCH",
            template="/services/{version}/installation/configuration/backends/{backend}",
            path_params={
                "backend": backend,
            },
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_configuration_service_backend(
                backend='backend_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/installation/configuration/backends/{backend}",
            path_params={
                "backend": backend,
            },
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
        replaced_by=None,
        encrypted=None,
        encryption_key=None,
        read_only=None,
        type=None,
        messages=None,
        locked=None,
        options=None,
        replaced=None,
        data=None,
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
            replaced_by (str): The Backend that replaced this backend. Example: replacedBy_example
            encrypted (bool): If true, data is encrypted at rest in the Backend. Example: encrypted_example
            encryption_key (str): The key to use for encrypting data in the Backend; if not specified, a
                random key will be generated. Example: encryptionKey_example
            read_only (bool): This Backend does not accept any requests that modify data. Example:
                readOnly_example
            type (str): The type of the Backend. Example: type_example
            messages (list): Oracle GoldenGate messages issued during the request. Example: messages_example
            locked (bool): This Backend does not accept any requests. Example: locked_example
            options (list): Configuration options for the Backend. Example: options_example
            replaced (list): The Backends that this backend replaced. Example: replaced_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                replaced_by=None,
                encrypted=None,
                encryption_key=None,
                read_only=None,
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
        return self._call(
            method="POST",
            template="/services/{version}/installation/configuration/backends/{backend}/actions/replaces",
            path_params={
                "backend": backend,
            },
            data=data,
            body_params={
                "id": id,
                "configuration": configuration,
                "name": name,
                "replacedBy": replaced_by,
                "encrypted": encrypted,
                "encryptionKey": encryption_key,
                "readOnly": read_only,
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
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Installation
        GET /services/{version}/installation/deployments
        Required Role: User
        Retrieve a list of all Oracle GoldenGate deployments for the installation.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_deployments(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/deployments",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/plugins
    def list_installation_plugins(
        self,
        raw_response=False
    ):
        """
        Service Manager/Plugin Management
        GET /services/{version}/installation/plugins
        Required Role: Administrator
        Retrieve the collection of plugins available to this installation.

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_plugins()

        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/plugins",
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/plugins/{plugin}
    def get_installation_plugin(
        self,
        plugin,
        raw_response=False
    ):
        """
        Service Manager/Plugin Management
        GET /services/{version}/installation/plugins/{plugin}
        Required Role: Administrator
        Retrieve the details for an installation plugin.

        Parameters:
            plugin (str): Name of the plugin. Required. Example: plugin_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_installation_plugin(
                plugin='plugin_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/plugins/{plugin}",
            path_params={
                "plugin": plugin,
            },
            ogg_service="ServiceManager",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/installation/services
    def list_installation_services(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Installation
        GET /services/{version}/installation/services
        Required Role: User
        Retrieve a list of all Oracle GoldenGate services for the installation.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_installation_services(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/installation/services",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs
    def list_logs(
        self,
        ogg_service='',
        raw_response=False
    ):
        """
        Common/Logs
        GET /services/{version}/logs
        Required Role: User
        Retrieve the collection of available logs.

        Parameters:
            ogg_service (str): The service name to use for the request. It is only needed when using a
                reverse proxy. Example: ogg_service_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_logs(
                ogg_service='adminsrvr'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/logs",
            ogg_service=ogg_service,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/logs/events
    def list_log_events(
        self,
        raw_response=False
    ):
        """
        Administration Service/Logs
        GET /services/{version}/logs/events
        Required Role: Administrator
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
            ogg_service="adminsrvr",
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
        Required Role: Administrator
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
        data_exists=None,
        data=None,
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
            data_exists (bool): True if data exists for the application log. Example: dataExists_example
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
                enabled=True,
                data_exists=None
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
                "dataExists": data_exists,
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
        Required Role: Administrator
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
        Administration Service/Master Keys
        GET /services/{version}/masterkey
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey
    def create_master_key_version(
        self,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Administration Service/Master Keys
        POST /services/{version}/masterkey
        Required Role: Administrator
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
            ogg_service="adminsrvr",
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
        Administration Service/Master Keys
        GET /services/{version}/masterkey/{keyVersion}
        Required Role: User
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
            ogg_service="adminsrvr",
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
        Administration Service/Master Keys
        PATCH /services/{version}/masterkey/{keyVersion}
        Required Role: Administrator
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def delete_master_key_version(
        self,
        key_version,
        raw_response=False
    ):
        """
        Administration Service/Master Keys
        DELETE /services/{version}/masterkey/{keyVersion}
        Required Role: Administrator
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/messages
    def list_messages(
        self,
        raw_response=False
    ):
        """
        Administration Service/Messages
        GET /services/{version}/messages
        Required Role: User
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
            ogg_service="adminsrvr",
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
        Required Role: Any
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
        Required Role: Any
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
        Performance Metrics Service/Commands
        GET /services/{version}/monitoring/commands
        Required Role: User
        Retrieve the list of commands

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_monitoring_commands()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/commands",
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/commands/execute
    def execute_monitoring_command(
        self,
        data=None,
        raw_response=False
    ):
        """
        Performance Metrics Service/Commands
        POST /services/{version}/monitoring/commands/execute
        Required Role: Operator
        Execute a command

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/lastMessageId
    def get_last_monitoring_message_id(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Service/Last Message Number
        GET /services/{version}/monitoring/lastMessageId
        Required Role: User
        Retrieve an existing Last message id number

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_last_monitoring_message_id()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/lastMessageId",
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/lastStatusChangeId
    def get_last_status_change_id(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Service/Last Status Change Id Number
        GET /services/{version}/monitoring/lastStatusChangeId
        Required Role: User
        Retrieve an existing Last status change id number

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_last_status_change_id()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/lastStatusChangeId",
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/messages
    def get_monitoring_messages(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Service/Messages
        GET /services/{version}/monitoring/messages
        Required Role: User
        Retrieve an existing Process Messages

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_monitoring_messages()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/messages",
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/statusChanges
    def list_status_changes(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Service/Status Changes
        GET /services/{version}/monitoring/statusChanges
        Required Role: User
        Retrieve an existing Process Status Changes

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_status_changes()

        """
        return self._call(
            method="GET",
            template="/services/{version}/monitoring/statusChanges",
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/{item}/messages
    def list_process_messages(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Messages
        GET /services/{version}/monitoring/{item}/messages
        Required Role: User
        Retrieve an existing Process Messages

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/monitoring/{item}/statusChanges
    def list_process_status_changes(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Status Changes
        GET /services/{version}/monitoring/{item}/statusChanges
        Required Role: User
        Retrieve an existing Process Status Changes

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/processes
    def list_processes(
        self,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/processes
        Required Role: User
        Retrieve an existing Process Information

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_processes()

        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/processes",
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/batchSqlStatistics
    def get_process_batch_sql_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/batchSqlStatistics
        Required Role: User
        Retrieve an existing Integrated Replicat Batch SQL Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brExtantObjectAges
    def get_process_br_extant_object_ages(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brExtantObjectAges
        Required Role: User
        Retrieve an existing Bounded Recovery Extant Object Ages Statistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_extant_object_ages(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/brExtantObjectAges",
            path_params={
                "item": item,
            },
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brExtantObjectSizes
    def get_process_br_extant_object_sizes(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brExtantObjectSizes
        Required Role: User
        Retrieve an existing Bounded Recovery Extant Object Sizes Statistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_extant_object_sizes(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/brExtantObjectSizes",
            path_params={
                "item": item,
            },
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brObjectAges
    def get_process_br_object_ages(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brObjectAges
        Required Role: User
        Retrieve an existing Bounded Recovery Object Ages Statistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_object_ages(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/brObjectAges",
            path_params={
                "item": item,
            },
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brObjectSizes
    def get_process_br_object_sizes(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brObjectSizes
        Required Role: User
        Retrieve an existing Bounded Recovery Object Sizes Statistics

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_object_sizes(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/brObjectSizes",
            path_params={
                "item": item,
            },
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brPoolsInfo
    def get_process_br_pools_info(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brPoolsInfo
        Required Role: User
        Retrieve an existing Bounded Recovery Object Pool Information

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_pools_info(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/brPoolsInfo",
            path_params={
                "item": item,
            },
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/brStatus
    def get_process_br_status(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/brStatus
        Required Role: User
        Retrieve an existing Bounded Recovery Status

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_br_status(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/brStatus",
            path_params={
                "item": item,
            },
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/cacheStatistics
    def get_process_cache_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/cacheStatistics
        Required Role: User
        Retrieve an existing Cache Manager Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/configurationEr
    def get_er_configuration(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/ER Metrics
        GET /services/{version}/mpoints/{item}/configurationEr
        Required Role: User
        Retrieve an existing Basic Configuration Information for Extract and Replicat

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/configurationManager
    def get_manager_configuration(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/ER Metrics
        GET /services/{version}/mpoints/{item}/configurationManager
        Required Role: User
        Retrieve an existing Basic Configuration Information for Manager and Services

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/coordinationReplicat
    def get_process_coordination_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/coordinationReplicat
        Required Role: User
        Retrieve an existing Coordinated Replicat Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/currentInflightTransactions
    def get_current_inflight_transactions(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/currentInflightTransactions
        Required Role: User
        Retrieve an existing In Flight Transaction Information

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/databaseInOut
    def get_process_database_in_out(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/databaseInOut
        Required Role: User
        Retrieve an existing Database Information

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/dependencyStats
    def get_process_dependency_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/dependencyStats
        Required Role: User
        Retrieve an existing Statistics about dependencies

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrChunkStats
    def get_process_distsrvr_chunk_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrChunkStats
        Required Role: User
        Retrieve an existing Distribution Service Chunk Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrNetworkStats
    def get_process_distsrvr_network_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrNetworkStats
        Required Role: User
        Retrieve an existing Distribution Service Network Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrPathStats
    def get_process_distsrvr_path_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrPathStats
        Required Role: User
        Retrieve an existing Distribution Service Path Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrTableStats
    def get_process_distsrvr_table_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/distsrvrTableStats
        Required Role: User
        Retrieve an existing Distribution Service Table Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/heartbeat
    def get_process_heartbeat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Heartbeat Metrics
        GET /services/{version}/mpoints/{item}/heartbeat
        Required Role: User
        Retrieve an existing Heartbeat timings

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_heartbeat(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/heartbeat",
            path_params={
                "item": item,
            },
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/networkStatistics
    def get_process_network_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/networkStatistics
        Required Role: User
        Retrieve an existing Network Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/parallelReplicat
    def get_process_parallel_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/parallelReplicat
        Required Role: User
        Retrieve an existing Parallel Replicat Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrProcStats
    def get_process_pmsrvr_proc_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrProcStats
        Required Role: User
        Retrieve an existing Performance Metrics Service Monitored Process Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrStats
    def get_process_pmsrvr_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrStats
        Required Role: User
        Retrieve an existing Performance Metrics Service Collector Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrWorkerStats
    def get_process_pmsrvr_worker_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/pmsrvrWorkerStats
        Required Role: User
        Retrieve an existing Performance Metrics Service Worker Thread Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/positionEr
    def get_process_position_er(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/ER Metrics
        GET /services/{version}/mpoints/{item}/positionEr
        Required Role: User
        Retrieve an existing Checkpoint Position Information

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/process
    def get_process_info(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/process
        Required Role: User
        Retrieve an existing Process Information

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/processPerformance
    def get_process_performance(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/processPerformance
        Required Role: User
        Retrieve an existing Process Performance Resource Utilization Information

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/queueBucketStatistics
    def get_process_queue_bucket_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/queueBucketStatistics
        Required Role: User
        Retrieve an existing Queue Bucket Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/queueStatistics
    def get_process_queue_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/queueStatistics
        Required Role: User
        Retrieve an existing Queue Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/recvsrvrStats
    def get_process_recvsrvr_stats(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/recvsrvrStats
        Required Role: User
        Retrieve an existing Receiver Service Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/serviceHealth
    def get_process_service_health(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Service Metrics
        GET /services/{version}/mpoints/{item}/serviceHealth
        Required Role: User
        Retrieve an existing Service Health

        Parameters:
            item (str): Required. Example: item_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_process_service_health(
                item='item_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/mpoints/{item}/serviceHealth",
            path_params={
                "item": item,
            },
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsExtract
    def get_process_statistics_extract(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsExtract
        Required Role: User
        Retrieve an existing Extract Database Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsProcedureExtract
    def get_process_statistics_procedure_extract(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsProcedureExtract
        Required Role: User
        Retrieve an existing Extract Database Statistics by Procedure Feature

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsProcedureReplicat
    def get_process_statistics_procedure_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsProcedureReplicat
        Required Role: User
        Retrieve an existing Database Statistics by Procedure Feature

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsReplicat
    def get_process_statistics_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsReplicat
        Required Role: User
        Retrieve an existing Replicat Database Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsTableExtract
    def get_process_statistics_table_extract(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Extract Metrics
        GET /services/{version}/mpoints/{item}/statisticsTableExtract
        Required Role: User
        Retrieve an existing Extract Database Statistics by Table

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsTableReplicat
    def get_process_statistics_table_replicat(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Replicat Metrics
        GET /services/{version}/mpoints/{item}/statisticsTableReplicat
        Required Role: User
        Retrieve an existing Replicat Database Statistics by Table

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/superpoolStatistics
    def get_process_superpool_statistics(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/superpoolStatistics
        Required Role: User
        Retrieve an existing Super Pool Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/threadPerformance
    def get_process_thread_performance(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/threadPerformance
        Required Role: User
        Retrieve an existing Process Thread Resource Utilization Information

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/trailInput
    def get_process_trail_input(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/trailInput
        Required Role: User
        Retrieve an existing Input Trail File Statistics

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
            ogg_service="pmsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/mpoints/{item}/trailOutput
    def get_process_trail_output(
        self,
        item,
        raw_response=False
    ):
        """
        Performance Metrics Service/Process Metrics
        GET /services/{version}/mpoints/{item}/trailOutput
        Required Role: User
        Retrieve an existing Output Trail File Statistics

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
            ogg_service="pmsrvr",
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
        Required Role: Any
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
        Required Role: Any
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
        Administration Service/Parameters
        GET /services/{version}/parameters
        Required Role: Any
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/parameters/{parameter}
    def get_parameter_info(
        self,
        parameter,
        raw_response=False
    ):
        """
        Administration Service/Parameters
        GET /services/{version}/parameters/{parameter}
        Required Role: Any
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats
    def list_replicats(
        self,
        raw_response=False
    ):
        """
        Administration Service/Replicats
        GET /services/{version}/replicats
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def get_replicat(
        self,
        replicat,
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
                encryption_profile=None,
                status=None,
                critical=None,
                managed_process_settings=None,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def delete_replicat(
        self,
        replicat,
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
            ogg_service="adminsrvr",
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
        Administration Service/Replicats
        POST /services/{version}/replicats/{replicat}/command
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info
    def get_replicat_info(
        self,
        replicat,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/checkpoints
    def get_replicat_checkpoint(
        self,
        replicat,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/diagnostics
    def list_replicat_diagnostics(
        self,
        replicat,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_replicat_diagnostics(
                replicat='replicat_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info/diagnostics",
            path_params={
                "replicat": replicat,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/diagnostics/{diagnostic}
    def get_replicat_diagnostic(
        self,
        replicat,
        diagnostic,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_diagnostic(
                replicat='replicat_example',
                diagnostic='diagnostic_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info/diagnostics/{diagnostic}",
            path_params={
                "replicat": replicat,
                "diagnostic": diagnostic,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/history
    def get_replicat_history(
        self,
        replicat,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/logs
    def list_replicat_logs(
        self,
        replicat,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_replicat_logs(
                replicat='replicat_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info/logs",
            path_params={
                "replicat": replicat,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/logs/{log}
    def get_replicat_log(
        self,
        replicat,
        log,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_replicat_log(
                replicat='replicat_example',
                log='log_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/replicats/{replicat}/info/logs/{log}",
            path_params={
                "replicat": replicat,
                "log": log,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/reports
    def list_replicat_reports(
        self,
        replicat,
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
            ogg_service="adminsrvr",
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/status
    def get_replicat_status(
        self,
        replicat,
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
            ogg_service="adminsrvr",
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
        Required Role: Administrator
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
        Required Role: User
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
        Required Role: User
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
        Required Role: User
        Get a list of distribution paths

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
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path

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
        Required Role: Administrator
        Create a new Oracle GoldenGate Distribution Path

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
                        "uri": "trail://sourcehost:9012/services/v2/sources?trail=a1"
                    },
                    "target": {
                        "uri": "wss://targethost:9013/services/v2/targets?trail=t1",
                        "authenticationMethod": {
                            "certificate": "default"
                        }
                    },
                    "begin": {
                        "sequence": "0",
                        "offset": "0"
                    },
                    "status": "running"
                }
            )

            client.create_distribution_path(
                distpath='distpath_example',
                begin={
                    "sequence": "0",
                    "offset": "0"
                },
                name='path1',
                encryption_profile=None,
                status='running',
                target_initiated=None,
                ruleset=None,
                source={
                    "uri": "trail://sourcehost:9012/services/v2/sources?trail=a1"
                },
                target={
                    "uri": "wss://targethost:9013/services/v2/targets?trail=t1",
                    "authenticationMethod": {
                        "certificate": "default"
                    }
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
        Required Role: Operator
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
        Required Role: Administrator
        Delete an existing Oracle GoldenGate Distribution Path

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
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path Checkpoints

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
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path Information

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
        Required Role: User
        Retrieve an existing Oracle GoldenGate Distribution Path Statistics

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

    # Endpoint: /services/{version}/stream
    def list_data_streams(
        self,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/stream
        Required Role: User
        Get a list of data stream resources

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_data_streams()

        """
        return self._call(
            method="GET",
            template="/services/{version}/stream",
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def get_data_stream(
        self,
        stream_name,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/stream/{streamName}
        Required Role: Operator
        Retrieve an existing Oracle GoldenGate Data Stream configuration

        Parameters:
            stream_name (str): Required. Example: streamName_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_data_stream(
                stream_name='streamName_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/stream/{stream_name}",
            path_params={
                "stream_name": stream_name,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def create_data_stream(
        self,
        stream_name,
        tcp_keep_alive_timeout=None,
        quality_of_service=None,
        encoding=None,
        rules=None,
        source=None,
        buffer_size=None,
        cloud_events_format=None,
        description=None,
        data=None,
        raw_response=False,
        if_exists='fail'
    ):
        """
        Distribution Service
        POST /services/{version}/stream/{streamName}
        Required Role: Administrator
        Create a new Oracle GoldenGate Data Stream configuration

        Parameters:
            stream_name (str): Required. Example: streamName_example
            tcp_keep_alive_timeout (int): Timeout (seconds) for keep-alive. Example:
                tcpKeepAliveTimeout_example
            quality_of_service (str): The quality level of the data streaming service. Example:
                qualityOfService_example
            encoding (dict): data encoding method. Example: encoding_example
            rules (list):  Example: rules_example
            source (dict): source endpoint of the data stream. Required if not included in `data`. Example:
                source_example
            buffer_size (int): data buffer size in bytes before flush. Example: bufferSize_example
            cloud_events_format (bool): data records conform to cloudEvents format. Example:
                cloudEventsFormat_example
            description (str): Description for the data stream. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().
            if_exists (str): Action if resource exists: 'fail' (error) or 'skip' (no action). Example:
                if_exists_example

        Example:
            client.create_data_stream(
                stream_name='streamName_example',
                data={
                    "source": "trail://localhost:9012/services/v2/sources?trail=a1",
                    "begin": "now",
                    "$schema": "ogg:dataStream"
                }
            )

            client.create_data_stream(
                stream_name='streamName_example',
                tcp_keep_alive_timeout=None,
                quality_of_service=None,
                encoding=None,
                rules=[
                    {
                        "description": None,
                        "filter": None,
                        "action": None
                    }
                ],
                source='trail://localhost:9012/services/v2/sources?trail=a1',
                buffer_size=None,
                cloud_events_format=None,
                description=None
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/stream/{stream_name}",
            path_params={
                "stream_name": stream_name,
            },
            data=data,
            body_params={
                "tcpKeepAliveTimeout": tcp_keep_alive_timeout,
                "qualityOfService": quality_of_service,
                "encoding": encoding,
                "rules": rules,
                "source": source,
                "bufferSize": buffer_size,
                "cloudEventsFormat": cloud_events_format,
                "description": description,
            },
            ogg_service="distsrvr",
            if_exists=if_exists,
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def update_data_stream(
        self,
        stream_name,
        tcp_keep_alive_timeout=None,
        quality_of_service=None,
        encoding=None,
        rules=None,
        source=None,
        buffer_size=None,
        cloud_events_format=None,
        description=None,
        data=None,
        raw_response=False
    ):
        """
        Distribution Service
        PATCH /services/{version}/stream/{streamName}
        Required Role: Administrator
        Update an existing Oracle GoldenGate Data Stream configuration

        Parameters:
            stream_name (str): Required. Example: streamName_example
            tcp_keep_alive_timeout (int): Timeout (seconds) for keep-alive. Example:
                tcpKeepAliveTimeout_example
            quality_of_service (str): The quality level of the data streaming service. Example:
                qualityOfService_example
            encoding (dict): data encoding method. Example: encoding_example
            rules (list):  Example: rules_example
            source (dict): source endpoint of the data stream. Required if not included in `data`. Example:
                source_example
            buffer_size (int): data buffer size in bytes before flush. Example: bufferSize_example
            cloud_events_format (bool): data records conform to cloudEvents format. Example:
                cloudEventsFormat_example
            description (str): Description for the data stream. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_data_stream(
                stream_name='streamName_example',
                data={
                    "source": "trail://localhost:9012/services/v2/sources?trail=a1",
                    "begin": "earliest",
                    "$schema": "ogg:dataStream"
                }
            )

            client.update_data_stream(
                stream_name='streamName_example',
                tcp_keep_alive_timeout=None,
                quality_of_service=None,
                encoding=None,
                rules=[
                    {
                        "description": None,
                        "filter": None,
                        "action": None
                    }
                ],
                source='trail://localhost:9012/services/v2/sources?trail=a1',
                buffer_size=None,
                cloud_events_format=None,
                description=None
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/stream/{stream_name}",
            path_params={
                "stream_name": stream_name,
            },
            data=data,
            body_params={
                "tcpKeepAliveTimeout": tcp_keep_alive_timeout,
                "qualityOfService": quality_of_service,
                "encoding": encoding,
                "rules": rules,
                "source": source,
                "bufferSize": buffer_size,
                "cloudEventsFormat": cloud_events_format,
                "description": description,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}
    def delete_data_stream(
        self,
        stream_name,
        raw_response=False
    ):
        """
        Distribution Service
        DELETE /services/{version}/stream/{streamName}
        Required Role: Administrator
        Delete an existing Oracle GoldenGate Data Stream configuration

        Parameters:
            stream_name (str): Required. Example: streamName_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_data_stream(
                stream_name='streamName_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/stream/{stream_name}",
            path_params={
                "stream_name": stream_name,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}/info
    def get_data_stream_info(
        self,
        stream_name,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/stream/{streamName}/info
        Required Role: User
        Retrieve an existing Oracle GoldenGate Data Stream Information

        Parameters:
            stream_name (str): Required. Example: streamName_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_data_stream_info(
                stream_name='streamName_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/stream/{stream_name}/info",
            path_params={
                "stream_name": stream_name,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}/info/errors
    def list_data_stream_errors(
        self,
        stream_name,
        raw_response=False
    ):
        """
        Data Stream Service error messages
        GET /services/{version}/stream/{streamName}/info/errors
        Required Role: User
        Retrieve the data stream service error messages if applicable

        Parameters:
            stream_name (str): Required. Example: streamName_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_data_stream_errors(
                stream_name='streamName_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/stream/{stream_name}/info/errors",
            path_params={
                "stream_name": stream_name,
            },
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}/yaml
    def get_data_stream_yaml(
        self,
        stream_name,
        raw_response=False
    ):
        """
        Distribution Service
        GET /services/{version}/stream/{streamName}/yaml
        Required Role: User
        Retrieve the asyncapi yaml specification

        Parameters:
            stream_name (str): Required. Example: streamName_example
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_data_stream_yaml(
                stream_name='streamName_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/stream/{stream_name}/yaml",
            path_params={
                "stream_name": stream_name,
            },
            ogg_service="distsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/stream/{streamName}/yaml
    def update_data_stream_yaml(
        self,
        stream_name,
        data=None,
        raw_response=False
    ):
        """
        Distribution Service
        PATCH /services/{version}/stream/{streamName}/yaml
        Required Role: Administrator
        update the asyncapi yaml specification

        Parameters:
            stream_name (str): Required. Example: streamName_example
            data (dict): Data payload. See call example below for more details.
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.update_data_stream_yaml(
                stream_name='streamName_example',
                data={})
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/stream/{stream_name}/yaml",
            path_params={
                "stream_name": stream_name,
            },
            data=data,
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
        Required Role: User
        Get a list of distribution paths

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
        Required Role: User
        Retrieve an existing Oracle GoldenGate Collector Path

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
        Required Role: Administrator
        Create a new Oracle GoldenGate Collector Path

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
                        "uri": "trail://sourcehost:9012/services/v2/sources?trail=a1"
                    },
                    "target": {
                        "uri": "wss://targethost:9013/services/v2/targets?trail=t1",
                        "authenticationMethod": {
                            "certificate": "default"
                        }
                    },
                    "begin": {
                        "sequence": "0",
                        "offset": "0"
                    },
                    "status": "running"
                }
            )

            client.create_receiver_path(
                path='path_example',
                begin={
                    "sequence": "0",
                    "offset": "0"
                },
                name='path1',
                encryption_profile=None,
                status='running',
                target_initiated=None,
                ruleset=None,
                source={
                    "uri": "trail://sourcehost:9012/services/v2/sources?trail=a1"
                },
                target={
                    "uri": "wss://targethost:9013/services/v2/targets?trail=t1",
                    "authenticationMethod": {
                        "certificate": "default"
                    }
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
        Required Role: Operator
        Update an existing Oracle GoldenGate Collector Path

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
        Required Role: Administrator
        Delete an existing Oracle GoldenGate Collector Path

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
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Path Checkpoints

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
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Path Information

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
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Progress

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
        Required Role: User
        Retrieve an existing Oracle GoldenGate Receiver Service Path Stats

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
        Administration Service/Tasks
        GET /services/{version}/tasks
        Required Role: User
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def get_task(
        self,
        task,
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
            ogg_service="adminsrvr",
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
        Administration Service/Tasks
        POST /services/{version}/tasks/{task}
        Required Role: Administrator
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
            ogg_service="adminsrvr",
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
        Administration Service/Tasks
        PATCH /services/{version}/tasks/{task}
        Required Role: Administrator
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}
    def delete_task(
        self,
        task,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}/info
    def list_task_info_types(
        self,
        task,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}/info/history
    def get_task_history(
        self,
        task,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/tasks/{task}/info/status
    def get_task_status(
        self,
        task,
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
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails
    def list_trails(
        self,
        raw_response=False
    ):
        """
        Administration Service/Trails
        GET /services/{version}/trails
        Required Role: User
        Retrieve a collection of all known trails

        Parameters:
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_trails()

        """
        return self._call(
            method="GET",
            template="/services/{version}/trails",
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}
    def get_trail(
        self,
        trail,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_trail(
                trail='trail_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/trails/{trail}",
            path_params={
                "trail": trail,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}
    def create_trail(
        self,
        trail,
        space_used=None,
        size_mb=None,
        offset=None,
        sequence_max_in_use=None,
        trail_name=None,
        path=None,
        remote=None,
        sequence_last_archived=None,
        name=None,
        sequence=None,
        sequence_min_in_use=None,
        sequence_length=None,
        sequence_min=None,
        sequence_length_flip=None,
        process_ref=None,
        sequence_max=None,
        description=None,
        data=None,
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
            space_used (int): Bytes consumed by all trail sequences. Example: spaceUsed_example
            size_mb (int): The maximum size, in megabytes, of a file in the trail. Example: sizeMB_example
            offset (int): Offset in trail sequence file. Example: offset_example
            sequence_max_in_use (int): Maximum trail sequence number in use. Example:
                sequenceMaxInUse_example
            trail_name (str): The optional 'user-friendly' name for the trail. Example: trailName_example
            path (str): The path where trail data is stored. Example: path_example
            remote (bool): Indicates if trail is local or remote. Example: remote_example
            sequence_last_archived (list): Last sequence number archived (Managed Trails only). Example:
                sequenceLastArchived_example
            name (str): The two-character name of the trail. Example: name_example
            sequence (int): Trail beginning sequence number. Example: sequence_example
            sequence_min_in_use (int): Minimum trail sequence number in use. Example:
                sequenceMinInUse_example
            sequence_length (str): Number of digits in sequence file name. Example: sequenceLength_example
            sequence_min (int): Minimum trail sequence number that exists in the deployment. Example:
                sequenceMin_example
            sequence_length_flip (bool): Indicates sequence number length will change. Example:
                sequenceLengthFlip_example
            process_ref (list): List of all processes associated with this trail. Example:
                processRef_example
            sequence_max (int): Maximum trail sequence number that exists in the deployment. Example:
                sequenceMax_example
            description (str): Description for the trail. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                    "sizeMB": 2000
                }
            )

            client.create_trail(
                trail='trail_example',
                space_used=None,
                size_mb=2000,
                offset=None,
                sequence_max_in_use=None,
                trail_name='HumanResources',
                path='north',
                remote=None,
                sequence_last_archived=[
                    {
                        "taskName": None,
                        "archiveTarget": None,
                        "sequence": None
                    }
                ],
                name='ea',
                sequence=None,
                sequence_min_in_use=None,
                sequence_length=None,
                sequence_min=None,
                sequence_length_flip=None,
                process_ref=[
                    {
                        "processType": None,
                        "processName": None
                    }
                ],
                sequence_max=None,
                description=None
            )
        """
        return self._call(
            method="POST",
            template="/services/{version}/trails/{trail}",
            path_params={
                "trail": trail,
            },
            data=data,
            body_params={
                "spaceUsed": space_used,
                "sizeMB": size_mb,
                "offset": offset,
                "sequenceMaxInUse": sequence_max_in_use,
                "trailName": trail_name,
                "path": path,
                "remote": remote,
                "sequenceLastArchived": sequence_last_archived,
                "name": name,
                "sequence": sequence,
                "sequenceMinInUse": sequence_min_in_use,
                "sequenceLength": sequence_length,
                "sequenceMin": sequence_min,
                "sequenceLengthFlip": sequence_length_flip,
                "processRef": process_ref,
                "sequenceMax": sequence_max,
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
        space_used=None,
        size_mb=None,
        offset=None,
        sequence_max_in_use=None,
        trail_name=None,
        path=None,
        remote=None,
        sequence_last_archived=None,
        name=None,
        sequence=None,
        sequence_min_in_use=None,
        sequence_length=None,
        sequence_min=None,
        sequence_length_flip=None,
        process_ref=None,
        sequence_max=None,
        description=None,
        data=None,
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
            space_used (int): Bytes consumed by all trail sequences. Example: spaceUsed_example
            size_mb (int): The maximum size, in megabytes, of a file in the trail. Example: sizeMB_example
            offset (int): Offset in trail sequence file. Example: offset_example
            sequence_max_in_use (int): Maximum trail sequence number in use. Example:
                sequenceMaxInUse_example
            trail_name (str): The optional 'user-friendly' name for the trail. Example: trailName_example
            path (str): The path where trail data is stored. Example: path_example
            remote (bool): Indicates if trail is local or remote. Example: remote_example
            sequence_last_archived (list): Last sequence number archived (Managed Trails only). Example:
                sequenceLastArchived_example
            name (str): The two-character name of the trail. Example: name_example
            sequence (int): Trail beginning sequence number. Example: sequence_example
            sequence_min_in_use (int): Minimum trail sequence number in use. Example:
                sequenceMinInUse_example
            sequence_length (str): Number of digits in sequence file name. Example: sequenceLength_example
            sequence_min (int): Minimum trail sequence number that exists in the deployment. Example:
                sequenceMin_example
            sequence_length_flip (bool): Indicates sequence number length will change. Example:
                sequenceLengthFlip_example
            process_ref (list): List of all processes associated with this trail. Example:
                processRef_example
            sequence_max (int): Maximum trail sequence number that exists in the deployment. Example:
                sequenceMax_example
            description (str): Description for the trail. Example: description_example
            data (dict): Override body payload with a raw dict. Individual parameters are merged into this
                dict when provided.
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
                space_used=None,
                size_mb=None,
                offset=None,
                sequence_max_in_use=None,
                trail_name=None,
                path=None,
                remote=None,
                sequence_last_archived=[
                    {
                        "taskName": None,
                        "archiveTarget": None,
                        "sequence": None
                    }
                ],
                name=None,
                sequence=None,
                sequence_min_in_use=None,
                sequence_length=None,
                sequence_min=None,
                sequence_length_flip=None,
                process_ref=[
                    {
                        "processType": None,
                        "processName": None
                    }
                ],
                sequence_max=None,
                description='Trail for employee tables from Human Resources'
            )
        """
        return self._call(
            method="PATCH",
            template="/services/{version}/trails/{trail}",
            path_params={
                "trail": trail,
            },
            data=data,
            body_params={
                "spaceUsed": space_used,
                "sizeMB": size_mb,
                "offset": offset,
                "sequenceMaxInUse": sequence_max_in_use,
                "trailName": trail_name,
                "path": path,
                "remote": remote,
                "sequenceLastArchived": sequence_last_archived,
                "name": name,
                "sequence": sequence,
                "sequenceMinInUse": sequence_min_in_use,
                "sequenceLength": sequence_length,
                "sequenceMin": sequence_min,
                "sequenceLengthFlip": sequence_length_flip,
                "processRef": process_ref,
                "sequenceMax": sequence_max,
                "description": description,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}
    def delete_trail(
        self,
        trail,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_trail(
                trail='trail_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/trails/{trail}",
            path_params={
                "trail": trail,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences
    def list_trail_sequences(
        self,
        trail,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.list_trail_sequences(
                trail='trail_example'
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/trails/{trail}/sequences",
            path_params={
                "trail": trail,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences
    def delete_trail_sequence_collection(
        self,
        trail,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_trail_sequence_collection(
                trail='trail_example'
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/trails/{trail}/sequences",
            path_params={
                "trail": trail,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences/{sequence}
    def get_trail_sequence(
        self,
        trail,
        sequence,
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.get_trail_sequence(
                trail='trail_example',
                sequence=1
            )
        """
        return self._call(
            method="GET",
            template="/services/{version}/trails/{trail}/sequences/{sequence}",
            path_params={
                "trail": trail,
                "sequence": sequence,
            },
            ogg_service="adminsrvr",
            raw_response=raw_response
        )

    # Endpoint: /services/{version}/trails/{trail}/sequences/{sequence}
    def create_trail_sequence(
        self,
        trail,
        sequence,
        data=None,
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
        return self._call(
            method="POST",
            template="/services/{version}/trails/{trail}/sequences/{sequence}",
            path_params={
                "trail": trail,
                "sequence": sequence,
            },
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
            raw_response (bool): If True, return raw parsed response from _parse() instead of
                _extract_main().

        Example:
            client.delete_trail_sequence(
                trail='trail_example',
                sequence=1
            )
        """
        return self._call(
            method="DELETE",
            template="/services/{version}/trails/{trail}/sequences/{sequence}",
            path_params={
                "trail": trail,
                "sequence": sequence,
            },
            ogg_service="adminsrvr",
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
        only_if_running=False
    ):
        """Restart an extract by updating its status to restart

        Args:
            extract (str): Name of the extract to restart.
            only_if_running (bool, optional): If True, only restart the extract if it is currently running.
                Defaults to False.
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
        only_if_running=False
    ):
        """Restart all extracts by updating their status to restart

        Args:
            only_if_running (bool, optional): If True, only restart extracts that are currently running.
                Defaults to False.
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
        only_if_running=False
    ):
        """Restart a replicat by updating its status to restart

        Args:
            replicat (str): Name of the replicat to restart.
            only_if_running (bool, optional): If True, only restart the replicat if it is currently running.
                Defaults to False.
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
        only_if_running=False
    ):
        """Restart all replicats by updating their status to restart

        Args:
            only_if_running (bool, optional): If True, only restart replicats that are currently running.
                Defaults to False.
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
        only_if_running=False,
        raw_response=False
    ):
        """Restart a service by updating its status to restart

        Args:
            deployment (str): Name of the deployment owning the service.
            service (str): Name of the service to restart.
            only_if_running (bool, optional): If True, only restart the service if it is currently running.
                Defaults to False.
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
                    f"Skipping restart of service '{service}' in deployment '{deployment}' "
                    f"because it is not running (status={service_status})."
                )
                return

        return self.update_service(
            deployment=deployment,
            service=service,
            data={'status': 'restart'},
            raw_response=raw_response
        )

    def _wait_until_resource_running(
        self,
        fetch_fn,
        resource_type,
        resource_name,
        sleep_seconds=5,
        max_retries=10,
    ):
        """Wait until a resource reports status 'running' by repeatedly calling the provided fetch function.

        Args:
            fetch_fn (function): Method that fetches the resource and returns its details as a dict.
                The dict must contain a 'status' key.
            resource_type (str): Type of the resource to wait for.
            resource_name (str): Name of the resource to wait for.
            sleep_seconds (int, optional): Number of seconds to sleep between retries. Defaults to 5.
            max_retries (int, optional): Maximum number of retries. Defaults to 10.

        Raises:
            RuntimeError: If the resource does not become running after the maximum number of retries.

        Returns:
            dict: The resource if it becomes running, otherwise raises an error.
        """
        for attempt in range(1, max_retries + 1):
            try:
                resource = fetch_fn()
                status = resource.get("status")
                if status == "running":
                    print(
                        f"{resource_type.capitalize()} '{resource_name}' is running. Continuing..."
                    )
                    return resource

                print(
                    f"{resource_type.capitalize()} '{resource_name}' status is '{status}' "
                    f"(attempt {attempt}/{max_retries}). Retrying in {sleep_seconds}s..."
                )
            except Exception as exc:
                print(
                    f"Error fetching {resource_type} '{resource_name}': {exc}. "
                    f"Retrying in {sleep_seconds}s... (attempt {attempt}/{max_retries})"
                )

            if attempt < max_retries:
                time.sleep(sleep_seconds)

        raise RuntimeError(
            f"{resource_type.capitalize()} '{resource_name}' did not become running after "
            f"{max_retries} retries."
        )

    def wait_until_deployment_running(
        self,
        deployment,
        sleep_seconds=5,
        max_retries=10,
    ):
        """Wait until a deployment reports status 'running'.

        Args:
            deployment (str): Name of the deployment to wait for.
            sleep_seconds (int, optional): Number of seconds to sleep between retries. Defaults to 5.
            max_retries (int, optional): Maximum number of retries. Defaults to 10.

        Returns:
            dict: The deployment resource if it becomes running, otherwise raises an error.
        """
        return self._wait_until_resource_running(
            lambda: self.get_deployment(deployment),
            "deployment",
            deployment,
            sleep_seconds,
            max_retries,
        )

    def wait_until_extract_running(
        self,
        extract,
        sleep_seconds=5,
        max_retries=10,
    ):
        """Wait until an extract reports status 'running'.

        Args:
            extract (str): Name of the extract to wait for.
            sleep_seconds (int, optional): Number of seconds to sleep between retries. Defaults to 5.
            max_retries (int, optional): Maximum number of retries. Defaults to 10.

        Returns:
            dict: The extract resource if it becomes running, otherwise raises an error.
        """
        return self._wait_until_resource_running(
            lambda: self.get_extract(extract),
            "extract",
            extract,
            sleep_seconds,
            max_retries,
        )

    def wait_until_replicat_running(
        self,
        replicat,
        sleep_seconds=5,
        max_retries=10,
    ):
        """Wait until a replicat reports status 'running'.

        Args:
            replicat (str): Name of the replicat to wait for.
            sleep_seconds (int, optional): Number of seconds to sleep between retries. Defaults to 5.
            max_retries (int, optional): Maximum number of retries. Defaults to 10.

        Returns:
            dict: The replicat resource if it becomes running, otherwise raises an error.
        """
        return self._wait_until_resource_running(
            lambda: self.get_replicat(replicat),
            "replicat",
            replicat,
            sleep_seconds,
            max_retries,
        )

    def wait_until_service_running(
        self,
        deployment,
        service,
        sleep_seconds=5,
        max_retries=10,
    ):
        """Wait until a service reports status 'running'.

        Args:
            deployment (str): Name of the deployment owning the service.
            service (str): Name of the service to wait for.
            sleep_seconds (int, optional): Number of seconds to sleep between retries. Defaults to 5.
            max_retries (int, optional): Maximum number of retries. Defaults to 10.

        Returns:
            dict: The service resource if it becomes running, otherwise raises an error.
        """

        return self._wait_until_resource_running(
            lambda: self.get_service(deployment, service),
            "service",
            f"{deployment}/{service}",
            sleep_seconds,
            max_retries,
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

            self.wait_until_deployment_running(
                deployment,
                sleep_seconds=5,
                max_retries=10,
            )

            # For the Service Manager deployment, we restart all services except the Service Manager service itself.
            # The reason is that the services like the AIService do not pick up the new home automatically.
            if deployment == "ServiceManager":
                services = self.list_services("ServiceManager")
                for service in services:
                    service_name = service.get("name")
                    if service_name == "ServiceManager":
                        continue

                    service_info = self.wait_until_service_running(
                        "ServiceManager",
                        service_name,
                        sleep_seconds=5,
                        max_retries=10,
                    )
                    service_status = service_info.get("status")
                    if service_status != "running":
                        print(
                            f"Skipping restart of service '{service_name}' in deployment 'ServiceManager'"
                            f" because it is not running (status={service_status})."
                        )
                        continue
                    else:
                        print(f"Restarting service '{service_name}' in deployment 'ServiceManager'...")
                        self.restart_service(
                            deployment="ServiceManager",
                            service=service_name,
                            only_if_running=False
                        )

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
                    self.restart_extract(
                        extract=extract.get("name"),
                        only_if_running=True
                    )

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
                    self.restart_replicat(
                        replicat=replicat.get("name"),
                        only_if_running=True
                    )

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
