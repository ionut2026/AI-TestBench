"""SSH rig behavior tested without external programs or instrument access."""


import subprocess

import pytest

from grf.rig_ssh import RigSsh, RigSshError


@pytest.fixture
def ssh_config(widget):
    hosts = widget.root.parent / f"{widget.root.name}.known_hosts"
    hosts.touch()
    return {"host": "board", "user": "root", "known_hosts": str(hosts)}


@pytest.mark.parametrize("change", [
    {"connect_timeout_s": float("nan")}, {"ready_timeout_s": float("inf")},
    {"port": -1}, {"port": True}, {"ready_timeout_s": 0}, {"widgetd": {"port": 65536}},
    {"widgetd": []}, {"log": {"typo": "x"}}, {"typo": 3},
])
def test_rejects_invalid_configuration(widget, ssh_config, change):
    with pytest.raises(RigSshError):
        RigSsh({**ssh_config, **change}, run=lambda *args: pytest.fail("no commands"))


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_invalid_downtime_never_stops(widget, ssh_config, value):
    rig = RigSsh({**ssh_config, "widgetd": {"stop": "stop", "start": "start"}},
                 run=lambda *args: pytest.fail("no commands"))
    with pytest.raises(RigSshError, match="down_ms"):
        rig.restart("widgetd", value)


def test_cancelled_downtime_still_attempts_start(widget, ssh_config):
    commands = []

    def run(argv, timeout):
        commands.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, "", "")

    def interrupted(seconds):
        raise KeyboardInterrupt()

    rig = RigSsh({**ssh_config, "widgetd": {"stop": "stop", "start": "start"}}, run=run, sleep=interrupted)
    with pytest.raises(KeyboardInterrupt):
        rig.restart("widgetd", 10)
    assert commands == ["stop", "start"]


def test_stop_and_recovery_failure_are_both_reported(widget, ssh_config):
    def run(argv, timeout):
        return subprocess.CompletedProcess(argv, 1, "", f"{argv[-1]} failed")

    rig = RigSsh({**ssh_config, "widgetd": {"stop": "stop", "start": "start"}}, run=run)
    with pytest.raises(RigSshError, match="start failed.*stop/downtime also failed.*stop failed"):
        rig.restart("widgetd", 10)


def test_contradictory_clock_bounds_are_unavailable(widget, ssh_config):
    walls = iter([0, 1, 0, 1])
    board = iter(["2000-01-01T00:00:00", "2001-01-01T00:00:00"])
    rig = RigSsh(ssh_config, wall_ms=lambda: next(walls),
                 run=lambda argv, timeout: subprocess.CompletedProcess(argv, 0, next(board), ""))
    with pytest.raises(RigSshError, match="contradict"):
        rig.clock_offset(samples=2)


def test_late_success_cannot_exceed_shared_readiness_budget(widget, ssh_config):
    from deploy_fakes import Clock

    clock = Clock()
    budgets = []

    def run(argv, timeout):
        if argv[-1] == "running":
            budgets.append(timeout)
            clock.sleep(4)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def port(host, number, timeout):
        budgets.append(timeout)
        clock.sleep(2)
        return True

    rig = RigSsh({**ssh_config, "ready_timeout_s": 5,
                  "widgetd": {"stop": "stop", "start": "start", "running": "running", "port": 42}},
                 run=run, clock=clock.monotonic, sleep=clock.sleep, port_open=port)
    with pytest.raises(RigSshError, match="timed out"):
        rig.restart("widgetd", 0)
    assert budgets == [5, 1]


@pytest.mark.parametrize("change", [
    {"client": "plink"}, {"ssh": "plink"}, {"hostkey": "fingerprint"}, {"scp": "pscp"},
])
def test_old_ssh_configs_require_explicit_migration(ssh_config, change):
    with pytest.raises(RigSshError, match="migration"):
        RigSsh({**ssh_config, **change}, run=lambda *args: pytest.fail("no commands"))


@pytest.mark.parametrize("listing", [
    "", "/logs/a.txt", "/other/a.txt\0", "/logs/../a.txt\0", "/logs/a.txt\0/logs/a.txt\0",
    "/logs/NUL\0", "/logs/a.txt.\0", "/logs/a:stream\0",
])
def test_log_listing_must_be_complete_safe_unique_and_nonempty(ssh_config, tmp_path, listing):
    rig = RigSsh({**ssh_config, "log": {"dir": "/logs"}},
                 run=lambda argv, seconds: subprocess.CompletedProcess(argv, 0, listing, ""))
    with pytest.raises(RigSshError):
        rig.fetch_log(tmp_path / "logs")


def test_rig_log_downloads_use_shared_transport_and_policy(ssh_config, tmp_path):
    from grf.ssh import CommandResult, Redactor

    class Connection:
        host, redact = "board", Redactor()

        def __init__(self):
            self.files = []

        def run(self, command, timeout_s, check):
            assert command == "find /logs -maxdepth 1 -type f -name '*.txt' -print0"
            return CommandResult(0, "/logs/a.txt\0/logs/b.txt\0", "")

        def get(self, remote, local, timeout_s):
            self.files.append(remote)
            local.write_text("synthetic log", encoding="utf-8")

        def put(self, local, remote, timeout_s):
            pytest.fail("rig log fetch never uploads")

        def close(self):
            pass

    connection = Connection()
    rig = RigSsh({**ssh_config, "log": {"dir": "/logs", "pattern": "*.txt"}}, connection=connection)
    output = tmp_path / "logs"
    rig.fetch_log(output)
    assert connection.files == ["/logs/a.txt", "/logs/b.txt"]
    assert len(list(output.iterdir())) == 2
