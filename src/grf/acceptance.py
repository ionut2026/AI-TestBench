"""Run requests and the acceptance gate (docs/WORKFLOWS.md).

A *run request* (TOML, ``schema = 1``) says exactly what a run is for: the tier, the specifications and tests that
must run, which builds of which components must be on the boards, who authorized it and until when. It contains no
credentials and no commands. ``<cli> request check`` validates one without contacting anything.

The *acceptance gate* (``<cli> gate``) compares a finished run (Robot ``output.xml``), the current suites, catalog and
review ledger, the deployment records and the versions the product reported during the run against the request. It
accepts the run only if every check passes; anything missing, unknown or ambiguous fails. A run that finished is not
automatically an accepted result: mock (non-evidence) tiers are never accepted, tests without a current human review
(content fingerprint and spechash) are never accepted, and the authorization in a request is a record of who
authorized the run, not an authentication: the controller (Jenkins) authenticates who starts it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUEST_SCHEMA = 1
RESULT_SCHEMA = 1
ACTIONS = ("test", "deploy-and-test")
GOOD_DEPLOY = ("DEPLOYED", "UNCHANGED")
DRIFT_KEYS = ("stale", "orphan", "nohash", "stateChanged", "linksChanged", "retired", "suspect")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_TOP = {"schema", "id", "action", "tier", "description", "source_revision", "catalog_sha256", "selection", "acceptance",
        "component", "authorization"}
_SELECTION = {"specs", "tests", "min_tests"}
_ACCEPTANCE = {"allowed_skips", "allow_known_failures", "max_catalog_age_days"}
_COMPONENT = {"name", "build", "sha256", "expected_version", "runtime_identity"}
_AUTHORIZATION = {"owner", "reference", "valid_until"}


class RequestError(ValueError):
    pass


@dataclass(frozen=True)
class ComponentRequest:
    name: str
    build: int | None = None
    sha256: str | None = None
    expected_version: str | None = None
    runtime_identity: bool = True


@dataclass(frozen=True)
class RunRequest:
    id: str
    action: str
    tier: str
    description: str = ""
    source_revision: str | None = None
    catalog_sha256: str | None = None
    specs: tuple[int, ...] = ()
    tests: tuple[str, ...] = ()
    min_tests: int = 1
    allowed_skips: tuple[str, ...] = ()
    allow_known_failures: bool = False
    max_catalog_age_days: int | None = None
    components: tuple[ComponentRequest, ...] = ()
    owner: str = ""
    reference: str = ""
    valid_until: dt.date | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def summary(self) -> list[str]:
        lines = [f"Request {self.id}: {self.action} on the {self.tier} tier",
                 f"  must run: {len(self.specs)} specification(s), {len(self.tests)} named test(s), at least {self.min_tests} test(s)"]
        for c in self.components:
            lines.append(f"  {c.name}: build {c.build if c.build is not None else '(installed)'}"
                         f"{', sha256 ' + c.sha256[:12] if c.sha256 else ''}"
                         f"{', expects version ' + c.expected_version if c.expected_version else ''}"
                         f"{'' if c.runtime_identity else ', no runtime identity check'}")
        lines.append(f"  authorized by {self.owner} ({self.reference}) until {self.valid_until}")
        return lines

    def as_dict(self) -> dict[str, Any]:
        """Validated request in the TOML schema's shape, with defaults and JSON-safe dates."""
        data: dict[str, Any] = {
            "schema": REQUEST_SCHEMA, "id": self.id, "action": self.action, "tier": self.tier,
            "description": self.description,
            "selection": {"specs": list(self.specs), "tests": list(self.tests), "min_tests": self.min_tests},
            "acceptance": {"allowed_skips": list(self.allowed_skips), "allow_known_failures": self.allow_known_failures},
            "component": [],
            "authorization": {"owner": self.owner, "reference": self.reference, "valid_until": str(self.valid_until)},
        }
        for key in ("source_revision", "catalog_sha256"):
            if (value := getattr(self, key)) is not None:
                data[key] = value
        if self.max_catalog_age_days is not None:
            data["acceptance"]["max_catalog_age_days"] = self.max_catalog_age_days
        for c in self.components:
            item: dict[str, Any] = {"name": c.name, "runtime_identity": c.runtime_identity}
            for key in ("build", "sha256", "expected_version"):
                if (value := getattr(c, key)) is not None:
                    item[key] = value
            data["component"].append(item)
        return data


