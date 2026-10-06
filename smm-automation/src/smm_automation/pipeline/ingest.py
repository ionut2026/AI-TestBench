"""Builds the test catalog from RV&S: specifications in scope with their requirements, user stories and
Explorative Tests, plus a content hash of every specification text (the drift anchor of each test)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tomllib
import unicodedata
from pathlib import Path
from typing import Any

from smm_automation import FRAMEWORK_ROOT
from smm_automation.pipeline.rvs import RvsClient, run

REF = re.compile(r"^(?P<id>\d+)\s+\((?P<type>[^)]+)\)\s*(?P<summary>.*?)\s*(?:\[(?P<state>[^\]]+)\])?$")
MESSAGE_NAME = re.compile(r"\b([A-Z][A-Za-z]+?(?:Request|Response|Notification))\b")
ET_SECTIONS = [
    ("preconditions", r"pre-?conditions?\s*:"),
    ("steps", r"steps?\s*:"),
    ("expected", r"expected\s+results?\s*:"),
    ("actual", r"actual\s+results?\s*:"),
]


# ---------------------------------------------------------------------- pure helpers (unit tested)


def normalize_text(text: str | None) -> str:
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def spec_hash(text: str | None) -> str:
    """SHA-256 of the normalized specification text. Tests carry the first 8 hex digits as ``spechash:``."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def parse_ref(ref: str) -> dict:
    """'2386628 (Requirement) [Accepted]' or '2404051 (ASD-User Story) StartUp: ... [Tested]'."""
    m = REF.match(ref.strip())
    if not m:
        return {"id": None, "raw": ref}
    return {"id": int(m["id"]), "type": m["type"], "summary": m["summary"] or None, "state": m["state"]}


def refs(item: dict, field: str) -> list[dict]:
    value = item.get(field) or []
    if isinstance(value, str):
        value = [value]
    return [r for r in (parse_ref(v) for v in value) if r.get("id")]


def parse_explorative_test(description: str | None) -> dict:
    """Splits an Explorative Test description into preconditions, steps, expected (and actual) results."""
    text = (description or "").replace("\r\n", "\n")
    found = []
    for key, pattern in ET_SECTIONS:
        m = re.search(rf"(?im)^\s*{pattern}", text)
        if m:
            found.append((m.start(), m.end(), key))
    found.sort()
    out: dict[str, Any] = {k: [] for k, _ in ET_SECTIONS}
    for i, (_start, end, key) in enumerate(found):
        stop = found[i + 1][0] if i + 1 < len(found) else len(text)
        lines = [ln.strip() for ln in text[end:stop].split("\n")]
        out[key] = [ln for ln in lines if ln]
    if not found:
        out["preconditions"] = []
        out["steps"] = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return out


def message_names(text: str | None, known: set[str] | None = None) -> list[str]:
    names = []
    for name in MESSAGE_NAME.findall(text or ""):
        if (not known or name in known) and name not in names:
            names.append(name)
    return names


def classify_area(text: str, areas: dict[str, list[str]]) -> str | None:
    lowered = text.lower()
    for area, words in areas.items():
        if any(w.lower() in lowered for w in words):
            return area
    return None


def known_icd_messages() -> set[str]:
    lock = json.loads((FRAMEWORK_ROOT / "testbench.lock.json").read_text(encoding="utf-8"))
    tb = Path(os.environ.get("SMM_TESTBENCH_DIR") or lock["defaultDir"])
    schemas = tb / "simulator" / "resources" / "schemas"
    return {p.name[3:-12] for p in schemas.glob("Icd*_schema.json")} if schemas.exists() else set()


# ---------------------------------------------------------------------- ingestion


