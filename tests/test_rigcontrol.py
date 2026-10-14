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
