import pytest
from robot.api import SkipExecution

from smm_automation import FRAMEWORK_ROOT
from smm_automation.pipeline import matrix
from smm_automation.pipeline.drift import TestRef, check, collect_tests
from smm_automation.pipeline.ingest import load_catalog
from smm_automation.pipeline.lint import lint
from smm_automation.pipeline.report import build_report, render_html
from smm_automation.SMMTestbench import SMMTestbench, _open_part, _response_errors

CATALOG = {"specifications": {
    "1": {"text": "appSMM answers ${x} with a Response.", "hash": "11111111aaaa"},
    "2": {"text": "Second spec.", "hash": "22222222bbbb"},
}}


def _model(*cells: matrix.Cell) -> matrix.Model:
    return matrix.Model(["NotInitialized", "Idle"], ["PingRequest"], list(cells))


def _cell(state="Idle", **kw) -> matrix.Cell:
    return matrix.Cell(state=state, request="PingRequest", **kw)


# ---------------------------------------------------------------------- model and generator


def test_repository_matrix_is_valid_and_generated_files_are_current():
    model = matrix.load_model(FRAMEWORK_ROOT / "catalog" / "state-matrix.toml")
    catalog = load_catalog(FRAMEWORK_ROOT / "catalog" / "pilot.json")
    tests = collect_tests([FRAMEWORK_ROOT / "robot" / "suites"])
    assert matrix.validate(model, catalog, tests) == []
    from smm_automation.pipeline.drift import load_reviews

    outputs = matrix.generate(model, catalog, load_reviews(FRAMEWORK_ROOT / "catalog" / "reviews.toml"))
    paths = {"suite": FRAMEWORK_ROOT / "robot" / "suites" / "pilot" / "state_matrix.robot",
             "questions": FRAMEWORK_ROOT / "catalog" / "state-matrix.questions.md"}
    assert matrix.write(outputs, paths, check=True) == []


def test_validate_reports_model_problems():
    model = matrix.Model(["Idle", "E-Stop"], ["PingRequest"], [
        _cell(response="PingResponse"),  # expectation without specification
        _cell(specs=[9], response="Ping | X=1"),  # unknown spec, not a Response name
        _cell(state="Bogus", questions=["q"]),
    ])
    errors = matrix.validate(model, CATALOG, [TestRef("other", "S", "s", [])])
    assert any("needs the specification" in e for e in errors)
    assert any("SDS-9 is not in the catalog" in e for e in errors)
    assert any("is not '<Name>Response" in e for e in errors)
    assert any("unknown state Bogus" in e for e in errors)
    assert "PingRequest in Idle: 2 cells (one cell per state x request)" in errors
    assert "PingRequest in E-Stop: missing (one cell per state x request)" in errors
    lonely = matrix.Model(["Idle"], ["PingRequest"], [_cell(covered_by=["gone"]), ])
    assert matrix.validate(lonely, CATALOG, []) == ["PingRequest in Idle: covered_by test 'gone' does not exist"]
    assert matrix.validate(matrix.Model(["Idle"], ["PingRequest"], [_cell()]), CATALOG) == [
        "PingRequest in Idle: neither an expectation nor an open question"]


def test_load_model_rejects_unknown_fields(tmp_path):
    path = tmp_path / "m.toml"
    path.write_text('states = ["Idle"]\nrequests = ["PingRequest"]\n[[cell]]\nstate = "Idle"\nrequest = "PingRequest"\nrespons = "x"\n',
                    encoding="utf-8")
    with pytest.raises(matrix.MatrixError, match="respons"):
        matrix.load_model(path)


def test_render_suite_tags_documentation_and_values():
    model = _model(
        _cell(state="NotInitialized", specs=[1, 2], response="PingResponse | Status=OK", next="unchanged",
              questions=["What about ${y}?"]),
        _cell(questions=["Should it answer?"]),
        _cell(state="E-Stop", specs=[1], covered_by=["hand written"]),
    )
    model.states.append("E-Stop")
    suite = matrix.render_suite(model, CATALOG, [])
    assert "SDS-1 Matrix PingRequest In NotInitialized" in suite
    assert "SDS-1    SDS-2    spechash:1:11111111    spechash:2:22222222    requires:restart    review:pending" in suite
    assert "answers \\${x} with" in suite and "What about \\${y}?" in suite  # variables are escaped
    assert "NotInitialized    PingRequest    ${EMPTY}    PingResponse | Status=OK    unchanged    ${EMPTY}" in suite
    assert "Matrix PingRequest In Idle\n" in suite
    assert "[Tags]    nospec:state-matrix    review:pending" in suite
    assert "In E-Stop" not in suite  # the covered cell is not generated
    assert all(len(line) <= 140 for line in suite.splitlines())
    questions = matrix.render_questions(model, CATALOG)
    assert "| PingRequest | generated (partly specified) | **unspecified** | covered |" in questions
    assert "1. What about ${y}?" in questions and "2. Should it answer?" in questions


def test_a_human_review_removes_review_pending():
    model = _model(_cell(specs=[1], response="PingResponse"))
    reviews = [{"test": "SDS-1 Matrix PingRequest In Idle", "spechash": "11111111", "reviewer": "jdoe"}]
    assert "review:pending" not in matrix.render_suite(model, CATALOG, reviews)
    agent = [{**reviews[0], "reviewer": "agent:smm-test-reviewer"}]
    assert "review:pending" in matrix.render_suite(model, CATALOG, agent)


