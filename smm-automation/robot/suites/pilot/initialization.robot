*** Settings ***
Documentation       Initialization: InitializationRequest/Response and the Initializing -> Idle -> Clearing
...                 -> Idle state sequence (7251 SDS Workflow).
Resource            ../../resources/smm.resource
Suite Setup         Open SMM Test Environment
Suite Teardown      Close SMM Test Environment
Test Setup          Begin SMM Test
Test Teardown       Finish SMM Test
Test Tags           area:initialization    pilot


*** Variables ***
# Time limit stated in the specification (not a tier timeout).
${SDS_2532404_LIMIT}      20s
# 2532404: how long to wait for the Error response (the 20 s limit plus a margin).
${SDS_2532404_WAIT}       30s


*** Test Cases ***
SDS-2955303 SystemStatusResponse Reports NotInitialized Before Initialization Starts
    [Documentation]    If the SystemStatusRequest message is received over the is/iw/rx topic before the
    ...    initialization starts, appSMM software responds with the SystemStatusResponse having the current
    ...    status: "NotInitialized".
    [Tags]    SDS-2955303    spechash:f1521b27    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    ${response}=    Request And Wait For Response    SystemStatusRequest    timeout=${RESPONSE_TIMEOUT}
    Should Be Equal    ${response}[CurrentState]    NotInitialized
    Received Messages Should Be Schema Valid

SDS-2525423 Initialization Starts On InitializationRequest In NotInitialized
    [Documentation]    SMM begins the initialization process only after the InitializationRequest message
    ...    is received over the is/iw/rx topic and the system state is "NotInitialized".
    ...    Note: When the InitializationRequest message is received over the is/iw/rx topic, appSMM
    ...    transmits the 'InitializeCmd' command to rtc_appl, only when the system state is 'NotInitialized'.
    [Tags]    SDS-2525423    spechash:9bb164c6    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    Message Should Not Arrive    SystemStatusNotification    duration=${QUIET_PERIOD}    CurrentState=Initializing
    Send ICD Message    InitializationRequest
    Wait For Message    SystemStatusNotification    timeout=${RESPONSE_TIMEOUT}
    ...    PreviousState=NotInitialized    CurrentState=Initializing
    ${status}=    Get Environment Status
    IF    '${status}[hardware][kind]' == 'twin'
        Wait For Hardware Command    InitializeCmd    timeout=${RESPONSE_TIMEOUT}
    END

SDS-2525423 InitializationRequest Outside NotInitialized Does Not Start Initialization
    [Documentation]    Negative case of 2525423: in "Idle" the InitializationRequest must not start the
    ...    initialization (no Initializing notification, no InitializeCmd to rtc_appl).
    [Tags]    SDS-2525423    spechash:9bb164c6    review:pending
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Send ICD Message    InitializationRequest
    ${response}=    Wait For Message    InitializationResponse    timeout=${RESPONSE_TIMEOUT}
    Log    InitializationResponse in Idle: ${response}
    Message Should Not Arrive    SystemStatusNotification    duration=${QUIET_PERIOD}    CurrentState=Initializing
    ${status}=    Get Environment Status
    IF    '${status}[hardware][kind]' == 'twin'
        Hardware Command Should Not Be Sent    InitializeCmd    duration=${SHORT_QUIET_PERIOD}
    END
    System State Should Be    Idle

SDS-2532404 InitializationResponse Error When Initialization Does Not Start Within 20 Seconds
    [Documentation]    If SMM initialization does not start within 20 seconds after the InitializationRequest
    ...    message is received, appSMM software publishes the InitializationResponse message with Status
    ...    "Error" over the is/iw/tx topic.
    ...    A COP fault rule drops appSMM's InitializeCmd, so rtc_appl never starts the initialization.
    [Tags]    SDS-2532404    spechash:05d98e45    needs:twin    requires:restart    review:pending
    Require Hardware Twin
    Restart appSMM And Wait Until NotInitialized
    Set Hardware Faults    InitializeCmd | action=drop
    ${request}=    Send ICD Message    InitializationRequest
    Wait For Hardware Command    InitializeCmd    timeout=${RESPONSE_TIMEOUT}
    ${entry}=    Wait For Message Entry    InitializationResponse    timeout=${SDS_2532404_WAIT}
    Response Should Have Status On is/iw/tx    ${entry}    Error
    Time Between Should Be At Least    ${request}    ${entry}    ${SDS_2532404_LIMIT}
    Hardware Fault Should Have Been Applied    InitializeCmd
    Received Messages Should Be Schema Valid

SDS-2528698 Initializing Is Notified After InitializationResponse OK
    [Documentation]    After publishing the InitializationResponse message with Status "OK", appSMM software
    ...    sends a SystemStatusNotification message over the is/iw/tx topic, which notifies that system
    ...    state changed from "NotInitialized" to "Initializing".
    [Tags]    SDS-2528698    spechash:c4283482    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    Send ICD Message    InitializationRequest
    Wait For Message Sequence
    ...    InitializationResponse | Status=OK | @topic=/is/iw/tx
    ...    SystemStatusNotification | PreviousState=NotInitialized | CurrentState=Initializing | @topic=/is/iw/tx
    ...    timeout=${RESPONSE_TIMEOUT}
    Received Messages Should Be Schema Valid
    Pair Issues Should Be Empty

SDS-2528703 Idle Is Notified When Clearing Is Completed
    [Documentation]    If system clearing is completed (all racks detected during initialization were
    ...    moved to Output), appSMM software publishes a SystemStatusNotification message which notifies
    ...    that the CurrentState of the system changed to "Idle".
    [Tags]    SDS-2528703    spechash:440acdc2    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    Send ICD Message    InitializationRequest
    Wait For Message Sequence
    ...    SystemStatusNotification | CurrentState=Clearing
    ...    SystemStatusNotification | PreviousState=Clearing | CurrentState=Idle
    ...    timeout=${INIT_TIMEOUT}
    System State Should Be    Idle
    Received Messages Should Be Schema Valid

SDS-2528710 InitializationRequest And InitializationResponse Are Logged With Topic And Content
    [Documentation]    The InitializationRequest and InitializationResponse messages are logged in appSMM log
    ...    files. The message topic and content of each exchanged message is included in the log files.
    ...    Checked in appSMM's SmartInspect log (trace.config next to appSMM.exe): one RX(/is/iw/rx) line for
    ...    the request and one TX(/is/iw/tx) line for the response, each with the exchanged JSON content.
    [Tags]    SDS-2528710    spechash:82a5ea1a    needs:applog    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    Send ICD Message    InitializationRequest
    Wait For Message    InitializationResponse    timeout=${RESPONSE_TIMEOUT}
    ${logged}=    ICD Messages Should Be Logged By appSMM    InitializationRequest    InitializationResponse
    ...    timeout=${RESPONSE_TIMEOUT}
    Length Should Be    ${logged}    2
