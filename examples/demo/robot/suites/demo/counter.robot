*** Settings ***
Library         product_automation.ProductLibrary
Test Setup      Reset Counter
Metadata        Tier    ${TIER}

*** Test Cases ***
SPEC-1 Increment Changes The Counter By One
    [Documentation]    When increment is requested, the counter increases by exactly one.
    [Tags]    SPEC-1    spechash:5404e433    review:pending
    Counter Should Be    0
    Request Increment
    Counter Should Be    1
