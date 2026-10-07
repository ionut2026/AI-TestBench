"""smm-auto doctor: checks that a tier can run before a long run starts (plan 0.1 / 6.4).

Without ``--deep`` it does not talk to appSMM: it checks the service, the TestBench pin, the tier's prerequisites
(mock: starts and stops the mock environment; offline: appSMM.exe, port 1883; rig: broker reachable, rig control,
operator) and lists the tags the run would exclude. ``--deep`` also starts the environment, connects as Bridge and
asks appSMM for its state and version. On the rig that announces a Bridge to the real instrument, so it is opt-in;
there it then listens for a few seconds for messages another SMM Bridge (an SMM UI, a real Bridge) sends to appSMM.
"""

from __future__ import annotations

import os
import runpy
import socket
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smm_automation import FRAMEWORK_ROOT
from smm_automation.capabilities import excluded_tags, tier_capabilities
from smm_automation.client import SERVICE_BUNDLE, ServiceClient, ServiceError, ensure_service
from smm_automation.rigcontrol import RigControl, RigControlError

DEFAULT_APPSMM_EXE = Path(r"D:\smm\appSMM\appSMM.exe")
DEFAULT_RIG_BROKER = "10.0.1.111:1883"
OFFLINE_BROKER = ("127.0.0.1", 1883)


@dataclass
class Check:
    name: str
    status: str  # OK | WARN | FAIL | SKIP
    detail: str


