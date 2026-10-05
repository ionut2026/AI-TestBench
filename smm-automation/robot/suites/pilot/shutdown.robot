*** Settings ***
Documentation       Shutdown: ShutdownRequest/Response and the transition to E-Stop (7251 SDS Workflow).
Resource            ../../resources/smm.resource
Suite Setup         Open SMM Test Environment
Suite Teardown      Close SMM Test Environment
Test Setup          Begin SMM Test
Test Teardown       Finish SMM Test
Test Tags           area:shutdown    pilot


*** Test Cases ***
SDS-2428419 E-Stop Is Notified After ShutdownResponse
    [Documentation]    After sending the ShutdownResponse, appSMM software publishes a
    ...    SystemStatusNotification message which notifies that system status changed from "Idle"
    ...    (PreviousState) to "E-Stop" (CurrentState)
    [Tags]    SDS-2428419    spechash:3fab7cb6
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Send ICD Message    ShutdownRequest
    Wait For Message Sequence
    ...    ShutdownResponse
    ...    SystemStatusNotification | PreviousState=Idle | CurrentState=E-Stop
    ...    timeout=${RESPONSE_TIMEOUT}
    System State Should Be    E-Stop
    Received Messages Should Be Schema Valid

SDS-2428417 ShutdownResponse OK Is Published On is/iw/tx And Matches The ICD Schema
    [Documentation]    If rtc_appl replies with "StopRsp" within 20 seconds, appSMM software publishes the
    ...    ShutDownResponse with Status "OK", over the is/iw/tx topic. The structure of the ShutDownResponse
    ...    message published by appSMM software matches the structure of the message file attached under
    ...    Properties.
    ...    (The hardware answers immediately; the ICD JSON schema stands in for the attached file.)
    [Tags]    SDS-2428417    spechash:01efab86
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Send ICD Message    ShutdownRequest
    ${entry}=    Wait For Message Entry    ShutdownResponse    timeout=20s    Status=OK
    Should Be Equal    ${entry}[topic]    /is/iw/tx
    Should Be True    ${entry}[valid]    ShutdownResponse breaks the ICD schema: ${entry}[errors]
    Pair Issues Should Be Empty
