"""Run requests, the acceptance gate, content fingerprints of reviews and the local run reservation."""

import datetime as dt
import json
import multiprocessing
import textwrap
from pathlib import Path

import pytest

from grf import cli
from grf.acceptance import RequestError, evaluate, parse_request, require_policy, validate_policy
from grf.pipeline import drift
from grf.reservation import ReservationError, exclusive

SHA = "a" * 64
TOMORROW = dt.date.today() + dt.timedelta(days=1)


def request_data(**over):
    data = {
        "schema": 1, "id": "run-1", "action": "test", "tier": "bench",
        "selection": {"specs": [1], "min_tests": 1},
        "component": [{"name": "widgetd", "expected_version": "1.2.3"}],
        "authorization": {"owner": "Instrument Owner", "reference": "TICKET-1", "valid_until": TOMORROW},
    }
    data.update(over)
    return data


class Tier:
    def __init__(self, evidence):
        self.evidence = evidence


TIERS = {"sim": Tier(False), "bench": Tier(True)}


# ---------------------------------------------------------------- request schema

def test_valid_request():
    r = parse_request(request_data(), TIERS, ["widgetd"])
    assert r.tier == "bench" and r.components[0].expected_version == "1.2.3"
    assert any("Instrument Owner" in line for line in r.summary())


@pytest.mark.parametrize("change, message", [
    ({"schema": 2}, "schema"),
    ({"schema": True}, "schema"),
    ({"extra": 1}, "unknown field"),
    ({"tier": "sim"}, "not product evidence"),
    ({"tier": "nope"}, "unknown tier"),
    ({"action": "deploy"}, "action"),
    ({"id": "bad id"}, "id must"),
    ({"source_revision": "main"}, "source_revision"),
    ({"selection": {"min_tests": 0}}, "min_tests"),
    ({"selection": {"specs": ["1"]}}, "specs"),
    ({"component": [{"name": "other", "expected_version": "1"}]}, "unknown component"),
    ({"component": [{"name": "widgetd"}]}, "expected_version"),
    ({"component": [{"name": "widgetd", "expected_version": "1", "token": "x"}]}, "unknown field"),
    ({"action": "deploy-and-test", "component": [{"name": "widgetd", "build": 5}]}, "sha256"),
    ({"action": "deploy-and-test", "component": []}, "at least one"),
    ({"authorization": {"owner": "agent:copilot", "reference": "x", "valid_until": TOMORROW}}, "not an agent"),
    ({"authorization": {"owner": "Owner", "reference": "x", "valid_until": "tomorrow"}}, "TOML date"),
    ({"authorization": {"owner": "Owner", "valid_until": TOMORROW}}, "reference"),
])
def test_invalid_requests_are_rejected(change, message):
    with pytest.raises(RequestError, match=message):
        parse_request(request_data(**change), TIERS, ["widgetd"])


@pytest.mark.parametrize("policy,message", [
    ({"require_source_revision": True}, "source_revision"),
    ({"require_catalog_sha256": True}, "catalog_sha256"),
    ({"allow_known_failures": False}, "known_failures"),
    ({"literal_tests": True}, "wildcard"),
    ({"runtime_identity": {"widgetd": True}}, "runtime identity"),
])
def test_acceptance_policy_cannot_be_relaxed_by_request(policy, message):
    data = request_data(selection={"tests": ["Pattern*"]}, acceptance={"allow_known_failures": True},
                        component=[{"name": "widgetd", "runtime_identity": False}])
    with pytest.raises(RequestError, match=message):
        require_policy(parse_request(data), policy)


@pytest.mark.parametrize("policy", [[], {"typo": False}, {"literal_tests": "true"},
                                   {"runtime_identity": []}, {"runtime_identity": {"app": 1}}])
def test_malformed_acceptance_policy_fails_early(policy):
    with pytest.raises(ValueError):
        validate_policy(policy)


