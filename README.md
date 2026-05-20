# Oracle GoldenGate Repository
This repository contains a collection of tools for Oracle GoldenGate.

## GoldenGate API Client
In each version directory, you will find a Python API client generated from the `swagger.json` specification for that version of GoldenGate. These clients provide a convenient way to interact with the GoldenGate REST API in Python.

### Usage Example
```python
from oggrestapi import OGGRestAPI

# Initialize the client
ogg_client = OGGRestAPI(
    url="https://vmogg:7810",
    username="ogg",
    password="password"
)

# Initialize the client when using a reverse proxy
ogg_client = OGGRestAPI(
    url="https://vmogg",
    username="ogg",
    password="password",
    deployment="ogg_test_01",
    reverse_proxy=True
)

# Example: Get a list of all extracts
extracts = ogg_client.list_extracts()

>>> print(extracts)
[{'name': 'EXT1', 'status': 'running'}, {'name': 'EXT2', 'status': 'running'}]
```
