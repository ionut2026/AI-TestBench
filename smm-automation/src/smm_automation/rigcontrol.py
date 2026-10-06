"""Remote control of the automation rig through a site-specific script (plan 6.1).

How appSMM, the broker and the log files on the instrument's RTC board can be reached differs per site (ssh,
a service on the board, a power switch...), so the framework does not assume one: the site provides a command
in the environment variable ``SMM_RIG_CONTROL`` (e.g. ``python D:\\rig\\rig_control.py`` or
``powershell -NoProfile -File D:\\rig\\rig_control.ps1``; run without a shell, or a JSON list of arguments) that
implements these subcommands:

    capabilities                     print the supported subcommands (whitespace separated)
    restart-appsmm --down-ms <ms>    kill appSMM (no goodbye), wait <ms>, start it again; return once it runs
    restart-broker --down-ms <ms>    stop the MQTT broker, wait <ms>, start it again; return once it listens
    fetch-log <dir>                  copy appSMM's SmartInspect log files (appSMM*.sil) into <dir>

Exit code 0 means done; anything else is a failure whose stderr is reported. ``SMM_RIG_CONTROL_TIMEOUT``
(seconds, default 300) bounds every call. Only the subcommands listed by ``capabilities`` are used: the rig
tier runs ``requires:restart`` tests only with ``restart-appsmm``, ``requires:broker-restart`` tests only with
``restart-broker`` and ``needs:applog`` tests only with ``fetch-log``.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path

ENV = "SMM_RIG_CONTROL"
TIMEOUT_ENV = "SMM_RIG_CONTROL_TIMEOUT"
SUBCOMMANDS = ("restart-appsmm", "restart-broker", "fetch-log")
# rig control subcommand -> test capability (see capabilities.TAG_CAPABILITY)
CAPABILITY_OF = {"restart-appsmm": "restart", "restart-broker": "broker-restart", "fetch-log": "applog"}


class RigControlError(RuntimeError):
    pass


def split_command(command: str) -> list[str]:
    """``SMM_RIG_CONTROL`` as an argument list: a JSON list, or a command line (quotes group, backslashes stay)."""
    text = command.strip()
    if text.startswith("["):
        try:
            parts = json.loads(text)
        except ValueError as err:
            raise RigControlError(f"{ENV} is not a valid JSON list: {err}") from err
        if not isinstance(parts, list) or not parts or not all(isinstance(p, str) for p in parts):
            raise RigControlError(f"{ENV} must be a non-empty JSON list of strings")
        return parts
    parts = shlex.split(text, posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


class RigControl:
    def __init__(self, command: list[str], timeout_s: float = 300.0):
        if not command:
            raise RigControlError(f"{ENV} is empty")
        self.command = command
        self.timeout_s = timeout_s
        self._capabilities: frozenset[str] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RigControl | None:
        """The configured rig control, or None when ``SMM_RIG_CONTROL`` is not set."""
        env = os.environ if env is None else env
        command = env.get(ENV, "").strip()
        if not command:
            return None
        try:
            timeout = float(env.get(TIMEOUT_ENV) or 300)
        except ValueError as err:
            raise RigControlError(f"{TIMEOUT_ENV} must be a number of seconds") from err
        return cls(split_command(command), timeout)

    def __str__(self) -> str:
        return " ".join(self.command)

    def capabilities(self) -> frozenset[str]:
        """The subcommands the site script supports (asked once)."""
        if self._capabilities is None:
            words = set(self._run("capabilities", timeout_s=min(self.timeout_s, 30)).split())
            self._capabilities = frozenset(w for w in words if w in SUBCOMMANDS)
        return self._capabilities

    def has(self, subcommand: str) -> bool:
        return subcommand in self.capabilities()

    def test_capabilities(self) -> set[str]:
        """The test capabilities (tag side) the rig gets from this script."""
        return {CAPABILITY_OF[c] for c in self.capabilities()}

    def restart_appsmm(self, down_ms: int) -> None:
        self._require("restart-appsmm")
        self._run("restart-appsmm", "--down-ms", str(int(down_ms)))

    def restart_broker(self, down_ms: int) -> None:
        self._require("restart-broker")
        self._run("restart-broker", "--down-ms", str(int(down_ms)))

    def fetch_log(self, dest: Path) -> list[Path]:
        """Copies appSMM's log files into ``dest``; returns the ``.sil`` files found there."""
        self._require("fetch-log")
        dest.mkdir(parents=True, exist_ok=True)
        self._run("fetch-log", str(dest))
        files = sorted(dest.glob("*.sil"))
        if not files:
            raise RigControlError(f"'{self} fetch-log' copied no .sil file into {dest}")
        return files

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
