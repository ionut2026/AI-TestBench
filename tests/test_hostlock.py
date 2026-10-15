import json
import os
import socket
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import psutil
import pytest

from grf import hostlock
from grf.project import ProjectError
from grf.reservation import exclusive


def registry(directory):
    return json.loads((directory / "leases.json").read_text(encoding="utf-8"))["leases"]


def test_acquire_atomic_set_heartbeat_and_idempotent_release(tmp_path):
    with hostlock.acquire(["service:demo", "tcp:1883"], run_id="run-1", lock_dir=tmp_path) as lease:
        entry = registry(tmp_path)[lease.lease_id]
        assert entry["resources"] == ["service:demo", "tcp:1883"]
        assert entry["runId"] == "run-1"
        assert entry["owner"] == asdict(hostlock.process_identity())
        first = entry["heartbeatNs"]
        assert lease.heartbeat() > first
        assert registry(tmp_path)[lease.lease_id]["heartbeatNs"] > first
        with pytest.raises(hostlock.HostLockError, match="live process") as err:
            hostlock.acquire(["tcp:1883", "service:spare"], run_id="run-2", lock_dir=tmp_path)
        assert all(value in str(err.value) for value in ("run-1", lease.lease_id, "pid", "created", "user", "host"))
        assert list(registry(tmp_path)) == [lease.lease_id]
        with hostlock.acquire(["service:spare"], run_id="run-3", lock_dir=tmp_path):
            assert len(registry(tmp_path)) == 2
    lease.release()
    assert registry(tmp_path) == {}
    with pytest.raises(hostlock.HostLockError, match="released"):
        lease.heartbeat()


@pytest.mark.parametrize("probe", ["gone", "recycled", "alive", "inaccessible"])
def test_stale_recovery_requires_proof_not_heartbeat_age(tmp_path, monkeypatch, probe):
    lease = hostlock.acquire(["broker:demo"], run_id="previous", lock_dir=tmp_path)
    state = registry(tmp_path)
    state[lease.lease_id]["heartbeatNs"] = 0
    (tmp_path / "leases.json").write_text(json.dumps({"schema": 1, "leases": state}), encoding="utf-8")
    owner = lease.owner

    def identity(pid=None):
        if pid is None:
            return owner
        if probe == "inaccessible":
            raise hostlock.HostLockError("access denied")
        return None if probe == "gone" else replace(owner, created=owner.created + 1) if probe == "recycled" else owner

    monkeypatch.setattr(hostlock, "process_identity", identity)
    if probe in ("alive", "inaccessible"):
        with pytest.raises(hostlock.HostLockError, match="live process|cannot be proven gone"):
            hostlock.acquire(["broker:demo"], run_id="next", lock_dir=tmp_path)
        assert registry(tmp_path) == state
    else:
        with hostlock.acquire(["broker:demo"], run_id="next", lock_dir=tmp_path) as next_lease:
            assert next_lease.lease_id != lease.lease_id
            assert list(registry(tmp_path)) == [next_lease.lease_id]
            lease.release()  # Missing old ID cannot release its replacement.
            assert list(registry(tmp_path)) == [next_lease.lease_id]


def test_different_host_cannot_be_recovered(tmp_path, monkeypatch):
    lease = hostlock.acquire(["service:demo"], run_id="old", lock_dir=tmp_path)
    monkeypatch.setattr(hostlock, "process_identity", lambda pid=None: replace(lease.owner, host="other-host"))
    with pytest.raises(hostlock.HostLockError, match="different host"):
        hostlock.acquire(["service:demo"], run_id="new", lock_dir=tmp_path)
    assert list(registry(tmp_path)) == [lease.lease_id]


@pytest.mark.parametrize("action", ["release", "heartbeat"])
def test_mutation_checks_original_process_creation(tmp_path, monkeypatch, action):
    lease = hostlock.acquire(["service:demo"], run_id="old", lock_dir=tmp_path)
    monkeypatch.setattr(hostlock, "process_identity", lambda pid=None: replace(lease.owner, created=lease.owner.created + 1))
    with pytest.raises(hostlock.HostLockError, match="original process"):
        getattr(lease, action)()
    assert list(registry(tmp_path)) == [lease.lease_id]


@pytest.mark.parametrize("action", ["release", "heartbeat"])
def test_mutation_checks_persisted_ownership(tmp_path, action):
    lease = hostlock.acquire(["service:demo"], run_id="old", lock_dir=tmp_path)
    state = registry(tmp_path)
    state[lease.lease_id]["runId"] = "replacement"
    (tmp_path / "leases.json").write_text(json.dumps({"schema": 1, "leases": state}), encoding="utf-8")
    with pytest.raises(hostlock.HostLockError, match="ownership changed"):
        getattr(lease, action)()
    assert registry(tmp_path) == state


