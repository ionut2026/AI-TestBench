"""Generation briefs: one self-contained Markdown file per specification, the input of the test-authoring
agent. A brief holds everything needed to write the Robot test: the specification, the requirement and user
story it serves, the Explorative Tests that show how people test it by hand, what the project's plugin adds
(e.g. the interface schemas of the messages involved), the available keywords and the project's authoring
rules (``[paths] brief_rules``)."""

from __future__ import annotations

from pathlib import Path

from grf import project
from grf.pipeline.drift import TestRef


def rules() -> str:
    """The project's authoring rules (``[paths] brief_rules``)."""
    return project.current().path("brief_rules").read_text(encoding="utf-8")


def keyword_docs() -> str:
    """Short documentation of the project's keyword libraries and resource files (``[paths] keyword_docs``, libdoc)."""
    from robot.libdocpkg import LibraryDocumentation

    p = project.current()
    lines = []
    for entry in p.raw_paths("keyword_docs"):
        source = str(p.root / entry) if "/" in entry or "\\" in entry or entry.endswith((".resource", ".robot")) else entry
        doc = LibraryDocumentation(source)
        lines.append(f"### {doc.name}\n")
        for kw in doc.keywords:
            args = ", ".join(str(a) for a in kw.args)
            short = (kw.short_doc or "").replace("\n", " ")
            lines.append(f"- `{kw.name}` ({args}): {short}")
        lines.append("")
    return "\n".join(lines)


def tags_for(spec: dict, multi: bool = False) -> list[str]:
    """Traceability tags of a specification; ``multi`` gives the per-specification hash form for tests that
    cover several specifications."""
    h = spec["hash"][:8]
    return [project.current().spec_tag(spec["id"]), f"spechash:{spec['id']}:{h}" if multi else f"spechash:{h}"]


def render_brief(catalog: dict, spec: dict, keywords: str, existing: list[TestRef], target: str) -> str:
    reqs = [catalog["requirements"].get(str(r)) for r in spec["satisfies"]]
    stories = [catalog["userStories"].get(str(u)) for u in spec["userStories"]]
    ets = [catalog["explorativeTests"].get(str(e)) for e in spec["explorativeTests"]]
    out = [
        f"# Generation brief: {project.current().spec_tag(spec['id'])}",
        "",
        f"- Document: {spec.get('document')} | State: {spec.get('state')} | Modified: {spec.get('modified')}",
        f"- Area: {spec['area']} | Target: `{target}`",
        f"- Tags: `{'`    `'.join(tags_for(spec))}`    `review:pending`"
        f" (in a test that also covers other specifications: `{tags_for(spec, multi=True)[1]}`)",
        f"- Messages mentioned: {', '.join(spec['messages']) or '(none)'}",
        "",
        "## Specification",
        "",
        spec["text"].strip(),
        "",
    ]
    if spec.get("deferredReason"):
        out += [f"> Deferred in the scope file: {spec['deferredReason']}", ""]
    out += ["## Requirements it satisfies", ""]
    out += [f"- **REQ-{r['id']}** [{r.get('state')}]: {(r.get('text') or '').strip()}" for r in reqs if r] or ["(none linked)"]
    out += ["", "## User stories", ""]
    for s in [s for s in stories if s]:
        out += [f"### US-{s['id']}: {s.get('summary')} [{s.get('state')}]", "", (s.get("description") or "").strip(), ""]
    if not any(stories):
        out += ["(none linked)", ""]
    out += ["## Explorative Tests (manual tests of this behaviour)", ""]
    for e in [e for e in ets if e]:
        out += [f"### ET-{e['id']}: {e.get('summary')} (result: {e.get('result')}, tested in: {e.get('testedIn')})", ""]
        for label, key in (("Preconditions", "preconditions"), ("Steps", "steps"), ("Expected", "expected")):
            if e.get(key):
                out += [f"**{label}:**", "", *[f"- {line}" for line in e[key]], ""]
    if not any(ets):
        out += ["(none linked)", ""]
    out += project.current().plugin.brief_sections(spec)
    out += ["## Existing tests for this specification", ""]
    out += [f"- `{t.name}` ({t.suite}){' [review:pending]' if t.pending else ''}" for t in existing] or ["(none)"]
    out += ["", "## Keywords", "", keywords, "## Authoring rules", "", rules()]
    return "\n".join(out)


def write_briefs(catalog: dict, out_dir: Path, tests: list[TestRef], only: list[int] | None = None, scope_name: str = "pilot") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = project.current()
    keywords = keyword_docs()
    target_pattern = str(p.data.get("paths", {}).get("brief_target", "robot/suites/{scope}/{area}.robot"))
    written = []
    for key, spec in catalog["specifications"].items():
        if only and int(key) not in only:
            continue
        if not spec["testable"]:
            continue
        existing = [t for t in tests if int(key) in t.specs]
        target = target_pattern.format(scope=scope_name, area=spec["area"])
        path = out_dir / f"{p.spec_tag(key)}.md"
        path.write_text(render_brief(catalog, spec, keywords, existing, target), encoding="utf-8")
        written.append(path)
    return written
