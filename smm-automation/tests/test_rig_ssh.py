import subprocess
import sys
from datetime import datetime

import pytest

from smm_automation import FRAMEWORK_ROOT
from smm_automation.rig_ssh import RigSsh, RigSshError, load_config, main
from smm_automation.rigcontrol import RigControl, RigControlError

CONFIG = {
    "host": "rig", "user": "op", "identity": "k",
    "appsmm": {"stop": "kill-app", "start": "start-app", "running": "is-app"},
    "broker": {"stop": "stop-mq", "start": "start-mq", "port": 1884},
    "log": {"dir": "/var/log/app/"},
}


class Board:
    """Fake ssh/scp: records the commands, answers from a script of return codes."""

    def __init__(self, codes=None, outputs=None):
        self.calls: list[list[str]] = []
        self.codes = codes or {}
        self.outputs = outputs or {}
        self.sleeps: list[float] = []
        self.now = 0.0
        self.port_checks = 0

    def run(self, argv, timeout_s):
        self.calls.append(argv)
        code = self.codes.get(argv[-1], 0)
        if isinstance(code, list):
            code = code.pop(0) if code else 0
        out = self.outputs.get(argv[-1], "")
        if isinstance(out, list):
            out = out.pop(0)
        return subprocess.CompletedProcess(argv, code, out, "boom" if code else "")

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    def rig(self, config=CONFIG, port_ready_after=0, wall=None):
        def port_open(host, port):
            self.port_checks += 1
            return self.port_checks > port_ready_after
        return RigSsh(config, run=self.run, sleep=self.sleep, port_open=port_open, clock=lambda: self.now,
                      **({"wall_ms": wall} if wall else {}))


def test_capabilities_follow_the_config():
    board = Board()
    assert board.rig().capabilities() == ["restart-appsmm", "restart-broker", "fetch-log", "clock"]
    assert board.rig({"host": "rig", "appsmm": {"stop": "x"}}).capabilities() == ["clock"]
    with pytest.raises(RigSshError, match="host"):
        RigSsh({})


def test_ssh_is_non_interactive():
    board = Board()
    board.rig().check()
    argv = board.calls[0]
    assert argv[0] == "ssh" and argv[-2:] == ["op@rig", "true"]
    assert "BatchMode=yes" in argv and argv[argv.index("-i") + 1] == "k"


def test_restart_appsmm_waits_down_and_until_running():
    board = Board({"is-app": [1, 1, 0]})
    board.rig().restart_appsmm(1500)
    assert [c[-1] for c in board.calls] == ["kill-app", "start-app", "is-app", "is-app", "is-app"]
    assert board.sleeps[0] == 1.5


def test_restart_appsmm_times_out():
    board = Board({"is-app": [1] * 500})
    with pytest.raises(RigSshError, match="appSMM to run again"):
        board.rig({**CONFIG, "ready_timeout_s": 5}).restart_appsmm(0)


def test_restart_broker_waits_for_the_port():
    board = Board()
    board.rig(port_ready_after=2).restart_broker(200)
    assert [c[-1] for c in board.calls] == ["stop-mq", "start-mq"]
    assert board.port_checks == 3


def test_failures_carry_the_remote_error():
    board = Board({"kill-app": 3})
    with pytest.raises(RigSshError, match="exit code 3: boom"):
        board.rig().restart_appsmm(0)


def test_fetch_log_uses_scp(tmp_path):
    board = Board()
    board.rig().fetch_log(tmp_path / "logs")
    argv = board.calls[0]
    assert argv[0] == "scp" and argv[-2:] == ["op@rig:/var/log/app/appSMM*.sil", str(tmp_path / "logs")]
    assert (tmp_path / "logs").is_dir()


def test_fetch_log_stages_the_files_on_the_board_first(tmp_path):
    board = Board()
    board.rig({**CONFIG, "log": {"dir": "/tmp/l", "prepare": "docker cp app:/var/log/. /tmp/l"}}).fetch_log(tmp_path)
    assert board.calls[0][-1] == "docker cp app:/var/log/. /tmp/l" and board.calls[1][0] == "scp"


def _local_ms(text):
    return datetime.fromisoformat(text).timestamp() * 1000


def test_clock_offset_is_bounded_by_the_time_around_each_call():
    stamps = ["2026-10-07T12:00:10.500", "2026-10-07T12:00:11.000", "2026-10-07T12:00:11.400"]
    board = Board(outputs={"python3 now": list(stamps)})
    pc = _local_ms("2026-10-07T12:00:00")
    walls = iter([pc, pc + 600, pc + 600, pc + 1000, pc + 1000, pc + 1200])
    rig = board.rig({"host": "rig", "clock": {"command": "python3 now"}}, wall=lambda: next(walls))
    offset, uncertainty = rig.clock_offset()
    # bounds: 10.5-0.6=9.9 .. 10.5, 11.0-1.0=10.0 .. 10.4, 11.4-1.2=10.2 .. 10.4 -> [10.2, 10.4]
    assert offset == pytest.approx(10300, abs=1) and uncertainty == pytest.approx(100, abs=1)


