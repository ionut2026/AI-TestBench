"""Test doubles for grf.deploy: a simulated board (files, containers, images, lock) behind the Transport protocol.

Nothing here talks to a network, a Jenkins server or an instrument. Passing tests with these fakes say nothing
about a real board: they check GRF's own step machine.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shlex
from pathlib import Path
from typing import Any

from grf.deploy.config import SiteConfig, Target
from grf.deploy.model import Component, ContainerRecipe, DeployError, Mount
from grf.deploy.transport import CommandResult, Redactor, _checked

WD = "/home/root"
PREV = f"{WD}/previous_builds"
REPO = "vendor/app/arm"


def component(**overrides: Any) -> Component:
    recipe = ContainerRecipe(image=REPO, name="app", log_options=(("max-size", "10m"),), mounts=(Mount("/data", "/data"),))
    values: dict[str, Any] = dict(
        name="app", description="synthetic test component", jenkins_job="view/X/job/App",
        artifact_pattern=r"^app_[\d.]+\.tar\.gz$", recipe=recipe,
        version_from_filename=r"app_(?P<version>[\d.]+)\.tar\.gz", requires_containers=("mosquitto",),
        stable_s=5, ready_timeout_s=30, stop_timeout_s=10)
    values.update(overrides)
    return Component(**values)


def target(**overrides: Any) -> Target:
    values: dict[str, Any] = dict(name="board", host="10.0.0.1", user="root", known_hosts="known_hosts", workdir=WD)
    values.update(overrides)
    return Target(**values)


def site(**overrides: Any) -> SiteConfig:
    values: dict[str, Any] = dict(path=Path("site.toml"), jenkins=None, targets={"board": target()},
                                  components={"app": "board"}, retention=2)
    values.update(overrides)
    return SiteConfig(**values)


def inspect_of(recipe: ContainerRecipe, image: str, image_id: str, status: str = "running", restarts: int = 0) -> dict:
    return {
        "Image": image_id, "RestartCount": restarts, "State": {"Status": status},
        "Config": {"Image": image, "Env": [f"{k}={v}" for k, v in recipe.env]},
        "HostConfig": {"NetworkMode": recipe.network, "Privileged": recipe.privileged, "Init": recipe.init,
                       "RestartPolicy": {"Name": recipe.restart}, "LogConfig": {"Config": dict(recipe.log_options)}},
        "Mounts": [{"Source": m.source, "Destination": m.target} for m in recipe.mounts],
    }


def tar_name(version: str) -> str:
    return f"app_{version}.tar.gz"


def image_id(version: str) -> str:
    return "sha256:" + hashlib.sha256(version.encode()).hexdigest()


class FakeBoard:
    """A board as the engine sees it through ssh. ``fail`` injects failures: a list of (regex, result-or-exception,
    times) checked against every command before it is executed."""

    def __init__(self, recipe: ContainerRecipe, secret: str | None = None):
        self.host = "10.0.0.1"
        self.recipe = recipe
        self.redact = Redactor([secret] if secret else [])
        self.files: dict[str, dict[str, Any]] = {}  # path -> {"sha": .., "size": .., "mtime": .., "text": ..}
        self.containers: dict[str, dict] = {}
        self.images: list[tuple[str, str]] = []  # (repo:tag, id), newest first
        self.tar_images: dict[str, tuple[str, str]] = {}  # tarball name -> (repo:tag, id) docker load reports
        self.commands: list[str] = []
        self.fail: list[list[Any]] = []
        self.free_mb, self.load, self.lock_age = 5000, 0.4, 10
        self.clock = 0
        self.closed = False
        self.after_run: Any = None  # called with the container dict after `docker run`

    # ------------------------------------------------------------- setup helpers

    def add_file(self, path: str, data: bytes | None = None, text: str | None = None) -> None:
        self.clock += 1
        content = data if data is not None else (text or path).encode()
        self.files[path] = {"sha": hashlib.sha256(content).hexdigest(), "size": len(content), "mtime": self.clock,
                            "text": text}

    def add_build(self, version: str, running: bool = True, in_workdir: bool = True) -> None:
        """A build that GRF (or someone) deployed earlier: tarball, loaded image, optional running container."""
        image = f"{REPO}:{version}"
        self.tar_images[tar_name(version)] = (image, image_id(version))
        self.images.insert(0, (image, image_id(version)))
        if in_workdir:
            self.add_file(f"{WD}/{tar_name(version)}", data=version.encode())
        if running:
            self.containers[self.recipe.name] = inspect_of(self.recipe, image, image_id(version))

    def add_companion(self, name: str = "mosquitto", status: str = "running") -> None:
        self.containers[name] = {"Image": "sha256:mq", "RestartCount": 0, "State": {"Status": status},
                                 "Config": {"Image": "eclipse-mosquitto"}}

    def running_image_id(self) -> str | None:
        c = self.containers.get(self.recipe.name)
        return c["Image"] if c and c["State"]["Status"] == "running" else None

    def names_in(self, folder: str) -> list[str]:
        return sorted(p.rsplit("/", 1)[1] for p in self.files if p.rsplit("/", 1)[0] == folder)

    def marker(self) -> dict:
        entry = self.files.get(f"{WD}/.grf-deploy/app.json")
        return json.loads(entry["text"]) if entry else {}

    def mutating(self) -> list[str]:
        keep = ("test ", "cat ", "docker version", "df ", "docker inspect", "ls ", "sha256sum", "wc ", "echo ",
                "docker image inspect", "docker images")
        return [c for c in self.commands if not c.startswith(keep)]

    # ------------------------------------------------------------- Transport protocol

    def run(self, command: str, timeout_s: float = 120, check: bool = True) -> CommandResult:
        self.commands.append(command)
        for rule in self.fail:
            pattern, outcome, times = rule
            if times and re.search(pattern, command):
                rule[2] = times - 1
                if isinstance(outcome, BaseException):
                    raise outcome
                return _checked(outcome, command, check, self.redact)
        return _checked(self._execute(command), command, check, self.redact)

    def put(self, local: Path, remote: str, timeout_s: float = 1800) -> None:
        self.commands.append(f"PUT {local.name} {remote}")
        for rule in self.fail:
            pattern, outcome, times = rule
            if times and re.search(pattern, f"PUT {remote}"):
                rule[2] = times - 1
                raise outcome
        self.add_file(remote, data=local.read_bytes())

    def close(self) -> None:
        self.closed = True

    # ------------------------------------------------------------- command simulation

    def _execute(self, command: str) -> CommandResult:  # noqa: C901 - a small shell
        ok = CommandResult(0, "", "")
        no = CommandResult(1, "", "no such file")
        if command == "cat /proc/loadavg":
            return CommandResult(0, f"{self.load} 0.3 0.2 1/100 999\n", "")
        if command.startswith("docker version"):
            return CommandResult(0, "24.0.5\n", "")
        if command.startswith("df -Pm"):
            return CommandResult(0, f"Filesystem 1M-blocks Used Available Capacity Mounted\n/dev/root 9000 1 {self.free_mb} 1% /\n", "")
        if command.startswith("echo $(( $(date +%s)"):
            return CommandResult(0, f"{self.lock_age}\n", "")
        if command.startswith("printf '%s' ") or (command.startswith("mkdir -p ") and "&& printf" in command):
            body = command.split("&& ", 1)[1] if command.startswith("mkdir -p ") else command
            first, _, rest = body.partition(" && ")
            parts = shlex.split(first.replace(" > ", " GREATER "))
            text, path = parts[2], parts[-1]
            self.add_file(path, text=text)
            if rest.startswith("date +%s > "):
                self.add_file(rest.split(" > ", 1)[1], text="0")
            return ok
        if command.endswith(" 2>/dev/null || true") and command.startswith("ls -1t "):
            folder = shlex.split(command.split(" 2>", 1)[0])[2]
            names = [(e["mtime"], p.rsplit("/", 1)[1]) for p, e in self.files.items() if p.rsplit("/", 1)[0] == folder]
            return CommandResult(0, "".join(f"{n}\n" for _, n in sorted(names, reverse=True)), "")
        if command.startswith("wc -c < "):
            entry = self.files.get(shlex.split(command[len("wc -c < "):])[0])
            return CommandResult(0, f"{entry['size']}\n", "") if entry else no
        argv = shlex.split(command)
        head = argv[:2]
        if argv[0] == "test":
            path = argv[2]
            exists = path in self.files or any(p.startswith(path.rstrip("/") + "/") for p in self.files)
            return ok if exists else no
        if argv[0] == "cat":
            entry = self.files.get(argv[1])
            return CommandResult(0, entry["text"] or "", "") if entry else no
        if argv[0] == "mkdir":
            if argv[1] == "-p":
                return ok
            if any(p == argv[1] or p.startswith(argv[1] + "/") for p in self.files):
                return CommandResult(1, "", "File exists")
            self.add_file(argv[1] + "/.dir", text="")
            return ok
        if argv[:2] == ["rm", "-rf"]:
            path = argv[-1]
            self.files = {p: e for p, e in self.files.items() if p != path and not p.startswith(path + "/")}
            return ok
        if argv[:2] == ["rm", "-f"]:
            for path in argv[2:]:
                self.files.pop(path, None)
            return ok
        if argv[:2] == ["mv", "-f"]:
            src, dst = argv[2], argv[3]
            if src not in self.files:
                return no
            if dst.endswith("/"):
                dst = dst + src.rsplit("/", 1)[1]
            self.files[dst] = self.files.pop(src)
            return ok
        if argv[0] == "sha256sum":
            entry = self.files.get(argv[1])
            return CommandResult(0, f"{entry['sha']}  {argv[1]}\n", "") if entry else no
        if head == ["docker", "inspect"]:
            info = self.containers.get(argv[-1])
            return CommandResult(0, json.dumps([info]), "") if info else CommandResult(1, "", "No such container")
        if head == ["docker", "stop"]:
            return self._container(argv[-1], lambda c: c["State"].update(Status="exited"))
        if head == ["docker", "start"]:
            return self._container(argv[-1], lambda c: c["State"].update(Status="running"))
        if head == ["docker", "rename"]:
            if argv[2] not in self.containers or argv[3] in self.containers:
                return CommandResult(1, "", "rename failed")
            self.containers[argv[3]] = self.containers.pop(argv[2])
            return ok
        if head == ["docker", "rm"]:
            name = argv[-1]
            if name not in self.containers:
                return CommandResult(1, "", "No such container")
            if "-f" not in argv and self.containers[name]["State"]["Status"] == "running":
                return CommandResult(1, "", "container is running")
            del self.containers[name]
            return ok
        if head == ["docker", "load"]:
            name = argv[-1].rsplit("/", 1)[1]
            if argv[-1] not in self.files or name not in self.tar_images:
                return CommandResult(1, "", "open: no such file")
            image, iid = self.tar_images[name]
            self.images = [(i, d) for i, d in self.images if i != image]
            self.images.insert(0, (image, iid))
            return CommandResult(0, f"Loaded image: {image}\n", "")
        if head == ["docker", "image"] and argv[2] == "inspect":
            found = [d for i, d in self.images if i == argv[-1]]
            return CommandResult(0, found[0] + "\n", "") if found else no
        if head == ["docker", "image"] and argv[2] == "prune":
            return ok
        if head == ["docker", "images"]:
            repo = argv[-1]
            return CommandResult(0, "".join(f"{d}\n" for i, d in self.images if i.rsplit(":", 1)[0] == repo), "")
        if head == ["docker", "rmi"]:
            iid = argv[-1]
            if any(c["Image"] == iid for c in self.containers.values()):
                return CommandResult(1, "", "image is in use")
            self.images = [(i, d) for i, d in self.images if d != iid]
            return ok
        if head == ["docker", "run"]:
            name = argv[argv.index("--name") + 1]
            if name in self.containers:
                return CommandResult(125, "", "Conflict. The container name is already in use")
            image = argv[-1]
            iid = next(d for i, d in self.images if i == image)
            self.containers[name] = inspect_of(self.recipe, image, iid)
            if self.after_run:
                self.after_run(self.containers[name])
            return CommandResult(0, "0123456789ab\n", "")
        raise AssertionError(f"the fake board does not know the command: {command}")

    def _container(self, name: str, change: Any) -> CommandResult:
        if name not in self.containers:
            return CommandResult(1, "", "No such container")
        change(self.containers[name])
        return CommandResult(0, name + "\n", "")

    def snapshot(self) -> dict:
        return copy.deepcopy({"files": self.files, "containers": self.containers, "images": self.images})


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


__all__ = ["DeployError", "FakeBoard", "Clock", "component", "target", "site", "inspect_of", "tar_name", "image_id",
           "WD", "PREV", "REPO"]