def test_project_validation_checks_acceptance_policy(widget):
    from grf.project import ProjectError

    widget.data["acceptance"] = {"policy": {"require_source_revision": "true"}}
    with pytest.raises(ProjectError, match="boolean"):
        widget.validate()


# ---------------------------------------------------------------- gate

SPEC = {"id": 1, "area": "counter", "text": "t", "hash": "abcdef123456", "state": "Accepted", "testable": True,
        "satisfies": [], "userStories": [], "explorativeTests": []}
FP = "sha256:" + "1" * 64


def case(tmp_path):
    catalog = {"generatedAt": dt.datetime.now(dt.UTC).isoformat(), "specifications": {"1": dict(SPEC)}}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    known = [drift.TestRef("Increment", "Suites.Counter", "", ["WID-1", "spechash:abcdef12"], specs=[1], hash="abcdef12",
                           suite_path="Counter", fingerprint=FP)]
    tests = [{"name": "Increment", "suite": "Widget bench.Counter", "suitePath": "Counter", "status": "PASS",
              "tags": ["WID-1", "spechash:abcdef12"], "specs": [1], "hash": "abcdef12", "hashes": {}, "pending": False,
              "knownIssues": []}]
    reviews = [{"test": "Increment", "suite": "Suites.Counter", "spechash": "abcdef12", "fingerprint": FP,
                "reviewer": "Owner", "verdict": "approved"}]
    return {"tests": tests, "meta": {"Tier": "bench"}, "known": known, "reviews": reviews, "catalog": catalog,
            "catalog_path": path, "records": [], "observed": {"widgetd": "1.2.3"},
            "matches": lambda c, e, o: e == o, "root": tmp_path}


def gate(request, kw):
    from grf import __version__

    digests = [{"file": f"record-{i}.json", "sha256": SHA} for i, _ in enumerate(kw["records"])]
    kw = {**kw, "meta": {"GRF Request ID": request.id, "GRF Request SHA256": request.sha256,
                        "GRF Version": __version__,
                        "GRF Deployment SHA256": json.dumps(sorted(d["sha256"] for d in digests), separators=(",", ":")),
                        **kw["meta"]}, "record_digests": digests, "output_sha256": SHA}
    result = evaluate(request, **kw)
    return result["status"], {c["name"]: c for c in result["checks"]}


def test_gate_accepts_complete_run(tmp_path):
    status, checks = gate(parse_request(request_data()), case(tmp_path))
    assert status == "ACCEPTED", checks


@pytest.mark.parametrize("mutate, check", [
    (lambda k: k["tests"].clear(), "selection"),
    (lambda k: k["tests"][0].update(status="FAIL"), "results"),
    (lambda k: k["tests"][0].update(status="SKIP"), "selection"),
    (lambda k: k["reviews"].clear(), "reviews"),
    (lambda k: k["reviews"][0].update(reviewer="agent:copilot"), "reviews"),
    (lambda k: k["reviews"][0].pop("fingerprint"), "reviews"),  # legacy review: not enough for acceptance
    (lambda k: k["reviews"][0].update(fingerprint="sha256:" + "2" * 64), "reviews"),  # content changed since review
    (lambda k: k["known"][0].tags.append("review:pending"), "reviews"),
    (lambda k: k["catalog"]["specifications"]["1"].update(hash="999999999999"), "drift"),
    (lambda k: k["catalog"]["specifications"]["1"].update(suspectCount=1), "drift"),
    (lambda k: k["meta"].update(Tier="sim"), "tier"),
    (lambda k: k["observed"].clear(), "component widgetd"),
    (lambda k: k["observed"].update(widgetd="9.9.9"), "component widgetd"),
    (lambda k: k["records"].append({"component": "widgetd", "outcome": "DEPLOYED"}), "component widgetd"),
    (lambda k: k["records"].append({"component": "other", "outcome": "DEPLOYED"}), "deployments"),
])
def test_gate_fails_closed(tmp_path, mutate, check):
    kw = case(tmp_path)
    mutate(kw)
    status, checks = gate(parse_request(request_data()), kw)
    assert status == "REJECTED"
    assert checks[check]["status"] == "FAIL", checks


