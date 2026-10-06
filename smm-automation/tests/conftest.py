import pytest

from smm_automation import icd

# ICD messages the unit tests know about; the real list comes from the automation service (smm_automation.icd).
FAKE_ICD = {
    "InitializationRequest", "InitializationResponse", "SystemStatusRequest", "SystemStatusResponse",
    "SystemStatusNotification", "ShutdownRequest", "ShutdownResponse", "RecoverRequest", "RecoverResponse",
}


@pytest.fixture(autouse=True)
def fake_icd(monkeypatch):
    """Keeps unit tests from starting the automation service for ICD lookups. ``fake_icd`` maps message name ->
    schema; tests add schemas to it."""
    schemas: dict[str, dict] = {}
    monkeypatch.setattr(icd, "message_names", lambda: set(FAKE_ICD))
    monkeypatch.setattr(icd, "schema", lambda name: schemas.get(name))
    return schemas
