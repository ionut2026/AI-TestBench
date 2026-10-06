"""Robot Framework keywords for testing appSMM through the SMM automation service.

The test plays the SMM Bridge (plus IW and analyzers) towards appSMM over MQTT, and drives the
(simulated or real) hardware. Every keyword talks to the service's HTTP API; the service runs the
SMM TestBench engine (Bridge beacon, ICD schemas, hardware twin) at a pinned commit.

Message matching:
    ``Wait For Message    SystemStatusNotification    CurrentState=Idle``
    keyword arguments are a partial match on the message body; values are read as JSON when they
    parse (``EventId=15859714`` is a number, ``EventArgs=[]`` an empty list), dotted keys reach into
    nested objects (``Module.Status=Ready``), and ``match=`` takes a full JSON object or dict.
    Operators: ``Severity={"$in": ["Warning", "CriticalError"]}``, ``Message={"$regex": "lost"}``.

Message specs (sequences): ``SystemStatusNotification | PreviousState=Idle | CurrentState=E-Stop``.
    Filter fields other than the body start with ``@``: ``@way=tx``, ``@analyzer=0``, ``@topic=/is/iw/tx``.

Time windows: every send records a mark; waits look at messages after the last mark unless
``since=`` is given (``since=test`` = since the test started, ``since=0`` = whole timeline).
"""

from __future__ import annotations

import html
import json
import time
from typing import Any

from robot.api import logger
from robot.api.deco import keyword, library
from robot.libraries.BuiltIn import BuiltIn
from robot.utils import timestr_to_secs

from smm_automation.client import DEFAULT_URL, ServiceClient, ServiceError, ensure_service

STATES = ("PowerOn", "NotInitialized", "Initializing", "Idle", "NormalOperation", "E-Stop", "Configuring", "Clearing")
FILTER_FIELDS = {"way", "topic", "analyzer", "valid", "since"}