def test_gate_rejects_expired_authorization_and_non_evidence(tmp_path):
    kw = case(tmp_path)
    request = parse_request(request_data())
    status, checks = gate(request, {**kw, "today": TOMORROW + dt.timedelta(days=1)})
    assert status == "REJECTED" and checks["authorization"]["status"] == "FAIL"
    status, checks = gate(request, {**kw, "evidence": False})
    assert status == "REJECTED" and checks["tier"]["status"] == "FAIL"


def test_gate_allowed_skip_and_known_failure(tmp_path):
    kw = case(tmp_path)
    other = drift.TestRef("Other", "Suites.Counter", "", ["WID-1", "spechash:abcdef12"], specs=[1], hash="abcdef12",
                          suite_path="Counter", fingerprint=FP)
    kw["known"].append(other)
    kw["reviews"].append({**kw["reviews"][0], "test": "Other"})
    kw["tests"].append({**kw["tests"][0], "name": "Other", "status": "SKIP"})
    kw["tests"][0].update(status="FAIL", knownIssues=["BUG-1"])
    assert gate(parse_request(request_data()), kw)[0] == "REJECTED"
    request = parse_request(request_data(acceptance={"allowed_skips": ["Other"], "allow_known_failures": True}))
    status, checks = gate(request, kw)
    assert status == "ACCEPTED", checks
    kw["tests"][0]["knownIssues"] = []
    assert gate(request, kw)[1]["results"]["status"] == "FAIL"


def test_gate_deploy_and_test_binds_build_and_sha(tmp_path):
    kw = case(tmp_path)
    data = request_data(action="deploy-and-test", component=[{"name": "widgetd", "build": 7, "sha256": SHA}])
    record = {"project": "Widget", "component": "widgetd", "outcome": "DEPLOYED", "dryRun": False,
              "installed": {"status": "running", "version": "1.2.3"},
              "artifact": {"build_number": 7, "sha256": SHA, "version": "1.2.3"}}
    kw["records"].append(record)
    assert gate(parse_request(data), kw)[0] == "ACCEPTED"
    record["artifact"]["build_number"] = 8
    status, checks = gate(parse_request(data), kw)
    assert status == "REJECTED" and "requested 7" in checks["component widgetd"]["detail"]
    record["artifact"].update(build_number=7, sha256="b" * 64)
    assert gate(parse_request(data), kw)[0] == "REJECTED"
    record["artifact"]["sha256"] = SHA
    record["dryRun"] = True
    assert gate(parse_request(data), kw)[0] == "REJECTED"


def test_installed_comparison_is_not_runtime_comparison(tmp_path):
    kw = case(tmp_path)
    kw["records"] = [{"component": "widgetd", "outcome": "DEPLOYED", "dryRun": False,
                     "artifact": {"build_number": 7, "sha256": SHA},
                     "installed": {"status": "running", "version": "1.2.3-main"}}]
    kw["matches"] = lambda component, expected, seen: expected == "1.2.3-main" and seen == "1.2.3"
    kw["observed"] = {"widgetd": "1.2.3"}
    data = request_data(action="deploy-and-test", component=[
        {"name": "widgetd", "build": 7, "sha256": SHA, "expected_version": "1.2.3-main"}])
    assert gate(parse_request(data), kw)[0] == "ACCEPTED"
    kw["installed_matches"] = lambda *args: False
    assert gate(parse_request(data), kw)[0] == "REJECTED"


def test_duplicate_identity_never_accepted(tmp_path):
    kw = case(tmp_path)
    kw["tests"].append(dict(kw["tests"][0]))
    assert gate(parse_request(request_data()), kw)[1]["test identities"]["status"] == "FAIL"


