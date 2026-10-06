"""Tier `mock`: embedded broker + scripted appSMM. Verdicts check the framework, NOT the product.

``SMM_MOCK_FAULTS`` (JSON list of fault rules, set by ``smm-auto mutate``) makes the mock appSMM
misbehave for mutation testing; see catalog/mutants.toml.
"""

import json
import os

TIER = "mock"
_FAULTS = os.environ.get("SMM_MOCK_FAULTS")
OVERRIDES = {"mock": {"faults": json.loads(_FAULTS)}} if _FAULTS else {}
RESPONSE_TIMEOUT = "5s"
STARTUP_TIMEOUT = "15s"
INIT_TIMEOUT = "30s"
RECOVER_TIMEOUT = "15s"
QUIET_PERIOD = "2s"