@library(scope="GLOBAL", auto_keywords=False)
class SMMTestbench:
    def __init__(self, url: str = DEFAULT_URL, autostart: bool = True):
        self.client = ServiceClient(url)
        self.autostart = str(autostart).lower() not in ("false", "no", "0")
        self._mark: int | None = None
        self._test_mark = 0
        self._tier: str | None = None
        self._trace_mark = 0
        self._test_started_ms = 0

    # ================================================================== service and environment

    @keyword
    def ensure_automation_service_is_running(self) -> dict:
        """Checks the service (starting the local one if allowed) and records its TestBench pin as metadata."""
        from smm_automation import FRAMEWORK_ROOT

        out_dir = _robot_var("${OUTPUT DIR}", str(FRAMEWORK_ROOT / "results"))
        from pathlib import Path

        health = ensure_service(self.client, self.autostart, Path(out_dir) / "automation-service.log")
        tb = health["testbench"]
        pin = f"{(tb.get('commit') or '?')[:12]}{' (dirty)' if tb.get('dirty') else ''}"
        if not tb.get("pinned"):
            logger.warn(f"SMM TestBench {pin} is not the pinned commit {tb['pinnedCommit'][:12]}")
        _metadata("SMM TestBench", pin)
        _metadata("Automation API", health["apiVersion"])
        _metadata("ICD version", str(health["icdVersion"]))
        return health

    @keyword
    def start_test_environment(self, tier: str = "mock", overrides: Any = None) -> dict:
        """Starts the environment for ``tier``: ``mock`` (embedded broker + mock appSMM, framework
        self-test only), ``offline`` (Mosquitto + real appSMM.exe + hardware twin) or ``rig`` (the
        automation instrument; broker and appSMM run on it). ``overrides`` is a dict or JSON, e.g.
        ``{"broker": {"host": "10.0.1.20"}}``."""
        self.ensure_automation_service_is_running()
        status = self.client.start_environment(tier, _as_obj(overrides) or {})
        self._tier = tier
        _metadata("Tier", tier + (" (framework self-test: NOT product evidence)" if tier == "mock" else ""))
        logger.info(f"Environment: {json.dumps(status, indent=1)}")
        return status

    @keyword
    def stop_test_environment(self) -> None:
        try:
            self.client.stop_environment()
        except ServiceError as err:
            logger.warn(f"Stopping the environment failed: {err}")

    @keyword
    def get_environment_status(self) -> dict:
        return self.client.environment()

    @keyword
    def current_tier_should_be(self, *tiers: str) -> None:
        tier = self.client.environment().get("tier")
        if tier not in tiers:
            raise AssertionError(f"Tier is {tier}, expected one of {', '.join(tiers)}")

    @keyword
    def restart_appsmm(self, down: str = "1s") -> None:
        """Kills appSMM (no goodbye message) and starts it again after ``down``."""
        self._mark = self.client.mark()
        self.client.restart_appsmm(int(timestr_to_secs(down) * 1000))

    @keyword
    def restart_mqtt_broker(self, down: str = "2s") -> None:
        """Takes the MQTT broker down for ``down``: appSMM and the Bridge both lose the connection."""
        self._mark = self.client.mark()
        self.client.restart_broker(int(timestr_to_secs(down) * 1000))

    # ================================================================== Bridge session

    @keyword
    def connect_as_bridge(self, timeout: str = "10s", clear: bool = True, record_version: bool = True) -> dict:
        """Connects the Bridge (announces SMMBridge, IW and the enabled analyzers as Connected)."""
        self._mark = None if _truthy(clear) else self.client.mark()
        snapshot = self.client.connect(timestr_to_secs(timeout), clear=_truthy(clear))
        if _truthy(clear):
            self._test_mark = 0
        if _truthy(record_version):
            self._record_appsmm_version()
        return snapshot

    @keyword
    def disconnect_bridge(self, abrupt: bool = False) -> None:
        """Disconnects the Bridge. ``abrupt=True`` drops the TCP connection so the broker publishes
        the Bridge's last will (``ConnectionNotification SMMBridge Disconnected``), like a crash."""
        self._mark = self.client.mark()
        self.client.disconnect(_truthy(abrupt))

    @keyword
    def bridge_should_be_connected(self) -> None:
        link = self.client.session()["link"]
        if link != "connected":
            raise AssertionError(f"Bridge link is {link}")

    @keyword
    def interrupt_bridge_connection(self, outage: str = "3s", timeout: str = "10s", abrupt: bool = True) -> dict:
        """Simulates a lost Bridge: disconnects (``abrupt``: without goodbye, so the broker publishes the
        last will), stays away for ``outage`` and connects again. The timeline is kept (evidence from
        before the outage stays in the log); waits afterwards look at what comes after the reconnection.
        The outage is the stimulus; nothing can be observed while the Bridge is away."""
        self.disconnect_bridge(abrupt)
        time.sleep(timestr_to_secs(outage))
        return self.connect_as_bridge(timeout, clear=False, record_version=False)

    def _record_appsmm_version(self) -> None:
        try:
            mark = self.client.mark()
            self.client.send("GetVersionRequest", {})
            body = self.client.wait({"name": "GetVersionResponse", "since": mark}, 5)["body"]
            i = body.get("Integration", {})
            _metadata("appSMM version", f"{i.get('Major')}.{i.get('Minor')}.{i.get('Build')}.{i.get('Revision')}")
        except ServiceError as err:
            logger.info(f"appSMM version not recorded: {err}")

    # ================================================================== sending

    @keyword
    def mark_timeline(self) -> int:
        """Waits after this look only at newer messages. Returns the mark."""
        self._mark = self.client.mark()
        return self._mark

    @keyword
    def send_icd_message(self, name: str, body: Any = None, analyzer: int = -1, strict: bool = True, **fields: Any) -> dict:
        """Publishes an ICD message to appSMM (IW topic, or ``analyzer=N`` for /is/hcaN/rx).
        The body is ``body=`` (dict/JSON) merged with keyword fields. ``strict`` refuses bodies
        that break the ICD schema; use `Send Raw Payload` for negative tests."""
        payload = {**(_as_obj(body) or {}), **_match_from(fields)}
        self._remember_trace()
        self._mark = self.client.mark()
        entry = self.client.send(name, payload, int(analyzer), _truthy(strict))
        logger.info(f"Sent {name}: {json.dumps(payload)}")
        return entry

    @keyword
    def send_raw_payload(self, topic: str, raw: str) -> dict:
        """Publishes any text on any topic (invalid JSON, wrong schema, wrong topic...)."""
        self._mark = self.client.mark()
        return self.client.send_raw(topic, raw)

    @keyword
    def request_and_wait_for_response(self, request: str, response: str | None = None, timeout: str = "10s",
                                      body: Any = None, analyzer: int = -1, **match: Any) -> dict:
        """Sends ``request`` and returns the body of the next ``response`` (default: Request -> Response)
        matching the given fields."""
        response = response or request.replace("Request", "Response")
        self.send_icd_message(request, body, analyzer)
        return self.wait_for_message(response, timeout=timeout, **match)

    # ================================================================== waiting and expecting

    @keyword
    def wait_for_message(self, name: str, timeout: str = "10s", since: Any = None, way: str = "rx", match: Any = None, **fields: Any) -> dict:
        """Waits for a message (from appSMM by default) and returns its body."""
        entry = self.wait_for_message_entry(name, timeout, since, way, match, **fields)
        return entry["body"]

    @keyword
    def wait_for_message_entry(self, name: str, timeout: str = "10s", since: Any = None, way: str = "rx", match: Any = None, **fields: Any) -> dict:
        """Like `Wait For Message` but returns the whole timeline entry (id, time, topic, body, valid, errors)."""
        flt = self._filter(name, way, since, match, fields)
        entry = self._wait(flt, timeout)
        entry.setdefault("errors", [])
        logger.info(f"Got #{entry['id']} {entry['name']} on {entry['topic']} at {entry['time']}: {json.dumps(entry['body'])}")
        return entry

    @keyword
    def wait_for_message_sequence(self, *specs: Any, timeout: str = "30s", since: Any = None) -> list:
        """Waits for messages in this order (others may come in between). Each spec is
        ``Name | Field=Value | ...`` or a filter dict. Returns the matched entries."""
        filters = [self._spec(s) for s in specs]
        start = self._since(since)
        try:
            entries = self.client.wait_sequence(filters, timestr_to_secs(timeout), start)
        except ServiceError as err:
            self._fail_with_context(err)
        for e in entries:
            e.setdefault("errors", [])
            logger.info(f"#{e['id']} {e['time']} {e['name']}: {json.dumps(e['body'])}")
        return entries

    @keyword
    def message_should_not_arrive(self, name: str, duration: str = "3s", since: Any = None, way: str = "rx", match: Any = None, **fields: Any) -> None:
        """Fails if a matching message is already on the timeline (after the mark) or arrives within ``duration``."""
        flt = self._filter(name, way, since, match, fields)
        try:
            self.client.expect_none(flt, timestr_to_secs(duration))
        except ServiceError as err:
            self._fail_with_context(err)

    @keyword
    def messages_should_have_been_received(self, name: str, count: int | None = None, since: Any = None, way: str = "rx", match: Any = None, **fields: Any) -> list:
        """Returns the matching messages already on the timeline; checks the count if given."""
        found = self.client.query(self._filter(name, way, since, match, fields))
        if count is not None and len(found) != int(count):
            raise AssertionError(f"Expected {count} {name}, got {len(found)}: {[e['body'] for e in found]}")
        return found

    # ================================================================== system state

    @keyword
    def get_system_state(self, timeout: str = "5s") -> str:
        """Asks appSMM (SystemStatusRequest) and returns CurrentState."""
        return self.request_and_wait_for_response("SystemStatusRequest", timeout=timeout)["CurrentState"]

    @keyword
    def system_state_should_be(self, expected: str, timeout: str = "5s") -> None:
        _check_state(expected)
        actual = self.get_system_state(timeout)
        if actual != expected:
            raise AssertionError(f"System state is {actual}, expected {expected}")

    @keyword
    def wait_for_system_state(self, expected: str, timeout: str = "60s", since: Any = None) -> dict:
        """Waits for a SystemStatusNotification with CurrentState ``expected``."""
        _check_state(expected)
        return self.wait_for_message("SystemStatusNotification", timeout=timeout, since=since, CurrentState=expected)

    @keyword
    def bring_smm_to_state(self, target: str = "Idle", timeout: str = "120s") -> None:
        """Test precondition: drives appSMM to ``NotInitialized``, ``Idle`` or ``E-Stop`` with the ICD
        (Initialization / Recover / Shutdown requests). Not a verification step: it accepts both a
        Recover that re-initializes on its own and one that stops in NotInitialized, and reaches Idle
        from any operating state (e.g. NormalOperation) through Shutdown -> E-Stop -> Recover."""
        _check_state(target)
        if target not in ("NotInitialized", "Idle", "E-Stop"):
            raise ValueError(f"Bring SMM To State supports NotInitialized, Idle and E-Stop, not {target}")
        budget = timestr_to_secs(timeout)
        state = None
        for _ in range(5):
            state = self._settled_state(budget)
            if state == target:
                return
            if state == "E-Stop":
                self.request_and_wait_for_response("RecoverRequest", timeout="60s", Status="OK")
                self.wait_for_system_state_change("E-Stop", timeout="60s")
            elif state == "NotInitialized" and target == "Idle":
                self.request_and_wait_for_response("InitializationRequest", timeout="30s", Status="OK")
                self._wait_idle_after_clearing(budget)
            elif state == "NotInitialized":
                raise AssertionError("Cannot bring appSMM from NotInitialized to E-Stop through the ICD")
            else:
                # Idle, NormalOperation, ... -> E-Stop; Recover follows on the next round if needed.
                self.request_and_wait_for_response("ShutdownRequest", timeout="30s")
                self.wait_for_system_state("E-Stop", timeout="30s")
        raise AssertionError(f"appSMM did not reach {target} (last state {state})")

    def wait_for_system_state_change(self, previous: str, timeout: str = "60s") -> dict:
        """Waits for a SystemStatusNotification leaving ``previous`` (any new state)."""
        return self.wait_for_message("SystemStatusNotification", timeout=timeout, PreviousState=previous)

    def _settled_state(self, budget: float) -> str:
        """Current state, waiting while appSMM is in a transient state (PowerOn, Initializing, Clearing, Configuring)."""

        deadline = time.monotonic() + budget
        previous = None
        while True:
            state = self.get_system_state()
            if time.monotonic() > deadline:
                return state
            if state not in ("PowerOn", "Initializing", "Clearing", "Configuring"):
                # Idle is also briefly passed between Initializing and Clearing: require two equal reads.
                if state == previous:
                    return state
                previous = state
                time.sleep(0.5)
                continue
            previous = None
            time.sleep(1)

    def _wait_idle_after_clearing(self, budget: float) -> None:
        self.wait_for_message_sequence(
            "SystemStatusNotification | CurrentState=Clearing",
            "SystemStatusNotification | PreviousState=Clearing | CurrentState=Idle",
            timeout=f"{budget}s",
        )

    # ================================================================== hardware

    @keyword
    def trigger_hardware_action(self, action: str, **args: Any) -> dict:
        """Operator/hardware action on the hardware twin (offline tier): emergencyStop, loadInputTray,
        removeInputTray, insertOutputTray, removeOutputTray, insertFrontIn, removeFrontIn,
        removeFrontOut, pauseLane, resumeLane, toggleLaneError (area=Input|Output), toggleOutputAvailable."""
        self._mark = self.client.mark()
        return self.client.hardware_action(action, {k: _coerce(v) for k, v in args.items()})

    @keyword
    def trigger_emergency_stop(self) -> dict:
        """Presses the E-Stop on the hardware twin (the twin goes to Halted)."""
        return self.trigger_hardware_action("emergencyStop")

    @keyword
    def get_hardware_snapshot(self) -> dict:
        return self.client.hardware()

    @keyword
    def hardware_state_should_be(self, expected: str) -> None:
        actual = self.client.hardware()["state"]
        if actual != expected:
            raise AssertionError(f"Hardware twin state is {actual}, expected {expected}")

    @keyword
    def clear_hardware_twin_racks(self) -> list:
        """Clean-up: takes every rack off the hardware twin by hand (trays, FrontIn/Out, lanes, buffers),
        so later tests start with an empty instrument. Returns the removed rack ids."""
        removed: list[str] = []
        try:
            racks = self.client.hardware().get("racks", [])
        except ServiceError as err:
            if err.kind != "unavailable":
                raise
            return removed
        for rack in racks:
            self.client.hardware_action("removeRack", {"key": rack["key"]})
            removed.append(rack.get("rackId"))
        if removed:
            logger.info(f"Removed racks from the hardware twin: {', '.join(map(str, removed))}")
        return removed

    @keyword
    def wait_for_hardware_command(self, command: str, timeout: str = "30s", since: Any = None) -> dict:
        """Waits until appSMM sends the RTC command ``command`` to the hardware (twin COP trace, offline
        tier), e.g. ``InitializeCmd``, ``DeInitializeCmd`` or ``AppMan.EmergencyStopCmd``. Looks after
        the last `Send ICD Message` unless ``since`` (a trace id) is given."""

        start = self._trace_mark if since is None else int(since)
        deadline = time.monotonic() + timestr_to_secs(timeout)
        while True:
            trace = self.client.hardware_trace(start)
            for entry in trace:
                if entry["way"] == "rx" and _command_matches(entry["name"], command):
                    logger.info(f"COP #{entry['id']} {entry['name']} {entry.get('text', '')}")
                    return entry
            if time.monotonic() > deadline:
                seen = sorted({e["name"] for e in trace if e["way"] == "rx"})
                raise AssertionError(f"appSMM did not send {command} to the hardware within {timeout} "
                                     f"(trace after #{start}: {len(trace)} entries; commands seen: {', '.join(seen) or 'none'})")
            time.sleep(0.2)

    @keyword
    def hardware_command_should_not_be_sent(self, command: str, duration: str = "3s", since: Any = None) -> None:
        """Fails if appSMM sends the RTC command ``command`` within ``duration``."""

        start = self._trace_mark if since is None else int(since)
        time.sleep(timestr_to_secs(duration))
        sent = [e for e in self.client.hardware_trace(start) if e["way"] == "rx" and _command_matches(e["name"], command)]
        if sent:
            raise AssertionError(f"appSMM sent {command}: " + ", ".join(f"#{e['id']} {e['name']}" for e in sent))

    def _remember_trace(self) -> None:
        try:
            trace = self.client.hardware_trace(self._trace_mark)
            if trace:
                self._trace_mark = trace[-1]["id"]
        except ServiceError:
            pass

    # ================================================================== quality checks

    @keyword
    def received_messages_should_be_schema_valid(self, since: Any = "test") -> None:
        """Every message from appSMM in the window must match its ICD schema."""
        bad = self.client.query({"way": "rx", "valid": False, "since": self._since(since) or 0})
        bad = [e for e in bad if e.get("name")]
        if bad:
            raise AssertionError("Schema-invalid messages from appSMM:\n" + "\n".join(f"#{e['id']} {e['name']}: {e.get('errors')}" for e in bad))

    @keyword
    def pair_issues_should_be_empty(self, since: str = "test") -> None:
        """No unanswered requests, duplicate answers or rack/tube identity problems. By default only
        issues raised since the test started count (``since=all`` checks the whole Bridge session)."""
        issues = self.client.pair_issues()
        if str(since).lower() == "test":
            issues = [i for i in issues if i.get("time", 0) >= self._test_started_ms]
        elif str(since).lower() not in ("all", "none"):
            raise ValueError(f"since must be 'test' or 'all', not {since!r}")
        if issues:
            raise AssertionError("Request/response pairing issues:\n" + "\n".join(json.dumps(i) for i in issues))

    # ================================================================== test hooks and logging

    @keyword
    def begin_smm_test(self) -> None:
        """Test setup: remembers where the test starts on the timeline."""

        self._test_started_ms = int(time.time() * 1000)
        try:
            self._test_mark = self.client.mark()
        except ServiceError:
            self._test_mark = 0
        self._mark = self._test_mark

    @keyword
    def finish_smm_test(self) -> None:
        """Test teardown: logs the test's timeline (and service logs when the test failed)."""
        try:
            entries = self.client.query({"since": self._test_mark}, 500)
        except ServiceError as err:
            logger.warn(f"Timeline not available: {err}")
            return
        failed = BuiltIn().get_variable_value("${TEST STATUS}") == "FAIL"
        logger.info(_timeline_html(entries, "Timeline of this test"), html=True)
        if failed:
            try:
                logs = self.client.environment_logs()[-80:]
                logger.info("<pre>" + html.escape("\n".join(f"{ln['source']}: {ln['line']}" for ln in logs)) + "</pre>", html=True)
            except ServiceError:
                pass

    @keyword
    def log_timeline(self, since: Any = "test", limit: int = 500) -> list:
        entries = self.client.query({"since": self._since(since) or 0}, int(limit))
        logger.info(_timeline_html(entries, "Timeline"), html=True)
        return entries

    # ================================================================== helpers

    def _since(self, since: Any) -> int | None:
        if since is None or since == "":
            return self._mark
        if str(since).lower() == "test":
            return self._test_mark
        if str(since).lower() in ("all", "none"):
            return None
        return int(since)

    def _filter(self, name: str, way: str | None, since: Any, match: Any, fields: dict) -> dict:
        flt: dict[str, Any] = {"name": name}
        if way and way != "any":
            flt["way"] = way
        start = self._since(since)
        if start is not None:
            flt["since"] = start
        body = {**(_as_obj(match) or {}), **_match_from(fields)}
        if body:
            flt["match"] = body
        return flt

    def _spec(self, spec: Any) -> dict:
        if isinstance(spec, dict):
            return spec
        parts = [p.strip() for p in str(spec).split("|")]
        flt: dict[str, Any] = {"name": parts[0], "way": "rx"}
        body: dict[str, Any] = {}
        for part in parts[1:]:
            if not part:
                continue
            key, sep, value = part.partition("=")
            if not sep:
                raise ValueError(f"'{part}' in '{spec}' is not Field=Value")
            key = key.strip()
            if key.startswith("@"):
                flt[key[1:]] = _coerce(value.strip()) if key[1:] != "way" else value.strip()
            else:
                _put(body, key, _coerce(value.strip()))
        if flt.get("way") == "any":
            del flt["way"]
        if body:
            flt["match"] = body
        return flt

    def _wait(self, flt: dict, timeout: str) -> dict:
        try:
            return self.client.wait(flt, timestr_to_secs(timeout))
        except ServiceError as err:
            self._fail_with_context(err)
            raise  # pragma: no cover

    def _fail_with_context(self, err: ServiceError):
        details = err.details or {}
        if err.kind == "timeout":
            recent = details.get("recentMessages", [])
            same = details.get("sameNameMessages", [])
            if same:
                logger.info(_timeline_html(same, "Messages with the same name (did not match)"), html=True)
            logger.info(_timeline_html(recent, "Last messages before the timeout"), html=True)
            if details.get("smm"):
                logger.info(f"Bridge view of appSMM: {json.dumps(details['smm'])}")
        elif err.kind == "expectation" and details.get("entry"):
            e = details["entry"]
            raise AssertionError(f"{err} -> #{e['id']} {e['name']} {json.dumps(e['body'])}") from None
        raise AssertionError(str(err)) from None


