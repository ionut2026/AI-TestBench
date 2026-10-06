"""Traceability report: Robot results (output.xml) joined with the RV&S catalog.

Per specification: PASS (every test of the spec ran and passed) / PARTIAL (some passed, others were not run or
skipped on this tier) / FAIL / SKIP / NOT RUN (tests exist but were excluded from this run) / UNCOVERED /
DEFERRED / NOT TESTABLE, plus stale-hash warnings. A PASS is only VERIFIED when every test of the spec has had a
human review (no ``review:pending`` tag). Per requirement and user story: the worst verdict of their in-scope
specifications.

Per test: failures of tests tagged ``known-issue:<id>`` are KNOWN FAIL, other failures NEW FAIL, and a passing
known-issue test is FIXED? (the finding may be fixed: re-check and remove the tag).

Tests of behaviour no specification states (``nospec:<kind>``: robustness, unspecified state x request cells)
are listed in their own section; they never change a specification verdict."""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from typing import Any

from smm_automation.pipeline.drift import (
    KNOWN_ISSUE_TAG,
    NOSPEC_TAG,
    PENDING_TAG,
    SDS_TAG,
    TestRef,
    check,
    hash_for,
    parse_hash_tags,
)

ORDER = ["FAIL", "NOT RUN", "UNCOVERED", "SKIP", "PARTIAL", "DEFERRED", "PASS", "NOT TESTABLE"]
COLORS = {
    "PASS": "#c8f0c8", "FAIL": "#f7c0c0", "SKIP": "#f3f3b0", "NOT RUN": "#e0e0e0", "UNCOVERED": "#f9d9a8",
    "DEFERRED": "#dde4f7", "NOT TESTABLE": "#eeeeee", "PARTIAL": "#e3f0b8",
    "NEW FAIL": "#f7a0a0", "KNOWN FAIL": "#f3d0d0", "FIXED?": "#b8e8f0", "UNREVIEWED": "#ffe08a", "VERIFIED": "#9ad89a",
}


def outcome(test: dict, product: bool = True) -> str:
    """PASS / SKIP / NEW FAIL / KNOWN FAIL / FIXED? of one executed test. Known issues are findings about appSMM:
    on the mock tier (not the product) they are ignored, so every mock failure is NEW."""
    known = product and bool(test.get("knownIssues"))
    if test["status"] == "FAIL":
        return "KNOWN FAIL" if known else "NEW FAIL"
    if test["status"] == "PASS" and known:
        return "FIXED?"
    return test["status"]


def is_product_tier(tier: str) -> bool:
    return not tier.startswith("mock")


def classify(tests: list[dict], product: bool = True) -> dict:
    """Test names per failure class, for the exit code of ``smm-auto run`` and the report header."""
    out: dict[str, list[str]] = {"new": [], "known": [], "fixed": []}
    for t in tests:
        o = outcome(t, product)
        key = {"NEW FAIL": "new", "KNOWN FAIL": "known", "FIXED?": "fixed"}.get(o)
        if key:
            out[key].append(t["name"])
    return out


def read_results(output_xml: Path) -> tuple[list[dict], dict]:
    from robot.api import ExecutionResult

    result = ExecutionResult(str(output_xml))
    tests: list[dict] = []

    def walk(suite):
        for t in suite.tests:
            tags = [str(x) for x in t.tags]
            plain, per_spec = parse_hash_tags(tags)
            tests.append({
                "name": t.name,
                "suite": suite.longname,
                "status": t.status,
                "message": t.message,
                "elapsed": round(t.elapsedtime / 1000, 2) if hasattr(t, "elapsedtime") else round(t.elapsed_time.total_seconds(), 2),
                "tags": tags,
                "specs": [int(m[1]) for x in tags if (m := SDS_TAG.match(x))],
                "hash": plain,
                "hashes": per_spec,
                "pending": any(x.lower() == PENDING_TAG for x in tags),
                "knownIssues": [m[1] for x in tags if (m := KNOWN_ISSUE_TAG.match(x))],
                "nospec": [m[1] for x in tags if (m := NOSPEC_TAG.match(x))],
            })
        for child in suite.suites:
            walk(child)

    walk(result.suite)
    meta: dict[str, Any] = {str(k): str(v) for k, v in result.suite.metadata.items()}
    stats = result.statistics.total
    meta["_totals"] = {"pass": stats.passed, "fail": stats.failed, "skip": stats.skipped}
    meta["_start"] = str(getattr(result.suite, "starttime", None) or getattr(result.suite, "start_time", ""))
    return tests, meta


