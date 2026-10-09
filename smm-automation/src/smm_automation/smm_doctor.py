"""SMM checks of ``smm-auto doctor`` (the generic ones are in doctor.py).

Without ``--deep`` it does not talk to appSMM: it checks the service, the TestBench pin, the tier's prerequisites
(mock: starts and stops the mock environment; offline: appSMM.exe, port 1883; rig: broker reachable, then the generic
rig control and operator checks) and lists the tags the run would exclude. ``--deep`` also starts the environment,
connects as Bridge and asks appSMM for its state and version. On the rig that announces a Bridge to the real
instrument, so it is opt-in; there it then listens for a few seconds for messages another SMM Bridge (an SMM UI, a real
Bridge) sends to appSMM.
"""

from __future__ import annotations

import runpy
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from smm_automation import project
from smm_automation.client import SERVICE_BUNDLE, ServiceClient, ServiceError, ensure_service
from smm_automation.doctor import Doctor, tcp_open

DEFAULT_APPSMM_EXE = Path(r"D:\smm\appSMM\appSMM.exe")
DEFAULT_RIG_BROKER = "10.0.1.111:1883"
OFFLINE_BROKER = ("127.0.0.1", 1883)


def _environment_file(tier: str) -> dict[str, Any]:
    return runpy.run_path(str(project.current().path("environments") / f"{tier}.py"))


def _seconds(value: Any, default: float) -> float:
    text = str(value or "").strip().lower()
    try:
        return float(text[:-1] if text.endswith("s") else text)
    except ValueError:
        return default


class SmmDoctor(Doctor):
    def __init__(self, tier: str, operator: str = "none", deep: bool = False, client: ServiceClient | None = None,
                 env: Mapping[str, str] | None = None, autostart: bool = True, port_open: Callable[[str, int], bool] = tcp_open,
                 is_tty: Callable[[], bool] | None = None, environment_file: Callable[[str], dict] = _environment_file,
                 listen_s: float = 5.0, sleep: Callable[[float], None] = time.sleep):
        super().__init__(tier, operator, deep, env=env, port_open=port_open, is_tty=is_tty, sleep=sleep)
        self.client = client or ServiceClient()
        self.autostart = autostart
        self.environment_file = environment_file
        self.listen_s = listen_s
        self.service_ok = False
        self.busy = True

    def before(self) -> None:
        self.service_ok = self._service()
        self.busy = self._environment() if self.service_ok else True
        if self.tier == "mock" and self.service_ok and not self.busy:
            self._start_stop()
        elif self.tier == "offline":
            self._offline(self.busy)
        elif self.tier == "rig":
            self._rig_broker()

    def deep_check(self) -> None:
        if not self.service_ok:
            self.add("deep check", "SKIP", "the service is not available")
        elif self.busy:
            self.add("deep check", "SKIP", "an environment is already running")
        else:
            self._deep()

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

    def _rig_broker(self) -> None:
        host, _, port = (self.env.get("SMM_RIG_BROKER") or DEFAULT_RIG_BROKER).partition(":")
        if self.port_open(host, int(port or 1883)):
            self.add("rig broker", "OK", f"{host}:{port or 1883} reachable")
        else:
            self.add("rig broker", "FAIL", f"{host}:{port or 1883} not reachable (SMM_RIG_BROKER)")

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
