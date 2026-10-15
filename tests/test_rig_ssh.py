"""SSH rig behavior tested without external programs or instrument access."""


import subprocess

import pytest

from grf.rig_ssh import RigSsh, RigSshError


@pytest.mark.parametrize("change", [
    {"connect_timeout_s": float("nan")}, {"ready_timeout_s": float("inf")},
    {"port": -1}, {"port": True}, {"ready_timeout_s": 0}, {"widgetd": {"port": 65536}},
    {"widgetd": []}, {"log": {"typo": "x"}}, {"typo": 3},
])
def test_rejects_invalid_configuration(widget, change):
    with pytest.raises(RigSshError):
        RigSsh({"host": "board", **change}, run=lambda *args: pytest.fail("no commands"))


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_invalid_downtime_never_stops(widget, value):
    rig = RigSsh({"host": "board", "widgetd": {"stop": "stop", "start": "start"}},
                 run=lambda *args: pytest.fail("no commands"))
    with pytest.raises(RigSshError, match="down_ms"):
        rig.restart("widgetd", value)


def test_cancelled_downtime_still_attempts_start(widget):
    commands = []

    def run(argv, timeout):
        commands.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, "", "")

    def interrupted(seconds):
        raise KeyboardInterrupt()

    rig = RigSsh({"host": "board", "widgetd": {"stop": "stop", "start": "start"}}, run=run, sleep=interrupted)
    with pytest.raises(KeyboardInterrupt):
        rig.restart("widgetd", 10)
    assert commands == ["stop", "start"]


def test_stop_and_recovery_failure_are_both_reported(widget):
    def run(argv, timeout):
        return subprocess.CompletedProcess(argv, 1, "", f"{argv[-1]} failed")

    rig = RigSsh({"host": "board", "widgetd": {"stop": "stop", "start": "start"}}, run=run)
    with pytest.raises(RigSshError, match="start failed.*stop/downtime also failed.*stop failed"):
        rig.restart("widgetd", 10)


def test_contradictory_clock_bounds_are_unavailable(widget):
    walls = iter([0, 1, 0, 1])
    board = iter(["2000-01-01T00:00:00", "2001-01-01T00:00:00"])
    rig = RigSsh({"host": "board"}, wall_ms=lambda: next(walls),
                 run=lambda argv, timeout: subprocess.CompletedProcess(argv, 0, next(board), ""))
    with pytest.raises(RigSshError, match="contradict"):
        rig.clock_offset(samples=2)


def test_late_success_cannot_exceed_shared_readiness_budget(widget):
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

    rig = RigSsh({"host": "board", "ready_timeout_s": 5,
                  "widgetd": {"stop": "stop", "start": "start", "running": "running", "port": 42}},
                 run=run, clock=clock.monotonic, sleep=clock.sleep, port_open=port)
    with pytest.raises(RigSshError, match="timed out"):
        rig.restart("widgetd", 0)
    assert budgets == [5, 1]
