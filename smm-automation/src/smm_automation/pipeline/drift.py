"""Drift check: compares the Robot suites with the RV&S catalog.

* stale     - a test's spechash for one of its specifications differs from the current specification text
* orphan    - a test references ``SDS-<id>`` that is not in the catalog (removed or out of scope)
* untagged  - a test without any ``SDS-<id>`` tag (no traceability) and without a ``nospec:<kind>`` tag
* nospec    - (information) tests that verify behaviour no specification states (``nospec:<kind>``, e.g.
  robustness or unspecified state x request cells): listed, not a problem
* nohash    - a test without a spechash for one of its specifications. A test with one ``SDS-<id>`` tag uses
  ``spechash:<hash>``; a test with several uses ``spechash:<id>:<hash>`` for each of them
* uncovered - a testable, not deferred, not retired specification without any test
* stateChanged - a covered specification whose RV&S state differs from the accepted baseline (``smm-auto accept``)
* linksChanged - a covered specification whose requirement / user story links differ from the accepted baseline
* retired   - a covered specification in a retired state (Rejected, To Be Deleted, Deleted…): remove or re-target the tests
* suspect   - a covered specification RV&S flags as suspect (Suspect Count > 0)
* pending   - tests still tagged ``review:pending``
* unrecorded - a test without ``review:pending`` but without a human review in the ledger ``catalog/reviews.toml``
  for its current ``spechash`` (the tag was removed without a recorded review, or the spec changed since)
* areaGuessed - (information) specifications whose suite was guessed from keywords, not given in the scope file
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SDS_TAG = re.compile(r"^SDS-(\d+)$", re.I)
HASH_TAG = re.compile(r"^spechash:(?:(\d+):)?([0-9a-f]{6,64})$", re.I)
KNOWN_ISSUE_TAG = re.compile(r"^known-issue:(\S+)$", re.I)
NOSPEC_TAG = re.compile(r"^nospec:(\S+)$", re.I)
PENDING_TAG = "review:pending"


def parse_hash_tags(tags: list[str]) -> tuple[str | None, dict[int, str]]:
    """``spechash:<hash>`` (plain) and ``spechash:<id>:<hash>`` (per specification) tag values."""
    plain: str | None = None
    per_spec: dict[int, str] = {}
    for tag in tags:
        if m := HASH_TAG.match(tag):
            if m[1]:
                per_spec[int(m[1])] = m[2].lower()
            else:
                plain = m[2].lower()
    return plain, per_spec


def hash_for(spec_id: int, specs: list[int], plain: str | None, per_spec: dict[int, str]) -> str | None:
    """The hash a test carries for one of its specifications; a plain tag only counts on a single-spec test."""
    if spec_id in per_spec:
        return per_spec[spec_id]
    return plain if len(set(specs)) == 1 else None


@dataclass
class TestRef:
    __test__ = False  # not a pytest test class
    name: str
    suite: str
    source: str
    tags: list[str]
    specs: list[int] = field(default_factory=list)
    hash: str | None = None
    hashes: dict[int, str] = field(default_factory=dict)

    def hash_for(self, spec_id: int) -> str | None:
        return hash_for(spec_id, self.specs, self.hash, self.hashes)

    @property
    def nospec(self) -> list[str]:
        """The ``nospec:<kind>`` kinds of the test."""
        return [m[1] for t in self.tags if (m := NOSPEC_TAG.match(t))]

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
            plain, per_spec = parse_hash_tags(tags)
            ref = TestRef(name=t.name, suite=s.longname, source=str(s.source), tags=tags, hash=plain, hashes=per_spec)
            for tag in tags:
                if m := SDS_TAG.match(tag):
                    ref.specs.append(int(m[1]))
            out.append(ref)
        for child in s.suites:
            walk(child)

    walk(suite)
    return out


def _links_delta(spec: dict) -> dict | None:
    base = spec.get("baseline")
    if not base:
        return None
    delta = {}
    for key in ("satisfies", "userStories"):
        old, new = set(base.get(key) or []), set(spec.get(key) or [])
        if old != new:
            delta[key] = {"added": sorted(new - old), "removed": sorted(old - new)}
    return delta or None


def check(catalog: dict, tests: list[TestRef], reviews: list[dict] | None = None) -> dict:
    specs = catalog["specifications"]
    retired_states = set(catalog.get("retiredStates") or ["Rejected", "To Be Deleted", "Deleted"])
    covered: dict[int, list[str]] = {}
    stale, orphan, untagged, nohash, nospec = [], [], [], [], []
    for t in tests:
        if not t.specs:
            if t.nospec:
                nospec.append({"test": t.name, "suite": t.suite, "kind": ",".join(t.nospec)})
            else:
                untagged.append({"test": t.name, "suite": t.suite})
            continue
        for sid in t.specs:
            spec = specs.get(str(sid))
            if not spec:
                orphan.append({"test": t.name, "suite": t.suite, "spec": sid})
                continue
            covered.setdefault(sid, []).append(t.name)
            h = t.hash_for(sid)
            if not h:
                item = {"test": t.name, "spec": sid}
                if t.hash and len(set(t.specs)) > 1:
                    item["reason"] = f"several specifications: use spechash:{sid}:<hash> for each"
                nohash.append(item)
            elif not spec["hash"].startswith(h):
                stale.append({"test": t.name, "suite": t.suite, "spec": sid, "testHash": h, "specHash": spec["hash"][:8], "modified": spec.get("modified")})
    uncovered = [
        {"spec": int(k), "area": s["area"]}
        for k, s in specs.items()
        if s["testable"] and not s.get("deferredReason") and s.get("state") not in retired_states and int(k) not in covered
    ]
    suspect = [{"spec": sid, "suspectCount": specs[str(sid)].get("suspectCount")} for sid in covered if (specs[str(sid)].get("suspectCount") or 0) > 0]
    state_changed, links_changed, retired = [], [], []
    for sid in sorted(covered):
        spec = specs[str(sid)]
        base = spec.get("baseline") or {}
        if base and base.get("state") != spec.get("state"):
            state_changed.append({"spec": sid, "from": base.get("state"), "to": spec.get("state"), "tests": covered[sid]})
        if delta := _links_delta(spec):
            links_changed.append({"spec": sid, **delta, "tests": covered[sid]})
        if spec.get("state") in retired_states:
            retired.append({"spec": sid, "state": spec.get("state"), "tests": covered[sid]})
    return {
        "catalogGeneratedAt": catalog.get("generatedAt"),
        "tests": len(tests),
        "coveredSpecs": len(covered),
        "stale": stale,
        "orphan": orphan,
        "untagged": untagged,
        "nohash": nohash,
        "nospec": nospec,
        "uncovered": uncovered,
        "stateChanged": state_changed,
        "linksChanged": links_changed,
        "retired": retired,
        "suspect": suspect,
        "pending": [{"test": t.name, "suite": t.suite} for t in tests if t.pending],
        "unrecorded": check_reviews(tests, reviews) if reviews is not None else [],
        "deferred": [{"spec": int(k), "reason": s["deferredReason"]} for k, s in specs.items() if s.get("deferredReason")],
        "notTestable": [{"spec": int(k), "reason": s.get("notTestableReason")} for k, s in specs.items() if not s["testable"]],
        "areaGuessed": [{"spec": int(k), "area": s["area"]} for k, s in specs.items() if s.get("areaSource") not in (None, "scope")],
    }


PROBLEM_KEYS = ("stale", "orphan", "untagged", "nohash", "uncovered", "stateChanged", "linksChanged", "retired", "unrecorded", "lint")


def problems(result: dict) -> int:
    return sum(len(result.get(k, [])) for k in PROBLEM_KEYS)


def format_text(result: dict) -> str:
    lines = [f"Drift check: {result['tests']} tests cover {result['coveredSpecs']} specifications (catalog {result['catalogGeneratedAt']})"]
    labels = {
        "stale": "STALE (specification text changed since the test was written)",
        "orphan": "ORPHAN (specification not in the catalog)",
        "untagged": "UNTAGGED (no SDS-<id> tag and no nospec:<kind> tag)",
        "nohash": "NO HASH (missing spechash: tag for a specification of the test)",
        "uncovered": "UNCOVERED specifications",
        "stateChanged": "SPEC-STATE-CHANGED (RV&S state differs from the accepted baseline: re-check the tests, then smm-auto accept)",
        "linksChanged": "LINKS-CHANGED (requirement / user story links differ from the accepted baseline: re-check, then smm-auto accept)",
        "retired": "RETIRED (covered specification is rejected/deleted in RV&S: remove or re-target the tests)",
        "suspect": "SUSPECT in RV&S (re-review the covering tests)",
        "pending": "PENDING human review",
        "unrecorded": "UNRECORDED REVIEW (review:pending removed without a matching entry in catalog/reviews.toml)",
        "lint": "LINT (test rule violations, see smm-auto lint)",
        "nospec": "Tests of behaviour no specification states (nospec:<kind>)",
        "deferred": "Deferred",
        "notTestable": "Not testable",
        "areaGuessed": "Area guessed from keywords (list the specification under its area in the scope file)",
    }
    for key, label in labels.items():
        items = result.get(key, [])
        if items:
            lines.append(f"\n{label}: {len(items)}")
            lines += [f"  - {json.dumps(i, ensure_ascii=False)}" for i in items]
    lines.append(f"\n{problems(result)} problem(s).")
    return "\n".join(lines)


_HASH_VALUE = re.compile(r"(?<![0-9a-z])(?:(\d+):)?([0-9a-f]{6,64})(?![0-9a-z])", re.I)


def hash_migrations(old: dict, new: dict, normalize) -> tuple[list[dict], list[int]]:
    """Specifications whose hash changed only because of the hashing (same normalised text): the old and new hash.
    The second list holds specifications whose hash changed because the text really changed (they stay STALE)."""
    o, n = old.get("specifications", {}), new.get("specifications", {})
    moves, changed = [], []
    for key in sorted(n.keys() & o.keys(), key=int):
        if o[key]["hash"] == n[key]["hash"]:
            continue
        if normalize(o[key].get("text")) == normalize(n[key].get("text")):
            moves.append({"spec": int(key), "old": o[key]["hash"], "new": n[key]["hash"]})
        else:
            changed.append(int(key))
    return moves, changed


def _rewrite(text: str, moves: list[dict]) -> tuple[str, int]:
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        sid, value = m[1], m[2].lower()
        for mv in moves:
            if (sid is None or int(sid) == mv["spec"]) and mv["old"].startswith(value):
                count += 1
                return (f"{sid}:" if sid else "") + mv["new"][: len(value)]
        return m[0]

    return _HASH_VALUE.sub(sub, text), count


def migrate_hash_tags(moves: list[dict], robot_files: list[Path], reviews_file: Path | None) -> dict[str, int]:
    """Replaces old ``spechash`` values by the new ones in the Robot files (``spechash:`` tags) and in the
    ``spechash`` lines of the review ledger. Returns the number of replacements per file."""
    out: dict[str, int] = {}
    if not moves:
        return out
    tag = re.compile(r"spechash:[0-9a-f:]+", re.I)
    for path in robot_files:
        text = path.read_bytes().decode("utf-8")  # keeps the line endings
        total = 0

        def sub_tag(m: re.Match) -> str:
            nonlocal total
            new, c = _rewrite(m[0][len("spechash:"):], moves)
            total += c
            return m[0][: len("spechash:")] + new

        updated = tag.sub(sub_tag, text)
        if total:
            path.write_bytes(updated.encode("utf-8"))
            out[str(path)] = total
    if reviews_file and reviews_file.exists():
        lines = reviews_file.read_bytes().decode("utf-8").splitlines(keepends=True)
        total = 0
        for i, line in enumerate(lines):
            if re.match(r"\s*spechash\s*=", line):
                key, _, value = line.partition("=")
                new, c = _rewrite(value, moves)
                lines[i], total = key + "=" + new, total + c
        if total:
            reviews_file.write_bytes("".join(lines).encode("utf-8"))
            out[str(reviews_file)] = total
    return out
