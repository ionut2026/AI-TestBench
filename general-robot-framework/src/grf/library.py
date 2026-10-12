"""The generic base of a product's Robot Framework keyword library.

A product library subclasses ``GrfLibrary`` (``@library(scope="GLOBAL", auto_keywords=False)``) and gets the rig
control of the project (``<ENV_PREFIX>_RIG_CONTROL``), operator actions (``${OPERATOR}`` / ``<ENV_PREFIX>_OPERATOR``),
timing keywords, the per-test bookkeeping of stimuli (marks) and timeline segments, and the helpers below. A
subclass may override a keyword to document it in the product's words; it then calls ``super()``.

The product decides what a *mark* is (e.g. the position on its message timeline): it overrides ``_set_marks``,
which every stimulus calls, and the timeline segments (restarts, operator actions) are recorded at that mark.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from robot.api import logger
from robot.api.deco import keyword, library
from robot.libraries.BuiltIn import BuiltIn
from robot.utils import timestr_to_secs

from grf import operator_prompt, project
from grf.rigcontrol import RigControl, RigControlError


@library(scope="GLOBAL", auto_keywords=False)
class GrfLibrary:
    def __init__(self) -> None:
        self._mark: int | None = None
        self._tier: str | None = None
        self._in_test = False
        self._test_started_ms = 0
        # (mark, label) of reconnections, restarts and operator actions inside the test, shown in its timeline.
        self._segments: list[tuple[int, str]] = []
        self._rig_control: RigControl | None = None
        self._rig_control_loaded = False

    # ================================================================== test bookkeeping

    def _begin_test(self, started_ms: int) -> None:
        self._test_started_ms = started_ms
        self._in_test = True
        self._segments = []

    def _end_test(self) -> None:
        self._in_test = False

    def _set_marks(self) -> int | None:
        """Called by every stimulus: the product marks where the next waits start (default: no marks)."""
        return self._mark

    def _segment(self, label: str) -> None:
        if self._in_test and self._mark is not None:
            self._segments.append((self._mark, label))

    # ================================================================== rig control

    def _control(self) -> RigControl | None:
        if not self._rig_control_loaded:
            self._rig_control = RigControl.from_env()
            self._rig_control_loaded = True
        return self._rig_control

    def _require_control(self, subcommand: str, what: str) -> RigControl:
        control = self._control()
        if control is None:
            raise AssertionError(f"Cannot {what} in tier {self._tier or '?'}: no rig control configured "
                                 f"({project.current().env('RIG_CONTROL')})")
        try:
            if not control.has(subcommand):
                raise AssertionError(f"Cannot {what}: the rig control '{control}' has no {subcommand}")
        except RigControlError as err:
            raise AssertionError(f"Cannot {what}: {err}") from None
        return control

    # ================================================================== operator

    @keyword
    def operator_action(self, instruction: str, timeout: str | None = None) -> str:
        """Asks the operator at the instrument to do ``instruction`` and waits for the confirmation (tag
        ``needs:operator``). Stimulus: waits after it look at what happened from the moment of the request. Fails
        if the operator reports a failure or does not confirm within ``timeout`` (default ``${OPERATOR_TIMEOUT}``).
        Mode from ``${OPERATOR}`` / ``<ENV_PREFIX>_OPERATOR``: console, dialog or none."""
        self._set_marks()
        return self._ask_operator(instruction, timeout)

    def _operator_mode(self) -> str:
        return operator_prompt.operator_mode(robot_var("${OPERATOR}", "") or None)

    def _ask_operator(self, instruction: str, timeout: str | None) -> str:
        timeout = timeout or robot_var("${OPERATOR_TIMEOUT}", "300s")
        self._segment(f"Operator: {instruction}")
        logger.info(f"Operator action: {instruction}")
        try:
            answer = operator_prompt.ask_operator(instruction, self._operator_mode(), timestr_to_secs(timeout))
        except operator_prompt.OperatorUnavailable as err:
            raise AssertionError(str(err)) from None
        logger.info(f"Operator confirmed: {answer}")
        return answer

    # ================================================================== timing

    @keyword
    def get_time_between(self, earlier: Any, later: Any) -> float:
        """Seconds from ``earlier`` to ``later``: entries with a ``time`` (e.g. returned by a wait), or times (ISO
        text or epoch ms)."""
        seconds = (epoch_ms(later) - epoch_ms(earlier)) / 1000
        logger.info(f"{label(earlier)} -> {label(later)}: {seconds:.3f} s")
        return seconds

    @keyword
    def time_between_should_be_less_than(self, earlier: Any, later: Any, limit: str) -> float:
        """Timing requirements ("within 20 s"): fails unless ``later`` came less than ``limit`` after ``earlier``,
        and if it came before it. Returns the seconds."""
        seconds = GrfLibrary.get_time_between(self, earlier, later)
        if seconds < 0:
            raise AssertionError(f"{label(later)} came {-seconds:.3f} s before {label(earlier)}")
        if seconds >= timestr_to_secs(limit):
            raise AssertionError(f"{label(later)} came {seconds:.3f} s after {label(earlier)}, limit {limit}")
        return seconds

    @keyword
    def time_between_should_be_at_least(self, earlier: Any, later: Any, minimum: str) -> float:
        """Fails if ``later`` came less than ``minimum`` after ``earlier`` (e.g. proves a delay took effect).
        Returns the seconds."""
        seconds = GrfLibrary.get_time_between(self, earlier, later)
        if seconds < timestr_to_secs(minimum):
            raise AssertionError(f"{label(later)} came {seconds:.3f} s after {label(earlier)}, expected at least {minimum}")
        return seconds


# ---------------------------------------------------------------------- helpers


def robot_var(name: str, default: str) -> str:
    """A Robot variable, or ``default`` (also outside a Robot run)."""
    try:
        return BuiltIn().get_variable_value(name, default)
    except Exception:  # noqa: BLE001 - outside a Robot run
        return default


def metadata(name: str, value: str) -> None:
    """Top-level suite metadata (ignored outside a Robot run)."""
    try:
        BuiltIn().set_suite_metadata(name, value, top=True)
    except Exception:  # noqa: BLE001
        pass


def truthy(value: Any) -> bool:
    return str(value).strip().lower() not in ("false", "no", "0", "off", "none", "")


def coerce(value: Any) -> Any:
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


def as_obj(value: Any) -> dict | None:
    """A dict or JSON object text -> dict (None for nothing)."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return dict(value)
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {value!r}")
    return parsed


