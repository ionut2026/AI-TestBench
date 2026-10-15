"""Opt-in, same-machine resource leases; trusted shared directory, not a distributed lock."""

from __future__ import annotations

import contextlib
import getpass
import json
import os
import socket
import tempfile
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from grf.reservation import ReservationError, exclusive
from grf.validation import integer, number, table, text


class HostLockError(RuntimeError):
    """Ownership is unavailable, uncertain or inconsistent; no takeover is authorized."""


@dataclass(frozen=True)
class ProcessIdentity:
    host: str
    user: str
    pid: int
    created: float  # OS process creation timestamp (seconds since epoch), never PID alone


def process_identity(pid: int | None = None) -> ProcessIdentity | None:
    """Read OS creation identity; None means proven nonexistent, not inaccessible."""
    try:
        process = psutil.Process(os.getpid() if pid is None else pid)
        return ProcessIdentity(socket.gethostname().casefold(), getpass.getuser() if pid is None else process.username(),
                               process.pid, process.create_time())
    except psutil.NoSuchProcess:
        return None
    except (psutil.Error, OSError, KeyError) as err:
        raise HostLockError(f"cannot establish process identity: {err}") from err


def default_lock_dir() -> Path:
    """Shared machine state, independent of project checkout and current user."""
    if os.name == "nt":
        root = os.environ.get("PROGRAMDATA")
        if not root:
            raise HostLockError("PROGRAMDATA is unavailable; configure an absolute shared hostlock directory")
        path = Path(root) / "GRF" / "hostlocks"
    else:
        path = Path("/var/tmp/grf-hostlocks")
    if not path.is_absolute():
        raise HostLockError("default hostlock directory must be absolute")
    return path


def _directory(value: str | Path | None) -> Path:
    path = default_lock_dir() if value is None else Path(value)
    if not path.is_absolute():
        raise HostLockError("hostlock directory must be an absolute, shared machine path")
    return path.resolve()


@contextlib.contextmanager
def _guard(directory: Path) -> Iterator[None]:
    try:
        with exclusive("registry", directory, "GRF host-wide resource registry"):
            yield
    except (ReservationError, OSError) as err:
        raise HostLockError(f"hostlock registry unavailable at {directory}: {err}") from err


def _read(directory: Path) -> dict[str, dict[str, Any]]:
    path = directory / "leases.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        raise HostLockError(f"cannot read hostlock registry {path}: {err}") from err
    try:
        table(data, "hostlock registry", {"schema", "leases"})
        if type(data.get("schema")) is not int or data["schema"] != 1:
            raise ValueError("unsupported hostlock schema")
        leases = table(data.get("leases"), "hostlock leases")
        occupied: set[str] = set()
        for lease_id, entry in leases.items():
            text(lease_id, "lease ID")
            table(entry, "lease", {"owner", "resources", "runId", "heartbeatNs"})
            owner = table(entry.get("owner"), "owner", {"host", "user", "pid", "created"})
            text(owner.get("host"), "owner host")
            text(owner.get("user"), "owner user")
            integer(owner.get("pid"), "owner PID", minimum=1)
            number(owner.get("created"), "owner creation time", positive=True)
            text(entry.get("runId"), "run ID")
            integer(entry.get("heartbeatNs"), "monotonic heartbeat")
            resources = _resources(entry.get("resources"))
            if occupied.intersection(resources):
                raise ValueError("hostlock registry contains overlapping resource leases")
            occupied.update(resources)
        return leases
    except ValueError as err:
        raise HostLockError(f"invalid hostlock registry {path}: {err}") from err


def _write(directory: Path, leases: dict[str, dict[str, Any]]) -> None:
    fd, name = tempfile.mkstemp(prefix="leases-", suffix=".tmp", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"schema": 1, "leases": leases}, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(directory / "leases.json")
        if os.name != "nt":
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _resources(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("resources must be a nonempty list or tuple")
    for value in values:
        text(value, "resource name")
        if value != value.strip():
            raise ValueError("resource names must not have surrounding whitespace")
    if len(set(values)) != len(values):
        raise ValueError("duplicate resource names")
    return tuple(sorted(values))


@dataclass
class Lease:
    """An owner-bound lease. Heartbeat explicitly; release or use as a context manager."""

    directory: Path
    lease_id: str
    run_id: str
    resources: tuple[str, ...]
    owner: ProcessIdentity
    _released: bool = False

    def _entry(self, leases: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        current = process_identity()
        if current != self.owner:
            raise HostLockError("only the original process creation identity may mutate this lease")
        entry = leases.get(self.lease_id)
        if entry is not None and (entry["owner"] != asdict(self.owner) or entry["runId"] != self.run_id
                                  or tuple(entry["resources"]) != self.resources):
            raise HostLockError(f"lease ownership changed for {self.lease_id}; refusing mutation")
        return entry

    def heartbeat(self) -> int:
        """Persist and return monotonic nanoseconds; heartbeat age never permits takeover."""
        if self._released:
            raise HostLockError("lease is already released")
        with _guard(self.directory):
            leases = _read(self.directory)
            entry = self._entry(leases)
            if entry is None:
                raise HostLockError(f"lease {self.lease_id} is no longer registered")
            entry["heartbeatNs"] = max(time.monotonic_ns(), entry["heartbeatNs"] + 1)
            _write(self.directory, leases)
            return int(entry["heartbeatNs"])

    def release(self) -> None:
        """Verify owner before removing only this lease; repeated successful release is a no-op."""
        if self._released:
            return
        with _guard(self.directory):
            leases = _read(self.directory)
            if self._entry(leases) is not None:
                del leases[self.lease_id]
                _write(self.directory, leases)
            self._released = True

    def __enter__(self) -> Lease:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.release()
        except BaseException as err:
            if exc is not None:
                exc.add_note(f"Host resource release also failed: {err}")
                return
            raise


def acquire(resources: Sequence[str], *, run_id: str, lock_dir: str | Path | None = None) -> Lease:
    """Atomically acquire all names or none, recovering only proven-dead process identities.

    All projects/users must share the same protected directory and resource naming convention.
    Never use this as proof that orphaned services are stopped or an instrument is safe.
    """
    try:
        names = _resources(resources)
        text(run_id, "run ID")
    except ValueError as err:
        raise HostLockError(str(err)) from err
    directory = _directory(lock_dir)
    owner = process_identity()
    if owner is None:
        raise HostLockError("cannot establish the current process identity")
    with _guard(directory):
        leases = _read(directory)
        stale = []
        for lease_id, entry in leases.items():
            if not set(names).intersection(entry["resources"]):
                continue
            previous = entry["owner"]
            details = f"{previous}, run {entry['runId']!r}, lease {lease_id}, heartbeat {entry['heartbeatNs']}"
            if previous["host"] != owner.host:
                raise HostLockError(f"resource owner belongs to a different host; refusing takeover: {details}")
            try:
                live = process_identity(previous["pid"])
            except HostLockError as err:
                raise HostLockError(f"resource owner cannot be proven gone: {details}: {err}") from err
            if live is not None and live.created == previous["created"]:
                raise HostLockError(f"resources are owned by a live process: {details}")
            stale.append(lease_id)
        for lease_id in stale:
            del leases[lease_id]
        lease_id = uuid.uuid4().hex
        leases[lease_id] = {"owner": asdict(owner), "runId": run_id, "resources": list(names),
                            "heartbeatNs": time.monotonic_ns()}
        _write(directory, leases)
    return Lease(directory, lease_id, run_id, names, owner)
