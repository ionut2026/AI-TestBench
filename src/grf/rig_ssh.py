"""A rig control (``<ENV_PREFIX>_RIG_CONTROL``, see ``rigcontrol.py``) that reaches the instrument's board over ssh.

The site describes the board in a TOML file (``--config <file>`` or ``<ENV_PREFIX>_RIG_SSH_CONFIG``):
host, user, and for each restart target of the project (``[rig] targets``) the shell commands that stop and start
it on the board, with its readiness (a
``running`` command that exits 0, and/or a ``port`` that accepts connections), and where the product writes its logs
(with an optional board command that first stages them, e.g. out of a container). Only the parts present in the file
are offered as capabilities; ``clock`` (the board's log
clock against this PC's) is always offered and reads the board's time with ``[clock] command``.
OpenSSH (default, keys only) and Paramiko (key or external password source) share deployment's
``grf.ssh`` transport, explicit ``known_hosts`` and host-key policy. PuTTY configs require migration.
Neither client prompts::

    $env:PRODUCT_RIG_CONTROL = '"<project>\\.venv\\Scripts\\python.exe" -m grf.rig_ssh --config D:\\rig\\rig_ssh.toml'
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import shlex
import socket
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import fields
from pathlib import Path, PureWindowsPath
from typing import Any

from grf import project
from grf.deadline import Deadline
from grf.process import run as run_process
from grf.ssh import OpenSshTransport, SshConfig, SshError, Transport, open_transport
from grf.validation import integer, number, table, text


def config_env() -> str:
    return project.current().env("RIG_SSH_CONFIG")


def __getattr__(name: str) -> Any:
    if name == "CONFIG_ENV":
        return config_env()
    raise AttributeError(name)

# Prints the board's local wall-clock time (what the product stamps its log lines with); 1 s resolution.
DEFAULT_CLOCK_COMMAND = "date +%Y-%m-%dT%H:%M:%S"

Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


class RigSshError(RuntimeError):
    pass


def _run(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    return run_process(argv, timeout_s)


def _port_open(host: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=min(2, timeout_s)):
            return True
    except OSError:
        return False


class RigSsh:
    def __init__(self, config: dict[str, Any], run: Runner = _run, sleep: Callable[[float], None] = time.sleep,
                 port_open: Callable[[str, int, float], bool] = _port_open, clock: Callable[[], float] = time.monotonic,
                 wall_ms: Callable[[], float] = lambda: time.time() * 1000, connection: Transport | None = None):
        try:
            self._validate(config)
            connection_keys = {field.name for field in fields(SshConfig)} - {"name"}
            settings = {key: value for key, value in config.items() if key in connection_keys}
            settings.setdefault("client", "openssh")
            settings.setdefault("host_key_policy", "pinned")
            target = SshConfig(name="rig", **settings)
            for key in ("known_hosts", "identity", "password_file"):
                value = getattr(target, key)
                if value and Path(value).resolve().is_relative_to(project.current().root.resolve()):
                    raise ValueError(f"{key} must be outside the product repository")
            self.transport = connection or (OpenSshTransport(target, runner=run) if target.client == "openssh"
                                           else open_transport(target, os.environ))
        except (ValueError, SshError) as err:
            raise RigSshError(str(err)) from err
        self.config = config
        self.host = target.host
        self.ready_timeout_s = float(config.get("ready_timeout_s", 180))
        self.run, self.sleep, self.port_open, self.clock, self.wall_ms = run, sleep, port_open, clock, wall_ms

    @staticmethod
    def _validate(config: dict) -> None:
        targets = set(project.current().rig_targets)
        if config.get("client") == "plink" or set(config).intersection({"ssh", "scp", "hostkey"}):
            raise ValueError("GRF 0.4 SSH migration: use openssh or paramiko, explicit user/known_hosts and "
                             "host_key_policy; remove plink/ssh/scp/hostkey settings (see docs/CONFIGURATION.md)")
        scalar = {field.name for field in fields(SshConfig)} - {"name"} | {"ready_timeout_s"}
        table(config, "rig SSH config", scalar | targets | {"log", "clock"})
        text(config.get("host"), "host")
        text(config.get("user"), "user")
        text(config.get("known_hosts"), "known_hosts")
        for key in scalar - {"connect_timeout_s", "ready_timeout_s", "port"}:
            if key in config:
                text(config[key], key)
        integer(config.get("port", 22), "SSH port", minimum=1, maximum=65535)
        number(config.get("connect_timeout_s", 10), "connect_timeout_s", positive=True)
        number(config.get("ready_timeout_s", 180), "ready_timeout_s", positive=True)
        for target in targets:
            section = table(config.get(target, {}), f"[{target}]", {"stop", "start", "running", "port"})
            for key in ("stop", "start", "running"):
                if key in section:
                    text(section[key], f"[{target}] {key}")
            if "port" in section:
                integer(section["port"], f"[{target}] port", minimum=1, maximum=65535)
        for name, keys in (("log", {"prepare", "pattern", "dir"}), ("clock", {"command"})):
            section = table(config.get(name, {}), f"[{name}]", keys)
            for key, value in section.items():
                text(value, f"[{name}] {key}")

    def targets(self) -> list[str]:
        """The project's restart targets this config can stop and start."""
        return [t for t in project.current().rig_targets if self._section(t).get("stop") and self._section(t).get("start")]

    def capabilities(self) -> list[str]:
        caps = [f"restart-{t}" for t in self.targets()]
        if self._section("log").get("dir"):
            caps.append("fetch-log")
        caps.append("clock")
        return caps

    def check(self) -> None:
        """Fails unless a non-interactive ssh login works (doctor calls ``capabilities``, which calls this)."""
        self.remote("true", timeout_s=30)

    def restart(self, target: str, down_ms: int) -> None:
        """Stops ``target``, waits ``down_ms``, starts it and waits until it is ready: its ``running`` command exits 0
        and/or its ``port`` (default: the project's ``[rig] ready_ports``) accepts connections."""
        try:
            integer(down_ms, "down_ms")
        except ValueError as err:
            raise RigSshError(str(err)) from err
        section = self._section(target)
        if not (section.get("stop") and section.get("start")):
            raise RigSshError(f"restart-{target} is not configured in the rig ssh config")
        port = section.get("port", project.current().rig_ready_ports.get(target))
        failure: BaseException | None = None
        try:
            self.remote(section["stop"])
            self.sleep(down_ms / 1000)
        except BaseException as err:
            failure = err
        try:
            self.remote(section["start"])
        except BaseException as err:
            prior = f"; stop/downtime also failed: {type(failure).__name__}: {failure}" if failure else ""
            raise RigSshError(f"recovery start of {target} failed; target may be down: {err}{prior}") from err
        if failure is not None:
            raise failure
        deadline = Deadline(self.ready_timeout_s, clock=self.clock)
        if section.get("running"):
            self._until(lambda timeout: self.remote(section["running"], check=False, timeout_s=timeout).returncode == 0,
                        f"{target} to run again", deadline)
        if port:
            self._until(lambda timeout: self.port_open(self.host, int(port), timeout),
                        f"{target} to listen on {self.host}:{port}", deadline)

    def fetch_log(self, dest: Path) -> None:
        section = self._section("log")
        dest.mkdir(parents=True, exist_ok=True)
        deadline = Deadline(120)
        if section.get("prepare"):
            self.remote(section["prepare"], timeout_s=deadline.remaining())
        pattern = section.get("pattern", project.current().rig_log_pattern)
        directory = str(section["dir"]).rstrip("/")
        if not directory.startswith("/") and directory:
            raise RigSshError("log dir must be an absolute board path")
        listing = self.remote(f"find {shlex.quote(directory or '/')} -maxdepth 1 -type f "
                              f"-name {shlex.quote(pattern)} -print0", timeout_s=deadline.remaining()).stdout
        if listing and not listing.endswith("\0"):
            raise RigSshError("log listing is incomplete (missing final NUL separator)")
        files = listing.split("\0")
        paths = [path for path in files if path]
        if not paths:
            raise RigSshError(f"no board logs match {pattern!r} in {directory}")
        if len(set(paths)) != len(paths) or any(
            not path.startswith(directory + "/") or "/" in path[len(directory) + 1:]
            or path.rsplit("/", 1)[-1] in {"", ".", ".."}
            or PureWindowsPath(path.rsplit("/", 1)[-1]).is_reserved()
            or path.endswith((".", " ")) or any(c in path.rsplit("/", 1)[-1] for c in '<>:"\\|?*')
            or any(ord(c) < 32 for c in path) for path in paths
        ):
            raise RigSshError("log listing returned an unsafe or duplicate filename")
        for path in paths:
            try:
                self.transport.get(path, dest / path.rsplit("/", 1)[-1], deadline.remaining())
            except (SshError, OSError) as err:
                raise RigSshError(str(err)) from err
        deadline.remaining()

    def clock_offset(self, samples: int = 3) -> tuple[float, float]:
        """(offset, uncertainty) in ms of the board's wall clock, read as this PC's local time (as the log reader
        does), against this PC's clock. Each sample bounds the offset by the time before and after the remote call."""
        command = str(self._section("clock").get("command") or DEFAULT_CLOCK_COMMAND)
        low, high = -float("inf"), float("inf")
        for _ in range(max(samples, 1)):
            before = self.wall_ms()
            text = self.remote(command, timeout_s=30).stdout.strip()
            after = self.wall_ms()
            if not math.isfinite(before) or not math.isfinite(after) or after < before:
                raise RigSshError("local wall clock moved backwards or returned a nonfinite sample")
            try:
                stamp = dt.datetime.fromisoformat(text.replace(",", "."))
            except ValueError:
                raise RigSshError(f"the clock command {command!r} printed {text!r}, not an ISO time") from None
            board = stamp.replace(tzinfo=None).timestamp() * 1000
            resolution = 1 if "." in text or "," in text else 1000
            low, high = max(low, board - after), min(high, board + resolution - before)
        if low > high:
            raise RigSshError("clock sample bounds contradict each other; clock synchronization is unavailable")
        return (low + high) / 2, (high - low) / 2

    def remote(self, command: str, check: bool = True, timeout_s: float = 120) -> subprocess.CompletedProcess[str]:
        try:
            done = self.transport.run(command, timeout_s, check)
        except (SshError, OSError) as err:
            raise RigSshError(str(err)) from err
        return subprocess.CompletedProcess(command, done.returncode, done.stdout, done.stderr)

    def close(self) -> None:
        self.transport.close()

    def _until(self, ready: Callable[[float], bool], what: str, deadline: Deadline) -> None:
        try:
            while True:
                result = ready(deadline.remaining())
                deadline.remaining()
                if result:
                    return
                deadline.sleep(1, sleep=self.sleep)
        except TimeoutError as err:
            raise RigSshError(f"timed out after {self.ready_timeout_s:g} s waiting for {what}") from err

    def _section(self, name: str) -> dict[str, Any]:
        section = self.config.get(name) or {}
        if not isinstance(section, dict):
            raise RigSshError(f"[{name}] in the rig ssh config must be a table")
        return section


