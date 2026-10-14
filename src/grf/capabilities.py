"""Capability-based test selection.

Tests declare required capabilities through tags configured in ``[capabilities]``. Tiers provide their static
capabilities through ``[tiers.*]``; site rig-control and operator modes may add capabilities. The framework
excludes only tests explicitly tagged for a missing capability. Products must lint undeclared requirements.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from grf import project
from grf.rigcontrol import RigControl


def __getattr__(name: str) -> Any:
    # module constants of the current project (kept for callers of the pre-project API)
    if name == "TAG_CAPABILITY":
        return project.current().tag_capability
    if name == "TIERS":
        return tuple(project.current().tiers)
    raise AttributeError(name)


def tier_capabilities(tier: str, operator: str = "none", env: Mapping[str, str] | None = None,
                      rig_control: RigControl | None = None) -> tuple[set[str], list[str]]:
    """(capabilities, where they come from). On the rig the site script is asked (RigControlError if it fails)."""
    p = project.current()
    t = p.tier(tier)
    caps = set(t.capabilities)
    notes = [f"{tier} tier: {', '.join(sorted(caps)) or 'nothing extra'}"]
    if t.rig_control:
        control = rig_control or RigControl.from_env(env)
        if control is None:
            missing = sorted({*p.rig_targets.values(), p.rig_log_capability})
            notes.append(f"no rig control ({p.env('RIG_CONTROL')} not set): no {', '.join(missing)}")
        else:
            got = control.test_capabilities()
            caps |= got
            notes.append(f"rig control '{control}': {', '.join(sorted(got)) or 'nothing'}")
    if operator != "none":
        caps |= p.operator_capabilities
        notes.append(f"operator ({operator}): {', '.join(sorted(p.operator_capabilities))}")
    return caps, notes


def excluded_tags(caps: set[str]) -> list[str]:
    """The capability tags whose capability is missing (in the project's ``[capabilities]`` order)."""
    return [tag for tag, cap in project.current().tag_capability.items() if cap not in caps]
