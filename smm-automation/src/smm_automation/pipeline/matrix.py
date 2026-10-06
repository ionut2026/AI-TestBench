"""State x request matrix (plan 5.1/5.2): ``catalog/state-matrix.toml`` -> generated Robot suite + questions.

The model lists, for every precondition state and request, what the specifications say (response, next
state, a COP command that must not be sent) and what they leave open. ``smm-auto matrix`` writes

* ``robot/suites/pilot/state_matrix.robot``: one templated test per cell that no hand-written test covers
  (``covered_by``). Cells with specifications carry ``SDS-<id>`` and ``spechash`` tags and quote the
  specification; cells without any specification are tagged ``nospec:state-matrix`` and only observe.
* ``catalog/state-matrix.questions.md``: the matrix overview and the open questions for the specification owner.

``smm-auto matrix --check`` fails when the files on disk differ from what the model generates (CI).
"""

from __future__ import annotations

import re
import textwrap
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from smm_automation.pipeline.drift import PENDING_TAG, TestRef, counts_as_review

NOSPEC_TAG = "nospec:state-matrix"
EMPTY = "${EMPTY}"
SEP = "    "
LINE = 100


class MatrixError(ValueError):
    pass


@dataclass
class Cell:
    state: str
    request: str
    specs: list[int] = field(default_factory=list)
    body: str = ""
    response: str = ""
    next: str = ""
    no_command: str = ""
    questions: list[str] = field(default_factory=list)
    covered_by: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def generated(self) -> bool:
        return not self.covered_by

    @property
    def specified(self) -> bool:
        return bool(self.response or self.next or self.no_command)

    @property
    def test_name(self) -> str:
        prefix = f"SDS-{self.specs[0]} " if self.specs else ""
        return f"{prefix}Matrix {self.request} In {self.state}"


@dataclass
class Model:
    states: list[str]
    requests: list[str]
    cells: list[Cell]


def load_model(path: Path) -> Model:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    known = set(Cell.__dataclass_fields__)
    cells = []
    for i, c in enumerate(raw.get("cell", []), 1):
        unknown = set(c) - known
        if unknown:
            raise MatrixError(f"cell {i}: unknown field(s) {', '.join(sorted(unknown))}")
        cells.append(Cell(**c))
    return Model(list(raw.get("states", [])), list(raw.get("requests", [])), cells)


def validate(model: Model, catalog: dict, tests: list[TestRef] | None = None) -> list[str]:
    """Problems of the model: unknown states/requests/specifications, missing or duplicate cells,
    ``covered_by`` tests that do not exist, cells that neither specify nor ask anything."""
    errors = []
    seen: dict[tuple[str, str], int] = {}
    names = {t.name for t in tests} if tests is not None else None
    specs = catalog.get("specifications", {})
    for c in model.cells:
        where = f"{c.request} in {c.state}"
        if c.state not in model.states:
            errors.append(f"{where}: unknown state {c.state}")
        if c.request not in model.requests:
            errors.append(f"{where}: unknown request {c.request}")
        seen[(c.state, c.request)] = seen.get((c.state, c.request), 0) + 1
        errors += [f"{where}: SDS-{s} is not in the catalog" for s in c.specs if str(s) not in specs]
        if c.specified and not c.specs:
            errors.append(f"{where}: an expectation needs the specification it comes from (specs)")
        if not c.specified and not c.questions and not c.covered_by:
            errors.append(f"{where}: neither an expectation nor an open question")
        if c.response.strip().lower() not in ("", "none") and not c.response.split("|")[0].strip().endswith("Response"):
            errors.append(f"{where}: response '{c.response}' is not '<Name>Response | Field=Value'")
        if names is not None:
            errors += [f"{where}: covered_by test '{n}' does not exist" for n in c.covered_by if n not in names]
    for state in model.states:
        for request in model.requests:
            n = seen.get((state, request), 0)
            if n != 1:
                errors.append(f"{request} in {state}: {'missing' if not n else f'{n} cells'} (one cell per state x request)")
    return errors


def _hash_tags(cell: Cell, catalog: dict) -> list[str]:
    specs = catalog["specifications"]
    if len(cell.specs) == 1:
        return [f"spechash:{specs[str(cell.specs[0])]['hash'][:8]}"]
    return [f"spechash:{s}:{specs[str(s)]['hash'][:8]}" for s in cell.specs]


def _reviewed(name: str, hash_tags: list[str], reviews: list[dict]) -> bool:
    key = ",".join(sorted(t.split(":", 1)[1].lower() for t in hash_tags))
    return any(r.get("test") == name and counts_as_review(r, key) for r in reviews)


def _wrap(text: str, first: str, indent: str = SEP, width: int = LINE) -> list[str]:
    """Robot continuation lines: ``first`` + text, then ``...`` lines."""
    words = textwrap.wrap(_escape(re.sub(r"\s+", " ", text).strip()), width, break_on_hyphens=False) or [""]
    return [f"{indent}{first}{SEP}{words[0]}"] + [f"{indent}...{SEP}{w}" for w in words[1:]]


def _cell_value(value: str) -> str:
    value = re.sub(r"\s{2,}", " ", value.strip())
    return _escape(value) or EMPTY


