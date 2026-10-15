import pytest

from grf.rigcontrol import RigControl, RigControlError, split_command


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "typo"])
def test_invalid_site_timeout_fails_before_execution(widget, value):
    with pytest.raises(RigControlError, match="seconds"):
        RigControl.from_env({"WIDGET_RIG_CONTROL": "synthetic", "WIDGET_RIG_CONTROL_TIMEOUT": value})


@pytest.mark.parametrize("command", ['[""]', '["python", 42]', '["unterminated"', '"unterminated'])
def test_invalid_site_command_has_project_diagnostic(widget, command):
    with pytest.raises(RigControlError, match="WIDGET_RIG_CONTROL"):
        split_command(command)


def test_json_command_preserves_windows_paths(widget):
    assert split_command(r'["D:\\rig\\python.exe", "D:\\rig\\control.py"]') == [
        r"D:\rig\python.exe", r"D:\rig\control.py",
    ]


def test_old_logs_cannot_satisfy_noop_fetch(widget, tmp_path, monkeypatch):
    old = tmp_path / "old.log"
    old.write_text("stale", encoding="utf-8")
    control = RigControl(["synthetic"])
    control._capabilities = frozenset({"fetch-log"})
    monkeypatch.setattr(control, "_run", lambda *args: "")
    with pytest.raises(RigControlError, match="no fresh"):
        control.fetch_log(tmp_path)
    assert old.read_text(encoding="utf-8") == "stale"


def test_fetch_returns_only_this_invocations_files(widget, tmp_path, monkeypatch):
    from pathlib import Path

    (tmp_path / "old.log").write_text("stale", encoding="utf-8")
    control = RigControl(["synthetic"])
    control._capabilities = frozenset({"fetch-log"})

    def run(command, directory):
        (Path(directory) / "new.log").write_text("fresh", encoding="utf-8")

    monkeypatch.setattr(control, "_run", run)
    assert control.fetch_log(tmp_path) == [tmp_path / "new.log"]


@pytest.mark.parametrize("sample", ["nan 1", "0 inf", "0 -1", "0", "1 2 3", "inf 0"])
def test_clock_requires_two_finite_bounds(widget, sample, monkeypatch):
    control = RigControl(["synthetic"])
    control._capabilities = frozenset({"clock"})
    monkeypatch.setattr(control, "_run", lambda *args, **kwargs: sample)
    with pytest.raises(RigControlError, match="uncertainty_ms"):
        control.log_clock_offset()
