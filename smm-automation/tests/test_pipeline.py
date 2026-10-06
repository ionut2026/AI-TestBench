from smm_automation.pipeline.drift import TestRef, check, check_reviews, problems
from smm_automation.pipeline.ingest import (
    build_catalog,
    classify_area,
    diff_catalogs,
    message_names,
    normalize_text,
    parse_explorative_test,
    parse_ref,
    spec_hash,
)
from smm_automation.pipeline.report import build_report, classify, outcome, render_html


def test_spec_hash_ignores_whitespace_and_unicode_form():
    assert spec_hash("If  the SMM\r\n receives\tX") == spec_hash("If the SMM receives X")
    assert spec_hash("caf\u00e9") == spec_hash("cafe\u0301")
    assert spec_hash("a") != spec_hash("b")
    assert normalize_text(None) == ""


def test_parse_ref():
    assert parse_ref("2386628 (Requirement) [Accepted]") == {"id": 2386628, "type": "Requirement", "summary": None, "state": "Accepted"}
    story = parse_ref("2404051 (ASD-User Story) StartUp: power on [Tested]")
    assert story["id"] == 2404051 and story["summary"] == "StartUp: power on" and story["state"] == "Tested"
    assert parse_ref("garbage")["id"] is None


def test_parse_explorative_test_sections():
    et = parse_explorative_test("Preconditions:\nSMM powered\n\nSteps:\n1. send X\n2. wait\nExpected result:\nY is sent\n")
    assert et["preconditions"] == ["SMM powered"]
    assert et["steps"] == ["1. send X", "2. wait"]
    assert et["expected"] == ["Y is sent"]
    assert parse_explorative_test("just do it\nand check")["steps"] == ["just do it", "and check"]


def test_message_names_and_area():
    text = "After InitializationResponse the SMM sends SystemStatusNotification and FooRequest."
    assert message_names(text) == ["InitializationResponse", "SystemStatusNotification", "FooRequest"]
    assert message_names(text, {"SystemStatusNotification"}) == ["SystemStatusNotification"]
    areas = {"bridge": ["SMMBridge"], "init": ["Initialization"]}
    assert classify_area("lost the smmbridge", areas) == "bridge"
    assert classify_area("nothing", areas) is None


def _item(id, text, **extra):
    return {"ID": id, "Text": text, "State": "Approved", **extra}


def _catalog():
    scope = {"name": "t", "not_testable": {"3": "overview"}, "deferred": {"4": "needs fault injection"}, "areas": {"init": ["Initialization"]}}
    specs = [
        _item(1, "InitializationRequest starts init", Satisfies=["10 (Requirement) [Accepted]"], **{"Described In": ["20 (ASD-User Story) Init [Tested]"]}),
        _item(2, "Other behaviour", Satisfies=["10 (Requirement) [Accepted]"]),
        _item(3, "Overview"),
        _item(4, "Initialization timeout"),
    ]
    reqs = [_item(10, "The SMM shall initialize")]
    stories = [{"ID": 20, "Summary": "Init", "State": "Tested", "Relevant Explorative Test": ["30 (Explorative Test) ET [Passed]"]}]
    ets = [{"ID": 30, "Summary": "ET", "Description": "Steps:\nsend\nExpected results:\nok"}]
    return build_catalog(scope, specs, reqs, stories, ets)


def test_build_catalog_links():
    cat = _catalog()
    s1 = cat["specifications"]["1"]
    assert s1["area"] == "init" and s1["satisfies"] == [10] and s1["userStories"] == [20]
    assert s1["explorativeTests"] == [30]
    assert cat["specifications"]["3"]["testable"] is False
    assert cat["specifications"]["4"]["deferredReason"] == "needs fault injection"
    assert cat["requirements"]["10"]["specifications"] == [1, 2]
    assert cat["explorativeTests"]["30"]["of"] == [20]
    assert cat["explorativeTests"]["30"]["expected"] == ["ok"]


