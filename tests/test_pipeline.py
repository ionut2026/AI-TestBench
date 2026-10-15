"""Neutral catalog/drift/migration contracts formerly exercised mainly by the consumer."""

import pytest

from grf.pipeline import drift, ingest


@pytest.fixture
def catalog(widget):
    scope = {"name": "neutral", "specifications": {"counter": [1, 2, 3, 4]},
             "not_testable": {"3": "overview"}, "deferred": {"4": "needs observability"}}
    specs = [{"ID": n, "Text": f"Counter behavior {n}", "State": "Approved",
              "Satisfies": ["10 (Requirement) [Accepted]"],
              "Described In": ["20 (User Story) Increment [Tested]"]} for n in range(1, 5)]
    stories = [{"ID": 20, "Summary": "Increment", "State": "Tested",
                "Relevant Explorative Test": ["30 (Explorative Test) Counter [Passed]"]}]
    return ingest.build_catalog(scope, specs, [{"ID": 10, "Text": "Count", "State": "Accepted"}], stories,
                                [{"ID": 30, "Description": "Preconditions:\npowered\nSteps:\nincrement\nExpected results:\none"}])


def test_hash_normalization_and_rich_text_are_deterministic():
    assert ingest.spec_hash("Counter\r\n  receives\tX") == ingest.spec_hash("Counter receives X")
    assert ingest.spec_hash("caf\u00e9") == ingest.spec_hash("cafe\u0301")
    text = "<!-- MKS HTML --><div>Counter&#160;receives <b>X</b>:</div><ul><li>send Y</li><li>stay</li></ul>"
    assert ingest.html_to_text(text) == "Counter receives X:\n\n- send Y\n\n- stay"
    assert ingest.normalize_text("\u201cX\u201d \u2013 it\u2019s\u200b ok") == "\"X\" - it's ok"
    assert ingest.html_to_text(None) == ingest.normalize_text(None) == ""


def test_reference_sections_and_product_message_pattern(widget):
    assert ingest.parse_ref("20 (User Story) Increment [Tested]") == {
        "id": 20, "type": "User Story", "summary": "Increment", "state": "Tested"}
    assert ingest.parse_ref("not a reference")["id"] is None
    assert ingest.parse_explorative_test("just do it\nand check")["steps"] == ["just do it", "and check"]
    widget.data["catalog"] = {"message_pattern": r"\b[A-Z]\w+(?:Request|Response)\b"}
    text = "IncrementRequest returns IncrementResponse then IncrementResponse"
    assert ingest.message_names(text) == ["IncrementRequest", "IncrementResponse"]
    assert ingest.message_names(text, {"IncrementResponse"}) == ["IncrementResponse"]
    assert ingest.classify_area("Counter increment", {"counter": ["increment"]}) == "counter"
    assert ingest.classify_area("Unrelated", {"counter": ["increment"]}) is None


def test_catalog_preserves_traceability_and_testability(catalog):
    assert catalog["specifications"]["1"]["satisfies"] == [10]
    assert catalog["specifications"]["1"]["userStories"] == [20]
    assert catalog["specifications"]["1"]["explorativeTests"] == [30]
    assert catalog["requirements"]["10"]["specifications"] == [1, 2, 3, 4]
    assert catalog["explorativeTests"]["30"]["of"] == [20]
    assert catalog["explorativeTests"]["30"]["expected"] == ["one"]
    assert catalog["specifications"]["3"]["testable"] is False
    assert catalog["specifications"]["4"]["deferredReason"] == "needs observability"
    assert [s["spec"] for s in drift.check(catalog, [])["uncovered"]] == [1, 2]


def test_state_and_link_baselines_require_explicit_acceptance(catalog):
    import copy

    previous = copy.deepcopy(catalog)
    del previous["specifications"]["1"]["baseline"]
    changed = catalog["specifications"]["1"]
    changed.update(state="Changed", satisfies=[10, 11])
    catalog["specifications"]["2"]["state"] = "Rejected"
    ingest.carry_baselines(previous, catalog)
    tests = [drift.TestRef(f"T{i}", "Root.Suite", "", [], specs=[i],
                           hash=catalog["specifications"][str(i)]["hash"][:8]) for i in (1, 2)]
    result = drift.check(catalog, tests)
    assert [entry["spec"] for entry in result["stateChanged"]] == [1, 2]
    assert result["linksChanged"][0]["satisfies"] == {"added": [11], "removed": []}
    assert [entry["spec"] for entry in result["retired"]] == [2]
    assert ingest.accept_changes(catalog, [1]) == [1]
    assert ingest.accept_changes(catalog, [1]) == []
    assert drift.check(catalog, tests)["linksChanged"] == []
    assert ingest.accept_changes(catalog, None) == [2]


def test_explicit_scope_areas_and_conflicts(widget):
    assert ingest.scope_specifications({"specifications": [1, 2]}) == {1: None, 2: None}
    with pytest.raises(ValueError, match="two areas"):
        ingest.scope_specifications({"specifications": {"first": [1], "second": [1]}})
    catalog = ingest.build_catalog({"areas": {"counter": ["increment"]}},
                                   [{"ID": 1, "Text": "Increment"}, {"ID": 2, "Text": "Other"}],
                                   [], [], [], assigned={2: "manual"})
    assert catalog["specifications"]["1"]["areaSource"] == "keywords"
    assert catalog["specifications"]["2"]["area"] == "manual"
    assert catalog["specifications"]["2"]["areaSource"] == "scope"


def test_multiple_specifications_need_individual_hashes(catalog):
    first, second = (catalog["specifications"][str(i)]["hash"][:8] for i in (1, 2))
    tests = [drift.TestRef("Multiple", "Root.Suite", "", [], specs=[1, 2], hash=first),
             drift.TestRef("Individual", "Root.Suite", "", [], specs=[1, 2], hashes={1: first, 2: second})]
    result = drift.check(catalog, tests)
    assert [(entry["test"], entry["spec"]) for entry in result["nohash"]] == [("Multiple", 1), ("Multiple", 2)]
    assert result["stale"] == []


def test_hash_migration_only_changes_unchanged_text_and_preserves_crlf(widget, tmp_path):
    old = {"specifications": {"1": {"hash": "aaaa1111", "text": "Same  text"},
                              "2": {"hash": "bbbb2222", "text": "Old"}}}
    new = {"specifications": {"1": {"hash": "1111aaaa", "text": "Same text"},
                              "2": {"hash": "2222bbbb", "text": "Changed"}}}
    moves, changed = drift.hash_migrations(old, new, ingest.normalize_text)
    assert [move["spec"] for move in moves] == [1] and changed == [2]
    suite = tmp_path / "example.robot"
    suite.write_bytes(b"    [Tags]    WID-1    spechash:aaaa1111\r\n"
                      b"    [Tags]    WID-1    WID-2    spechash:1:aaaa1111    spechash:2:bbbb2222\r\n")
    ledger = tmp_path / "reviews.toml"
    ledger.write_bytes(b'[[review]]\r\ntest = "Keep aaaa1111 in name"\r\nspechash = "1:aaaa1111,2:bbbb2222"\r\n')
    assert drift.migrate_hash_tags(moves, [suite], ledger) == {str(suite): 2, str(ledger): 1}
    assert suite.read_bytes().count(b"1111aaaa") == 2 and suite.read_bytes().count(b"\r\n") == 2
    assert b'test = "Keep aaaa1111 in name"\r\nspechash = "1:1111aaaa,2:bbbb2222"' in ledger.read_bytes()
