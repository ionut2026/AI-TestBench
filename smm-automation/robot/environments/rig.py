"""Tier `rig`: the dedicated automation instrument (real appSMM on the RTC board, real hardware).

The broker address is site specific: set SMM_RIG_BROKER (host or host:port).
SMM_RIG_CONTROL (a site script, see smm_automation/rigcontrol.py) enables the appSMM/broker restart and log file
tests; ``smm-auto run --operator console|dialog`` (or SMM_OPERATOR) the emergency stop and other operator steps.
Check the set-up first with ``smm-auto doctor --tier rig``.
"""

import os

_host, _, _port = os.environ.get("SMM_RIG_BROKER", "10.0.1.111:1883").partition(":")

TIER = "rig"
OVERRIDES = {"broker": {"host": _host, "port": int(_port or 1883)}}
RESPONSE_TIMEOUT = "20s"
STARTUP_TIMEOUT = "120s"
INIT_TIMEOUT = "600s"
RECOVER_TIMEOUT = "120s"
QUIET_PERIOD = "5s"