def load_config(path: str | None) -> dict[str, Any]:
    path = path or os.environ.get(config_env())
    if not path:
        raise RigSshError(f"no config: pass --config <file> or set {config_env()}")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise RigSshError(f"cannot read the rig ssh config {path}: {err}") from err


def main(argv: Sequence[str] | None = None, rig: RigSsh | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m grf.rig_ssh", description=__doc__.splitlines()[0])
    parser.add_argument("--config", help=f"site TOML file (default: ${config_env()})")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    sub.add_parser("clock")
    for target in project.current().rig_targets:
        sub.add_parser(f"restart-{target}").add_argument("--down-ms", type=int, default=0)
    sub.add_parser("fetch-log").add_argument("dir", type=Path)
    args = parser.parse_args(argv)
    try:
        rig = rig or RigSsh(load_config(args.config))
        if args.command == "capabilities":
            rig.check()
            print(" ".join(rig.capabilities()))
        elif args.command == "clock":
            offset, uncertainty = rig.clock_offset()
            print(f"{offset:.0f} {uncertainty:.0f}")
        else:
            if args.command not in rig.capabilities():
                raise RigSshError(f"{args.command} is not configured in the rig ssh config")
            if args.command == "fetch-log":
                rig.fetch_log(args.dir)
            else:
                rig.restart(args.command.removeprefix("restart-"), args.down_ms)
    except (RigSshError, TimeoutError) as err:
        print(err, file=sys.stderr)
        return 1
    finally:
        if rig is not None:
            rig.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
