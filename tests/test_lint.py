from grf.pipeline.lint import lint


def test_transitive_resource_imports_enforce_capability_rules(widget):
    widget.data["lint"] = {"tag_rules": [
        {"rule": 4, "tag": "needs:reset", "keywords": ["Reset Product"], "what": "resets the product"},
    ]}
    suites = widget.root / "robot" / "suites"
    resources = widget.root / "robot" / "resources"
    suites.mkdir(parents=True)
    resources.mkdir()
    (suites / "counter.robot").write_text("""
*** Settings ***
Resource    ../resources/outer.resource
*** Test Cases ***
Reset counter
    [Documentation]    Reset returns the counter to its initial value.
    [Tags]    nospec:robustness
    Outer Reset
""", encoding="utf-8")
    (resources / "outer.resource").write_text("""
*** Settings ***
Resource    inner.resource
*** Keywords ***
Outer Reset
    Inner Reset
""", encoding="utf-8")
    (resources / "inner.resource").write_text("""
*** Settings ***
Resource    outer.resource
*** Keywords ***
Inner Reset
    Reset Product
""", encoding="utf-8")
    violations = lint([suites])
    assert len(violations) == 1
    assert violations[0].rule == "GRF04"
    assert "not tagged needs:reset" in violations[0].message