def test_missing_heartbeat_is_not_success(tmp_path):
    lease = hostlock.acquire(["service:demo"], run_id="old", lock_dir=tmp_path)
    (tmp_path / "leases.json").write_text('{"schema":1,"leases":{}}', encoding="utf-8")
    with pytest.raises(hostlock.HostLockError, match="no longer registered"):
        lease.heartbeat()
    lease.release()


@pytest.mark.parametrize("resources", [[], "tcp:1883", ["x", "x"], [""], [" x"], [7]])
def test_invalid_resources_fail_before_directory_creation(tmp_path, resources):
    directory = tmp_path / "new"
    with pytest.raises(hostlock.HostLockError):
        hostlock.acquire(resources, run_id="run", lock_dir=directory)
    assert not directory.exists()


def test_empty_run_id_is_rejected(tmp_path):
    with pytest.raises(hostlock.HostLockError, match="run ID"):
        hostlock.acquire(["x"], run_id="", lock_dir=tmp_path)


def test_relative_directory_is_rejected():
    with pytest.raises(hostlock.HostLockError, match="absolute"):
        hostlock.acquire(["x"], run_id="run", lock_dir="relative")


@pytest.mark.parametrize("state", [
    "broken JSON", "[]", '{"schema":2,"leases":{}}', '{"schema":true,"leases":{}}',
    '{"schema":1,"leases":[]}', '{"schema":1,"leases":{"bad":{}}}',
])
def test_corrupt_registry_never_permits_acquisition(tmp_path, state):
    (tmp_path / "leases.json").write_text(state, encoding="utf-8")
    with pytest.raises(hostlock.HostLockError, match="registry"):
        hostlock.acquire(["x"], run_id="run", lock_dir=tmp_path)
    assert (tmp_path / "leases.json").read_text(encoding="utf-8") == state


def test_registry_mutex_contention_is_fail_closed(tmp_path):
    with exclusive("registry", tmp_path):
        with pytest.raises(hostlock.HostLockError, match="registry unavailable"):
            hostlock.acquire(["x"], run_id="run", lock_dir=tmp_path)
    assert not (tmp_path / "leases.json").exists()


def test_atomic_write_failure_preserves_registry_and_all_resources(tmp_path, monkeypatch):
    lease = hostlock.acquire(["x"], run_id="run", lock_dir=tmp_path)
    original = (tmp_path / "leases.json").read_bytes()
    monkeypatch.setattr(Path, "replace", lambda *args: (_ for _ in ()).throw(OSError("injected write failure")))
    with pytest.raises(hostlock.HostLockError, match="injected write failure"):
        hostlock.acquire(["y", "z"], run_id="next", lock_dir=tmp_path)
    assert (tmp_path / "leases.json").read_bytes() == original
    assert not list(tmp_path.glob("leases-*.tmp"))
    assert lease.lease_id in registry(tmp_path)


