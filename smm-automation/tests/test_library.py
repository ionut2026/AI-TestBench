from types import SimpleNamespace

import pytest

from smm_automation.client import ServiceError
from smm_automation.SMMTestbench import SMMTestbench, _timeline_html


class FakeSmm(SMMTestbench):
    """State machine stand-in: ``auto_init`` = Recover re-initializes on its own (spec), otherwise
    Recover stops in NotInitialized (appSMM 0.7)."""

    def __init__(self, state, auto_init=False):
        super().__init__(autostart=False)
        self.state, self.auto_init, self.sent = state, auto_init, []

    def _settled_state(self, deadline):
        return self.state

    def _set_marks(self):
        return 0

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


# ---------------------------------------------------------------------- library against a fake service


class FakeClient:
    """Records what the library asks the service; answers from scripted values."""

    def __init__(self):
        self.calls = []
        self.next_id = 100
        self.trace_id = 7
        self.state = "Idle"
        self.notifications = []  # bodies the next SystemStatusNotification waits return (empty = timeout)
        self.last_notification = {"PreviousState": "Clearing", "CurrentState": "Idle"}
        self.snapshot = {"timeline": {"droppedThrough": 0, "cap": 20000}}
        self.env = {"trace": {"droppedThrough": 0, "cap": 10000}}

    def _entry(self, name, body=None):
        self.next_id += 1
        return {"id": self.next_id, "name": name, "topic": "/is/iw/tx", "time": "", "body": body or {}}

    def mark_all(self):
        return self.next_id, self.trace_id

    def mark(self):
        return self.next_id

    def send(self, name, body=None, analyzer=-1, strict=True):
        self.calls.append(("send", name))
        return self._entry(name, body)

    def wait(self, flt, timeout_s):
        self.calls.append(("wait", flt))
        if flt["name"] == "SystemStatusResponse":
            return self._entry("SystemStatusResponse", {"CurrentState": self.state})
        if flt["name"] == "SystemStatusNotification" and "match" not in flt:
            if not self.notifications:
                raise ServiceError(408, "no notification", "timeout")
            body = self.notifications.pop(0)
            self.state = body["CurrentState"]
            return self._entry("SystemStatusNotification", body)
        return self._entry(flt["name"])

    def wait_sequence(self, filters, timeout_s, since=None):
        self.calls.append(("sequence", filters, since))
        return [self._entry(f["name"]) for f in filters]

    def expect_none(self, flt, duration_s):
        self.calls.append(("expect_none", flt))

    def query(self, flt=None, limit=1000):
        self.calls.append(("query", flt))
        if flt and flt.get("name") == "SystemStatusNotification" and limit == 1:
            return [{"id": 1, "body": self.last_notification}]
        return []

    def session(self):
        return {"smm": {"systemState": self.state}, **self.snapshot}

    def environment(self):
        return self.env

    def connect(self, timeout_s=10, clear=True, **settings):
        self.calls.append(("connect", clear))
        return {}

    def disconnect(self, abrupt=False):
        self.calls.append(("disconnect", abrupt))

    def trace_wait(self, command, since, timeout_s):
        self.calls.append(("trace_wait", command, since))
        return {"id": since + 1, "name": "AppMan." + command, "text": ""}

    def trace_expect_none(self, command, since, duration_s):
        self.calls.append(("trace_expect_none", command, since))
        raise ServiceError(409, f"appSMM sent {command}", "expectation",
                           {"entry": {"id": since + 2, "name": "AppMan." + command, "text": "args"}})

    def set_hardware_faults(self, faults):
        self.calls.append(("set_hardware_faults", faults))
        self.faults = faults
        return {"faults": faults, "stats": [{"message": f["message"], "action": f["action"], "matched": 0, "applied": 0} for f in faults]}

    def clear_hardware_faults(self):
        self.calls.append(("clear_hardware_faults",))
        self.faults = []

    def hardware_faults(self):
        return {"faults": getattr(self, "faults", []), "stats": getattr(self, "fault_stats", [])}


@pytest.fixture
def smm(monkeypatch):
    lib = SMMTestbench(autostart=False)
    lib.client = FakeClient()
    monkeypatch.setattr("smm_automation.SMMTestbench.BuiltIn", lambda: SimpleNamespace(
        get_variable_value=lambda *a: "PASS", set_suite_metadata=lambda *a, **k: None))
    lib.begin_smm_test()
    return lib


def _last(client, kind):
    return [c for c in client.calls if c[0] == kind][-1]


