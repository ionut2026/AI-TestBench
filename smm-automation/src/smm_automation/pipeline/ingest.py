"""Builds the test catalog from RV&S: specifications in scope with their requirements, user stories and
Explorative Tests, plus a content hash of every specification text (the drift anchor of each test).

Hash version 2: the specification text is fetched as RV&S rich text (HTML) and converted to text **by this module**
(``html_to_text``), then normalised (``normalize_text``: Unicode NFKC, typographic quotes/dashes, zero-width
characters, whitespace). The hash therefore does not depend on how the MCP server converts rich text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import tomllib
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from smm_automation import icd
from smm_automation.pipeline.rvs import RvsClient, run

HASH_VERSION = 2
CATALOG_SCHEMA = 2
DEFAULT_RETIRED_STATES = ["Rejected", "To Be Deleted", "Deleted"]
REF = re.compile(r"^(?P<id>\d+)\s+\((?P<type>[^)]+)\)\s*(?P<summary>.*?)\s*(?:\[(?P<state>[^\]]+)\])?$")
MESSAGE_NAME = re.compile(r"\b([A-Z][A-Za-z]+?(?:Request|Response|Notification))\b")
ET_SECTIONS = [
    ("preconditions", r"pre-?conditions?\s*:"),
    ("steps", r"steps?\s*:"),
    ("expected", r"expected\s+results?\s*:"),
    ("actual", r"actual\s+results?\s*:"),
]
MKS_HTML = "<!-- MKS HTML -->"
_PUNCTUATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u200b": None, "\u200c": None, "\u200d": None, "\u2060": None, "\ufeff": None, "\u00ad": None,
})
_BLOCK_TAGS = {"p", "div", "br", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr"}


# ---------------------------------------------------------------------- pure helpers (unit tested)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """RV&S rich text (``<!-- MKS HTML -->…``) to plain text with line breaks; plain text is returned unchanged."""
    value = value or ""
    if MKS_HTML not in value and not re.search(r"</?(p|div|br|span|b|i|u|li|ul|ol|table|tr|td|font|strong|em)\b", value, re.I):
        return value.strip()
    parser = _TextExtractor()
    parser.feed(value.replace(MKS_HTML, ""))
    parser.close()
    text = "".join(parser.parts).replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def normalize_text(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_PUNCTUATION)
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


def scope_specifications(scope: dict) -> dict[int, str | None]:
    """Specification ids of the scope with their area (suite). ``specifications`` is either a table
    ``area = [ids]`` (explicit, preferred) or a plain list (area guessed from keywords, with a warning)."""
    raw = scope.get("specifications") or []
    out: dict[int, str | None] = {}
    if isinstance(raw, dict):
        for area, ids in raw.items():
            for sid in ids:
                if int(sid) in out and out[int(sid)] != area:
                    raise ValueError(f"SDS-{sid} is listed under two areas: {out[int(sid)]} and {area}")
                out[int(sid)] = area
    else:
        for sid in raw:
            out.setdefault(int(sid), None)
    return out


def link_baseline(spec: dict) -> dict:
    return {"state": spec.get("state"), "satisfies": sorted(spec.get("satisfies") or []), "userStories": sorted(spec.get("userStories") or [])}


# ---------------------------------------------------------------------- ingestion


def load_scope(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


async def _ingest(scope: dict, server_js: Path | None) -> dict:
    async with RvsClient(server_js) as rvs:
        assigned = scope_specifications(scope)
        for search in scope.get("searches", []):
            search = dict(search)
            area = search.pop("area", None)
            for item in await rvs.search(**search):
                assigned.setdefault(int(item["ID"]), area)
        ids = list(assigned)
        specs = await rvs.get_items(ids, maxFieldLength=50000, richText=True)

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
    return build_catalog(scope, specs, requirements, stories, ets, assigned)


def build_catalog(scope: dict, specs: list[dict], requirements: list[dict], stories: list[dict], ets: list[dict],
                  assigned: dict[int, str | None] | None = None, known: set[str] | None = None) -> dict:
    """``specs`` carry ``Text`` as RV&S rich text (or plain text); ``assigned`` maps spec id -> area from the scope;
    ``known`` are the ICD message names (default: from the automation service)."""
    known = icd.message_names() if known is None else known
    not_testable = {int(k): v for k, v in (scope.get("not_testable") or {}).items()}
    deferred = {int(k): v for k, v in (scope.get("deferred") or {}).items()}
    areas = scope.get("areas") or {}
    assigned = assigned if assigned is not None else scope_specifications(scope)

    et_parent: dict[int, set[int]] = {}
    for item in [*specs, *requirements, *stories]:
        for r in refs(item, "Relevant Explorative Test"):
            et_parent.setdefault(r["id"], set()).add(int(item["ID"]))

    out_specs = {}
    for s in specs:
        sid = int(s["ID"])
        text = html_to_text(s.get("Text"))
        req_refs = refs(s, "Satisfies")
        story_refs = refs(s, "Described In")
        linked_ets = sorted(
            {r["id"] for r in refs(s, "Relevant Explorative Test")}
            | {e for r in [*req_refs, *story_refs] for e, parents in et_parent.items() if r["id"] in parents}
        )
        area, area_source = assigned.get(sid), "scope"
        if not area:
            area, area_source = classify_area(text, areas), "keywords"
            if not area:
                area, area_source = "other", "none"
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
            "area": area,
            "areaSource": area_source,
            "testable": sid not in not_testable,
            "notTestableReason": not_testable.get(sid),
            "deferredReason": deferred.get(sid),
        }
        out_specs[str(sid)]["baseline"] = link_baseline(out_specs[str(sid)])

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
        "schema": CATALOG_SCHEMA,
        "hashVersion": HASH_VERSION,
        "scope": {k: scope.get(k) for k in ("name", "title")},
        "retiredStates": list(scope.get("retired_states") or DEFAULT_RETIRED_STATES),
        "generatedAt": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "source": "Windchill RV&S (windchill MCP server, read-only)",
        "counts": {"specifications": len(out_specs), "requirements": len(out_reqs), "userStories": len(out_stories), "explorativeTests": len(out_ets)},
        "specifications": out_specs,
        "requirements": out_reqs,
        "userStories": out_stories,
        "explorativeTests": out_ets,
    }


def carry_baselines(previous: dict | None, catalog: dict) -> None:
    """Keeps the accepted state/link baseline of every specification across ingestions: a change in RV&S stays
    visible in ``drift`` until someone runs ``smm-auto accept``. Catalogs without baselines (schema 1) use their
    own state and links as the baseline, so the first new ingestion shows what changed since then."""
    old = (previous or {}).get("specifications", {})
    for key, spec in catalog["specifications"].items():
        if key in old:
            spec["baseline"] = old[key].get("baseline") or link_baseline(old[key])


def accept_changes(catalog: dict, ids: list[int] | None) -> list[int]:
    """Accepts the current state and links of these specifications (all when ``ids`` is None) as the new baseline."""
    accepted = []
    stamp = dt.date.today().isoformat()
    for key, spec in catalog["specifications"].items():
        if ids is not None and int(key) not in ids:
            continue
        current = link_baseline(spec)
        if {k: v for k, v in (spec.get("baseline") or {}).items() if k != "acceptedAt"} != current:
            spec["baseline"] = {**current, "acceptedAt": stamp}
            accepted.append(int(key))
    return accepted


def ingest(scope_file: Path, out_file: Path, server_js: Path | None = None) -> dict:
    catalog = run(_ingest(load_scope(scope_file), server_js))
    previous = load_catalog(out_file) if out_file.exists() else None
    carry_baselines(previous, catalog)
    if previous:
        catalog["changes"] = diff_catalogs(previous, catalog)
    for key, spec in catalog["specifications"].items():
        if spec.get("areaSource") != "scope":
            print(f"WARNING: SDS-{key} has no area in the scope file; guessed '{spec['area']}' from keywords "
                  f"(list it under its area in [specifications])", file=sys.stderr)
    write_catalog(out_file, catalog)
    return catalog


def write_catalog(path: Path, catalog: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def load_catalog(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def diff_catalogs(old: dict, new: dict) -> dict:
    """What changed in RV&S since the previous ingestion (specification level). When the hash version changed,
    texts are compared in normalised form instead of by hash."""
    o, n = old.get("specifications", {}), new.get("specifications", {})
    same_hash = old.get("hashVersion", 1) == new.get("hashVersion", 1)

    def text_changed(k: str) -> bool:
        if same_hash:
            return bool(n[k]["hash"] != o[k]["hash"])
        return normalize_text(n[k].get("text")) != normalize_text(o[k].get("text"))

    def links(s: dict) -> tuple:
        return sorted(s.get("satisfies") or []), sorted(s.get("userStories") or [])

    return {
        "since": old.get("generatedAt"),
        "added": sorted(int(k) for k in n.keys() - o.keys()),
        "removed": sorted(int(k) for k in o.keys() - n.keys()),
        "textChanged": sorted(int(k) for k in n.keys() & o.keys() if text_changed(k)),
        "stateChanged": sorted(int(k) for k in n.keys() & o.keys() if n[k]["state"] != o[k]["state"]),
        "linksChanged": sorted(int(k) for k in n.keys() & o.keys() if links(n[k]) != links(o[k])),
    }
