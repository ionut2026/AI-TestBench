*** Settings ***
Documentation       Robustness (plan 5.3): malformed, unknown and misrouted input on the ICD, duplicate and
...                 back-to-back requests. The malformed-input tests check behaviour no specification states
...                 (``nospec:robustness``): appSMM must ignore the input, keep its state and keep answering.
...                 What appSMM should answer to such input (an error, nothing) is an open question for the
...                 specification owners; the tests do not assume either.
Resource            ../../resources/smm.resource
Suite Setup         Open SMM Test Environment
Suite Teardown      Close SMM Test Environment
Test Setup          Begin SMM Test
Test Teardown       Finish SMM Test
Test Template       Input Should Not Disturb appSMM In Idle
Test Tags           area:robustness    pilot    robustness


*** Variables ***
# Stimulus: how many SystemStatusRequests are sent back to back.
${BURST_SIZE}       50
${IW_RX}            /is/iw/rx


*** Test Cases ***
Truncated JSON Is Ignored
    [Documentation]    A payload that is not complete JSON is published on is/iw/rx.
    [Tags]    nospec:robustness    review:pending
    ${IW_RX}    {"Version":7,"SystemStatusRequest":{

Text That Is Not JSON Is Ignored
    [Documentation]    Plain text is published on is/iw/rx.
    [Tags]    nospec:robustness    review:pending
    ${IW_RX}    hello appSMM

Empty Payload Is Ignored
    [Documentation]    An empty payload is published on is/iw/rx.
    [Tags]    nospec:robustness    review:pending
    ${IW_RX}    ${EMPTY}

JSON null Is Ignored
    [Documentation]    The JSON value null (valid JSON, not an object) is published on is/iw/rx.
    [Tags]    nospec:robustness    review:pending
    ${IW_RX}    null

JSON Array Is Ignored
    [Documentation]    A JSON array holding a valid request is published on is/iw/rx.
    [Tags]    nospec:robustness    review:pending
    ${IW_RX}    [{"Version":7,"SystemStatusRequest":{}}]

Request Body Of The Wrong Type Does Not Restart Initialization
    [Documentation]    An InitializationRequest whose body is a string instead of an object is published in
    ...    Idle. Whether appSMM answers is open; it must not change its state.
    [Tags]    nospec:robustness    review:pending
    ${IW_RX}    {"Version":7,"InitializationRequest":"now"}

Unknown ICD Version Does Not Disturb appSMM
    [Documentation]    A SystemStatusRequest with an ICD Version appSMM does not know (999). Whether appSMM
    ...    answers is open.
    [Tags]    nospec:robustness    review:pending
    ${IW_RX}    {"Version":999,"SystemStatusRequest":{}}

Missing ICD Version Does Not Disturb appSMM
    [Documentation]    A SystemStatusRequest without the Version field. Whether appSMM answers is open.
    [Tags]    nospec:robustness    review:pending
    ${IW_RX}    {"SystemStatusRequest":{}}

Unknown Message Is Ignored
    [Documentation]    A message name the ICD does not define is published on is/iw/rx.
    [Tags]    nospec:robustness    review:pending
    ${IW_RX}    {"Version":7,"SelfDestructRequest":{}}

ShutdownRequest On appSMM's Own Transmit Topic Is Not Executed
    [Documentation]    A valid ShutdownRequest published on is/iw/tx (the topic appSMM publishes on, not the
    ...    one it receives requests on) must not shut appSMM down.
    [Tags]    nospec:robustness    review:pending
    /is/iw/tx    {"Version":7,"ShutdownRequest":{}}

ShutdownRequest On An Unknown Topic Is Not Executed
    [Documentation]    A valid ShutdownRequest published on a topic the ICD does not define must not shut
    ...    appSMM down.
    [Tags]    nospec:robustness    review:pending
    /is/iw/unknown    {"Version":7,"ShutdownRequest":{}}

SDS-2525423 A Second InitializationRequest During Initialization Does Not Start It Again
    [Documentation]    SMM begins the initialization process only after the InitializationRequest message is
    ...    received over the is/iw/rx topic and the system state is "NotInitialized".
    ...    Note: When the InitializationRequest message is received over the is/iw/rx topic, appSMM transmits
    ...    the 'InitializeCmd' command to rtc_appl, only when the system state is 'NotInitialized'.
    ...    The second request arrives in Initializing: the initialization runs once. The response to it is
    ...    not specified and not checked.
    [Tags]    SDS-2525423    spechash:9bb164c6    requires:restart    review:pending
    [Template]    NONE
    Restart appSMM And Wait Until NotInitialized
    Send ICD Message    InitializationRequest
    Wait For System State    Initializing    timeout=${RESPONSE_TIMEOUT}
    Send ICD Message    InitializationRequest
    Wait For System State    Idle    timeout=${INIT_TIMEOUT}    since=test
    Messages Should Have Been Received    SystemStatusNotification    count=1    since=test
    ...    CurrentState=Initializing
    Received Messages Should Be Schema Valid

SDS-2525392 Every One Of Back-To-Back SystemStatusRequests Gets A SystemStatusResponse On is/iw/tx
    [Documentation]    After it gets connected to the MQTT Broker, the appSMM software automatically publishes
    ...    a SystemStatusResponse message each time when a SystemStatusRequest message is received over the
    ...    is/iw/rx topic. The SystemStatusResponse message is always published over the is/iw/tx topic.
    ...    "Each time" under load: ${BURST_SIZE} requests are sent back to back, exactly as many responses
    ...    must come.
    [Tags]    SDS-2525392    spechash:5865c681    review:pending
    [Template]    NONE
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    ${mark}=    Mark Timeline
    Send SystemStatusRequests Back To Back    ${BURST_SIZE}
    Each Request Should Get One Idle Response On is/iw/tx    ${BURST_SIZE}    ${mark}


*** Keywords ***
Input Should Not Disturb appSMM In Idle
    [Documentation]    Publishes ``raw`` on ``topic`` in Idle; appSMM must keep its state and keep working.
    [Arguments]    ${topic}    ${raw}
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Send Raw Payload    ${topic}    ${raw}
    Message Should Not Arrive    SystemStatusNotification    duration=${QUIET_PERIOD}
    ${env}=    Get Environment Status
    Should Be True    $env.get('appSmm', {}).get('running') is not False    msg=appSMM is no longer running
    System State Should Be    Idle    timeout=${RESPONSE_TIMEOUT}

Send SystemStatusRequests Back To Back
    [Documentation]    Sends ``count`` SystemStatusRequests without waiting in between.
    [Arguments]    ${count}
    FOR    ${_}    IN RANGE    ${count}
        Send ICD Message    SystemStatusRequest
    END

Each Request Should Get One Idle Response On is/iw/tx
    [Documentation]    Exactly ``count`` SystemStatusResponses (CurrentState Idle, topic is/iw/tx) since ``mark``:
    ...    one ordered wait for ``count`` distinct responses, then no further response.
    [Arguments]    ${count}    ${mark}
    VAR    @{specs}=    @{EMPTY}
    FOR    ${_}    IN RANGE    ${count}
        VAR    @{specs}=    @{specs}    SystemStatusResponse | CurrentState=Idle
    END
    ${entries}=    Wait For Message Sequence    @{specs}    timeout=${RESPONSE_TIMEOUT}    since=${mark}
    FOR    ${entry}    IN    @{entries}
        Should Be Equal    ${entry}[topic]    /is/iw/tx
    END
    Message Should Not Arrive    SystemStatusResponse    duration=${SHORT_QUIET_PERIOD}    since=last
