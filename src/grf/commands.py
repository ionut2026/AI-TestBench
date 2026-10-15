"""Public command-local services for core and product CLI extensions."""

from __future__ import annotations

import argparse
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from grf.project import Project, using

if TYPE_CHECKING:
    from grf.pipeline.drift import TestRef


@dataclass(frozen=True)
class CommandContext:
    project: Project

    def path(self, args: argparse.Namespace, key: str, option: str | None = None) -> Path:
        override = getattr(args, option or key, None)
        return Path(override) if override else self.project.path(key)

    def scope_path(self, args: argparse.Namespace) -> Path:
        return self.path(args, "scope")

    def catalog_path(self, args: argparse.Namespace) -> Path:
        override = getattr(args, "catalog", None)
        if override:
            return Path(override)
        with self.scope_path(args).open("rb") as stream:
            name = tomllib.load(stream).get("name", "pilot")
        if not isinstance(name, str) or not name.strip() or Path(name).name != name or name in (".", ".."):
            raise ValueError("scope name must be a nonempty filename stem")
        return self.project.path("catalog_dir") / f"{name}.json"

    def suites(self, args: argparse.Namespace) -> list[Path]:
        sources = getattr(args, "suites", None)
        return [Path(source) for source in sources] if sources else self.project.paths("suites")

    def reviews_path(self, args: argparse.Namespace) -> Path:
        return self.path(args, "reviews")

    def load_catalog(self, args: argparse.Namespace) -> dict:
        from grf.pipeline.ingest import load_catalog

        return load_catalog(self.catalog_path(args))

    def collect_tests(self, args: argparse.Namespace) -> list[TestRef]:
        from grf.pipeline.drift import collect_tests

        with using(self.project):
            return collect_tests(self.suites(args))
