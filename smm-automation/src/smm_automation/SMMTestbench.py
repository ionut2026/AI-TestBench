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

Time windows: every stimulus (send, hardware action, restart, disconnect) records a mark on the
timeline and on the COP trace; waits look at messages after the last mark unless ``since=`` is
given (``since=test`` = since the test started, ``since=last`` = after the previous match,
``since=0`` = whole timeline). Within a test a message is returned by one wait only: later waits
skip messages that earlier waits (or sequences) already matched, and the state polls of
`Bring SMM To State` are hidden from all waits and counts. Use `Wait For Message Sequence`
(or ``since=last``) to assert order.
"""

from __future__ import annotations

import html
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from robot.api import logger
from robot.api.deco import keyword, library
from robot.libraries.BuiltIn import BuiltIn
from robot.utils import timestr_to_secs

from smm_automation import operator_prompt, sil
from smm_automation.client import (
    DEFAULT_URL,
    ServiceClient,
    ServiceError,
    claim_worker_slot,
    ensure_service,
    worker_broker_port,
    worker_url,
)
from smm_automation.rigcontrol import RigControl, RigControlError

STATES = ("PowerOn", "NotInitialized", "Initializing", "Idle", "NormalOperation", "E-Stop", "Configuring", "Clearing")
TRANSIENT_STATES = ("PowerOn", "Initializing", "Clearing", "Configuring")
FILTER_FIELDS = {"way", "topic", "analyzer", "valid", "since"}
# After Initializing -> Idle appSMM goes on to Clearing; how long to wait for it before taking Idle as settled.
CLEARING_GRACE_S = 15.0
# appSMM's log lines may be stamped slightly before the service saw the message (same PC, different clocks).
LOG_CLOCK_SLACK_MS = 2000
# How often a log check re-fetches the appSMM log from the rig while it waits.
LOG_FETCH_INTERVAL_S = 2.0
# What the operator does at the instrument for a hardware action when there is no hardware twin (rig tier).
OPERATOR_INSTRUCTIONS = {
    "emergencyStop": "Press the EMERGENCY STOP button of the SMM, then release (unlock) it again so the system can be "
                     "recovered later.",
}


@library(scope="GLOBAL", auto_keywords=False)
class SMMTestbench:
    def __init__(self, url: str = DEFAULT_URL, autostart: bool = True):
        self.client = ServiceClient(url)
        self.autostart = str(autostart).lower() not in ("false", "no", "0")
        self._mark: int | None = None
        self._test_mark = 0
        self._tier: str | None = None
        self._trace_mark = 0
        self._test_trace_mark = 0
        self._test_started_ms = 0
        self._other_bridge_start = 0
        self._in_test = False
        # Timeline ids already returned by a wait in this test, and the hidden state polls.
        self._consumed: set[int] = set()
        self._hidden: set[int] = set()
        self._last_match: int | None = None
        # (mark, label) of reconnections and restarts inside the test, shown in the test's timeline.
        self._segments: list[tuple[int, str]] = []
        self._hardware_faults_set = False
        # Kinds of the running environment (from Start Test Environment): broker, appSmm, hardware.
        self._kinds: dict[str, str] = {}
        self._rig_control: RigControl | None = None
        self._rig_control_loaded = False
        self._log_fetches = 0
        self._log_fetched = False
        self._slot: int | None = None
        self._slot_handle: Any = None

    def _worker(self) -> int | None:
        """The worker slot in a parallel run (``smm-auto run --processes N``, pabot), else None. The worker uses its
        own local service (port + 10 + slot) and broker so parallel suites never share an environment."""
        if self._slot is None and str(_robot_var("${PABOTEXECUTIONPOOLID}", "")).strip():
            self._slot, self._slot_handle = claim_worker_slot()
            self.client.url = worker_url(self.client.url, self._slot)
            self.client.token = None
            logger.info(f"Parallel worker slot {self._slot}: automation service {self.client.url}")
        return self._slot

    # ================================================================== service and environment

    @keyword
    def ensure_automation_service_is_running(self) -> dict:
        """Checks the service (starting the local one if allowed) and records its TestBench pin as metadata."""
        from smm_automation import FRAMEWORK_ROOT

        out_dir = _robot_var("${OUTPUT DIR}", str(FRAMEWORK_ROOT / "results"))
        self._worker()
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
        overrides = _as_obj(overrides) or {}
        worker = self._worker()
        if worker is not None:
            if tier != "mock":
                raise AssertionError(f"Parallel runs are only possible on the mock tier, not {tier}")
            broker = {"port": worker_broker_port(worker), **(overrides.get("broker") or {})}
            overrides = {**overrides, "broker": broker}
        status = self.client.start_environment(tier, overrides)
        self._tier = tier
        self._kinds = {part: str((status.get(part) or {}).get("kind")) for part in ("broker", "appSmm", "hardware")}
        _metadata("Tier", tier + (" (framework self-test: NOT product evidence)" if tier == "mock" else ""))
        logger.info(f"Environment: {json.dumps(status, indent=1)}")
        if tier == "rig":
            control = self._control()
            _metadata("Rig control", f"{control} ({' '.join(sorted(control.capabilities())) or 'no capabilities'})" if control else "none")
            _metadata("Operator", self._operator_mode())
        return status

    @keyword
    def stop_test_environment(self) -> None:
        self._kinds = {}
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
        """Kills appSMM (no goodbye message) and starts it again after ``down``. On the rig this needs the site's
        rig control with ``restart-appsmm`` (``SMM_RIG_CONTROL``)."""
        self._set_marks()
        self._segment(f"appSMM restarted (down {down})")
        down_ms = int(timestr_to_secs(down) * 1000)
        if self._kind("appSmm") == "external":
            self._require_control("restart-appsmm", "restart appSMM").restart_appsmm(down_ms)
        else:
            self.client.restart_appsmm(down_ms)

    @keyword
    def restart_mqtt_broker(self, down: str = "2s") -> None:
        """Takes the MQTT broker down for ``down``: appSMM and the Bridge both lose the connection. On the rig this
        needs the site's rig control with ``restart-broker`` (``SMM_RIG_CONTROL``)."""
        self._set_marks()
        self._segment(f"MQTT broker restarted (down {down})")
        down_ms = int(timestr_to_secs(down) * 1000)
        if self._kind("broker") == "external":
            self._require_control("restart-broker", "restart the MQTT broker").restart_broker(down_ms)
        else:
            self.client.restart_broker(down_ms)

    def _kind(self, part: str) -> str:
        if part not in self._kinds:
            status = self.client.environment()
            self._kinds = {p: str((status.get(p) or {}).get("kind")) for p in ("broker", "appSmm", "hardware")}
        return self._kinds[part]

    def _control(self) -> RigControl | None:
        if not self._rig_control_loaded:
            self._rig_control = RigControl.from_env()
            self._rig_control_loaded = True
        return self._rig_control

    def _require_control(self, subcommand: str, what: str) -> RigControl:
        control = self._control()
        if control is None:
            raise AssertionError(f"Cannot {what} in tier {self._tier or '?'}: no rig control configured (SMM_RIG_CONTROL)")
        try:
            if not control.has(subcommand):
                raise AssertionError(f"Cannot {what}: the rig control '{control}' has no {subcommand}")
        except RigControlError as err:
            raise AssertionError(f"Cannot {what}: {err}") from None
        return control

    # ================================================================== Bridge session

    @keyword
    def connect_as_bridge(self, timeout: str = "10s", clear: Any = None, record_version: bool = True) -> dict:
        """Connects the Bridge (announces SMMBridge, IW and the enabled analyzers as Connected).

        ``clear`` empties the timeline first. By default (``None``) it clears only outside a test
        (suite setup); inside a test the timeline is kept, so the evidence from before a
        reconnection stays in the test's log."""
        do_clear = not self._in_test if clear is None or str(clear).strip().lower() in ("", "none", "auto") else _truthy(clear)
        if do_clear:
            self._mark = None
        else:
            self._set_marks()
            self._segment("Bridge connected again")
        snapshot = self.client.connect(timestr_to_secs(timeout), clear=do_clear)
        if do_clear:
            self._test_mark = 0
        if _truthy(record_version):
            self._record_appsmm_version()
        return snapshot

    @keyword
    def disconnect_bridge(self, abrupt: bool = False) -> None:
        """Disconnects the Bridge. ``abrupt=True`` drops the TCP connection so the broker publishes
        the Bridge's last will (``ConnectionNotification SMMBridge Disconnected``), like a crash."""
        self._set_marks()
        self._segment("Bridge disconnected" + (" abruptly (last will)" if _truthy(abrupt) else ""))
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
            self._hidden.add(self.client.send("GetVersionRequest", {})["id"])
            entry = self.client.wait({"name": "GetVersionResponse", "since": mark}, 5)
            self._hidden.add(entry["id"])
            i = entry["body"].get("Integration", {})
            _metadata("appSMM version", f"{i.get('Major')}.{i.get('Minor')}.{i.get('Build')}.{i.get('Revision')}")
        except ServiceError as err:
            logger.info(f"appSMM version not recorded: {err}")

    # ================================================================== sending

    @keyword
    def mark_timeline(self) -> int:
        """Waits after this look only at newer messages (and newer COP commands). Returns the mark."""
        return self._set_marks()

    @keyword
    def send_icd_message(self, name: str, body: Any = None, analyzer: int = -1, strict: bool = True, **fields: Any) -> dict:
        """Publishes an ICD message to appSMM (IW topic, or ``analyzer=N`` for /is/hcaN/rx).
        The body is ``body=`` (dict/JSON) merged with keyword fields. ``strict`` refuses bodies
        that break the ICD schema; use `Send Raw Payload` for negative tests."""
        payload = {**(_as_obj(body) or {}), **_match_from(fields)}
        self._set_marks()
        entry = self.client.send(name, payload, int(analyzer), _truthy(strict))
        logger.info(f"Sent {name}: {json.dumps(payload)}")
        return entry

    @keyword
    def send_raw_payload(self, topic: str, raw: str) -> dict:
        """Publishes any text on any topic (invalid JSON, wrong schema, wrong topic...)."""
        self._set_marks()
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
        flt = self._filter(name, way, since, match, fields, self._wait_exclusions(since))
        entry = self._wait(flt, timeout)
        entry.setdefault("errors", [])
        self._consume([entry])
        logger.info(f"Got #{entry['id']} {entry['name']} on {entry['topic']} at {entry['time']}: {json.dumps(entry['body'])}")
        return entry

    @keyword
    def wait_for_message_sequence(self, *specs: Any, timeout: str = "30s", since: Any = None) -> list:
        """Waits for messages in this order (others may come in between). Each spec is
        ``Name | Field=Value | ...`` or a filter dict. Returns the matched entries."""
        exclude = self._wait_exclusions(since)
        filters = [self._spec(s) for s in specs]
        if exclude:
            filters = [{**f, "exclude": sorted(set(f.get("exclude", [])) | set(exclude))} for f in filters]
        start = self._since(since)
        try:
            entries = self.client.wait_sequence(filters, timestr_to_secs(timeout), start)
        except ServiceError as err:
            self._fail_with_context(err)
        self._consume(entries)
        for e in entries:
            e.setdefault("errors", [])
            logger.info(f"#{e['id']} {e['time']} {e['name']}: {json.dumps(e['body'])}")
        return entries

    @keyword
    def message_should_not_arrive(self, name: str, duration: str = "3s", since: Any = None, way: str = "rx", match: Any = None, **fields: Any) -> None:
        """Fails if a matching message is already on the timeline (after the mark, not counting the ones
        earlier waits returned) or arrives within ``duration``."""
        flt = self._filter(name, way, since, match, fields, self._wait_exclusions(since))
        try:
            self.client.expect_none(flt, timestr_to_secs(duration))
        except ServiceError as err:
            self._fail_with_context(err)

    @keyword
    def messages_should_have_been_received(self, name: str, count: int | None = None, since: Any = None, way: str = "rx", match: Any = None, **fields: Any) -> list:
        """Returns the matching messages already on the timeline (including the ones waits returned,
        not the hidden state polls); checks the count if given."""
        found = self.client.query(self._filter(name, way, since, match, fields, sorted(self._hidden)))
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
        from any operating state (e.g. NormalOperation) through Shutdown -> E-Stop -> Recover.

        The current state is taken from what appSMM already reported (SystemStatusNotification /
        Response); while appSMM is in a transient state it waits for its next notification instead of
        polling. The confirming SystemStatusRequest polls are hidden from the test's waits and counts.
        Afterwards waits look at what comes after the precondition."""
        _check_state(target)
        if target not in ("NotInitialized", "Idle", "E-Stop"):
            raise ValueError(f"Bring SMM To State supports NotInitialized, Idle and E-Stop, not {target}")
        budget = timestr_to_secs(timeout)
        deadline = time.monotonic() + budget
        state = None
        try:
            for _ in range(5):
                state = self._settled_state(deadline)
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
        finally:
            try:
                self._set_marks()
            except ServiceError:
                pass

    def wait_for_system_state_change(self, previous: str, timeout: str = "60s") -> dict:
        """Waits for a SystemStatusNotification leaving ``previous`` (any new state)."""
        return self.wait_for_message("SystemStatusNotification", timeout=timeout, PreviousState=previous)

    def _settled_state(self, deadline: float) -> str:
        """The state appSMM is in once it is not changing: waits (event-driven) while appSMM is in a
        transient state or still has to clear after initializing, then confirms with one hidden poll."""
        while True:
            anchor = self.client.mark()
            state = self._observed_state()
            if time.monotonic() < deadline and (state in TRANSIENT_STATES or self._clearing_due(state)):
                until = deadline if state in TRANSIENT_STATES else min(deadline, time.monotonic() + CLEARING_GRACE_S)
                if self._status_notification_after(anchor, until):
                    continue
            confirmed = self._poll_state()
            if confirmed == state and confirmed not in TRANSIENT_STATES:
                return confirmed
            if time.monotonic() >= deadline:
                raise AssertionError(f"appSMM did not settle in time (state {confirmed}, before {state})")

    def _observed_state(self) -> str:
        """The state the Bridge last saw appSMM report; asks (hidden) if it has not reported one."""
        state = (self.client.session().get("smm") or {}).get("systemState")
        return state if state in STATES else self._poll_state()

    def _clearing_due(self, state: str) -> bool:
        """Idle straight after Initializing: appSMM still goes through Clearing."""
        if state != "Idle":
            return False
        last = self.client.query({"name": "SystemStatusNotification", "way": "rx"}, 1)
        return bool(last) and (last[-1].get("body") or {}).get("PreviousState") == "Initializing"

    def _status_notification_after(self, anchor: int, until: float) -> bool:
        remaining = until - time.monotonic()
        if remaining <= 0:
            return False
        try:
            self.client.wait({"name": "SystemStatusNotification", "way": "rx", "since": anchor}, remaining)
        except ServiceError as err:
            if err.kind == "timeout":
                return False
            raise
        return True

    def _poll_state(self) -> str:
        """SystemStatusRequest that the test's waits and counts do not see."""
        mark = self.client.mark()
        self._hidden.add(self.client.send("SystemStatusRequest", {})["id"])
        try:
            entry = self.client.wait({"name": "SystemStatusResponse", "way": "rx", "since": mark, "exclude": sorted(self._hidden)}, 5)
        except ServiceError as err:
            raise AssertionError(f"appSMM did not answer SystemStatusRequest: {err}") from None
        self._hidden.add(entry["id"])
        return str(entry["body"]["CurrentState"])

    def _wait_idle_after_clearing(self, budget: float) -> None:
        self.wait_for_message_sequence(
            "SystemStatusNotification | CurrentState=Clearing",
            "SystemStatusNotification | PreviousState=Clearing | CurrentState=Idle",
            timeout=f"{budget}s",
        )

    # ================================================================== state x request matrix

    @keyword
    def observe_request_outcome(self, request: str, body: Any = None, timeout: str = "10s", observe: str = "3s",
                                settle: str = "180s") -> dict:
        """Sends ``request`` and records what appSMM does, without judging it: the response (waited for up
        to ``timeout``), the SystemStatusNotifications within the observation window ``observe`` after it,
        and the state appSMM settles in (waiting up to ``settle`` while it is in a transient state).
        Returns ``{request, response, notifications, settled}`` for `Request Outcome Should Match`."""
        response_name = request.replace("Request", "Response")
        mark = self._set_marks()
        self.send_icd_message(request, body)
        response = None
        try:
            response = self.client.wait({"name": response_name, "way": "rx", "since": mark, "exclude": sorted(self._hidden)},
                                        timestr_to_secs(timeout))
            self._consume([response])
        except ServiceError as err:
            if err.kind != "timeout":
                raise
        time.sleep(timestr_to_secs(observe))  # observation window, not a wait for an expected event
        try:
            settled = self._settled_state(time.monotonic() + timestr_to_secs(settle))
        except AssertionError as err:
            settled = f"unsettled ({err})"
        notes = self.client.query({"name": "SystemStatusNotification", "way": "rx", "since": mark, "exclude": sorted(self._hidden)})
        outcome = {
            "request": request,
            "response": response and {"name": response["name"], "topic": response.get("topic"), "body": response.get("body")},
            "notifications": [f"{(n.get('body') or {}).get('PreviousState')} -> {(n.get('body') or {}).get('CurrentState')}" for n in notes],
            "settled": settled,
        }
        logger.info(f"Outcome of {request}: {json.dumps(outcome)}")
        return outcome

    @keyword
    def request_outcome_should_match(self, outcome: dict, state: str, response: str = "", next_state: str = "",
                                     questions: str = "", checked: Any = False) -> None:
        """Checks an outcome of `Observe Request Outcome` (sent in precondition ``state``) against a state
        matrix cell. ``response``: ``Name | Field=Value | @topic=...``, ``none`` (no answer allowed) or empty
        (unspecified). ``next_state``: the state appSMM must reach, ``unchanged`` or empty (unspecified).
        ``questions``: a text whose part after ``Open:`` lists what the specification leaves open (`` / ``
        separated), e.g. the test documentation; logged as a warning with the observed outcome. ``checked``: something was already verified by the caller (e.g. a COP command).
        When nothing was specified or checked on this tier, the test is skipped with the observation."""
        errors: list[str] = []
        observed = _describe_outcome(outcome)
        if response.strip().lower() == "none":
            if outcome["response"]:
                errors.append(f"appSMM answered {outcome['request']} with {json.dumps(outcome['response'])}; no answer is allowed")
        elif response.strip():
            errors += _response_errors(outcome["response"], response)
        if next_state.strip().lower() == "unchanged":
            if outcome["notifications"] or outcome["settled"] != state:
                errors.append(f"state changed: notifications {outcome['notifications']}, settled in {outcome['settled']}; expected unchanged {state}")
        elif next_state.strip():
            reached = [n.split(" -> ")[-1] for n in outcome["notifications"]] + [outcome["settled"]]
            if next_state.strip() not in reached:
                errors.append(f"state {next_state.strip()} not reached: notifications {outcome['notifications']}, settled in {outcome['settled']}")
        if errors:
            raise AssertionError("; ".join(errors) + f". Observed: {observed}")
        open_questions = [q.strip() for q in _open_part(questions).split(" / ") if q.strip()]
        if open_questions:
            logger.warn(f"Unspecified behaviour of {outcome['request']} in {state}: {' / '.join(open_questions)} Observed: {observed}")
        if not (response.strip() or next_state.strip() or _truthy(checked)):
            from robot.api import SkipExecution

            raise SkipExecution(f"Not specified; observed: {observed}. Open: {' / '.join(open_questions) or '-'}")

    # ================================================================== hardware

    @keyword
    def trigger_hardware_action(self, action: str, **args: Any) -> dict:
        """Operator/hardware action: emergencyStop, loadInputTray, removeInputTray, insertOutputTray,
        removeOutputTray, insertFrontIn, removeFrontIn, removeFrontOut, pauseLane, resumeLane, toggleLaneError
        (area=Input|Output), toggleOutputAvailable. Done by the hardware twin (offline tier); without a twin (rig)
        the operator does it (``emergencyStop`` only, run with ``--operator``), anything else is unavailable."""
        self._set_marks()
        if self._kind("hardware") == "twin":
            return self.client.hardware_action(action, {k: _coerce(v) for k, v in args.items()})
        instruction = OPERATOR_INSTRUCTIONS.get(action)
        if instruction is None or args:
            raise AssertionError(f"Hardware action {action}{' with arguments' if args else ''} needs the hardware twin "
                                 f"(offline tier); the tier is {self._tier or '?'}")
        answer = self._ask_operator(instruction, None)
        return {"action": action, "by": "operator", "answer": answer}

    @keyword
    def trigger_emergency_stop(self) -> dict:
        """Presses the E-Stop: on the hardware twin (which goes to Halted), or by the operator on the rig."""
        return self.trigger_hardware_action("emergencyStop")

    @keyword
    def hardware_action_is_possible(self, action: str = "emergencyStop") -> bool:
        """True when ``action`` can be done in this tier: by the hardware twin, or by an operator (``--operator``)
        for the actions an operator can do on the real instrument (``emergencyStop``)."""
        if self._kind("hardware") == "twin":
            return True
        return action in OPERATOR_INSTRUCTIONS and self._kind("hardware") == "external" and self._operator_mode() != "none"

    @keyword
    def operator_action(self, instruction: str, timeout: str | None = None) -> str:
        """Asks the operator at the instrument to do ``instruction`` and waits for the confirmation (tag
        ``needs:operator``). Stimulus: waits after it look at what happened from the moment of the request. Fails
        if the operator reports a failure or does not confirm within ``timeout`` (default ``${OPERATOR_TIMEOUT}``).
        Mode from ``${OPERATOR}`` / ``SMM_OPERATOR``: console, dialog or none."""
        self._set_marks()
        return self._ask_operator(instruction, timeout)

    def _operator_mode(self) -> str:
        return operator_prompt.operator_mode(_robot_var("${OPERATOR}", "") or None)

    def _ask_operator(self, instruction: str, timeout: str | None) -> str:
        timeout = timeout or _robot_var("${OPERATOR_TIMEOUT}", "300s")
        self._segment(f"Operator: {instruction}")
        logger.info(f"Operator action: {instruction}")
        try:
            answer = operator_prompt.ask_operator(instruction, self._operator_mode(), timestr_to_secs(timeout))
        except operator_prompt.OperatorUnavailable as err:
            raise AssertionError(str(err)) from None
        logger.info(f"Operator confirmed: {answer}")
        return answer

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
        the last stimulus (send, hardware action, mark) unless ``since`` is given (a trace id,
        ``test`` or ``all``)."""
        start = self._trace_since(since)
        try:
            entry = self.client.trace_wait(command, start, timestr_to_secs(timeout))
        except ServiceError as err:
            raise AssertionError(str(err)) from None
        logger.info(f"COP #{entry['id']} {entry['name']} {entry.get('text', '')}")
        return entry

    @keyword
    def hardware_command_should_not_be_sent(self, command: str, duration: str = "3s", since: Any = None) -> None:
        """Fails as soon as appSMM sends the RTC command ``command`` to the hardware (after the last
        stimulus, or ``since``), or if it does not stay away for ``duration``."""
        start = self._trace_since(since)
        try:
            self.client.trace_expect_none(command, start, timestr_to_secs(duration))
        except ServiceError as err:
            e = (err.details or {}).get("entry")
            raise AssertionError(f"{err}: COP #{e['id']} {e['name']} {e.get('text', '')}" if e else str(err)) from None

    @keyword
    def set_hardware_faults(self, *rules: Any) -> dict:
        """Makes the COP link between appSMM and the hardware twin misbehave (offline tier). Each rule is
        ``Message | action=drop|delay|error | ms=... | code=... | skip=... | count=...`` (or a dict / JSON
        object); ``delay=15s`` is short for ``action=delay | ms=15000``. Message is the COP name as the trace shows it: ``DeInitializeRsp`` (twin -> appSMM),
        ``InitializeCmd`` or ``Initialize`` (appSMM -> twin), optionally with the module
        (``AppMan.DeInitializeRsp``). Replaces earlier rules; the test teardown clears them. Example:
        ``Set Hardware Faults    DeInitializeRsp | action=delay | ms=15000``."""
        faults = [_fault_rule(r) for r in rules]
        self._hardware_faults_set = bool(faults)
        try:
            status = self.client.set_hardware_faults(faults)
        except ServiceError as err:
            raise AssertionError(str(err)) from None
        logger.info(f"COP fault rules: {json.dumps(faults)}")
        return status

    @keyword
    def clear_hardware_faults(self) -> None:
        """Back to a well-behaved COP link (also done by the test teardown after Set Hardware Faults)."""
        self._hardware_faults_set = False
        self.client.clear_hardware_faults()

    @keyword
    def get_hardware_fault_status(self) -> dict:
        """The active COP fault rules and per rule how often a message matched and the fault was applied."""
        return self.client.hardware_faults()

    @keyword
    def hardware_fault_should_have_been_applied(self, message: str, times: int | None = None) -> None:
        """Guards against a vacuous test: the fault rule for ``message`` hit at least once (or exactly
        ``times``)."""
        stats = [s for s in self.client.hardware_faults()["stats"] if s["message"] == message]
        if not stats:
            raise AssertionError(f"No COP fault rule for {message}")
        applied = stats[0]["applied"]
        if (times is None and applied < 1) or (times is not None and applied != int(times)):
            raise AssertionError(f"COP fault rule for {message} was applied {applied} time(s), expected {times if times is not None else 'at least 1'}")

    # ================================================================== timing

    @keyword
    def get_time_between(self, earlier: Any, later: Any) -> float:
        """Seconds from ``earlier`` to ``later``: entries returned by Send ICD Message, Wait For Message
        (Entry), Wait For Message Sequence or Wait For Hardware Command, or times (ISO text or epoch ms)."""
        seconds = (_epoch_ms(later) - _epoch_ms(earlier)) / 1000
        logger.info(f"{_label(earlier)} -> {_label(later)}: {seconds:.3f} s")
        return seconds

    @keyword
    def time_between_should_be_less_than(self, earlier: Any, later: Any, limit: str) -> float:
        """SDS timing requirements ("within 20 s"): fails unless ``later`` came less than ``limit`` after
        ``earlier``, and if it came before it. Returns the seconds."""
        seconds = self.get_time_between(earlier, later)
        if seconds < 0:
            raise AssertionError(f"{_label(later)} came {-seconds:.3f} s before {_label(earlier)}")
        if seconds >= timestr_to_secs(limit):
            raise AssertionError(f"{_label(later)} came {seconds:.3f} s after {_label(earlier)}, limit {limit}")
        return seconds

    @keyword
    def time_between_should_be_at_least(self, earlier: Any, later: Any, minimum: str) -> float:
        """Fails if ``later`` came less than ``minimum`` after ``earlier`` (e.g. proves a delay fault took
        effect, or that appSMM waited as specified). Returns the seconds."""
        seconds = self.get_time_between(earlier, later)
        if seconds < timestr_to_secs(minimum):
            raise AssertionError(f"{_label(later)} came {seconds:.3f} s after {_label(earlier)}, expected at least {minimum}")
        return seconds

    # ================================================================== appSMM log files

    @keyword
    def get_appsmm_log_location(self) -> str | None:
        """The configured appSMM log file (``...\\appSMM.sil``): the variable ``${APPSMM_LOG}`` if set, else
        ``trace.config`` next to the appSMM.exe the environment runs (offline tier), else on the rig a fresh copy
        fetched by the rig control (``fetch-log``) into ``${OUTPUT DIR}/appsmm-log/``. None if unknown (mock; rig
        without ``fetch-log``)."""
        configured = BuiltIn().get_variable_value("${APPSMM_LOG}")
        self._log_fetched = False
        if configured:
            return str(configured)
        if self._can_fetch_log():
            self._log_fetches += 1
            self._log_fetched = True
            return str(self._fetch_log(self._log_fetches))
        try:
            exe = (self.client.environment().get("appSmm") or {}).get("exe")
        except ServiceError:
            return None
        target = sil.log_target(Path(exe).parent) if exe else None
        return str(target) if target else None

    def _can_fetch_log(self) -> bool:
        if self._kinds.get("appSmm") != "external":
            return False
        control = self._control()
        try:
            return bool(control and control.has("fetch-log"))
        except RigControlError as err:
            logger.warn(f"Rig control: {err}")
            return False

    def _fetch_log(self, n: int) -> Path:
        """Fetches the rig's appSMM log into ``appsmm-log/<n>`` (replacing an earlier fetch of the same check)."""
        import shutil

        from smm_automation import FRAMEWORK_ROOT

        dest = Path(_robot_var("${OUTPUT DIR}", str(FRAMEWORK_ROOT / "results"))) / "appsmm-log" / str(n)
        shutil.rmtree(dest, ignore_errors=True)
        control = self._control()
        assert control is not None
        try:
            files = control.fetch_log(dest)
        except RigControlError as err:
            raise AssertionError(f"Fetching the appSMM log from the rig failed: {err}") from None
        named = [f for f in files if f.stem.lower() == "appsmm" or f.stem.lower().startswith("appsmm-")]
        # The copies carry the rig's file times (or none): restamp them with this PC's clock, keeping their order,
        # so the log reader's file-time filter does not drop them; the entries keep appSMM's own time stamps.
        now = time.time()
        ordered = sorted(files, key=lambda f: f.stat().st_mtime)
        for i, f in enumerate(ordered):
            stamp = now - (len(ordered) - i) * 0.001
            os.utime(f, (stamp, stamp))
        return dest / "appSMM.sil" if named else files[0]

    @keyword
    def get_appsmm_log_messages(self, name: str | None = None, since: Any = "test") -> list:
        """ICD messages appSMM wrote to its SmartInspect log (``.sil``) since ``since`` (``test``, ``all``, an
        entry or a time). Each is ``{time, way (RX/TX from appSMM's side), topic, name, body, line}``."""
        target = self._log_target()
        found = [m.as_dict() for m in sil.logged_messages(target, self._log_since_ms(since)) if not name or m.name == name]
        logger.info(f"{len(found)} ICD message(s){' ' + name if name else ''} in the appSMM log {target}")
        return found

    @keyword
    def icd_messages_should_be_logged_by_appsmm(self, *names: str, since: Any = "test", timeout: str = "10s") -> list:
        """Every ``names`` message exchanged with appSMM on the timeline since ``since`` (``test``, ``all`` or a
        timeline entry) is in appSMM's log
        file with its topic and content: received ones as ``RX(<topic>)``, published ones as ``TX(<topic>)``.
        Waits up to ``timeout`` for appSMM to write the file. Returns the matching log entries."""
        if not names:
            raise ValueError("Name at least one ICD message")
        target = self._log_target()
        fetching = self._log_fetched
        since_ms = self._log_since_ms(since)
        start = int(since["id"]) - 1 if isinstance(since, dict) else (0 if str(since).lower() in ("all", "none") else self._test_mark)
        exchanged = [e for e in self.client.query({"since": start}, 2000) if e.get("name") in names]
        if not exchanged:
            raise AssertionError(f"No {', '.join(names)} exchanged with appSMM in this window: nothing to look for in the log")
        deadline = time.monotonic() + timestr_to_secs(timeout)
        while True:
            # the log stamps lines with appSMM's clock; allow for the time between receiving and logging
            logged = sil.logged_messages(target, since_ms - LOG_CLOCK_SLACK_MS)
            matched, missing = _match_logged(exchanged, logged)
            if not missing or time.monotonic() >= deadline:
                break
            if fetching:
                time.sleep(LOG_FETCH_INTERVAL_S)
                target = self._fetch_log(self._log_fetches)
            else:
                time.sleep(0.5)
        if missing:
            lines = "\n".join(f"  #{e['id']} {'RX' if e['way'] == 'tx' else 'TX'}({e['topic']}) {e['name']} {json.dumps(e['body'])}" for e in missing)
            raise AssertionError(f"{len(missing)} of {len(exchanged)} message(s) not in the appSMM log {target}:\n{lines}")
        logger.info(f"All {len(exchanged)} {', '.join(names)} message(s) are in the appSMM log:\n" + "\n".join(m["line"] for m in matched))
        return matched

    def _log_target(self) -> Path:
        location = self.get_appsmm_log_location()
        if not location:
            raise AssertionError("The appSMM log location is unknown in this tier (set ${APPSMM_LOG}, or on the rig a rig control "
                                 "with fetch-log in SMM_RIG_CONTROL)")
        return Path(location)

    def _log_since_ms(self, since: Any) -> float:
        if since is None or str(since).lower() == "test":
            return float(self._test_started_ms)
        if str(since).lower() in ("all", "none"):
            return 0.0
        return _epoch_ms(since)

    def _trace_since(self, since: Any) -> int:
        if since is None or since == "":
            return self._trace_mark
        if str(since).lower() == "test":
            return self._test_trace_mark
        if str(since).lower() in ("all", "none"):
            return 0
        return int(since)

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
        """Test setup: remembers where the test starts on the timeline and the COP trace."""

        self._test_started_ms = int(time.time() * 1000)
        self._in_test = True
        self._consumed, self._hidden, self._last_match, self._segments = set(), set(), None, []
        try:
            self._test_mark, self._test_trace_mark = self.client.mark_all()
        except ServiceError:
            self._test_mark, self._test_trace_mark = 0, 0
        try:
            self._other_bridge_start = int((self.client.session().get("otherBridge") or {}).get("count", 0))
        except ServiceError:
            self._other_bridge_start = 0
        self._mark, self._trace_mark = self._test_mark, self._test_trace_mark

    @keyword
    def finish_smm_test(self) -> None:
        """Test teardown: logs the test's timeline (and service logs when the test failed), and fails
        the test if the service dropped part of its evidence (timeline or COP trace cap reached) or another
        SMM Bridge published to appSMM during the test (shared rig broker)."""
        self._in_test = False
        if self._hardware_faults_set:
            self._hardware_faults_set = False
            try:
                self.client.clear_hardware_faults()
            except ServiceError as err:
                logger.warn(f"COP fault rules not cleared: {err}")
        try:
            entries = self.client.query({"since": self._test_mark}, 500)
        except ServiceError as err:
            logger.warn(f"Timeline not available: {err}")
            return
        failed = BuiltIn().get_variable_value("${TEST STATUS}") == "FAIL"
        logger.info(_timeline_html(entries, "Timeline of this test", self._segments), html=True)
        if failed:
            try:
                logs = self.client.environment_logs()[-80:]
                logger.info("<pre>" + html.escape("\n".join(f"{ln['source']}: {ln['line']}" for ln in logs)) + "</pre>", html=True)
            except ServiceError:
                pass
        self._check_evidence_complete()

    def _check_evidence_complete(self) -> None:
        lost = []
        try:
            session = self.client.session()
            trace = self.client.environment().get("trace") or {}
        except ServiceError:
            return
        timeline = session.get("timeline") or {}
        if int(timeline.get("droppedThrough", 0)) > self._test_mark:
            lost.append(f"timeline messages up to #{timeline['droppedThrough']} (the test started after #{self._test_mark}; "
                        f"cap {timeline.get('cap')}: raise --timeline-cap / SMM_TIMELINE_CAP)")
        if int(trace.get("droppedThrough", 0)) > self._test_trace_mark:
            lost.append(f"COP trace entries up to #{trace['droppedThrough']} (the test started after #{self._test_trace_mark}; "
                        f"cap {trace.get('cap')}: raise --trace-cap / SMM_TRACE_CAP)")
        problems = []
        if lost:
            problems.append("The automation service dropped evidence of this test: " + "; ".join(lost))
        other = session.get("otherBridge") or {}
        count = int(other.get("count", 0))
        # the service resets the count when the Bridge connects again during the test
        new = count - self._other_bridge_start if count >= self._other_bridge_start else count
        if new > 0:
            problems.append(f"Another SMM Bridge published {new} message(s) to appSMM during this test "
                            f"(last: {str(other.get('lastMessage', ''))[:200]}): the result is not trustworthy; "
                            "disconnect the other Bridge or SMM UI from the broker")
        if problems:
            raise AssertionError("\n".join(problems))

    @keyword
    def log_timeline(self, since: Any = "test", limit: int = 500) -> list:
        entries = self.client.query({"since": self._since(since) or 0}, int(limit))
        logger.info(_timeline_html(entries, "Timeline", self._segments), html=True)
        return entries

    # ================================================================== helpers

    def _set_marks(self) -> int:
        """Marks the timeline and the COP trace: the next waits look only at what comes after."""
        self._mark, self._trace_mark = self.client.mark_all()
        return self._mark

    def _segment(self, label: str) -> None:
        if self._in_test and self._mark is not None:
            self._segments.append((self._mark, label))

    def _consume(self, entries: list[dict]) -> None:
        ids = [int(e["id"]) for e in entries if "id" in e]
        if ids:
            self._consumed.update(ids)
            self._last_match = max(ids)

    def _wait_exclusions(self, since: Any) -> list[int]:
        """What a wait skips: earlier matches (in the default window or ``since=last``) and hidden polls."""
        implicit = since is None or since == "" or str(since).lower() == "last"
        return sorted(self._hidden | self._consumed if implicit else self._hidden)

    def _since(self, since: Any) -> int | None:
        if since is None or since == "":
            return self._mark
        if str(since).lower() == "test":
            return self._test_mark
        if str(since).lower() == "last":
            return self._last_match if self._last_match is not None else self._mark
        if str(since).lower() in ("all", "none"):
            return None
        return int(since)

    def _filter(self, name: str, way: str | None, since: Any, match: Any, fields: dict, exclude: list[int] | None = None) -> dict:
        flt: dict[str, Any] = {"name": name}
        if way and way != "any":
            flt["way"] = way
        start = self._since(since)
        if start is not None:
            flt["since"] = start
        if exclude:
            flt["exclude"] = exclude
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