# ---------------------------------------------------------------------- module helpers


def _robot_var(name: str, default: str) -> str:
    try:
        return BuiltIn().get_variable_value(name, default)
    except Exception:  # noqa: BLE001 - outside a Robot run
        return default


def _metadata(name: str, value: str) -> None:
    try:
        BuiltIn().set_suite_metadata(name, value, top=True)
    except Exception:  # noqa: BLE001
        pass


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in ("false", "no", "0", "off", "none", "")


def _coerce(value: Any) -> Any:
    """Robot passes strings: read numbers, booleans, null, lists and objects as JSON."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text and (text[0] in '[{"-' or text[0].isdigit() or text in ("true", "false", "null")):
        try:
            return json.loads(text)
        except ValueError:
            return value
    return value


def _as_obj(value: Any) -> dict | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return dict(value)
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {value!r}")
    return parsed


def _put(target: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = value


def _match_from(fields: dict) -> dict:
    body: dict[str, Any] = {}
    for key, value in fields.items():
        _put(body, key, _coerce(value))
    return body


def _check_state(state: str) -> None:
    if state not in STATES:
        raise ValueError(f"Unknown system state '{state}' (ICD: {', '.join(STATES)})")


def _command_matches(trace_name: str, command: str) -> bool:
    """``AppMan.InitializeCmd`` matches ``InitializeCmd``, ``Initialize`` and ``AppMan.InitializeCmd``."""
    wanted = command if command.endswith("Cmd") or "." in command else command + "Cmd"
    return trace_name == wanted or trace_name.endswith("." + wanted)


def _timeline_html(entries: list[dict], title: str) -> str:
    rows = []
    for e in entries:
        style = "" if e.get("valid") is not False else ' style="background:#fde2e2"'
        arrow = "&rarr; appSMM" if e.get("way") == "tx" else "appSMM &rarr;"
        body = html.escape(json.dumps(e.get("body"), ensure_ascii=False))
        errors = f"<br><i>{html.escape('; '.join(e.get('errors') or []))}</i>" if e.get("valid") is False else ""
        rows.append(
            f"<tr{style}><td>{e.get('id')}</td><td>{html.escape(str(e.get('time', ''))[11:23])}</td><td>{arrow}</td>"
            f"<td>{html.escape(str(e.get('topic')))}</td><td><b>{html.escape(str(e.get('name') or '?'))}</b></td>"
            f"<td style='font-family:monospace'>{body}{errors}</td></tr>"
        )
    return (
        f"<details open><summary><b>{html.escape(title)}</b> ({len(entries)} messages)</summary>"
        "<table border='1' style='border-collapse:collapse;font-size:12px'>"
        "<tr><th>#</th><th>Time (UTC)</th><th>Dir</th><th>Topic</th><th>Message</th><th>Body</th></tr>"
        + "".join(rows)
        + "</table></details>"
    )
