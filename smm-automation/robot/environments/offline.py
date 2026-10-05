"""Tier `offline`: Mosquitto + the real appSMM.exe + the SMM hardware twin on this PC (no instrument).

appSMM.exe location and the twin settings come from the SMM TestBench runner settings; override with
e.g. ``OVERRIDES = {"runner": {"appSmmExe": r"D:\\smm\\appSMM\\appSMM.exe"}}``.
"""

import os

TIER = "offline"
OVERRIDES = {"runner": {"appSmmExe": os.environ["SMM_APPSMM_EXE"]}} if os.environ.get("SMM_APPSMM_EXE") else {}
RESPONSE_TIMEOUT = "20s"
STARTUP_TIMEOUT = "90s"
INIT_TIMEOUT = "300s"
RECOVER_TIMEOUT = "90s"
QUIET_PERIOD = "5s"