def _open_part(text: str) -> str:
    """The open questions in ``text``: everything after ``Open:`` (nothing without it), whitespace normalised."""
    text = " ".join(str(text or "").split())
    return text.split("Open:", 1)[1] if "Open:" in text else ""


def _describe_outcome(outcome: dict) -> str:
    resp = outcome.get("response")
    answer = f"{resp['name']} {json.dumps(resp['body'])} on {resp['topic']}" if resp else "no response"
    states = ", ".join(outcome.get("notifications") or []) or "no state notification"
    return f"{answer}; {states}; settled in {outcome.get('settled')}"


def _response_errors(resp: dict | None, expected: str) -> list[str]:
    """Differences between an observed response and ``Name | Field=Value | @topic=...``."""
    parts = [p.strip() for p in expected.split("|") if p.strip()]
    if not resp:
        return [f"no {parts[0]} received"]
    errors = []
    if resp["name"] != parts[0]:
        errors.append(f"got {resp['name']}, expected {parts[0]}")
    for part in parts[1:]:
        key, _, value = part.partition("=")
        key, want = key.strip(), _coerce(value.strip())
        if key.startswith("@"):
            got: Any = resp.get(key[1:])
        else:
            got = resp.get("body") or {}
            for step in key.split("."):
                got = got.get(step) if isinstance(got, dict) else None
        if got != want:
            errors.append(f"{resp['name']} {key} is {json.dumps(got)}, expected {json.dumps(want)}")
    return errors


