"""Generation briefs: one self-contained Markdown file per specification, the input of the test-authoring
agent (.github/agents/smm-test-author.agent.md). A brief holds everything needed to write the Robot test:
the specification, the requirement and user story it serves, the Explorative Tests that show how people
test it by hand, the ICD schemas of the messages involved, the available keywords and the authoring rules."""

from __future__ import annotations

import json
import os
from pathlib import Path

from smm_automation import FRAMEWORK_ROOT
from smm_automation.pipeline.drift import TestRef

RULES = """\
1. One test per observable behaviour of the specification; negative cases of the same specification are separate tests.
2. Name: `SDS-<id> <behaviour in title case>`. Put the specification text verbatim into `[Documentation]`
   and explain any interpretation you made on extra `...` lines.
3. Tags: exactly the tags under "Tags" below, plus `review:pending`, plus capability tags:
   `needs:twin` when the test drives the simulated hardware (Trigger Hardware Action, Wait For Hardware
   Command as a *required* step), `requires:restart` when it restarts appSMM or the broker. A test that
   covers several specifications carries `SDS-<id>` and `spechash:<id>:<hash>` for each of them
   (a plain `spechash:<hash>` only works on a single-specification test).
4. Only use keywords from the list below and Robot BuiltIn/Collections. No `Sleep` (`smm-auto lint` rejects it):
   wait for messages (`Wait For Message`, `Wait For Message Sequence`, `Wait For System State`), prove absence
   with `Message Should Not Arrive`, simulate a lost Bridge with `Interrupt Bridge Connection`. Each wait consumes
   the message it matched (two waits need two messages); consecutive waits do not imply order, so assert order
   with `Wait For Message Sequence` (or `since=last`).
5. Preconditions with `Bring SMM To State`, `Bring SMM To E-Stop With Shutdown` or
   `Restart appSMM And Wait Until NotInitialized`; they are not verification steps.
6. Assert what the specification says and nothing more: states, message order, fields, topic (`@topic=/is/iw/tx`),
   ICD schema validity (`Received Messages Should Be Schema Valid`). Values the specification leaves open
   (e.g. "EventId": ??) are logged, not asserted.
7. Timeouts come from variables (`${RESPONSE_TIMEOUT}`, `${STARTUP_TIMEOUT}`, `${INIT_TIMEOUT}`,
   `${RECOVER_TIMEOUT}`, `${QUIET_PERIOD}`, `${SHORT_QUIET_PERIOD}`). A time stated by the specification goes into a
   suite variable `${SDS_<id>_LIMIT}` (quote it in the documentation); literal `timeout=`/`duration=` values are
   rejected by `smm-auto lint`.
8. If the behaviour cannot be observed through the ICD or the hardware twin, do not write a test: say why,
   so the specification can be listed under `[deferred]` in the scope file.
9. Add the test to the suite file named under "Target"; keep the suite's Settings unchanged.
10. A human reviews every generated test against the specification and removes `review:pending`.
"""


def keyword_docs() -> str:
    """Short documentation of the SMMTestbench and smm.resource keywords (libdoc)."""
    from robot.libdocpkg import LibraryDocumentation

    lines = []
    for source in ("smm_automation.SMMTestbench", str(FRAMEWORK_ROOT / "robot" / "resources" / "smm.resource")):
        doc = LibraryDocumentation(source)
        lines.append(f"### {doc.name}\n")
        for kw in doc.keywords:
            args = ", ".join(str(a) for a in kw.args)
            short = (kw.short_doc or "").replace("\n", " ")
            lines.append(f"- `{kw.name}` ({args}): {short}")
        lines.append("")
    return "\n".join(lines)


def schema_dir() -> Path:
    lock = json.loads((FRAMEWORK_ROOT / "testbench.lock.json").read_text(encoding="utf-8"))
    return Path(os.environ.get("SMM_TESTBENCH_DIR") or lock["defaultDir"]) / "simulator" / "resources" / "schemas"


def tags_for(spec: dict, multi: bool = False) -> list[str]:
    """Traceability tags of a specification; ``multi`` gives the per-specification hash form for tests that
    cover several specifications."""
    h = spec["hash"][:8]
    return [f"SDS-{spec['id']}", f"spechash:{spec['id']}:{h}" if multi else f"spechash:{h}"]


def render_brief(catalog: dict, spec: dict, keywords: str, existing: list[TestRef], target: str) -> str:
    reqs = [catalog["requirements"].get(str(r)) for r in spec["satisfies"]]
    stories = [catalog["userStories"].get(str(u)) for u in spec["userStories"]]
    ets = [catalog["explorativeTests"].get(str(e)) for e in spec["explorativeTests"]]
    out = [
        f"# Generation brief: SDS-{spec['id']}",
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
    out += ["## ICD schemas", ""]
    for name in spec["messages"]:
        path = schema_dir() / f"Icd{name}_schema.json"
        if path.exists():
            out += [f"### {name}", "", "```json", path.read_text(encoding="utf-8").strip(), "```", ""]
    out += ["## Existing tests for this specification", ""]
    out += [f"- `{t.name}` ({t.suite}){' [review:pending]' if t.pending else ''}" for t in existing] or ["(none)"]
    out += ["", "## Keywords", "", keywords, "## Authoring rules", "", RULES]
    return "\n".join(out)


def write_briefs(catalog: dict, out_dir: Path, tests: list[TestRef], only: list[int] | None = None, scope_name: str = "pilot") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    keywords = keyword_docs()
    written = []
    for key, spec in catalog["specifications"].items():
        if only and int(key) not in only:
            continue
        if not spec["testable"]:
            continue
        existing = [t for t in tests if int(key) in t.specs]
        target = f"robot/suites/{scope_name}/{spec['area']}.robot"
        path = out_dir / f"SDS-{key}.md"
        path.write_text(render_brief(catalog, spec, keywords, existing, target), encoding="utf-8")
        written.append(path)
    return written
