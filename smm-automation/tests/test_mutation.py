import json
from pathlib import Path

import pytest

import robot
from smm_automation import FRAMEWORK_ROOT
from smm_automation.cli import main
from smm_automation.pipeline.drift import TestRef, collect_tests
from smm_automation.pipeline.mutation import (
    MutantError,
    load_mutants,
    mutate,
    per_spec,
    robot_argv,
    score,
    select_tests,
    specs_without_mutants,
    survivors_markdown,
    verdict,
    write_report,
)

MUTANTS = """
[[mutant]]
id = "drop-a"
description = "A is not sent"
specs = [1]
faults = [{ message = "A", action = "drop" }]

[[mutant]]
id = "drop-b"
description = "B is not sent"
specs = [2]
faults = [{ message = "B", action = "drop" }]

[[mutant]]
id = "untested"
description = "nobody checks C"
specs = [3]
faults = [{ message = "C", action = "drop" }]
"""

# The fake product: test 1 notices faults on A, test 2 never notices anything.
SUITE = """*** Test Cases ***
Checks A
    [Tags]    SDS-1
    Should Not Contain    %{SMM_MOCK_FAULTS}    "A"
Ignores B
    [Tags]    SDS-2
    No Operation
Twin Only
    [Tags]    SDS-1    needs:twin
    No Operation
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _fake_runner(argv: list[str], env: dict[str, str], console: Path, timeout_s: int) -> int:
    """Runs the selected tests in-process; the suites argument is the last argv entry."""
    import os

    options = argv[argv.index("robot") + 1:-1]
    includes = [options[i + 1] for i, o in enumerate(options) if o == "--include"]
    excludes = [options[i + 1] for i, o in enumerate(options) if o == "--exclude"]
    out_dir = options[options.index("--outputdir") + 1]
    old = os.environ.get("SMM_MOCK_FAULTS")
    os.environ["SMM_MOCK_FAULTS"] = env["SMM_MOCK_FAULTS"]
    try:
        return robot.run(argv[-1], include=includes, exclude=excludes, outputdir=out_dir, log="NONE", report="NONE",
                         stdout=None, console="none")
    finally:
        if old is None:
            os.environ.pop("SMM_MOCK_FAULTS", None)
        else:
            os.environ["SMM_MOCK_FAULTS"] = old


def test_load_mutants_validates(tmp_path):
    assert [m["id"] for m in load_mutants(_write(tmp_path, "m.toml", MUTANTS))] == ["drop-a", "drop-b", "untested"]
    bad = {
        "duplicate id": MUTANTS + MUTANTS,
        "Bad Id": MUTANTS.replace('"drop-a"', '"Bad Id"'),
        "specs": MUTANTS.replace("specs = [1]", "specs = []"),
        "action": MUTANTS.replace('action = "drop" }]\n\n[[mutant]]\nid = "drop-b"', 'action = "explode" }]\n\n[[mutant]]\nid = "drop-b"'),
    }
    for case, text in bad.items():
        with pytest.raises(MutantError):
            load_mutants(_write(tmp_path, f"{case}.toml", text))


def test_catalog_mutants_are_valid_and_tested():
    mutants = load_mutants(FRAMEWORK_ROOT / "catalog" / "mutants.toml")
    assert len(mutants) >= 40
    tests = collect_tests([FRAMEWORK_ROOT / "robot" / "suites"])
    for m in mutants:
        assert select_tests(m, tests, ["needs:twin"]), f"{m['id']} targets no test on the mock tier"


def test_select_tests_respects_specs_and_excludes():
    tests = [TestRef("t1", "s", "f", ["SDS-1"], [1]), TestRef("t2", "s", "f", ["SDS-1", "NEEDS:TWIN"], [1]),
             TestRef("t3", "s", "f", ["SDS-2"], [2])]
    assert [t.name for t in select_tests({"specs": [1]}, tests, ["needs:twin"])] == ["t1"]
    assert specs_without_mutants([{"specs": [1]}], tests, ["needs:twin"]) == [2]


def test_verdict_and_score():
    killed = verdict([{"name": "a", "status": "FAIL", "message": "Parent suite setup failed:\nboom"},
                      {"name": "b", "status": "FAIL", "message": "x != y"}, {"name": "c", "status": "PASS"}])
    assert killed["status"] == "killed" and [k["phase"] for k in killed["killedBy"]] == ["setup", "test"]
    assert verdict([{"name": "a", "status": "PASS"}])["status"] == "survived"
    assert verdict([{"name": "a", "status": "SKIP"}])["status"] == "noTests"
    entries = [{"id": "k", "status": "killed", "specs": [1]}, {"id": "s", "status": "survived", "specs": [1, 2]},
               {"id": "n", "status": "noTests", "specs": [3]}, {"id": "e", "status": "error", "specs": [3]}]
    assert score(entries) == {"killed": 1, "survived": 1, "noTests": 1, "errors": 1, "score": 0.5}
    assert per_spec(entries)["1"] == {"killed": ["k"], "survived": ["s"], "other": []}
    assert score([])["score"] is None


def test_robot_argv(tmp_path):
    argv = robot_argv("mock", tmp_path / "mock.py", [1, 2], ["needs:twin"], tmp_path / "out", [tmp_path / "suites"], "Mutant:x")
    assert argv[1:3] == ["-m", "robot"] and argv[-1] == str(tmp_path / "suites")
    assert argv.count("--include") == 2 and "SDS-2" in argv and argv[argv.index("--exclude") + 1] == "needs:twin"


def test_mutate_end_to_end_with_fake_runner(tmp_path):
    mutants = load_mutants(_write(tmp_path, "m.toml", MUTANTS))
    suite = _write(tmp_path, "suite.robot", SUITE)
    tests = collect_tests([suite])
    result = mutate(mutants, tests, tmp_path / "out", tier="mock", variablefile=tmp_path / "mock.py", excludes=["needs:twin"],
                    suites=[suite], runner=_fake_runner, log=lambda _: None)
    assert result["baseline"]["failed"] == [] and result["baseline"]["tests"] == 2
    status = {m["id"]: m["status"] for m in result["mutants"]}
    assert status == {"drop-a": "killed", "drop-b": "survived", "untested": "noTests"}
    assert result["mutants"][0]["killedBy"][0]["test"] == "Checks A"
    assert result["score"]["score"] == 0.5
    report = write_report(result, tmp_path / "out")
    assert "drop-b" in report.read_text(encoding="utf-8")
    assert json.loads((tmp_path / "out" / "mutation.json").read_text(encoding="utf-8"))["score"]["survived"] == 1
    md = survivors_markdown(result)
    assert "**drop-b** (SDS-2)" in md and "Ignores B" in md and "drop-a" not in md


def test_mutate_aborts_when_baseline_fails(tmp_path):
    mutants = load_mutants(_write(tmp_path, "m.toml", MUTANTS))
    suite = _write(tmp_path, "suite.robot", SUITE.replace("No Operation", "Fail    broken", 1))
    result = mutate(mutants, collect_tests([suite]), tmp_path / "out", tier="mock", variablefile=tmp_path / "mock.py",
                    excludes=["needs:twin"], suites=[suite], runner=_fake_runner, log=lambda _: None)
    assert result["aborted"] and result["baseline"]["failed"] == ["Ignores B"] and result["mutants"] == []


def test_mutate_reports_errors_when_robot_produces_no_output(tmp_path):
    mutants = load_mutants(_write(tmp_path, "m.toml", MUTANTS))[:1]
    suite = _write(tmp_path, "suite.robot", SUITE)
    result = mutate(mutants, collect_tests([suite]), tmp_path / "out", tier="mock", variablefile=tmp_path / "mock.py",
                    excludes=["needs:twin"], suites=[suite], baseline=False, runner=lambda *a: 252, log=lambda _: None)
    assert result["mutants"][0]["status"] == "error" and result["score"]["errors"] == 1


def test_cli_mutate_rejects_unknown_mutant(tmp_path, capsys):
    assert main(["mutate", "--mutants", str(_write(tmp_path, "m.toml", MUTANTS)), "--mutant", "nope"]) == 2
    assert "unknown mutant(s): nope" in capsys.readouterr().err
    assert main(["mutate", "--mutants", str(_write(tmp_path, "bad.toml", "[[mutant]]\nid = 'x'\n"))]) == 2