def _as_obj(value: Any) -> dict | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return dict(value)
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {value!r}")
    return parsed


def _fault_rule(rule: Any) -> dict:
    """``DeInitializeRsp | action=delay | ms=15000`` (or ``| delay=15s``, or a dict / JSON object) -> COP fault rule."""
    if isinstance(rule, dict):
        return dict(rule)
    text = str(rule).strip()
    if text.startswith("{"):
        return _as_obj(text) or {}
    parts = [p.strip() for p in text.split("|")]
    out: dict[str, Any] = {"message": parts[0]}
    for part in parts[1:]:
        key, sep, value = part.partition("=")
        if not sep:
            raise ValueError(f"'{part}' in '{rule}' is not key=value")
        if key.strip() == "delay":
            out["action"] = "delay"
            out["ms"] = round(timestr_to_secs(value.strip()) * 1000)
        else:
            out[key.strip()] = _coerce(value.strip())
    return out


def _match_logged(exchanged: list[dict], logged: list[sil.LoggedMessage]) -> tuple[list[dict], list[dict]]:
    """Pairs each timeline entry with its own log line (same direction, topic, name and content, logged within
    LOG_CLOCK_SLACK_MS). The timeline's ``tx`` (sent by the test) is appSMM's ``RX``."""
    used: set[int] = set()
    matched, missing = [], []
    for e in sorted(exchanged, key=lambda x: _epoch_ms(x)):
        way = "RX" if e["way"] == "tx" else "TX"
        when = _epoch_ms(e)
        body = _canonical(e.get("body"))
        hit = next((i for i, m in enumerate(logged) if i not in used and m.way == way and m.topic == e["topic"]
                    and m.name == e["name"] and abs(m.time_ms - when) <= LOG_CLOCK_SLACK_MS and _canonical(m.body) == body), None)
        if hit is None:
            missing.append(e)
        else:
            used.add(hit)
            matched.append(logged[hit].as_dict())
    return matched, missing


