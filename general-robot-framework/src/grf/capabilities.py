"""What a tier can do, and therefore which tagged tests ``<cli> run`` excludes (plan 6.2).

A test declares what it needs with a tag; a tier runs it only if it has the matching capability. Tags, tiers and
what they can do come from the project (``[capabilities]``, ``[tiers.*]``, ``[operator]`` in ``grf.toml``). For SMM:

    needs:twin               twin             the SMM hardware twin (fault injection, racks, COP trace)
    needs:hardware-action    hardware-action  a physical action such as the emergency stop (twin or operator)
    needs:operator           operator         an operator at the instrument (``Operator Action``)
    needs:applog             applog           appSMM's SmartInspect log files
    requires:restart         restart          killing and restarting appSMM
    requires:broker-restart  broker-restart   stopping and restarting the MQTT broker

mock and offline get theirs from the service; the rig gets ``restart``, ``broker-restart`` and ``applog`` from the
site's rig control script (``SMM_RIG_CONTROL``, see rigcontrol.py) and ``operator``/``hardware-action`` from
``--operator console|dialog``.
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