def test_duplicate_source_identity_rejected(widget, tmp_path):
    suite = tmp_path / "duplicate.robot"
    suite.write_text("*** Test Cases ***\nSame\n    No Operation\nSame\n    Log    different\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate test identity"):
        drift.collect_tests([suite])


@pytest.mark.parametrize("change", [
    {"installed": None},
    {"installed": {"status": "exited", "version": "1.2.3"}},
    {"installed": {"status": "running"}},
    {"installed": {"status": "running", "version": "wrong"}},
    {"project": "Other"},
    {"dryRun": None},
    {"artifact": "invalid"},
])
def test_gate_rejects_incomplete_or_unrelated_deployments(tmp_path, change):
    kw = case(tmp_path)
    record = {"project": "Widget", "component": "widgetd", "outcome": "DEPLOYED", "dryRun": False,
              "artifact": {"build_number": 7, "sha256": SHA, "version": "1.2.3"},
              "installed": {"status": "running", "version": "1.2.3"}}
    record.update(change)
    kw.update(records=[record], project_name="Widget")
    data = request_data(action="deploy-and-test", component=[{"name": "widgetd", "build": 7, "sha256": SHA}])
    assert gate(parse_request(data), kw)[0] == "REJECTED"


def test_gate_rejects_ambiguous_deployment_records(tmp_path):
    kw = case(tmp_path)
    kw["records"] = [{"component": "widgetd"}, {"component": "widgetd"}]
    data = request_data(action="deploy-and-test", component=[{"name": "widgetd", "build": 7, "sha256": SHA}])
    assert "exactly one" in gate(parse_request(data), kw)[1]["component widgetd"]["detail"]


def test_gate_named_selection_cannot_be_satisfied_by_allowed_skip(tmp_path):
    kw = case(tmp_path)
    kw["tests"][0]["status"] = "SKIP"
    data = request_data(selection={"tests": ["Increment"]}, acceptance={"allowed_skips": ["Increment"]})
    assert gate(parse_request(data), kw)[1]["selection"]["status"] == "FAIL"


def test_gate_catalog_binding(tmp_path):
    kw = case(tmp_path)
    assert gate(parse_request(request_data(catalog_sha256="c" * 64)), kw)[0] == "REJECTED"
    kw["catalog"]["generatedAt"] = "2000-01-01T00:00:00+00:00"
    status, checks = gate(parse_request(request_data(acceptance={"max_catalog_age_days": 30})), kw)
    assert status == "REJECTED" and "older than" in checks["catalog"]["detail"]


def test_gate_source_revision_without_git_fails(tmp_path):
    status, checks = gate(parse_request(request_data(source_revision="0" * 40)), case(tmp_path))
    assert status == "REJECTED" and checks["source"]["status"] == "FAIL"


# ---------------------------------------------------------------- fingerprints

SUITE = """\
*** Settings ***
Resource    ../common.resource

*** Test Cases ***
{padding}Increment
    [Tags]    WID-1    spechash:abcdef12{pending}
    Do It
"""


def write_suite(root: Path, padding="", pending="    review:pending", resource="Do It\n    No Operation\n"):
    (root / "suites").mkdir(exist_ok=True)
    (root / "common.resource").write_text(f"*** Keywords ***\n{resource}", encoding="utf-8")
    (root / "suites" / "counter.robot").write_text(SUITE.format(padding=padding, pending=pending), encoding="utf-8")
    return next(t for t in drift.collect_tests([root / "suites"]) if t.name == "Increment")


def test_fingerprint_ignores_position_pending_tag_and_location(widget, tmp_path):
    base = write_suite(tmp_path).fingerprint
    assert base and base.startswith("sha256:")
    assert write_suite(tmp_path, padding="Other\n    No Operation\n\n").fingerprint == base
    assert write_suite(tmp_path, pending="").fingerprint == base
    elsewhere = tmp_path / "copy"
    elsewhere.mkdir()
    assert write_suite(elsewhere).fingerprint == base


