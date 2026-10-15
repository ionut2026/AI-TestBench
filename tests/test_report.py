import json

import pytest

from grf.evidence import PREFLIGHT, EvidenceCheck, EvidenceChecks
from grf.pipeline import drift, report
from grf.pipeline.drift import TestRef


@pytest.fixture
def evidence_case(widget, synthetic_evidence):
    spec = {
        "id": 1, "area": "counter", "text": "Increment the counter once", "hash": "abcdef123456",
        "state": "Accepted", "testable": True, "satisfies": [2], "userStories": [3], "explorativeTests": [],
    }
    catalog = {"specifications": {"1": spec}, "requirements": {"2": {}}, "userStories": {"3": {}}}
    known = [TestRef("Increment", "Suites.Counter", "", ["WID-1", "spechash:abcdef12"],
                     specs=[1], hash="abcdef12", fingerprint="sha256:" + "1" * 64)]
    executed = [{
        "name": "Increment", "suite": "Widget bench.Counter", "status": "PASS", "message": "", "elapsed": 0.1,
        "specs": [1], "hash": "abcdef12", "hashes": {}, "pending": False, "knownIssues": [],
    }]
    reviews = [{"test": "Increment", "suite": "Suites.Counter", "spechash": "abcdef12",
                "reviewer": "Owner", "verdict": "approved", "fingerprint": known[0].fingerprint}]
    return catalog, known, executed, reviews


def build(case, tier="bench"):
    catalog, known, executed, reviews = case
    preflight = EvidenceChecks(tuple(EvidenceCheck(name, True, "synthetic") for name in PREFLIGHT),
                               {"engine": "synthetic-fixture"})
    return report.build_report(catalog, executed, {"Tier": tier, "GRF Preflight": json.dumps(preflight.as_dict())},
                               known, reviews)


def test_verified_requires_current_human_approval(evidence_case):
    assert build(evidence_case)["coverage"]["verified"] == 1
    evidence_case[3].clear()
    result = build(evidence_case)
    assert result["specifications"][0]["verdict"] == "PASS"
    assert not result["specifications"][0]["verified"]
    assert result["review"] == {"tests": 1, "unreviewed": 1}


def test_passing_spec_tag_does_not_replace_explicit_assertion_coverage(evidence_case, widget, monkeypatch):
    monkeypatch.setattr(widget.plugin, "assertion_coverage", lambda *args: {})
    result = build(evidence_case)
    assert result["specifications"][0]["verdict"] == "PARTIAL"
    assert not result["specifications"][0]["verified"]
    assert "assertion coverage unavailable" in result["specifications"][0]["evidenceProblems"][0]


def test_legacy_hash_only_review_cannot_verify_current_content(evidence_case):
    evidence_case[3][0].pop("fingerprint")
    assert not build(evidence_case)["specifications"][0]["verified"]


@pytest.mark.parametrize("tier", ["sim", "unknown", "?", "sim-parallel"])
def test_mock_and_unknown_tiers_never_verified(evidence_case, tier):
    result = build(evidence_case, tier)
    assert not result["productEvidence"] and result["coverage"]["verified"] == 0


@pytest.mark.parametrize("issue", ["pending", "stale", "missing-hash", "state", "links", "retired", "suspect", "agent"])
def test_evidence_blockers(evidence_case, issue):
    catalog, known, executed, reviews = evidence_case
    spec = catalog["specifications"]["1"]
    if issue == "pending":
        known[0].tags.append("review:pending")
    elif issue == "stale":
        executed[0]["hash"] = "12345678"
    elif issue == "missing-hash":
        executed[0]["hash"] = None
    elif issue == "state":
        spec["baseline"] = {"state": "Draft", "satisfies": [2], "userStories": [3]}
    elif issue == "links":
        spec["baseline"] = {"state": "Accepted", "satisfies": [], "userStories": [3]}
    elif issue == "retired":
        spec["state"] = "Deleted"
    elif issue == "suspect":
        spec["suspectCount"] = 1
    elif issue == "agent":
        reviews[0]["reviewer"] = "agent:model"
    assert not build(evidence_case)["specifications"][0]["verified"]


def test_latest_current_hash_human_verdict_wins(evidence_case):
    _, known, _, reviews = evidence_case
    reviews.append({**reviews[0], "verdict": "rejected"})
    assert "rejected" in drift.check_reviews(known, reviews)[0]["reason"]
    assert not build(evidence_case)["specifications"][0]["verified"]
    reviews.append({**reviews[0], "verdict": "approved-with-notes"})
    assert build(evidence_case)["specifications"][0]["verified"]


def test_duplicate_names_in_different_suites_cannot_hide_missing_run(evidence_case):
    _, known, _, reviews = evidence_case
    known.append(TestRef("Increment", "Suites.Other", "", ["WID-1", "spechash:abcdef12"],
                         specs=[1], hash="abcdef12"))
    reviews.append({**reviews[0], "suite": "Suites.Other"})
    result = build(evidence_case)
    assert result["review"]["tests"] == 2
    assert result["specifications"][0]["verdict"] == "PARTIAL"
    assert result["specifications"][0]["notRun"] == ["Increment"]
    assert not result["specifications"][0]["verified"]


def test_dotted_root_names_do_not_change_test_identity(evidence_case):
    _, known, executed, _ = evidence_case
    known[0].suite_path = "Counter"
    executed[0]["suite"] = "Widget 2.0 bench.Counter"
    executed[0]["suitePath"] = "Counter"
    assert build(evidence_case)["specifications"][0]["verified"]


def test_legacy_reviews_require_unique_names(evidence_case):
    _, known, _, reviews = evidence_case
    reviews[0].pop("suite")
    assert not drift.check_reviews(known, reviews)
    known.append(TestRef("Increment", "Suites.Other", "", known[0].tags.copy(), specs=[1], hash="abcdef12"))
    assert len(drift.check_reviews(known, reviews)) == 2


def test_record_review_qualifies_suite(evidence_case, tmp_path):
    _, known, _, _ = evidence_case
    path = tmp_path / "reviews.toml"
    entry = drift.record_review(path, known[0], "Owner", "approved", "2026-10-14")
    assert entry["suite"] == known[0].suite
    loaded = drift.load_reviews(path)[0]
    assert loaded["suite"] == entry["suite"] and loaded["spechash"] == entry["spechash"]
    assert loaded["date"].isoformat() == entry["date"]
