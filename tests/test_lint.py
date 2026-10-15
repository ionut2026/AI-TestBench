import pytest

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


def configure_rule(widget):
    widget.data["lint"] = {"tag_rules": [
        {"rule": 4, "tag": "needs:reset", "keywords": ["Reset Product"], "what": "resets the product"},
    ]}


def suite(path, settings, call, tags="nospec:robustness"):
    path.write_text(f"*** Settings ***\n{settings}\n*** Test Cases ***\nExample\n"
                    f"    [Documentation]    Synthetic observable behavior.\n    [Tags]    {tags}\n    {call}\n",
                    encoding="utf-8")


def test_curdir_robot_resources_are_followed(widget, tmp_path):
    configure_rule(widget)
    resource = tmp_path / "wrapper.robot"
    resource.write_text("*** Keywords ***\nWrapper\n    Reset Product\n", encoding="utf-8")
    path = tmp_path / "test.robot"
    suite(path, "Resource    ${CURDIR}${/}wrapper.robot", "Wrapper")
    assert any(v.rule == "GRF04" for v in lint([path]))


def test_unrelated_resource_namespaces_do_not_leak(widget, tmp_path):
    configure_rule(widget)
    for folder, body, tags in (("one", "Reset Product", "nospec:robustness    needs:reset"),
                               ("two", "No Operation", "nospec:robustness")):
        root = tmp_path / folder
        root.mkdir()
        (root / "common.resource").write_text(f"*** Keywords ***\nWrapper\n    {body}\n", encoding="utf-8")
        suite(root / "test.robot", "Resource    common.resource", "Wrapper", tags)
    assert lint([tmp_path]) == []


def test_duplicate_wrappers_cannot_overwrite_an_action(widget, tmp_path):
    configure_rule(widget)
    (tmp_path / "a.resource").write_text("*** Keywords ***\nWrapper\n    Reset Product\n", encoding="utf-8")
    (tmp_path / "b.resource").write_text("*** Keywords ***\nWrapper\n    No Operation\n", encoding="utf-8")
    path = tmp_path / "test.robot"
    suite(path, "Resource    a.resource\nResource    b.resource", "Wrapper")
    violations = lint([path])
    assert {v.rule for v in violations} == {"GRF00", "GRF04"}
    suite(path, "Resource    a.resource\nResource    b.resource", "b.Wrapper")
    assert lint([path]) == []


@pytest.mark.parametrize("setting", ["Test Setup", "Test Teardown", "Suite Setup", "Suite Teardown"])
def test_lifecycle_timing_is_checked(widget, tmp_path, setting):
    path = tmp_path / "test.robot"
    suite(path, f"{setting}    Sleep    10s", "No Operation")
    assert any(v.rule == "GRF01" for v in lint([path]))


def test_parent_suite_lifecycle_actions_require_tags(widget, tmp_path):
    configure_rule(widget)
    (tmp_path / "__init__.robot").write_text("*** Settings ***\nSuite Setup    Reset Product\n", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    suite(child / "test.robot", "", "No Operation")
    assert any(v.rule == "GRF04" for v in lint([tmp_path]))


def test_inherited_test_setup_timing_and_explicit_none(widget, tmp_path):
    (tmp_path / "__init__.robot").write_text("*** Settings ***\nTest Setup    Sleep    10s\n", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    path = child / "test.robot"
    suite(path, "", "No Operation")
    assert any(v.rule == "GRF01" for v in lint([tmp_path]))
    suite(path, "Test Setup    NONE", "No Operation")
    assert lint([tmp_path]) == []


def test_unresolved_static_or_dynamic_resource_is_not_silent(widget, tmp_path):
    path = tmp_path / "test.robot"
    suite(path, "Resource    ${UNRESOLVED}/missing.resource", "No Operation")
    assert any(v.rule == "GRF00" and "Cannot statically resolve" in v.message for v in lint([path]))