def load_scope(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


async def _ingest(scope: dict, server_js: Path | None) -> dict:
    async with RvsClient(server_js) as rvs:
        ids: list[int] = [int(i) for i in scope.get("specifications", [])]
        for search in scope.get("searches", []):
            for item in await rvs.search(**search):
                if int(item["ID"]) not in ids:
                    ids.append(int(item["ID"]))
        specs = await rvs.get_items(ids, maxFieldLength=20000)

        req_ids, story_ids, et_ids = set(), set(), set()
        for s in specs:
            req_ids |= {r["id"] for r in refs(s, "Satisfies")}
            story_ids |= {r["id"] for r in refs(s, "Described In")}
            et_ids |= {r["id"] for r in refs(s, "Relevant Explorative Test")}
        requirements = await rvs.get_items(sorted(req_ids), maxFieldLength=8000) if req_ids else []
        stories = await rvs.get_items(sorted(story_ids), maxFieldLength=8000) if story_ids else []
        for item in [*requirements, *stories]:
            et_ids |= {r["id"] for r in refs(item, "Relevant Explorative Test")}
        ets = await rvs.get_items(sorted(et_ids), maxFieldLength=20000) if et_ids else []
    return build_catalog(scope, specs, requirements, stories, ets)


def build_catalog(scope: dict, specs: list[dict], requirements: list[dict], stories: list[dict], ets: list[dict]) -> dict:
    known = known_icd_messages()
    not_testable = {int(k): v for k, v in (scope.get("not_testable") or {}).items()}
    deferred = {int(k): v for k, v in (scope.get("deferred") or {}).items()}
    areas = scope.get("areas") or {}

    et_parent: dict[int, set[int]] = {}
    for item in [*specs, *requirements, *stories]:
        for r in refs(item, "Relevant Explorative Test"):
            et_parent.setdefault(r["id"], set()).add(int(item["ID"]))

    out_specs = {}
    for s in specs:
        sid = int(s["ID"])
        text = s.get("Text") or ""
        req_refs = refs(s, "Satisfies")
        story_refs = refs(s, "Described In")
        linked_ets = sorted(
            {r["id"] for r in refs(s, "Relevant Explorative Test")}
            | {e for r in [*req_refs, *story_refs] for e, parents in et_parent.items() if r["id"] in parents}
        )
        out_specs[str(sid)] = {
            "id": sid,
            "state": s.get("State"),
            "project": s.get("Project"),
            "document": s.get("Computed Document Summary"),
            "documentId": s.get("Document ID"),
            "text": text,
            "hash": spec_hash(text),
            "modified": s.get("Modified Date"),
            "traceStatus": s.get("Trace Status"),
            "suspectCount": s.get("Suspect Count", 0),
            "verificationMethod": s.get("Verification method"),
            "satisfies": [r["id"] for r in req_refs],
            "userStories": [r["id"] for r in story_refs],
            "explorativeTests": linked_ets,
            "messages": message_names(text, known or None),
            "area": classify_area(text, areas) or "other",
            "testable": sid not in not_testable,
            "notTestableReason": not_testable.get(sid),
            "deferredReason": deferred.get(sid),
        }

    out_reqs = {
        str(r["ID"]): {
            "id": int(r["ID"]),
            "state": r.get("State"),
            "text": r.get("Text") or r.get("Summary"),
            "document": r.get("Computed Document Summary"),
            "specifications": sorted(int(k) for k, v in out_specs.items() if int(r["ID"]) in v["satisfies"]),
        }
        for r in requirements
    }
    out_stories = {
        str(s["ID"]): {
            "id": int(s["ID"]),
            "summary": s.get("Summary"),
            "state": s.get("State"),
            "description": s.get("Description"),
            "explorativeTests": [r["id"] for r in refs(s, "Relevant Explorative Test")],
        }
        for s in stories
    }
    out_ets = {}
    for e in ets:
        parsed = parse_explorative_test(e.get("Description"))
        out_ets[str(e["ID"])] = {
            "id": int(e["ID"]),
            "summary": e.get("Summary"),
            "state": e.get("State"),
            "result": e.get("ET - Result"),
            "testedIn": e.get("Tested in Version - Modification") or e.get("Tested in Version"),
            "of": [r["id"] for r in refs(e, "Explorative Test of")] or sorted(et_parent.get(int(e["ID"]), [])),
            "preconditions": parsed["preconditions"],
            "steps": parsed["steps"],
            "expected": parsed["expected"],
        }
    return {
        "schema": 1,
        "scope": {k: scope.get(k) for k in ("name", "title")},
        "generatedAt": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "source": "Windchill RV&S (windchill MCP server, read-only)",
        "counts": {"specifications": len(out_specs), "requirements": len(out_reqs), "userStories": len(out_stories), "explorativeTests": len(out_ets)},
        "specifications": out_specs,
        "requirements": out_reqs,
        "userStories": out_stories,
        "explorativeTests": out_ets,
    }


def ingest(scope_file: Path, out_file: Path, server_js: Path | None = None) -> dict:
    catalog = run(_ingest(load_scope(scope_file), server_js))
    previous = load_catalog(out_file) if out_file.exists() else None
    if previous:
        catalog["changes"] = diff_catalogs(previous, catalog)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog


def load_catalog(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def diff_catalogs(old: dict, new: dict) -> dict:
    """What changed in RV&S since the previous ingestion (specification level)."""
    o, n = old.get("specifications", {}), new.get("specifications", {})
    return {
        "since": old.get("generatedAt"),
        "added": sorted(int(k) for k in n.keys() - o.keys()),
        "removed": sorted(int(k) for k in o.keys() - n.keys()),
        "textChanged": sorted(int(k) for k in n.keys() & o.keys() if n[k]["hash"] != o[k]["hash"]),
        "stateChanged": sorted(int(k) for k in n.keys() & o.keys() if n[k]["state"] != o[k]["state"]),
    }
