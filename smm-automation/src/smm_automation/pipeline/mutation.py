"""Mutation testing of the suites against the mock appSMM (plan 3.1-3.4).

A mutant (``catalog/mutants.toml``) is a set of fault rules for the mock appSMM (service
``faults.ts``: drop, duplicate, delay, reorder, wrongTopic, set, unset, invalid) that breaks one or
more specifications. For each mutant the tests tagged ``SDS-<spec>`` for those specifications run
on the mock tier with the faults active: if at least one fails, the mutant is *killed*; if all
pass, it *survived* and the tests would not notice that defect in appSMM. This measures the
suites, not the product.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from smm_automation.pipeline.drift import TestRef

MUTANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
FAULT_ACTIONS = {"drop", "duplicate", "delay", "reorder", "wrongTopic", "set", "unset", "invalid"}
SETUP_FAILURE = re.compile(r"^(Parent suite setup failed|Setup failed)", re.IGNORECASE)
DEFAULT_MIN_SCORE = 0.9


class MutantError(ValueError):
    pass


def load_mutants(path: Path) -> list[dict]:
    """Reads and checks the mutant catalogue (the service validates the fault rules in full)."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    mutants = data.get("mutant", [])
    if not isinstance(mutants, list) or not mutants:
        raise MutantError(f"{path}: no [[mutant]] entries")
    seen: set[str] = set()
    for i, m in enumerate(mutants):
        where = f"{path.name}: mutant {m.get('id') or i + 1}"
        if not isinstance(m.get("id"), str) or not MUTANT_ID.match(m["id"]):
            raise MutantError(f"{where}: id must be lower-case letters, digits and dashes")
        if m["id"] in seen:
            raise MutantError(f"{where}: duplicate id")
        seen.add(m["id"])
        if not isinstance(m.get("description"), str) or not m["description"].strip():
            raise MutantError(f"{where}: description is required")
        specs = m.get("specs")
        if not isinstance(specs, list) or not specs or not all(isinstance(s, int) for s in specs):
            raise MutantError(f"{where}: specs must be a non-empty list of specification IDs")
        faults = m.get("faults")
        if not isinstance(faults, list) or not faults:
            raise MutantError(f"{where}: faults must be a non-empty list")
        for j, fault in enumerate(faults):
            if not isinstance(fault, dict) or not fault.get("message") or fault.get("action") not in FAULT_ACTIONS:
                raise MutantError(f"{where}: faults[{j}] needs message and action ({', '.join(sorted(FAULT_ACTIONS))})")
    return mutants


def select_tests(mutant: dict, tests: list[TestRef], excludes: list[str]) -> list[TestRef]:
    """The tests that verify one of the mutant's specifications and run on the tier."""
    specs = set(mutant["specs"])
    excluded = {e.lower() for e in excludes}
    return [t for t in tests if specs & set(t.specs) and not excluded & {x.lower() for x in t.tags}]


def verdict(results: list[dict]) -> dict:
    """``killed`` if a selected test failed, ``survived`` if they all passed, ``noTests`` otherwise."""
    failed = [r for r in results if r["status"] == "FAIL"]
    passed = [r for r in results if r["status"] == "PASS"]
    if failed:
        status = "killed"
    elif passed:
        status = "survived"
    else:
        status = "noTests"
    return {
        "status": status,
        "killedBy": [{"test": r["name"], "phase": "setup" if SETUP_FAILURE.match(r.get("message") or "") else "test",
                      "message": (r.get("message") or "")[:300]} for r in failed],
        "passed": [r["name"] for r in passed],
    }


def score(entries: list[dict]) -> dict:
    killed = sum(1 for e in entries if e["status"] == "killed")
    survived = sum(1 for e in entries if e["status"] == "survived")
    total = killed + survived
    return {"killed": killed, "survived": survived, "noTests": sum(1 for e in entries if e["status"] == "noTests"),
            "errors": sum(1 for e in entries if e["status"] == "error"),
            "score": round(killed / total, 3) if total else None}


def per_spec(entries: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in entries:
        for spec in e["specs"]:
            s = out.setdefault(str(spec), {"killed": [], "survived": [], "other": []})
            s["killed" if e["status"] == "killed" else "survived" if e["status"] == "survived" else "other"].append(e["id"])
    return dict(sorted(out.items()))


def robot_argv(tier: str, variablefile: Path, specs: list[int], excludes: list[str], out_dir: Path, suites: list[Path],
               metadata: str) -> list[str]:
    argv = [sys.executable, "-m", "robot", "--variablefile", str(variablefile), "--outputdir", str(out_dir),
            "--name", f"SMM {tier} mutation", "--metadata", metadata, "--console", "dotted", "--report", "NONE"]
    for spec in specs:
        argv += ["--include", f"SDS-{spec}"]
    for tag in excludes:
        argv += ["--exclude", tag]
    return argv + [str(p) for p in suites]


Runner = Callable[[list[str], dict[str, str], Path, int], int]


def _subprocess_runner(argv: list[str], env: dict[str, str], console: Path, timeout_s: int) -> int:
    with open(console, "w", encoding="utf-8") as out:
        try:
            return subprocess.run(argv, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=timeout_s).returncode
        except subprocess.TimeoutExpired:
            out.write(f"\nTIMEOUT after {timeout_s} s\n")
            return 255


def run_once(name: str, faults: list[dict], specs: list[int], out_dir: Path, *, tier: str, variablefile: Path, excludes: list[str],
             suites: list[Path], runner: Runner = _subprocess_runner, timeout_s: int = 1800) -> tuple[int, list[dict]]:
    """Runs the tests of ``specs`` once with ``faults`` active in the mock appSMM; returns (rc, test results)."""
    from smm_automation.pipeline.report import read_results

    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SMM_MOCK_FAULTS=json.dumps(faults))
    rc = runner(robot_argv(tier, variablefile, specs, excludes, out_dir, suites, f"Mutant:{name}"), env, out_dir / "console.txt", timeout_s)
    output = out_dir / "output.xml"
    if not output.exists():
        return rc, []
    tests, _meta = read_results(output)
    return rc, tests


