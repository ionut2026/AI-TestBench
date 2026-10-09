"""Golden-file tests: the pipeline outputs for the committed catalog, suites and review ledger must not change by
accident (safety net for refactorings such as the framework split). After an intended change, regenerate with
``UPDATE_GOLDENS=1 pytest tests/test_golden.py`` and review the diff of ``tests/golden``."""
import json
import os
from pathlib import Path

import pytest

from smm_automation import FRAMEWORK_ROOT
from smm_automation.pipeline import briefs, drift, lint, report
from smm_automation.pipeline.ingest import load_catalog

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDENS") == "1"
SUITES = [FRAMEWORK_ROOT / "robot" / "suites"]
BRIEF_SPECS = [2532504, 2532510, 2854109]

LINT_SUITE = """*** Settings ***
Resource    x.resource

*** Test Cases ***
SDS-1 Bad Timing
    [Documentation]    Variant of 1.
    [Tags]    SDS-1
    Sleep    3s
    Wait For Message    X    timeout=20s

SDS-1 No Doc
    [Tags]    SDS-1
    Log    x

SDS-1 Unrelated Doc
    [Documentation]    Checks something about the bridge.
    [Tags]    SDS-1
    Log    x

SDS-1 Restarts Through A User Keyword
    [Documentation]    Variant of 1.
    [Tags]    SDS-1
    Restart And Wait

SDS-1 Tagged Without Use
    [Documentation]    Variant of 1.
    [Tags]    SDS-1    needs:twin    requires:restart
    Log    x

SDS-1 Reads The Log Without Tag
    [Documentation]    Variant of 1.
    [Tags]    SDS-1
    ICD Messages Should Be Logged By appSMM    SystemStatusRequest

SDS-1 E-Stop Without Tag
    [Documentation]    Variant of 1.
    [Tags]    SDS-1
    Trigger Emergency Stop

SDS-1 Broker Restart Needs Its Own Tag
    [Documentation]    Variant of 1.
    [Tags]    SDS-1    requires:restart
    Restart MQTT Broker    down=${BROKER_OUTAGE}
"""
LINT_RESOURCE = """*** Keywords ***
Restart And Wait
    Restart appSMM    down=1s
"""
LINT_CATALOG = {"specifications": {"1": {"text": "If the bridge is lost, appSMM goes to the E-Stop state within 20 seconds."}}}


def _portable(text: str) -> str:
    return text.replace(str(FRAMEWORK_ROOT), "<ROOT>").replace("\\", "/")


def _assert_golden(name: str, actual: str) -> None:
    path = GOLDEN / name
    actual = _portable(actual)
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.exists(), f"missing golden file {path} (run with UPDATE_GOLDENS=1)"
    assert actual == path.read_text(encoding="utf-8"), f"{name} differs from the golden file"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(FRAMEWORK_ROOT / "catalog" / "pilot.json")


@pytest.fixture(scope="module")
def reviews():
    return drift.load_reviews(FRAMEWORK_ROOT / "catalog" / "reviews.toml")


def test_drift(catalog, reviews):
    result = drift.check(catalog, drift.collect_tests(SUITES), reviews)
    _assert_golden("drift.json", json.dumps(result, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    _assert_golden("drift.txt", drift.format_text(result) + "\n")


def test_lint_messages(tmp_path):
    (tmp_path / "x.resource").write_text(LINT_RESOURCE, encoding="utf-8")
    suite = tmp_path / "s.robot"
    suite.write_text(LINT_SUITE, encoding="utf-8")
    violations = lint.lint([suite], LINT_CATALOG)
    text = lint.format_text(violations, tmp_path).replace(str(tmp_path), "<TMP>")
    _assert_golden("lint.txt", text + "\n")


def test_briefs(catalog, tmp_path):
    paths = briefs.write_briefs(catalog, tmp_path, drift.collect_tests(SUITES), BRIEF_SPECS, "pilot")
    assert len(paths) == len(BRIEF_SPECS)
    for path in sorted(paths):
        _assert_golden(f"briefs/{path.name}", path.read_text(encoding="utf-8"))


def test_report(catalog, reviews):
    stored = json.loads((GOLDEN / "report-input.json").read_text(encoding="utf-8"))
    built = report.build_report(catalog, stored["tests"], stored["meta"], drift.collect_tests(SUITES), reviews)
    built["generatedAt"] = "2000-01-01T00:00:00+00:00"
    _assert_golden("report.json", json.dumps(built, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    _assert_golden("report.html", report.render_html(built))
