from grf.pipeline import briefs, ingest
from grf.pipeline.drift import TestRef


def catalog():
    return ingest.build_catalog(
        {"name": "counter", "specifications": {"counter": [1, 2]}, "not_testable": {"2": "overview"}},
        [{"ID": 1, "Text": "Increment raises the counter", "State": "Approved",
          "Satisfies": ["10 (Requirement) [Accepted]"], "Described In": ["20 (User Story) Increment [Tested]"]},
         {"ID": 2, "Text": "Overview", "State": "Approved"}],
        [{"ID": 10, "Text": "Count", "State": "Accepted"}],
        [{"ID": 20, "Summary": "Increment", "Description": "As a user", "State": "Tested",
          "Relevant Explorative Test": ["30 (Explorative Test) Counter [Passed]"]}],
        [{"ID": 30, "Summary": "Counter", "Description": "Preconditions:\npowered\nSteps:\nincrement\nExpected results:\none"}])


def authoring(widget, tmp_path):
    rules = tmp_path / "rules.md"
    rules.write_text("No Sleep; current human review required.", encoding="utf-8")
    widget.data["paths"]["brief_rules"] = str(rules)


def test_brief_contains_traceability_rules_and_product_sections(widget, tmp_path, monkeypatch):
    authoring(widget, tmp_path)
    monkeypatch.setattr(widget.plugin, "brief_sections", lambda spec: ["## Product observation", "Counter value"])
    data = catalog()
    spec = data["specifications"]["1"]
    existing = [TestRef("Old", "Suites.Counter", "", ["WID-1", "review:pending"], specs=[1])]
    text = briefs.render_brief(data, spec, "KEYWORDS", existing, "robot/suites/counter/example.robot")
    for expected in ("# Generation brief: WID-1", f"`spechash:{spec['hash'][:8]}`", "`review:pending`",
                     "Increment raises the counter", "**REQ-10** [Accepted]: Count", "### US-20: Increment [Tested]",
                     "### ET-30: Counter", "- one", "## Product observation", "Counter value",
                     "`Old` (Suites.Counter) [review:pending]", "KEYWORDS", "No Sleep"):
        assert expected in text
    assert briefs.tags_for(spec, multi=True) == ["WID-1", f"spechash:1:{spec['hash'][:8]}"]


def test_brief_writes_only_selected_testable_specs(widget, tmp_path, monkeypatch):
    authoring(widget, tmp_path)
    monkeypatch.setattr(briefs, "keyword_docs", lambda: "KEYWORDS")
    paths = briefs.write_briefs(catalog(), tmp_path / "briefs", [], scope_name="example")
    assert [path.name for path in paths] == ["WID-1.md"]
    assert "Target: `robot/suites/example/counter.robot`" in paths[0].read_text(encoding="utf-8")
    assert briefs.write_briefs(catalog(), tmp_path / "empty", [], only=[2]) == []


def test_keyword_docs_uses_only_configured_library_and_resources(widget, tmp_path):
    resource = tmp_path / "counter.resource"
    resource.write_text("*** Keywords ***\nObserve Counter\n    [Documentation]    Read the counter.\n    No Operation\n",
                        encoding="utf-8")
    widget.data["paths"]["keyword_docs"] = ["grf.library.GrfLibrary", str(resource)]
    text = briefs.keyword_docs()
    assert "`Get Time Between`" in text and "`Observe Counter`" in text and "Read the counter." in text
