"""Board lock and hold file.

* Lock ``<workdir>/.grf-deploy.lock``: created atomically with ``mkdir`` before anything on the board changes, so two
  deployments (two Jenkins jobs, or Jenkins and a person) never change one board at the same time. ``owner.json``
  says who holds it. A lock older than ``lock_ttl_s`` (default 2 h) is *stale* (its deployment crashed); it is only
  removed with ``--break-stale-lock`` when complete owner/timestamp metadata proves its age.
  Initialization, takeover and token-checked release are serialized by a separate guard directory;
  an abandoned guard or incomplete lock needs owner inspection and is never automatically broken.
* Hold ``<workdir>/.grf-deploy.hold``: the instrument owner's "do not deploy" switch (``touch`` it on the board,
  optionally with a reason inside). While it exists every deployment ends with ``HELD``; a manual run may pass
  ``--ignore-hold``.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from grf.deploy.model import DeployError, Outcome
from grf.deploy.transport import Transport

LOCK = ".grf-deploy.lock"
HOLD = ".grf-deploy.hold"


def lock_path(workdir: str) -> str:
    return f"{workdir.rstrip('/')}/{LOCK}"


def hold_path(workdir: str) -> str:
    return f"{workdir.rstrip('/')}/{HOLD}"


def hold_reason(transport: Transport, workdir: str) -> str | None:
    """The hold file's text (``"(no reason given)"`` if empty), or ``None`` if there is no hold."""
    path = shlex.quote(hold_path(workdir))
    if transport.run(f"test -e {path}", check=False).returncode != 0:
        return None
    text = transport.run(f"cat {path}", check=False).stdout.strip()
    return text[:300] or "(no reason given)"


def lock_info(transport: Transport, workdir: str) -> dict[str, Any] | None:
    path = shlex.quote(lock_path(workdir))
    if transport.run(f"test -d {path}", check=False).returncode != 0:
        return None
    raw = transport.run(f"cat {path}/owner.json", check=False).stdout
    try:
        owner = json.loads(raw) if raw.strip() else {}
    except ValueError:
        owner = {"unreadable": raw[:200]}
    age = transport.run(f"echo $(( $(date +%s) - $(cat {path}/since) ))", check=False).stdout.strip()
    if not isinstance(owner, dict):
        owner = {"unreadable": raw[:200]}
    owner["ageSeconds"] = int(age) if age.isdigit() else None
    return owner


@contextmanager
def _guard(transport: Transport, workdir: str) -> Iterator[None]:
    """Serialize lock initialization, takeover and release; an abandoned guard fails closed."""
    path = shlex.quote(lock_path(workdir) + ".guard")
    if transport.run(f"mkdir {path}", check=False).returncode != 0:
        raise DeployError("deployment lock metadata is busy or its guard was abandoned; "
                          "the board owner must inspect it before retrying", Outcome.LOCKED)
    try:
        yield
    finally:
        transport.run(f"rmdir {path}")


def acquire(transport: Transport, workdir: str, owner: dict[str, Any], ttl_s: int, break_stale: bool,
            echo: Callable[[str], None]) -> None:
    if not isinstance(owner.get("token"), str) or not owner["token"]:
        raise DeployError("deployment lock requires an owner token", Outcome.LOCKED)
    with _guard(transport, workdir):
        _acquire(transport, workdir, owner, ttl_s, break_stale, echo)


def _acquire(transport: Transport, workdir: str, owner: dict[str, Any], ttl_s: int, break_stale: bool,
             echo: Callable[[str], None]) -> None:
    path = shlex.quote(lock_path(workdir))
    for _ in range(2):
        if transport.run(f"mkdir {path}", check=False).returncode == 0:
            transport.run(f"printf '%s' {shlex.quote(json.dumps(owner))} > {path}/owner.json && date +%s > {path}/since")
            return
        info = lock_info(transport, workdir) or {}
        age = info.get("ageSeconds")
        who = ", ".join(f"{k}={info[k]}" for k in ("user", "component", "jenkinsRun", "started") if info.get(k)) or "unknown"
        stale = age is not None and age > ttl_s and bool(info.get("token"))
        if stale and break_stale:
            echo(f"WARNING: removing the stale deployment lock on {transport.host} ({who}, age {age} s)")
            transport.run(f"rm -rf -- {path}")
            continue
        hint = (" It is older than the lock TTL (a crashed deployment?): check the board, then re-run with "
                "--break-stale-lock." if stale else " Wait until it finishes; incomplete/legacy lock metadata "
                "requires board-owner inspection and is never automatically broken.")
        raise DeployError(f"{transport.host} is being deployed by someone else ({who}, age {age} s).{hint}", Outcome.LOCKED)
    raise DeployError(f"could not take the deployment lock on {transport.host}", Outcome.LOCKED)


def release(transport: Transport, workdir: str, token: str) -> None:
    with _guard(transport, workdir):
        info = lock_info(transport, workdir)
        if not info or not token or info.get("token") != token:
            raise DeployError("refusing to release a deployment lock owned by another operation", Outcome.LOCKED)
        transport.run(f"rm -rf -- {shlex.quote(lock_path(workdir))}")