def _spec_verdict(spec: dict, run: list[dict], known: list[TestRef]) -> str:
    if not spec["testable"]:
        return "NOT TESTABLE"
    if run:
        statuses = {t["status"] for t in run}
        if "FAIL" in statuses:
            return "FAIL"
        if "PASS" in statuses:
            ran = {t["name"] for t in run}
            not_run = [k for k in known if k.name not in ran]
            return "PARTIAL" if "SKIP" in statuses or not_run else "PASS"
        return "SKIP"
    if known:
        return "NOT RUN"
    if spec.get("deferredReason"):
        return "DEFERRED"
    return "UNCOVERED"


def _worst(verdicts: list[str]) -> str:
    relevant = [v for v in verdicts if v != "NOT TESTABLE"] or verdicts
    return min(relevant, key=ORDER.index) if relevant else "UNCOVERED"


def build_report(catalog: dict, tests: list[dict], meta: dict, known: list[TestRef], reviews: list[dict] | None = None) -> dict:
    tier = meta.get("Tier", "?")
    product = is_product_tier(tier)
    specs_out = []
    for key, spec in catalog["specifications"].items():
        sid = int(key)
        run = [t for t in tests if sid in t["specs"]]
        defined = [k for k in known if sid in k.specs]
        verdict = _spec_verdict(spec, run, defined)
        stale = [t["name"] for t in run if (h := hash_for(sid, t["specs"], t["hash"], t.get("hashes") or {})) and not spec["hash"].startswith(h)]
        unreviewed = sorted({t["name"] for t in run if t.get("pending")} | {k.name for k in defined if k.pending})
        outcomes = [outcome(t, product) for t in run]
        specs_out.append({
            "id": sid,
            "area": spec["area"],
            "verdict": verdict,
            "verified": verdict == "PASS" and not unreviewed,
            "unreviewed": unreviewed,
            "failClass": ("new" if "NEW FAIL" in outcomes else "known") if verdict == "FAIL" else None,
            "text": spec["text"],
            "state": spec.get("state"),
            "document": spec.get("document"),
            "satisfies": spec["satisfies"],
            "userStories": spec["userStories"],
            "explorativeTests": spec["explorativeTests"],
            "reason": spec.get("deferredReason") or spec.get("notTestableReason"),
            "stale": stale,
            "suspect": (spec.get("suspectCount") or 0) > 0,
            "tests": [
                {**{k: t[k] for k in ("name", "suite", "status", "message", "elapsed")},
                 "outcome": outcome(t, product), "pending": bool(t.get("pending")), "knownIssues": t.get("knownIssues", [])}
                for t in run
            ],
            "notRun": [k.name for k in defined if k.name not in {t["name"] for t in run}],
        })

    def roll_up(items: dict, link: str) -> list[dict]:
        out = []
        for key, item in items.items():
            linked = [s for s in specs_out if int(key) in s[link]]
            out.append({
                "id": int(key),
                "title": item.get("summary") or (item.get("text") or "")[:160],
                "state": item.get("state"),
                "verdict": _worst([s["verdict"] for s in linked]),
                "specifications": [s["id"] for s in linked],
            })
        return out

    summary = {v: sum(1 for s in specs_out if s["verdict"] == v) for v in ORDER}
    testable = [s for s in specs_out if s["verdict"] != "NOT TESTABLE"]
    return {
        "generatedAt": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "catalogGeneratedAt": catalog.get("generatedAt"),
        "scope": catalog.get("scope"),
        "tier": tier,
        "productEvidence": product,
        "metadata": {k: v for k, v in meta.items() if not k.startswith("_")},
        "totals": meta.get("_totals"),
        "summary": summary,
        "failures": classify(tests, product),
        "review": {
            "tests": len({t["name"] for t in tests} | {k.name for k in known}),
            "unreviewed": len({t["name"] for t in tests if t.get("pending")} | {k.name for k in known if k.pending}),
        },
        "coverage": {
            "testable": len(testable),
            "automated": sum(1 for s in testable if s["verdict"] in ("PASS", "PARTIAL", "FAIL", "SKIP", "NOT RUN")),
            "passed": summary["PASS"],
            "verified": sum(1 for s in specs_out if s["verified"]),
        },
        "specifications": sorted(specs_out, key=lambda s: (ORDER.index(s["verdict"]), s["area"], s["id"])),
        "nospec": [
            {**{k: t[k] for k in ("name", "suite", "status", "message", "elapsed")}, "outcome": outcome(t, product),
             "kind": ",".join(t.get("nospec") or []), "pending": bool(t.get("pending"))}
            for t in tests if not t["specs"] and t.get("nospec")
        ],
        "requirements": roll_up(catalog.get("requirements", {}), "satisfies"),
        "userStories": roll_up(catalog.get("userStories", {}), "userStories"),
        "drift": check(catalog, known, reviews) if known else None,
    }


