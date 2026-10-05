*** Settings ***
Documentation       Bridge connection: loss of the SMMBridge connection leads to E-Stop, and the operator is
...                 told when the connection comes back (SDS Event Handling).
Resource            ../../resources/smm.resource
Suite Setup         Open SMM Test Environment
Suite Teardown      Close SMM Test Environment
Test Setup          Begin SMM Test
Test Teardown       Finish SMM Test
Test Tags           area:bridge_connection    pilot


*** Variables ***
${RECONNECTED_MESSAGE}      The SMM system went to E-Stop because the connection to SMMBridge was lost. Connection is re-established now.


*** Test Cases ***
SDS-2854109 Bridge Crash Puts The System Into E-Stop
    [Documentation]    If the SMMBridge gets disconnected, the SMM system goes to default E-Stop state
    ...    (i.e. system follows the behaviour described under Default E-Stop chapter).
    ...    The Bridge drops its connection without a goodbye, so the broker publishes its last will.
    [Tags]    SDS-2854109    spechash:293117f8
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Disconnect Bridge    abrupt=True
    Sleep    ${QUIET_PERIOD}
    Connect As Bridge    timeout=${STARTUP_TIMEOUT}    record_version=False
    System State Should Be    E-Stop    timeout=${RESPONSE_TIMEOUT}

SDS-2854109 Broker Outage Puts The System Into E-Stop
    [Documentation]    Variant of 2854109 from ET 2855849: the MQTT broker is killed and started again.
    [Tags]    SDS-2854109    spechash:293117f8    requires:restart
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Restart MQTT Broker    down=3s
    Wait Until Keyword Succeeds    ${STARTUP_TIMEOUT}    2s    Bridge Should Be Connected
    System State Should Be    E-Stop    timeout=${RESPONSE_TIMEOUT}

SDS-2854281 Operator Warning When The Bridge Connection Is Re-Established
    [Documentation]    If the connection to SMMBridge is re-established and appSMM software restarts
    ...    following the RecoverRequest message (see 2653094), appSMM software publishes an event over the
    ...    is/iw/tx topic, with the following details:
    ...    - "Category": "Operator"
    ...    - "EventId": ??
    ...    - "Severity": "Warning"
    ...    - "TimeStamp": time when the event was detected
    ...    - "Message": "The SMM system went to E-Stop because the connection to SMMBridge was lost.
    ...    Connection is re-established now."
    ...    - "EventArgs": none
    ...    EventId is still open in the specification ("??"): it is logged, not checked.
    [Tags]    SDS-2854281    spechash:ae9110ff
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Disconnect Bridge    abrupt=True
    Sleep    ${QUIET_PERIOD}
    Connect As Bridge    timeout=${STARTUP_TIMEOUT}    record_version=False
    ${state}=    Get System State
    Should Be Equal    ${state}    E-Stop
    ...    Precondition not met: appSMM is ${state}, not E-Stop, after the Bridge connection was lost (see 2854109)
    ...    values=False
    Send ICD Message    RecoverRequest
    Wait For Message    RecoverResponse    timeout=${RECOVER_TIMEOUT}    Status=OK
    # The warning may come on reconnection or with the restart after RecoverRequest: look at both.
    ${entry}=    Wait For Message Entry    EventNotification    timeout=${RESPONSE_TIMEOUT}    since=all
    ...    Category=Operator    Severity=Warning    Message=${RECONNECTED_MESSAGE}    EventArgs=[]
    Should Be Equal    ${entry}[topic]    /is/iw/tx
    Log    EventId of the reconnection warning: ${entry}[body][EventId]
    Received Messages Should Be Schema Valid    since=all