def _canonical(body: Any) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _epoch_ms(value: Any) -> float:
    """Entry (``time`` field), epoch ms or ISO 8601 text -> epoch ms."""
    if isinstance(value, dict):
        if "time" not in value:
            raise ValueError(f"Entry has no time: {value!r}")
        value = value["time"]
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000


def _label(value: Any) -> str:
    if isinstance(value, dict):
        return f"#{value.get('id', '?')} {value.get('name', '?')}"
    return str(value)


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


def _timeline_html(entries: list[dict], title: str, segments: list[tuple[int, str]] | tuple = ()) -> str:
    rows = []
    pending = sorted(segments)

    def separators(before_id: float) -> None:
        while pending and pending[0][0] < before_id:
            label = pending.pop(0)[1]
            rows.append(f"<tr style='background:#e8eefc'><td colspan='6'><b>&#9472;&#9472; {html.escape(label)} &#9472;&#9472;</b></td></tr>")

    for e in entries:
        separators(float(e.get("id") or 0))
        style = "" if e.get("valid") is not False else ' style="background:#fde2e2"'
        arrow = "&rarr; appSMM" if e.get("way") == "tx" else "appSMM &rarr;"
        body = html.escape(json.dumps(e.get("body"), ensure_ascii=False))
        errors = f"<br><i>{html.escape('; '.join(e.get('errors') or []))}</i>" if e.get("valid") is False else ""
        rows.append(
            f"<tr{style}><td>{e.get('id')}</td><td>{html.escape(str(e.get('time', ''))[11:23])}</td><td>{arrow}</td>"
            f"<td>{html.escape(str(e.get('topic')))}</td><td><b>{html.escape(str(e.get('name') or '?'))}</b></td>"
            f"<td style='font-family:monospace'>{body}{errors}</td></tr>"
        )
    separators(float("inf"))
    return (
        f"<details open><summary><b>{html.escape(title)}</b> ({len(entries)} messages)</summary>"
        "<table border='1' style='border-collapse:collapse;font-size:12px'>"
        "<tr><th>#</th><th>Time (UTC)</th><th>Dir</th><th>Topic</th><th>Message</th><th>Body</th></tr>"
        + "".join(rows)
        + "</table></details>"
    )
