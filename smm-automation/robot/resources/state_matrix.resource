*** Settings ***
Documentation       Keywords of the generated state x request matrix suite (``smm-auto matrix``, model in
...                 ``catalog/state-matrix.toml``). The template sends one request in one precondition state,
...                 observes what appSMM does and checks the cell's specified expectation; whatever the
...                 specifications leave open is logged as a warning, and a cell without any expectation is
...                 skipped with the observed behaviour.
Resource            smm.resource


*** Keywords ***
Request In State Should Behave    # robocop: off=too-many-arguments
    [Documentation]    One matrix cell: ``request`` (optional JSON ``body``) sent in ``state``. ``response``
    ...    is ``Name | Field=Value | ...``, ``none`` or empty (unspecified); ``next_state`` a state,
    ...    ``unchanged`` or empty; ``no_command`` a COP command appSMM must not send (checked where the
    ...    hardware twin is present). The open questions are the ``Open:`` part of the test documentation.
    [Arguments]    ${state}    ${request}    ${body}    ${response}    ${next_state}    ${no_command}
    Bring SMM To Matrix State    ${state}
    ${outcome}=    Observe Request Outcome    ${request}    body=${body}    timeout=${RESPONSE_TIMEOUT}
    ...    observe=${QUIET_PERIOD}    settle=${INIT_TIMEOUT}
    VAR    ${checked}=    ${False}
    IF    $no_command
        ${env}=    Get Environment Status
        IF    '${env}[hardware][kind]' == 'twin'
            Hardware Command Should Not Be Sent    ${no_command}    duration=${SHORT_QUIET_PERIOD}
            VAR    ${checked}=    ${True}
        END
    END
    Request Outcome Should Match    ${outcome}    ${state}    response=${response}    next_state=${next_state}
    ...    questions=${TEST_DOCUMENTATION}    checked=${checked}

Bring SMM To Matrix State
    [Documentation]    Precondition of a matrix cell. NotInitialized needs an appSMM restart (Recover
    ...    re-initializes), so those cells are tagged ``requires:restart``.
    [Arguments]    ${state}
    IF    $state == 'NotInitialized'
        Restart appSMM And Wait Until NotInitialized
    ELSE IF    $state == 'E-Stop'
        ${current}=    Get System State    timeout=${RESPONSE_TIMEOUT}
        IF    $current == 'NotInitialized'
            Bring SMM To State    Idle    timeout=${INIT_TIMEOUT}
        END
        Bring SMM To State    E-Stop    timeout=${INIT_TIMEOUT}
    ELSE
        Bring SMM To State    ${state}    timeout=${INIT_TIMEOUT}
    END
