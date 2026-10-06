from pathlib import Path

from smm_automation.pipeline.lint import lint

CATALOG = {"specifications": {"1": {"text": "If the bridge is lost, appSMM goes to the E-Stop state within 20 seconds."}}}

RESOURCE = """*** Keywords ***
Restart And Wait
    Restart appSMM    down=1s
    Wait For System State    NotInitialized    timeout=${STARTUP_TIMEOUT}

Teardown Maybe Restarting
    IF    $twin
        Clear Hardware Twin Racks
        Restart appSMM
    END
"""


def _lint(tmp_path: Path, tests: str, catalog=CATALOG):
    (tmp_path / "x.resource").write_text(RESOURCE, encoding="utf-8")
    suite = tmp_path / "s.robot"
    suite.write_text("*** Settings ***\nResource    x.resource\n\n*** Test Cases ***\n" + tests, encoding="utf-8")
    return lint([suite], catalog)


def _rules(violations):
    return sorted(v.rule for v in violations)


def test_clean_test_passes(tmp_path):
    tests = """SDS-1 Good
    [Documentation]    If the bridge is lost, appSMM goes to the E-Stop state.
    [Tags]    SDS-1
    Wait For System State    E-Stop    timeout=${RESPONSE_TIMEOUT}
"""
    assert _lint(tmp_path, tests) == []


def test_sleep_and_literal_timeouts(tmp_path):
    tests = """SDS-1 Bad Timing
    [Documentation]    Variant of 1.
    [Tags]    SDS-1
    Sleep    3s
    Wait For Message    X    timeout=20s
    Message Should Not Arrive    Y    duration=1s
    Wait Until Keyword Succeeds    10s    1s    Bridge Should Be Connected
"""
    assert _rules(_lint(tmp_path, tests)) == ["SMM01", "SMM03", "SMM03", "SMM03"]


def test_documentation_must_cite_the_spec(tmp_path):
    tests = """SDS-1 No Doc
    [Tags]    SDS-1
    Log    x

SDS-1 Unrelated Doc
    [Documentation]    Checks something about the bridge.
    [Tags]    SDS-1
    Log    x
"""
    violations = _lint(tmp_path, tests)
    assert _rules(violations) == ["SMM02", "SMM02"]
    # without a catalog only the missing documentation is reported
    assert [v.item for v in _lint(tmp_path, tests, None)] == ["SDS-1 No Doc"]


def test_tier_tags_follow_keyword_use(tmp_path):
    tests = """SDS-1 Restarts Through A User Keyword
    [Documentation]    Variant of 1.
    [Tags]    SDS-1
    Restart And Wait

SDS-1 Twin Without Tag
    [Documentation]    Variant of 1.
    [Tags]    SDS-1
    Trigger Emergency Stop

SDS-1 Tagged Without Use
    [Documentation]    Variant of 1.
    [Tags]    SDS-1    needs:twin    requires:restart
    Log    x

SDS-1 Conditional Use Needs No Tag
    [Documentation]    Variant of 1.
    [Tags]    SDS-1
    IF    $twin
        Wait For Hardware Command    InitializeCmd    timeout=${RESPONSE_TIMEOUT}
    END
    [Teardown]    Teardown Maybe Restarting

SDS-1 Tagged And Used
    [Documentation]    Variant of 1.
    [Tags]    SDS-1    needs:twin    requires:restart
    Run Keyword And Ignore Error    Restart MQTT Broker
    Trigger Hardware Action    insertFrontIn

SDS-1 Reads The Log Without Tag
    [Documentation]    Variant of 1.
    [Tags]    SDS-1
    ICD Messages Should Be Logged By appSMM    SystemStatusRequest

SDS-1 Fault Rule Is A Twin Use
    [Documentation]    Variant of 1.
    [Tags]    SDS-1    needs:twin    needs:applog
    Set Hardware Faults    DeInitializeRsp | action=drop
"""
    found = {(v.item, v.rule) for v in _lint(tmp_path, tests)}
    assert found == {
        ("SDS-1 Restarts Through A User Keyword", "SMM05"),
        ("SDS-1 Twin Without Tag", "SMM04"),
        ("SDS-1 Tagged Without Use", "SMM04"),
        ("SDS-1 Tagged Without Use", "SMM05"),
        ("SDS-1 Reads The Log Without Tag", "SMM06"),
        ("SDS-1 Fault Rule Is A Twin Use", "SMM06"),
    }


def test_pilot_suites_are_clean():
    from smm_automation import FRAMEWORK_ROOT
    from smm_automation.pipeline.ingest import load_catalog

    catalog = load_catalog(FRAMEWORK_ROOT / "catalog" / "pilot.json")
    assert lint([FRAMEWORK_ROOT / "robot" / "suites"], catalog) == []
