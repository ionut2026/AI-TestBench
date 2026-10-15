"""The deployment record: one JSON file per deployment (``deployment.json`` in its output folder) that says who
deployed which build where, with checksum, provenance, every step and the outcome. ``<cli> run --deployment`` shows
it in the traceability report; ``audit_log`` in the site config also appends it as one line to a site audit file."""

from __future__ import annotations

import datetime as dt
import getpass
import json
import os
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from grf.deploy.model import ArtifactRef, Installed, Outcome, Step
from grf.validation import array, integer, number, table, text

RECORD_SCHEMA = 1
RECORD_FILE = "deployment.json"


def validate_record(data: Any) -> dict[str, Any]:
    """Validate structural record integrity; binding/gate apply their own outcome policies."""
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != RECORD_SCHEMA:
        raise ValueError(f"not a GRF deployment record (schema {RECORD_SCHEMA})")
    for name in ("project", "component", "target"):
        text(data.get(name), f"deployment {name}")
    for name in ("host", "trigger", "started", "finished", "recordId", "message"):
        if data.get(name) is not None and not isinstance(data[name], str):
            raise ValueError(f"deployment {name} must be text or null")
    for name in ("dryRun", "insecureTls"):
        if name in data and type(data[name]) is not bool:
            raise ValueError(f"deployment {name} must be a boolean")
    outcome = data.get("outcome")
    if outcome is not None and (not isinstance(outcome, str) or outcome not in {v.value for v in Outcome}):
        raise ValueError("deployment outcome is not recognized")
    for name in ("artifact", "previous", "installed"):
        if data.get(name) is None:
            continue
        item = table(data[name], f"deployment {name} (running component)" if name == "installed" else f"deployment {name}")
        strings = ("component", "source", "file_name", "version", "sha256", "path", "url", "job_url", "branch",
                   "build_url", "build_result", "build_timestamp", "revision", "container", "status", "image", "image_id")
        for key in strings:
            if item.get(key) is not None and not isinstance(item[key], str):
                raise ValueError(f"deployment {name} {key} must be text or null")
        for key in ("size", "build_number", "restarts"):
            if item.get(key) is not None:
                integer(item[key], f"deployment {name}.{key}")
        if "extra" in item:
            table(item["extra"], f"deployment {name}.extra")
    for index, step in enumerate(array(data.get("steps", []), "deployment.steps")):
        where = f"deployment.steps[{index}]"
        table(step, where)
        text(step.get("name"), f"{where}.name")
        if step.get("status") not in ("IN_PROGRESS", "OK", "WARN", "FAIL", "SKIP"):
            raise ValueError(f"{where}.status is not recognized")
        number(step.get("seconds", 0), f"{where}.seconds")
        if "detail" in step and not isinstance(step["detail"], str):
            raise ValueError(f"{where}.detail must be text")
    for name in ("drift", "planned", "recovery"):
        if any(not isinstance(v, str) for v in array(data.get(name, []), f"deployment.{name}")):
            raise ValueError(f"deployment.{name} must contain strings")
    for name in ("actor", "framework"):
        if name in data:
            item = table(data[name], f"deployment.{name}")
            if any(value is not None and not isinstance(value, str) for value in item.values()):
                raise ValueError(f"deployment.{name} values must be text or null")
    return data


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
    record_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _output: Path | None = field(default=None, init=False, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": RECORD_SCHEMA,
            "recordId": self.record_id,
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

    def reserve(self, out_dir: Path) -> None:
        """Exclusively reserve evidence output before any connection or board action."""
        out_dir.mkdir(parents=True, exist_ok=True)
        if (out_dir / "deploy.log").exists():
            raise FileExistsError(f"deployment output already has a deploy.log: {out_dir}")
        path = out_dir / RECORD_FILE
        with path.open("x", encoding="utf-8") as f:
            json.dump(self.as_dict(), f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            descriptor = os.open(out_dir, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        self._output = out_dir.resolve()

    def write(self, out_dir: Path, audit_log: str | None = None) -> Path:
        if self._output is None:
            self.reserve(out_dir)
        if self._output != out_dir.resolve():
            raise ValueError("a deployment record cannot change its reserved output directory")
        data = self.as_dict()
        path = out_dir / RECORD_FILE
        # Replace a complete, flushed snapshot, never truncate the previous checkpoint.
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out_dir,
                                             prefix=".deployment-", suffix=".tmp", delete=False) as f:
                temporary = Path(f.name)
                json.dump(data, f, indent=1, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            for attempt in range(6):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError as err:
                    if getattr(err, "winerror", None) not in (5, 32, 33) or attempt == 5:
                        raise
                    time.sleep(0.01 * 2**attempt)
            if os.name != "nt":
                descriptor = os.open(out_dir, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
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
                data = validate_record(json.loads(file.read_text(encoding="utf-8")))
            except (OSError, ValueError) as err:
                raise ValueError(f"cannot read the deployment record {file}: {err}") from err
            records.append(data)
    return records
