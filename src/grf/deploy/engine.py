"""The deployment step machine (product-independent; the product supplies ``Component`` definitions).

Steps of ``deploy run`` for one component on one board (docs/DEPLOYMENT.md explains each):

1. resolve the artifact (Jenkins build or ``--local-tar``) and download it with size and SHA-256 check;
2. connect (host key checked) and run the read-only preflight: hold file, Docker, free space, load, companion
   containers, current container, a leftover of an unfinished deployment;
3. dry run (``deploy plan``) stops here and lists the board commands it would run;
4. skip with ``UNCHANGED`` when the board already runs exactly this artifact (unless ``--force``);
5. lock the board; move the component's current tarball to ``previous_builds``; upload, verify the SHA-256 on the
   board;
6. stop the running container and rename it ``<name>_grf_previous`` (the rollback point);
7. ``docker load``, ``docker run`` from the recipe, health check, verify image and configuration;
8. commit: remove the old container, keep ``retention`` earlier images and tarballs, prune dangling images, write the
   board marker; unlock.

A failure in 6-7 (or Ctrl+C / Jenkins abort) rolls back automatically to the renamed container: ``ROLLED_BACK``.
If that is impossible: ``ROLLBACK_FAILED`` with the manual recovery commands. Failures before 6 never touch the
running container.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from grf.deploy import docker, lock
from grf.deploy.config import SiteConfig, Target
from grf.deploy.jenkins import Jenkins, local_artifact
from grf.deploy.model import ArtifactRef, Component, DeployError, Installed, Outcome, Step
from grf.deploy.record import DeployRecord
from grf.deploy.transport import Redactor, Transport, open_transport

MARKERS = ".grf-deploy"
PREVIOUS = "previous_builds"
PREVIOUS_SUFFIX = "_grf_previous"
_SHA = re.compile(r"^[0-9a-f]{64}$")


def q(value: str) -> str:
    return shlex.quote(value)


@dataclass
class Options:
    build: str = "lastSuccessfulBuild"
    branch: str | None = None
    local_tar: Path | None = None
    dry_run: bool = False
    force: bool = False
    ignore_hold: bool = False
    break_stale_lock: bool = False
    restore: bool = False  # `deploy rollback`: redeploy the newest tarball in previous_builds
    sha256: str | None = None  # expected downloaded bytes, checked before connecting to the board


class Deployment:
    def __init__(self, project: str, component: Component, site: SiteConfig, target: Target, *, work_dir: Path,
                 trigger: str = "cli", env: Mapping[str, str] | None = None,
                 transport_factory: Callable[[Target, Mapping[str, str]], Transport] = open_transport,
                 jenkins_factory: Callable[..., Jenkins] = Jenkins, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic, echo: Callable[[str], None] = print):
        self.project, self.component, self.site, self.target = project, component, site, target
        self.recipe = component.recipe
        self.env = os.environ if env is None else env
        self.work_dir = work_dir
        self.transport_factory, self.jenkins_factory = transport_factory, jenkins_factory
        self.sleep, self.monotonic, self.echo = sleep, monotonic, echo
        self.t: Transport | None = None
        self.redact = Redactor()
        self.record = DeployRecord(project=project, component=component.name, target=target.name, host=target.host,
                                   trigger=trigger)
        wd = target.workdir.rstrip("/")
        self.wd, self.prev_dir, self.marker = wd, f"{wd}/{PREVIOUS}", f"{wd}/{MARKERS}/{component.name}.json"
        self.name, self.prev_name = self.recipe.name, self.recipe.name + PREVIOUS_SUFFIX

    # ------------------------------------------------------------------ helpers

    def log(self, text: str) -> None:
        text = self.redact(text)
        self.record.log.append(f"{dt.datetime.now(dt.UTC).strftime('%H:%M:%S')} {text}")
        self.echo(text)

    @contextmanager
    def step(self, name: str) -> Iterator[Step]:
        current = Step(name, "OK")
        self.record.steps.append(current)
        start = self.monotonic()
        try:
            yield current
        except BaseException as err:
            current.status = "FAIL"
            current.detail = current.detail or self.redact(str(err) or type(err).__name__)
            raise
        finally:
            current.seconds = round(self.monotonic() - start, 2)
            self.log(f"[{current.status}] {name}" + (f": {current.detail}" if current.detail else ""))

    def run(self, command: str, timeout_s: float = 120, check: bool = True) -> str:
        assert self.t is not None
        return self.t.run(command, timeout_s=timeout_s, check=check).stdout

    def ok(self, command: str, timeout_s: float = 120) -> bool:
        assert self.t is not None
        return self.t.run(command, timeout_s=timeout_s, check=False).returncode == 0

    def inspect(self, name: str) -> dict[str, Any] | None:
        assert self.t is not None
        result = self.t.run(f"docker inspect --type container {q(name)}", check=False)
        if result.returncode != 0:
            return None
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise DeployError(f"docker inspect {name} returned no JSON") from None
        return data[0] if isinstance(data, list) and data else None

    def state(self, name: str) -> tuple[str, int, str] | None:
        info = self.inspect(name)
        if info is None:
            return None
        st = info.get("State") or {}
        return str(st.get("Status")), int(info.get("RestartCount") or 0), str(info.get("Image") or "")

    def read_marker(self) -> dict[str, Any]:
        assert self.t is not None
        result = self.t.run(f"cat {q(self.marker)}", check=False)
        try:
            data = json.loads(result.stdout) if result.returncode == 0 else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def installed(self, info: dict[str, Any] | None, marker: dict[str, Any]) -> Installed:
        if info is None:
            return Installed(container=self.name, status="missing")
        image = (info.get("Config") or {}).get("Image")
        image_id = info.get("Image")
        mine = bool(marker) and marker.get("imageId") == image_id
        return Installed(
            container=self.name, status=str((info.get("State") or {}).get("Status")), image=image, image_id=image_id,
            version=(marker.get("version") if mine else None) or docker.tag_of(image),
            sha256=marker.get("sha256") if mine else None, restarts=int(info.get("RestartCount") or 0),
            extra={"deployedBy": marker.get("deployedBy"), "deployedAt": marker.get("deployedAt"),
                   "buildUrl": marker.get("buildUrl"), "file": marker.get("file")} if mine else {},
        )

    def connect(self) -> None:
        with self.step(f"connect to {self.target.name} ({self.target.user}@{self.target.host})"):
            self.t = self.transport_factory(self.target, self.env)
            self.redact = self.t.redact

    def tarballs(self, folder: str) -> list[str]:
        """Component tarballs in a board folder, newest first."""
        out = self.run(f"ls -1t {q(folder)} 2>/dev/null || true")
        return [n.strip() for n in out.splitlines() if n.strip() and self.component.matches(n.strip())]

    # ------------------------------------------------------------------ public

    def status(self) -> dict[str, Any]:
        """What runs on the board for the component (read-only)."""
        try:
            self.connect()
            info = self.inspect(self.name)
            marker = self.read_marker()
            current = self.installed(info, marker)
            return {
                "component": self.component.name, "target": self.target.name, "host": self.target.host,
                "installed": current.as_dict(),
                "drift": docker.drift(self.recipe, info) if info else [],
                "unfinishedDeployment": self.inspect(self.prev_name) is not None,
                "previousBuilds": self.tarballs(self.prev_dir),
                "hold": lock.hold_reason(self.t, self.wd) if self.t else None,
                "lock": lock.lock_info(self.t, self.wd) if self.t else None,
            }
        finally:
            if self.t:
                self.t.close()

    def deploy(self, options: Options) -> DeployRecord:
        record = self.record
        if options.restore:
            record.trigger = "rollback"
        record.dry_run = options.dry_run
        locked = False
        owner = self.lock_owner()
        try:
            if options.sha256 is not None:
                if not isinstance(options.sha256, str) or not _SHA.fullmatch(options.sha256):
                    raise DeployError("sha256 must be 64 lowercase hex characters", Outcome.ARTIFACT_FAILED)
                if options.restore:
                    raise DeployError("sha256 cannot pin a board-selected rollback", Outcome.ARTIFACT_FAILED)
            if not options.restore:
                record.artifact = self.resolve(options)
            self.connect()
            if options.restore:
                record.artifact = self.resolve_previous()
            artifact = record.artifact
            assert artifact is not None
            info = self.preflight(artifact, options)
            marker = self.read_marker()
            record.previous = self.installed(info, marker)
            if options.dry_run:
                record.planned = self.plan(artifact, info)
                record.outcome, record.message = Outcome.DRY_RUN, "dry run: nothing was changed on the board"
                return record
            if not options.force and not options.restore and self.unchanged(artifact, info, marker):
                record.installed = record.previous
                record.outcome = Outcome.UNCHANGED
                record.message = f"{self.target.name} already runs {artifact.file_name}; nothing was changed (--force redeploys)"
                self.log(record.message)
                return record
            with self.step("lock the board"):
                assert self.t is not None
                lock.acquire(self.t, self.wd, owner, self.site.lock_ttl_s, options.break_stale_lock, self.log)
                locked = True
            self.change(artifact, info, options)
        except DeployError as err:
            record.outcome, record.message = err.outcome, self.redact(str(err))
            self.log(f"{record.outcome.value}: {record.message}")
        except KeyboardInterrupt:
            record.outcome = Outcome.FAILED
            record.message = ("aborted during clean-up; the new build runs but clean-up may be incomplete (check `deploy status`)"
                              if record.installed and not options.dry_run else
                              "aborted before the running container was touched")
            self.log(f"{record.outcome.value}: {record.message}")
        except Exception as err:  # unexpected: report, never hide
            record.outcome, record.message = Outcome.FAILED, self.redact(f"{type(err).__name__}: {err}")
            self.log(f"{record.outcome.value}: {record.message}")
        finally:
            if locked and self.t is not None:
                try:
                    lock.release(self.t, self.wd, owner["token"])
                except Exception as err:  # noqa: BLE001 - the record must still be written
                    self.log(f"WARNING: could not release the board lock: {err}")
            if self.t is not None:
                self.t.close()
            record.finished = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        return record

    # ------------------------------------------------------------------ steps 1-4

    def resolve(self, options: Options) -> ArtifactRef:
        with self.step("resolve and download the artifact") as s:
            if options.local_tar:
                ref = local_artifact(self.component, options.local_tar)
            else:
                if self.site.jenkins is None:
                    raise DeployError(f"{self.site.path} has no [jenkins] section (or pass --local-tar)", Outcome.ARTIFACT_FAILED)
                if self.site.jenkins.insecure_tls:
                    self.record.insecure_tls = True
                    self.log("WARNING: [jenkins] insecure_tls = true: the Jenkins TLS certificate is NOT verified")
                client = self.jenkins_factory(self.site.jenkins, self.env)
                ref = client.download(client.resolve(self.component, options.build, options.branch),
                                      self.work_dir / "artifacts")
            if options.sha256 is not None and ref.sha256 != options.sha256:
                raise DeployError(f"artifact sha256 {ref.sha256} does not match expected {options.sha256}",
                                  Outcome.ARTIFACT_FAILED)
            s.detail = f"{ref.file_name} ({ref.size} bytes, sha256 {ref.sha256})" + (
                f", Jenkins build {ref.build_number}" if ref.build_number else ", local file")
            return ref

    def resolve_previous(self) -> ArtifactRef:
        with self.step("find the retained previous build") as s:
            names = self.tarballs(self.prev_dir)
            if not names:
                raise DeployError(f"no retained {self.component.name} build in {self.prev_dir}", Outcome.ARTIFACT_FAILED)
            path = f"{self.prev_dir}/{names[0]}"
            sha = self.run(f"sha256sum {q(path)}", timeout_s=600).split()[0]
            size = self.run(f"wc -c < {q(path)}").strip()
            s.detail = names[0]
            return ArtifactRef(component=self.component.name, source="board", file_name=names[0],
                               version=self.component.version_of(names[0]), sha256=sha,
                               size=int(size) if size.isdigit() else None, path=path)

    def preflight(self, artifact: ArtifactRef, options: Options) -> dict[str, Any] | None:
        assert self.t is not None
        with self.step("preflight (read-only)") as s:
            reason = lock.hold_reason(self.t, self.wd)
            if reason and not options.ignore_hold:
                raise DeployError(f"the board owner put a hold on {self.target.name} ({lock.hold_path(self.wd)}): {reason}",
                                  Outcome.HELD)
            if reason:
                self.log(f"WARNING: hold ignored (--ignore-hold): {reason}")
            notes = []
            try:
                notes.append("docker " + self.run("docker version --format '{{.Server.Version}}'").strip())
                free = int(self.run(f"df -Pm {q(self.wd)}").strip().splitlines()[-1].split()[3])
                load = float(self.run("cat /proc/loadavg").split()[0])
            except (IndexError, ValueError) as err:
                raise DeployError(f"unexpected board answer during preflight: {err}", Outcome.PREFLIGHT_FAILED) from None
            except DeployError as err:
                raise DeployError(str(err), Outcome.PREFLIGHT_FAILED) from None
            needed = self.site.min_free_mb + (0 if artifact.source == "board" else (artifact.size or 0) // (1 << 20))
            if free < needed:
                raise DeployError(f"only {free} MB free in {self.wd} on {self.target.name}, {needed} MB needed",
                                  Outcome.PREFLIGHT_FAILED)
            if load > self.site.max_load:
                raise DeployError(f"board load {load} is above {self.site.max_load}; try again later", Outcome.PREFLIGHT_FAILED)
            notes += [f"{free} MB free", f"load {load}"]
            for companion in self.component.requires_containers:
                st = self.state(companion)
                if not st or st[0] != "running":
                    s.status = "WARN"
                    notes.append(f"companion {companion} is {st[0] if st else 'missing'}")
            if self.inspect(self.prev_name) is not None:
                raise DeployError(
                    f"container {self.prev_name} exists: an earlier deployment did not finish. Check `deploy status`; "
                    f"remove it (docker rm {self.prev_name}) or rename it back (docker rename {self.prev_name} {self.name}) "
                    "before deploying", Outcome.PREFLIGHT_FAILED)
            info = self.inspect(self.name)
            notes.append(f"{self.name}: " + ((info.get("State") or {}).get("Status", "?") if info else "not installed"))
            s.detail = ", ".join(notes)
            return info

    def unchanged(self, artifact: ArtifactRef, info: dict[str, Any] | None, marker: dict[str, Any]) -> bool:
        if not info or (info.get("State") or {}).get("Status") != "running":
            return False
        if marker.get("sha256") != artifact.sha256 or marker.get("imageId") != info.get("Image"):
            return False
        return not docker.drift(self.recipe, info)

    def plan(self, artifact: ArtifactRef, info: dict[str, Any] | None) -> list[str]:
        tar = f"{self.wd}/{artifact.file_name}"
        image = f"{self.recipe.image}:{artifact.version or '<tag from docker load>'}"
        steps = [f"mkdir {lock.lock_path(self.wd)}   # lock",
                 f"mkdir -p {self.prev_dir}; mv {self.wd}/<current {self.component.name} tarball> {self.prev_dir}/"]
        if artifact.source == "board":
            steps.append(f"mv {artifact.path} {tar}")
        else:
            steps += [f"upload {artifact.file_name} -> {tar}.partial", f"sha256sum {tar}.partial  # must be {artifact.sha256}",
                      f"mv {tar}.partial {tar}"]
        if info:
            steps += [f"docker stop -t {self.component.stop_timeout_s} {self.name}", f"docker rename {self.name} {self.prev_name}"]
        steps += [f"docker load -i {tar}", docker.run_command(self.recipe, image),
                  f"health: running and stable for {self.component.stable_s:g} s"
                  + (f", `{self.component.health_command}`" if self.component.health_command else "")
                  + (f", port {self.component.health_port}" if self.component.health_port else ""),
                  "verify image and configuration"]
        if info:
            steps.append(f"docker rm {self.prev_name}")
        steps += [f"keep {self.site.retention} earlier {self.recipe.image} images and tarballs; docker image prune -f",
                  f"write {self.marker}; rm -r {lock.lock_path(self.wd)}"]
        for line in steps:
            self.log(f"  would run: {line}")
        return steps

    def lock_owner(self) -> dict[str, Any]:
        return {"user": self.record.actor.get("user"), "jenkinsRun": self.record.actor.get("jenkinsRun"),
                "component": self.component.name, "trigger": self.record.trigger, "token": uuid.uuid4().hex,
                "started": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}

    # ------------------------------------------------------------------ steps 5-8

    def change(self, artifact: ArtifactRef, info: dict[str, Any] | None, options: Options) -> None:
        tar = f"{self.wd}/{artifact.file_name}"
        moved: list[str] = []  # tarballs moved from workdir to previous_builds
        staged = False  # the artifact file is in place in workdir
        # rollback state
        self.stopped = self.renamed = self.started = False
        self.had_previous = info is not None
        try:
            with self.step("keep the current tarball in previous_builds") as s:
                self.run(f"mkdir -p {q(self.prev_dir)}")
                for name in self.tarballs(self.wd):
                    if artifact.source == "board" and name == artifact.file_name:
                        continue
                    self.run(f"mv -f {q(self.wd + '/' + name)} {q(self.prev_dir + '/')}")
                    moved.append(name)
                s.detail = ", ".join(moved) or "none"
            with self.step("put the new tarball on the board") as s:
                if artifact.source == "board":
                    self.run(f"mv -f {q(str(artifact.path))} {q(tar)}")
                else:
                    assert self.t is not None and artifact.path
                    self.t.put(Path(artifact.path), tar + ".partial")
                    staged = True
                    found = self.run(f"sha256sum {q(tar + '.partial')}", timeout_s=900).split()
                    if not found or found[0] != artifact.sha256:
                        raise DeployError(f"checksum on the board differs ({found[0] if found else '?'} != {artifact.sha256}); "
                                          "upload corrupted", Outcome.FAILED)
                    self.run(f"mv -f {q(tar + '.partial')} {q(tar)}")
                staged = True
                s.detail = tar
        except BaseException as err:
            self.restore_files(artifact, tar, moved, staged)
            if isinstance(err, KeyboardInterrupt):
                raise DeployError("aborted before the running container was touched", Outcome.FAILED) from None
            raise
        try:
            loaded = self.replace(tar, artifact)
        except BaseException as err:  # includes Ctrl+C and Jenkins abort
            reason = "aborted (Ctrl+C or Jenkins abort)" if isinstance(err, KeyboardInterrupt) else self.redact(str(err))
            self.rollback(reason, artifact, tar, moved)
        self.commit(artifact, loaded, options)

    def restore_files(self, artifact: ArtifactRef, tar: str, moved: list[str], staged: bool) -> None:
        """Puts the tarballs back where they were before this deployment (best effort, logged)."""
        try:
            if artifact.source == "board":
                if staged:
                    self.run(f"mv -f {q(tar)} {q(str(artifact.path))}", check=False)
            else:
                self.run(f"rm -f {q(tar)} {q(tar + '.partial')}", check=False)
            for name in moved:
                self.run(f"mv -f {q(self.prev_dir + '/' + name)} {q(self.wd + '/')}", check=False)
        except Exception as err:  # noqa: BLE001
            self.log(f"WARNING: could not restore the tarballs: {err}")

    def replace(self, tar: str, artifact: ArtifactRef) -> str:
        if self.had_previous:
            with self.step(f"stop {self.name} and keep it as {self.prev_name}"):
                self.stopped = True
                self.run(f"docker stop -t {int(self.component.stop_timeout_s)} {q(self.name)}",
                         timeout_s=self.component.stop_timeout_s + 60)
                self.run(f"docker rename {q(self.name)} {q(self.prev_name)}")
                self.renamed = True
        with self.step("docker load") as s:
            out = self.run(f"docker load -i {q(tar)}", timeout_s=1800)
            images = re.findall(r"Loaded image:\s*(\S+)", out)
            loaded = next((i for i in images if i.rsplit(":", 1)[0] == self.recipe.image), None)
            if not loaded:
                raise DeployError(f"the tarball does not contain the image {self.recipe.image} (docker load: "
                                  f"{', '.join(images) or out.strip()[:200]})")
            if artifact.version and docker.tag_of(loaded) != artifact.version:
                raise DeployError(f"artifact version {artifact.version} contradicts loaded image {loaded}")
            s.detail = loaded
        image_id = self.run(f"docker image inspect --format '{{{{.Id}}}}' {q(loaded)}").strip()
        with self.step(f"docker run {self.name}"):
            self.started = True
            self.run(docker.run_command(self.recipe, loaded), timeout_s=300)
        with self.step("health check") as s:
            s.detail = self.health(fresh=True)
        with self.step("verify image and configuration") as s:
            info = self.inspect(self.name) or {}
            if info.get("Image") != image_id:
                raise DeployError(f"{self.name} runs image {info.get('Image')}, not the loaded {image_id}")
            found = docker.drift(self.recipe, info, image=loaded)
            self.record.drift = found
            if found and self.component.drift == "fail":
                raise DeployError("configuration differs from the recipe: " + "; ".join(found))
            if found:
                s.status = "WARN"
            self.record.installed = Installed(
                container=self.name, status="running", image=loaded, image_id=image_id,
                version=docker.tag_of(loaded), sha256=artifact.sha256,
                restarts=int(info.get("RestartCount") or 0))
            s.detail = f"{loaded}, version {self.record.installed.version}" + (f"; drift: {'; '.join(found)}" if found else "")
        return loaded

    def health(self, fresh: bool) -> str:
        c = self.component
        deadline = self.monotonic() + c.ready_timeout_s
        while True:
            st = self.state(self.name)
            if st and st[0] == "running":
                break
            if self.monotonic() > deadline or (st and st[0] in ("exited", "dead")):
                raise DeployError(f"{self.name} is {st[0] if st else 'missing'}, not running")
            self.sleep(2)
        assert st is not None
        restarts = st[1]
        if fresh and restarts:
            raise DeployError(f"{self.name} restarted {restarts} times while starting")
        self.sleep(c.stable_s)
        again = self.state(self.name)
        if not again or again[0] != "running" or again[1] != restarts:
            raise DeployError(f"{self.name} did not stay up for {c.stable_s:g} s "
                              f"({again[0] if again else 'missing'}, {again[1] if again else '?'} restarts)")
        checks = [f"running {c.stable_s:g} s without restart"]
        if c.health_command:
            self.wait_until(c.health_command, deadline, f"health command `{c.health_command}`")
            checks.append("health command OK")
        if c.health_port:
            self.wait_until(f"(ss -ltn 2>/dev/null || netstat -ltn) | grep -Eq ':{int(c.health_port)}([^0-9]|$)'",
                            deadline, f"port {c.health_port}")
            checks.append(f"port {c.health_port} listening")
        return ", ".join(checks)

    def wait_until(self, command: str, deadline: float, what: str) -> None:
        while not self.ok(command, timeout_s=60):
            if self.monotonic() > deadline:
                raise DeployError(f"{what} did not succeed within {self.component.ready_timeout_s:g} s")
            self.sleep(3)

    def rollback(self, reason: str, artifact: ArtifactRef, tar: str, moved: list[str]) -> NoReturn:
        self.log(f"deployment failed ({reason}); rolling back")
        try:
            with self.step("rollback") as s:
                if self.started or self.renamed:
                    self.run(f"docker rm -f {q(self.name)}", check=False)
                if self.renamed:
                    self.run(f"docker rename {q(self.prev_name)} {q(self.name)}")
                if self.stopped:
                    self.run(f"docker start {q(self.name)}")
                    s.detail = f"previous {self.name} restarted: " + self.health(fresh=False)
                self.restore_files(artifact, tar, moved, staged=True)
                if not self.had_previous:
                    raise DeployError("there was no previous container to return to")
        except BaseException as err:
            self.record.recovery = self.recovery_commands()
            raise DeployError(f"ROLLBACK FAILED, the instrument needs manual attention. Deployment failed: {reason}. "
                              f"Rollback failed: {self.redact(str(err))}. Recovery: see 'recovery' in the record.",
                              Outcome.ROLLBACK_FAILED) from None
        self.record.installed = self.record.previous
        raise DeployError(f"deployment failed ({reason}); the previous {self.name} runs again", Outcome.ROLLED_BACK)

    def recovery_commands(self) -> list[str]:
        return [f"ssh {self.target.user}@{self.target.host}",
                f"docker ps -a --filter name={self.name}",
                f"docker rm -f {self.name}  # only the failed new container",
                f"docker rename {self.prev_name} {self.name} && docker start {self.name}  # if {self.prev_name} exists",
                f"ls -t {self.prev_dir}  # retained tarballs; or: <cli> deploy rollback --component {self.component.name} "
                f"--target {self.target.name}",
                f"docker load -i {self.prev_dir}/<tarball> and docker run as in the recipe (docs/DEPLOYMENT.md)"]

    def commit(self, artifact: ArtifactRef, loaded: str, options: Options) -> None:
        """The new build runs: from here on, problems are warnings (the board is in the intended state)."""
        with self.step("clean up and record on the board") as s:
            notes = []
            keep = self.site.retention
            running_id = (self.record.installed.image_id if self.record.installed else None) or ""
            marker = {"component": self.component.name, "file": artifact.file_name, "sha256": artifact.sha256,
                      "version": self.record.installed.version if self.record.installed else None,
                      "image": loaded, "imageId": running_id, "buildUrl": artifact.build_url,
                      "deployedBy": self.record.actor.get("user"),
                      "deployedAt": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
            def write_marker() -> None:
                self.run(f"mkdir -p {q(self.wd + '/' + MARKERS)} && printf '%s' {q(json.dumps(marker))} > {q(self.marker)}")

            def remove_previous() -> None:
                if self.renamed:
                    self.run(f"docker rm {q(self.prev_name)}")

            def prune() -> None:
                self.run("docker image prune -f", timeout_s=600)

            def remove_tarballs() -> None:
                for name in self.tarballs(self.prev_dir)[keep:]:
                    self.run(f"rm -f {q(self.prev_dir + '/' + name)}")

            actions: list[tuple[str, Callable[[], str | None]]] = [
                ("write the board marker", write_marker), (f"remove {self.prev_name}", remove_previous),
                ("remove old images", lambda: self.remove_old_images(running_id, keep)),
                ("prune dangling images", prune), ("remove old tarballs", remove_tarballs)]
            for what, action in actions:
                try:
                    note = action()
                    if note:
                        notes.append(note)
                except DeployError as err:
                    s.status = "WARN"
                    notes.append(f"could not {what}: {err}")
            s.detail = "; ".join(notes) or f"kept {keep} earlier build(s)"
        self.record.outcome = Outcome.RESTORED if options.restore else Outcome.DEPLOYED
        self.record.message = f"{self.target.name} runs {loaded} ({artifact.file_name})"
        self.log(f"{self.record.outcome.value}: {self.record.message}")

    def remove_old_images(self, running_id: str, keep: int) -> str | None:
        seen: list[str] = []
        for line in self.run(f"docker images --no-trunc --format '{{{{.ID}}}}' {q(self.recipe.image)}").splitlines():
            if line.strip() and line.strip() not in seen:
                seen.append(line.strip())
        old = [i for i in seen if i != running_id][keep:]
        failed = [i for i in old if not self.ok(f"docker rmi {q(i)}")]
        if failed:
            raise DeployError(f"docker rmi refused {len(failed)} image(s)")
        return f"removed {len(old)} old image(s), kept {keep} earlier build(s)" if old else None