def test_failed_release_can_be_retried_without_losing_ownership(tmp_path, monkeypatch):
    lease = hostlock.acquire(["x"], run_id="run", lock_dir=tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", lambda *args: (_ for _ in ()).throw(OSError("injected failure")))
        with pytest.raises(hostlock.HostLockError, match="injected failure"):
            lease.release()
    assert list(registry(tmp_path)) == [lease.lease_id]
    lease.release()
    lease.release()
    assert registry(tmp_path) == {}


def test_stale_recovery_is_not_committed_when_another_requested_resource_is_live(tmp_path, monkeypatch):
    stale = hostlock.acquire(["stale"], run_id="old", lock_dir=tmp_path)
    live = hostlock.acquire(["live"], run_id="active", lock_dir=tmp_path)
    state = registry(tmp_path)
    state[stale.lease_id]["owner"]["pid"] += 100000
    (tmp_path / "leases.json").write_text(json.dumps({"schema": 1, "leases": state}), encoding="utf-8")
    owner = live.owner
    monkeypatch.setattr(hostlock, "process_identity",
                        lambda pid=None: None if pid == owner.pid + 100000 else owner)
    with pytest.raises(hostlock.HostLockError, match="live process"):
        hostlock.acquire(["stale", "live", "new"], run_id="next", lock_dir=tmp_path)
    assert registry(tmp_path) == state


def test_overlapping_registry_entries_are_corruption_not_permission_to_take_over(tmp_path):
    first = hostlock.acquire(["x"], run_id="first", lock_dir=tmp_path)
    state = registry(tmp_path)
    state["duplicate"] = dict(state[first.lease_id])
    (tmp_path / "leases.json").write_text(json.dumps({"schema": 1, "leases": state}), encoding="utf-8")
    with pytest.raises(hostlock.HostLockError, match="overlapping"):
        hostlock.acquire(["unrelated"], run_id="next", lock_dir=tmp_path)


def test_heartbeat_increases_even_if_clock_reading_does_not(tmp_path, monkeypatch):
    lease = hostlock.acquire(["x"], run_id="run", lock_dir=tmp_path)
    before = registry(tmp_path)[lease.lease_id]["heartbeatNs"]
    monkeypatch.setattr(hostlock.time, "monotonic_ns", lambda: 0)
    assert lease.heartbeat() == before + 1
    lease.release()


def test_no_current_creation_identity_cannot_claim_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(hostlock, "process_identity", lambda: None)
    with pytest.raises(hostlock.HostLockError, match="current process identity"):
        hostlock.acquire(["x"], run_id="run", lock_dir=tmp_path)
    assert not (tmp_path / "leases.json").exists()


def test_context_preserves_body_failure_when_release_also_fails(tmp_path, monkeypatch):
    lease = hostlock.acquire(["x"], run_id="run", lock_dir=tmp_path)

    def failed_release():
        raise hostlock.HostLockError("release failed")

    monkeypatch.setattr(lease, "release", failed_release)
    with pytest.raises(ValueError, match="body") as err, lease:
        raise ValueError("body")
    assert "release failed" in err.value.__notes__[0]
    with pytest.raises(hostlock.HostLockError, match="release failed"), lease:
        pass


def test_real_process_identity_includes_os_creation_time():
    identity = hostlock.process_identity()
    assert identity.pid == os.getpid()
    assert identity.created == psutil.Process().create_time()
    assert identity.host == socket.gethostname().casefold()
    assert identity.user


def test_os_identity_probe_distinguishes_gone_and_inaccessible(monkeypatch):
    def gone(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, "Process", gone)
    assert hostlock.process_identity(123) is None

    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, "Process", denied)
    with pytest.raises(hostlock.HostLockError, match="cannot establish"):
        hostlock.process_identity(123)


def test_live_owner_excludes_another_process(tmp_path):
    script = """
import sys
from grf.hostlock import acquire, HostLockError
try:
    acquire(["tcp:1883", "service:spare"], run_id="child", lock_dir=sys.argv[1])
except HostLockError as error:
    print(error)
    sys.exit(3)
sys.exit(0)
"""
    with hostlock.acquire(["tcp:1883"], run_id="parent", lock_dir=tmp_path):
        result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 3, result.stderr
        assert "parent" in result.stdout and "live process" in result.stdout
        assert len(registry(tmp_path)) == 1


def test_real_crashed_owner_can_be_recovered_without_pid_only_reasoning(tmp_path):
    script = """
import os, sys
from grf.hostlock import acquire
acquire(["broker:demo", "tcp:1883"], run_id="crashed", lock_dir=sys.argv[1])
os._exit(9)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 9, result.stderr
    old = next(iter(registry(tmp_path)))
    with hostlock.acquire(["broker:demo", "tcp:1883"], run_id="recovered", lock_dir=tmp_path) as lease:
        assert list(registry(tmp_path)) == [lease.lease_id]
        assert lease.lease_id != old
    assert registry(tmp_path) == {}


def test_project_hostlock_directory_is_absolute_shared_and_not_checkout(widget):
    shared = widget.root.parent / "shared-hostlocks"
    widget.data["hostlock"] = {"directory": str(shared)}
    widget.validate()
    assert widget.hostlock_dir == shared.resolve()
    for value in ["relative", str(widget.root), str(widget.root / "locks"), ""]:
        widget.data["hostlock"]["directory"] = value
        with pytest.raises(ProjectError):
            widget.validate()


def test_default_directory_does_not_depend_on_checkout_or_user(widget, monkeypatch, tmp_path):
    if os.name == "nt":
        monkeypatch.setenv("PROGRAMDATA", str(tmp_path.parent / "machine-state"))
    expected = hostlock.default_lock_dir()
    monkeypatch.chdir(widget.root)
    assert widget.hostlock_dir == expected
    assert not expected.is_relative_to(widget.root)
    if os.name == "nt":
        monkeypatch.delenv("PROGRAMDATA")
        with pytest.raises(hostlock.HostLockError, match="PROGRAMDATA"):
            hostlock.default_lock_dir()