def _unknown(table: dict, allowed: set[str], where: str) -> None:
    extra = sorted(set(table) - allowed)
    if extra:
        raise RequestError(f"{where}: unknown field(s) {', '.join(extra)}")


def _table(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise RequestError(f"[{key}] must be a table")
    return value


def _strings(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise RequestError(f"{where} must be a list of nonempty strings")
    if len(set(value)) != len(value):
        raise RequestError(f"{where} has duplicates")
    return tuple(value)


def parse_request(data: dict, tiers: dict[str, Any] | None = None, components: list[str] | None = None) -> RunRequest:
    """Validates a request (unknown fields, types, values); ``tiers``/``components`` restrict names to the project's."""
    _unknown(data, _TOP, "request")
    if type(data.get("schema")) is not int or data.get("schema") != REQUEST_SCHEMA:
        raise RequestError(f"schema must be {REQUEST_SCHEMA}")
    rid = data.get("id")
    if not isinstance(rid, str) or not _ID.match(rid):
        raise RequestError("id must be 1-80 characters: letters, digits, '.', '_' or '-'")
    action = data.get("action")
    if action not in ACTIONS:
        raise RequestError(f"action must be one of {', '.join(ACTIONS)}")
    tier = data.get("tier")
    if not isinstance(tier, str) or not tier:
        raise RequestError("tier is required")
    if tiers is not None:
        if tier not in tiers:
            raise RequestError(f"unknown tier '{tier}' ({', '.join(tiers)})")
        if not getattr(tiers[tier], "evidence", True):
            raise RequestError(f"the {tier} tier is not product evidence; a run request is only for evidence tiers")
    for key, pattern, what in (("source_revision", _REVISION, "a full 40-character git commit"),
                               ("catalog_sha256", _SHA256, "64 lowercase hex characters")):
        if key in data and (not isinstance(data[key], str) or not pattern.match(data[key])):
            raise RequestError(f"{key} must be {what}")
    if not isinstance(data.get("description", ""), str):
        raise RequestError("description must be text")

    selection = _table(data, "selection")
    _unknown(selection, _SELECTION, "[selection]")
    specs = selection.get("specs", [])
    if not isinstance(specs, list) or not all(isinstance(s, int) and not isinstance(s, bool) and s > 0 for s in specs):
        raise RequestError("[selection] specs must be a list of positive specification IDs")
    if len(set(specs)) != len(specs):
        raise RequestError("[selection] specs has duplicates")
    tests = _strings(selection.get("tests", []), "[selection] tests")
    min_tests = selection.get("min_tests", 1)
    if not isinstance(min_tests, int) or isinstance(min_tests, bool) or min_tests < 1:
        raise RequestError("[selection] min_tests must be at least 1 (a run without tests is never accepted)")

    acceptance = _table(data, "acceptance")
    _unknown(acceptance, _ACCEPTANCE, "[acceptance]")
    skips = _strings(acceptance.get("allowed_skips", []), "[acceptance] allowed_skips")
    known = acceptance.get("allow_known_failures", False)
    if not isinstance(known, bool):
        raise RequestError("[acceptance] allow_known_failures must be true or false")
    age = acceptance.get("max_catalog_age_days")
    if age is not None and (not isinstance(age, int) or isinstance(age, bool) or age < 0):
        raise RequestError("[acceptance] max_catalog_age_days must be a nonnegative whole number")

    raw_components = data.get("component", [])
    if not isinstance(raw_components, list):
        raise RequestError("[[component]] must be an array of tables")
    comps: list[ComponentRequest] = []
    for i, c in enumerate(raw_components):
        where = f"[[component]] #{i + 1}"
        if not isinstance(c, dict):
            raise RequestError(f"{where} must be a table")
        _unknown(c, _COMPONENT, where)
        name = c.get("name")
        if not isinstance(name, str) or not name:
            raise RequestError(f"{where}: name is required")
        if components is not None and name not in components:
            raise RequestError(f"{where}: unknown component '{name}' ({', '.join(components) or 'none defined'})")
        if any(x.name == name for x in comps):
            raise RequestError(f"{where}: component '{name}' appears twice")
        build = c.get("build")
        if build is not None and (not isinstance(build, int) or isinstance(build, bool) or build < 1):
            raise RequestError(f"{where}: build must be a positive Jenkins build number")
        sha = c.get("sha256")
        if sha is not None and (not isinstance(sha, str) or not _SHA256.match(sha)):
            raise RequestError(f"{where}: sha256 must be 64 lowercase hex characters")
        version = c.get("expected_version")
        if version is not None and (not isinstance(version, str) or not version.strip()):
            raise RequestError(f"{where}: expected_version must be nonempty text")
        runtime = c.get("runtime_identity", True)
        if not isinstance(runtime, bool):
            raise RequestError(f"{where}: runtime_identity must be true or false")
        if action == "deploy-and-test" and (build is None or sha is None):
            raise RequestError(f"{where}: deploy-and-test needs the exact build and its sha256 (take both from a reviewed plan)")
        if action == "test" and runtime and version is None:
            raise RequestError(f"{where}: a tests-only run needs expected_version to check what is installed")
        comps.append(ComponentRequest(name, build, sha, version, runtime))
    if action == "deploy-and-test" and not comps:
        raise RequestError("deploy-and-test needs at least one [[component]]")

    auth = _table(data, "authorization")
    _unknown(auth, _AUTHORIZATION, "[authorization]")
    owner, reference, until = auth.get("owner"), auth.get("reference"), auth.get("valid_until")
    if not isinstance(owner, str) or not owner.strip() or owner.strip().lower().startswith("agent:"):
        raise RequestError("[authorization] owner must name the person (the instrument owner), not an agent")
    if not isinstance(reference, str) or not reference.strip():
        raise RequestError("[authorization] reference is required (ticket, e-mail or approval ID)")
    if not isinstance(until, dt.date) or isinstance(until, dt.datetime):
        raise RequestError("[authorization] valid_until must be a TOML date, e.g. 2026-07-31")
    return RunRequest(rid, action, tier, data.get("description", ""), data.get("source_revision"), data.get("catalog_sha256"),
                      tuple(specs), tests, min_tests, skips, known, age, tuple(comps), owner.strip(), reference.strip(), until,
                      raw=data)


def load_request(path: Path, tiers: dict[str, Any] | None = None, components: list[str] | None = None) -> RunRequest:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as err:
        raise RequestError(f"{path}: {err}") from err
    try:
        return parse_request(data, tiers, components)
    except RequestError as err:
        raise RequestError(f"{path}: {err}") from err


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_state(root: Path) -> tuple[str | None, bool | None]:
    """(HEAD commit, tracked files modified?) of the checkout; (None, None) without git."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=30)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True,
                               text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if head.returncode or dirty.returncode:
        return None, None
    return head.stdout.strip(), bool(dirty.stdout.strip())


@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL
    detail: str


def _check(checks: list[Check], name: str, problems: list[str], ok: str) -> None:
    checks.append(Check(name, "FAIL" if problems else "PASS", "; ".join(problems) if problems else ok))


def evaluate(request: RunRequest, *, tests: list[dict], meta: dict, known: list, reviews: list[dict], catalog: dict,
             catalog_path: Path, records: list[dict], observed: dict[str, str], matches, root: Path,
             today: dt.date | None = None, evidence: bool = True, project_name: str | None = None) -> dict:
    """The acceptance result. ``tests``/``meta``: ``report.read_results``; ``known``: the current suites
    (``drift.collect_tests``); ``observed``: component -> runtime version (the plugin); ``matches(component, expected,
    observed)``: the plugin's version comparison."""
    from grf.pipeline.drift import check as drift_check
    from grf.pipeline.drift import review_problem
    from grf.pipeline.report import _test_key

    today = today or dt.date.today()
    checks: list[Check] = []

    # authorization window and tier
    problems = []
    if request.valid_until is None or today > request.valid_until:
        problems.append(f"the authorization expired on {request.valid_until}")
    _check(checks, "authorization", problems, f"{request.owner} ({request.reference}) until {request.valid_until}")
    tier = meta.get("Tier", "?")
    problems = []
    if tier != request.tier:
        problems.append(f"the run is on the '{tier}' tier, the request is for '{request.tier}'")
    if not evidence:
        problems.append(f"the {tier} tier is not product evidence (a mock); it is never accepted")
    _check(checks, "tier", problems, f"{tier} (product evidence)")

    # source revision and catalog binding
    problems = []
    head, dirty = git_state(root)
    if request.source_revision:
        if head is None:
            problems.append("cannot read the git revision of the checkout")
        elif head != request.source_revision:
            problems.append(f"checkout is {head}, the request is for {request.source_revision}")
        elif dirty:
            problems.append("tracked files are modified in the checkout")
    _check(checks, "source", problems, f"{head or 'unknown revision'}{' (modified)' if dirty else ''}"
           + ("" if request.source_revision else "; the request pins no source_revision"))
    problems = []
    digest = file_sha256(catalog_path) if catalog_path.exists() else None
    if request.catalog_sha256 and digest != request.catalog_sha256:
        problems.append(f"catalog sha256 is {digest}, the request is for {request.catalog_sha256}")
    generated = catalog.get("generatedAt")
    if request.max_catalog_age_days is not None:
        try:
            when = dt.datetime.fromisoformat(str(generated).replace("Z", "+00:00")).date()
            if (today - when).days > request.max_catalog_age_days:
                problems.append(f"the catalog is from {when}, older than {request.max_catalog_age_days} day(s)")
        except ValueError:
            problems.append(f"the catalog has no usable generatedAt ({generated!r})")
    _check(checks, "catalog", problems, f"generated {generated}, sha256 {str(digest)[:12]}")

    # what ran
    executed = [t for t in tests if t["status"] in ("PASS", "FAIL", "SKIP")]
    names = {t["name"] for t in executed if t["status"] != "SKIP"}
    problems = []
    if len(executed) < request.min_tests:
        problems.append(f"{len(executed)} test(s) ran, at least {request.min_tests} required")
    if not any(t["status"] != "SKIP" for t in executed):
        problems.append("no test executed (every selected test was skipped)")
    for name in request.tests:
        if sum(t["name"] == name for t in executed) > 1:
            problems.append(f"required test name is ambiguous across suites: {name}")
    missing_tests = [n for n in request.tests if n not in names]
    if missing_tests:
        problems.append("required test(s) did not run: " + ", ".join(missing_tests))
    ran_specs = {s for t in executed if t["status"] != "SKIP" for s in t["specs"]}
    missing_specs = [s for s in request.specs if s not in ran_specs]
    if missing_specs:
        problems.append("required specification(s) without an executed test: " + ", ".join(map(str, missing_specs)))
    _check(checks, "selection", problems, f"{len(executed)} test(s) ran, covering {len(ran_specs)} specification(s)")

    # results
    problems = []
    for t in executed:
        if t["status"] == "FAIL" and not (request.allow_known_failures and t.get("knownIssues")):
            problems.append(f"FAIL {t['name']}")
        if t["status"] == "SKIP" and t["name"] not in request.allowed_skips:
            problems.append(f"SKIP {t['name']} (not in allowed_skips)")
    _check(checks, "results", problems, "every executed test passed (or is an allowed skip/known failure)")

    # reviews of the executed tests at their current content
    by_key = {_test_key(k): k for k in known}
    problems = []
    for t in executed:
        ref = by_key.get(_test_key(t))
        if ref is None:
            problems.append(f"{t['name']}: not found in the current suites (the run is not from this checkout)")
            continue
        if ref.pending or t.get("pending"):
            problems.append(f"{t['name']}: tagged review:pending")
            continue
        if sorted(ref.tags) != sorted(t["tags"]):
            problems.append(f"{t['name']}: tags differ between the run and the current suites")
            continue
        reason = review_problem(ref, known, reviews, content=True)
        if reason:
            problems.append(f"{t['name']}: {reason}")
    _check(checks, "reviews", problems, "every executed test has a current human approval (spechash and content)")

    # catalog drift of the executed tests' specifications
    problems = []
    run_refs = [by_key[_test_key(t)] for t in executed if _test_key(t) in by_key]
    drift = drift_check(catalog, run_refs) if run_refs else {}
    for key in DRIFT_KEYS:
        for item in drift.get(key, []):
            problems.append(f"{key}: {item.get('test') or ''} {catalog_tag(item)}".strip())
    _check(checks, "drift", problems, "no stale, orphan, changed, retired or suspect specification among the executed tests")

    # deployments and identity
    for c in request.components:
        problems = []
        mine = [r for r in records if r.get("component") == c.name]
        expected = c.expected_version
        if request.action == "deploy-and-test":
            if len(mine) != 1:
                problems.append(f"expected exactly one deployment record, got {len(mine)}")
            else:
                r = mine[0]
                art = r.get("artifact")
                if not isinstance(art, dict):
                    art = {}
                    problems.append("no artifact object")
                if project_name is not None and r.get("project") != project_name:
                    problems.append(f"deployment project {r.get('project')!r}, expected {project_name!r}")
                if r.get("dryRun") is not False:
                    problems.append("the deployment record is a dry run")
                if r.get("outcome") not in GOOD_DEPLOY:
                    problems.append(f"deployment outcome {r.get('outcome')}")
                if art.get("build_number") != c.build:
                    problems.append(f"deployed build {art.get('build_number')}, requested {c.build}")
                if art.get("sha256") != c.sha256:
                    problems.append(f"deployed sha256 {art.get('sha256')}, requested {c.sha256}")
                installed = r.get("installed")
                if not isinstance(installed, dict) or installed.get("status") != "running":
                    problems.append("no installed running component")
                else:
                    installed_version = installed.get("version")
                    if not isinstance(installed_version, str) or not installed_version.strip():
                        problems.append("no installed version")
                    elif expected and not matches(c.name, expected, installed_version):
                        problems.append(f"installed version {installed_version}, requested {expected}")
                    else:
                        expected = installed_version
        elif mine:
            problems.append("a tests-only request must not come with a deployment record of this component")
        if c.runtime_identity:
            seen = observed.get(c.name)
            if not expected:
                problems.append("no expected version (request expected_version or the deployment record)")
            elif not isinstance(seen, str) or not seen.strip():
                problems.append("the run reported no version of this component (identity unavailable)")
            elif not matches(c.name, expected, seen):
                problems.append(f"the run reported version {seen}, expected {expected}")
        ok = f"build {c.build if c.build is not None else 'installed'}"
        ok += f", version {observed.get(c.name)} matches {expected}" if c.runtime_identity else ", runtime identity not checked"
        _check(checks, f"component {c.name}", problems, ok)
    unexpected = sorted({str(r.get("component")) for r in records} - {c.name for c in request.components})
    if unexpected:
        _check(checks, "deployments", [f"deployment record(s) of component(s) not in the request: {', '.join(unexpected)}"], "")

    accepted = all(c.status == "PASS" for c in checks)
    return {
        "schema": RESULT_SCHEMA,
        "request": request.id,
        "action": request.action,
        "tier": tier,
        "status": "ACCEPTED" if accepted else "REJECTED",
        "evaluatedAt": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "sourceRevision": head,
        "sourceModified": dirty,
        "catalogSha256": digest,
        "authorization": {"owner": request.owner, "reference": request.reference, "validUntil": str(request.valid_until)},
        "executed": len(executed),
        "checks": [vars(c) for c in checks],
        "note": "Accepted means: this run satisfies the request. It is product evidence only together with the run's "
                "report; the request's authorization is a record, the controller authenticates who started the run.",
    }


def catalog_tag(item: dict) -> str:
    return f"(specification {item['spec']})" if "spec" in item else ""


def format_result(result: dict) -> str:
    lines = [f"Acceptance of request {result['request']} ({result['action']}, {result['tier']} tier): {result['status']}"]
    for c in result["checks"]:
        lines.append(f"  {c['status']:4} {c['name']}: {c['detail']}")
    if result["status"] != "ACCEPTED":
        lines.append("REJECTED: the run is not an accepted product result (see the FAIL lines above).")
    return "\n".join(lines)


def write_result(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
