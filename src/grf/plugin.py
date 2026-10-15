"""What a product adds to the generic framework: the hooks a project's plugin (``[project] plugin`` in ``grf.toml``)
overrides. The defaults do nothing, so a project without a plugin gets the plain framework."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grf.deploy.model import Component
    from grf.doctor import Doctor
    from grf.project import Project

Command = Callable[[argparse.Namespace], int]


class ProductPlugin:
    def __init__(self, project: Project):
        from grf.commands import CommandContext

        self.project = project
        self.context = CommandContext(project)

    def known_messages(self) -> set[str] | None:
        """Message names the catalog may mention (None: every match of ``[catalog] message_pattern`` counts)."""
        return None

    def brief_sections(self, spec: dict) -> list[str]:
        """Extra Markdown lines of a generation brief, after the Explorative Tests (e.g. interface schemas)."""
        return []

    def doctor_class(self) -> type[Doctor]:
        from grf.doctor import Doctor

        return Doctor

    def add_commands(self, sub: argparse._SubParsersAction) -> dict[str, Command]:
        """Adds product commands to the CLI; returns command name -> handler."""
        return {}

    def deploy_components(self) -> list[Component]:
        """What ``<cli> deploy`` can put on the product's boards (docs/DEPLOYMENT.md). Default: nothing."""
        return []

    def runtime_identity(self, meta: dict[str, str]) -> dict[str, str]:
        """Component -> version that the product reported during a run, read from the Robot run metadata
        (``<cli> gate``). Default: nothing (a gate that needs runtime identity fails closed)."""
        return {}

    def identity_matches(self, component: str, expected: str, observed: str) -> bool:
        """Legacy runtime comparator; override ``runtime_matches`` in new plugins."""
        return expected.strip() == observed.strip()

    def installed_matches(self, component: str, expected: str, installed: str) -> bool:
        """Compare requested and installed build identities, not runtime protocol versions."""
        return expected == installed

    def runtime_matches(self, component: str, expected: str, observed: str) -> bool:
        """Compare an installed build with a fresh product observation.

        Delegates to the legacy hook during migration. Installed comparisons never use it.
        """
        return self.identity_matches(component, expected, observed)
