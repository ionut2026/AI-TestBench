import pytest

from smm_automation.SMMTestbench import SMMTestbench
from smm_automation.SMMTestbench import _command_matches as m


def test_command_matches():
    assert m("AppMan.InitializeCmd", "InitializeCmd")
    assert m("AppMan.InitializeCmd", "Initialize")
    assert m("AppMan.InitializeCmd", "AppMan.InitializeCmd")
    assert not m("AppMan.DeInitializeCmd", "InitializeCmd")
    assert not m("AppMan.DeInitializeCmd", "Initialize")
    assert m("AppMan.DeInitializeCmd", "DeInitialize")


class FakeSmm(SMMTestbench):
    """State machine stand-in: ``auto_init`` = Recover re-initializes on its own (spec), otherwise
    Recover stops in NotInitialized (appSMM 0.7)."""

    def __init__(self, state, auto_init=False):
        super().__init__(autostart=False)
        self.state, self.auto_init, self.sent = state, auto_init, []

    def get_system_state(self, timeout="5s"):
        return self.state

    def request_and_wait_for_response(self, request, response=None, timeout="10s", body=None, analyzer=-1, **match):
        self.sent.append(request)
        if request == "ShutdownRequest":
            self.state = "E-Stop"
        elif request == "RecoverRequest":
            self.state = "Idle" if self.auto_init else "NotInitialized"
        elif request == "InitializationRequest":
            self.state = "Idle"
        return {"Status": "OK"}

    def wait_for_system_state(self, expected, timeout="60s", since=None):
        return {}

    def wait_for_system_state_change(self, previous, timeout="60s"):
        return {}

    def _wait_idle_after_clearing(self, budget):
        pass


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


@pytest.mark.parametrize("start,auto_init,target,expected", [
    ("NormalOperation", False, "Idle", ["ShutdownRequest", "RecoverRequest", "InitializationRequest"]),
    ("E-Stop", False, "Idle", ["RecoverRequest", "InitializationRequest"]),
    ("E-Stop", True, "Idle", ["RecoverRequest"]),
    ("Idle", False, "E-Stop", ["ShutdownRequest"]),
    ("Idle", False, "NotInitialized", ["ShutdownRequest", "RecoverRequest"]),
    ("Idle", False, "Idle", []),
])
def test_bring_smm_to_state(start, auto_init, target, expected):
    smm = FakeSmm(start, auto_init)
    smm.bring_smm_to_state(target)
    assert smm.state == target
    assert smm.sent == expected


def test_bring_smm_to_state_refuses_impossible_targets():
    with pytest.raises(AssertionError):
        FakeSmm("NotInitialized").bring_smm_to_state("E-Stop")
    with pytest.raises(ValueError):
        FakeSmm("Idle").bring_smm_to_state("NormalOperation")


def test_pair_issues_are_scoped_to_the_test():
    smm = FakeSmm("Idle")
    smm._test_started_ms = 1000
    old, new = {"time": 999, "title": "old"}, {"time": 1001, "title": "new"}
    smm.client.pair_issues = lambda: [old, new]
    with pytest.raises(AssertionError, match="new") as err:
        smm.pair_issues_should_be_empty()
    assert "old" not in str(err.value)
    smm.client.pair_issues = lambda: [old]
    smm.pair_issues_should_be_empty()
    with pytest.raises(AssertionError, match="old"):
        smm.pair_issues_should_be_empty(since="all")


def test_clear_hardware_twin_racks():
    from smm_automation.client import ServiceError

    smm = FakeSmm("NormalOperation")
    actions = []
    smm.client.hardware = lambda: {"racks": [{"key": 3, "rackId": "A001"}, {"key": 7, "rackId": "B002"}]}
    smm.client.hardware_action = lambda action, args=None: actions.append((action, args)) or {}
    assert smm.clear_hardware_twin_racks() == ["A001", "B002"]
    assert actions == [("removeRack", {"key": 3}), ("removeRack", {"key": 7})]

    def no_twin():
        raise ServiceError(409, "No hardware twin", kind="unavailable")

    smm.client.hardware = no_twin
    assert smm.clear_hardware_twin_racks() == []
