from pathlib import Path

import robot

from smm_automation.cli import _exit_code

SUITE = """*** Test Cases ***
Passes
    No Operation
Known Failure
    [Tags]    known-issue:FINDING-1
    Fail    expected
Fixed Known Issue
    [Tags]    known-issue:FINDING-2
    No Operation
New Failure
    Fail    regression
"""


def _run(tmp_path: Path, suite: str) -> tuple[int, Path]:
    source = tmp_path / "suite.robot"
    source.write_text(suite, encoding="utf-8")
    output = tmp_path / "output.xml"
    rc = robot.run(str(source), output=str(output), log="NONE", report="NONE", stdout=None, console="none")
    return rc, output


def test_exit_code_counts_only_new_failures(tmp_path, capsys):
    rc, output = _run(tmp_path, SUITE)
    assert rc == 2
    assert _exit_code(rc, output, "new") == 1
    assert _exit_code(rc, output, "any") == 2
    assert _exit_code(rc, output, "none") == 0
    printed = capsys.readouterr().out
    assert "NEW FAIL (1): New Failure" in printed and "FIXED? (1)" in printed and "KNOWN FAIL (1)" in printed


def test_exit_code_known_only_is_green(tmp_path):
    rc, output = _run(tmp_path, SUITE.split("New Failure")[0])
    assert rc == 1 and _exit_code(rc, output, "new") == 0


def test_exit_code_keeps_robot_errors(tmp_path):
    assert _exit_code(252, tmp_path / "missing.xml", "new") == 252