def test_fingerprint_follows_test_and_resource_changes(widget, tmp_path):
    base = write_suite(tmp_path).fingerprint
    assert write_suite(tmp_path, resource="Do It\n    Log    changed\n").fingerprint != base
    (tmp_path / "suites" / "counter.robot").write_text(SUITE.format(padding="", pending="").replace("Do It", "Do It Twice"),
                                                       encoding="utf-8")
    assert drift.collect_tests([tmp_path / "suites"])[0].fingerprint != base


def test_review_records_fingerprint_and_content_change_invalidates(widget, tmp_path):
    test = write_suite(tmp_path, pending="")
    ledger = tmp_path / "reviews.toml"
    entry = drift.record_review(ledger, test, "Owner", "approved", "2026-01-01")
    assert entry["fingerprint"] == test.fingerprint
    reviews = drift.load_reviews(ledger)
    assert drift.check_reviews([test], reviews) == []
    changed = write_suite(tmp_path, pending="", resource="Do It\n    Log    changed\n")
    assert "changed after the human review" in drift.check_reviews([changed], reviews)[0]["reason"]
    legacy = [{k: v for k, v in reviews[0].items() if k != "fingerprint"}]
    assert drift.check_reviews([test], legacy) == []  # drift keeps accepting pre-0.3 reviews ...
    assert "fingerprint" in drift.review_problem(test, [test], legacy, content=True)  # ... the gate does not


# ---------------------------------------------------------------- reservation

def _hold(lock_dir, ready, release):
    with exclusive("bench", Path(lock_dir), "other process"):
        ready.set()
        release.wait(30)


