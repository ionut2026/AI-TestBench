"""Traceability report: Robot results (output.xml) joined with the RV&S catalog.

Per specification: PASS / FAIL / SKIP / NOT RUN (tests exist but were excluded from this run) /
UNCOVERED / DEFERRED / NOT TESTABLE, plus stale-hash warnings. Per requirement and user story: the
worst verdict of their in-scope specifications."""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

from smm_automation.pipeline.drift import HASH_TAG, SDS_TAG, TestRef, check

ORDER = ["FAIL", "NOT RUN", "UNCOVERED", "SKIP", "DEFERRED", "PASS", "NOT TESTABLE"]
COLORS = {"PASS": "#c8f0c8", "FAIL": "#f7c0c0", "SKIP": "#f3f3b0", "NOT RUN": "#e0e0e0", "UNCOVERED": "#f9d9a8", "DEFERRED": "#dde4f7", "NOT TESTABLE": "#eeeeee"}


def read_results(output_xml: Path) -> tuple[list[dict], dict]:
    from robot.api import ExecutionResult

    result = ExecutionResult(str(output_xml))
    tests: list[dict] = []

    def walk(suite):
        for t in suite.tests:
            tags = [str(x) for x in t.tags]
            tests.append({
                "name": t.name,
                "suite": suite.longname,
                "status": t.status,
                "message": t.message,
                "elapsed": round(t.elapsedtime / 1000, 2) if hasattr(t, "elapsedtime") else round(t.elapsed_time.total_seconds(), 2),
                "tags": tags,
                "specs": [int(m[1]) for x in tags if (m := SDS_TAG.match(x))],
                "hash": next((m[1].lower() for x in tags if (m := HASH_TAG.match(x))), None),
            })
        for child in suite.suites:
            walk(child)

    walk(result.suite)
    meta = {str(k): str(v) for k, v in result.suite.metadata.items()}
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
            return "PASS"
        return "SKIP"
    if known:
        return "NOT RUN"
    if spec.get("deferredReason"):
        return "DEFERRED"
    return "UNCOVERED"


def _worst(verdicts: list[str]) -> str:
    relevant = [v for v in verdicts if v != "NOT TESTABLE"] or verdicts
    return min(relevant, key=ORDER.index) if relevant else "UNCOVERED"