def mutate(mutants: list[dict], tests: list[TestRef], out_dir: Path, *, tier: str, variablefile: Path, excludes: list[str],
           suites: list[Path], baseline: bool = True, runner: Runner = _subprocess_runner,
           log: Callable[[str], None] = print) -> dict:
    """Runs every mutant; with ``baseline`` the selected tests first run once without faults and must pass."""
    selected = {m["id"]: select_tests(m, tests, excludes) for m in mutants}
    result: dict[str, Any] = {"tier": tier, "started": dt.datetime.now().isoformat(timespec="seconds"), "mutants": []}
    if baseline:
        specs = sorted({s for m in mutants if selected[m["id"]] for s in m["specs"]})
        rc, base = run_once("baseline", [], specs, out_dir / "baseline", tier=tier, variablefile=variablefile, excludes=excludes,
                            suites=suites, runner=runner)
        failed = [t["name"] for t in base if t["status"] == "FAIL"]
        result["baseline"] = {"rc": rc, "tests": len(base), "failed": failed}
        log(f"baseline: {len(base)} test(s), {len(failed)} failed")
        if failed or not base:
            result["aborted"] = "the tests fail (or did not run) without faults: mutation results would be meaningless"
            return result
    for m in mutants:
        entry: dict[str, Any] = {"id": m["id"], "description": m["description"], "specs": m["specs"], "faults": m["faults"],
                                 "tests": [t.name for t in selected[m["id"]]]}
        if not selected[m["id"]]:
            entry.update(status="noTests", killedBy=[], passed=[])
        else:
            rc, results = run_once(m["id"], m["faults"], m["specs"], out_dir / m["id"], tier=tier, variablefile=variablefile,
                                   excludes=excludes, suites=suites, runner=runner)
            entry.update(verdict(results))
            entry["rc"] = rc
            if not results:
                entry["status"] = "error"
        log(f"{entry['status'].upper():8} {m['id']}")
        result["mutants"].append(entry)
    result["score"] = score(result["mutants"])
    result["specs"] = per_spec(result["mutants"])
    return result


def specs_without_mutants(mutants: list[dict], tests: list[TestRef], excludes: list[str]) -> list[int]:
    """Specifications with tests on the tier that no mutant targets."""
    excluded = {e.lower() for e in excludes}
    tested = {s for t in tests if not excluded & {x.lower() for x in t.tags} for s in t.specs}
    return sorted(tested - {s for m in mutants for s in m["specs"]})


def write_report(result: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "mutation.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    e = html.escape
    sc = result.get("score") or {}
    rows = "".join(
        f"<tr class='{m['status']}'><td>{e(m['id'])}</td><td>{e(m['status'])}</td><td>{e(', '.join(map(str, m['specs'])))}</td>"
        f"<td>{e(m['description'])}</td><td>{e('; '.join(k['test'] + (' (setup)' if k['phase'] == 'setup' else '') for k in m.get('killedBy', [])))}</td>"
        f"<td><code>{e(json.dumps(m['faults']))}</code></td></tr>"
        for m in result.get("mutants", []))
    spec_rows = "".join(
        f"<tr><td>SDS-{e(s)}</td><td>{len(v['killed'])}</td><td>{len(v['survived'])}</td><td>{e(', '.join(v['survived']))}</td></tr>"
        for s, v in (result.get("specs") or {}).items())
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>SMM mutation testing</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px}}table{{border-collapse:collapse;margin-bottom:24px}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}}code{{font-size:11px}}
tr.killed td:nth-child(2){{color:#1a7f37}}tr.survived td:nth-child(2){{color:#cf222e;font-weight:bold}}</style></head><body>
<h1>Mutation testing ({e(result.get('tier', '?'))} tier)</h1>
<p>Measures whether the suites notice defects injected into the mock appSMM. Not evidence about the product.</p>
<p>{e(result.get('aborted', ''))}</p>
<p>Score: <b>{sc.get('score')}</b> &mdash; killed {sc.get('killed', 0)}, survived {sc.get('survived', 0)},
no tests {sc.get('noTests', 0)}, errors {sc.get('errors', 0)}. Started {e(result.get('started', ''))}.</p>
<h2>Mutants</h2><table><tr><th>Mutant</th><th>Result</th><th>Specs</th><th>Defect</th><th>Killed by</th><th>Faults</th></tr>{rows}</table>
<h2>Per specification</h2><table><tr><th>Spec</th><th>Killed</th><th>Survived</th><th>Surviving mutants</th></tr>{spec_rows}</table>
</body></html>"""
    path = out_dir / "mutation.html"
    path.write_text(page, encoding="utf-8")
    return path


def survivors_markdown(result: dict) -> str:
    """Body of the CI issue for surviving mutants (empty when none survived)."""
    survived = [m for m in result.get("mutants", []) if m["status"] == "survived"]
    if not survived:
        return ""
    lines = [f"Mutation score {result['score']['score']} ({result['score']['killed']} killed, {len(survived)} survived).", "",
             "Each surviving mutant is an appSMM defect the suites would not notice: strengthen the tests of its "
             "specifications, or document why the mutant is equivalent (catalog/mutants.toml).", ""]
    for m in survived:
        lines += [f"- **{m['id']}** (SDS-{', SDS-'.join(map(str, m['specs']))}): {m['description']}",
                  f"  - passing tests: {'; '.join(m['passed'])}"]
    return "\n".join(lines) + "\n"
