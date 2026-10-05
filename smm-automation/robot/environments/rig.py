"""Tier `rig`: the dedicated automation instrument (real appSMM on the RTC board, real hardware).

The broker address is site specific: set SMM_RIG_BROKER (host or host:port).
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
