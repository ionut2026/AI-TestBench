import json

import pytest

from grf import cli
from grf.evidence import (
    FINAL,
    PREFLIGHT,
    RESULT,
    AssertionCoverage,
    EvidenceCheck,
    EvidenceChecks,
    EvidenceError,
    assess,
    require_checks,
)


def checks(names=RESULT, *, failed=None, identities=None):
    return EvidenceChecks(tuple(EvidenceCheck(name, name != failed, f"{name} observed") for name in names),
                          identities if identities is not None else {"engine": "synthetic-fixture"})


@pytest.mark.parametrize("value", [None, False, {}, "PASS"])
def test_unavailable_or_wrong_contract_is_not_success(value):
    with pytest.raises(ValueError):
        require_checks(value, PREFLIGHT)


@pytest.mark.parametrize("name", RESULT)
def test_each_integrity_obligation_is_mandatory(name):
    with pytest.raises(EvidenceError, match=name):
        require_checks(checks(failed=name), RESULT)
    with pytest.raises(EvidenceError, match=name):
        require_checks(checks(tuple(key for key in RESULT if key != name)), RESULT)


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_checks_require_exact_boolean(value):
    with pytest.raises(ValueError, match="boolean"):
        EvidenceCheck("startup", value, "observed")


def test_evidence_shape_identity_and_names_are_explicit():
    with pytest.raises(ValueError, match="unique"):
        EvidenceChecks((EvidenceCheck("x", True, "one"), EvidenceCheck("x", True, "two")), {"engine": "fake"})
    with pytest.raises(EvidenceError, match="identities"):
        require_checks(checks(identities={}), RESULT)
    assert EvidenceChecks.from_dict(checks().as_dict()) == checks()
    with pytest.raises(ValueError):
        EvidenceChecks.from_dict({**checks().as_dict(), "schema": True})
    identities = {"engine": "validated"}
    snapshot = checks(identities=identities)
    identities["engine"] = "changed"
    assert snapshot.identities["engine"] == "validated"
    with pytest.raises(TypeError):
        snapshot.identities["engine"] = "changed"


@pytest.mark.parametrize("value", ["", "null", "[]", "{}", '{"checks": [], "identities": {}}'])
def test_missing_or_malformed_run_preflight_is_invalid(widget, synthetic_evidence, value):
    result = assess(widget.plugin, "bench", [], {"GRF Preflight": value})
    assert any("preflight" in problem for problem in result.problems)


def test_result_identity_cannot_differ_from_preflight(widget, monkeypatch, synthetic_evidence):
    monkeypatch.setattr(widget.plugin, "evidence_checks",
                        lambda *args: checks(identities={"engine": "different-generation"}))
    result = assess(widget.plugin, "bench", [], {"GRF Preflight": json.dumps(synthetic_evidence.as_dict())})
    assert any("engine differs" in problem for problem in result.problems)


def test_only_passing_tests_can_supply_assertion_observations(widget, monkeypatch, synthetic_evidence):
    seen = []
    monkeypatch.setattr(widget.plugin, "assertion_coverage", lambda tests, meta: seen.extend(tests) or {
        1: AssertionCoverage(("response", "hardware-command"), ("response",)),
    })
    tests = [{"status": "PASS", "name": "passed"}, {"status": "FAIL", "name": "failed"}, {"status": "SKIP"}]
    result = assess(widget.plugin, "bench", tests, {"GRF Preflight": json.dumps(synthetic_evidence.as_dict())})
    assert not result.problems and seen == [tests[0]]
    assert "hardware-command" in result.coverage_problems([1])[0]
    assert "unavailable" in result.coverage_problems([2])[0]
    assert AssertionCoverage((), ()).problems()


def test_default_plugin_cannot_claim_evidence(widget):
    result = assess(widget.plugin, "bench", [{"status": "PASS", "specs": [1]}], {})
    assert result.problems and result.coverage_problems([1])


def test_evidence_preflight_runs_before_capability_discovery(widget, monkeypatch, capsys):
    monkeypatch.setattr(cli, "tier_capabilities", lambda *args: pytest.fail("no product/rig discovery before preflight"))
    assert cli.main(["run", "--tier", "bench", "--operator", "none"]) == 2
    assert "validation is unavailable" in capsys.readouterr().err


def test_development_preflight_failure_is_not_ignored(widget, monkeypatch):
    monkeypatch.setattr(widget.plugin, "evidence_preflight", lambda tier: checks(PREFLIGHT, failed="actual-target"))
    monkeypatch.setattr(cli, "tier_capabilities", lambda *args: pytest.fail("no target contact"))
    assert cli.main(["run", "--tier", "sim", "--operator", "none"]) == 2


def test_missing_final_integrity_cannot_be_suppressed_by_fail_on_none(
    widget, tmp_path, monkeypatch, capsys, synthetic_evidence,
):
    suite = tmp_path / "robot" / "suites" / "integrity.robot"
    suite.parent.mkdir(parents=True)
    suite.write_text("*** Test Cases ***\nSynthetic Only\n    No Operation\n", encoding="utf-8")
    env = tmp_path / "robot" / "environments" / "bench.py"
    env.parent.mkdir()
    env.write_text('TIER = "bench"\n', encoding="utf-8")
    monkeypatch.setattr(widget.plugin, "evidence_checks", lambda *args: checks(failed=FINAL[0]))
    monkeypatch.delenv("WIDGET_RIG_CONTROL", raising=False)
    assert cli.main(["run", "--tier", "bench", "--operator", "none", "--no-report", "--fail-on", "none",
                     "--outdir", str(tmp_path / "results" / "run")]) == 2
    assert "INVALID EVIDENCE" in capsys.readouterr().err
