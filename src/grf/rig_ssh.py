"""A rig control (``<ENV_PREFIX>_RIG_CONTROL``, see ``rigcontrol.py``) that reaches the instrument's board over ssh.

The site describes the board in a TOML file (``--config <file>`` or ``<ENV_PREFIX>_RIG_SSH_CONFIG``; SMM example in
``robot\\environments\\rig_ssh.example.toml``): host, user, and for each restart target of the project (``[rig] targets``;
SMM: ``[appsmm]`` and ``[broker]``) the shell commands that stop and start it on the board, with its readiness (a
``running`` command that exits 0, and/or a ``port`` that accepts connections), and where the product writes its logs
(with an optional board command that first stages them, e.g. out of a container). Only the parts present in the file
are offered as capabilities; ``clock`` (the board's log
clock against this PC's) is always offered and reads the board's time with ``[clock] command``. Two clients: OpenSSH (``client = "openssh"``, the default; ssh and scp with ``BatchMode=yes``, key
authentication only) and PuTTY (``client = "plink"``; plink and pscp with ``-batch``, a key or a password read from
``password_file``, a file outside the repository that only the site's operator writes). Neither ever prompts::

    $env:SMM_RIG_CONTROL = '"<project>\\.venv\\Scripts\\python.exe" -m grf.rig_ssh --config D:\\rig\\rig_ssh.toml'
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import socket
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from grf import project
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
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=False)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


class RigSsh:
    def __init__(self, config: dict[str, Any], run: Runner = _run, sleep: Callable[[float], None] = time.sleep,
                 port_open: Callable[[str, int], bool] = _port_open, clock: Callable[[], float] = time.monotonic,
                 wall_ms: Callable[[], float] = lambda: time.time() * 1000):
        try:
            self._validate(config)
        except ValueError as err:
            raise RigSshError(str(err)) from err
        self.config = config
        self.host = str(config.get("host") or "")
        if not self.host:
            raise RigSshError("the rig ssh config needs 'host'")
        user = str(config.get("user") or "")
        self.client = str(config.get("client", "openssh"))
        if self.client == "openssh":
            self.target = f"{user}@{self.host}" if user else self.host
            self.options = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={int(config.get('connect_timeout_s', 10))}"]
            if config.get("port"):
                self.options += ["-o", f"Port={int(config['port'])}"]
            if config.get("identity"):
                self.options += ["-i", str(config["identity"])]
            self.ssh = str(config.get("ssh", "ssh"))
            self.scp = str(config.get("scp", "scp"))
        elif self.client == "plink":
            self.target = self.host
            self.options = ["-batch"]
            if user:
                self.options += ["-l", user]
            if config.get("port"):
                self.options += ["-P", str(int(config["port"]))]
            if config.get("identity"):
                self.options += ["-i", str(config["identity"])]
            if config.get("hostkey"):
                self.options += ["-hostkey", str(config["hostkey"])]
            if config.get("password_file"):
                password_file = Path(str(config["password_file"]))
                if not password_file.is_file():
                    raise RigSshError(f"password_file {password_file} does not exist")
                self.options += ["-pwfile", str(password_file)]
            self.ssh = str(config.get("ssh", "plink"))
            self.scp = str(config.get("scp", "pscp"))
        else:
            raise RigSshError(f"client must be 'openssh' or 'plink', not {self.client!r}")
        self.ready_timeout_s = float(config.get("ready_timeout_s", 180))
        self.run, self.sleep, self.port_open, self.clock, self.wall_ms = run, sleep, port_open, clock, wall_ms

    @staticmethod
    def _validate(config: dict) -> None:
        targets = set(project.current().rig_targets)
        scalar = {"host", "user", "client", "connect_timeout_s", "ready_timeout_s", "port",
                  "identity", "ssh", "scp", "hostkey", "password_file"}
        table(config, "rig SSH config", scalar | targets | {"log", "clock"})
        text(config.get("host"), "host")
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
        if section.get("running"):
            self._until(lambda: self.remote(section["running"], check=False).returncode == 0, f"{target} to run again")
        if port:
            self._until(lambda: self.port_open(self.host, int(port)), f"{target} to listen on {self.host}:{port}")

    def fetch_log(self, dest: Path) -> None:
        section = self._section("log")
        dest.mkdir(parents=True, exist_ok=True)
        if section.get("prepare"):
            self.remote(section["prepare"])
        pattern = section.get("pattern", project.current().rig_log_pattern)
        source = f"{self.target}:{str(section['dir']).rstrip('/')}/{pattern}"
        self._call([self.scp, *self.options, "-q", source, str(dest)], f"{self.scp} {source}")

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
        ssh = [self.ssh, *self.options, *(["-ssh"] if self.client == "plink" else []), self.target, command]
        return self._call(ssh, f"{self.ssh} {self.target} {command!r}", check, timeout_s)

    def _call(self, argv: list[str], what: str, check: bool = True, timeout_s: float = 120) -> subprocess.CompletedProcess[str]:
        try:
            done = self.run(argv, timeout_s)
        except FileNotFoundError as err:
            raise RigSshError(f"{argv[0]} not found: {err}") from err
        except subprocess.TimeoutExpired as err:
            raise RigSshError(f"{what} did not finish within {timeout_s:g} s") from err
        if check and done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip()
            raise RigSshError(f"{what} failed with exit code {done.returncode}" + (f": {detail}" if detail else ""))
        return done

    def _until(self, ready: Callable[[], bool], what: str) -> None:
        deadline = self.clock() + self.ready_timeout_s
        while not ready():
            if self.clock() >= deadline:
                raise RigSshError(f"timed out after {self.ready_timeout_s:g} s waiting for {what}")
            self.sleep(1)

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
    except RigSshError as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
