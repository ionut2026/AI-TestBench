"""<cli> doctor: checks that a tier can run before a long run starts (plan 0.1 / 6.4).

The generic checks are the rig control (tiers with ``rig_control``: its subcommands and the log clock), the operator
and the tags the run would exclude. The project's plugin adds its own checks (``ProductPlugin.doctor_class``): what
its tiers need before (``before``) and a ``--deep`` check that talks to the product.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from grf import project
from grf.capabilities import excluded_tags, tier_capabilities
from grf.rigcontrol import RigControl, RigControlError


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


def _and(items: list[str]) -> str:
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else "".join(items)


class Doctor:
    def __init__(self, tier: str, operator: str = "none", deep: bool = False, env: Mapping[str, str] | None = None,
                 port_open: Callable[[str, int], bool] = tcp_open, is_tty: Callable[[], bool] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.project = project.current()
        self.tier, self.operator, self.deep = tier, operator, deep
        self.env = os.environ if env is None else env
        self.port_open = port_open
        self.is_tty = is_tty or (lambda: bool(sys.__stdin__ and sys.__stdin__.isatty()))
        self.sleep = sleep
        self.checks: list[Check] = []

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name, status, detail))

    def run(self) -> list[Check]:
        self.before()
        rig_control = self._rig_control() if self.project.tier(self.tier).rig_control else None
        self._operator()
        self._capabilities(rig_control)
        if self.deep:
            self.deep_check()
        return self.checks

    # ------------------------------------------------------------------ project hooks

    def before(self) -> None:
        """The project's checks of what the tier needs (service, product, ports...), before the generic ones."""

    def deep_check(self) -> None:
        """``--deep``: start the environment and talk to the product."""
        self.add("deep check", "SKIP", f"the {self.project.name} project has no deep check")

    # ------------------------------------------------------------------ generic checks

    def _rig_control(self) -> RigControl | None:
        p = self.project
        try:
            control = RigControl.from_env(self.env)
        except RigControlError as err:
            self.add("rig control", "FAIL", str(err))
            return None
        if control is None:
            missing = list(dict.fromkeys([*p.rig_targets.values(), p.rig_log_capability]))
            self.add("rig control", "WARN", f"{p.env('RIG_CONTROL')} is not set: {_and(missing)} tests will be excluded")
            return None
        try:
            caps = control.capabilities()
        except RigControlError as err:
            self.add("rig control", "FAIL", str(err))
            return None
        self.add("rig control", "OK", f"'{control}': {' '.join(sorted(caps)) or 'no subcommands'}")
        if "clock" in caps:
            try:
                offset, uncertainty = control.log_clock_offset()
            except RigControlError as err:
                self.add("rig clock", "WARN", f"{err}: {p.product} log times will be taken as they are")
            else:
                self.add("rig clock", "OK", f"{offset / 1000:+.1f} s (+/- {uncertainty / 1000:.1f} s) against this PC, "
                         f"applied to the fetched {p.product} log")
        return control

    def _operator(self) -> None:
        if self.operator == "none":
            if self.project.tier(self.tier).rig_control:
                self.add("operator", "WARN", "no operator (--operator console|dialog): "
                         f"{_and(sorted(self.project.operator_capabilities))} tests will be excluded")
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
        env = self.env
        if self.project.tier(self.tier).rig_control and rig_control is None:
            env = {k: v for k, v in self.env.items() if k != self.project.env("RIG_CONTROL")}
        try:
            caps, _ = tier_capabilities(self.tier, self.operator, env, rig_control)
        except RigControlError as err:
            self.add("capabilities", "FAIL", str(err))
            return
        excluded = excluded_tags(caps)
        self.add("capabilities", "OK", f"{', '.join(sorted(caps)) or 'none'}; excluded tags: {', '.join(excluded) or 'none'}")


def run_doctor(tier: str, operator: str = "none", deep: bool = False, **kwargs: Any) -> list[Check]:
    """Runs the project's doctor (``ProductPlugin.doctor_class``); ``kwargs`` go to its constructor."""
    return project.current().plugin.doctor_class()(tier, operator, deep, **kwargs).run()


def format_checks(checks: list[Check]) -> str:
    width = max((len(c.name) for c in checks), default=0)
    return "\n".join(f"{c.status:<4}  {c.name:<{width}}  {c.detail}" for c in checks)