def canonical(body: Any) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def epoch_ms(value: Any) -> float:
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


def label(value: Any) -> str:
    if isinstance(value, dict):
        return f"#{value.get('id', '?')} {value.get('name', '?')}"
    return str(value)


def put(target: dict, dotted: str, value: Any) -> None:
    """``target[a][b] = value`` for ``a.b``."""
    keys = dotted.split(".")
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = value


def match_from(fields: dict) -> dict:
    """Robot keyword arguments (``Field=Value``, dotted keys) -> a nested match object with coerced values."""
    body: dict[str, Any] = {}
    for key, value in fields.items():
        put(body, key, coerce(value))
    return body


def timeline_html(entries: list[dict], title: str, segments: list[tuple[int, str]] | tuple = (), peer: str = "product") -> str:
    """A message timeline (entries with id, time, way tx/rx, topic, name, body, valid, errors) as an HTML table for
    the Robot log, with a separator row for each segment ``(mark, label)`` before the first entry after its mark."""
    rows = []
    pending = sorted(segments)
    peer_html = html.escape(peer)

    def separators(before_id: float) -> None:
        while pending and pending[0][0] < before_id:
            text = pending.pop(0)[1]
            rows.append(f"<tr style='background:#e8eefc'><td colspan='6'><b>&#9472;&#9472; {html.escape(text)} &#9472;&#9472;</b></td></tr>")

    for e in entries:
        separators(float(e.get("id") or 0))
        style = "" if e.get("valid") is not False else ' style="background:#fde2e2"'
        arrow = f"&rarr; {peer_html}" if e.get("way") == "tx" else f"{peer_html} &rarr;"
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
