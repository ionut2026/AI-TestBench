*** Settings ***
Documentation       System Status: SystemStatusRequest/Response and SystemStatusNotification behaviour
...                 (7251 SDS Workflow). Specification texts are in each test's documentation and in
...                 catalog/pilot.json.
Resource            ../../resources/smm.resource
Suite Setup         Open SMM Test Environment
Suite Teardown      Close SMM Test Environment
Test Setup          Begin SMM Test
Test Teardown       Finish SMM Test
Test Tags           area:system_status    pilot


*** Test Cases ***
SDS-2525388 PowerOn Then NotInitialized Are Notified After appSMM Connects To The Broker
    [Documentation]    Following appSMM connection to MQTT Broker, the appSMM software publishes two
    ...    SystemStatusNotification messages over the is/iw/tx topic, to notify that CurrentState of the
    ...    system changed first to "PowerOn", and then to "NotInitialized".
    [Tags]    SDS-2525388    spechash:ba57a704    requires:restart    review:pending    known-issue:FINDING-3
    Restart appSMM    down=1s
    Wait For Message Sequence
    ...    ConnectionNotification | Source=SMM | Status=Connected
    ...    SystemStatusNotification | CurrentState=PowerOn | @topic=/is/iw/tx
    ...    SystemStatusNotification | CurrentState=NotInitialized | @topic=/is/iw/tx
    ...    timeout=${STARTUP_TIMEOUT}
    Messages Should Have Been Received    SystemStatusNotification    count=2    since=test
    Received Messages Should Be Schema Valid

SDS-2752658 NotInitialized Is Notified After appSMM Restart
    [Documentation]    After restart appSMM software publishes the SystemStatusNotification message over
    ...    the is/iw/tx topic, to notify the Bridge that the current system state is "NotInitialized".
    [Tags]    SDS-2752658    spechash:e492fe7e    requires:restart    review:pending
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Restart appSMM    down=1s
    Wait For System State    NotInitialized    timeout=${STARTUP_TIMEOUT}
    Messages From appSMM Should Use Topic    SystemStatusNotification
    System State Should Be    NotInitialized

SDS-2525392 Every SystemStatusRequest Gets A SystemStatusResponse On is/iw/tx
    [Documentation]    After it gets connected to the MQTT Broker, the appSMM software automatically
    ...    publishes a SystemStatusResponse message each time when a SystemStatusRequest message is
    ...    received over the is/iw/rx topic. The SystemStatusResponse message is always published over
    ...    the is/iw/tx topic.
    [Tags]    SDS-2525392    spechash:5865c681    review:pending
    ${mark}=    Mark Timeline
    FOR    ${_}    IN RANGE    3
        ${response}=    Request And Wait For Response    SystemStatusRequest    timeout=${RESPONSE_TIMEOUT}
        Dictionary Should Contain Key    ${response}    CurrentState
    END
    Messages Should Have Been Received    SystemStatusResponse    count=3    since=${mark}
    Messages From appSMM Should Use Topic    SystemStatusResponse
    Received Messages Should Be Schema Valid
    Pair Issues Should Be Empty

SDS-2525390 SystemStatusNotification Matches The ICD Schema And Uses is/iw/tx
    [Documentation]    The structure of the SystemStatusNotification message published by appSMM software
    ...    matches the structure of the message file attached under Properties.
    ...    The SystemStatusNotification message is always published over the is/iw/tx topic.
    ...    (The ICD JSON schema of the pinned SMM TestBench stands in for the attached message file.)
    [Tags]    SDS-2525390    spechash:156f1bba    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    Send ICD Message    InitializationRequest
    Wait For Message Sequence
    ...    SystemStatusNotification | CurrentState=Initializing
    ...    SystemStatusNotification | CurrentState=Idle
    ...    timeout=${INIT_TIMEOUT}
    Messages From appSMM Should Use Topic    SystemStatusNotification
    Received Messages Should Be Schema Valid

SDS-2528706 Idle Then Clearing Are Notified When All Lanes Are Initialized
    [Documentation]    If all SMM lanes are successfully initialized, appSMM software publishes two
    ...    SystemStatusNotification messages, which notifiy that the CurrentState of the system changed
    ...    firstly to "Idle" and then to "Clearing".
    [Tags]    SDS-2528706    spechash:fd1f48cd    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    Send ICD Message    InitializationRequest
    Wait For Message Sequence
    ...    SystemStatusNotification | CurrentState=Initializing
    ...    SystemStatusNotification | PreviousState=Initializing | CurrentState=Idle
    ...    SystemStatusNotification | PreviousState=Idle | CurrentState=Clearing
    ...    timeout=${INIT_TIMEOUT}
    Received Messages Should Be Schema Valid

