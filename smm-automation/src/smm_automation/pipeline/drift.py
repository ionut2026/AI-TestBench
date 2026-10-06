"""Drift check: compares the Robot suites with the RV&S catalog.

* stale     - a test's ``spechash:`` differs from the current specification text (the spec changed in RV&S)
* orphan    - a test references ``SDS-<id>`` that is not in the catalog (removed or out of scope)
* untagged  - a test without any ``SDS-<id>`` tag (no traceability)
* nohash    - a test with ``SDS-<id>`` but no ``spechash:`` tag
* uncovered - a testable, not deferred specification without any test
* suspect   - a covered specification RV&S flags as suspect (Suspect Count > 0)
* pending   - tests still tagged ``review:pending``
* unrecorded - a test without ``review:pending`` but without a human review in the ledger ``catalog/reviews.toml``
  for its current ``spechash`` (the tag was removed without a recorded review, or the spec changed since)
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SDS_TAG = re.compile(r"^SDS-(\d+)$", re.I)
HASH_TAG = re.compile(r"^spechash:([0-9a-f]{6,64})$", re.I)
KNOWN_ISSUE_TAG = re.compile(r"^known-issue:(\S+)$", re.I)
PENDING_TAG = "review:pending"


@dataclass
class TestRef:
    __test__ = False  # not a pytest test class
    name: str
    suite: str
    source: str
    tags: list[str]
    specs: list[int] = field(default_factory=list)
    hash: str | None = None

    @property
    def pending(self) -> bool:
        return any(t.lower() == PENDING_TAG for t in self.tags)

    @property
    def review_key(self) -> str:
        """What a review is recorded against: the test's ``spechash:`` tag value(s)."""
        return ",".join(sorted(t.split(":", 1)[1].lower() for t in self.tags if t.lower().startswith("spechash:")))


def load_reviews(path: Path) -> list[dict]:
    """The review ledger: ``[[review]]`` tables with test, spechash, reviewer, date and ref (PR / commit)."""
    if not path.exists():
        return []
    with open(path, "rb") as f:
        return list(tomllib.load(f).get("review", []))


def is_human(entry: dict) -> bool:
    reviewer = str(entry.get("reviewer", "")).strip()
    return bool(reviewer) and not reviewer.lower().startswith("agent:")


def check_reviews(tests: list[TestRef], reviews: list[dict]) -> list[dict]:
    out = []
    for t in tests:
        if t.pending:
            continue
        mine = [r for r in reviews if r.get("test") == t.name and is_human(r)]
        if any(str(r.get("spechash", "")).lower() == t.review_key for r in mine):
            continue
        reason = "no human review recorded" if not mine else f"recorded review is for spechash {mine[-1].get('spechash')}, test has {t.review_key}"
        out.append({"test": t.name, "suite": t.suite, "reason": reason})
    return out


def collect_tests(paths: list[Path]) -> list[TestRef]:
    from robot.api import TestSuiteBuilder

    suite = TestSuiteBuilder(allow_empty_suite=True).build(*[str(p) for p in paths])
    out: list[TestRef] = []

    def walk(s):
        for t in s.tests:
            tags = [str(x) for x in t.tags]
            ref = TestRef(name=t.name, suite=s.longname, source=str(s.source), tags=tags)
            for tag in tags:
                if m := SDS_TAG.match(tag):
                    ref.specs.append(int(m[1]))
                elif m := HASH_TAG.match(tag):
                    ref.hash = m[1].lower()
            out.append(ref)
        for child in s.suites:
            walk(child)

    walk(suite)
    return out


def check(catalog: dict, tests: list[TestRef], reviews: list[dict] | None = None) -> dict:
    specs = catalog["specifications"]
    covered: dict[int, list[str]] = {}
    stale, orphan, untagged, nohash = [], [], [], []
    for t in tests:
        if not t.specs:
            untagged.append({"test": t.name, "suite": t.suite})
            continue
        for sid in t.specs:
            spec = specs.get(str(sid))
            if not spec:
                orphan.append({"test": t.name, "suite": t.suite, "spec": sid})
                continue
            covered.setdefault(sid, []).append(t.name)
            if not t.hash:
                nohash.append({"test": t.name, "spec": sid})
            elif len(t.specs) == 1 and not spec["hash"].startswith(t.hash):
                stale.append({"test": t.name, "suite": t.suite, "spec": sid, "testHash": t.hash, "specHash": spec["hash"][:8], "modified": spec.get("modified")})
    uncovered = [
        {"spec": int(k), "area": s["area"]}
        for k, s in specs.items()
        if s["testable"] and not s.get("deferredReason") and int(k) not in covered
    ]
    suspect = [{"spec": sid, "suspectCount": specs[str(sid)].get("suspectCount")} for sid in covered if (specs[str(sid)].get("suspectCount") or 0) > 0]
    return {
        "catalogGeneratedAt": catalog.get("generatedAt"),
        "tests": len(tests),
        "coveredSpecs": len(covered),
        "stale": stale,
        "orphan": orphan,
        "untagged": untagged,
        "nohash": nohash,
        "uncovered": uncovered,
        "suspect": suspect,
        "pending": [{"test": t.name, "suite": t.suite} for t in tests if t.pending],
        "unrecorded": check_reviews(tests, reviews) if reviews is not None else [],
        "deferred": [{"spec": int(k), "reason": s["deferredReason"]} for k, s in specs.items() if s.get("deferredReason")],
        "notTestable": [{"spec": int(k), "reason": s.get("notTestableReason")} for k, s in specs.items() if not s["testable"]],
    }


def problems(result: dict) -> int:
    return sum(len(result.get(k, [])) for k in ("stale", "orphan", "untagged", "nohash", "uncovered", "unrecorded", "lint"))


def format_text(result: dict) -> str:
    lines = [f"Drift check: {result['tests']} tests cover {result['coveredSpecs']} specifications (catalog {result['catalogGeneratedAt']})"]
    labels = {
        "stale": "STALE (specification text changed since the test was written)",
        "orphan": "ORPHAN (specification not in the catalog)",
        "untagged": "UNTAGGED (no SDS-<id> tag)",
        "nohash": "NO HASH (missing spechash: tag)",
        "uncovered": "UNCOVERED specifications",
        "suspect": "SUSPECT in RV&S (re-review the covering tests)",
        "pending": "PENDING human review",
        "unrecorded": "UNRECORDED REVIEW (review:pending removed without a matching entry in catalog/reviews.toml)",
        "lint": "LINT (test rule violations, see smm-auto lint)",
        "deferred": "Deferred",
        "notTestable": "Not testable",
    }
    for key, label in labels.items():
        items = result.get(key, [])
        if items:
            lines.append(f"\n{label}: {len(items)}")
            lines += [f"  - {json.dumps(i, ensure_ascii=False)}" for i in items]
    lines.append(f"\n{problems(result)} problem(s).")
    return "\n".join(lines)
