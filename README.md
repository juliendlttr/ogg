# oggrestapi

A Python client for the Oracle GoldenGate REST API.

## Install

```bash
pip install .
```

## Usage

```python
from oggrestapi import OGGRestAPI

# Initialize the client when using a reverse proxy
ogg_client = OGGRestAPI(
    url="https://vmogg",
    username="ogg",
    deployment="ogg_test_01",
    reverse_proxy=True
)

# Initialize the client with auto-discovery, when using the same credentials for all services
ogg_client = OGGRestAPI(
    url="https://vmogg:7809",
    username="ogg",
    deployment="ogg_test_01",
    auto_discovery=True
)

# Initialize the client against a single service
ogg_client = OGGRestAPI(
    url="https://vmogg:7810",
    username="ogg"
)

# Example: Get a list of all extracts
extracts = ogg_client.list_extracts()

>>> print(extracts)
[{'name': 'EXT1', 'status': 'running'}, {'name': 'EXT2', 'status': 'running'}]
```