def build_report(catalog: dict, tests: list[dict], meta: dict, known: list[TestRef]) -> dict:
    specs_out = []
    for key, spec in catalog["specifications"].items():
        sid = int(key)
        run = [t for t in tests if sid in t["specs"]]
        defined = [k for k in known if sid in k.specs]
        verdict = _spec_verdict(spec, run, defined)
        stale = [t["name"] for t in run if t["hash"] and not spec["hash"].startswith(t["hash"])]
        specs_out.append({
            "id": sid,
            "area": spec["area"],
            "verdict": verdict,
            "text": spec["text"],
            "state": spec.get("state"),
            "document": spec.get("document"),
            "satisfies": spec["satisfies"],
            "userStories": spec["userStories"],
            "explorativeTests": spec["explorativeTests"],
            "reason": spec.get("deferredReason") or spec.get("notTestableReason"),
            "stale": stale,
            "suspect": (spec.get("suspectCount") or 0) > 0,
            "tests": [{k: t[k] for k in ("name", "suite", "status", "message", "elapsed")} for t in run],
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

    tier = meta.get("Tier", "?")
    summary = {v: sum(1 for s in specs_out if s["verdict"] == v) for v in ORDER}
    testable = [s for s in specs_out if s["verdict"] != "NOT TESTABLE"]
    return {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "catalogGeneratedAt": catalog.get("generatedAt"),
        "scope": catalog.get("scope"),
        "tier": tier,
        "productEvidence": not tier.startswith("mock"),
        "metadata": {k: v for k, v in meta.items() if not k.startswith("_")},
        "totals": meta.get("_totals"),
        "summary": summary,
        "coverage": {
            "testable": len(testable),
            "automated": sum(1 for s in testable if s["verdict"] in ("PASS", "FAIL", "SKIP", "NOT RUN")),
            "passed": summary["PASS"],
        },
        "specifications": sorted(specs_out, key=lambda s: (ORDER.index(s["verdict"]), s["area"], s["id"])),
        "requirements": roll_up(catalog.get("requirements", {}), "satisfies"),
        "userStories": roll_up(catalog.get("userStories", {}), "userStories"),
        "drift": check(catalog, known) if known else None,
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
            f"{_badge(t['status'])} {e(t['name'])} <small>({t['elapsed']} s)</small>" + (f"<br><small class='msg'>{e(t['message'][:400])}</small>" if t["message"] else "")
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
        links = " ".join([f"REQ-{r}" for r in s["satisfies"]] + [f"US-{u}" for u in s["userStories"]] + [f"ET-{x}" for x in s["explorativeTests"]])
        spec_rows.append(
            f"<tr><td>{_badge(s['verdict'])}</td><td><b>SDS-{s['id']}</b><br><small>{e(s['area'])}</small></td>"
            f"<td class='text'>{e(s['text'][:600])}</td><td>{tests or '-'}</td><td><small>{e(links)}</small><br>{'<br>'.join(notes)}</td></tr>"
        )

    def roll_table(items: list[dict], prefix: str) -> str:
        rows = "".join(
            f"<tr><td>{_badge(i['verdict'])}</td><td><b>{prefix}-{i['id']}</b></td><td>{e(str(i['title']))}</td>"
            f"<td>{e(str(i['state']))}</td><td>{', '.join(f'SDS-{s}' for s in i['specifications'])}</td></tr>"
            for i in sorted(items, key=lambda i: (ORDER.index(i["verdict"]), i["id"]))
        )
        return f"<table><tr><th>Verdict</th><th>ID</th><th>Title</th><th>RV&amp;S state</th><th>Specifications in scope</th></tr>{rows}</table>"

    drift = report.get("drift") or {}
    drift_html = ""
    for key in ("stale", "orphan", "untagged", "nohash", "uncovered", "suspect", "pending"):
        if drift.get(key):
            drift_html += f"<h3>{e(key)} ({len(drift[key])})</h3><ul>" + "".join(f"<li><code>{e(json.dumps(i, ensure_ascii=False))}</code></li>" for i in drift[key]) + "</ul>"
    scope = report.get("scope") or {}
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>SMM traceability: {e(str(scope.get('name')))} ({e(report['tier'])})</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}} table{{border-collapse:collapse;margin:8px 0 24px;width:100%}}
td,th{{border:1px solid #ccc;padding:4px 6px;vertical-align:top;text-align:left;font-size:13px}} th{{background:#f4f4f4}}
.v{{display:inline-block;padding:1px 6px;border-radius:3px;font-weight:600;font-size:12px;white-space:nowrap}}
.warn{{background:#fff3cd;border:1px solid #e0b400;padding:10px;margin:12px 0}} .text{{max-width:520px}} .msg{{color:#900}}
table.meta{{width:auto}}
</style></head><body>
<h1>SMM traceability report</h1>
<p>{e(str(scope.get('title') or ''))}</p>
{warn}
<table class="meta"><tr><th>Tier</th><td>{e(report['tier'])}</td></tr>{meta_rows}
<tr><th>Catalog (RV&amp;S) from</th><td>{e(str(report['catalogGeneratedAt']))}</td></tr>
<tr><th>Report generated</th><td>{e(report['generatedAt'])}</td></tr>
<tr><th>Tests</th><td>{e(json.dumps(report['totals']))}</td></tr>
<tr><th>Coverage</th><td>{cov['automated']} of {cov['testable']} testable specifications automated, {cov['passed']} passed</td></tr></table>
<p>{summary}</p>
<h2>Specifications</h2>
<table><tr><th>Verdict</th><th>Spec</th><th>Text</th><th>Tests</th><th>Links / notes</th></tr>{''.join(spec_rows)}</table>
<h2>Requirements</h2>{roll_table(report['requirements'], 'REQ')}
<h2>User stories</h2>{roll_table(report['userStories'], 'US')}
<h2>Drift</h2>{drift_html or '<p>No drift problems.</p>'}
</body></html>"""


def write_report(catalog: dict, output_xml: Path, out_dir: Path, known: list[TestRef]) -> dict:
    tests, meta = read_results(output_xml)
    report = build_report(catalog, tests, meta, known)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "traceability.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "traceability.html").write_text(render_html(report), encoding="utf-8")
    return report
