"""Docker on the board: the ``docker run`` command of a recipe and the comparison of a running container's
configuration (``docker inspect``) with the recipe (configuration drift)."""

from __future__ import annotations

import shlex
from typing import Any

from grf.deploy.model import ContainerRecipe


def run_command(recipe: ContainerRecipe, image: str) -> str:
    """``docker run -d`` for ``image`` (``repository:tag``) with exactly the recipe's settings."""
    args = ["docker", "run", "-d", "--name", recipe.name, f"--net={recipe.network}", f"--restart={recipe.restart}"]
    if recipe.privileged:
        args.append("--privileged")
    if recipe.init:
        args.append("--init")
    for key, value in recipe.log_options:
        args += ["--log-opt", f"{key}={value}"]
    for mount in recipe.mounts:
        args += ["-v", mount.arg()]
    for key, value in recipe.env:
        args += ["-e", f"{key}={value}"]
    args += [*recipe.extra_args, image]
    return " ".join(shlex.quote(a) for a in args)


def drift(recipe: ContainerRecipe, inspect: dict[str, Any], image: str | None = None) -> list[str]:
    """Differences between the recipe and a container's ``docker inspect`` object (empty: as expected)."""
    host = inspect.get("HostConfig") or {}
    config = inspect.get("Config") or {}
    found = []

    def differs(what: str, expected: Any, actual: Any) -> None:
        if expected != actual:
            found.append(f"{what}: expected {expected!r}, found {actual!r}")

    if image:
        differs("image", image, config.get("Image"))
    differs("network", recipe.network, host.get("NetworkMode"))
    differs("privileged", recipe.privileged, bool(host.get("Privileged")))
    differs("init", recipe.init, bool(host.get("Init")))
    differs("restart policy", recipe.restart, (host.get("RestartPolicy") or {}).get("Name"))
    log = (host.get("LogConfig") or {}).get("Config") or {}
    for key, value in recipe.log_options:
        differs(f"log option {key}", value, log.get(key))
    mounts = {(m.get("Source"), m.get("Destination")) for m in inspect.get("Mounts") or []}
    for mount in recipe.mounts:
        if (mount.source, mount.target) not in mounts:
            found.append(f"mount {mount.source} -> {mount.target} missing")
    expected_targets = {m.target for m in recipe.mounts}
    for source, destination in sorted(mounts, key=str):
        if destination not in expected_targets:
            found.append(f"unexpected mount {source} -> {destination}")
    env = set(config.get("Env") or [])
    for key, value in recipe.env:
        if f"{key}={value}" not in env:
            found.append(f"environment {key} is not {value!r}")
    return found


def tag_of(image: str | None) -> str | None:
    if not image or ":" not in image.rsplit("/", 1)[-1]:
        return None
    return image.rsplit(":", 1)[1]