def tcp_open(host: str, port: int, timeout_s: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _environment_file(tier: str) -> dict[str, Any]:
    return runpy.run_path(str(FRAMEWORK_ROOT / "robot" / "environments" / f"{tier}.py"))


def _seconds(value: Any, default: float) -> float:
    text = str(value or "").strip().lower()
    try:
        return float(text[:-1] if text.endswith("s") else text)
    except ValueError:
        return default


class Doctor:
    def __init__(self, tier: str, operator: str = "none", deep: bool = False, client: ServiceClient | None = None,
                 env: Mapping[str, str] | None = None, autostart: bool = True, port_open: Callable[[str, int], bool] = tcp_open,
                 is_tty: Callable[[], bool] | None = None, environment_file: Callable[[str], dict] = _environment_file,
                 listen_s: float = 5.0, sleep: Callable[[float], None] = time.sleep):
        self.tier, self.operator, self.deep = tier, operator, deep
        self.client = client or ServiceClient()
        self.env = os.environ if env is None else env
        self.autostart = autostart
        self.port_open = port_open
        self.is_tty = is_tty or (lambda: bool(sys.__stdin__ and sys.__stdin__.isatty()))
        self.environment_file = environment_file
        self.listen_s, self.sleep = listen_s, sleep
        self.checks: list[Check] = []

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name, status, detail))

    def run(self) -> list[Check]:
        service_ok = self._service()
        busy = self._environment() if service_ok else True
        rig_control = None
        if self.tier == "mock" and service_ok and not busy:
            self._start_stop()
        elif self.tier == "offline":
            self._offline(busy)
        elif self.tier == "rig":
            rig_control = self._rig()
        self._operator()
        self._capabilities(rig_control)
        if self.deep:
            if not service_ok:
                self.add("deep check", "SKIP", "the service is not available")
            elif busy:
                self.add("deep check", "SKIP", "an environment is already running")
            else:
                self._deep()
        return self.checks

    # ------------------------------------------------------------------ checks

    def _service(self) -> bool:
        if SERVICE_BUNDLE.exists():
            self.add("service bundle", "OK", str(SERVICE_BUNDLE))
        elif self.client.is_up():
            self.add("service bundle", "WARN", f"{SERVICE_BUNDLE} is missing (a service is running at {self.client.url})")
        else:
            self.add("service bundle", "FAIL", f"{SERVICE_BUNDLE} is missing: run 'npm ci' and 'npm run build' in {SERVICE_BUNDLE.parent.parent}")
            return False
        try:
            health = ensure_service(self.client, autostart=self.autostart)
        except ServiceError as err:
            self.add("service", "FAIL", str(err))
            return False
        self.add("service", "OK", f"API {health.get('apiVersion')}, ICD {health.get('icdVersion')} at {self.client.url}")
        tb = health.get("testbench") or {}
        commit, pinned = str(tb.get("commit") or "?")[:12], str(tb.get("pinnedCommit") or "?")[:12]
        if not tb.get("pinned"):
            status = "WARN" if self.tier == "mock" else "FAIL"
            self.add("TestBench pin", status, f"bundled TestBench {commit} is not the pinned {pinned} (testbench.lock.json); "
                     "results are not reproducible" + ("" if status == "WARN" else ": rebuild the service at the pinned commit"))
        elif tb.get("dirty"):
            self.add("TestBench pin", "WARN", f"{commit} (pinned) built from a working tree with local changes")
        else:
            self.add("TestBench pin", "OK", f"{commit} (pinned)")
        return True

    def _environment(self) -> bool:
        try:
            status = self.client.environment()
        except ServiceError as err:
            self.add("environment", "FAIL", str(err))
            return True
        if status.get("running"):
            self.add("environment", "WARN", f"a {status.get('tier')} environment is already running (another run?): start checks skipped")
            return True
        self.add("environment", "OK", "no environment running")
        return False

    def _start_stop(self) -> None:
        try:
            status = self.client.start_environment(self.tier, self.environment_file(self.tier).get("OVERRIDES") or {})
            running = (status.get("appSmm") or {}).get("running")
            self.add("start environment", "OK" if running else "FAIL", f"{self.tier}: appSMM {'running' if running else 'not running'}")
        except ServiceError as err:
            self.add("start environment", "FAIL", str(err))
        finally:
            self._stop()

    def _stop(self) -> None:
        try:
            self.client.stop_environment()
        except ServiceError as err:
            self.add("stop environment", "FAIL", str(err))

    def _offline(self, busy: bool) -> None:
        configured = self.env.get("SMM_APPSMM_EXE")
        exe = Path(configured) if configured else DEFAULT_APPSMM_EXE
        if exe.is_file():
            self.add("appSMM.exe", "OK", str(exe))
        elif configured:
            self.add("appSMM.exe", "FAIL", f"SMM_APPSMM_EXE={configured} does not exist")
        else:
            self.add("appSMM.exe", "WARN", f"{exe} not found: set SMM_APPSMM_EXE unless the TestBench runner settings point elsewhere")
        if not busy:
            host, port = OFFLINE_BROKER
            if self.port_open(host, port):
                self.add("broker port", "WARN", f"something already listens on {host}:{port}; the offline tier starts its own Mosquitto there")
            else:
                self.add("broker port", "OK", f"{host}:{port} is free")

    def _rig(self) -> RigControl | None:
        host, _, port = (self.env.get("SMM_RIG_BROKER") or DEFAULT_RIG_BROKER).partition(":")
        if self.port_open(host, int(port or 1883)):
            self.add("rig broker", "OK", f"{host}:{port or 1883} reachable")
        else:
            self.add("rig broker", "FAIL", f"{host}:{port or 1883} not reachable (SMM_RIG_BROKER)")
        try:
            control = RigControl.from_env(self.env)
        except RigControlError as err:
            self.add("rig control", "FAIL", str(err))
            return None
        if control is None:
            self.add("rig control", "WARN", "SMM_RIG_CONTROL is not set: restart, broker-restart and applog tests will be excluded")
            return None
        try:
            caps = control.capabilities()
        except RigControlError as err:
            self.add("rig control", "FAIL", str(err))
            return None
        self.add("rig control", "OK", f"'{control}': {' '.join(sorted(caps)) or 'no subcommands'}")
        return control

    def _operator(self) -> None:
        if self.operator == "none":
            if self.tier == "rig":
                self.add("operator", "WARN", "no operator (--operator console|dialog): hardware-action and operator tests will be excluded")
            return
        if self.operator == "console" and not self.is_tty():
            self.add("operator", "WARN", "console mode, but stdin is not a terminal: nobody can type 'done'")
        elif self.operator == "dialog":
            try:
                import tkinter  # noqa: F401
            except ImportError:
                self.add("operator", "FAIL", "dialog mode needs tkinter, which this Python does not have")
                return
            self.add("operator", "OK", "dialog")
        else:
            self.add("operator", "OK", self.operator)

    def _capabilities(self, rig_control: RigControl | None) -> None:
        env = {k: v for k, v in self.env.items() if k != "SMM_RIG_CONTROL"} if self.tier == "rig" and rig_control is None else self.env
        try:
            caps, _ = tier_capabilities(self.tier, self.operator, env, rig_control)
        except RigControlError as err:
            self.add("capabilities", "FAIL", str(err))
            return
        excluded = excluded_tags(caps)
        self.add("capabilities", "OK", f"{', '.join(sorted(caps)) or 'none'}; excluded tags: {', '.join(excluded) or 'none'}")

    def _deep(self) -> None:
        settings = self.environment_file(self.tier)
        startup = _seconds(settings.get("STARTUP_TIMEOUT"), 60)
        try:
            self.client.start_environment(self.tier, settings.get("OVERRIDES") or {})
            self.client.connect(startup)
            try:
                state = self._ask("SystemStatusRequest", "SystemStatusResponse")
                version = self._ask("GetVersionRequest", "GetVersionResponse")
                i = version.get("Integration", {})
                self.add("appSMM", "OK", f"state {state.get('CurrentState')}, version "
                         f"{i.get('Major')}.{i.get('Minor')}.{i.get('Build')}.{i.get('Revision')}")
                if self.tier == "rig":
                    self._other_bridge()
            finally:
                self.client.disconnect()
        except ServiceError as err:
            self.add("appSMM", "FAIL", str(err))
        finally:
            self._stop()

    def _other_bridge(self) -> None:
        """The service counts messages on appSMM's receive topics it did not publish itself. A connected but
        silent Bridge cannot be seen; the test teardown repeats the check for every test."""
        self.sleep(self.listen_s)
        other = self.client.session().get("otherBridge")
        if other:
            self.add("other Bridge", "FAIL", f"another SMM Bridge published {other.get('count')} message(s) to appSMM "
                     f"(last: {str(other.get('lastMessage', ''))[:120]}): disconnect it (SMM UI / real Bridge) before a run")
        else:
            self.add("other Bridge", "OK", f"no other Bridge traffic within {self.listen_s:g} s")

    def _ask(self, request: str, response: str) -> dict:
        mark = self.client.mark()
        self.client.send(request, {})
        return self.client.wait({"name": response, "way": "rx", "since": mark}, 10)["body"]


def run_doctor(tier: str, operator: str = "none", deep: bool = False, **kwargs: Any) -> list[Check]:
    return Doctor(tier, operator, deep, **kwargs).run()


def format_checks(checks: list[Check]) -> str:
    width = max((len(c.name) for c in checks), default=0)
    return "\n".join(f"{c.status:<4}  {c.name:<{width}}  {c.detail}" for c in checks)
