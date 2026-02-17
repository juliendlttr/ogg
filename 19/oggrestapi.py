#!/usr/bin/env python3
"""
Oracle GoldenGate REST API Client
Author: Julien DELATTRE
"""

import requests
import urllib3
from pprint import pprint


class OGGRestAPI:
    def __init__(self, url, username=None, password=None, ca_cert=None, verify_ssl=True,
                 test_connection=True, timeout=None):
        """
        Initialize Oracle GoldenGate REST API client.

        :param url: Base URL of the OGG REST API. It can be:
                    'http(s)://hostname:port' without NGINX reverse proxy,
                    'https://nginx_host:nginx_port' with NGINX reverse proxy.
        :param username: service username
        :param password: service password
        :param ca_cert: path to a trusted CA cert (for self-signed certs)
        :param verify_ssl: bool, whether to verify SSL certs
        :param test_connection: if True, will attempt to retrieve API versions on init
        :param timeout: request timeout in seconds
        """
        self.base_url = url
        self.username = username
        self.swagger_version = '2023.12.12'
        self.auth = (self.username, password)
        self.headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
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

    def _request(self, method, path, *, params=None, data=None, extract=True):
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
        self._check_response(response)
        result = self._parse(response)
        return self._extract_main(result) if extract else result

    def _build_path(self, template, path_params=None):
        path_params = path_params or {}
        return template.format(**path_params)

    def _call(self, method, template, *, path_params=None, params=None, data=None, extract=True):
        path = self._build_path(template, path_params=path_params)
        result = self._request(method, path, params=params, data=data, extract=False)
        if extract:
            return self._extract_main(result)
        return result

    def _get(self, path, params=None, extract=True):
        return self._request('GET', path, params=params, extract=extract)

    def _post(self, path, data=None, extract=True):
        return self._request('POST', path, data=data, extract=extract)

    def _put(self, path, data=None, extract=True):
        return self._request('PUT', path, data=data, extract=extract)

    def _patch(self, path, data=None, extract=True):
        return self._request('PATCH', path, data=data, extract=extract)

    def _delete(self, path, extract=True):
        return self._request('DELETE', path, extract=extract)

    def _check_response(self, response):
        if not response.ok:
            if 'messages' in response.json():
                messages = response.json().get('messages', [])
                raise Exception(
                    ' ; '.join([f"{message['severity']}: {message['title']}" for message in messages])
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
    def retrieve_api_versions(self):
        """
        GET /services
        Each Oracle GoldenGate service exposes one or more versions of the REST API for backward compatibility.
            Retrieve the collection of available API versions using this endpoint.

        Example:
            client.retrieve_api_versions()

        """
        return self._call("GET", "/services")

    # Endpoint: /services/{version}
    def describe_api_version(self, version='v2'):
        """
        GET /services/{version}
        Use this endpoint to obtain details of a specific version of an Oracle GoldenGate Service REST API.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.describe_api_version()

        """
        return self._call(
            "GET",
            "/services/{version}",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/authorizations
    def list_user_roles(self, version='v2'):
        """
        GET /services/{version}/authorizations
        Get the collection of roles in this deployment.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_user_roles()

        """
        return self._call(
            "GET",
            "/services/{version}/authorizations",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/authorizations/{role}
    def list_users(self, role, version='v2'):
        """
        GET /services/{version}/authorizations/{role}
        Get the collection of Authorized Users associated with the Authorization Role.

        Parameters:
            role (string): Authorization Role Resource Name Example: User
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_users(
                role='User'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/authorizations/{role}",
            path_params={"role": role, "version": version},
        )

    # Endpoint: /services/{version}/authorizations/{role}
    def bulk_create_users_for_role(self, role, data=None, version='v2'):
        """
        POST /services/{version}/authorizations/{role}
        Create multiple users associated with the given role.

        Parameters:
            role (string): Authorization Role Resource Name Example: User
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.bulk_create_users_for_role(
                role='User',
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
                })
        """
        return self._call(
            "POST",
            "/services/{version}/authorizations/{role}",
            path_params={"role": role, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def retrieve_user(self, user, role, version='v2'):
        """
        GET /services/{version}/authorizations/{role}/{user}
        Get Authorization User Resource information.

        Parameters:
            user (string): User Resource Name Example: user_example
            role (string): Authorization Role Resource Name Example: User
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_user(
                user='user_example',
                role='User'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/authorizations/{role}/{user}",
            path_params={"user": user, "role": role, "version": version},
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def create_user(self, user, role, data=None, version='v2'):
        """
        POST /services/{version}/authorizations/{role}/{user}
        Create a new Authorization User Resource.

        Parameters:
            user (string): User Resource Name Example: user_example
            role (string): Authorization Role Resource Name Example: User
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_user(
                user='user_example',
                role='User',
                data={
                    "credential": "password-A1z",
                    "info": "Example user #3"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/authorizations/{role}/{user}",
            path_params={"user": user, "role": role, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def update_user(self, user, role, data=None, version='v2'):
        """
        PATCH /services/{version}/authorizations/{role}/{user}
        Update an existing Authorization User Resource.

        Parameters:
            user (string): User Resource Name Example: user_example
            role (string): Authorization Role Resource Name Example: User
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_user(
                user='user_example',
                role='User',
                data={
                    "credential": "NewPassword-Z1a"
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/authorizations/{role}/{user}",
            path_params={"user": user, "role": role, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/authorizations/{role}/{user}
    def delete_user(self, user, role, version='v2'):
        """
        DELETE /services/{version}/authorizations/{role}/{user}
        Delete an existing Authorization user role. To completely remove a user from the deployment, use a value
            of "all" for {role}.

        Parameters:
            user (string): User Resource Name Example: user_example
            role (string): Authorization Role Resource Name Example: User
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_user(
                user='user_example',
                role='User'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/authorizations/{role}/{user}",
            path_params={"user": user, "role": role, "version": version},
        )

    # Endpoint: /services/{version}/commands/execute
    def execute_command(self, data=None, version='v2'):
        """
        POST /services/{version}/commands/execute
        Execute a command. Reporting commands are accessible for users with the 'User' role. Other commands
            require the 'Operator' role.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

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
                })
        """
        return self._call(
            "POST",
            "/services/{version}/commands/execute",
            path_params={"version": version},
            data=data,
        )

    # Endpoint: /services/{version}/config/files
    def list_configuration_files(self, version='v2'):
        """
        GET /services/{version}/config/files
        Retrieve the collection of configuration files.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_configuration_files()

        """
        return self._call(
            "GET",
            "/services/{version}/config/files",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/config/files/{file}
    def retrieve_configuration_file(self, file, version='v2'):
        """
        GET /services/{version}/config/files/{file}
        Retrieve the contents of a configuration file.

        Parameters:
            file (string): The name of a configuration file. Example: file_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_configuration_file(
                file='file_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/config/files/{file}",
            path_params={"file": file, "version": version},
        )

    # Endpoint: /services/{version}/config/files/{file}
    def create_configuration_file(self, file, data=None, version='v2'):
        """
        POST /services/{version}/config/files/{file}
        Create a new configuration file.

        Parameters:
            file (string): The name of a configuration file. Example: file_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_configuration_file(
                file='file_example',
                data={
                    "lines": [
                        "UseridAlias oggadmin",
                        "ReportCount Every 1000 Records"
                    ]
                })
        """
        return self._call(
            "POST",
            "/services/{version}/config/files/{file}",
            path_params={"file": file, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/config/files/{file}
    def replace_configuration_file(self, file, data=None, version='v2'):
        """
        PUT /services/{version}/config/files/{file}
        Modify an existing configuration file.

        Parameters:
            file (string): The name of a configuration file. Example: file_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.replace_configuration_file(
                file='file_example',
                data={
                    "lines": [
                        "UseridAlias oggadmin",
                        "ReportCount Every 100000 Records"
                    ]
                })
        """
        return self._call(
            "PUT",
            "/services/{version}/config/files/{file}",
            path_params={"file": file, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/config/files/{file}
    def delete_configuration_file(self, file, version='v2'):
        """
        DELETE /services/{version}/config/files/{file}
        Delete a configuration file.

        Parameters:
            file (string): The name of a configuration file. Example: file_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_configuration_file(
                file='file_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/config/files/{file}",
            path_params={"file": file, "version": version},
        )

    # Endpoint: /services/{version}/config/health
    def service_health_details(self, version='v2'):
        """
        GET /services/{version}/config/health
        Retrieve detailed information for the service health.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.service_health_details()

        """
        return self._call(
            "GET",
            "/services/{version}/config/health",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/config/health/check
    def service_health_summary(self, version='v2'):
        """
        GET /services/{version}/config/health/check
        Retrieve summary information for the service health.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.service_health_summary()

        """
        return self._call(
            "GET",
            "/services/{version}/config/health/check",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/config/summary
    def service_configuration_summary(self, version='v2'):
        """
        GET /services/{version}/config/summary
        Retrieve summary information for the service.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.service_configuration_summary()

        """
        return self._call(
            "GET",
            "/services/{version}/config/summary",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/config/types
    def list_configuration_data_types(self, version='v2'):
        """
        GET /services/{version}/config/types
        Retrieve the collection of configuration variable data types.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_configuration_data_types()

        """
        return self._call(
            "GET",
            "/services/{version}/config/types",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/config/types/{type}
    def retrieve_configuration_data_type(self, version='v2'):
        """
        GET /services/{version}/config/types/{type}
        Retrieve a configuration data type.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_configuration_data_type()

        """
        return self._call(
            "GET",
            "/services/{version}/config/types/{type}",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/config/types/{type}
    def create_configuration_data_type(self, data=None, version='v2'):
        """
        POST /services/{version}/config/types/{type}
        Create a new configuration data type.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_configuration_data_type(
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
                    "additionalProperties": false
                })
        """
        return self._call(
            "POST",
            "/services/{version}/config/types/{type}",
            path_params={"version": version},
            data=data,
        )

    # Endpoint: /services/{version}/config/types/{type}
    def delete_configuration_data_type(self, version='v2'):
        """
        DELETE /services/{version}/config/types/{type}
        Delete a configuration data type.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_configuration_data_type()

        """
        return self._call(
            "DELETE",
            "/services/{version}/config/types/{type}",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/config/types/{type}/values
    def list_configuration_values(self, version='v2'):
        """
        GET /services/{version}/config/types/{type}/values
        Retrieve the collection of names of the configuration values for a data type.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_configuration_values()

        """
        return self._call(
            "GET",
            "/services/{version}/config/types/{type}/values",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def retrieve_configuration_value(self, value, version='v2'):
        """
        GET /services/{version}/config/types/{type}/values/{value}
        Retrieve a configuration value.

        Parameters:
            value (string): Value name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_', ':' or '-'. Example: value_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_configuration_value(
                value='value_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params={"value": value, "version": version},
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def create_configuration_value(self, value, data=None, version='v2'):
        """
        POST /services/{version}/config/types/{type}/values/{value}
        Create a new configuration value.

        Parameters:
            value (string): Value name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_', ':' or '-'. Example: value_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_configuration_value(
                value='value_example',
                data={
                    "$schema": "custom:config",
                    "lines": [
                        "--",
                        "--  Example Configuration Data",
                        "--"
                    ]
                })
        """
        return self._call(
            "POST",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params={"value": value, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def replace_configuration_value(self, value, data=None, version='v2'):
        """
        PUT /services/{version}/config/types/{type}/values/{value}
        Replace an existing configuration value.

        Parameters:
            value (string): Value name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_', ':' or '-'. Example: value_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.replace_configuration_value(
                value='value_example',
                data={
                    "$schema": "custom:config",
                    "lines": [
                        "--",
                        "--  Example Configuration Data",
                        "--",
                        "Include core.inc"
                    ]
                })
        """
        return self._call(
            "PUT",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params={"value": value, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/config/types/{type}/values/{value}
    def delete_configuration_value(self, value, version='v2'):
        """
        DELETE /services/{version}/config/types/{type}/values/{value}
        Delete a configuration value.

        Parameters:
            value (string): Value name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_', ':' or '-'. Example: value_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_configuration_value(
                value='value_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/config/types/{type}/values/{value}",
            path_params={"value": value, "version": version},
        )

    # Endpoint: /services/{version}/connections
    def list_connections(self, version='v2'):
        """
        GET /services/{version}/connections
        Retrieve the list of known database connections. For each item in the credential store, a database
            connection of the form 'domain.alias' is created.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_connections()

        """
        return self._call(
            "GET",
            "/services/{version}/connections",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}
    def delete_connection(self, connection, version='v2'):
        """
        DELETE /services/{version}/connections/{connection}
        Remove a database connection.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_connection(
                connection='MYCONN'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/connections/{connection}",
            path_params={"connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}
    def replace_connection(self, connection, data=None, version='v2'):
        """
        PUT /services/{version}/connections/{connection}
        Update a database connection. Connections created for aliases in the credential store cannot be updated.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.replace_connection(
                connection='MYCONN',
                data={
                    "credentials": {
                        "alias": "oggadmin"
                    }
                })
        """
        return self._call(
            "PUT",
            "/services/{version}/connections/{connection}",
            path_params={"connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/connections/{connection}
    def create_connection(self, connection, data=None, version='v2'):
        """
        POST /services/{version}/connections/{connection}
        Create a new database connection. Connections are automatically created for aliases in the credential
            store.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_connection(
                connection='MYCONN',
                data={
                    "credentials": {
                        "domain": "OracleGoldenGate",
                        "alias": "oggadmin"
                    }
                })
        """
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}",
            path_params={"connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/connections/{connection}
    def retrieve_connection(self, connection, version='v2'):
        """
        GET /services/{version}/connections/{connection}
        Retrieve the database connection details.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_connection(
                connection='MYCONN'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}",
            path_params={"connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/activeTransactions
    def retrieve_active_transaction_details(self, connection, version='v2'):
        """
        GET /services/{version}/connections/{connection}/activeTransactions
        Retrieve details of the active transactions for a database connection.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_active_transaction_details(
                connection='MYCONN'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/activeTransactions",
            path_params={"connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/databases
    def retrieve_database_names(self, connection, version='v2'):
        """
        GET /services/{version}/connections/{connection}/databases
        Retrieve names of databases.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            name (string): Database name filter, including wildcard characters '*' or '?'. Example:
                name_example

        Example:
            client.retrieve_database_names(
                connection='MYCONN'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/databases",
            path_params={"connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}
    def retrieve_database_schemas(self, database, connection, version='v2'):
        """
        GET /services/{version}/connections/{connection}/databases/{database}
        Retrieve names of schemas in the database.

        Parameters:
            database (string): Database name. Example: database_example
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            name (string): Schema name filter, including wildcard characters '*' or '?'. Example:
                name_example

        Example:
            client.retrieve_database_schemas(
                database='database_example',
                connection='MYCONN'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/databases/{database}",
            path_params={"database": database, "connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}/{schema}
    def retrieve_database_tables(self, schema, database, connection, version='v2'):
        """
        GET /services/{version}/connections/{connection}/databases/{database}/{schema}
        Retrieve names of tables in the schema.

        Parameters:
            schema (string): Schema name in the database. Example: schema_example
            database (string): Database name. Example: database_example
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            name (string): Table name filter, including wildcard characters '*' or '?'. Example:
                name_example

        Example:
            client.retrieve_database_tables(
                schema='schema_example',
                database='database_example',
                connection='MYCONN'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/databases/{database}/{schema}",
            path_params={"schema": schema, "database": database, "connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}
    def retrieve_database_table_details(self, table, schema, database, connection, version='v2'):
        """
        GET /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}
        Retrieve details for a table in the schema.

        Parameters:
            table (string): Table name in the database. Example: table_example
            schema (string): Schema name in the database. Example: schema_example
            database (string): Database name. Example: database_example
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_database_table_details(
                table='table_example',
                schema='schema_example',
                database='database_example',
                connection='MYCONN'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/databases/{database}/{schema}/{table}",
            path_params={"table": table, "schema": schema, "database": database, "connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}/instantiationCsn
    def manage_instantiation_csn(self, table, schema, database, connection, data=None, version='v2'):
        """
        POST /services/{version}/connections/{connection}/databases/{database}/{schema}/{table}/instantiationCsn
        Manage the instantiation CSN for filtering.

        Parameters:
            table (string): Table name in the database. Example: table_example
            schema (string): Schema name in the database. Example: schema_example
            database (string): Database name. Example: database_example
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.manage_instantiation_csn(
                table='table_example',
                schema='schema_example',
                database='database_example',
                connection='MYCONN',
                data={
                    "command": "clear",
                    "source": "source.table"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/databases/{database}/{schema}/{table}/instantiationCsn",
            path_params={"table": table, "schema": schema, "database": database, "connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/checkpoint
    def manage_checkpoint_tables(self, connection, data=None, version='v2'):
        """
        POST /services/{version}/connections/{connection}/tables/checkpoint
        Manage Oracle GoldenGate Checkpoint table

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.manage_checkpoint_tables(
                connection='MYCONN',
                data={
                    "operation": "add",
                    "name": "oggadmin.checkpoints"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/tables/checkpoint",
            path_params={"connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def retrieve_heartbeat_table(self, connection, version='v2'):
        """
        GET /services/{version}/connections/{connection}/tables/heartbeat
        Retrieve details of the heartbeat table for a database connection.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_heartbeat_table(
                connection='MYCONN'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/tables/heartbeat",
            path_params={"connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def create_heartbeat_table(self, connection, data=None, version='v2'):
        """
        POST /services/{version}/connections/{connection}/tables/heartbeat
        Create the heartbeat table for a database connection.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_heartbeat_table(
                connection='MYCONN',
                data={
                    "frequency": 60
                })
        """
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/tables/heartbeat",
            path_params={"connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def update_heartbeat_table(self, connection, data=None, version='v2'):
        """
        PATCH /services/{version}/connections/{connection}/tables/heartbeat
        Modify the heartbeat table parameters for a database connection.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_heartbeat_table(
                connection='MYCONN',
                data={
                    "purgeFrequency": 2
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/connections/{connection}/tables/heartbeat",
            path_params={"connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat
    def delete_heartbeat_table(self, connection, version='v2'):
        """
        DELETE /services/{version}/connections/{connection}/tables/heartbeat
        Remove heartbeat resources from a database.

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_heartbeat_table(
                connection='MYCONN'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/connections/{connection}/tables/heartbeat",
            path_params={"connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat/{process}
    def retrieve_process_heartbeat_records(self, process, connection, version='v2'):
        """
        GET /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Retrieve heartbeat table entries for an extract or replicat group.

        Parameters:
            process (string): The name of the extract or replicat process. Example: process_example
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_process_heartbeat_records(
                process='process_example',
                connection='MYCONN'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/connections/{connection}/tables/heartbeat/{process}",
            path_params={"process": process, "connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/heartbeat/{process}
    def delete_process_heartbeat_records(self, process, connection, version='v2'):
        """
        DELETE /services/{version}/connections/{connection}/tables/heartbeat/{process}
        Delete heartbeat table entries for an extract or replicat group.

        Parameters:
            process (string): The name of the extract or replicat process. Example: process_example
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_process_heartbeat_records(
                process='process_example',
                connection='MYCONN'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/connections/{connection}/tables/heartbeat/{process}",
            path_params={"process": process, "connection": connection, "version": version},
        )

    # Endpoint: /services/{version}/connections/{connection}/tables/trace
    def manage_trace_tables(self, connection, data=None, version='v2'):
        """
        POST /services/{version}/connections/{connection}/tables/trace
        Manage Oracle GoldenGate Trace table

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.manage_trace_tables(
                connection='MYCONN',
                data={
                    "operation": "add",
                    "name": "oggadmin.trace01"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/tables/trace",
            path_params={"connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/procedure
    def manage_procedural_supplemental_logging(self, connection, data=None, version='v2'):
        """
        POST /services/{version}/connections/{connection}/trandata/procedure
        Manage Supplemental Logging for Database Procedures

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.manage_procedural_supplemental_logging(
                connection='MYCONN',
                data={
                    "operation": "info"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/trandata/procedure",
            path_params={"connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/schema
    def manage_schema_supplemental_logging(self, connection, data=None, version='v2'):
        """
        POST /services/{version}/connections/{connection}/trandata/schema
        Manage Supplemental Logging for Database Schemas

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.manage_schema_supplemental_logging(
                connection='MYCONN',
                data={
                    "operation": "info",
                    "schemaName": "oggadmin"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/trandata/schema",
            path_params={"connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/connections/{connection}/trandata/table
    def manage_table_supplemental_logging(self, connection, data=None, version='v2'):
        """
        POST /services/{version}/connections/{connection}/trandata/table
        Manage Supplemental Logging for Database Tables

        Parameters:
            connection (string): Connection name. For each alias in the credential store, a connection with
                the name 'domain.alias' exists. Example: MYCONN
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.manage_table_supplemental_logging(
                connection='MYCONN',
                data={
                    "operation": "add",
                    "tableName": "oggadmin.table01"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/connections/{connection}/trandata/table",
            path_params={"connection": connection, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/content
    def static_files(self, version='v2'):
        """
        GET /services/{version}/content
        Top level file list.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.static_files()

        """
        return self._call(
            "GET",
            "/services/{version}/content",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/content/{a}
    def static_files_a(self, a, version='v2'):
        """
        GET /services/{version}/content/{a}
        Return the contents of file described by the provided path.

        Parameters:
            a (string): Content file described by the provided path. Example: a_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.static_files_a(
                a='a_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}",
            path_params={"a": a, "version": version},
        )

    # Endpoint: /services/{version}/content/{a}/{b}
    def static_files_a_b(self, b, a, version='v2'):
        """
        GET /services/{version}/content/{a}/{b}
        Return the contents of file described by the provided path.

        Parameters:
            b (string): Content file described by the provided path. Example: b_example
            a (string): Content file described by the provided path. Example: a_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.static_files_a_b(
                b='b_example',
                a='a_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}/{b}",
            path_params={"b": b, "a": a, "version": version},
        )

    # Endpoint: /services/{version}/content/{a}/{b}/{c}
    def static_files_a_b_c(self, c, b, a, version='v2'):
        """
        GET /services/{version}/content/{a}/{b}/{c}
        Return the contents of file described by the provided path.

        Parameters:
            c (string): Content file described by the provided path. Example: c_example
            b (string): Content file described by the provided path. Example: b_example
            a (string): Content file described by the provided path. Example: a_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.static_files_a_b_c(
                c='c_example',
                b='b_example',
                a='a_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}/{b}/{c}",
            path_params={"c": c, "b": b, "a": a, "version": version},
        )

    # Endpoint: /services/{version}/content/{a}/{b}/{c}/{d}
    def static_files_a_b_c_d(self, d, c, b, a, version='v2'):
        """
        GET /services/{version}/content/{a}/{b}/{c}/{d}
        Return the contents of file described by the provided path.

        Parameters:
            d (string): Content file described by the provided path. Example: d_example
            c (string): Content file described by the provided path. Example: c_example
            b (string): Content file described by the provided path. Example: b_example
            a (string): Content file described by the provided path. Example: a_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.static_files_a_b_c_d(
                d='d_example',
                c='c_example',
                b='b_example',
                a='a_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}/{b}/{c}/{d}",
            path_params={"d": d, "c": c, "b": b, "a": a, "version": version},
        )

    # Endpoint: /services/{version}/content/{a}/{b}/{c}/{d}/{e}
    def static_files_a_b_c_d_e(self, e, d, c, b, a, version='v2'):
        """
        GET /services/{version}/content/{a}/{b}/{c}/{d}/{e}
        Return the contents of file described by the provided path.

        Parameters:
            e (string): Content file described by the provided path. Example: e_example
            d (string): Content file described by the provided path. Example: d_example
            c (string): Content file described by the provided path. Example: c_example
            b (string): Content file described by the provided path. Example: b_example
            a (string): Content file described by the provided path. Example: a_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.static_files_a_b_c_d_e(
                e='e_example',
                d='d_example',
                c='c_example',
                b='b_example',
                a='a_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}/{b}/{c}/{d}/{e}",
            path_params={"e": e, "d": d, "c": c, "b": b, "a": a, "version": version},
        )

    # Endpoint: /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}
    def static_files_a_b_c_d_e_f(self, f, e, d, c, b, a, version='v2'):
        """
        GET /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}
        Return the contents of file described by the provided path.

        Parameters:
            f (string): Content file described by the provided path. Example: f_example
            e (string): Content file described by the provided path. Example: e_example
            d (string): Content file described by the provided path. Example: d_example
            c (string): Content file described by the provided path. Example: c_example
            b (string): Content file described by the provided path. Example: b_example
            a (string): Content file described by the provided path. Example: a_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.static_files_a_b_c_d_e_f(
                f='f_example',
                e='e_example',
                d='d_example',
                c='c_example',
                b='b_example',
                a='a_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}",
            path_params={"f": f, "e": e, "d": d, "c": c, "b": b, "a": a, "version": version},
        )

    # Endpoint: /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}
    def static_files_a_b_c_d_e_f_g(self, g, f, e, d, c, b, a, version='v2'):
        """
        GET /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}
        Return the contents of file described by the provided path.

        Parameters:
            g (string): Content file described by the provided path. Example: g_example
            f (string): Content file described by the provided path. Example: f_example
            e (string): Content file described by the provided path. Example: e_example
            d (string): Content file described by the provided path. Example: d_example
            c (string): Content file described by the provided path. Example: c_example
            b (string): Content file described by the provided path. Example: b_example
            a (string): Content file described by the provided path. Example: a_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.static_files_a_b_c_d_e_f_g(
                g='g_example',
                f='f_example',
                e='e_example',
                d='d_example',
                c='c_example',
                b='b_example',
                a='a_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}",
            path_params={"g": g, "f": f, "e": e, "d": d, "c": c, "b": b, "a": a, "version": version},
        )

    # Endpoint: /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}/{h}
    def static_files_a_b_c_d_e_f_g_h(self, h, g, f, e, d, c, b, a, version='v2'):
        """
        GET /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}/{h}
        Return the contents of file described by the provided path.

        Parameters:
            h (string): Content file described by the provided path. Example: h_example
            g (string): Content file described by the provided path. Example: g_example
            f (string): Content file described by the provided path. Example: f_example
            e (string): Content file described by the provided path. Example: e_example
            d (string): Content file described by the provided path. Example: d_example
            c (string): Content file described by the provided path. Example: c_example
            b (string): Content file described by the provided path. Example: b_example
            a (string): Content file described by the provided path. Example: a_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.static_files_a_b_c_d_e_f_g_h(
                h='h_example',
                g='g_example',
                f='f_example',
                e='e_example',
                d='d_example',
                c='c_example',
                b='b_example',
                a='a_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}/{h}",
            path_params={"h": h, "g": g, "f": f, "e": e, "d": d, "c": c, "b": b, "a": a, "version": version},
        )

    # Endpoint: /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}/{h}/{i}
    def static_files_a_b_c_d_e_f_g_h_i(self, d, f, e, a, i, b, g, h, c, version='v2'):
        """
        GET /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}/{h}/{i}
        Return the contents of file described by the provided path.

        Parameters:
            d (string): Content file described by the provided path. Example: d_example
            f (string): Content file described by the provided path. Example: f_example
            e (string): Content file described by the provided path. Example: e_example
            a (string): Content file described by the provided path. Example: a_example
            i (string): Content file described by the provided path. Example: i_example
            b (string): Content file described by the provided path. Example: b_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            g (string): Content file described by the provided path. Example: g_example
            h (string): Content file described by the provided path. Example: h_example
            c (string): Content file described by the provided path. Example: c_example

        Example:
            client.static_files_a_b_c_d_e_f_g_h_i(
                d='d_example',
                f='f_example',
                e='e_example',
                a='a_example',
                i='i_example',
                b='b_example',
                g='g_example',
                h='h_example',
                c='c_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}/{h}/{i}",
            path_params={"d": d, "f": f, "e": e, "a": a, "i": i, "b": b, "g": g, "h": h, "c": c, "version": version},
        )

    # Endpoint: /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}/{h}/{i}/{j}
    def static_files_a_b_c_d_e_f_g_h_i_j(self, d, f, e, j, a, i, b, g, h, c, version='v2'):
        """
        GET /services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}/{h}/{i}/{j}
        Return the contents of file described by the provided path.

        Parameters:
            d (string): Content file described by the provided path. Example: d_example
            f (string): Content file described by the provided path. Example: f_example
            e (string): Content file described by the provided path. Example: e_example
            j (string): Content file described by the provided path. Example: j_example
            a (string): Content file described by the provided path. Example: a_example
            i (string): Content file described by the provided path. Example: i_example
            b (string): Content file described by the provided path. Example: b_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            g (string): Content file described by the provided path. Example: g_example
            h (string): Content file described by the provided path. Example: h_example
            c (string): Content file described by the provided path. Example: c_example

        Example:
            client.static_files_a_b_c_d_e_f_g_h_i_j(
                d='d_example',
                f='f_example',
                e='e_example',
                j='j_example',
                a='a_example',
                i='i_example',
                b='b_example',
                g='g_example',
                h='h_example',
                c='c_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/content/{a}/{b}/{c}/{d}/{e}/{f}/{g}/{h}/{i}/{j}",
            path_params={"d": d, "f": f, "e": e, "j": j, "a": a, "i": i, "b": b, "g": g, "h": h, "c": c, "version": version},
        )

    # Endpoint: /services/{version}/credentials
    def list_domains(self, version='v2'):
        """
        GET /services/{version}/credentials
        Retrieve the list of domains in the credential store.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_domains()

        """
        return self._call(
            "GET",
            "/services/{version}/credentials",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/credentials/{domain}
    def list_domain_aliases(self, domain, version='v2'):
        """
        GET /services/{version}/credentials/{domain}
        Retrieve the list of aliases for a domain in the credential store.

        Parameters:
            domain (string): Credential store domain name. Example: OracleGoldenGate
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_domain_aliases(
                domain='OracleGoldenGate'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/credentials/{domain}",
            path_params={"domain": domain, "version": version},
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def retrieve_alias(self, alias, domain, version='v2'):
        """
        GET /services/{version}/credentials/{domain}/{alias}
        Retrieve the available information for an alias in a credential store domain. The password for an alias
            will not be returned.

        Parameters:
            alias (string): Credential store alias. Example: ggnorth
            domain (string): Credential store domain name. Example: OracleGoldenGate
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_alias(
                alias='ggnorth',
                domain='OracleGoldenGate'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params={"alias": alias, "domain": domain, "version": version},
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def create_alias(self, alias, domain, data=None, version='v2'):
        """
        POST /services/{version}/credentials/{domain}/{alias}
        Create a new alias in the credential store.

        Parameters:
            alias (string): Credential store alias. Example: ggnorth
            domain (string): Credential store domain name. Example: OracleGoldenGate
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_alias(
                alias='ggnorth',
                domain='OracleGoldenGate',
                data={
                    "userid": "oggadmin",
                    "password": "oggadmin"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params={"alias": alias, "domain": domain, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def replace_alias(self, alias, domain, data=None, version='v2'):
        """
        PUT /services/{version}/credentials/{domain}/{alias}
        Update an alias in the credential store.

        Parameters:
            alias (string): Credential store alias. Example: ggnorth
            domain (string): Credential store domain name. Example: OracleGoldenGate
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.replace_alias(
                alias='ggnorth',
                domain='OracleGoldenGate',
                data={
                    "userid": "oggadmin",
                    "password": "newPassword"
                })
        """
        return self._call(
            "PUT",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params={"alias": alias, "domain": domain, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}
    def delete_alias(self, alias, domain, version='v2'):
        """
        DELETE /services/{version}/credentials/{domain}/{alias}
        Delete an alias from the credential store.

        Parameters:
            alias (string): Credential store alias. Example: ggnorth
            domain (string): Credential store domain name. Example: OracleGoldenGate
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_alias(
                alias='ggnorth',
                domain='OracleGoldenGate'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/credentials/{domain}/{alias}",
            path_params={"alias": alias, "domain": domain, "version": version},
        )

    # Endpoint: /services/{version}/credentials/{domain}/{alias}/valid
    def validate(self, alias, domain, version='v2'):
        """
        GET /services/{version}/credentials/{domain}/{alias}/valid
        Check validity of credentials and return database credentials details.

        Parameters:
            alias (string): Credential store alias. Example: ggnorth
            domain (string): Credential store domain name. Example: OracleGoldenGate
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.validate(
                alias='ggnorth',
                domain='OracleGoldenGate'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/credentials/{domain}/{alias}/valid",
            path_params={"alias": alias, "domain": domain, "version": version},
        )

    # Endpoint: /services/{version}/currentuser
    def retrieve_information(self, version='v2'):
        """
        GET /services/{version}/currentuser
        Return the current user's identity information encoded in the request.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_information()

        """
        return self._call(
            "GET",
            "/services/{version}/currentuser",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/currentuser
    def reset_information(self, version='v2'):
        """
        DELETE /services/{version}/currentuser
        Remove the current user's identity information encoded in the request.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.reset_information()

        """
        return self._call(
            "DELETE",
            "/services/{version}/currentuser",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/deployments
    def list_deployments(self, version='v2'):
        """
        GET /services/{version}/deployments
        Retrieve the collection of Oracle GoldenGate Deployments.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_deployments()

        """
        return self._call(
            "GET",
            "/services/{version}/deployments",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def retrieve_deployment(self, deployment, version='v2'):
        """
        GET /services/{version}/deployments/{deployment}
        Retrieve the details of a deployment.

        Parameters:
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_deployment(
                deployment='deployment_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}",
            path_params={"deployment": deployment, "version": version},
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def create_deployment(self, deployment, data=None, version='v2'):
        """
        POST /services/{version}/deployments/{deployment}
        Create a new Oracle GoldenGate deployment.

        Parameters:
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_deployment(
                deployment='deployment_example',
                data={
                    "oggHome": "/home/oracle/oggSecondary",
                    "oggEtcHome": "/home/oracle/ogg/etc",
                    "enabled": false
                })
        """
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}",
            path_params={"deployment": deployment, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def update_deployment(self, deployment, data=None, version='v2'):
        """
        PATCH /services/{version}/deployments/{deployment}
        Update the properties of a deployment.

        Parameters:
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_deployment(
                deployment='deployment_example',
                data={
                    "enabled": true
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/deployments/{deployment}",
            path_params={"deployment": deployment, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/deployments/{deployment}
    def remove_deployment(self, deployment, version='v2'):
        """
        DELETE /services/{version}/deployments/{deployment}
        Delete a deployment.

        Parameters:
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.remove_deployment(
                deployment='deployment_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/deployments/{deployment}",
            path_params={"deployment": deployment, "version": version},
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services
    def list_services(self, deployment, version='v2'):
        """
        GET /services/{version}/deployments/{deployment}/services
        Retrieve the collection of Oracle GoldenGate Services in a deployment.

        Parameters:
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_services(
                deployment='deployment_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/services",
            path_params={"deployment": deployment, "version": version},
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def retrieve_service(self, service, deployment, version='v2'):
        """
        GET /services/{version}/deployments/{deployment}/services/{service}
        Retrieve the details of a service in an Oracle GoldenGate deployment.

        Parameters:
            service (string): Name of the service. Example: service_example
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_service(
                service='service_example',
                deployment='deployment_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params={"service": service, "deployment": deployment, "version": version},
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def create_service(self, service, deployment, data=None, version='v2'):
        """
        POST /services/{version}/deployments/{deployment}/services/{service}
        Add a new service to a deployment. An application with the service name must exist for this request to
            succeed.

        Parameters:
            service (string): Name of the service. Example: service_example
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_service(
                service='service_example',
                deployment='deployment_example',
                data={
                    "$schema": "ogg:service",
                    "config": {
                        "network": {
                            "serviceListeningPort": 11001
                        },
                        "security": false,
                        "authorizationEnabled": true,
                        "defaultSynchronousWait": 30,
                        "asynchronousOperationEnabled": true,
                        "legacyProtocolEnabled": true,
                        "taskManagerEnabled": true
                    },
                    "enabled": false
                })
        """
        return self._call(
            "POST",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params={"service": service, "deployment": deployment, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def update_service_properties(self, service, deployment, data=None, version='v2'):
        """
        PATCH /services/{version}/deployments/{deployment}/services/{service}
        Update the properties of a service.

        Parameters:
            service (string): Name of the service. Example: service_example
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_service_properties(
                service='service_example',
                deployment='deployment_example',
                data={
                    "enabled": true,
                    "status": "running"
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params={"service": service, "deployment": deployment, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}
    def remove_service(self, service, deployment, version='v2'):
        """
        DELETE /services/{version}/deployments/{deployment}/services/{service}
        Remove a service from an Oracle GoldenGate deployment.

        Parameters:
            service (string): Name of the service. Example: service_example
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.remove_service(
                service='service_example',
                deployment='deployment_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/deployments/{deployment}/services/{service}",
            path_params={"service": service, "deployment": deployment, "version": version},
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}/logs
    def list_service_logs(self, service, deployment, version='v2'):
        """
        GET /services/{version}/deployments/{deployment}/services/{service}/logs
        Retrieve the set of logs for the service

        Parameters:
            service (string): Name of the service. Example: service_example
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_service_logs(
                service='service_example',
                deployment='deployment_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/services/{service}/logs",
            path_params={"service": service, "deployment": deployment, "version": version},
        )

    # Endpoint: /services/{version}/deployments/{deployment}/services/{service}/logs/default
    def default_log(self, service, deployment, version='v2'):
        """
        GET /services/{version}/deployments/{deployment}/services/{service}/logs/default
        Retrieve the service log

        Parameters:
            service (string): Name of the service. Example: service_example
            deployment (string): Name for the Oracle GoldenGate deployment. Example: deployment_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.default_log(
                service='service_example',
                deployment='deployment_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/deployments/{deployment}/services/{service}/logs/default",
            path_params={"service": service, "deployment": deployment, "version": version},
        )

    # Endpoint: /services/{version}/enckeys
    def list_encryption_keys(self, version='v2'):
        """
        GET /services/{version}/enckeys
        Retrieve the names of all encryption keys

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_encryption_keys()

        """
        return self._call(
            "GET",
            "/services/{version}/enckeys",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def retrieve_encryption_key(self, keyName, version='v2'):
        """
        GET /services/{version}/enckeys/{keyName}
        Retrieve details for an Encryption Key.

        Parameters:
            keyName (string): The name of the Encryption Key. Example: keyName_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_encryption_key(
                keyName='keyName_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/enckeys/{keyName}",
            path_params={"keyName": keyName, "version": version},
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def create_encryption_key(self, keyName, data=None, version='v2'):
        """
        POST /services/{version}/enckeys/{keyName}
        Create an Encryption Key.

        Parameters:
            keyName (string): The name of the Encryption Key. Example: keyName_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_encryption_key(
                keyName='keyName_example',
                data={
                    "bitLength": 128
                })
        """
        return self._call(
            "POST",
            "/services/{version}/enckeys/{keyName}",
            path_params={"keyName": keyName, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/enckeys/{keyName}
    def delete_encryption_key(self, keyName, version='v2'):
        """
        DELETE /services/{version}/enckeys/{keyName}
        Delete an Encryption Key

        Parameters:
            keyName (string): The name of the Encryption Key. Example: keyName_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_encryption_key(
                keyName='keyName_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/enckeys/{keyName}",
            path_params={"keyName": keyName, "version": version},
        )

    # Endpoint: /services/{version}/enckeys/{keyName}/encrypt
    def encrypt_data(self, keyName, data=None, version='v2'):
        """
        POST /services/{version}/enckeys/{keyName}/encrypt
        Encrypt data using the Encryption Key.

        Parameters:
            keyName (string): The name of the Encryption Key. Example: keyName_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.encrypt_data(
                keyName='keyName_example',
                data={
                    "data": "plaintext-password"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/enckeys/{keyName}/encrypt",
            path_params={"keyName": keyName, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/extracts
    def list_extracts(self, version='v2'):
        """
        GET /services/{version}/extracts
        Retrieve the collection of Extract processes

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_extracts()

        """
        return self._call(
            "GET",
            "/services/{version}/extracts",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def retrieve_extract(self, extract, version='v2'):
        """
        GET /services/{version}/extracts/{extract}
        Retrieve the details of an extract process.

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_extract(
                extract='extract_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}",
            path_params={"extract": extract, "version": version},
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def create_extract(self, extract, data=None, version='v2'):
        """
        POST /services/{version}/extracts/{extract}
        Create a new extract process.

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

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
                })
        """
        return self._call(
            "POST",
            "/services/{version}/extracts/{extract}",
            path_params={"extract": extract, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def update_extract(self, extract, data=None, version='v2'):
        """
        PATCH /services/{version}/extracts/{extract}
        Update an existing extract process. A user with the 'Operator' role may change the "status" property.
            Any other changes require the 'Administrator' role.

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_extract(
                extract='extract_example',
                data={
                    "status": "running"
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/extracts/{extract}",
            path_params={"extract": extract, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/extracts/{extract}
    def delete_extract(self, extract, version='v2'):
        """
        DELETE /services/{version}/extracts/{extract}
        Delete an extract process. If the extract process is currently running, it is stopped first.

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_extract(
                extract='extract_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/extracts/{extract}",
            path_params={"extract": extract, "version": version},
        )

    # Endpoint: /services/{version}/extracts/{extract}/command
    def issue_command_extract(self, extract, data=None, version='v2'):
        """
        POST /services/{version}/extracts/{extract}/command
        Execute an Extract process command

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.issue_command_extract(
                extract='extract_example',
                data={
                    "command": "STATS",
                    "arguments": "HOURLY"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/extracts/{extract}/command",
            path_params={"extract": extract, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/extracts/{extract}/info
    def list_information_types_extract(self, extract, version='v2'):
        """
        GET /services/{version}/extracts/{extract}/info
        Retrieve types of information available for an extract.

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_information_types_extract(
                extract='extract_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info",
            path_params={"extract": extract, "version": version},
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/checkpoints
    def retrieve_checkpoints_extract(self, extract, version='v2'):
        """
        GET /services/{version}/extracts/{extract}/info/checkpoints
        Retrieve the checkpoint information for the extract process.

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            history (string): Number of historical checkpoint records to return Example: history_example

        Example:
            client.retrieve_checkpoints_extract(
                extract='extract_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/checkpoints",
            path_params={"extract": extract, "version": version},
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/history
    def retrieve_history_extract(self, extract, version='v2'):
        """
        GET /services/{version}/extracts/{extract}/info/history
        Retrieve the execution history of a managed extract process.

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_history_extract(
                extract='extract_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/history",
            path_params={"extract": extract, "version": version},
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/reports
    def list_reports_extract(self, extract, version='v2'):
        """
        GET /services/{version}/extracts/{extract}/info/reports
        Retrieve the list of reports available for the extract process.

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_reports_extract(
                extract='extract_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/reports",
            path_params={"extract": extract, "version": version},
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/reports/{report}
    def retrieve_report_extract(self, report, extract, version='v2'):
        """
        GET /services/{version}/extracts/{extract}/info/reports/{report}
        Retrieve a report from the extract process.

        Parameters:
            report (string): The name of the report, which is the extract name, followed by an optional
                revision number and '.rpt' Example: report_example
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_report_extract(
                report='report_example',
                extract='extract_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/reports/{report}",
            path_params={"report": report, "extract": extract, "version": version},
        )

    # Endpoint: /services/{version}/extracts/{extract}/info/status
    def retrieve_status_extract(self, extract, version='v2'):
        """
        GET /services/{version}/extracts/{extract}/info/status
        Retrieve the current status of the extract process.

        Parameters:
            extract (string): The name of the extract. Extract names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                extract_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_status_extract(
                extract='extract_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/extracts/{extract}/info/status",
            path_params={"extract": extract, "version": version},
        )

    # Endpoint: /services/{version}/exttrails
    def get_list_deployment_extracts_with_their_trail_files(self, version='v2'):
        """
        GET /services/{version}/exttrails

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.get_list_deployment_extracts_with_their_trail_files()

        """
        return self._call(
            "GET",
            "/services/{version}/exttrails",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/installation/deployments
    def retrieve_deployment_list(self, version='v2'):
        """
        GET /services/{version}/installation/deployments
        Retrieve a list of all Oracle GoldenGate deployments for the installation.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2
            deployment (string): The name of a deployment for filtering results Example: deployment_example

        Example:
            client.retrieve_deployment_list()

        """
        return self._call(
            "GET",
            "/services/{version}/installation/deployments",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/installation/services
    def retrieve_service_list(self, version='v2'):
        """
        GET /services/{version}/installation/services
        Retrieve a list of all Oracle GoldenGate services for the installation.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2
            deployment (string): The name of a deployment for filtering results Example: deployment_example
            service (string): The name of a service for filtering results Example: service_example

        Example:
            client.retrieve_service_list()

        """
        return self._call(
            "GET",
            "/services/{version}/installation/services",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/logs
    def list_replication_logs(self, version='v2'):
        """
        GET /services/{version}/logs
        Retrieve the set of logs for ER processes

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_replication_logs()

        """
        return self._call(
            "GET",
            "/services/{version}/logs",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/logs/events
    def critical_events(self, version='v2'):
        """
        GET /services/{version}/logs/events
        This endpoint provides a log of all critical events that occur in replication processes.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.critical_events()

        """
        return self._call(
            "GET",
            "/services/{version}/logs/events",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/logs/{log}
    def retrieve_log(self, log, version='v2'):
        """
        GET /services/{version}/logs/{log}
        Retrieve an application log

        Parameters:
            log (string): Name of the log. Example: log_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_log(
                log='log_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/logs/{log}",
            path_params={"log": log, "version": version},
        )

    # Endpoint: /services/{version}/logs/{log}
    def modify_log_properties(self, log, data=None, version='v2'):
        """
        PATCH /services/{version}/logs/{log}
        Update application log properties.
        Not all logs can be modified, and if a PATCH operation is issued for a read-only log a status code of
            400 Bad Request is returned.

        Parameters:
            log (string): Name of the log. Example: log_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.modify_log_properties(
                log='log_example',
                data={
                    "enabled": true
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/logs/{log}",
            path_params={"log": log, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/logs/{log}
    def reset_log_data(self, log, version='v2'):
        """
        DELETE /services/{version}/logs/{log}
        Clear the contents of an application log.
        Not all logs can be modified, and if a DELETE operation is issued for a read-only log a status code of
            400 Bad Request is returned.

        Parameters:
            log (string): Name of the log. Example: log_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.reset_log_data(
                log='log_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/logs/{log}",
            path_params={"log": log, "version": version},
        )

    # Endpoint: /services/{version}/masterkey
    def list_versions(self, version='v2'):
        """
        GET /services/{version}/masterkey
        Retrieve all versions of the Master Key

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_versions()

        """
        return self._call(
            "GET",
            "/services/{version}/masterkey",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/masterkey
    def create_version(self, version='v2'):
        """
        POST /services/{version}/masterkey
        Create a new Master Key version

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.create_version()

        """
        return self._call(
            "POST",
            "/services/{version}/masterkey",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def retrieve_version(self, keyVersion, version='v2'):
        """
        GET /services/{version}/masterkey/{keyVersion}
        Retrieve a Master Key by version.

        Parameters:
            keyVersion (integer): The Master Key version number, 1 to 32767. Example: 1
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_version(
                keyVersion=1
            )
        """
        return self._call(
            "GET",
            "/services/{version}/masterkey/{keyVersion}",
            path_params={"keyVersion": keyVersion, "version": version},
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def update_version(self, keyVersion, data=None, version='v2'):
        """
        PATCH /services/{version}/masterkey/{keyVersion}
        Update a Master Key version

        Parameters:
            keyVersion (integer): The Master Key version number, 1 to 32767. Example: 1
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_version(
                keyVersion=1,
                data={
                    "status": "unavailable"
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/masterkey/{keyVersion}",
            path_params={"keyVersion": keyVersion, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/masterkey/{keyVersion}
    def delete_version(self, keyVersion, version='v2'):
        """
        DELETE /services/{version}/masterkey/{keyVersion}
        Delete a Master Key version

        Parameters:
            keyVersion (integer): The Master Key version number, 1 to 32767. Example: 1
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_version(
                keyVersion=1
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/masterkey/{keyVersion}",
            path_params={"keyVersion": keyVersion, "version": version},
        )

    # Endpoint: /services/{version}/messages
    def retrieve_messages(self, version='v2'):
        """
        GET /services/{version}/messages
        Retrieve messages from the Oracle GoldenGate deployment.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_messages()

        """
        return self._call(
            "GET",
            "/services/{version}/messages",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/metadata-catalog
    def retrieve_catalog(self, version='v2'):
        """
        GET /services/{version}/metadata-catalog
        The REST API catalog contains information about resources provided by each Oracle GoldenGate Service.
            Use this endpoint to retrieve a collection of all items in the catalog.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_catalog()

        """
        return self._call(
            "GET",
            "/services/{version}/metadata-catalog",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/metadata-catalog/{resource}
    def describe_catalog_item(self, resource, version='v2'):
        """
        GET /services/{version}/metadata-catalog/{resource}
        Use this endpoint to describe a single item in the metadata catalog. A list of items in the metadata
            catalog is obtained using the Retrieve Catalog endpoint.

        Parameters:
            resource (string): Name of the item in the metadata catalog. Example: resource_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.describe_catalog_item(
                resource='resource_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/metadata-catalog/{resource}",
            path_params={"resource": resource, "version": version},
        )

    # Endpoint: /services/{version}/monitoring/commands
    def retrieve_list_commands(self, version='v2'):
        """
        GET /services/{version}/monitoring/commands

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_list_commands()

        """
        return self._call(
            "GET",
            "/services/{version}/monitoring/commands",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/monitoring/commands/execute
    def execute_command_monitoring(self, data=None, version='v2'):
        """
        POST /services/{version}/monitoring/commands/execute

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.execute_command_monitoring(
                data={
                    "name": "purgeDatastore",
                    "daysValue": 90
                })
        """
        return self._call(
            "POST",
            "/services/{version}/monitoring/commands/execute",
            path_params={"version": version},
            data=data,
        )

    # Endpoint: /services/{version}/monitoring/lastMessageId
    def retrieve_existing_last_message_id_number(self, version='v2'):
        """
        GET /services/{version}/monitoring/lastMessageId

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_last_message_id_number()

        """
        return self._call(
            "GET",
            "/services/{version}/monitoring/lastMessageId",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/monitoring/lastStatusChangeId
    def retrieve_existing_last_status_change_id_number(self, version='v2'):
        """
        GET /services/{version}/monitoring/lastStatusChangeId

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_last_status_change_id_number()

        """
        return self._call(
            "GET",
            "/services/{version}/monitoring/lastStatusChangeId",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/monitoring/messages
    def retrieve_existing_process_messages(self, version='v2'):
        """
        GET /services/{version}/monitoring/messages

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2
            fromID (string): Starting Index Number Example: fromID_example
            toID (string): Ending Index Number Example: toID_example
            offset (string): Starting offset in result set Example: offset_example
            limit (string): Limit on the number of records to retreive Example: limit_example

        Example:
            client.retrieve_existing_process_messages()

        """
        return self._call(
            "GET",
            "/services/{version}/monitoring/messages",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/monitoring/statusChanges
    def retrieve_existing_process_status_changes(self, version='v2'):
        """
        GET /services/{version}/monitoring/statusChanges

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2
            fromID (string): Starting Index Number Example: fromID_example
            toID (string): Ending Index Number Example: toID_example
            offset (string): Starting offset in result set Example: offset_example
            limit (string): Limit on the number of records to retreive Example: limit_example

        Example:
            client.retrieve_existing_process_status_changes()

        """
        return self._call(
            "GET",
            "/services/{version}/monitoring/statusChanges",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/monitoring/{item}/messages
    def retrieve_existing_process_messages_item(self, item, version='v2'):
        """
        GET /services/{version}/monitoring/{item}/messages

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            fromID (string): Starting Index Number Example: fromID_example
            toID (string): Ending Index Number Example: toID_example
            offset (string): Starting offset in result set Example: offset_example
            limit (string): Limit on the number of records to retreive Example: limit_example

        Example:
            client.retrieve_existing_process_messages_item(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/monitoring/{item}/messages",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/monitoring/{item}/statusChanges
    def retrieve_existing_process_status_changes_item(self, item, version='v2'):
        """
        GET /services/{version}/monitoring/{item}/statusChanges

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            fromID (string): Starting Index Number Example: fromID_example
            toID (string): Ending Index Number Example: toID_example
            offset (string): Starting offset in result set Example: offset_example
            limit (string): Limit on the number of records to retreive Example: limit_example

        Example:
            client.retrieve_existing_process_status_changes_item(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/monitoring/{item}/statusChanges",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/processes
    def retrieve_existing_process_information_processes(self, version='v2'):
        """
        GET /services/{version}/mpoints/processes

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_process_information_processes()

        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/processes",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/batchSqlStatistics
    def retrieve_existing_integrated_replicat_batch_sql_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/batchSqlStatistics

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_integrated_replicat_batch_sql_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/batchSqlStatistics",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/cacheStatistics
    def retrieve_existing_cache_manager_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/cacheStatistics

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_cache_manager_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/cacheStatistics",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/configurationEr
    def retrieve_existing_basic_configuration_information_for_extract_and_replicat(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/configurationEr

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_basic_configuration_information_for_extract_and_replicat(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/configurationEr",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/configurationManager
    def retrieve_existing_basic_configuration_information_for_manager_and_services(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/configurationManager

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_basic_configuration_information_for_manager_and_services(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/configurationManager",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/coordinationReplicat
    def retrieve_existing_coordinated_replicat_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/coordinationReplicat

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_coordinated_replicat_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/coordinationReplicat",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/currentInflightTransactions
    def retrieve_existing_in_flight_transaction_information(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/currentInflightTransactions

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_in_flight_transaction_information(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/currentInflightTransactions",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/databaseInOut
    def retrieve_existing_database_information(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/databaseInOut

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_database_information(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/databaseInOut",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/dependencyStats
    def retrieve_existing_statistics_about_dependencies(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/dependencyStats

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_statistics_about_dependencies(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/dependencyStats",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrChunkStats
    def retrieve_existing_distribution_server_chunk_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/distsrvrChunkStats

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_distribution_server_chunk_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/distsrvrChunkStats",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrNetworkStats
    def retrieve_existing_distribution_server_network_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/distsrvrNetworkStats

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_distribution_server_network_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/distsrvrNetworkStats",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrPathStats
    def retrieve_existing_distribution_server_path_statistics_distsrvrPathStats(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/distsrvrPathStats

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_distribution_server_path_statistics_distsrvrPathStats(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/distsrvrPathStats",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/distsrvrTableStats
    def retrieve_existing_distribution_server_path_statistics_distsrvrTableStats(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/distsrvrTableStats

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_distribution_server_path_statistics_distsrvrTableStats(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/distsrvrTableStats",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/networkStatistics
    def retrieve_existing_network_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/networkStatistics

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_network_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/networkStatistics",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/parallelReplicat
    def retrieve_existing_parallel_replicat_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/parallelReplicat

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_parallel_replicat_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/parallelReplicat",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrProcStats
    def retrieve_existing_pm_service_monitored_process_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/pmsrvrProcStats

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_pm_service_monitored_process_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/pmsrvrProcStats",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrStats
    def retrieve_existing_pm_service_collector_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/pmsrvrStats

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_pm_service_collector_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/pmsrvrStats",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/pmsrvrWorkerStats
    def retrieve_existing_pm_service_worker_thread_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/pmsrvrWorkerStats

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_pm_service_worker_thread_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/pmsrvrWorkerStats",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/positionEr
    def retrieve_existing_checkpoint_position_information(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/positionEr

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_checkpoint_position_information(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/positionEr",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/process
    def retrieve_existing_process_information_item(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/process

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_process_information_item(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/process",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/processPerformance
    def retrieve_existing_process_performance_resource_utilization_information(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/processPerformance

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_process_performance_resource_utilization_information(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/processPerformance",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/queueBucketStatistics
    def retrieve_existing_queue_bucket_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/queueBucketStatistics

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_queue_bucket_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/queueBucketStatistics",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/queueStatistics
    def retrieve_existing_queue_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/queueStatistics

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_queue_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/queueStatistics",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/recvsrvrStats
    def retrieve_existing_receiver_service_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/recvsrvrStats

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_receiver_service_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/recvsrvrStats",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsExtract
    def retrieve_existing_extract_database_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/statisticsExtract

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_extract_database_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsExtract",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsProcedureExtract
    def retrieve_existing_extract_database_statistics_by_procedure_feature(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/statisticsProcedureExtract

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_extract_database_statistics_by_procedure_feature(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsProcedureExtract",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsProcedureReplicat
    def retrieve_existing_database_statistics_by_procedure_feature(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/statisticsProcedureReplicat

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_database_statistics_by_procedure_feature(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsProcedureReplicat",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsReplicat
    def retrieve_existing_replicat_database_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/statisticsReplicat

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_replicat_database_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsReplicat",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsTableExtract
    def retrieve_existing_extract_database_statistics_by_table(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/statisticsTableExtract

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_extract_database_statistics_by_table(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsTableExtract",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/statisticsTableReplicat
    def retrieve_existing_replicat_database_statistics_by_table(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/statisticsTableReplicat

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_replicat_database_statistics_by_table(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/statisticsTableReplicat",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/superpoolStatistics
    def retrieve_existing_super_pool_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/superpoolStatistics

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_super_pool_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/superpoolStatistics",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/threadPerformance
    def retrieve_existing_process_thread_resource_utilization_information(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/threadPerformance

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_process_thread_resource_utilization_information(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/threadPerformance",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/trailInput
    def retrieve_existing_input_trail_file_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/trailInput

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_input_trail_file_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/trailInput",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/mpoints/{item}/trailOutput
    def retrieve_existing_output_trail_file_statistics(self, item, version='v2'):
        """
        GET /services/{version}/mpoints/{item}/trailOutput

        Parameters:
            item (string):  Example: item_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_output_trail_file_statistics(
                item='item_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/mpoints/{item}/trailOutput",
            path_params={"item": item, "version": version},
        )

    # Endpoint: /services/{version}/oggerr
    def retrieve_list_message_codes(self, version='v2'):
        """
        GET /services/{version}/oggerr
        Retrieve all message codes from the Oracle GoldenGate deployment.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_list_message_codes()

        """
        return self._call(
            "GET",
            "/services/{version}/oggerr",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/oggerr/{message}
    def retrieve_message_explanation(self, message, version='v2'):
        """
        GET /services/{version}/oggerr/{message}
        Retrieve a detailed explanation for an Oracle GoldenGate message.

        Parameters:
            message (string): The Oracle GoldenGate Message Code, OGG-99999 Example: message_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_message_explanation(
                message='message_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/oggerr/{message}",
            path_params={"message": message, "version": version},
        )

    # Endpoint: /services/{version}/parameters
    def list_parameter_names(self, version='v2'):
        """
        GET /services/{version}/parameters
        Retrieve names of all known OGG parameters.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_parameter_names()

        """
        return self._call(
            "GET",
            "/services/{version}/parameters",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/parameters/{parameter}
    def retrieve_parameter_info(self, parameter, version='v2'):
        """
        GET /services/{version}/parameters/{parameter}
        Retrieve details for a parameter.

        Parameters:
            parameter (string): Name of parameter for information request Example: parameter_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_parameter_info(
                parameter='parameter_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/parameters/{parameter}",
            path_params={"parameter": parameter, "version": version},
        )

    # Endpoint: /services/{version}/replicats
    def list_replicats(self, version='v2'):
        """
        GET /services/{version}/replicats
        Retrieve the collection of Replicat processes

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2
            threads (string): Which replicat threads to include in the results. Example: threads_example

        Example:
            client.list_replicats()

        """
        return self._call(
            "GET",
            "/services/{version}/replicats",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def retrieve_replicat(self, replicat, version='v2'):
        """
        GET /services/{version}/replicats/{replicat}
        Retrieve the details of an replicat process.

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_replicat(
                replicat='replicat_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}",
            path_params={"replicat": replicat, "version": version},
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def create_replicat(self, replicat, data=None, version='v2'):
        """
        POST /services/{version}/replicats/{replicat}
        Create a new replicat process.

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

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
                })
        """
        return self._call(
            "POST",
            "/services/{version}/replicats/{replicat}",
            path_params={"replicat": replicat, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def update_replicat(self, replicat, data=None, version='v2'):
        """
        PATCH /services/{version}/replicats/{replicat}
        Update an existing replicat process. A user with the 'Operator' role may change the "status" property.
            Any other changes require the 'Administrator' role.

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_replicat(
                replicat='replicat_example',
                data={
                    "status": "running"
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/replicats/{replicat}",
            path_params={"replicat": replicat, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/replicats/{replicat}
    def delete_replicat(self, replicat, version='v2'):
        """
        DELETE /services/{version}/replicats/{replicat}
        Delete a replicat process. If the replicat process is currently running, it is stopped first.

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_replicat(
                replicat='replicat_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/replicats/{replicat}",
            path_params={"replicat": replicat, "version": version},
        )

    # Endpoint: /services/{version}/replicats/{replicat}/command
    def issue_command_replicat(self, replicat, data=None, version='v2'):
        """
        POST /services/{version}/replicats/{replicat}/command
        Execute a Replicat process command

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.issue_command_replicat(
                replicat='replicat_example',
                data={
                    "command": "STATS",
                    "arguments": "HOURLY"
                })
        """
        return self._call(
            "POST",
            "/services/{version}/replicats/{replicat}/command",
            path_params={"replicat": replicat, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info
    def list_information_types_replicat(self, replicat, version='v2'):
        """
        GET /services/{version}/replicats/{replicat}/info
        Retrieve types of information available for a replicat.

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_information_types_replicat(
                replicat='replicat_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info",
            path_params={"replicat": replicat, "version": version},
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/checkpoints
    def retrieve_checkpoints_replicat(self, replicat, version='v2'):
        """
        GET /services/{version}/replicats/{replicat}/info/checkpoints
        Retrieve the checkpoint information for the replicat process.

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            history (string): Number of historical checkpoint records to return Example: history_example

        Example:
            client.retrieve_checkpoints_replicat(
                replicat='replicat_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/checkpoints",
            path_params={"replicat": replicat, "version": version},
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/history
    def retrieve_history_replicat(self, replicat, version='v2'):
        """
        GET /services/{version}/replicats/{replicat}/info/history
        Retrieve the execution history of a managed replicat process.

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_history_replicat(
                replicat='replicat_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/history",
            path_params={"replicat": replicat, "version": version},
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/reports
    def list_reports_replicat(self, replicat, version='v2'):
        """
        GET /services/{version}/replicats/{replicat}/info/reports
        Retrieve the list of reports available for the replicat process.

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_reports_replicat(
                replicat='replicat_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/reports",
            path_params={"replicat": replicat, "version": version},
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/reports/{report}
    def retrieve_report_replicat(self, report, replicat, version='v2'):
        """
        GET /services/{version}/replicats/{replicat}/info/reports/{report}
        Retrieve a report from the replicat process.

        Parameters:
            report (string): The name of the report, which is the replicat name, followed by an optional
                revision number and '.rpt' Example: report_example
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_report_replicat(
                report='report_example',
                replicat='replicat_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/reports/{report}",
            path_params={"report": report, "replicat": replicat, "version": version},
        )

    # Endpoint: /services/{version}/replicats/{replicat}/info/status
    def retrieve_status_replicat(self, replicat, version='v2'):
        """
        GET /services/{version}/replicats/{replicat}/info/status
        Retrieve the current status of the replicat process.

        Parameters:
            replicat (string): The name of the replicat. Replicat names are upper case, begin with an
                alphabetic character followed by up to seven alpha-numeric characters. Example:
                replicat_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_status_replicat(
                replicat='replicat_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/replicats/{replicat}/info/status",
            path_params={"replicat": replicat, "version": version},
        )

    # Endpoint: /services/{version}/requests
    def retrieve_background_requests(self, version='v2'):
        """
        GET /services/{version}/requests
        Retrieve the collection of background REST API requests.

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_background_requests()

        """
        return self._call(
            "GET",
            "/services/{version}/requests",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/requests/{request}
    def retrieve_request_status(self, request, version='v2'):
        """
        GET /services/{version}/requests/{request}
        Retrieve the background request status.

        Parameters:
            request (integer): Identifier for background request. Example: 1
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_request_status(
                request=1
            )
        """
        return self._call(
            "GET",
            "/services/{version}/requests/{request}",
            path_params={"request": request, "version": version},
        )

    # Endpoint: /services/{version}/requests/{request}/result
    def retrieve_request_result(self, request, version='v2'):
        """
        GET /services/{version}/requests/{request}/result
        Retrieve the background request result.

        Parameters:
            request (integer): Identifier for background request. Example: 1
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_request_result(
                request=1
            )
        """
        return self._call(
            "GET",
            "/services/{version}/requests/{request}/result",
            path_params={"request": request, "version": version},
        )

    # Endpoint: /services/{version}/sources
    def get_list_distribution_paths_sources(self, version='v2'):
        """
        GET /services/{version}/sources

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.get_list_distribution_paths_sources()

        """
        return self._call(
            "GET",
            "/services/{version}/sources",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def delete_existing_oracle_goldengate_distribution_path(self, distpath, version='v2'):
        """
        DELETE /services/{version}/sources/{distpath}

        Parameters:
            distpath (string):  Example: distpath_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_existing_oracle_goldengate_distribution_path(
                distpath='distpath_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/sources/{distpath}",
            path_params={"distpath": distpath, "version": version},
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def create_new_oracle_goldengate_distribution_path(self, distpath, data=None, version='v2'):
        """
        POST /services/{version}/sources/{distpath}

        Parameters:
            distpath (string):  Example: distpath_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_new_oracle_goldengate_distribution_path(
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
                })
        """
        return self._call(
            "POST",
            "/services/{version}/sources/{distpath}",
            path_params={"distpath": distpath, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def update_existing_distribution_path(self, distpath, data=None, version='v2'):
        """
        PATCH /services/{version}/sources/{distpath}
        Update an existing distribution path. A user with the Operator role may change the status property. Any
            other changes require the Administrator role.

        Parameters:
            distpath (string):  Example: distpath_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_existing_distribution_path(
                distpath='distpath_example',
                data={
                    "$schema": "ogg:distPath",
                    "status": "stopped"
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/sources/{distpath}",
            path_params={"distpath": distpath, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/sources/{distpath}
    def retrieve_existing_oracle_goldengate_distribution_path(self, distpath, version='v2'):
        """
        GET /services/{version}/sources/{distpath}

        Parameters:
            distpath (string):  Example: distpath_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_oracle_goldengate_distribution_path(
                distpath='distpath_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/sources/{distpath}",
            path_params={"distpath": distpath, "version": version},
        )

    # Endpoint: /services/{version}/sources/{distpath}/checkpoints
    def retrieve_existing_oracle_goldengate_distribution_path_checkpoints(self, distpath, version='v2'):
        """
        GET /services/{version}/sources/{distpath}/checkpoints

        Parameters:
            distpath (string):  Example: distpath_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_oracle_goldengate_distribution_path_checkpoints(
                distpath='distpath_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/sources/{distpath}/checkpoints",
            path_params={"distpath": distpath, "version": version},
        )

    # Endpoint: /services/{version}/sources/{distpath}/info
    def retrieve_existing_oracle_goldengate_distribution_path_information(self, distpath, version='v2'):
        """
        GET /services/{version}/sources/{distpath}/info

        Parameters:
            distpath (string):  Example: distpath_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_oracle_goldengate_distribution_path_information(
                distpath='distpath_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/sources/{distpath}/info",
            path_params={"distpath": distpath, "version": version},
        )

    # Endpoint: /services/{version}/sources/{distpath}/stats
    def retrieve_existing_oracle_goldengate_distribution_path_statistics(self, distpath, version='v2'):
        """
        GET /services/{version}/sources/{distpath}/stats

        Parameters:
            distpath (string):  Example: distpath_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_oracle_goldengate_distribution_path_statistics(
                distpath='distpath_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/sources/{distpath}/stats",
            path_params={"distpath": distpath, "version": version},
        )

    # Endpoint: /services/{version}/targets
    def get_list_distribution_paths_targets(self, version='v2'):
        """
        GET /services/{version}/targets

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2
            targetInitiated (string): Filters the result with paths that match the property target-initiated
                Example: targetInitiated_example

        Example:
            client.get_list_distribution_paths_targets()

        """
        return self._call(
            "GET",
            "/services/{version}/targets",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/targets/{path}
    def delete_existing_oracle_goldengate_collector_path(self, path, version='v2'):
        """
        DELETE /services/{version}/targets/{path}

        Parameters:
            path (string):  Example: path_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_existing_oracle_goldengate_collector_path(
                path='path_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/targets/{path}",
            path_params={"path": path, "version": version},
        )

    # Endpoint: /services/{version}/targets/{path}
    def create_new_oracle_goldengate_collector_path(self, path, data=None, version='v2'):
        """
        POST /services/{version}/targets/{path}

        Parameters:
            path (string):  Example: path_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_new_oracle_goldengate_collector_path(
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
                })
        """
        return self._call(
            "POST",
            "/services/{version}/targets/{path}",
            path_params={"path": path, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/targets/{path}
    def update_existing_oracle_goldengate_collector_path(self, path, data=None, version='v2'):
        """
        PATCH /services/{version}/targets/{path}

        Parameters:
            path (string):  Example: path_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_existing_oracle_goldengate_collector_path(
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
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/targets/{path}",
            path_params={"path": path, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/targets/{path}
    def retrieve_existing_oracle_goldengate_collector_path(self, path, version='v2'):
        """
        GET /services/{version}/targets/{path}

        Parameters:
            path (string):  Example: path_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_oracle_goldengate_collector_path(
                path='path_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/targets/{path}",
            path_params={"path": path, "version": version},
        )

    # Endpoint: /services/{version}/targets/{path}/checkpoints
    def retrieve_existing_oracle_goldengate_receiver_server_path_checkpoints(self, path, version='v2'):
        """
        GET /services/{version}/targets/{path}/checkpoints

        Parameters:
            path (string):  Example: path_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_oracle_goldengate_receiver_server_path_checkpoints(
                path='path_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/targets/{path}/checkpoints",
            path_params={"path": path, "version": version},
        )

    # Endpoint: /services/{version}/targets/{path}/info
    def retrieve_existing_oracle_goldengate_receiver_server_path_information(self, path, version='v2'):
        """
        GET /services/{version}/targets/{path}/info

        Parameters:
            path (string):  Example: path_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_oracle_goldengate_receiver_server_path_information(
                path='path_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/targets/{path}/info",
            path_params={"path": path, "version": version},
        )

    # Endpoint: /services/{version}/targets/{path}/progress
    def retrieve_existing_oracle_receiver_server_progress(self, path, version='v2'):
        """
        GET /services/{version}/targets/{path}/progress

        Parameters:
            path (string):  Example: path_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_oracle_receiver_server_progress(
                path='path_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/targets/{path}/progress",
            path_params={"path": path, "version": version},
        )

    # Endpoint: /services/{version}/targets/{path}/stats
    def retrieve_existing_oracle_goldengate_receiver_server_path_stats(self, path, version='v2'):
        """
        GET /services/{version}/targets/{path}/stats

        Parameters:
            path (string):  Example: path_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_existing_oracle_goldengate_receiver_server_path_stats(
                path='path_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/targets/{path}/stats",
            path_params={"path": path, "version": version},
        )

    # Endpoint: /services/{version}/tasks
    def list_tasks(self, version='v2'):
        """
        GET /services/{version}/tasks
        Retrieve the list of tasks

        Parameters:
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_tasks()

        """
        return self._call(
            "GET",
            "/services/{version}/tasks",
            path_params={"version": version},
        )

    # Endpoint: /services/{version}/tasks/{task}
    def retrieve_task(self, task, version='v2'):
        """
        GET /services/{version}/tasks/{task}
        Retrieve the details for a task.

        Parameters:
            task (string): Task name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_' or '-'. Example: task_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_task(
                task='task_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/tasks/{task}",
            path_params={"task": task, "version": version},
        )

    # Endpoint: /services/{version}/tasks/{task}
    def create_task(self, task, data=None, version='v2'):
        """
        POST /services/{version}/tasks/{task}
        Create a new administrative task.

        Parameters:
            task (string): Task name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_' or '-'. Example: task_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.create_task(
                task='task_example',
                data={
                    "description": "Check critical lag every hour",
                    "enabled": false,
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
                })
        """
        return self._call(
            "POST",
            "/services/{version}/tasks/{task}",
            path_params={"task": task, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/tasks/{task}
    def update_task(self, task, data=None, version='v2'):
        """
        PATCH /services/{version}/tasks/{task}
        Update an existing administrative task.

        Parameters:
            task (string): Task name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_' or '-'. Example: task_example
            version (string): Oracle GoldenGate Service API version. Example: v2
            body (object):  Example: body_example

        Example:
            client.update_task(
                task='task_example',
                data={
                    "enabled": true
                })
        """
        return self._call(
            "PATCH",
            "/services/{version}/tasks/{task}",
            path_params={"task": task, "version": version},
            data=data,
        )

    # Endpoint: /services/{version}/tasks/{task}
    def delete_task(self, task, version='v2'):
        """
        DELETE /services/{version}/tasks/{task}
        Delete an administrative task.

        Parameters:
            task (string): Task name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_' or '-'. Example: task_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.delete_task(
                task='task_example'
            )
        """
        return self._call(
            "DELETE",
            "/services/{version}/tasks/{task}",
            path_params={"task": task, "version": version},
        )

    # Endpoint: /services/{version}/tasks/{task}/info
    def list_information_types_task(self, task, version='v2'):
        """
        GET /services/{version}/tasks/{task}/info
        Retrieve the collection of information types available for a task.

        Parameters:
            task (string): Task name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_' or '-'. Example: task_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.list_information_types_task(
                task='task_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/tasks/{task}/info",
            path_params={"task": task, "version": version},
        )

    # Endpoint: /services/{version}/tasks/{task}/info/history
    def retrieve_task_history(self, task, version='v2'):
        """
        GET /services/{version}/tasks/{task}/info/history
        Retrieve the execution history of an administrative task.

        Parameters:
            task (string): Task name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_' or '-'. Example: task_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_task_history(
                task='task_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/tasks/{task}/info/history",
            path_params={"task": task, "version": version},
        )

    # Endpoint: /services/{version}/tasks/{task}/info/status
    def retrieve_task_status(self, task, version='v2'):
        """
        GET /services/{version}/tasks/{task}/info/status
        Retrieve the current status of an administrative task.

        Parameters:
            task (string): Task name, an alpha-numeric character followed by up to 63 alpha-numeric
                characters, '_' or '-'. Example: task_example
            version (string): Oracle GoldenGate Service API version. Example: v2

        Example:
            client.retrieve_task_status(
                task='task_example'
            )
        """
        return self._call(
            "GET",
            "/services/{version}/tasks/{task}/info/status",
            path_params={"task": task, "version": version},
        )
