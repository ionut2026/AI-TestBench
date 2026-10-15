"""Deployment adapter for the shared public :mod:`grf.ssh` transport."""

from collections.abc import Mapping
from pathlib import Path

from grf import ssh
from grf.deploy.config import Target
from grf.deploy.model import DeployError
from grf.ssh import CommandResult, Redactor, SshError


class Transport:
    def __init__(self, connection: ssh.Transport):
        self.connection = connection
        self.host, self.redact = connection.host, connection.redact

    def run(self, command: str, timeout_s: float = 120, check: bool = True) -> CommandResult:
        try:
            return self.connection.run(command, timeout_s, check)
        except SshError as err:
            raise DeployError(str(err)) from err

    def put(self, local: Path, remote: str, timeout_s: float = 1800) -> None:
        try:
            self.connection.put(local, remote, timeout_s)
        except SshError as err:
            raise DeployError(str(err)) from err

    def close(self) -> None:
        self.connection.close()


def _checked(result: CommandResult, command: str, check: bool, redact: Redactor) -> CommandResult:
    """Compatibility for injected deployment transports; policy lives in grf.ssh."""
    try:
        return ssh._checked(result, command, check, redact)
    except SshError as err:
        raise DeployError(str(err)) from err


def open_transport(target: Target, env: Mapping[str, str]) -> Transport:
    try:
        return Transport(ssh.open_transport(target, env))
    except SshError as err:
        raise DeployError(str(err)) from err