def test_a_message_is_returned_by_one_wait_only(smm):
    first = smm.wait_for_message_entry("InitializationResponse")
    smm.wait_for_message_entry("InitializationResponse")
    assert _last(smm.client, "wait")[1]["exclude"] == [first["id"]]
    smm.wait_for_message_entry("InitializationResponse", since="test")
    assert "exclude" not in _last(smm.client, "wait")[1]


def test_since_last_looks_after_the_previous_match(smm):
    entry = smm.wait_for_message_entry("RecoverResponse")
    smm.wait_for_message_entry("SystemStatusNotification", since="last", CurrentState="Initializing")
    flt = _last(smm.client, "wait")[1]
    assert flt["since"] == entry["id"]
    assert flt["exclude"] == [entry["id"]]


def test_sequence_skips_and_consumes_earlier_matches(smm):
    first = smm.wait_for_message_entry("ShutdownResponse")
    entries = smm.wait_for_message_sequence("ShutdownResponse", "SystemStatusNotification | CurrentState=E-Stop")
    _, filters, _ = _last(smm.client, "sequence")
    assert all(f["exclude"] == [first["id"]] for f in filters)
    assert smm._consumed == {first["id"], *(e["id"] for e in entries)}
    assert smm._last_match == entries[-1]["id"]


def test_counts_include_earlier_matches_but_not_hidden_polls(smm):
    smm._hidden.add(5)
    smm.wait_for_message_entry("SystemStatusResponse")
    smm.messages_should_have_been_received("SystemStatusResponse", since="test")
    assert _last(smm.client, "query")[1]["exclude"] == [5]
    smm.message_should_not_arrive("SystemStatusResponse")
    assert len(_last(smm.client, "expect_none")[1]["exclude"]) == 2


def test_connect_as_bridge_clears_only_outside_a_test(smm):
    smm.connect_as_bridge(record_version=False)
    assert _last(smm.client, "connect") == ("connect", False)
    assert smm._segments[-1][1] == "Bridge connected again"
    smm.connect_as_bridge(clear=True, record_version=False)
    assert _last(smm.client, "connect") == ("connect", True)
    smm._in_test = False
    smm.connect_as_bridge(record_version=False)
    assert _last(smm.client, "connect") == ("connect", True)


def test_stimuli_mark_the_cop_trace(smm):
    smm.client.trace_id = 42
    smm.send_icd_message("RecoverRequest")
    smm.wait_for_hardware_command("DeInitializeCmd")
    assert _last(smm.client, "trace_wait") == ("trace_wait", "DeInitializeCmd", 42)
    smm.wait_for_hardware_command("InitializeCmd", since="test")
    assert _last(smm.client, "trace_wait")[2] == 7
    with pytest.raises(AssertionError, match=r"COP #44 AppMan.InitializeCmd args"):
        smm.hardware_command_should_not_be_sent("InitializeCmd")


def test_settled_state_waits_for_notifications_and_hides_its_polls(smm):
    smm.client.state = "Initializing"
    smm.client.notifications = [{"PreviousState": "Initializing", "CurrentState": "Idle"},
                                {"PreviousState": "Idle", "CurrentState": "Clearing"},
                                {"PreviousState": "Clearing", "CurrentState": "Idle"}]

    def last_notification():
        # Idle reached from Initializing as long as the Clearing notifications are still to come.
        return {"PreviousState": "Initializing"} if smm.client.state == "Idle" and smm.client.notifications else {"PreviousState": "Clearing"}

    smm.client.query = lambda flt=None, limit=1000: [{"id": 1, "body": last_notification()}]
    assert smm._settled_state(float("inf")) == "Idle"
    assert smm.client.notifications == []
    polls = [c for c in smm.client.calls if c == ("send", "SystemStatusRequest")]
    assert len(polls) == 1
    assert len(smm._hidden) == 2


def test_idle_after_initializing_waits_for_clearing_then_settles(smm, monkeypatch):
    monkeypatch.setattr("smm_automation.SMMTestbench.CLEARING_GRACE_S", 0.01)
    smm.client.last_notification = {"PreviousState": "Initializing", "CurrentState": "Idle"}
    assert smm._settled_state(float("inf")) == "Idle"
    waits = [c[1] for c in smm.client.calls if c[0] == "wait" and c[1]["name"] == "SystemStatusNotification"]
    assert len(waits) == 1


def test_settled_state_gives_up_at_the_deadline(smm):
    smm.client.state = "Initializing"
    with pytest.raises(AssertionError, match="did not settle"):
        smm._settled_state(0)


def test_bring_smm_to_state_marks_after_the_precondition(smm):
    smm.client.next_id = 500
    smm.bring_smm_to_state("Idle")
    assert smm._mark >= 500