def test_write_and_check(tmp_path):
    paths = {"suite": tmp_path / "s.robot", "questions": tmp_path / "q.md"}
    outputs = {"suite": "a\n", "questions": "b\n"}
    assert matrix.write(outputs, paths, check=True) == list(paths.values())
    assert not paths["suite"].exists()
    matrix.write(outputs, paths)
    paths["questions"].write_bytes(b"b\r\n")  # a CRLF checkout is still current
    assert matrix.write(outputs, paths, check=True) == []
    assert matrix.write({**outputs, "suite": "changed\n"}, paths, check=True) == [paths["suite"]]


# ---------------------------------------------------------------------- library: judging an outcome


def _outcome(response=None, notifications=(), settled="Idle"):
    return {"request": "PingRequest", "response": response, "notifications": list(notifications), "settled": settled}


PING_OK = {"name": "PingResponse", "topic": "/is/iw/tx", "body": {"Status": "OK", "Lane": {"Id": 2}}}


def test_response_errors():
    assert _response_errors(PING_OK, "PingResponse | Status=OK | Lane.Id=2 | @topic=/is/iw/tx") == []
    assert _response_errors(PING_OK, "PingResponse | Status=Error | @topic=/is/iw/rx") == [
        'PingResponse Status is "OK", expected "Error"', 'PingResponse @topic is "/is/iw/tx", expected "/is/iw/rx"']
    assert _response_errors(None, "PingResponse") == ["no PingResponse received"]
    assert _response_errors(PING_OK, "OtherResponse") == ["got PingResponse, expected OtherResponse"]


def test_open_part():
    assert _open_part("Cell text.\n    Open: a? /\n    b?") == " a? / b?"
    assert _open_part("no questions") == ""


def test_request_outcome_should_match(monkeypatch):
    lib = SMMTestbench(autostart=False)
    warnings = []
    monkeypatch.setattr("smm_automation.SMMTestbench.logger.warn", warnings.append)
    lib.request_outcome_should_match(_outcome(PING_OK), "Idle", "PingResponse | Status=OK", "unchanged", "Doc. Open: q1 / q2")
    assert len(warnings) == 1 and "q1 / q2" in warnings[0] and "PingResponse" in warnings[0]
    with pytest.raises(AssertionError, match="state changed"):
        lib.request_outcome_should_match(_outcome(PING_OK, ["Idle -> E-Stop"], "E-Stop"), "Idle", next_state="unchanged")
    with pytest.raises(AssertionError, match="no answer is allowed"):
        lib.request_outcome_should_match(_outcome(PING_OK), "Idle", response="none")
    lib.request_outcome_should_match(_outcome(None, ["Idle -> E-Stop"], "E-Stop"), "Idle", response="none", next_state="E-Stop")
    with pytest.raises(AssertionError, match="Idle not reached"):
        lib.request_outcome_should_match(_outcome(None, [], "E-Stop"), "E-Stop", next_state="Idle")
    with pytest.raises(SkipExecution, match="Not specified; observed: PingResponse"):
        lib.request_outcome_should_match(_outcome(PING_OK), "Idle", questions="Open: should it answer?")
    lib.request_outcome_should_match(_outcome(PING_OK), "Idle", checked=True)  # a COP check was made


# ---------------------------------------------------------------------- lint, drift and report of nospec / templated tests


def test_lint_templated_and_nospec_tests(tmp_path):
    (tmp_path / "x.resource").write_text("*** Keywords ***\nCell\n    [Arguments]    ${a}\n    Restart appSMM\n", encoding="utf-8")
    suite = tmp_path / "s.robot"
    suite.write_text(
        "*** Settings ***\nResource    x.resource\nTest Template    Cell\n\n*** Test Cases ***\n"
        "Restarting Cell Without Tag\n    [Documentation]    Observes.\n    [Tags]    nospec:robustness\n    1\n\n"
        "Nospec Without Documentation\n    [Tags]    nospec:robustness    requires:restart\n    1\n\n"
        "SDS-1 Not A Template\n    [Documentation]    appSMM answers x with a Response.\n    [Tags]    SDS-1\n"
        "    [Template]    NONE\n    Log    x\n",
        encoding="utf-8")
    found = {(v.item, v.rule) for v in lint([suite], CATALOG)}
    assert found == {("Restarting Cell Without Tag", "SMM05"), ("Nospec Without Documentation", "SMM02")}


def test_drift_lists_nospec_tests_apart_from_untagged():
    tests = [TestRef("robust", "S", "s", ["nospec:robustness"]), TestRef("bare", "S", "s", [])]
    r = check({"specifications": {}}, tests)
    assert [n["test"] for n in r["nospec"]] == ["robust"] and r["nospec"][0]["kind"] == "robustness"
    assert [u["test"] for u in r["untagged"]] == ["bare"]


def test_report_lists_nospec_tests():
    run = [{"name": "robust", "suite": "S", "status": "SKIP", "message": "Not specified; observed: x", "elapsed": 1.0,
            "tags": ["nospec:state-matrix", "review:pending"], "specs": [], "hash": None, "hashes": {}, "pending": True,
            "knownIssues": [], "nospec": ["state-matrix"]}]
    report = build_report({"specifications": {}}, run, {"Tier": "offline"}, [])
    assert [(t["name"], t["kind"], t["outcome"], t["pending"]) for t in report["nospec"]] == [("robust", "state-matrix", "SKIP", True)]
    page = render_html(report)
    assert "Tests without a specification" in page and "Not specified; observed: x" in page


def test_pilot_robustness_and_matrix_suites_are_tagged():
    tests = {t.name: t for t in collect_tests([FRAMEWORK_ROOT / "robot" / "suites" / "pilot"])}
    nospec = [t for t in tests.values() if t.nospec]
    assert {k for t in nospec for k in t.nospec} == {"robustness", "state-matrix"}
    assert all(not t.specs for t in nospec)

