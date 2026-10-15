"""Fail-closed product extension contracts; no product transport or instrument actions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from grf.validation import text

if TYPE_CHECKING:
    from grf.plugin import ProductPlugin

PREFLIGHT = ("provenance", "actual-target")
STARTUP = ("startup", "provenance", "actual-target")
FINAL = ("completeness", "cleanup")
RESULT = (*STARTUP, *FINAL)
PREFLIGHT_METADATA = "GRF Preflight"


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceCheck:
    name: str
    passed: bool
    detail: str

    def __post_init__(self) -> None:
        text(self.name, "evidence check name")
        text(self.detail, "evidence check detail")
        if type(self.passed) is not bool:
            raise ValueError("evidence check passed must be an exact boolean")


@dataclass(frozen=True)
class EvidenceChecks:
    checks: tuple[EvidenceCheck, ...]
    identities: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.checks, tuple) or any(not isinstance(c, EvidenceCheck) for c in self.checks):
            raise ValueError("evidence checks must be a tuple of EvidenceCheck")
        if len({c.name for c in self.checks}) != len(self.checks):
            raise ValueError("evidence check names must be unique")
        if not isinstance(self.identities, Mapping):
            raise ValueError("evidence identities must be a mapping")
        for key, value in self.identities.items():
            text(key, "evidence identity name")
            text(value, f"evidence identity {key}")
        object.__setattr__(self, "identities", MappingProxyType(dict(self.identities)))

    def problems(self, required: Sequence[str]) -> list[str]:
        checks = {c.name: c for c in self.checks}
        problems = [f"{name}: mandatory evidence check unavailable" for name in required if name not in checks]
        problems += [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]
        if not self.identities:
            problems.append("validated evidence identities are unavailable")
        return problems

    def as_dict(self) -> dict:
        return {"schema": 1, "checks": [asdict(c) for c in self.checks], "identities": dict(self.identities)}

    @classmethod
    def from_dict(cls, data: object) -> EvidenceChecks:
        if not isinstance(data, dict) or set(data) != {"schema", "checks", "identities"} \
                or type(data["schema"]) is not int or data["schema"] != 1 \
                or not isinstance(data["checks"], list):
            raise ValueError("evidence must contain schema=1, checks and identities")
        checks = []
        for item in data["checks"]:
            if not isinstance(item, dict) or set(item) != {"name", "passed", "detail"}:
                raise ValueError("malformed evidence check")
            checks.append(EvidenceCheck(**item))
        return cls(tuple(checks), data["identities"])


def require_checks(value: EvidenceChecks | None, required: Sequence[str]) -> EvidenceChecks:
    """Use at a product's mandatory setup/teardown boundary; unavailable is never success."""
    if value is None:
        raise EvidenceError("product evidence validation is unavailable; implement the ProductPlugin evidence hooks")
    if not isinstance(value, EvidenceChecks):
        raise EvidenceError("product evidence validation must return EvidenceChecks")
    if problems := value.problems(required):
        raise EvidenceError("invalid product evidence: " + "; ".join(problems))
    return value


@dataclass(frozen=True)
class AssertionCoverage:
    required: tuple[str, ...]
    observed: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("required", "observed"):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise ValueError(f"assertion {name} must be a tuple")
            for value in values:
                text(value, f"assertion {name} identity")
            if len(set(values)) != len(values):
                raise ValueError(f"assertion {name} identities must be unique")

    def problems(self) -> list[str]:
        if not self.required:
            return ["required assertion clauses have not been declared"]
        missing = sorted(set(self.required) - set(self.observed))
        return ["required assertions not observed: " + ", ".join(missing)] if missing else []


@dataclass(frozen=True)
class EvidenceAssessment:
    problems: tuple[str, ...]
    coverage: Mapping[int, AssertionCoverage]

    def coverage_problems(self, specs: Sequence[int]) -> list[str]:
        return [f"specification {sid}: {problem}" for sid in sorted(set(specs))
                for problem in (self.coverage[sid].problems() if sid in self.coverage
                                else ["explicit assertion coverage unavailable"])]


def assess(plugin: ProductPlugin, tier: str, tests: list[dict], meta: dict) -> EvidenceAssessment:
    """Pure result validation: adapters inspect persisted evidence, never contact a product here."""
    problems = []
    preflight = None
    try:
        value = json.loads(meta.get(PREFLIGHT_METADATA, "null"))
        preflight = EvidenceChecks.from_dict(value)
        problems.extend(preflight.problems(PREFLIGHT))
    except (ValueError, TypeError) as err:
        problems.append(f"run preflight evidence unavailable or malformed: {err}")
    result = plugin.evidence_checks(tier, tests, meta)
    if result is None:
        problems.append("product startup/completeness/cleanup evidence is unavailable")
    elif not isinstance(result, EvidenceChecks):
        raise ValueError("evidence_checks must return EvidenceChecks or None")
    else:
        problems.extend(result.problems(RESULT))
        if preflight is not None:
            for key, expected in preflight.identities.items():
                if result.identities.get(key) != expected:
                    problems.append(f"evidence identity {key} differs from validated run preflight")
    coverage = plugin.assertion_coverage([test for test in tests if test["status"] == "PASS"], meta)
    if not isinstance(coverage, Mapping) or any(
        type(sid) is not int or sid <= 0 or not isinstance(value, AssertionCoverage) for sid, value in coverage.items()
    ):
        raise ValueError("assertion_coverage must map positive specification IDs to AssertionCoverage")
    return EvidenceAssessment(tuple(problems), coverage)
