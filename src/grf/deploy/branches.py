"""Shared per-component Jenkins branch selection for deployment commands."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from grf.deploy.model import Component, DeployError


def resolve_branches(components: Sequence[Component], *, default_branch: str | None = None,
                     configured: Mapping[str, str] | None = None,
                     overrides: Sequence[str] | str | None = None) -> dict[str, str | None]:
    """Resolve CLI overrides, then ``[deploy.branches]``, then the global default.

    A sole unqualified CLI value retains the legacy all-component override. Qualified
    values are repeatable; mixing the two forms is refused rather than guessing.
    """
    names = {component.name for component in components}
    result: dict[str, str | None] = dict.fromkeys(names, default_branch)
    for name, branch in (configured or {}).items():
        if name not in names:
            raise DeployError(f"unknown component {name!r} in [deploy.branches]")
        if not isinstance(branch, str) or not branch.strip():
            raise DeployError(f"branch for {name!r} must be nonempty")
        result[name] = branch
    values = [overrides] if isinstance(overrides, str) else list(overrides or [])
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise DeployError("--branch requires a nonempty branch or COMPONENT=BRANCH")
        if "=" not in value:
            if len(values) != 1:
                raise DeployError("legacy --branch BRANCH must be used alone; use repeatable COMPONENT=BRANCH")
            return dict.fromkeys(names, value)
        name, branch = value.split("=", 1)
        if not name.strip() or not branch.strip():
            raise DeployError("--branch requires nonempty COMPONENT=BRANCH")
        if name not in names:
            raise DeployError(f"unknown component {name!r} in --branch")
        if name in seen:
            raise DeployError(f"duplicate --branch for component {name!r}")
        seen.add(name)
        result[name] = branch
    return result