def test_finish_fails_when_the_service_dropped_evidence(smm):
    smm.finish_smm_test()
    smm.begin_smm_test()
    smm.client.snapshot = {"timeline": {"droppedThrough": smm._test_mark + 1, "cap": 100}}
    with pytest.raises(AssertionError, match="SMM_TIMELINE_CAP"):
        smm.finish_smm_test()
    smm.begin_smm_test()
    smm.client.snapshot = {"timeline": {"droppedThrough": 0, "cap": 100}}
    smm.client.env = {"trace": {"droppedThrough": smm._test_trace_mark + 1, "cap": 100}}
    with pytest.raises(AssertionError, match="SMM_TRACE_CAP"):
        smm.finish_smm_test()


def test_timeline_html_shows_reconnections():
    entries = [{"id": 1, "name": "A", "body": {}}, {"id": 5, "name": "B", "body": {}}]
    out = _timeline_html(entries, "t", [(3, "Bridge connected again"), (9, "appSMM restarted")])
    assert out.index("Bridge connected again") < out.index("<b>B</b>")
    assert out.index("<b>A</b>") < out.index("Bridge connected again")
    assert out.rindex("appSMM restarted") > out.index("<b>B</b>")


def test_hardware_fault_rules_from_robot_specs_and_cleared_by_teardown(smm):
    smm.set_hardware_faults("DeInitializeRsp | action=delay | ms=15000", {"message": "InitializeCmd", "action": "drop"},
                            '{"message": "InitializeRsp", "action": "error", "code": 3}')
    assert _last(smm.client, "set_hardware_faults")[1] == [
        {"message": "DeInitializeRsp", "action": "delay", "ms": 15000},
        {"message": "InitializeCmd", "action": "drop"},
        {"message": "InitializeRsp", "action": "error", "code": 3},
    ]
    smm.set_hardware_faults("EmergencyStopRsp | delay=1.5s | count=1")
    assert _last(smm.client, "set_hardware_faults")[1] == [{"message": "EmergencyStopRsp", "action": "delay", "ms": 1500, "count": 1}]
    with pytest.raises(ValueError, match="key=value"):
        smm.set_hardware_faults("DeInitializeRsp | drop")
    smm.finish_smm_test()
    assert ("clear_hardware_faults",) in smm.client.calls
    smm.client.calls.clear()
    smm.begin_smm_test()
    smm.finish_smm_test()
    assert ("clear_hardware_faults",) not in smm.client.calls


def test_hardware_fault_should_have_been_applied(smm):
    smm.client.fault_stats = [{"message": "DeInitializeRsp", "action": "drop", "matched": 2, "applied": 1}]
    smm.hardware_fault_should_have_been_applied("DeInitializeRsp")
    smm.hardware_fault_should_have_been_applied("DeInitializeRsp", times=1)
    with pytest.raises(AssertionError, match="applied 1 time"):
        smm.hardware_fault_should_have_been_applied("DeInitializeRsp", times=2)
    with pytest.raises(AssertionError, match="No COP fault rule"):
        smm.hardware_fault_should_have_been_applied("InitializeRsp")
    smm.client.fault_stats = [{"message": "DeInitializeRsp", "action": "drop", "matched": 0, "applied": 0}]
    with pytest.raises(AssertionError, match="at least 1"):
        smm.hardware_fault_should_have_been_applied("DeInitializeRsp")


def test_time_between_entries_iso_and_epoch(smm):
    sent = {"id": 1, "name": "RecoverRequest", "time": "2026-01-01T10:00:00.000Z"}
    answer = {"id": 9, "name": "RecoverResponse", "time": "2026-01-01T10:00:15.250Z"}
    assert smm.get_time_between(sent, answer) == 15.25
    assert smm.time_between_should_be_less_than(sent, answer, "20s") == 15.25
    assert smm.time_between_should_be_at_least(sent, answer, "15s") == 15.25
    with pytest.raises(AssertionError, match=r"came 15\.250 s after #1 RecoverRequest, limit 15s"):
        smm.time_between_should_be_less_than(sent, answer, "15s")
    with pytest.raises(AssertionError, match="expected at least 16s"):
        smm.time_between_should_be_at_least(sent, answer, "16s")
    with pytest.raises(AssertionError, match="before"):
        smm.time_between_should_be_less_than(answer, sent, "20s")
    assert smm.get_time_between(1_000, "3500") == 2.5
    assert smm.get_time_between({"time": 0}, "1970-01-01T00:00:01+00:00") == 1.0
    with pytest.raises(ValueError, match="no time"):
        smm.get_time_between({"id": 1}, 0)
