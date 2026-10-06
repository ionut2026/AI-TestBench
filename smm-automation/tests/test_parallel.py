import importlib
from pathlib import Path

import pytest

from smm_automation import SMMTestbench as lib_module
from smm_automation import cli
from smm_automation.client import ServiceError, claim_worker_slot, worker_broker_port, worker_url

ROBOT_RUN = importlib.import_module("robot.run")  # the module, not robot.run()


def test_each_worker_gets_its_own_local_service_and_broker_port():
    assert worker_url("http://127.0.0.1:8765/api/v1", 0) == "http://127.0.0.1:8775/api/v1"
    assert worker_url("http://localhost:9000/api/v1", 3) == "http://localhost:9013/api/v1"
    assert worker_url("http://[::1]:8765/api/v1", 1) == "http://[::1]:8776/api/v1"
    assert [worker_broker_port(n) for n in range(3)] == [1894, 1895, 1896]
    with pytest.raises(ServiceError, match="local automation service"):
        worker_url("http://10.0.1.20:8765/api/v1", 0)


def test_worker_slots_are_exclusive_until_released(tmp_path):
    first, h0 = claim_worker_slot(tmp_path, slots=2)
    second, h1 = claim_worker_slot(tmp_path, slots=2)
    assert (first, second) == (0, 1)
    with pytest.raises(ServiceError, match="No free parallel worker slot"):
        claim_worker_slot(tmp_path, slots=2)
    h0.close()
    again, h2 = claim_worker_slot(tmp_path, slots=2)
    assert again == 0
    h1.close()
    h2.close()


@pytest.fixture
def suite(tmp_path, monkeypatch):
    monkeypatch.setenv("SMM_OPERATOR", "none")
    path = tmp_path / "s.robot"
    path.write_text("*** Test Cases ***\nT\n    No Operation\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("tier", ["offline", "rig"])
def test_parallel_runs_are_refused_outside_the_mock_tier(monkeypatch, tmp_path, capsys, suite, tier):
    monkeypatch.setattr(ROBOT_RUN, "run_cli", lambda argv, exit=False: pytest.fail("robot must not run"))
    assert cli.main(["--suites", str(suite), "run", "--tier", tier, "--processes", "2", "--outdir", str(tmp_path / "o")]) == 2
    assert "only possible on the mock tier" in capsys.readouterr().err


def test_parallel_mock_run_uses_pabot_without_pabotlib(monkeypatch, tmp_path, suite):
    import pabot.pabot

    seen = {}

    def main_program(argv):
        seen["argv"] = argv
        Path(".pabotsuitenames").write_text("cache", encoding="utf-8")
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pabot.pabot, "main_program", main_program)
    monkeypatch.setattr(ROBOT_RUN, "run_cli", lambda argv, exit=False: pytest.fail("robot must not run directly"))
    assert cli.main(["--suites", str(suite), "run", "--tier", "mock", "--processes", "3", "--outdir", str(tmp_path / "o"), "--no-report"]) == 0
    argv = seen["argv"]
    assert argv[:8] == ["--processes", "3", "--no-pabotlib", "--command", cli.sys.executable, "-m", "robot", "--end-command"]
    assert argv[argv.index("--outputdir") + 1] == str(tmp_path / "o") and argv[-1] == str(suite)
    assert not (tmp_path / ".pabotsuitenames").exists()


class FakeClient:
    def __init__(self):
        self.url = "http://127.0.0.1:8765/api/v1"
        self.token = "old"
        self.started = None

    def start_environment(self, tier, overrides):
        self.started = (tier, overrides)
        return {"tier": tier}


@pytest.fixture
def worker_lib(monkeypatch):
    def make(pool):
        monkeypatch.setattr(lib_module, "_robot_var", lambda name, default: pool if name == "${PABOTEXECUTIONPOOLID}" else default)
        monkeypatch.setattr(lib_module, "_metadata", lambda name, value: None)
        monkeypatch.setattr(lib_module, "claim_worker_slot", lambda: (2, None))
        lib = lib_module.SMMTestbench()
        lib.client = FakeClient()
        monkeypatch.setattr(lib, "ensure_automation_service_is_running", lambda: lib._worker())
        return lib
    return make


def test_a_worker_moves_to_its_own_service_and_broker(worker_lib):
    lib = worker_lib("0")  # pabot's pool id only says "parallel"; the slot comes from claim_worker_slot
    lib.start_test_environment("mock", {"mock": {"faults": []}})
    assert lib.client.url == "http://127.0.0.1:8777/api/v1" and lib.client.token is None
    assert lib.client.started == ("mock", {"mock": {"faults": []}, "broker": {"port": 1896}})
    lib.start_test_environment("mock", {"broker": {"port": 2000}})
    assert lib.client.url == "http://127.0.0.1:8777/api/v1"  # moved once
    assert lib.client.started == ("mock", {"broker": {"port": 2000}})
    with pytest.raises(AssertionError, match="only possible on the mock tier"):
        lib.start_test_environment("offline")


def test_a_serial_run_keeps_the_default_service(worker_lib):
    lib = worker_lib("")
    lib.start_test_environment("mock")
    assert lib.client.url == "http://127.0.0.1:8765/api/v1" and lib.client.started == ("mock", {})
