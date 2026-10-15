"""What can be deployed (``Component`` with its ``ContainerRecipe``), what was resolved (``ArtifactRef``) and how a
deployment ended (``Outcome`` and its exit code)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from grf.validation import integer, number


class Outcome(StrEnum):
    DEPLOYED = "DEPLOYED"  # the new build runs and passed health, version and configuration checks
    UNCHANGED = "UNCHANGED"  # the same artifact already runs; nothing was touched
    DRY_RUN = "DRY_RUN"  # resolved, downloaded, checked and planned; nothing was changed on the board
    ROLLED_BACK = "ROLLED_BACK"  # the board runs the previous build again after a failed deployment
    RESTORED = "RESTORED"  # `deploy rollback`: the retained previous build runs
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"  # the board was not ready; nothing was changed
    ARTIFACT_FAILED = "ARTIFACT_FAILED"  # the build or its file was not usable; nothing was changed
    ROLLBACK_FAILED = "ROLLBACK_FAILED"  # the new build failed and the previous one could not be restored
    LOCKED = "LOCKED"  # someone else is deploying to this board
    HELD = "HELD"  # the owner put a hold file on the board
    FAILED = "FAILED"  # failed before the running container was touched (e.g. ssh)


EXIT_CODES = {
    Outcome.DEPLOYED: 0,
    Outcome.UNCHANGED: 0,
    Outcome.DRY_RUN: 0,
    Outcome.RESTORED: 0,
    Outcome.PREFLIGHT_FAILED: 3,
    Outcome.FAILED: 3,
    Outcome.ARTIFACT_FAILED: 4,
    Outcome.ROLLED_BACK: 5,
    Outcome.ROLLBACK_FAILED: 6,
    Outcome.LOCKED: 7,
    Outcome.HELD: 8,
}


class DeployError(RuntimeError):
    def __init__(self, message: str, outcome: Outcome = Outcome.FAILED):
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class Mount:
    source: str  # path on the board
    target: str  # path in the container
    readonly: bool = False

    def arg(self) -> str:
        return f"{self.source}:{self.target}" + (":ro" if self.readonly else "")


@dataclass(frozen=True)
class ContainerRecipe:
    """The exact ``docker run`` settings of a component."""

    image: str  # repository without tag, e.g. vendor/app/arm
    name: str  # container name
    network: str = "host"
    privileged: bool = False
    init: bool = False
    restart: str = "unless-stopped"
    log_options: tuple[tuple[str, str], ...] = ()
    mounts: tuple[Mount, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    extra_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", self.image):
            raise ValueError(f"invalid image repository {self.image!r}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.name):
            raise ValueError(f"invalid container name {self.name!r}")


@dataclass(frozen=True)
class Component:
    """One deployable application of a product."""

    name: str  # CLI name, e.g. appsmm
    description: str
    jenkins_job: str  # path below the Jenkins base URL, e.g. view/SMM/job/SMMApplication
    artifact_pattern: str  # regex over the artifact file name; exactly one artifact of a build must match
    recipe: ContainerRecipe
    # regex with a group "version" over the file name; None: the version is the tag of the loaded image
    version_from_filename: str | None = None
    health_command: str | None = None  # board command that exits 0 once the component works
    health_port: int | None = None  # TCP port on the board that must accept connections
    requires_containers: tuple[str, ...] = ()  # companions that must run (checked, never changed)
    drift: str = "fail"  # fail | warn: a running configuration that differs from the recipe
    stable_s: float = 30.0  # how long the new container must keep running without a restart
    ready_timeout_s: float = 180.0
    stop_timeout_s: int = 30

    def __post_init__(self) -> None:
        number(self.stable_s, "stable_s")
        number(self.ready_timeout_s, "ready_timeout_s", positive=True)
        integer(self.stop_timeout_s, "stop_timeout_s")
        if self.stable_s > self.ready_timeout_s:
            raise ValueError("stable_s must not exceed ready_timeout_s")
        if self.health_port is not None:
            integer(self.health_port, "health_port", minimum=1, maximum=65535)
        if not re.fullmatch(r"[a-z][a-z0-9-]*", self.name):
            raise ValueError(f"invalid component name {self.name!r}")
        if self.drift not in ("fail", "warn"):
            raise ValueError("drift must be 'fail' or 'warn'")
        re.compile(self.artifact_pattern)
        if self.version_from_filename and "(?P<version>" not in self.version_from_filename:
            raise ValueError("version_from_filename needs a (?P<version>...) group")

    def matches(self, file_name: str) -> bool:
        return re.search(self.artifact_pattern, file_name) is not None

    def version_of(self, file_name: str) -> str | None:
        if not self.version_from_filename:
            return None
        match = re.search(self.version_from_filename, file_name)
        return match.group("version") if match else None


@dataclass
class ArtifactRef:
    component: str
    source: str  # jenkins | local | board
    file_name: str
    version: str | None = None
    size: int | None = None
    sha256: str | None = None
    path: str | None = None  # local file after download, or the board path for source "board"
    url: str | None = None
    job_url: str | None = None
    branch: str | None = None
    build_number: int | None = None
    build_url: str | None = None
    build_result: str | None = None
    build_timestamp: str | None = None
    revision: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Step:
    name: str
    status: str  # OK | WARN | FAIL | SKIP
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Installed:
    """What runs on the board for a component."""

    container: str
    status: str  # running | exited | ... | missing
    image: str | None = None
    image_id: str | None = None
    version: str | None = None
    sha256: str | None = None  # of the tarball GRF deployed last (from the board's marker file)
    restarts: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
