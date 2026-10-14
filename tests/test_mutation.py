import json

import pytest

from grf.cli import main
from grf.pipeline import mutation, report
from grf.pipeline.drift import TestRef


@pytest.fixture
def mutation_case(widget, monkeypatch):
    mutants = [{"id": "drop", "description": "Drop increment", "specs": [1],
                "faults": [{"message": "increment", "action": "drop"}]}]
    tests = [TestRef("Increment", "Counter", "", ["WID-1"], specs=[1])]
    results = [{"name": "Increment", "status": "PASS", "message": ""}]
    monkeypatch.setattr(report, "read_results", lambda _: (list(results), {}))
    return mutants, tests, results


def run_mutation(widget, case, runner, baseline=True):
    mutants, tests, _ = case
    return mutation.mutate(mutants, tests, widget.root / "mutation", tier="sim",
                           variablefile=widget.root / "sim.py", excludes=[], suites=[widget.root / "suites"],
                           baseline=baseline, runner=runner)


def test_mutation_propagates_project_and_faults(widget, mutation_case):
    calls = []

    def runner(argv, env, console, timeout):
        calls.append((argv, env))
        (console.parent / "output.xml").write_text("synthetic", encoding="utf-8")
        return 0

    result = run_mutation(widget, mutation_case, runner)
    assert result["score"]["survived"] == 1
    assert all(env["GRF_PROJECT"] == str(widget.root) for _, env in calls)
    assert json.loads(calls[0][1]["WIDGET_MOCK_FAULTS"]) == []
    assert json.loads(calls[1][1]["WIDGET_MOCK_FAULTS"]) == mutation_case[0][0]["faults"]


@pytest.mark.parametrize("rc,status", [(255, "PASS"), (-9, "PASS"), (1, "FAIL"), (0, "SKIP")])
def test_baseline_errors_and_skips_abort(widget, mutation_case, rc, status):
    mutation_case[2][0]["status"] = status

    def runner(argv, env, console, timeout):
        (console.parent / "output.xml").write_text("synthetic", encoding="utf-8")
        return rc

    assert run_mutation(widget, mutation_case, runner)["aborted"]


@pytest.mark.parametrize("rc,message", [
    (255, "Assertion failed"), (-9, "Assertion failed"),
    (1, "Setup failed: unavailable"), (1, "Parent suite setup failed: unavailable"),
    (1, "Teardown failed: unavailable"),
])
def test_infrastructure_failures_are_not_killed_mutants(widget, mutation_case, rc, message):
    mutation_case[2][0].update(status="FAIL", message=message)

    def runner(argv, env, console, timeout):
        (console.parent / "output.xml").write_text("synthetic", encoding="utf-8")
        return rc

    result = run_mutation(widget, mutation_case, runner, baseline=False)
    assert result["mutants"][0]["status"] == "error"
    assert result["score"]["errors"] == 1 and result["score"]["killed"] == 0


def test_assertion_failure_kills_mutant(widget, mutation_case):
    mutation_case[2][0].update(status="FAIL", message="Counter is 0, expected 1")

    def runner(argv, env, console, timeout):
        (console.parent / "output.xml").write_text("synthetic", encoding="utf-8")
        return 1

    assert run_mutation(widget, mutation_case, runner, baseline=False)["score"]["killed"] == 1


def test_partly_skipped_mutant_is_not_a_successful_analysis():
    result = mutation.verdict([{"name": "One", "status": "PASS"}, {"name": "Two", "status": "SKIP"}])
    assert result["status"] == "error"


def test_existing_mutation_output_is_not_reused(widget, mutation_case):
    out = widget.root / "mutation" / "drop"
    out.mkdir(parents=True)
    output = out / "output.xml"
    output.write_text("previous", encoding="utf-8")

    def runner(*args):
        pytest.fail("must not execute against existing output")

    with pytest.raises(mutation.MutantError, match="refusing to reuse"):
        run_mutation(widget, mutation_case, runner, baseline=False)
    assert output.read_text(encoding="utf-8") == "previous"


@pytest.mark.parametrize("killed,survived,no_tests,expected", [
    (0, 0, 1, 2), (1, 0, 1, 2), (0, 1, 0, 1), (1, 0, 0, 0), (2, 1, 0, 1),
])
def test_cli_rejects_empty_scores_and_compares_unrounded_threshold(
    widget, monkeypatch, killed, survived, no_tests, expected,
):
    monkeypatch.setattr(mutation, "load_mutants", lambda _: [])
    monkeypatch.setattr("grf.pipeline.drift.collect_tests", lambda _: [])
    entries = ([{"status": "killed"}] * killed + [{"status": "survived"}] * survived
               + [{"status": "noTests"}] * no_tests)
    monkeypatch.setattr(mutation, "mutate", lambda *args, **kwargs: {
        "mutants": [], "score": mutation.score(entries),
    })
    assert main(["mutate", "--outdir", str(widget.root / "out"), "--min-score", "0.6668"]) == expected