def _badge(verdict: str) -> str:
    return f"<span class='v' style='background:{COLORS.get(verdict, '#fff')}'>{html.escape(verdict)}</span>"


def render_html(report: dict) -> str:
    e = html.escape
    meta_rows = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in report["metadata"].items())
    summary = " ".join(f"{_badge(v)} {n}" for v, n in report["summary"].items() if n)
    cov = report["coverage"]
    warn = ""
    if not report["productEvidence"]:
        warn = ("<div class='warn'><b>MOCK TIER:</b> these verdicts come from the scripted mock appSMM. They check the "
                "test framework and the tests themselves, they are <b>not evidence about the SMM product</b>.</div>")
    spec_rows = []
    for s in report["specifications"]:
        tests = "<br>".join(
            f"{_badge(t.get('outcome', t['status']))} {e(t['name'])} <small>({t['elapsed']} s)</small>"
            + (f" {_badge('UNREVIEWED')}" if t.get("pending") else "")
            + (f" <small>known issue {e(', '.join(t['knownIssues']))}</small>" if t.get("knownIssues") else "")
            + (f"<br><small class='msg'>{e(t['message'][:400])}</small>" if t["message"] else "")
            for t in s["tests"]
        )
        if s["notRun"]:
            tests += "".join(f"<br>{_badge('NOT RUN')} {e(n)}" for n in s["notRun"])
        notes = []
        if s["reason"]:
            notes.append(e(s["reason"]))
        if s["stale"]:
            notes.append("<b>STALE:</b> specification text changed after the test was written")
        if s["suspect"]:
            notes.append("<b>SUSPECT</b> in RV&amp;S")
        if s.get("failClass") == "known":
            notes.append("only known issues fail")
        links = " ".join([f"REQ-{r}" for r in s["satisfies"]] + [f"US-{u}" for u in s["userStories"]] + [f"ET-{x}" for x in s["explorativeTests"]])
        if s.get("verified"):
            review = _badge("VERIFIED")
        elif s.get("unreviewed"):
            review = f"{_badge('UNREVIEWED')}<br><small>{len(s['unreviewed'])} test(s) without human review</small>"
        else:
            review = "-"
        spec_rows.append(
            f"<tr><td>{_badge(s['verdict'])}</td><td>{review}</td><td><b>SDS-{s['id']}</b><br><small>{e(s['area'])}</small></td>"
            f"<td class='text'>{e(s['text'][:600])}</td><td>{tests or '-'}</td><td><small>{e(links)}</small><br>{'<br>'.join(notes)}</td></tr>"
        )
    failures = report.get("failures") or {"new": [], "known": [], "fixed": []}
    fail_html = ""
    if failures["new"]:
        fail_html += f"<div class='bad'><b>{len(failures['new'])} NEW FAIL:</b> " + ", ".join(e(n) for n in failures["new"]) + "</div>"
    if failures["fixed"]:
        fail_html += (f"<div class='warn'><b>{len(failures['fixed'])} FIXED?</b> known-issue tests now pass (re-check, then remove "
                      "the <code>known-issue:</code> tag): " + ", ".join(e(n) for n in failures["fixed"]) + "</div>")
    if failures["known"]:
        fail_html += f"<p>{len(failures['known'])} KNOWN FAIL (tagged <code>known-issue:</code>): " + ", ".join(e(n) for n in failures["known"]) + "</p>"
    rev = report.get("review") or {}
    if rev.get("unreviewed"):
        warn += (f"<div class='warn'><b>{rev['unreviewed']} of {rev['tests']} tests have no human review</b> "
                 "(<code>review:pending</code>). Their verdicts are shown, but a specification is only VERIFIED when "
                 "all its tests have been reviewed.</div>")

    def roll_table(items: list[dict], prefix: str) -> str:
        rows = "".join(
            f"<tr><td>{_badge(i['verdict'])}</td><td><b>{prefix}-{i['id']}</b></td><td>{e(str(i['title']))}</td>"
            f"<td>{e(str(i['state']))}</td><td>{', '.join(f'SDS-{s}' for s in i['specifications'])}</td></tr>"
            for i in sorted(items, key=lambda i: (ORDER.index(i["verdict"]), i["id"]))
        )
        return f"<table><tr><th>Verdict</th><th>ID</th><th>Title</th><th>RV&amp;S state</th><th>Specifications in scope</th></tr>{rows}</table>"

    nospec_rows = "".join(
        f"<tr><td>{_badge(t['outcome'])}</td><td>{e(t['kind'])}</td><td>{e(t['name'])}"
        + (f" {_badge('UNREVIEWED')}" if t.get("pending") else "")
        + f"</td><td><small>{e(t['message'][:600])}</small></td></tr>"
        for t in report.get("nospec") or []
    )
    nospec_html = ("<h2>Tests without a specification</h2><p>Robustness and unspecified state x request cells "
                   "(<code>nospec:</code>): they do not count for any specification; a SKIP carries what appSMM did.</p>"
                   f"<table><tr><th>Outcome</th><th>Kind</th><th>Test</th><th>Message / observation</th></tr>{nospec_rows}</table>"
                   if nospec_rows else "")
    drift = report.get("drift") or {}
    drift_html = ""
    for key in ("stale", "orphan", "untagged", "nohash", "unrecorded", "uncovered", "stateChanged", "linksChanged", "retired", "suspect", "pending"):
        if drift.get(key):
            drift_html += f"<h3>{e(key)} ({len(drift[key])})</h3><ul>" + "".join(f"<li><code>{e(json.dumps(i, ensure_ascii=False))}</code></li>" for i in drift[key]) + "</ul>"
    scope = report.get("scope") or {}
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>SMM traceability: {e(str(scope.get('name')))} ({e(report['tier'])})</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}} table{{border-collapse:collapse;margin:8px 0 24px;width:100%}}
td,th{{border:1px solid #ccc;padding:4px 6px;vertical-align:top;text-align:left;font-size:13px}} th{{background:#f4f4f4}}
.v{{display:inline-block;padding:1px 6px;border-radius:3px;font-weight:600;font-size:12px;white-space:nowrap}}
.warn{{background:#fff3cd;border:1px solid #e0b400;padding:10px;margin:12px 0}} .text{{max-width:520px}} .msg{{color:#900}}
.bad{{background:#f7c0c0;border:1px solid #c00;padding:10px;margin:12px 0}}
table.meta{{width:auto}}
</style></head><body>
<h1>SMM traceability report</h1>
<p>{e(str(scope.get('title') or ''))}</p>
{warn}
{fail_html}
<table class="meta"><tr><th>Tier</th><td>{e(report['tier'])}</td></tr>{meta_rows}
<tr><th>Catalog (RV&amp;S) from</th><td>{e(str(report['catalogGeneratedAt']))}</td></tr>
<tr><th>Report generated</th><td>{e(report['generatedAt'])}</td></tr>
<tr><th>Tests</th><td>{e(json.dumps(report['totals']))}</td></tr>
<tr><th>Coverage</th><td>{cov['automated']} of {cov['testable']} testable specifications automated, {cov['passed']} passed, {cov.get('verified', 0)} verified (passed and reviewed)</td></tr></table>
<p>{summary}</p>
<h2>Specifications</h2>
<table><tr><th>Verdict</th><th>Review</th><th>Spec</th><th>Text</th><th>Tests</th><th>Links / notes</th></tr>{''.join(spec_rows)}</table>
{nospec_html}
<h2>Requirements</h2>{roll_table(report['requirements'], 'REQ')}
<h2>User stories</h2>{roll_table(report['userStories'], 'US')}
<h2>Drift</h2>{drift_html or '<p>No drift problems.</p>'}
</body></html>"""


def write_report(catalog: dict, output_xml: Path, out_dir: Path, known: list[TestRef], reviews: list[dict] | None = None) -> dict:
    tests, meta = read_results(output_xml)
    report = build_report(catalog, tests, meta, known, reviews)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "traceability.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "traceability.html").write_text(render_html(report), encoding="utf-8")
    return report
