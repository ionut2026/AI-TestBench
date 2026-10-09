"""The project a framework run is for: its names, paths, tiers and rules, read from the project's ``grf.toml``.

The generic framework (pipeline, lint, report, capabilities, rig control, doctor, CLI) knows nothing about a product;
everything product-specific comes from here or from the project's plugin (``[project] plugin = "module:attr"``, a
``ProductPlugin``). ``current()`` finds the project: the ``GRF_PROJECT`` environment variable (a ``grf.toml`` file
or its folder), else the default root. ``smm-auto run`` sets ``GRF_PROJECT`` for the Robot processes it starts.
"""

from __future__ import annotations

import importlib
import os
import re
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from smm_automation.plugin import ProductPlugin

CONFIG_FILE = "grf.toml"
PROJECT_ENV = "GRF_PROJECT"


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class Tier:
    name: str
    capabilities: frozenset[str]
    evidence: bool = True  # verdicts say something about the product (False: a mock of it)
    parallel: bool = False  # tests may run in parallel workers (pabot)
    rig_control: bool = False  # capabilities also come from the site's rig control


@dataclass(frozen=True)
class TagRule:
    """Lint rule: a test that uses ``keywords`` unconditionally carries ``tag`` (or one of ``also``); a test tagged
    ``tag`` uses one of ``satisfied_by``."""

    rule: str
    tag: str
    also: frozenset[str]
    keywords: frozenset[str]
    satisfied_by: frozenset[str]
    what: str


def norm_keyword(name: str) -> str:
    """Robot keyword name normalisation (case, spaces and underscores ignored; ``Library.`` prefix dropped)."""
    name = name.split(".")[-1] if "." in name and " " not in name.split(".")[0] else name
    return re.sub(r"[\s_]", "", name).lower()


