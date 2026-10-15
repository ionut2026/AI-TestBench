"""Shared noninteractive SSH access for deployment and rig control.

Two clients (``[targets.<name>] client``):

* ``paramiko`` (default; needs ``pip install general-robot-framework[deploy]``): password (``password_env`` or
  ``password_file``) or key (``identity``) login; files are copied with the scp
  protocol.
* ``openssh``: the system ``ssh``/``scp`` with ``BatchMode=yes``, keys only.

Host keys are always checked against ``known_hosts`` (a file outside the repository): ``host_key_policy =
"accept-new"`` stores the key of a board seen for the first time and refuses a *changed* key; ``"pinned"`` refuses
any board whose key is not already in the file. Passwords never appear in output: every text passes ``Redactor``.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from grf.deadline import Deadline
from grf.process import run as run_process
from grf.validation import integer, number, text

CLIENTS = ("paramiko", "openssh")
HOST_KEY_POLICIES = ("accept-new", "pinned")


class SshError(RuntimeError):
    pass


@dataclass(frozen=True)
class SshConfig:
    name: str
    host: str
    user: str
    port: int = 22
    client: str = "paramiko"
    password_env: str | None = None
    password_file: str | None = None
    identity: str | None = None
    known_hosts: str = ""
    host_key_policy: str = "accept-new"
    connect_timeout_s: float = 15.0

    def __post_init__(self) -> None:
        integer(self.port, f"target {self.name} port", minimum=1, maximum=65535)
        number(self.connect_timeout_s, f"target {self.name} connect_timeout_s", positive=True)
        for key in ("name", "host", "user", "known_hosts"):
            text(getattr(self, key), f"target {self.name} {key}")
        for key in ("password_env", "password_file", "identity"):
            if getattr(self, key) is not None:
                text(getattr(self, key), f"target {self.name} {key}")
        if self.client not in CLIENTS or self.host_key_policy not in HOST_KEY_POLICIES:
            raise ValueError("target client or host_key_policy is invalid")
        if sum(getattr(self, key) is not None for key in ("password_env", "password_file", "identity")) > 1:
            raise ValueError("target must use only one of password_env, password_file and identity")
        if self.client == "openssh" and (self.password_env or self.password_file):
            raise ValueError("the openssh client uses keys only; use paramiko for a password")

    def password(self, env: Mapping[str, str]) -> str | None:
        if self.password_env:
            value = env.get(self.password_env)
            if not value:
                raise SshError(f"target {self.name}: environment variable {self.password_env} (password_env) is not set")
            return value
        if self.password_file:
            try:
                value = Path(self.password_file).read_text(encoding="utf-8").strip()
            except OSError as err:
                raise SshError(f"target {self.name}: cannot read password_file: {err.strerror}") from None
            if not value:
                raise SshError(f"target {self.name}: password_file is empty")
            return value
        return None


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class Redactor:
    def __init__(self, secrets: list[str] | None = None):
        self.secrets = [s for s in (secrets or []) if s]

    def add(self, secret: str | None) -> None:
        if secret:
            self.secrets.append(secret)

    def __call__(self, text: str) -> str:
        for secret in sorted(set(self.secrets), key=len, reverse=True):
            text = text.replace(secret, "***")
        return text


class Transport(Protocol):
    host: str
    redact: Redactor

    def run(self, command: str, timeout_s: float = 120, check: bool = True) -> CommandResult: ...

    def put(self, local: Path, remote: str, timeout_s: float = 1800) -> None: ...

    def get(self, remote: str, local: Path, timeout_s: float = 1800) -> None: ...

    def close(self) -> None: ...


def _checked(result: CommandResult, command: str, check: bool, redact: Redactor) -> CommandResult:
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SshError(redact(f"board command failed with exit code {result.returncode}: {command}"
                             + (f": {detail[-500:]}" if detail else "")))
    return CommandResult(result.returncode, redact(result.stdout), redact(result.stderr))


class ParamikoTransport:
    def __init__(self, target: SshConfig, env: Mapping[str, str], client_factory: Any = None):
        try:
            import paramiko
        except ImportError:
            raise SshError("the paramiko ssh client is not installed: "
                              "pip install \"general-robot-framework[deploy]\" (or use client = \"openssh\" with a key)") from None
        self.paramiko = paramiko
        self.host = target.host
        password = target.password(env)
        self.redact = Redactor([password] if password else [])
        known_hosts = Path(target.known_hosts)
        client = (client_factory or paramiko.SSHClient)()
        if target.host_key_policy == "accept-new":
            known_hosts.parent.mkdir(parents=True, exist_ok=True)
            known_hosts.touch(exist_ok=True)
            client.load_host_keys(str(known_hosts))
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # stores the new key in known_hosts
        else:
            if not known_hosts.is_file():
                raise SshError(f"known_hosts {known_hosts} does not exist (host_key_policy = \"pinned\")")
            client.load_host_keys(str(known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(target.host, port=target.port, username=target.user, password=password,
                           key_filename=target.identity, timeout=target.connect_timeout_s,
                           allow_agent=False, look_for_keys=False)
        except paramiko.BadHostKeyException:
            client.close()
            raise SshError(f"the host key of {target.host} CHANGED since it was stored in {known_hosts}: refused. "
                              "If the board was really replaced, the owner removes its old line from that file.") from None
        except paramiko.SSHException as err:
            client.close()
            raise SshError(self.redact(f"ssh login to {target.user}@{target.host}:{target.port} failed: {err}")) from None
        except OSError as err:
            client.close()
            raise SshError(self.redact(f"cannot reach {target.host}:{target.port}: {err}")) from None
        self.client = client

    def run(self, command: str, timeout_s: float = 120, check: bool = True) -> CommandResult:
        deadline = Deadline(timeout_s)
        channel = None
        try:
            stdin, stdout, _ = self.client.exec_command(command, timeout=deadline.remaining())
            channel = stdout.channel
            stdin.close()
            output, errors = [], []
            while True:
                deadline.remaining()
                if channel.recv_ready():
                    output.append(channel.recv(65536))
                if channel.recv_stderr_ready():
                    errors.append(channel.recv_stderr(65536))
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                deadline.sleep(0.01)
            result = CommandResult(channel.recv_exit_status(), b"".join(output).decode("utf-8", "replace"),
                                   b"".join(errors).decode("utf-8", "replace"))
        except (self.paramiko.SSHException, OSError) as err:
            raise SshError(self.redact(f"ssh error running {command}: {err}")) from None
        finally:
            if channel is not None:
                channel.close()
        return _checked(result, command, check, self.redact)

    def put(self, local: Path, remote: str, timeout_s: float = 1800) -> None:
        """Copies ``local`` to the board path ``remote`` with the scp protocol (works without an sftp server)."""
        size = local.stat().st_size
        deadline = Deadline(timeout_s)
        transport = self.client.get_transport()
        channel = transport.open_session(timeout=deadline.remaining())
        try:
            channel.settimeout(deadline.remaining())
            channel.exec_command(f"scp -t {shlex.quote(remote)}")
            self._ack(channel, "start", deadline)
            channel.settimeout(deadline.remaining())
            channel.sendall(f"C0644 {size} {Path(remote).name}\n".encode())
            self._ack(channel, "header", deadline)
            with local.open("rb") as f:
                while block := f.read(1 << 16):
                    channel.settimeout(deadline.remaining())
                    channel.sendall(block)
            channel.settimeout(deadline.remaining())
            channel.sendall(b"\0")
            self._ack(channel, "data", deadline)
            deadline.remaining()
        except (SshError, self.paramiko.SSHException, OSError) as err:
            raise SshError(self.redact(f"copying {local.name} to {self.host}:{remote} failed: {err}")) from None
        finally:
            channel.close()

    def get(self, remote: str, local: Path, timeout_s: float = 1800) -> None:
        """Download one exact file with bounded SCP, without trusting the server's filename."""
        deadline = Deadline(timeout_s)
        channel = self.client.get_transport().open_session(timeout=deadline.remaining())
        created = False
        try:
            channel.settimeout(deadline.remaining())
            channel.exec_command(f"scp -f {shlex.quote(remote)}")
            channel.sendall(b"\0")
            header = bytearray()
            while not header.endswith(b"\n"):
                if len(header) >= 4096:
                    raise SshError("scp download header is too long")
                channel.settimeout(deadline.remaining())
                byte = channel.recv(1)
                if not byte:
                    raise SshError("scp download closed before its file header")
                header.extend(byte)
            fields = bytes(header[:-1]).split(b" ", 2)
            if len(fields) != 3 or len(fields[0]) != 5 or not fields[0].startswith(b"C") \
                    or any(c not in b"01234567" for c in fields[0][1:]) or not fields[1].isdigit() \
                    or fields[2].decode("utf-8", "replace") != remote.rsplit("/", 1)[-1]:
                raise SshError(self.redact(f"scp refused or returned an unexpected file header: {bytes(header)!r}"))
            remaining = int(fields[1])
            with local.open("xb") as output:
                created = True
                channel.settimeout(deadline.remaining())
                channel.sendall(b"\0")
                while remaining:
                    channel.settimeout(deadline.remaining())
                    block = channel.recv(min(65536, remaining))
                    if not block:
                        raise SshError("scp download closed before all file bytes arrived")
                    output.write(block)
                    remaining -= len(block)
                self._ack(channel, "download data", deadline)
                channel.settimeout(deadline.remaining())
                channel.sendall(b"\0")
                while not channel.exit_status_ready():
                    deadline.sleep(0.01)
                if channel.recv_exit_status() != 0:
                    raise SshError("scp source failed after sending file bytes")
                deadline.remaining()
        except BaseException as err:
            if created:
                local.unlink(missing_ok=True)
            if isinstance(err, (SshError, self.paramiko.SSHException, OSError)):
                raise SshError(self.redact(f"downloading {remote} from {self.host} failed: {err}")) from err
            raise
        finally:
            channel.close()

    @staticmethod
    def _ack(channel: Any, what: str, deadline: Deadline) -> None:
        channel.settimeout(deadline.remaining())
        code = channel.recv(1)
        if code != b"\0":
            channel.settimeout(deadline.remaining())
            message = channel.recv(1024).decode("utf-8", "replace").strip() if code else "connection closed"
            raise SshError(f"scp refused the {what}: {message}")

    def close(self) -> None:
        self.client.close()