def test_diff_catalogs():
    old = {"generatedAt": "x", "specifications": {"1": {"hash": "a", "state": "A"}, "2": {"hash": "b", "state": "A"}}}
    new = {"specifications": {"1": {"hash": "c", "state": "B"}, "3": {"hash": "d", "state": "A"}}}
    assert diff_catalogs(old, new) == {"since": "x", "added": [3], "removed": [2], "textChanged": [1], "stateChanged": [1]}


def _ref(name, specs, hash=None, tags=()):
    return TestRef(name=name, suite="S", source="s.robot", tags=list(tags), specs=specs, hash=hash)


def test_drift_check():
    cat = _catalog()
    h1 = cat["specifications"]["1"]["hash"][:8]
    tests = [
        _ref("ok", [1], h1, ["review:pending"]),
        _ref("stale", [1], "deadbeef"),
        _ref("orphan", [99], "00000000"),
        _ref("untagged", []),
        _ref("nohash", [1]),
    ]
    r = check(cat, tests)
    assert [s["test"] for s in r["stale"]] == ["stale"]
    assert [o["spec"] for o in r["orphan"]] == [99]
    assert [u["test"] for u in r["untagged"]] == ["untagged"]
    assert [n["test"] for n in r["nohash"]] == ["nohash"]
    assert [u["spec"] for u in r["uncovered"]] == [2]  # 3 is not testable, 4 is deferred
    assert [p["test"] for p in r["pending"]] == ["ok"]
    assert problems(r) == 5


def _result(name, specs, status, hash=None, tags=()):
    tags = list(tags)
    return {
        "name": name, "suite": "S", "status": status, "message": "", "elapsed": 1.0, "tags": tags, "specs": specs, "hash": hash,
        "pending": "review:pending" in tags, "knownIssues": [t.split(":", 1)[1] for t in tags if t.startswith("known-issue:")],
    }


def test_report_verdicts_and_roll_up():
    cat = _catalog()
    h1 = cat["specifications"]["1"]["hash"][:8]
    known = [_ref("a", [1], h1), _ref("b", [1], h1), _ref("c", [2], "x")]
    run = [_result("a", [1], "PASS", h1), _result("b", [1], "FAIL", "0bad0bad")]
    report = build_report(cat, run, {"Tier": "rig"}, known)
    verdicts = {s["id"]: s["verdict"] for s in report["specifications"]}
    assert verdicts == {1: "FAIL", 2: "NOT RUN", 3: "NOT TESTABLE", 4: "DEFERRED"}
    s1 = next(s for s in report["specifications"] if s["id"] == 1)
    assert s1["stale"] == ["b"]
    assert report["requirements"][0]["verdict"] == "FAIL"
    assert report["userStories"][0]["verdict"] == "FAIL"
    assert report["productEvidence"] is True
    assert build_report(cat, run, {"Tier": "mock (self-test)"}, known)["productEvidence"] is False


def test_report_pass_and_uncovered():
    cat = _catalog()
    h1 = cat["specifications"]["1"]["hash"][:8]
    report = build_report(cat, [_result("a", [1], "PASS", h1)], {}, [_ref("a", [1], h1)])
    verdicts = {s["id"]: s["verdict"] for s in report["specifications"]}
    assert verdicts[1] == "PASS" and verdicts[2] == "UNCOVERED"
    assert report["requirements"][0]["verdict"] == "UNCOVERED"
    assert report["userStories"][0]["verdict"] == "PASS"


def _verdict(cat, run, known):
    return {s["id"]: s["verdict"] for s in build_report(cat, run, {"Tier": "offline"}, known)["specifications"]}[1]


def test_spec_verdict_combinations():
    cat = _catalog()
    h1 = cat["specifications"]["1"]["hash"][:8]
    a, b = _ref("a", [1], h1), _ref("b", [1], h1)
    assert _verdict(cat, [_result("a", [1], "PASS", h1), _result("b", [1], "PASS", h1)], [a, b]) == "PASS"
    assert _verdict(cat, [_result("a", [1], "PASS", h1)], [a, b]) == "PARTIAL"  # b not run on this tier
    assert _verdict(cat, [_result("a", [1], "PASS", h1), _result("b", [1], "SKIP", h1)], [a, b]) == "PARTIAL"
    assert _verdict(cat, [_result("a", [1], "SKIP", h1), _result("b", [1], "SKIP", h1)], [a, b]) == "SKIP"
    assert _verdict(cat, [_result("a", [1], "PASS", h1), _result("b", [1], "FAIL", h1)], [a, b]) == "FAIL"
    assert _verdict(cat, [_result("a", [1], "SKIP", h1), _result("b", [1], "FAIL", h1)], [a, b]) == "FAIL"
    assert _verdict(cat, [], [a, b]) == "NOT RUN"
    assert _verdict(cat, [], []) == "UNCOVERED"