@dataclass
class Project:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> Project:
        path = Path(path)
        file = path / CONFIG_FILE if path.is_dir() else path
        try:
            with open(file, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as err:
            raise ProjectError(f"cannot read the project configuration {file}: {err}") from err
        return cls(file.resolve().parent, data)

    # ------------------------------------------------------------------ names

    def _section(self, name: str) -> dict[str, Any]:
        section = self.data.get(name) or {}
        if not isinstance(section, dict):
            raise ProjectError(f"[{name}] in {CONFIG_FILE} must be a table")
        return section

    def _project(self, key: str, default: str) -> str:
        return str(self._section("project").get(key) or default)

    @property
    def name(self) -> str:
        return self._project("name", self.root.name)

    @property
    def cli(self) -> str:
        return self._project("cli", "grf")

    @property
    def product(self) -> str:
        """What the tests test (used in report and doctor texts)."""
        return self._project("product", self.name)

    @property
    def spec_prefix(self) -> str:
        """Tag prefix of a specification id: ``<prefix>-<id>``."""
        return self._project("spec_prefix", "SPEC")

    @property
    def rule_prefix(self) -> str:
        """Prefix of the lint rule codes (``<prefix>01``)."""
        return self._project("rule_prefix", "GRF")

    @property
    def env_prefix(self) -> str:
        return self._project("env_prefix", "GRF")

    def env(self, name: str) -> str:
        """The project's name of a framework environment variable, e.g. ``env("RIG_CONTROL")`` -> ``SMM_RIG_CONTROL``."""
        return f"{self.env_prefix}_{name}"

    def spec_tag(self, spec_id: int | str) -> str:
        return f"{self.spec_prefix}-{spec_id}"

    @cached_property
    def spec_tag_re(self) -> re.Pattern[str]:
        return re.compile(rf"^{re.escape(self.spec_prefix)}-(\d+)$", re.I)

    def rule(self, number: int) -> str:
        return f"{self.rule_prefix}{number:02d}"

    # ------------------------------------------------------------------ paths

    def path(self, key: str, default: str | None = None) -> Path:
        value = self._section("paths").get(key, default)
        if not isinstance(value, str) or not value:
            raise ProjectError(f"[paths] {key} is not set in {self.root / CONFIG_FILE}")
        return self.root / value

    def paths(self, key: str, default: list[str] | None = None) -> list[Path]:
        value = self._section("paths").get(key, default)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not value:
            raise ProjectError(f"[paths] {key} is not set in {self.root / CONFIG_FILE}")
        return [self.root / v for v in value]

    def raw_paths(self, key: str, default: list[str] | None = None) -> list[str]:
        """Path entries as written (module names stay module names)."""
        value = self._section("paths").get(key, default or [])
        return [value] if isinstance(value, str) else list(value)

    # ------------------------------------------------------------------ tiers and capabilities

    @cached_property
    def tiers(self) -> dict[str, Tier]:
        out = {}
        for name, t in self._section("tiers").items():
            out[name] = Tier(name, frozenset(t.get("capabilities", [])), bool(t.get("evidence", True)),
                             bool(t.get("parallel", False)), bool(t.get("rig_control", False)))
        if not out:
            raise ProjectError(f"no [tiers] in {self.root / CONFIG_FILE}")
        return out

    def tier(self, name: str) -> Tier:
        if name not in self.tiers:
            raise ValueError(f"Unknown tier '{name}' ({', '.join(self.tiers)})")
        return self.tiers[name]

    def is_evidence(self, tier: str) -> bool:
        """Whether verdicts of ``tier`` are evidence about the product (unknown tiers count as evidence)."""
        t = self.tiers.get(tier) or next((x for n, x in self.tiers.items() if tier.startswith(n)), None)
        return t.evidence if t else True

    @property
    def tag_capability(self) -> dict[str, str]:
        """Capability tag (``needs:twin``) -> capability (``twin``), in the configured order."""
        return {str(k): str(v) for k, v in self._section("capabilities").items()}

    @property
    def operator_capabilities(self) -> frozenset[str]:
        return frozenset(self._section("operator").get("capabilities", ["operator"]))

    @property
    def rig_targets(self) -> dict[str, str]:
        """Rig control restart targets -> the capability they give (``appsmm`` -> ``restart``)."""
        return {str(k): str(v) for k, v in (self._section("rig").get("targets") or {}).items()}

    @property
    def rig_ready_ports(self) -> dict[str, int]:
        """Default readiness port of a restart target (the ssh rig control waits until it accepts connections)."""
        return {str(k): int(v) for k, v in (self._section("rig").get("ready_ports") or {}).items()}

    @property
    def rig_log_capability(self) -> str:
        return str(self._section("rig").get("log_capability", "applog"))

    @property
    def rig_log_files(self) -> str:
        """Glob of the log files ``fetch-log`` must deliver."""
        return str(self._section("rig").get("log_files", "*"))

    @property
    def rig_log_pattern(self) -> str:
        """Default remote file pattern of the ssh rig control's ``[log]``."""
        return str(self._section("rig").get("log_pattern", "*.log"))

    # ------------------------------------------------------------------ rules

    @cached_property
    def lint_tag_rules(self) -> tuple[TagRule, ...]:
        rules = []
        for r in self._section("lint").get("tag_rules", []):
            keywords = frozenset(norm_keyword(k) for k in r.get("keywords", []))
            satisfied = frozenset(norm_keyword(k) for k in r.get("satisfied_by", [])) or keywords
            rules.append(TagRule(self.rule(int(r["rule"])), str(r["tag"]), frozenset(r.get("also", [])), keywords,
                                 satisfied, str(r.get("what", f"needs {r['tag']}"))))
        return tuple(rules)

    @property
    def message_pattern(self) -> re.Pattern[str] | None:
        pattern = self._section("catalog").get("message_pattern")
        return re.compile(pattern) if pattern else None

    @property
    def mutation_faults_env(self) -> str:
        return str(self._section("mutation").get("faults_env") or self.env("MOCK_FAULTS"))

    def text(self, key: str, default: str = "") -> str:
        """Free texts the generic modules show (``[texts]``)."""
        return str(self._section("texts").get(key, default))

    # ------------------------------------------------------------------ plugin

    @cached_property
    def plugin(self) -> ProductPlugin:
        from smm_automation.plugin import ProductPlugin

        ref = self._section("project").get("plugin")
        if not ref:
            return ProductPlugin(self)
        module, _, attr = str(ref).partition(":")
        try:
            obj = getattr(importlib.import_module(module), attr or "plugin")
        except (ImportError, AttributeError) as err:
            raise ProjectError(f"cannot load the project plugin {ref!r}: {err}") from err
        plugin = obj(self) if isinstance(obj, type) else obj
        if not isinstance(plugin, ProductPlugin):
            raise ProjectError(f"the project plugin {ref!r} is not a ProductPlugin")
        return plugin


_current: Project | None = None


def default_root() -> Path:
    from smm_automation import FRAMEWORK_ROOT

    return FRAMEWORK_ROOT


def current() -> Project:
    """The project of this process (``GRF_PROJECT``, else the default root), loaded once."""
    global _current
    if _current is None:
        _current = Project.load(os.environ.get(PROJECT_ENV) or default_root())
    return _current


def use(project: Project | None) -> None:
    """Makes ``project`` the current one (None: load again on the next ``current()``)."""
    global _current
    _current = project
