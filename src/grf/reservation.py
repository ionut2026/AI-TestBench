"""Local exclusive reservation of what a tier run uses on this machine (docs/WORKFLOWS.md).

Tiers that may not run in parallel (``parallel = false``) share something that only one run at a time may use: the
offline tier one PC installation of the product, its broker and ports; the rig tier one instrument. ``<cli> run`` holds
an operating-system file lock ``<results>/.locks/<lock>.lock`` for the whole run, so a second run on the same machine
stops at once instead of disturbing the first. The lock is released by the operating system when the process ends,
even after a crash, so there is no stale lock to clean up.

This only protects runs started on this machine. Runs from several machines against one instrument need the
controller's reservation (for example a Jenkins lockable resource, configured by the instrument owner).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path


class ReservationError(RuntimeError):
    pass


def _lock(handle) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle) -> None:
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def exclusive(name: str, lock_dir: Path, what: str = "") -> Iterator[Path]:
    """Holds the lock ``name`` for the ``with`` block; raises ReservationError if another process holds it."""
    if not name or any(ch in name for ch in '\\/:*?"<>|'):
        raise ReservationError(f"invalid lock name {name!r}")
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{name}.lock"
    info = lock_dir / f"{name}.owner.json"
    handle = open(path, "a+b")  # noqa: SIM115 - held open for the whole block
    try:
        if not _lock(handle):
            holder = ""
            with contextlib.suppress(OSError, ValueError):
                data = json.loads(info.read_text(encoding="utf-8"))
                holder = f" (held by process {data.get('pid')} since {data.get('since')}: {data.get('what')})"
            raise ReservationError(f"'{name}' is in use by another run on this machine{holder}; wait for it to finish")
        info.write_text(json.dumps({"pid": os.getpid(), "since": dt.datetime.now().isoformat(timespec="seconds"),
                                    "what": what}), encoding="utf-8")
        try:
            yield path
        finally:
            with contextlib.suppress(OSError):
                info.unlink()
            _unlock(handle)
    finally:
        handle.close()
