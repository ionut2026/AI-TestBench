import importlib
import sys
from pathlib import Path

import pytest

from smm_automation import cli
from smm_automation.capabilities import TAG_CAPABILITY, excluded_tags, tier_capabilities
from smm_automation.doctor import format_checks, run_doctor
from smm_automation.rigcontrol import RigControl, RigControlError

ROBOT_RUN = importlib.import_module("robot.run")  # the module, not robot.run()


class FakeControl(RigControl):
    def __init__(self, caps=(), error=None):
        super().__init__(["rig-control"])
        self._caps, self._error = frozenset(caps), error

    def capabilities(self):
        if self._error:
            raise RigControlError(self._error)
        return self._caps


def _excluded(tier, operator="none", env=None, control=None):
    return excluded_tags(tier_capabilities(tier, operator, env or {}, control)[0])


def test_tier_exclusions():
    assert _excluded("mock") == ["needs:twin", "needs:hardware-action", "needs:operator", "needs:applog"]
    assert _excluded("offline") == ["needs:operator"]
    assert _excluded("offline", "console") == []
    assert set(_excluded("rig")) == set(TAG_CAPABILITY)
    assert _excluded("rig", "dialog", control=FakeControl({"restart-appsmm", "fetch-log"})) == ["needs:twin", "requires:broker-restart"]
    with pytest.raises(RigControlError):
        tier_capabilities("rig", "none", {}, FakeControl(error="board unreachable"))
    with pytest.raises(ValueError):
        tier_capabilities("bench")


def test_rig_capabilities_from_the_environment(tmp_path):
    script = tmp_path / "rig.py"
    script.write_text("print('restart-broker')", encoding="utf-8")
    caps, notes = tier_capabilities("rig", "none", {"SMM_RIG_CONTROL": f'"{sys.executable}" "{script}"'})
    assert caps == {"broker-restart"}
    assert any("rig control" in n for n in notes)


def test_run_excludes_missing_capabilities_and_sets_the_operator(monkeypatch, tmp_path, capsys):
    seen = {}

    def run_cli(argv, exit=False):
        seen["argv"] = argv
        seen["operator"] = cli.os.environ.get("SMM_OPERATOR")
        return 0

    monkeypatch.setattr(ROBOT_RUN, "run_cli", run_cli)
    monkeypatch.setenv("SMM_OPERATOR", "none")
    suite = tmp_path / "s.robot"
    suite.write_text("*** Test Cases ***\nT\n    No Operation\n", encoding="utf-8")
    rc = cli.main(["--suites", str(suite), "run", "--tier", "offline", "--operator", "console", "--outdir", str(tmp_path / "out"), "--no-report"])
    assert rc == 0
    excludes = [seen["argv"][i + 1] for i, a in enumerate(seen["argv"]) if a == "--exclude"]
    assert excludes == [] and seen["operator"] == "console"
    monkeypatch.setenv("SMM_OPERATOR", "none")
    rc = cli.main(["--suites", str(suite), "run", "--tier", "mock", "--outdir", str(tmp_path / "out"), "--no-report"])
    excludes = [seen["argv"][i + 1] for i, a in enumerate(seen["argv"]) if a == "--exclude"]
    assert excludes == ["needs:twin", "needs:hardware-action", "needs:operator", "needs:applog"]
    assert seen["operator"] == "none"
    assert "Excluded (capability missing): needs:twin" in capsys.readouterr().out


def test_run_stops_when_the_rig_control_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SMM_RIG_CONTROL", "no-such-program-smm-rig")
    monkeypatch.setenv("SMM_OPERATOR", "none")
    monkeypatch.setattr(ROBOT_RUN, "run_cli", lambda argv, exit=False: pytest.fail("robot must not run"))
    assert cli.main(["run", "--tier", "rig", "--outdir", str(tmp_path)]) == 2
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------- doctor


class FakeService:
    url = "http://fake/api/v1"

    def __init__(self, pinned=True, running=False, app_running=True, error=None):
        self.health_body = {"apiVersion": "1.3.0", "icdVersion": 7,
                            "testbench": {"commit": "c55073075f06aa", "pinnedCommit": "c55073075f06aa" if pinned else "0123456789ab",
                                          "pinned": pinned, "dirty": False}}
        self.running = running
        self.app_running = app_running
        self.error = error
        self.calls = []
        self.next_id = 0
        self.session_body = {}

    def health(self):
        if self.error:
            from smm_automation.client import ServiceError

            raise ServiceError(0, self.error, "unreachable")
        return self.health_body

    def is_up(self):
        return not self.error

    def environment(self):
        return {"running": self.running, "tier": "offline" if self.running else None}

    def start_environment(self, tier, overrides=None):
        self.calls.append(("start", tier, overrides))
        return {"running": True, "appSmm": {"running": self.app_running}}

    def stop_environment(self):
        self.calls.append(("stop",))

    def connect(self, timeout_s=10, clear=True):
        self.calls.append(("connect", timeout_s))

    def disconnect(self, abrupt=False):
        self.calls.append(("disconnect",))

    def mark(self):
        return self.next_id

    def session(self):
        return self.session_body

    def send(self, name, body=None):
        self.calls.append(("send", name))

    def wait(self, flt, timeout_s):
        bodies = {"SystemStatusResponse": {"CurrentState": "Idle"},
                  "GetVersionResponse": {"Integration": {"Major": 0, "Minor": 7, "Build": 2305, "Revision": 25001}}}
        return {"body": bodies[flt["name"]]}


