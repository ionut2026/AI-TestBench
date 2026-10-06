from smm_automation.pipeline import briefs
from smm_automation.pipeline.drift import TestRef
from smm_automation.pipeline.ingest import build_catalog


def _catalog():
    scope = {"name": "t", "not_testable": {"3": "overview"}, "areas": {"init": ["Initialization"]}}
    specs = [
        {"ID": 1, "Text": "After InitializationRequest the SMM sends InitializationResponse.", "State": "Approved",
         "Satisfies": ["10 (Requirement) [Accepted]"], "Described In": ["20 (ASD-User Story) Init [Tested]"]},
        {"ID": 3, "Text": "Overview", "State": "Approved"},
    ]
    reqs = [{"ID": 10, "Text": "The SMM shall initialize", "State": "Accepted"}]
    stories = [{"ID": 20, "Summary": "Init", "State": "Tested", "Description": "As a user ...",
                "Relevant Explorative Test": ["30 (Explorative Test) ET [Passed]"]}]
    ets = [{"ID": 30, "Summary": "ET", "Description": "Preconditions:\npowered\nSteps:\nsend\nExpected results:\nok"}]
    return build_catalog(scope, specs, reqs, stories, ets)


def test_render_brief_holds_everything_the_author_needs(fake_icd):
    fake_icd["InitializationResponse"] = {"type": "object"}
    cat = _catalog()
    spec = cat["specifications"]["1"]
    existing = [TestRef("SDS-1 Old", "init", "init.robot", ["SDS-1", "review:pending"], [1], spec["hash"][:8])]
    text = briefs.render_brief(cat, spec, "KEYWORDS", existing, "robot/suites/t/init.robot")
    assert text.startswith("# Generation brief: SDS-1")
    assert f"`SDS-1`    `spechash:{spec['hash'][:8]}`    `review:pending`" in text
    assert "After InitializationRequest the SMM sends InitializationResponse." in text
    assert "**REQ-10** [Accepted]: The SMM shall initialize" in text
    assert "### US-20: Init [Tested]" in text and "### ET-30: ET" in text and "- ok" in text
    assert '### InitializationResponse\n\n```json\n{\n  "type": "object"\n}' in text
    assert "### InitializationRequest" not in text  # no schema for it
    assert "`SDS-1 Old` (init) [review:pending]" in text
    assert "KEYWORDS" in text and "No `Sleep`" in text and "${SDS_<id>_LIMIT}" in text


def test_write_briefs_skips_untestable_and_filters(monkeypatch, tmp_path):
    monkeypatch.setattr(briefs, "keyword_docs", lambda: "KEYWORDS")
    written = briefs.write_briefs(_catalog(), tmp_path / "out", [], scope_name="t")
    assert [p.name for p in written] == ["SDS-1.md"]
    assert "Target: `robot/suites/t/init.robot`" in written[0].read_text(encoding="utf-8")
    assert briefs.write_briefs(_catalog(), tmp_path / "out2", [], only=[3]) == []


def test_keyword_docs_lists_library_and_resource_keywords():
    docs = briefs.keyword_docs()
    assert "`Interrupt Bridge Connection`" in docs
    assert "`Open SMM Test Environment`" in docs
