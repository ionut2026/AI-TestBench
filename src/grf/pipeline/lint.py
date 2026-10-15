"""Test rules the Robot suites must follow (``<cli> lint``, also part of ``<cli> drift --strict``).

Rule codes carry the project's ``rule_prefix``; rules 04.. are configured tag rules
(``[[lint.tag_rules]]`` in ``grf.toml``).

* GRF01 no-sleep        - no ``Sleep``: wait for observable state, or explicitly assert silence
* GRF02 documentation   - every test has ``[Documentation]`` that quotes its specification (at least
  ``QUOTE_WORDS`` consecutive words of the spec text) or, for a variant, names the specification id. A test
  without a specification (``nospec:<kind>`` tag, e.g. robustness) needs documentation saying what it expects
* GRF03 literal-timeout - ``timeout=`` / ``duration=`` and the `Wait Until Keyword Succeeds` timeout are
  variables (tier timeouts from ``robot/environments``, spec limits as ``${<SPEC_PREFIX>_<id>_LIMIT}``), not literals
* Configured rules require capability tags for unconditional keyword use, and require corresponding keyword
  use when a test carries the tag.

Keyword use is followed through user keywords (resource files and the suite's own keywords), so
resource wrappers count as use of their underlying keywords. A templated test (``Test Template`` /
``[Template]``) uses its template keyword.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from grf import project
from grf.pipeline.resources import resolve_resource
from grf.project import norm_keyword as norm

QUOTE_WORDS = 5
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
    source: Path | None = None


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
    """Resolve user calls in their actual import context; never merge unrelated suite namespaces."""

    def __init__(self, models: dict, imports: dict[Path, list[Path]]):
        self.imports = imports
        self.keywords: dict[Path, dict[str, list[list[Call]]]] = {}
        self.tagged: frozenset[str] = frozenset().union(*(r.satisfied_by for r in project.current().lint_tag_rules))
        self.ambiguities: set[tuple[Path, int, str]] = set()
        for source, model in models.items():
            definitions: dict[str, list[list[Call]]] = {}
            for kw in _section_items(model, "KeywordSection"):
                definitions.setdefault(norm(kw.name), []).append(
                    [replace(call, source=source) for call in _calls(kw.body)])
            self.keywords[source] = definitions

    def closure(self, source: Path) -> list[Path]:
        seen: set[Path] = set()
        pending = [source]
        while pending:
            path = pending.pop()
            if path not in seen:
                seen.add(path)
                pending.extend(self.imports.get(path, []))
        return sorted(seen)

    def resolve(self, call: Call) -> list[list[Call]]:
        assert call.source is not None
        name = norm(call.name)
        namespace, dot, _ = call.name.rpartition(".")
        sources = self.closure(call.source)
        if dot:
            normalized = re.sub(r"[\s_]", "", namespace).lower()
            sources = [source for source in sources if re.sub(r"[\s_]", "", source.stem).lower() == normalized]
        elif name in self.keywords[call.source]:
            sources = [call.source]
        definitions = [body for source in sources for body in self.keywords[source].get(name, [])]
        if len(definitions) > 1:
            self.ambiguities.add((call.source, call.line, call.name))
        return definitions

    def _sub_calls(self, call: Call) -> list[Call]:
        n = norm(call.name)
        if n.startswith("runkeyword") or n in ("waituntilkeywordsucceeds", "repeatkeyword"):
            cond = call.conditional or n in RUN_KEYWORD_CONDITIONAL
            candidates = [Call(a, (), call.line, cond, call.source) for a in call.args]
            return [candidate for candidate in candidates if norm(candidate.name) in self.tagged or self.resolve(candidate)]
        return []

    def uses(self, calls: list[Call], targets: frozenset[str]) -> tuple[bool, bool]:
        """(used at all, used unconditionally) of any keyword in ``targets``."""
        any_use = uncond = False
        seen: set[tuple[Path | None, str, bool]] = set()
        stack = list(calls)
        while stack:
            call = stack.pop()
            n = norm(call.name)
            key = (call.source, call.name.lower(), call.conditional)
            if key in seen:
                continue
            seen.add(key)
            if n in targets:
                any_use = True
                uncond = uncond or not call.conditional
            stack += self._sub_calls(call)
            for definition in self.resolve(call):
                for inner in definition:
                    stack.append(replace(inner, conditional=inner.conditional or call.conditional))
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


def _resources(model, source: Path, out: list[Violation]) -> list[Path]:
    resources = []
    for s in model.sections:
        if type(s).__name__ == "SettingSection":
            for node in s.body:
                if type(node).__name__ == "ResourceImport" and node.name:
                    path = resolve_resource(node.name, source.parent)
                    if path is not None:
                        resources.append(path)
                    else:
                        out.append(Violation(project.current().rule(0), str(source), node.lineno, "Resource",
                                             f"Cannot statically resolve resource {node.name!r}"))
    return resources


def _check_calls(calls: list[Call], source: str, item: str, out: list[Violation]) -> None:
    p = project.current()
    for c in calls:
        n = norm(c.name)
        if n == "sleep":
            out.append(Violation(p.rule(1), source, c.line, item, "Sleep: wait for a message or state, or assert silence with Message Should Not Arrive"))
        timing = [a for a in c.args if a.lower().startswith(TIMING_ARGS)]
        if n == "waituntilkeywordsucceeds" and c.args:
            timing.append("timeout=" + c.args[0])
        for a in timing:
            value = a.split("=", 1)[1]
            if value and "${" not in value:
                out.append(Violation(p.rule(3), source, c.line, item,
                                     f"Literal timing value '{a}': use a tier variable or ${{{p.spec_prefix}_<id>_LIMIT}}"))


def lint(paths: list[Path], catalog: dict | None = None) -> list[Violation]:
    from robot.api.parsing import get_model, get_resource_model

    files = _robot_files(paths)
    models = {}
    for f in files:
        models[f.resolve()] = get_resource_model(str(f)) if f.suffix == ".resource" else get_model(str(f))
    roots = [path.resolve() for path in paths if path.is_dir()]
    for source in list(models):
        for directory in (source.parent, *source.parent.parents):
            if not any(directory == root or directory.is_relative_to(root) for root in roots):
                break
            initializer = directory / "__init__.robot"
            if initializer.is_file() and initializer not in models:
                models[initializer] = get_model(str(initializer))
    out: list[Violation] = []
    imports: dict[Path, list[Path]] = {}
    pending = list(models)
    while pending:
        f = pending.pop()
        m = models[f]
        imports[f] = _resources(m, f, out)
        for r in imports[f]:
            if r not in models:
                models[r] = get_resource_model(str(r))
                pending.append(r)
    p = project.current()
    index = KeywordIndex(models, imports)
    for f, model in models.items():
        src = str(f)
        for kw in _section_items(model, "KeywordSection"):
            _check_calls(list(_calls(kw.body)), src, kw.name, out)
        if f.suffix == ".resource":
            continue
        suite_tags = list(getattr(_setting(model, "TestTags"), "values", ()) or ()) + list(getattr(_setting(model, "ForceTags"), "values", ()) or ())
        suite_setup, suite_teardown = _setting(model, "TestSetup"), _setting(model, "TestTeardown")
        suite_template = _setting(model, "TestTemplate")
        lifecycle = []
        ancestors = [model]
        for directory in (f.parent, *f.parent.parents):
            initializer = directory / "__init__.robot"
            if initializer in models and initializer != f:
                ancestors.append(models[initializer])
        for ancestor in ancestors:
            origin = Path(ancestor.source).resolve()
            for kind in ("SuiteSetup", "SuiteTeardown"):
                setting = _setting(ancestor, kind)
                if setting is not None and setting.name and setting.name.upper() != "NONE":
                    lifecycle.append(Call(setting.name, tuple(setting.args), setting.lineno, False, origin))
        setup_origin = teardown_origin = f
        for ancestor in ancestors[1:]:
            origin = Path(ancestor.source).resolve()
            if suite_setup is None and (setting := _setting(ancestor, "TestSetup")) is not None:
                suite_setup, setup_origin = setting, origin
            if suite_teardown is None and (setting := _setting(ancestor, "TestTeardown")) is not None:
                suite_teardown, teardown_origin = setting, origin
            if suite_template is None:
                suite_template = _setting(ancestor, "TestTemplate")
        for test in _section_items(model, "TestCaseSection"):
            nodes = {type(n).__name__: n for n in test.body}
            tags = [t.lower() for t in suite_tags + list(getattr(nodes.get("Tags"), "values", ()) or ())]
            spec_ids = [int(m[1]) for t in tags if (m := p.spec_tag_re.match(t))]
            doc = nodes["Documentation"].value if "Documentation" in nodes else ""
            nospec = not spec_ids and any(t.startswith(NOSPEC_PREFIX) for t in tags)
            if not doc.strip():
                what = "say what the test expects" if nospec else "quote the specification text"
                out.append(Violation(p.rule(2), src, test.lineno, test.name, f"No [Documentation]: {what}"))
            elif not nospec and not cites(doc, spec_ids, catalog):
                out.append(Violation(p.rule(2), src, test.lineno, test.name, f"[Documentation] does not quote the specification text ({QUOTE_WORDS}+ consecutive words) or name the specification id"))
            template = nodes.get("Template")
            template_name = template.value if template is not None else (suite_template.value if suite_template is not None else None)
            if template_name and template_name.upper() != "NONE":
                # data rows of a templated test are arguments, not keyword calls
                calls = [Call(template_name, (), test.lineno, False, f)]
            else:
                calls = [replace(call, source=f) for call in _calls(test.body)]
            if "Setup" not in nodes and suite_setup is not None and suite_setup.name:
                calls.append(Call(suite_setup.name, tuple(suite_setup.args), suite_setup.lineno, False, setup_origin))
            if "Teardown" not in nodes and suite_teardown is not None and suite_teardown.name:
                calls.append(Call(suite_teardown.name, tuple(suite_teardown.args), suite_teardown.lineno, False, teardown_origin))
            calls += lifecycle
            for call in calls:
                _check_calls([call], str(call.source or f), test.name, out)
            index.ambiguities.clear()
            for tag_rule in p.lint_tag_rules:
                _, uncond = index.uses(calls, tag_rule.keywords)
                used, _ = index.uses(calls, tag_rule.satisfied_by)
                if uncond and tag_rule.tag not in tags and not tag_rule.also & set(tags):
                    out.append(Violation(tag_rule.rule, src, test.lineno, test.name, f"Test {tag_rule.what} but is not tagged {tag_rule.tag}"))
                elif tag_rule.tag in tags and not used:
                    out.append(Violation(tag_rule.rule, src, test.lineno, test.name, f"Tagged {tag_rule.tag} but never {tag_rule.what}"))
            # Resolve even without tag rules so duplicate wrappers cannot silently select one body.
            index.uses(calls, frozenset())
            for origin, line, name in sorted(index.ambiguities):
                out.append(Violation(p.rule(0), str(origin), line, test.name, f"Ambiguous user keyword {name!r}"))
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
