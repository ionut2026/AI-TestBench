"""What a tier can do, and therefore which tagged tests ``smm-auto run`` excludes (plan 6.2).

A test declares what it needs with a tag; a tier runs it only if it has the matching capability:

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

from smm_automation.rigcontrol import RigControl

TAG_CAPABILITY = {
    "needs:twin": "twin",
    "needs:hardware-action": "hardware-action",
    "needs:operator": "operator",
    "needs:applog": "applog",
    "requires:restart": "restart",
    "requires:broker-restart": "broker-restart",
}
TIER_CAPABILITIES: dict[str, frozenset[str]] = {
    "mock": frozenset({"restart", "broker-restart"}),
    "offline": frozenset({"restart", "broker-restart", "twin", "applog", "hardware-action"}),
    "rig": frozenset(),
}
TIERS = tuple(TIER_CAPABILITIES)


def tier_capabilities(tier: str, operator: str = "none", env: Mapping[str, str] | None = None,
                      rig_control: RigControl | None = None) -> tuple[set[str], list[str]]:
    """(capabilities, where they come from). On the rig the site script is asked (RigControlError if it fails)."""
    if tier not in TIER_CAPABILITIES:
        raise ValueError(f"Unknown tier '{tier}' ({', '.join(TIERS)})")
    caps = set(TIER_CAPABILITIES[tier])
    notes = [f"{tier} tier: {', '.join(sorted(caps)) or 'nothing extra'}"]
    if tier == "rig":
        control = rig_control or RigControl.from_env(env)
        if control is None:
            notes.append("no rig control (SMM_RIG_CONTROL not set): no appSMM/broker restart, no log files")
        else:
            got = control.test_capabilities()
            caps |= got
            notes.append(f"rig control '{control}': {', '.join(sorted(got)) or 'nothing'}")
    if operator != "none":
        caps |= {"operator", "hardware-action"}
        notes.append(f"operator ({operator}): operator, hardware-action")
    return caps, notes


def excluded_tags(caps: set[str]) -> list[str]:
    """The capability tags whose capability is missing (in TAG_CAPABILITY order)."""
    return [tag for tag, cap in TAG_CAPABILITY.items() if cap not in caps]
