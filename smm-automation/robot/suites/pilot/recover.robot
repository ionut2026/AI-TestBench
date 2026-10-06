*** Settings ***
Documentation       Recover: RecoverRequest/Response, de-initialization and re-initialization after E-Stop
...                 (7251 SDS Workflow).
Resource            ../../resources/smm.resource
Suite Setup         Open SMM Test Environment
Suite Teardown      Close SMM Test Environment
Test Setup          Begin SMM Test
Test Teardown       Finish SMM Test
Test Tags           area:recover    pilot


*** Variables ***
# Time limit stated in the specification (not a tier timeout).
${SDS_2532504_LIMIT}      20s


*** Test Cases ***
SDS-2653094 System Restarts With RecoverRequest After Shutdown
    [Documentation]    If the system is shut down following a fatal error event, the SMM system can be
    ...    re-started if the RecoverRequest message is received over the is/iw/rx topic.
    ...    After the RecoverRequest message is received over the is/iw/rx topic, the SMM system follows the
    ...    recovery procedure, as described in 2532492, 2532504, 2532508, 2532510.
    ...    Here the shutdown is requested with ShutdownRequest; see the next test for a hardware fatal error.
    [Tags]    SDS-2653094    spechash:590de9f9    review:pending    known-issue:FINDING-2
    Bring SMM To E-Stop With Shutdown
    Send ICD Message    RecoverRequest
    Wait For Message Sequence
    ...    RecoverResponse | Status=OK
    ...    SystemStatusNotification | CurrentState=Initializing
    ...    timeout=${RECOVER_TIMEOUT}
    Wait For Message    SystemStatusNotification    timeout=${INIT_TIMEOUT}
    ...    PreviousState=Clearing    CurrentState=Idle
    Received Messages Should Be Schema Valid

SDS-2653094 System Restarts With RecoverRequest After A Hardware Fatal Error
    [Documentation]    Variant of 2653094 (see the previous test): the E-Stop is caused by a fatal hardware error
    ...    (twin emergency stop).
    [Tags]    SDS-2653094    spechash:590de9f9    needs:twin    review:pending    known-issue:FINDING-2
    Require Hardware Twin
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Trigger Emergency Stop
    Wait For System State    E-Stop    timeout=${RESPONSE_TIMEOUT}
    Send ICD Message    RecoverRequest
    Wait For Message Sequence
    ...    RecoverResponse | Status=OK
    ...    SystemStatusNotification | CurrentState=Initializing
    ...    timeout=${RECOVER_TIMEOUT}
    Wait For System State    Idle    timeout=${INIT_TIMEOUT}

SDS-2532492 RecoverRequest In E-Stop Sends DeInitializeCmd To RTC
    [Documentation]    If the RecoverRequest message is received over the is/iw/rx topic, appSMM software
    ...    sends the "DeInitializeCmd" command to RTC, in order to initiate system de-initialization. The
    ...    "DeInitializeCmd" command is sent only if the system status is "E-Stop".
    [Tags]    SDS-2532492    spechash:69c84bb6    needs:twin    review:pending
    Require Hardware Twin
    Bring SMM To E-Stop With Shutdown
    Send ICD Message    RecoverRequest
    Wait For Hardware Command    DeInitializeCmd    timeout=${RESPONSE_TIMEOUT}
    Wait For Message    RecoverResponse    timeout=${RECOVER_TIMEOUT}    Status=OK

SDS-2532492 RecoverRequest Outside E-Stop Does Not De-Initialize
    [Documentation]    Negative case of 2532492: in "Idle" a RecoverRequest must not de-initialize the
    ...    system (no DeInitializeCmd, the state stays "Idle").
    [Tags]    SDS-2532492    spechash:69c84bb6    review:pending
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Send ICD Message    RecoverRequest
    Message Should Not Arrive    RecoverResponse    duration=${QUIET_PERIOD}    Status=OK
    ${status}=    Get Environment Status
    IF    '${status}[hardware][kind]' == 'twin'
        Hardware Command Should Not Be Sent    DeInitializeCmd    duration=${SHORT_QUIET_PERIOD}
    END
    Message Should Not Arrive    SystemStatusNotification    duration=${SHORT_QUIET_PERIOD}    PreviousState=Idle
    System State Should Be    Idle

SDS-2532504 RecoverResponse OK Matches The ICD Schema
    [Documentation]    If rtc_appl replies with 'DeInitializeRsp' within 20 seconds, appSMM software
    ...    publishes the RecoverResponse message with Status "OK" over the is/iw/tx topic. The structure of
    ...    the RecoverResponse message published by appSMM software matches the structure of the message
    ...    file attached under Properties.
    ...    (The hardware answers DeInitialize immediately; the ICD JSON schema stands in for the attached file.)
    [Tags]    SDS-2532504    spechash:bba306d8    review:pending
    Bring SMM To E-Stop With Shutdown
    Send ICD Message    RecoverRequest
    ${entry}=    Wait For Message Entry    RecoverResponse    timeout=${SDS_2532504_LIMIT}    Status=OK
    Should Be Equal    ${entry}[topic]    /is/iw/tx
    Should Be True    ${entry}[valid]    RecoverResponse breaks the ICD schema: ${entry}[errors]
    Received Messages Should Be Schema Valid

SDS-2532510 Initialization Starts After RecoverResponse
    [Documentation]    After publishing the RecoverResponse message, appSMM software sends the
    ...    'InitializeCmd' command to RTC, in order to initiate system initialization.
    ...    At the ICD the initialization shows as the Initializing notification after RecoverResponse;
    ...    with the hardware twin the InitializeCmd itself is checked.
    [Tags]    SDS-2532510    spechash:f56cd486    review:pending    known-issue:FINDING-2
    Bring SMM To E-Stop With Shutdown
    Send ICD Message    RecoverRequest
    Wait For Message Sequence
    ...    RecoverResponse | Status=OK
    ...    SystemStatusNotification | CurrentState=Initializing
    ...    timeout=${RECOVER_TIMEOUT}
    ${status}=    Get Environment Status
    IF    '${status}[hardware][kind]' == 'twin'
        Wait For Hardware Command    InitializeCmd    timeout=${RESPONSE_TIMEOUT}
    END
    Wait For System State    Idle    timeout=${INIT_TIMEOUT}
