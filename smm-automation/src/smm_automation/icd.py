"""ICD message names and schemas, asked from the automation service (GET /icd, GET /schemas/:name).

The service loads the schemas of the pinned SMM TestBench; asking it keeps the Python side independent of where the
TestBench is checked out. The service is started locally when none is running (like the Robot library does).
"""

from __future__ import annotations

from smm_automation.client import ServiceClient, ServiceError, ensure_service

_client: ServiceClient | None = None
_names: set[str] | None = None
_schemas: dict[str, dict | None] = {}


def _service() -> ServiceClient:
    global _client
    if _client is None:
        client = ServiceClient()
        ensure_service(client)
        _client = client
    return _client


def message_names() -> set[str]:
    """Names of every ICD message the service has a schema for, e.g. ``SystemStatusNotification``."""
    global _names
    if _names is None:
        _names = set(_service().icd()["schemas"])
    return _names


def schema(name: str) -> dict | None:
    """The JSON schema of one ICD message, or None if the ICD has no such message."""
    if name not in _schemas:
        try:
            _schemas[name] = _service().schema(name)
        except ServiceError as err:
            if err.status != 404:
                raise
            _schemas[name] = None
    return _schemas[name]
