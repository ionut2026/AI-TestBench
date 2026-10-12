"""What a product adds to the generic framework: the hooks a project's plugin (``[project] plugin`` in ``grf.toml``)
overrides. The defaults do nothing, so a project without a plugin gets the plain framework."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grf.doctor import Doctor
    from grf.project import Project

Command = Callable[[argparse.Namespace], int]


class ProductPlugin:
    def __init__(self, project: Project):
        self.project = project

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