def test_review_status_and_verified():
    cat = _catalog()
    h1 = cat["specifications"]["1"]["hash"][:8]
    pending = _ref("a", [1], h1, ["review:pending"])
    report = build_report(cat, [_result("a", [1], "PASS", h1, ["review:pending"])], {"Tier": "offline"}, [pending])
    s1 = next(s for s in report["specifications"] if s["id"] == 1)
    assert s1["verdict"] == "PASS" and s1["verified"] is False and s1["unreviewed"] == ["a"]
    assert report["coverage"]["verified"] == 0 and report["review"] == {"tests": 1, "unreviewed": 1}
    assert "UNREVIEWED" in render_html(report)
    reviewed = build_report(cat, [_result("a", [1], "PASS", h1)], {"Tier": "offline"}, [_ref("a", [1], h1)])
    assert next(s for s in reviewed["specifications"] if s["id"] == 1)["verified"] is True
    assert reviewed["coverage"]["verified"] == 1


def test_known_issue_outcomes():
    tests = [
        _result("new", [1], "FAIL"),
        _result("known", [1], "FAIL", tags=["known-issue:FINDING-1"]),
        _result("fixed", [2], "PASS", tags=["known-issue:FINDING-2"]),
        _result("ok", [2], "PASS"),
        _result("skipped", [2], "SKIP", tags=["known-issue:FINDING-2"]),
    ]
    assert [outcome(t) for t in tests] == ["NEW FAIL", "KNOWN FAIL", "FIXED?", "PASS", "SKIP"]
    assert classify(tests) == {"new": ["new"], "known": ["known"], "fixed": ["fixed"]}
    # on the mock tier known issues are ignored: the mock follows the specification, any failure is a regression
    assert classify(tests, product=False) == {"new": ["new", "known"], "known": [], "fixed": []}
    cat = _catalog()
    report = build_report(cat, tests[1:3], {"Tier": "offline"}, [])
    s1 = next(s for s in report["specifications"] if s["id"] == 1)
    assert s1["verdict"] == "FAIL" and s1["failClass"] == "known"
    assert build_report(cat, tests[:2], {"Tier": "offline"}, [])["specifications"][0]["failClass"] == "new"
    html = render_html(report)
    assert "KNOWN FAIL" in html and "FIXED?" in html


def test_review_ledger():
    tests = [
        _ref("pending", [1], "aaaa1111", ["review:pending", "spechash:aaaa1111"]),
        _ref("reviewed", [1], "bbbb2222", ["spechash:bbbb2222"]),
        _ref("unrecorded", [1], "cccc3333", ["spechash:cccc3333"]),
        _ref("outdated", [1], "dddd4444", ["spechash:dddd4444"]),
        _ref("agent only", [1], "eeee5555", ["spechash:eeee5555"]),
    ]
    ledger = [
        {"test": "reviewed", "spechash": "bbbb2222", "reviewer": "Jane Doe"},
        {"test": "outdated", "spechash": "00000000", "reviewer": "Jane Doe"},
        {"test": "agent only", "spechash": "eeee5555", "reviewer": "agent:gpt-6-sol"},
    ]
    out = check_reviews(tests, ledger)
    assert [o["test"] for o in out] == ["unrecorded", "outdated", "agent only"]
    assert "00000000" in out[1]["reason"]
    cat = _catalog()
    r = check(cat, tests, ledger)
    assert len(r["unrecorded"]) == 3
    assert check(cat, tests)["unrecorded"] == []  # no ledger given: not checked
