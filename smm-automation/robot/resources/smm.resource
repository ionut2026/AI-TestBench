*** Settings ***
Documentation       Shared setup for all SMM suites: one test environment per suite (tier from
...                 ``--variablefile robot/environments/<tier>.py``), the test plays the SMM Bridge.
...
...                 Tag conventions (see README):
...                 - ``SDS-<id>``: the RV&S specification the test verifies (REQ/US/ET links come from the catalog)
...                 - ``spechash:<8 hex>``: hash of the specification text the test was written against (drift check)
...                 - ``needs:twin``: drives the simulated hardware (offline tier only)
...                 - ``needs:hardware-action``: an E-Stop (``Trigger Emergency Stop``): the twin in the offline tier,
...                 the operator on the rig (``smm-auto run --operator console|dialog``)
...                 - ``needs:operator``: an ``Operator Action`` at the instrument (only with ``--operator``)
...                 - ``needs:applog``: reads appSMM's SmartInspect log files (offline tier; rig with a rig control
...                 that can ``fetch-log``)
...                 - ``requires:restart``: restarts appSMM (offline/mock; rig with ``restart-appsmm``)
...                 - ``requires:broker-restart``: restarts the MQTT broker (offline/mock; rig with ``restart-broker``)
...                 - ``review:pending``: generated, not yet reviewed by a human. Runs on every tier; the report
...                 labels it UNREVIEWED and its specification is never VERIFIED. Remove it only together with an
...                 entry in ``catalog/reviews.toml`` (``smm-auto drift --strict`` checks this).
...                 - ``known-issue:<id>``: fails because of a candidate finding (README "Findings"); its
...                 failure is KNOWN FAIL and does not fail ``smm-auto run`` (``--fail-on new``); if it passes
...                 the report shows FIXED?
Library             Collections
Library             smm_automation.SMMTestbench


*** Variables ***
${TIER}                 mock
&{OVERRIDES}            &{EMPTY}
${RESPONSE_TIMEOUT}     10s
${STARTUP_TIMEOUT}      60s
${INIT_TIMEOUT}         180s
${RECOVER_TIMEOUT}      60s
${QUIET_PERIOD}         3s
# Silence checked right after a response, when a forbidden message would follow immediately.
${SHORT_QUIET_PERIOD}   1s
# How long the Bridge stays away in a simulated Bridge crash (a stimulus, not a timeout).
${BRIDGE_OUTAGE}        ${QUIET_PERIOD}
# How the 'Operator Action' steps reach the operator: console, dialog or none (empty: SMM_OPERATOR, else none).
${OPERATOR}             ${EMPTY}
${OPERATOR_TIMEOUT}     300s


*** Keywords ***
Open SMM Test Environment
    [Documentation]    Suite setup: starts the tier's environment and connects as the Bridge.
    Start Test Environment    ${TIER}    ${OVERRIDES}
    Connect As Bridge    timeout=${STARTUP_TIMEOUT}

Close SMM Test Environment
    [Documentation]    Suite teardown.
    Run Keyword And Ignore Error    Disconnect Bridge
    Stop Test Environment

Restart appSMM And Wait Until NotInitialized
    [Documentation]    Precondition: a freshly started appSMM in NotInitialized (the only way to get
    ...    there once the system has been initialized). Waits until the new appSMM has announced itself on
    ...    the broker, then for its settled state: on the rig appSMM reaches NotInitialized before it connects
    ...    to the broker, so no SystemStatusNotification can be relied on here (SDS-2752658 checks that).
    Restart appSMM    down=1s
    Wait For Message    ConnectionNotification    timeout=${STARTUP_TIMEOUT}    Source=SMM    Status=Connected
    Bring SMM To State    NotInitialized    timeout=${STARTUP_TIMEOUT}

Require Hardware Twin
    [Documentation]    Skips the test when the tier cannot drive the hardware (only offline has the twin).
    ${status}=    Get Environment Status
    Skip If    '${status}[hardware][kind]' != 'twin'    Needs the hardware twin (offline tier); tier is ${TIER}

Require Hardware Action
    [Documentation]    Skips the test when no one can do the hardware ``action`` in this tier: the twin (offline)
    ...    or the operator at the instrument (rig, run with ``--operator``).
    [Arguments]    ${action}=emergencyStop
    ${possible}=    Hardware Action Is Possible    ${action}
    Skip If    not ${possible}    Needs the hardware twin or an operator for ${action}; tier is ${TIER}

Bring SMM To E-Stop With Shutdown
    [Documentation]    Precondition: Idle, then ShutdownRequest -> E-Stop.
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Send ICD Message    ShutdownRequest
    Wait For System State    E-Stop    timeout=${RESPONSE_TIMEOUT}

Finish SMM Test And Empty The Instrument
    [Documentation]    Teardown for tests that put racks on the hardware twin: logs the test, takes every
    ...    rack off the twin and restarts appSMM (which keeps its rack bookkeeping across Shutdown/Recover and
    ...    would otherwise go straight to NormalOperation), then initializes so the next test starts from Idle.
    Finish SMM Test
    ${removed}=    Clear Hardware Twin Racks
    IF    ${removed}
        Restart appSMM And Wait Until NotInitialized
        Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    END

Messages From appSMM Should Use Topic
    [Documentation]    Every ``name`` message received in this test was published on ``topic``.
    [Arguments]    ${name}    ${topic}=/is/iw/tx
    ${entries}=    Messages Should Have Been Received    ${name}    since=test
    Should Not Be Empty    ${entries}    No ${name} received in this test
    FOR    ${entry}    IN    @{entries}
        Should Be Equal    ${entry}[topic]    ${topic}    ${entry}[id] ${name} was published on ${entry}[topic]
    END

Response Should Have Status On is/iw/tx
    [Documentation]    The response ``entry`` (from Wait For Message Entry) has ``Status`` = ``status`` and was
    ...    published on is/iw/tx.
    [Arguments]    ${entry}    ${status}
    Should Be Equal    ${entry}[body][Status]    ${status}    ${entry}[name] has Status ${entry}[body][Status]
    Should Be Equal    ${entry}[topic]    /is/iw/tx    ${entry}[name] was published on ${entry}[topic]
