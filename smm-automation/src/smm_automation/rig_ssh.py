"""A rig control (``SMM_RIG_CONTROL``, see ``rigcontrol.py``) that reaches the instrument's RTC board over ssh.

The site describes the board in a TOML file (``--config <file>`` or ``SMM_RIG_SSH_CONFIG``; example in
``robot\\environments\\rig_ssh.example.toml``): host, user, and the shell commands that stop, start and check appSMM and the
broker on the board, and where appSMM writes its ``.sil`` logs. Only the parts present in the file are offered as
capabilities. Two clients: OpenSSH (``client = "openssh"``, the default; ssh and scp with ``BatchMode=yes``, key
authentication only) and PuTTY (``client = "plink"``; plink and pscp with ``-batch``, a key or a password read from
``password_file``, a file outside the repository that only the site's operator writes). Neither ever prompts::

    $env:SMM_RIG_CONTROL = '"D:\\projects\\AI-TestBench\\smm-automation\\.venv\\Scripts\\python.exe" -m smm_automation.rig_ssh --config D:\\rig\\rig_ssh.toml'
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

CONFIG_ENV = "SMM_RIG_SSH_CONFIG"

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
                 port_open: Callable[[str, int], bool] = _port_open, clock: Callable[[], float] = time.monotonic):
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
        self.run, self.sleep, self.port_open, self.clock = run, sleep, port_open, clock

    def capabilities(self) -> list[str]:
        caps = []
        appsmm, broker, log = self._section("appsmm"), self._section("broker"), self._section("log")
        if appsmm.get("stop") and appsmm.get("start"):
            caps.append("restart-appsmm")
        if broker.get("stop") and broker.get("start"):
            caps.append("restart-broker")
        if log.get("dir"):
            caps.append("fetch-log")
        return caps

    def check(self) -> None:
        """Fails unless a non-interactive ssh login works (doctor calls ``capabilities``, which calls this)."""
        self.remote("true", timeout_s=30)

    def restart_appsmm(self, down_ms: int) -> None:
        section = self._section("appsmm")
        self.remote(section["stop"])
        self.sleep(down_ms / 1000)
        self.remote(section["start"])
        if section.get("running"):
            self._until(lambda: self.remote(section["running"], check=False).returncode == 0, "appSMM to run again")

    def restart_broker(self, down_ms: int) -> None:
        section = self._section("broker")
        port = int(section.get("port", 1883))
        self.remote(section["stop"])
        self.sleep(down_ms / 1000)
        self.remote(section["start"])
        self._until(lambda: self.port_open(self.host, port), f"the broker to listen on {self.host}:{port}")

    def fetch_log(self, dest: Path) -> None:
        section = self._section("log")
        dest.mkdir(parents=True, exist_ok=True)
        source = f"{self.target}:{str(section['dir']).rstrip('/')}/{section.get('pattern', 'appSMM*.sil')}"
        self._call([self.scp, *self.options, "-q", source, str(dest)], f"{self.scp} {source}")

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
    path = path or os.environ.get(CONFIG_ENV)
    if not path:
        raise RigSshError(f"no config: pass --config <file> or set {CONFIG_ENV}")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise RigSshError(f"cannot read the rig ssh config {path}: {err}") from err


def main(argv: Sequence[str] | None = None, rig: RigSsh | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m smm_automation.rig_ssh", description=__doc__.splitlines()[0])
    parser.add_argument("--config", help=f"site TOML file (default: ${CONFIG_ENV})")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    for name in ("restart-appsmm", "restart-broker"):
        sub.add_parser(name).add_argument("--down-ms", type=int, default=0)
    sub.add_parser("fetch-log").add_argument("dir", type=Path)
    args = parser.parse_args(argv)
    try:
        rig = rig or RigSsh(load_config(args.config))
        if args.command == "capabilities":
            rig.check()
            print(" ".join(rig.capabilities()))
        else:
            if args.command not in rig.capabilities():
                raise RigSshError(f"{args.command} is not configured in the rig ssh config")
            if args.command == "restart-appsmm":
                rig.restart_appsmm(args.down_ms)
            elif args.command == "restart-broker":
                rig.restart_broker(args.down_ms)
            else:
                rig.fetch_log(args.dir)
    except RigSshError as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