class OpenSshTransport:
    def __init__(self, target: SshConfig, runner: Any = None):
        self.host = target.host
        self.redact = Redactor()
        known_hosts = Path(target.known_hosts)
        if target.host_key_policy == "pinned" and not known_hosts.is_file():
            raise SshError(f"known_hosts {known_hosts} does not exist (host_key_policy = \"pinned\")")
        known_hosts.parent.mkdir(parents=True, exist_ok=True)
        strict = "accept-new" if target.host_key_policy == "accept-new" else "yes"
        self.options = ["-o", "BatchMode=yes", "-o", f"StrictHostKeyChecking={strict}",
                        "-o", f"UserKnownHostsFile={known_hosts}", "-o", f"ConnectTimeout={int(target.connect_timeout_s)}"]
        if target.identity:
            self.options += ["-i", target.identity]
        self.port = target.port
        self.login = f"{target.user}@{target.host}"
        self.runner = runner or run_process

    def _call(self, argv: list[str], timeout_s: float) -> CommandResult:
        try:
            done = self.runner(argv, timeout_s)
        except FileNotFoundError as err:
            raise SshError(f"{argv[0]} not found: {err}") from None
        except subprocess.TimeoutExpired:
            raise SshError(f"{argv[0]} did not finish within {timeout_s:g} s") from None
        return CommandResult(done.returncode, done.stdout or "", done.stderr or "")

    def run(self, command: str, timeout_s: float = 120, check: bool = True) -> CommandResult:
        result = self._call(["ssh", *self.options, "-p", str(self.port), self.login, command], timeout_s)
        if result.returncode == 255:
            raise SshError(self.redact(f"ssh to {self.login} failed: {result.stderr.strip()}"))
        return _checked(result, command, check, self.redact)

    def put(self, local: Path, remote: str, timeout_s: float = 1800) -> None:
        result = self._call(["scp", *self.options, "-P", str(self.port), "-q", "-O",
                             str(local), f"{self.login}:{shlex.quote(remote)}"], timeout_s)
        if result.returncode != 0:
            raise SshError(self.redact(f"scp of {local.name} to {self.host} failed: {result.stderr.strip()}"))

    def get(self, remote: str, local: Path, timeout_s: float = 1800) -> None:
        try:
            local.open("xb").close()
        except OSError as err:
            raise SshError(f"cannot reserve download destination {local}: {err}") from err
        try:
            result = self._call(["scp", *self.options, "-P", str(self.port), "-q", "-O",
                                 f"{self.login}:{shlex.quote(remote)}", str(local)], timeout_s)
            if result.returncode != 0:
                raise SshError(self.redact(f"scp from {self.host} failed: {result.stderr.strip()}"))
        except BaseException:
            local.unlink(missing_ok=True)
            raise

    def close(self) -> None:
        pass


def open_transport(target: SshConfig, env: Mapping[str, str]) -> Transport:
    if target.client == "openssh":
        return OpenSshTransport(target)
    return ParamikoTransport(target, env)
