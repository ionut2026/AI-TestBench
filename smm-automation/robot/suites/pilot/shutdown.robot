*** Settings ***
Documentation       Shutdown: ShutdownRequest/Response and the transition to E-Stop (7251 SDS Workflow).
Resource            ../../resources/smm.resource
Suite Setup         Open SMM Test Environment
Suite Teardown      Close SMM Test Environment
Test Setup          Begin SMM Test
Test Teardown       Finish SMM Test
Test Tags           area:shutdown    pilot


*** Variables ***
# Time limit stated in the specification (not a tier timeout).
${SDS_2428417_LIMIT}      20s
# Stimulus: how late the hardware answers EmergencyStop in the boundary test (inside the 20 s limit).
${LATE_EMERGENCY_STOP_RSP}    15s


*** Test Cases ***
SDS-2428419 E-Stop Is Notified After ShutdownResponse
    [Documentation]    After sending the ShutdownResponse, appSMM software publishes a
    ...    SystemStatusNotification message which notifies that system status changed from "Idle"
    ...    (PreviousState) to "E-Stop" (CurrentState)
    [Tags]    SDS-2428419    spechash:3fab7cb6    review:pending
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
    [Tags]    SDS-2428417    spechash:01efab86    review:pending
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Send ICD Message    ShutdownRequest
    ${entry}=    Wait For Message Entry    ShutdownResponse    timeout=${SDS_2428417_LIMIT}    Status=OK
    Should Be Equal    ${entry}[topic]    /is/iw/tx
    Should Be True    ${entry}[valid]    ShutdownResponse breaks the ICD schema: ${entry}[errors]
    Pair Issues Should Be Empty

SDS-2428417 ShutdownResponse OK When The Stop Reply Comes Late But Within 20 Seconds
    [Documentation]    If rtc_appl replies with "StopRsp" within 20 seconds, appSMM software publishes the
    ...    ShutDownResponse with Status "OK", over the is/iw/tx topic.
    ...    Boundary case: appSMM stops the hardware with EmergencyStopCmd (the specification's "StopRsp" is
    ...    taken to be its EmergencyStopRsp); a COP fault rule delays that reply by 15 s, so appSMM has to wait
    ...    for it and still answer OK, within the 20 s.
    [Tags]    SDS-2428417    spechash:01efab86    needs:twin    review:pending
    Require Hardware Twin
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Set Hardware Faults    EmergencyStopRsp | delay=${LATE_EMERGENCY_STOP_RSP}
    ${request}=    Send ICD Message    ShutdownRequest
    ${entry}=    Wait For Message Entry    ShutdownResponse    timeout=${SDS_2428417_LIMIT}
    Response Should Have Status On is/iw/tx    ${entry}    OK
    Time Between Should Be At Least    ${request}    ${entry}    ${LATE_EMERGENCY_STOP_RSP}
    Time Between Should Be Less Than    ${request}    ${entry}    ${SDS_2428417_LIMIT}
    Hardware Fault Should Have Been Applied    EmergencyStopRsp    times=1
