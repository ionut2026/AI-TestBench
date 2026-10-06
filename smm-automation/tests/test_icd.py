import importlib

import pytest

from smm_automation.client import ServiceError


class FakeService:
    def __init__(self):
        self.calls: list[str] = []

    def icd(self):
        self.calls.append("icd")
        return {"version": 7, "messages": [], "schemas": ["SystemStatusRequest", "GetVersionRequest"]}

    def schema(self, name):
        self.calls.append(name)
        if name == "Broken":
            raise ServiceError(500, "boom", "internal")
        if name != "SystemStatusRequest":
            raise ServiceError(404, f"No ICD schema for {name}")
        return {"type": "object"}


@pytest.fixture
def real_icd(monkeypatch):
    # The autouse fake_icd fixture (conftest) replaces the functions; reload for the real ones.
    import smm_automation.icd as module

    module = importlib.reload(module)
    service = FakeService()
    monkeypatch.setattr(module, "_service", lambda: service)
    yield module, service
    importlib.reload(module)


def test_message_names_and_schemas_come_from_the_service_once(real_icd):
    icd, service = real_icd
    assert icd.message_names() == {"SystemStatusRequest", "GetVersionRequest"}
    assert icd.message_names() == {"SystemStatusRequest", "GetVersionRequest"}
    assert icd.schema("SystemStatusRequest") == {"type": "object"}
    assert icd.schema("SystemStatusRequest") == {"type": "object"}
    assert icd.schema("NoSuch") is None
    assert icd.schema("NoSuch") is None
    assert service.calls == ["icd", "SystemStatusRequest", "NoSuch"]


def test_other_service_errors_are_not_taken_for_a_missing_schema(real_icd):
    icd, _ = real_icd
    with pytest.raises(ServiceError):
        icd.schema("Broken")
