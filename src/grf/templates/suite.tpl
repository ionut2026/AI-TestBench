*** Settings ***
Library         product_automation.ProductLibrary
Test Setup      Reset Counter
Metadata        Tier    ${TIER}

*** Test Cases ***
__SPEC_PREFIX__-1 Increment Changes The Counter By One
    [Documentation]    When increment is requested, the counter increases by exactly one.
    [Tags]    __SPEC_PREFIX__-1    spechash:__SPEC_HASH__    review:pending
    Counter Should Be    0
    Request Increment
    Counter Should Be    1