def _escape(text: str) -> str:
    text = text.replace("\\", "\\\\")
    return re.sub(r"([$@&%])\{", r"\\\1{", text)


def _documentation(cell: Cell, catalog: dict) -> str:
    parts = [f"State x request matrix cell: {cell.request} received in \"{cell.state}\" (generated from catalog/state-matrix.toml)."]
    for s in cell.specs:
        parts.append(f"SDS-{s}: {catalog['specifications'][str(s)]['text']}")
    if cell.note:
        parts.append(cell.note)
    if not cell.specs:
        parts.append("No specification covers this cell: the test observes appSMM and is skipped with what it saw.")
    if cell.questions:
        parts.append("Open: " + " / ".join(cell.questions))
    return " ".join(parts)


def render_suite(model: Model, catalog: dict, reviews: list[dict]) -> str:
    out = [
        "*** Settings ***",
        "Documentation       State x request matrix (plan 5.2). GENERATED by ``smm-auto matrix`` from",
        "...                 ``catalog/state-matrix.toml``: do not edit, change the model and regenerate.",
        "...                 Cells already covered by hand-written tests are not repeated here; open questions",
        "...                 are in ``catalog/state-matrix.questions.md``.",
        "Resource            ../../resources/state_matrix.resource",
        "Suite Setup         Open SMM Test Environment",
        "Suite Teardown      Close SMM Test Environment",
        "Test Setup          Begin SMM Test",
        "Test Teardown       Finish SMM Test",
        "Test Template       Request In State Should Behave",
        "Test Tags           area:state-matrix    pilot    matrix",
        "",
        "",
        "*** Test Cases ***",
    ]
    first = True
    for c in model.cells:
        if not c.generated:
            continue
        if not first:
            out.append("")
        first = False
        tags = [f"SDS-{s}" for s in c.specs] + (_hash_tags(c, catalog) if c.specs else [NOSPEC_TAG])
        if c.state == "NotInitialized":
            tags.append("requires:restart")
        if not _reviewed(c.test_name, [t for t in tags if t.startswith("spechash:")], reviews):
            tags.append(PENDING_TAG)
        out.append(c.test_name)
        out += _wrap(_documentation(c, catalog), "[Documentation]")
        out.append(f"{SEP}[Tags]{SEP}" + SEP.join(tags))
        values = [c.state, c.request, c.body, c.response, c.next, c.no_command]
        out.append(SEP + SEP.join(_cell_value(v) for v in values))
    return "\n".join(out) + "\n"


def render_questions(model: Model, catalog: dict) -> str:
    def mark(c: Cell) -> str:
        if c.covered_by:
            return "covered" + (" (open questions)" if c.questions else "")
        if not c.specs:
            return "**unspecified**"
        return "generated" + (" (partly specified)" if c.questions else "")

    cells = {(c.state, c.request): c for c in model.cells}
    out = [
        "# appSMM state x request matrix: open questions",
        "",
        "GENERATED by `smm-auto matrix` from `catalog/state-matrix.toml`; do not edit.",
        "",
        "For each stable state and request: what the specifications say. *covered* = hand-written tests verify the cell,",
        "*generated* = a test in `robot/suites/pilot/state_matrix.robot` verifies it, *unspecified* = no specification",
        "says anything; the generated test only observes appSMM and is skipped with the observed behaviour.",
        "",
        "| Request | " + " | ".join(model.states) + " |",
        "|---|" + "---|" * len(model.states),
    ]
    for r in model.requests:
        row = []
        for s in model.states:
            c = cells.get((s, r))
            row.append(mark(c) if c else "-")
        out.append(f"| {r} | " + " | ".join(row) + " |")
    out += ["", "## Questions for the specification owner", ""]
    n = 0
    for c in model.cells:
        if not c.questions:
            continue
        refs = ", ".join(f"SDS-{s}" for s in c.specs) or "no specification"
        out.append(f"### {c.request} in {c.state} ({refs})")
        out.append("")
        for q in c.questions:
            n += 1
            out.append(f"{n}. {q}")
        if c.generated:
            out += ["", f"Test: `{c.test_name}` (the observed behaviour is in its log and skip message).", ""]
        else:
            out += ["", "Covered by: " + "; ".join(f"`{t}`" for t in c.covered_by), ""]
    return "\n".join(out).rstrip("\n") + "\n"


def generate(model: Model, catalog: dict, reviews: list[dict]) -> dict[str, str]:
    return {"suite": render_suite(model, catalog, reviews), "questions": render_questions(model, catalog)}


def _same(path: Path, text: str) -> bool:
    return path.exists() and path.read_text(encoding="utf-8").replace("\r\n", "\n") == text


def write(outputs: dict[str, str], paths: dict[str, Path], check: bool = False) -> list[Path]:
    """Writes the generated files; with ``check`` only returns the ones that differ."""
    differ = [paths[k] for k, text in outputs.items() if not _same(paths[k], text)]
    if not check:
        for k, text in outputs.items():
            if paths[k] in differ:
                paths[k].parent.mkdir(parents=True, exist_ok=True)
                paths[k].write_text(text, encoding="utf-8", newline="\n")
    return differ
