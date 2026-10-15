import datetime as dt

import pytest
from robot import run

from grf import cli
from grf.acceptance import parse_request
from grf.pipeline.drift import TestRef, collect_tests
from grf.pipeline.report import read_results
from grf.selection import modifier, select


def request(specs=(), tests=(), min_tests=1):
    return parse_request({"schema": 1, "id": "selection", "action": "test", "tier": "bench",
                          "selection": {"specs": list(specs), "tests": list(tests), "min_tests": min_tests},
                          "authorization": {"owner": "Owner", "reference": "TICKET",
                                            "valid_until": dt.date.today() + dt.timedelta(days=1)}})


def test_selection_is_union_of_specs_and_exact_names():
    tests = [TestRef("A", "Root.One", "", [], specs=[1]), TestRef("B", "Root.Two", "", [], specs=[2]),
             TestRef("C", "Root.Three", "", [], specs=[3])]
    assert select(request([1], ["B"]), tests) == tests[:2]
    assert select(request(), tests) == tests


@pytest.mark.parametrize("selection", [
    {"tests": ["Missing"]}, {"tests": ["Duplicate"]}, {"specs": [99]}, {"min_tests": 3},
])
def test_selection_fails_before_execution_when_missing_or_ambiguous(selection):
    tests = [TestRef("Duplicate", "Root.One", "", [], specs=[1]), TestRef("Duplicate", "Root.Two", "", [], specs=[1])]
    with pytest.raises(ValueError):
        select(request(**selection), tests)


def test_prerun_selection_is_literal_and_removes_unselected_suite_setup(widget, tmp_path):
    suites = tmp_path / "suites"
    suites.mkdir()
    name = "Literal [brackets] *?"
    (suites / "chosen.robot").write_text(f"*** Test Cases ***\n{name}\n    No Operation\n"
                                         "Unrequested\n    Fail    Must not run\n", encoding="utf-8")
    (suites / "unrequested.robot").write_text("*** Settings ***\nSuite Setup    Fail    Must not run\n"
                                              "*** Test Cases ***\nOther\n    Fail    Must not run\n", encoding="utf-8")
    chosen = select(request(tests=[name]), collect_tests([suites]))
    output = tmp_path / "output"
    assert run(str(suites), outputdir=str(output), name="Renamed.Root", prerunmodifier=modifier(chosen),
               report=None, log=None) == 0
    tests, _ = read_results(output / "output.xml")
    assert [(test["name"], test["status"]) for test in tests] == [(name, "PASS")]


@pytest.mark.parametrize("failure", ["expired", "tier", "missing-test", "missing-record", "source-pin"])
def test_request_preflight_rejects_before_capability_discovery(widget, tmp_path, monkeypatch, failure):
    from types import SimpleNamespace

    suites = tmp_path / "robot" / "suites"
    suites.mkdir(parents=True)
    (suites / "example.robot").write_text("*** Test Cases ***\nExample\n    No Operation\n", encoding="utf-8")
    date = dt.date.today() + dt.timedelta(days=-1 if failure == "expired" else 1)
    action = "deploy-and-test" if failure == "missing-record" else "test"
    extra = ""
    if failure == "source-pin":
        extra = f'source_revision = "{"0" * 40}"\n'
    if failure == "missing-test":
        extra += '[selection]\ntests = ["Not present"]\n'
    if failure == "missing-record":
        extra += f'[[component]]\nname = "app"\nbuild = 7\nsha256 = "{"a" * 64}"\n'
        monkeypatch.setattr(widget.plugin, "deploy_components", lambda: [SimpleNamespace(name="app")])
    path = tmp_path / "request.toml"
    path.write_text(f'schema = 1\nid = "preflight"\naction = "{action}"\ntier = "bench"\n{extra}'
                    f'[authorization]\nowner = "Owner"\nreference = "TICKET"\nvalid_until = {date}\n',
                    encoding="utf-8")
    def forbidden_discovery(*args):
        pytest.fail("capability discovery occurred before invalid request rejection")
    monkeypatch.setattr(cli, "tier_capabilities", forbidden_discovery)
    args = ["run", "--request", str(path), "--operator", "none", "--no-report"]
    if failure == "tier":
        args += ["--tier", "sim"]
    assert cli.main(args) == 2