SDS-2427781 SetConfigurationRequest In NotInitialized Notifies Configuring
    [Documentation]    If the SetConfigurationRequest message is received over the is/iw/rx topic and the
    ...    system state is 'NotInitialized', appSMM software automatically publishes the
    ...    SystemStatusNotification message over the is/iw/tx topic, which notifies that system state
    ...    changed from "NotInitialized" to "Configuring".
    [Tags]    SDS-2427781    spechash:56b72b64    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    Send ICD Message    SetConfigurationRequest    body={"STI.Barcode.Code128": "Enabled"}
    Wait For Message    SystemStatusNotification    timeout=${RESPONSE_TIMEOUT}
    ...    PreviousState=NotInitialized    CurrentState=Configuring
    Messages From appSMM Should Use Topic    SystemStatusNotification
    Received Messages Should Be Schema Valid

SDS-2551270 NotInitialized Is Notified After SetConfigurationResponse
    [Documentation]    If the SetConfigurationResponse message was published over the is/iw/tx topic,
    ...    appSMM software automatically publishes the SystemStatusNotification message over the
    ...    is/iw/tx topic, which notifies that system state changed from "Configuring" to "NotInitialized".
    [Tags]    SDS-2551270    spechash:9f824f08    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    Send ICD Message    SetConfigurationRequest    body={"STI.Barcode.Code128": "Enabled"}
    Wait For Message Sequence
    ...    SetConfigurationResponse | @topic=/is/iw/tx
    ...    SystemStatusNotification | PreviousState=Configuring | CurrentState=NotInitialized | @topic=/is/iw/tx
    ...    timeout=${RESPONSE_TIMEOUT}
    Received Messages Should Be Schema Valid
    System State Should Be    NotInitialized

SDS-2535547 NormalOperation Is Notified When A Loaded Rack Is Picked Up In Idle
    [Documentation]    If a loaded rack was picked up by the Distributor either from Input lane or from
    ...    FrontIn while the system status is "Idle", appSMM software publishes a SystemStatusNotification
    ...    message over the is/iw/tx topic with the following details:
    ...    - "PreviousState":"Idle"
    ...    - "CurrentState":"NormalOperation"
    [Tags]    SDS-2535547    spechash:1ccfe824    needs:twin    review:pending
    Require Hardware Twin
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Trigger Hardware Action    insertFrontIn    rackId=A001    firstSample=1
    Wait For Message    SystemStatusNotification    timeout=${INIT_TIMEOUT}
    ...    PreviousState=Idle    CurrentState=NormalOperation
    Messages From appSMM Should Use Topic    SystemStatusNotification
    Received Messages Should Be Schema Valid
    [Teardown]    Finish SMM Test And Empty The Instrument

SDS-2653086 E-Stop After A Fatal Error Is Notified From Idle
    [Documentation]    If the SMM system goes to E-Stop following a fatal error, appSMM software publishes
    ...    a SystemStatusNotification message which notifies that system status changed from "Idle"
    ...    (PreviousState) to "E-Stop" (CurrentState).
    ...    The fatal error is the hardware emergency stop of the twin.
    [Tags]    SDS-2653086    spechash:068ff31d    needs:twin    review:pending
    Require Hardware Twin
    Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
    Trigger Emergency Stop
    Wait For Message    SystemStatusNotification    timeout=${RESPONSE_TIMEOUT}
    ...    PreviousState=Idle    CurrentState=E-Stop
    Received Messages Should Be Schema Valid

SDS-2528708 SystemStatus Messages Are Logged With Topic And Content
    [Documentation]    The SystemStatusRequest, SystemStatusResponse and SystemStatusNotification messages are
    ...    logged in appSMM log files. The message topic and content of each exchanged message is included in
    ...    the log files.
    ...    Checked in appSMM's SmartInspect log (trace.config next to appSMM.exe): every request as an
    ...    RX(/is/iw/rx) line, every response and notification (the restart's NotInitialized notification) as a
    ...    TX(/is/iw/tx) line, each with the exchanged JSON content.
    [Tags]    SDS-2528708    spechash:4786b9fb    needs:applog    requires:restart    review:pending
    Restart appSMM And Wait Until NotInitialized
    Request And Wait For Response    SystemStatusRequest    timeout=${RESPONSE_TIMEOUT}
    ICD Messages Should Be Logged By appSMM
    ...    SystemStatusRequest    SystemStatusResponse    SystemStatusNotification    timeout=${RESPONSE_TIMEOUT}
