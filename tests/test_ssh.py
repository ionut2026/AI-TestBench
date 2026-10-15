"""Shared transport contracts; all channels and processes are synthetic."""

import subprocess
from types import SimpleNamespace

import pytest

from grf.ssh import OpenSshTransport, ParamikoTransport, Redactor, SshConfig, SshError


@pytest.mark.parametrize("change", [
    {"port": True}, {"connect_timeout_s": float("nan")}, {"user": ""},
    {"known_hosts": ""}, {"client": "plink"}, {"host_key_policy": "ignore"},
    {"password_env": "PW", "identity": "key"},
    {"client": "openssh", "password_file": "pw.txt"},
])
def test_common_connection_validation(change):
    with pytest.raises(ValueError):
        SshConfig(**{"name": "rig", "host": "board", "user": "root", "known_hosts": "external", **change})


def test_short_and_overlapping_secrets_are_fully_redacted():
    assert Redactor(["a", "abc"])("abc a") == "*** ***"


class DownloadChannel:
    def __init__(self, data):
        self.data = bytearray(data)
        self.closed = False

    def settimeout(self, seconds):
        assert seconds > 0

    def exec_command(self, command):
        assert command == "scp -f /logs/log.txt"

    def sendall(self, data):
        assert data == b"\0"

    def recv(self, count):
        data = bytes(self.data[:count])
        del self.data[:count]
        return data

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return 0

    def close(self):
        self.closed = True


def downloader(data):
    channel = DownloadChannel(data)
    transport = object.__new__(ParamikoTransport)
    transport.host, transport.redact = "board", Redactor(["secret"])
    transport.paramiko = SimpleNamespace(SSHException=RuntimeError)
    transport.client = SimpleNamespace(get_transport=lambda: SimpleNamespace(open_session=lambda **kwargs: channel))
    return transport, channel


def test_scp_exact_download_closes_channel(tmp_path):
    transport, channel = downloader(b"C0644 3 log.txt\nabc\0")
    output = tmp_path / "log.txt"
    transport.get("/logs/log.txt", output, timeout_s=1)
    assert output.read_bytes() == b"abc" and channel.closed


@pytest.mark.parametrize("data", [
    b"C0644 3 ../outside\nabc\0", b"C0644 -1 log.txt\n", b"C0644 5 log.txt\nab",
    b"\1secret: access denied\n", b"T123 0 123 0\n", b"x" * 4096,
])
def test_scp_rejects_bad_or_truncated_data_without_leaving_partial_output(tmp_path, data):
    transport, channel = downloader(data)
    output = tmp_path / "log.txt"
    with pytest.raises(SshError) as error:
        transport.get("/logs/log.txt", output, timeout_s=1)
    assert not output.exists() and channel.closed
    assert "secret" not in str(error.value)


def test_scp_never_overwrites_an_existing_file(tmp_path):
    transport, channel = downloader(b"C0644 3 log.txt\nabc\0")
    output = tmp_path / "log.txt"
    output.write_text("original", encoding="utf-8")
    with pytest.raises(SshError):
        transport.get("/logs/log.txt", output)
    assert output.read_text(encoding="utf-8") == "original" and channel.closed


def test_openssh_download_reserves_destination_and_removes_failed_partial(tmp_path):
    calls = []

    def runner(argv, seconds):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "remote file unavailable")

    target = SshConfig("rig", "board", "root", client="openssh", known_hosts=str(tmp_path / "hosts"))
    transport = OpenSshTransport(target, runner=runner)
    output = tmp_path / "log.txt"
    with pytest.raises(SshError, match="unavailable"):
        transport.get("/logs/log.txt", output)
    assert not output.exists() and calls[0][-2] == "root@board:/logs/log.txt"
    output.write_text("original", encoding="utf-8")
    with pytest.raises(SshError, match="reserve"):
        transport.get("/logs/log.txt", output)
    assert len(calls) == 1 and output.read_text(encoding="utf-8") == "original"