def test_clock_offset_with_whole_seconds_and_a_bad_answer():
    board = Board(outputs={"date +%Y-%m-%dT%H:%M:%S": ["2026-10-07T12:00:05", "noon"]})
    pc = _local_ms("2026-10-07T12:00:00")
    walls = iter([pc, pc + 400, pc, pc])
    rig = board.rig({"host": "rig"}, wall=lambda: next(walls))
    offset, uncertainty = rig.clock_offset(samples=1)
    assert offset == pytest.approx(5300) and uncertainty == pytest.approx(700)
    with pytest.raises(RigSshError, match="not an ISO time"):
        rig.clock_offset(samples=1)


def test_main_prints_the_clock_offset(capsys):
    class Rig:
        def clock_offset(self):
            return 11630012.4, 250.2

    assert main(["clock"], rig=Rig()) == 0
    assert capsys.readouterr().out.strip() == "11630012 250"


def test_plink_client_uses_putty_tools_with_a_password_file(tmp_path):
    secret = tmp_path / "rtc.pw"
    secret.write_text("x", encoding="utf-8")
    board = Board()
    rig = board.rig({**CONFIG, "client": "plink", "user": "root", "port": 22, "identity": None,
                     "password_file": str(secret)})
    rig.check()
    rig.fetch_log(tmp_path / "logs")
    ssh, scp = board.calls
    assert ssh[0] == "plink" and ssh[-3:] == ["-ssh", "rig", "true"]
    assert ssh[ssh.index("-pwfile") + 1] == str(secret) and "-batch" in ssh and ssh[ssh.index("-l") + 1] == "root"
    assert ssh[ssh.index("-P") + 1] == "22" and "-i" not in ssh and "x" not in ssh
    assert scp[0] == "pscp" and scp[-2:] == ["rig:/var/log/app/appSMM*.sil", str(tmp_path / "logs")]
    assert "-pwfile" in scp and "-ssh" not in scp


def test_plink_client_checks_its_settings(tmp_path):
    with pytest.raises(RigSshError, match="password_file"):
        RigSsh({"host": "rig", "client": "plink", "password_file": str(tmp_path / "missing")})
    with pytest.raises(RigSshError, match="client must be"):
        RigSsh({"host": "rig", "client": "telnet"})


def test_main_refuses_unconfigured_subcommands(capsys):
    board = Board()
    rig = board.rig({"host": "rig"})
    assert main(["restart-appsmm", "--down-ms", "5"], rig=rig) == 1
    assert "not configured" in capsys.readouterr().err
    assert main(["capabilities"], rig=rig) == 0 and board.calls[-1][-1] == "true"


def test_main_capabilities_fail_when_ssh_does(capsys):
    board = Board({"true": 255})
    assert main(["capabilities"], rig=board.rig()) == 1
    assert "exit code 255" in capsys.readouterr().err


def test_load_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SMM_RIG_SSH_CONFIG", raising=False)
    with pytest.raises(RigSshError, match="no config"):
        load_config(None)
    path = tmp_path / "rig.toml"
    path.write_text('host = "rig"\n[log]\ndir = "/l"\n', encoding="utf-8")
    monkeypatch.setenv("SMM_RIG_SSH_CONFIG", str(path))
    assert load_config(None)["log"] == {"dir": "/l"}
    path.write_text("host = ", encoding="utf-8")
    with pytest.raises(RigSshError, match="cannot read"):
        load_config(str(path))


def test_example_config_offers_nothing_until_filled_in():
    config = load_config(str(FRAMEWORK_ROOT / "robot" / "environments" / "rig_ssh.example.toml"))
    assert config["host"] == "10.0.1.111"
    assert RigSsh(config).capabilities() == ["clock"]


def test_works_as_the_rig_control_command(tmp_path):
    path = tmp_path / "rig.toml"
    path.write_text('host = "rig.invalid"\n', encoding="utf-8")
    control = RigControl([sys.executable, "-m", "smm_automation.rig_ssh", "--config", str(path)], timeout_s=60)
    # ssh to the unresolvable host rig.invalid fails, so the rig gets no capability and doctor reports why
    with pytest.raises(RigControlError, match="capabilities' failed"):
        control.capabilities()
