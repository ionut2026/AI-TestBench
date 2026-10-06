"""Test rules the Robot suites must follow (``smm-auto lint``, also part of ``smm-auto drift --strict``).

* SMM01 no-sleep        - no ``Sleep``: wait for a message/state, or assert silence (`Message Should Not Arrive`)
* SMM02 documentation   - every test has ``[Documentation]`` that quotes its specification (at least
  ``QUOTE_WORDS`` consecutive words of the spec text) or, for a variant, names the specification id. A test
  without a specification (``nospec:<kind>`` tag, e.g. robustness) needs documentation saying what it expects
* SMM03 literal-timeout - ``timeout=`` / ``duration=`` and the `Wait Until Keyword Succeeds` timeout are
  variables (tier timeouts from ``robot/environments``, spec limits as ``${SDS_<id>_LIMIT}``), not literals
* SMM04 twin-tag        - a test that drives the hardware twin unconditionally (not inside ``IF``) is tagged
  ``needs:twin``; a ``needs:twin`` test uses at least one hardware keyword
* SMM05 restart-tag     - same for restarting appSMM and `requires:restart`, and for restarting the MQTT broker
  and `requires:broker-restart` (the rig may be able to do one and not the other)
* SMM06 applog-tag      - same for reading appSMM's log files and `needs:applog` (only where the log is reachable)
* SMM07 action-tag      - an E-Stop (`Trigger Emergency Stop`, which the operator can do on the rig) is tagged
  ``needs:hardware-action`` (or ``needs:twin``); an `Operator Action` is tagged ``needs:operator``

Keyword use is followed through user keywords (resource files and the suite's own keywords), so
``Restart appSMM And Wait Until NotInitialized`` counts as a restart. A templated test (``Test Template`` /
``[Template]``) uses its template keyword.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

QUOTE_WORDS = 5
# Keywords only the hardware twin can do (offline tier).
TWIN_KEYWORDS = {
    "triggerhardwareaction", "gethardwaresnapshot", "hardwarestateshouldbe",
    "clearhardwaretwinracks", "waitforhardwarecommand", "hardwarecommandshouldnotbesent",
    "sethardwarefaults", "clearhardwarefaults", "gethardwarefaultstatus", "hardwarefaultshouldhavebeenapplied",
}
# Hardware actions the operator can also do on the real instrument.
ACTION_KEYWORDS = {"triggeremergencystop"}
HARDWARE_KEYWORDS = TWIN_KEYWORDS | ACTION_KEYWORDS
RESTART_KEYWORDS = {"restartappsmm"}
BROKER_RESTART_KEYWORDS = {"restartmqttbroker"}
OPERATOR_KEYWORDS = {"operatoraction"}
APPLOG_KEYWORDS = {"getappsmmlogmessages", "icdmessagesshouldbeloggedbyappsmm"}
# (rule, tag, other tags that also allow the use, keywords that need the tag, keywords that justify the tag, what)
TAG_RULES: tuple[tuple[str, str, set[str], set[str], set[str], str], ...] = (
    ("SMM04", "needs:twin", set(), TWIN_KEYWORDS, HARDWARE_KEYWORDS, "drives the hardware twin"),
    ("SMM05", "requires:restart", set(), RESTART_KEYWORDS, RESTART_KEYWORDS, "restarts appSMM"),
    ("SMM05", "requires:broker-restart", set(), BROKER_RESTART_KEYWORDS, BROKER_RESTART_KEYWORDS, "restarts the MQTT broker"),
    ("SMM06", "needs:applog", set(), APPLOG_KEYWORDS, APPLOG_KEYWORDS, "reads the appSMM log files"),
    ("SMM07", "needs:hardware-action", {"needs:twin"}, ACTION_KEYWORDS, ACTION_KEYWORDS, "triggers an E-Stop"),
    ("SMM07", "needs:operator", set(), OPERATOR_KEYWORDS, OPERATOR_KEYWORDS, "asks the operator for an action"),
)
TAGGED_KEYWORDS = set().union(*(r[4] for r in TAG_RULES))
TIMING_ARGS = ("timeout=", "duration=")
NOSPEC_PREFIX = "nospec:"
RUN_KEYWORD_CONDITIONAL = {"runkeywordif", "runkeywordunless"}


@dataclass
class Violation:
    rule: str
    source: str
    line: int
    item: str
    message: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "source": self.source, "line": self.line, "item": self.item, "message": self.message}


def norm(name: str) -> str:
    name = name.split(".")[-1] if "." in name and " " not in name.split(".")[0] else name
    return re.sub(r"[\s_]", "", name).lower()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def cites(doc: str, spec_ids: list[int], catalog: dict | None) -> bool:
    """Documentation quotes the spec text (QUOTE_WORDS consecutive words) or names one of the spec ids."""
    if any(str(i) in doc for i in spec_ids):
        return True
    if not catalog:
        return True
    words = " " + " ".join(_words(doc)) + " "
    for sid in spec_ids:
        spec = catalog["specifications"].get(str(sid))
        if not spec:
            continue
        sw = _words(spec.get("text", ""))
        n = min(QUOTE_WORDS, len(sw))
        if n and any(" " + " ".join(sw[i:i + n]) + " " in words for i in range(len(sw) - n + 1)):
            return True
    return False


@dataclass
class Call:
    name: str
    args: tuple[str, ...]
    line: int
    conditional: bool


def _calls(body, conditional: bool = False):
    """Keyword calls in a test/keyword body; ``conditional`` when inside IF/ELSE, WHILE or EXCEPT."""
    for node in body or []:
        kind = type(node).__name__
        if kind == "KeywordCall":
            yield Call(node.keyword, tuple(node.args), node.lineno, conditional)
        elif kind in ("Setup", "Teardown") and getattr(node, "name", None):
            if node.name.upper() != "NONE":
                yield Call(node.name, tuple(node.args), node.lineno, conditional)
        elif kind == "If":
            branch = node
            while branch is not None:
                yield from _calls(branch.body, True)
                branch = branch.orelse
        elif kind == "Try":
            yield from _calls(node.body, conditional)
            branch = node.next
            while branch is not None:
                finally_ = type(branch.header).__name__ == "FinallyHeader"
                yield from _calls(branch.body, conditional if finally_ else True)
                branch = branch.next
        elif kind == "While":
            yield from _calls(node.body, True)
        elif hasattr(node, "body") and kind not in ("Documentation", "Tags"):
            yield from _calls(node.body, conditional)


def _section_items(model, section: str):
    for s in model.sections:
        if type(s).__name__ == section:
            for item in s.body:
                if hasattr(item, "name") and hasattr(item, "body"):
                    yield item


class KeywordIndex:
    """User keywords (name -> calls) from resource files and suites, for following calls transitively."""

    def __init__(self):
        self.keywords: dict[str, list[Call]] = {}

    def add(self, model) -> None:
        for kw in _section_items(model, "KeywordSection"):
            self.keywords[norm(kw.name)] = list(_calls(kw.body))

    def _sub_calls(self, call: Call) -> list[Call]:
        n = norm(call.name)
        if n.startswith("runkeyword") or n in ("waituntilkeywordsucceeds", "repeatkeyword"):
            cond = call.conditional or n in RUN_KEYWORD_CONDITIONAL
            return [Call(a, (), call.line, cond) for a in call.args if norm(a) in self.keywords or norm(a) in TAGGED_KEYWORDS]
        return []

    def uses(self, calls: list[Call], targets: set[str]) -> tuple[bool, bool]:
        """(used at all, used unconditionally) of any keyword in ``targets``."""
        any_use = uncond = False
        seen: set[tuple[str, bool]] = set()
        stack = list(calls)
        while stack:
            call = stack.pop()
            n = norm(call.name)
            if (n, call.conditional) in seen:
                continue
            seen.add((n, call.conditional))
            if n in targets:
                any_use = True
                uncond = uncond or not call.conditional
            stack += self._sub_calls(call)
            for inner in self.keywords.get(n, []):
                stack.append(Call(inner.name, inner.args, inner.line, inner.conditional or call.conditional))
        return any_use, uncond


def _setting(model, kind: str):
    for s in model.sections:
        if type(s).__name__ == "SettingSection":
            for node in s.body:
                if type(node).__name__ == kind:
                    return node
    return None


def _robot_files(paths: list[Path]) -> list[Path]:
    out = []
    for p in paths:
        if p.is_dir():
            out += sorted(x for x in p.rglob("*") if x.suffix in (".robot", ".resource"))
        elif p.suffix in (".robot", ".resource"):
            out.append(p)
    return out


def _resources(model, source: Path) -> list[Path]:
    out = []
    for s in model.sections:
        if type(s).__name__ == "SettingSection":
            for node in s.body:
                if type(node).__name__ == "ResourceImport" and node.name:
                    path = (source.parent / node.name).resolve()
                    if path.exists():
                        out.append(path)
    return out


def _check_calls(calls: list[Call], source: str, item: str, out: list[Violation]) -> None:
    for c in calls:
        n = norm(c.name)
        if n == "sleep":
            out.append(Violation("SMM01", source, c.line, item, "Sleep: wait for a message or state, or assert silence with Message Should Not Arrive"))
        timing = [a for a in c.args if a.lower().startswith(TIMING_ARGS)]
        if n == "waituntilkeywordsucceeds" and c.args:
            timing.append("timeout=" + c.args[0])
        for a in timing:
            value = a.split("=", 1)[1]
            if value and "${" not in value:
                out.append(Violation("SMM03", source, c.line, item, f"Literal timing value '{a}': use a tier variable or ${{SDS_<id>_LIMIT}}"))


def lint(paths: list[Path], catalog: dict | None = None) -> list[Violation]:
    from robot.api.parsing import get_model, get_resource_model

    files = _robot_files(paths)
    models = {}
    for f in files:
        models[f.resolve()] = get_resource_model(str(f)) if f.suffix == ".resource" else get_model(str(f))
    shared = KeywordIndex()
    for f, m in list(models.items()):
        for r in _resources(m, f):
            if r not in models:
                models[r] = get_resource_model(str(r))
    for f, m in models.items():
        if f.suffix == ".resource":
            shared.add(m)

    out: list[Violation] = []
    for f, model in models.items():
        src = str(f)
        for kw in _section_items(model, "KeywordSection"):
            _check_calls(list(_calls(kw.body)), src, kw.name, out)
        if f.suffix == ".resource":
            continue
        index = KeywordIndex()
        index.keywords = dict(shared.keywords)
        index.add(model)
        suite_tags = list(getattr(_setting(model, "TestTags"), "values", ()) or ()) + list(getattr(_setting(model, "ForceTags"), "values", ()) or ())
        suite_setup, suite_teardown = _setting(model, "TestSetup"), _setting(model, "TestTeardown")
        suite_template = _setting(model, "TestTemplate")
        for test in _section_items(model, "TestCaseSection"):
            nodes = {type(n).__name__: n for n in test.body}
            tags = [t.lower() for t in suite_tags + list(getattr(nodes.get("Tags"), "values", ()) or ())]
            spec_ids = [int(t[4:]) for t in tags if re.fullmatch(r"sds-\d+", t)]
            doc = nodes["Documentation"].value if "Documentation" in nodes else ""
            nospec = not spec_ids and any(t.startswith(NOSPEC_PREFIX) for t in tags)
            if not doc.strip():
                what = "say what the test expects" if nospec else "quote the specification text"
                out.append(Violation("SMM02", src, test.lineno, test.name, f"No [Documentation]: {what}"))
            elif not nospec and not cites(doc, spec_ids, catalog):
                out.append(Violation("SMM02", src, test.lineno, test.name, f"[Documentation] does not quote the specification text ({QUOTE_WORDS}+ consecutive words) or name the specification id"))
            template = nodes.get("Template")
            template_name = template.value if template is not None else (suite_template.value if suite_template is not None else None)
            if template_name and template_name.upper() != "NONE":
                # data rows of a templated test are arguments, not keyword calls
                calls = [Call(template_name, (), test.lineno, False)]
            else:
                calls = list(_calls(test.body))
            _check_calls(calls, src, test.name, out)
            if "Setup" not in nodes and suite_setup is not None and suite_setup.name:
                calls.append(Call(suite_setup.name, tuple(suite_setup.args), suite_setup.lineno, False))
            if "Teardown" not in nodes and suite_teardown is not None and suite_teardown.name:
                calls.append(Call(suite_teardown.name, tuple(suite_teardown.args), suite_teardown.lineno, False))
            for rule, tag, also, targets, satisfies, what in TAG_RULES:
                _, uncond = index.uses(calls, targets)
                used, _ = index.uses(calls, satisfies)
                if uncond and tag not in tags and not also & set(tags):
                    out.append(Violation(rule, src, test.lineno, test.name, f"Test {what} but is not tagged {tag}"))
                elif tag in tags and not used:
                    out.append(Violation(rule, src, test.lineno, test.name, f"Tagged {tag} but never {what}"))
    return out


def format_text(violations: list[Violation], root: Path | None = None) -> str:
    lines = []
    for v in violations:
        src = v.source
        if root:
            try:
                src = str(Path(v.source).relative_to(root))
            except ValueError:
                pass
        lines.append(f"{src}:{v.line}: {v.rule} [{v.item}] {v.message}")
    lines.append(f"{len(violations)} rule violation(s).")
    return "\n".join(lines)