ENV_FILE = {"OVERRIDES": {"broker": {"host": "rig", "port": 1883}}, "STARTUP_TIMEOUT": "120s"}


def _doctor(tier, service, operator="none", deep=False, env=None, port_open=lambda h, p: False, tty=True):
    checks = run_doctor(tier, operator, deep, client=service, env=env or {}, autostart=False, port_open=port_open,
                        is_tty=lambda: tty, environment_file=lambda t: ENV_FILE, sleep=lambda s: None)
    return {c.name: (c.status, c.detail) for c in checks}


def test_doctor_mock_starts_and_stops():
    service = FakeService()
    checks = _doctor("mock", service)
    assert checks["service"][0] == "OK" and checks["TestBench pin"][0] == "OK"
    assert checks["start environment"] == ("OK", "mock: appSMM running")
    assert service.calls == [("start", "mock", ENV_FILE["OVERRIDES"]), ("stop",)]
    assert "needs:twin" in checks["capabilities"][1]


def test_doctor_does_not_touch_a_running_environment():
    service = FakeService(running=True)
    checks = _doctor("mock", service, deep=True)
    assert checks["environment"][0] == "WARN" and checks["deep check"][0] == "SKIP"
    assert service.calls == []


def test_doctor_unpinned_testbench_fails_offline_only():
    assert _doctor("mock", FakeService(pinned=False))["TestBench pin"][0] == "WARN"
    checks = _doctor("offline", FakeService(pinned=False), env={"SMM_APPSMM_EXE": str(Path(sys.executable))})
    assert checks["TestBench pin"][0] == "FAIL" and checks["appSMM.exe"][0] == "OK"


def test_doctor_offline_checks_exe_and_port():
    checks = _doctor("offline", FakeService(), env={"SMM_APPSMM_EXE": r"Z:\nowhere\appSMM.exe"}, port_open=lambda h, p: True)
    assert checks["appSMM.exe"][0] == "FAIL"
    assert checks["broker port"][0] == "WARN"


def test_doctor_unreachable_service():
    checks = _doctor("mock", FakeService(error="refused"), deep=True)
    assert checks["service"] == ("FAIL", "refused")
    assert checks["deep check"][0] == "SKIP" and "start environment" not in checks


def test_doctor_rig(tmp_path):
    script = tmp_path / "rig.py"
    script.write_text("print('restart-appsmm fetch-log')", encoding="utf-8")
    env = {"SMM_RIG_BROKER": "rig:1884", "SMM_RIG_CONTROL": f'"{sys.executable}" "{script}"'}
    probed = []
    checks = _doctor("rig", FakeService(), "console", env=env, port_open=lambda h, p: probed.append((h, p)) or True, tty=False)
    assert probed == [("rig", 1884)]
    assert checks["rig broker"][0] == "OK"
    assert checks["rig control"][0] == "OK" and "fetch-log restart-appsmm" in checks["rig control"][1]
    assert checks["operator"][0] == "WARN"
    assert checks["capabilities"][1].endswith("excluded tags: needs:twin, requires:broker-restart")


def test_doctor_rig_without_control_or_operator():
    checks = _doctor("rig", FakeService(), env={}, port_open=lambda h, p: False)
    assert checks["rig broker"][0] == "FAIL"
    assert checks["rig control"][0] == "WARN" and checks["operator"][0] == "WARN"
    assert "start environment" not in checks
    broken = _doctor("rig", FakeService(), env={"SMM_RIG_CONTROL": "no-such-program-smm-rig"})
    assert broken["rig control"][0] == "FAIL" and broken["capabilities"][0] == "OK"


def test_doctor_deep_asks_appsmm():
    service = FakeService()
    checks = _doctor("offline", service, deep=True, env={"SMM_APPSMM_EXE": sys.executable})
    assert checks["appSMM"] == ("OK", "state Idle, version 0.7.2305.25001")
    assert ("connect", 120.0) in service.calls and service.calls[-1] == ("stop",)
    assert "OK" in format_checks(run_doctor("mock", client=FakeService(), env={}, autostart=False, environment_file=lambda t: ENV_FILE))


def test_doctor_deep_rig_detects_another_bridge():
    service = FakeService()
    checks = _doctor("rig", service, deep=True, port_open=lambda h, p: True)
    assert checks["appSMM"][0] == "OK" and checks["other Bridge"] == ("OK", "no other Bridge traffic within 5 s")
    service.session_body = {"otherBridge": {"count": 3, "lastMessage": '{"ShutdownRequest": {}}'}}
    checks = _doctor("rig", service, deep=True, port_open=lambda h, p: True)
    assert checks["other Bridge"][0] == "FAIL" and "3 message(s)" in checks["other Bridge"][1]
    assert service.calls[-1] == ("stop",)
    assert "other Bridge" not in _doctor("offline", FakeService(), deep=True, env={"SMM_APPSMM_EXE": sys.executable})
