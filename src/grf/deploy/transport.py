"""SSH access to a board for a deployment: run a command, copy a file.

Two clients (``[targets.<name>] client``):

* ``paramiko`` (default; needs ``pip install general-robot-framework[deploy]``): password (``password_env`` or
  ``password_file``) or key (``identity``) login, as EST's DeploymentLibrary does; files are copied with the scp
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
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from grf.deadline import Deadline
from grf.deploy.config import Target
from grf.deploy.model import DeployError


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class Redactor:
    def __init__(self, secrets: list[str] | None = None):
        self.secrets = [s for s in (secrets or []) if s and len(s) >= 3]

    def add(self, secret: str | None) -> None:
        if secret and len(secret) >= 3:
            self.secrets.append(secret)

    def __call__(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text


class Transport(Protocol):
    host: str
    redact: Redactor

    def run(self, command: str, timeout_s: float = 120, check: bool = True) -> CommandResult: ...

    def put(self, local: Path, remote: str, timeout_s: float = 1800) -> None: ...

    def close(self) -> None: ...


def _checked(result: CommandResult, command: str, check: bool, redact: Redactor) -> CommandResult:
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise DeployError(redact(f"board command failed with exit code {result.returncode}: {command}"
                                 + (f": {detail[-500:]}" if detail else "")))
    return CommandResult(result.returncode, redact(result.stdout), redact(result.stderr))


class ParamikoTransport:
    def __init__(self, target: Target, env: Mapping[str, str], client_factory: Any = None):
        try:
            import paramiko
        except ImportError:
            raise DeployError("the paramiko ssh client is not installed: "
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
                raise DeployError(f"known_hosts {known_hosts} does not exist (host_key_policy = \"pinned\")")
            client.load_host_keys(str(known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(target.host, port=target.port, username=target.user, password=password,
                           key_filename=target.identity, timeout=target.connect_timeout_s,
                           allow_agent=False, look_for_keys=False)
        except paramiko.BadHostKeyException:
            raise DeployError(f"the host key of {target.host} CHANGED since it was stored in {known_hosts}: refused. "
                              "If the board was really replaced, the owner removes its old line from that file.") from None
        except paramiko.SSHException as err:
            raise DeployError(self.redact(f"ssh login to {target.user}@{target.host}:{target.port} failed: {err}")) from None
        except OSError as err:
            raise DeployError(f"cannot reach {target.host}:{target.port}: {err}") from None
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
            raise DeployError(self.redact(f"ssh error running {command}: {err}")) from None
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
        except (self.paramiko.SSHException, OSError) as err:
            raise DeployError(self.redact(f"copying {local.name} to {self.host}:{remote} failed: {err}")) from None
        finally:
            channel.close()

    @staticmethod
    def _ack(channel: Any, what: str, deadline: Deadline) -> None:
        channel.settimeout(deadline.remaining())
        code = channel.recv(1)
        if code != b"\0":
            channel.settimeout(deadline.remaining())
            message = channel.recv(1024).decode("utf-8", "replace").strip() if code else "connection closed"
            raise DeployError(f"scp refused the {what}: {message}")

    def close(self) -> None:
        self.client.close()


class OpenSshTransport:
    def __init__(self, target: Target, runner: Any = None):
        self.host = target.host
        self.redact = Redactor()
        known_hosts = Path(target.known_hosts)
        if target.host_key_policy == "pinned" and not known_hosts.is_file():
            raise DeployError(f"known_hosts {known_hosts} does not exist (host_key_policy = \"pinned\")")
        known_hosts.parent.mkdir(parents=True, exist_ok=True)
        strict = "accept-new" if target.host_key_policy == "accept-new" else "yes"
        self.options = ["-o", "BatchMode=yes", "-o", f"StrictHostKeyChecking={strict}",
                        "-o", f"UserKnownHostsFile={known_hosts}", "-o", f"ConnectTimeout={int(target.connect_timeout_s)}"]
        if target.identity:
            self.options += ["-i", target.identity]
        self.port = target.port
        self.login = f"{target.user}@{target.host}"
        self.runner = runner or (lambda argv, timeout: subprocess.run(argv, capture_output=True, text=True,
                                                                     timeout=timeout, check=False))

    def _call(self, argv: list[str], timeout_s: float) -> CommandResult:
        try:
            done = self.runner(argv, timeout_s)
        except FileNotFoundError as err:
            raise DeployError(f"{argv[0]} not found: {err}") from None
        except subprocess.TimeoutExpired:
            raise DeployError(f"{argv[0]} did not finish within {timeout_s:g} s") from None
        return CommandResult(done.returncode, done.stdout or "", done.stderr or "")

    def run(self, command: str, timeout_s: float = 120, check: bool = True) -> CommandResult:
        result = self._call(["ssh", *self.options, "-p", str(self.port), self.login, command], timeout_s)
        if result.returncode == 255:
            raise DeployError(f"ssh to {self.login} failed: {result.stderr.strip()}")
        return _checked(result, command, check, self.redact)

    def put(self, local: Path, remote: str, timeout_s: float = 1800) -> None:
        result = self._call(["scp", *self.options, "-P", str(self.port), "-q", str(local), f"{self.login}:{remote}"], timeout_s)
        if result.returncode != 0:
            raise DeployError(f"scp of {local.name} to {self.host} failed: {result.stderr.strip()}")

    def close(self) -> None:
        pass


def open_transport(target: Target, env: Mapping[str, str]) -> Transport:
    if target.client == "openssh":
        return OpenSshTransport(target)
    return ParamikoTransport(target, env)
