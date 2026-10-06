import json
import sys
from pathlib import Path

import pytest

from smm_automation.rigcontrol import RigControl, RigControlError, split_command

SCRIPT = r'''
import pathlib, sys, time
args = sys.argv[1:]
log = pathlib.Path(__file__).with_name("calls.txt")
with log.open("a") as f:
    f.write(" ".join(args) + "\n")
caps = pathlib.Path(__file__).with_name("caps.txt")
if args[0] == "capabilities":
    print(caps.read_text() if caps.exists() else "restart-appsmm restart-broker fetch-log unknown-thing")
elif args[0] == "restart-appsmm":
    if args[2] == "666":
        print("board unreachable", file=sys.stderr)
        sys.exit(3)
elif args[0] == "restart-broker":
    time.sleep(float(args[2]) / 1000)
elif args[0] == "fetch-log":
    dest = pathlib.Path(args[1])
    if pathlib.Path(__file__).with_name("nolog").exists():
        sys.exit(0)
    (dest / "appSMM-2026-01-01-10-00-00.sil").write_bytes(b"x")
    (dest / "appSMM.sil").write_bytes(b"y")
else:
    sys.exit(2)
'''


@pytest.fixture
def script(tmp_path: Path) -> Path:
    path = tmp_path / "rig_control.py"
    path.write_text(SCRIPT, encoding="utf-8")
    return path


def _calls(script: Path) -> list[str]:
    return script.with_name("calls.txt").read_text().splitlines()


def test_split_command_line_and_json():
    assert split_command(r'python "D:\rig scripts\rig.py" --site lab1') == ["python", r"D:\rig scripts\rig.py", "--site", "lab1"]
    assert split_command('["C:\\\\Program Files\\\\py.exe", "x.py"]') == [r"C:\Program Files\py.exe", "x.py"]
    with pytest.raises(RigControlError):
        split_command("[1, 2]")
    with pytest.raises(RigControlError):
        split_command("[not json")


def test_from_env():
    assert RigControl.from_env({}) is None
    assert RigControl.from_env({"SMM_RIG_CONTROL": "  "}) is None
    control = RigControl.from_env({"SMM_RIG_CONTROL": "python rig.py", "SMM_RIG_CONTROL_TIMEOUT": "12"})
    assert control is not None and control.command == ["python", "rig.py"] and control.timeout_s == 12
    with pytest.raises(RigControlError):
        RigControl.from_env({"SMM_RIG_CONTROL": "python rig.py", "SMM_RIG_CONTROL_TIMEOUT": "soon"})


def test_capabilities_are_asked_once_and_filtered(script):
    control = RigControl([sys.executable, str(script)])
    assert control.capabilities() == {"restart-appsmm", "restart-broker", "fetch-log"}
    assert control.test_capabilities() == {"restart", "broker-restart", "applog"}
    assert control.has("fetch-log")
    assert _calls(script) == ["capabilities"]


def test_restart_and_failure(script):
    control = RigControl([sys.executable, str(script)])
    control.restart_appsmm(1500)
    control.restart_broker(10)
    with pytest.raises(RigControlError, match="exit code 3: board unreachable"):
        control.restart_appsmm(666)
    assert _calls(script)[1:] == ["restart-appsmm --down-ms 1500", "restart-broker --down-ms 10", "restart-appsmm --down-ms 666"]


def test_missing_capability_is_refused(script):
    script.with_name("caps.txt").write_text("fetch-log")
    control = RigControl([sys.executable, str(script)])
    with pytest.raises(RigControlError, match="does not support restart-appsmm"):
        control.restart_appsmm(1000)
    assert _calls(script) == ["capabilities"]


def test_fetch_log(script, tmp_path):
    control = RigControl([sys.executable, str(script)])
    files = control.fetch_log(tmp_path / "log" / "1")
    assert [f.name for f in files] == ["appSMM-2026-01-01-10-00-00.sil", "appSMM.sil"]
    script.with_name("nolog").write_text("")
    with pytest.raises(RigControlError, match="copied no .sil file"):
        control.fetch_log(tmp_path / "log" / "2")


def test_timeout_and_missing_program(script):
    control = RigControl([sys.executable, str(script)], timeout_s=0.5)
    script.with_name("caps.txt").write_text("restart-broker")
    control.capabilities()
    with pytest.raises(RigControlError, match="did not finish within 0.5 s"):
        control.restart_broker(5000)
    with pytest.raises(RigControlError, match="not found"):
        RigControl(["no-such-program-smm-rig"]).capabilities()


def test_json_command_runs(script):
    control = RigControl.from_env({"SMM_RIG_CONTROL": json.dumps([sys.executable, str(script)])})
    assert control is not None and control.has("restart-broker")
