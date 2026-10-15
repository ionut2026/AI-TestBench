"""The deployment record: one JSON file per deployment (``deployment.json`` in its output folder) that says who
deployed which build where, with checksum, provenance, every step and the outcome. ``<cli> run --deployment`` shows
it in the traceability report; ``audit_log`` in the site config also appends it as one line to a site audit file."""

from __future__ import annotations

import datetime as dt
import getpass
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from grf.deploy.model import ArtifactRef, Installed, Outcome, Step

RECORD_SCHEMA = 1
RECORD_FILE = "deployment.json"


def actor(env: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Who started the deployment: the Jenkins user (``BUILD_USER_ID``, Build User Vars plugin) and run, else the
    OS user."""
    env = os.environ if env is None else env
    try:
        user = getpass.getuser()
    except OSError:
        user = None
    return {"user": env.get("BUILD_USER_ID") or user, "jenkinsRun": env.get("BUILD_URL"),
            "jenkinsCause": env.get("BUILD_CAUSE") or env.get("ROOT_BUILD_CAUSE"), "host": env.get("NODE_NAME") or os.environ.get("COMPUTERNAME")}


def _version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


@dataclass
class DeployRecord:
    project: str
    component: str
    target: str
    host: str
    trigger: str  # manual | nightly | cli | rollback
    dry_run: bool = False
    started: str = field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))
    finished: str | None = None
    outcome: Outcome | None = None
    message: str = ""
    artifact: ArtifactRef | None = None
    previous: Installed | None = None
    installed: Installed | None = None
    steps: list[Step] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)  # board commands a dry run would execute
    recovery: list[str] = field(default_factory=list)  # manual commands after ROLLBACK_FAILED
    actor: dict[str, str | None] = field(default_factory=actor)
    insecure_tls: bool = False
    log: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": RECORD_SCHEMA,
            "project": self.project,
            "component": self.component,
            "target": self.target,
            "host": self.host,
            "trigger": self.trigger,
            "dryRun": self.dry_run,
            "started": self.started,
            "finished": self.finished,
            "outcome": self.outcome.value if self.outcome else None,
            "message": self.message,
            "artifact": self.artifact.as_dict() if self.artifact else None,
            "previous": self.previous.as_dict() if self.previous else None,
            "installed": self.installed.as_dict() if self.installed else None,
            "steps": [vars(s) for s in self.steps],
            "drift": self.drift,
            "planned": self.planned,
            "recovery": self.recovery,
            "actor": self.actor,
            "insecureTls": self.insecure_tls,
            "framework": {"grf": _version("general-robot-framework")},
        }

    def write(self, out_dir: Path, audit_log: str | None = None) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        data = self.as_dict()
        path = out_dir / RECORD_FILE
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        (out_dir / "deploy.log").write_text("\n".join(self.log) + "\n", encoding="utf-8")
        if audit_log:
            audit = Path(audit_log)
            audit.parent.mkdir(parents=True, exist_ok=True)
            with audit.open("a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        return path


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    """Deployment records for the report (a folder means its ``deployment.json`` files, newest last)."""
    records = []
    for path in paths:
        files = sorted(path.rglob(RECORD_FILE)) if path.is_dir() else [path]
        for file in files:
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as err:
                raise ValueError(f"cannot read the deployment record {file}: {err}") from err
            if not isinstance(data, dict) or data.get("schema") != RECORD_SCHEMA:
                raise ValueError(f"{file} is not a GRF deployment record (schema {RECORD_SCHEMA})")
            records.append(data)
    return records
