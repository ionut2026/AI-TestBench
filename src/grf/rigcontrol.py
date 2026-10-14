"""Remote control of the automation rig through a site-specific script (plan 6.1).

How appSMM, the broker and the log files on the instrument's RTC board can be reached differs per site (ssh,
a service on the board, a power switch...), so the framework does not assume one: the site provides a command
in the environment variable ``<ENV_PREFIX>_RIG_CONTROL`` (``SMM_RIG_CONTROL`` for SMM; e.g. ``python D:\\rig\\rig_control.py`` or
``powershell -NoProfile -File D:\\rig\\rig_control.ps1``; run without a shell, or a JSON list of arguments) that
implements these subcommands (the restart targets come from the project's ``[rig] targets``; for SMM):

    capabilities                     print the supported subcommands (whitespace separated)
    restart-<target> --down-ms <ms>  stop the target, wait <ms>, start it again; return once it runs
                                     (SMM: restart-appsmm kills appSMM, no goodbye; restart-broker the MQTT broker)
    fetch-log <dir>                  copy the product's log files (``[rig] log_files``; SMM: appSMM*.sil) into <dir>
    clock                            print "<offset_ms> <uncertainty_ms>": how far the time appSMM stamps its log
                                     lines with (the board's wall-clock time, read as this PC's local time) is
                                     ahead of this PC's clock (optional; without it log times are taken as is)

Exit code 0 means done; anything else is a failure whose stderr is reported. ``<ENV_PREFIX>_RIG_CONTROL_TIMEOUT``
(seconds, default 300) bounds every call. Only the subcommands listed by ``capabilities`` are used: the rig
tier gets the capability of a target (SMM: ``restart-appsmm`` -> ``requires:restart``, ``restart-broker`` ->
``requires:broker-restart``) only from its restart subcommand, and the log capability (``needs:applog``) only
from ``fetch-log``.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from grf import project


def env_name() -> str:
    return project.current().env("RIG_CONTROL")


def timeout_env_name() -> str:
    return project.current().env("RIG_CONTROL_TIMEOUT")


def capability_of() -> dict[str, str]:
    """Rig control subcommand -> test capability (see the project's ``[capabilities]``)."""
    p = project.current()
    return {**{f"restart-{t}": cap for t, cap in p.rig_targets.items()}, "fetch-log": p.rig_log_capability}


def subcommands() -> tuple[str, ...]:
    return (*capability_of(), "clock")


def __getattr__(name: str) -> Any:
    # module constants of the current project (kept for callers of the pre-project API)
    values = {"ENV": env_name, "TIMEOUT_ENV": timeout_env_name, "SUBCOMMANDS": subcommands, "CAPABILITY_OF": capability_of}
    if name in values:
        return values[name]()
    raise AttributeError(name)


class RigControlError(RuntimeError):
    pass


def split_command(command: str) -> list[str]:
    """The rig control command as an argument list: a JSON list, or a command line (quotes group, backslashes stay)."""
    text = command.strip()
    if text.startswith("["):
        try:
            parts = json.loads(text)
        except ValueError as err:
            raise RigControlError(f"{env_name()} is not a valid JSON list: {err}") from err
        if not isinstance(parts, list) or not parts or not all(isinstance(p, str) and p.strip() for p in parts):
            raise RigControlError(f"{env_name()} must be a non-empty JSON list of strings")
        return parts
    try:
        parts = shlex.split(text, posix=False)
    except ValueError as err:
        raise RigControlError(f"{env_name()} has invalid command quoting: {err}") from err
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


class RigControl:
    def __init__(self, command: list[str], timeout_s: float = 300.0):
        if not command:
            raise RigControlError(f"{env_name()} is empty")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise RigControlError(f"{timeout_env_name()} must be a finite positive number of seconds")
        self.command = command
        self.timeout_s = timeout_s
        self._capabilities: frozenset[str] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RigControl | None:
        """The configured rig control, or None when ``<ENV_PREFIX>_RIG_CONTROL`` is not set."""
        env = os.environ if env is None else env
        command = env.get(env_name(), "").strip()
        if not command:
            return None
        try:
            timeout = float(env.get(timeout_env_name()) or 300)
        except ValueError as err:
            raise RigControlError(f"{timeout_env_name()} must be a number of seconds") from err
        return cls(split_command(command), timeout)

    def __str__(self) -> str:
        return " ".join(self.command)

    def capabilities(self) -> frozenset[str]:
        """The subcommands the site script supports (asked once)."""
        if self._capabilities is None:
            words = set(self._run("capabilities", timeout_s=min(self.timeout_s, 30)).split())
            known = subcommands()
            self._capabilities = frozenset(w for w in words if w in known)
        return self._capabilities

    def has(self, subcommand: str) -> bool:
        return subcommand in self.capabilities()

    def test_capabilities(self) -> set[str]:
        """The test capabilities (tag side) the rig gets from this script."""
        of = capability_of()
        return {of[c] for c in self.capabilities() if c in of}

    def restart(self, target: str, down_ms: int) -> None:
        """``restart-<target> --down-ms <ms>`` (a target of the project's ``[rig] targets``)."""
        self._require(f"restart-{target}")
        self._run(f"restart-{target}", "--down-ms", str(int(down_ms)))

    def fetch_log(self, dest: Path) -> list[Path]:
        """Copies the product's log files into ``dest``; returns the files found there (``[rig] log_files``)."""
        self._require("fetch-log")
        dest.mkdir(parents=True, exist_ok=True)
        self._run("fetch-log", str(dest))
        pattern = project.current().rig_log_files
        files = sorted(dest.glob(pattern))
        if not files:
            what = pattern.removeprefix("*") if pattern.startswith("*.") else pattern
            raise RigControlError(f"'{self} fetch-log' copied no {what} file into {dest}")
        return files

    def log_clock_offset(self) -> tuple[float, float]:
        """(offset, uncertainty) in ms of the rig's log clock against this PC's clock (``clock``)."""
        self._require("clock")
        words = self._run("clock", timeout_s=min(self.timeout_s, 60)).split()
        try:
            offset, uncertainty = float(words[0]), float(words[1]) if len(words) > 1 else 0.0
        except (IndexError, ValueError):
            raise RigControlError(f"'{self} clock' printed {' '.join(words)!r}, not '<offset_ms> <uncertainty_ms>'") from None
        return offset, abs(uncertainty)

    def _require(self, subcommand: str) -> None:
        if not self.has(subcommand):
            raise RigControlError(f"The rig control '{self}' does not support {subcommand} (capabilities: "
                                  f"{' '.join(sorted(self.capabilities())) or 'none'})")

    def _run(self, *args: str, timeout_s: float | None = None) -> str:
        timeout = self.timeout_s if timeout_s is None else timeout_s
        try:
            done = subprocess.run([*self.command, *args], capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError as err:
            raise RigControlError(f"Rig control '{self}' not found: {err}") from err
        except subprocess.TimeoutExpired as err:
            raise RigControlError(f"'{self} {' '.join(args)}' did not finish within {timeout:g} s") from err
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip()
            raise RigControlError(f"'{self} {' '.join(args)}' failed with exit code {done.returncode}" + (f": {detail}" if detail else ""))
        return done.stdout