def test_reservation_is_exclusive_across_processes(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    proc = ctx.Process(target=_hold, args=(str(tmp_path), ready, release))
    proc.start()
    try:
        assert ready.wait(60)
        with pytest.raises(ReservationError, match="other process"), exclusive("bench", tmp_path):
            pass
    finally:
        release.set()
        proc.join(60)
    with exclusive("bench", tmp_path):  # released when the holder ended
        with pytest.raises(ReservationError), exclusive("bench", tmp_path):
            pass


def test_reservation_rejects_bad_names(tmp_path):
    with pytest.raises(ReservationError), exclusive("../x", tmp_path):
        pass


# ---------------------------------------------------------------- CLI

def test_cli_request_check_and_gate(widget, tmp_path, capsys):
    req = tmp_path / "request.toml"
    req.write_text(textwrap.dedent(f"""\
        schema = 1
        id = "bench-1"
        action = "test"
        tier = "bench"
        [selection]
        specs = [1]
        [authorization]
        owner = "Instrument Owner"
        reference = "TICKET-1"
        valid_until = {TOMORROW.isoformat()}
        """), encoding="utf-8")
    assert cli.main(["request", "check", str(req)]) == 0
    assert "Request is valid" in capsys.readouterr().out
    normalized = tmp_path / "normalized.json"
    assert cli.main(["request", "check", str(req), "--json", str(normalized)]) == 0
    data = json.loads(normalized.read_text(encoding="utf-8"))
    assert data["selection"] == {"specs": [1], "tests": [], "min_tests": 1}
    assert data["acceptance"] == {"allowed_skips": [], "allow_known_failures": False}
    assert data["authorization"]["valid_until"] == TOMORROW.isoformat()
    (tmp_path / "bad.toml").write_text(req.read_text(encoding="utf-8").replace('"bench"', '"sim"'), encoding="utf-8")
    assert cli.main(["request", "check", str(tmp_path / "bad.toml")]) == 1
    assert "not product evidence" in capsys.readouterr().err
    assert cli.main(["request", "check", str(tmp_path / "bad.toml"), "--json", str(tmp_path / "invalid.json")]) == 1
    assert not (tmp_path / "invalid.json").exists()

    suites = tmp_path / "robot" / "suites"
    suites.mkdir(parents=True)
    (suites / "counter.robot").write_text("*** Test Cases ***\nIncrement\n    [Tags]    WID-1    spechash:abcdef12\n"
                                          "    No Operation\n", encoding="utf-8")
    (tmp_path / "catalog").mkdir()
    catalog = tmp_path / "catalog" / "demo.json"
    catalog.write_text(json.dumps({"generatedAt": dt.datetime.now(dt.UTC).isoformat(), "specifications": {"1": SPEC}}),
                       encoding="utf-8")
    out = tmp_path / "results" / "bench"
    environment = tmp_path / "robot" / "environments"
    environment.mkdir()
    (environment / "bench.py").write_text("", encoding="utf-8")
    assert cli.main(["--catalog", str(catalog), "run", "--request", str(req),
                     "--operator", "none", "--outdir", str(out)]) == 0
    assert (out / "request.toml").read_bytes() == req.read_bytes()
    gate_args = ["--catalog", str(catalog), "gate", "--request", str(req), "--output", str(out / "output.xml")]
    assert cli.main(gate_args) == 1  # no human review yet
    text = capsys.readouterr().out
    assert "REJECTED" in text and "no human review" in text
    test = drift.collect_tests([suites])[0]
    drift.record_review(tmp_path / "catalog" / "reviews.toml", test, "Owner", "approved", "2026-01-01")
    assert cli.main(gate_args) == 0, capsys.readouterr().out
    result = json.loads((out / "acceptance.json").read_text(encoding="utf-8"))
    assert result["status"] == "ACCEPTED" and result["executed"] == 1
    assert result["requestSha256"] and result["outputSha256"] and result["framework"]["grf"]


@pytest.mark.parametrize("metadata", [
    {"GRF Request ID": "another-request"}, {"GRF Request SHA256": "b" * 64},
    {"GRF Deployment SHA256": '["wrong"]'}, {"GRF Version": "old"},
])
def test_gate_rejects_missing_or_mismatched_run_provenance(tmp_path, metadata):
    kw = case(tmp_path)
    kw["meta"].update(metadata)
    assert gate(parse_request(request_data()), kw)[1]["provenance"]["status"] == "FAIL"


def test_unbound_legacy_output_is_rejected(tmp_path):
    result = evaluate(parse_request(request_data()), **case(tmp_path), output_sha256=SHA)
    assert result["status"] == "REJECTED"
    assert next(c for c in result["checks"] if c["name"] == "provenance")["status"] == "FAIL"


def test_request_file_digest_covers_exact_snapshot_bytes(tmp_path):
    from grf.acceptance import load_request

    path = tmp_path / "request.toml"
    content = f'schema = 1\nid = "bound"\naction = "test"\ntier = "bench"\n[authorization]\n' \
              f'owner = "Owner"\nreference = "TICKET"\nvalid_until = {TOMORROW}\n'
    path.write_text(content, encoding="utf-8")
    first = load_request(path)
    path.write_text(content + "\n# Reviewed change\n", encoding="utf-8")
    second = load_request(path)
    assert first.as_dict() == second.as_dict() and first.sha256 != second.sha256
    assert first.source_bytes != second.source_bytes


def test_run_holds_tier_reservation(widget, tmp_path, capsys):
    from grf import project

    assert project.current().tier("bench").lock == "bench"
    assert project.current().tier("sim").lock is None  # parallel tier: no reservation
    (tmp_path / "robot" / "environments").mkdir(parents=True)
    (tmp_path / "robot" / "environments" / "bench.py").write_text("", encoding="utf-8")
    (tmp_path / "robot" / "suites").mkdir(parents=True)
    with exclusive("bench", tmp_path / "results" / ".locks", "holder"):
        assert cli.main(["run", "--tier", "bench", "--operator", "none", "--no-report",
                         "--outdir", str(tmp_path / "results" / "x")]) == 2
    assert "in use by another run" in capsys.readouterr().err
