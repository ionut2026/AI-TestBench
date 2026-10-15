import json

import pytest
from robot import run

from grf.deploy.binding import assert_product_version
from grf.deploy.model import ArtifactRef, Installed, Outcome
from grf.deploy.record import DeployRecord
from grf.library import GrfLibrary


@pytest.fixture
def record(tmp_path):
    return DeployRecord(
        "Widget", "app", "board-1", "fake-board", "manual",
        outcome=Outcome.DEPLOYED,
        artifact=ArtifactRef("app", "local", "app.tar.gz", version="requested-not-installed"),
        installed=Installed("app", "running", version="1.2.3.4"),
    ).write(tmp_path / "deploy")


def check(record, **overrides):
    args = {"component": "app", "actual_version": "1.2.3.4", "target": "board-1", "project": "Widget"}
    args.update(overrides)
    return assert_product_version(record, **args)


def replace(record, **fields):
    data = json.loads(record.read_text(encoding="utf-8"))
    data.update(fields)
    record.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("outcome", ["DEPLOYED", "UNCHANGED", "RESTORED"])
def test_success_uses_installed_version_and_returns_record(record, outcome):
    replace(record, outcome=outcome)
    assert check(record)["installed"]["version"] == "1.2.3.4"
    assert check(str(record), project=None)["outcome"] == outcome


@pytest.mark.parametrize("outcome", [o.value for o in Outcome if o not in (
    Outcome.DEPLOYED, Outcome.UNCHANGED, Outcome.RESTORED,
)] + [None, "", {}, ["DEPLOYED"]])
def test_unsuccessful_outcomes_cannot_bind(record, outcome):
    replace(record, outcome=outcome)
    with pytest.raises(AssertionError, match="outcome"):
        check(record)


@pytest.mark.parametrize("fields,message", [
    ({"schema": True}, "schema"),
    ({"schema": 2}, "schema"),
    ({"dryRun": True}, "dryRun"),
    ({"dryRun": None}, "dryRun"),
    ({"dryRun": 0}, "dryRun"),
    ({"project": "Other"}, "project"),
    ({"component": "other"}, "component"),
    ({"target": "board-2"}, "target"),
    ({"project": ""}, "project"),
    ({"component": None}, "component"),
    ({"target": ["board-1"]}, "target"),
    ({"installed": None}, "running"),
    ({"installed": []}, "running"),
    ({"installed": {"status": "exited", "version": "1.2.3.4"}}, "running"),
    ({"installed": {"status": "running"}}, "installed version"),
    ({"installed": {"status": "running", "version": ""}}, "installed version"),
    ({"installed": {"status": "running", "version": 1234}}, "installed version"),
])
def test_invalid_record_fails_explicitly(record, fields, message):
    replace(record, **fields)
    with pytest.raises(AssertionError, match=message):
        check(record)


@pytest.mark.parametrize("value", ["", " ", None, 1234, True])
@pytest.mark.parametrize("name", ["component", "actual_version", "target", "project"])
def test_invalid_expected_values(record, name, value):
    if name == "project" and value is None:
        return  # None deliberately omits only the expected-project comparison.
    with pytest.raises(AssertionError, match=name):
        check(record, **{name: value})


@pytest.mark.parametrize("version", ["requested-not-installed", "1.2.3", "1.2.3.4 ", "v1.2.3.4"])
def test_versions_are_exact_and_never_fall_back_to_artifact(record, version):
    with pytest.raises(AssertionError, match="expected installed version"):
        check(record, actual_version=version)


@pytest.mark.parametrize("text", ["invalid JSON", "[]", "null", "{}", '{"schema":1}'])
def test_malformed_file(record, text):
    record.write_text(text, encoding="utf-8")
    with pytest.raises(AssertionError):
        check(record)


def test_one_explicit_file_required(record):
    for path in (record.parent, record.parent / "absent.json", "", None):
        with pytest.raises(AssertionError, match="record file"):
            check(path)


def test_missing_fields_are_not_defaults(record):
    for key in ("dryRun", "project", "component", "target"):
        data = json.loads(record.read_text(encoding="utf-8"))
        data.pop(key)
        record.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(AssertionError):
            check(record)
        data[key] = {"dryRun": False, "project": "Widget", "component": "app", "target": "board-1"}[key]
        record.write_text(json.dumps(data), encoding="utf-8")


def test_keyword_defaults_to_current_project(record, widget):
    lib = GrfLibrary()
    assert lib.deployment_record_should_match_product_version(str(record), "app", "1.2.3.4", "board-1")
    with pytest.raises(AssertionError, match="project"):
        lib.deployment_record_should_match_product_version(str(record), "app", "1.2.3.4", "board-1", "Other")


def test_inherited_keyword_runs_in_robot(record, widget, tmp_path):
    from grf.scaffold import init_project

    root = init_project(tmp_path / "product", "Widget", "WID")
    suite = tmp_path / "binding.robot"
    library_path = str(root / "src" / "product_automation" / "ProductLibrary.py").replace("\\", "\\\\")
    record_path = str(record).replace("\\", "\\\\")
    suite.write_text(
        "*** Settings ***\n"
        f"Library    {library_path}\n"
        "*** Test Cases ***\n"
        "Matching\n"
        f"    Deployment Record Should Match Product Version    {record_path}    app    1.2.3.4    board-1\n"
        "Mismatch\n"
        "    Run Keyword And Expect Error    *expected installed version*"
        f"    Deployment Record Should Match Product Version    {record_path}    app    wrong    board-1\n",
        encoding="utf-8",
    )
    assert run(str(suite), outputdir=str(tmp_path / "robot")) == 0
