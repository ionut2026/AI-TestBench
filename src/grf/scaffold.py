"""Create a runnable product starter from the templates shipped in the wheel."""

from __future__ import annotations

import argparse
import json
import re
import sys
from importlib.resources import files
from pathlib import Path

from grf.pipeline.ingest import html_to_text, spec_hash

TEMPLATES = {
    "config": "grf.toml",
    "pyproject": "pyproject.toml",
    "gitignore": ".gitignore",
    "readme": "README.md",
    "scope": "catalog/demo.scope.toml",
    "reviews": "catalog/reviews.toml",
    "rules": "catalog/brief-rules.md",
    "mutants": "catalog/mutants.toml",
    "fixture": "fixtures/rvs.json",
    "package": "src/product_automation/__init__.py",
    "library": "src/product_automation/ProductLibrary.py",
    "plugin": "src/product_automation/plugin.py",
    "environment": "robot/environments/mock.py",
    "suite": "robot/suites/demo/counter.robot",
    "author": ".github/agents/test-author.agent.md",
    "reviewer": ".github/agents/test-reviewer.agent.md",
}
SKILLS = (
    "getting-started",
    "author-test",
    "review-test",
    "interpret-report",
    "spec-drift",
    "mutation-testing",
    "doctor-tier",
    "product-plugin",
    "template-upgrade",
)
TEMPLATES.update({f"skill-{name}": f".github/skills/grf-{name}/SKILL.md" for name in SKILLS})


def add_parser(sub: argparse._SubParsersAction) -> None:
    cmd = sub.add_parser("init", allow_abbrev=False, help="create a runnable product starter (no project needed)")
    cmd.add_argument("destination", type=Path)
    cmd.add_argument("--name", default="Demo Product", help="display name of the product")
    cmd.add_argument("--spec-prefix", default="SPEC", help="specification tag prefix (letters, digits, underscores)")


def init_project(destination: Path, name: str, spec_prefix: str) -> Path:
    """Refuse nonempty destinations; never overwrite a project's files."""
    if not name.strip() or any(ord(c) < 32 for c in name):
        raise ValueError("name must be nonempty and contain no control characters")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", spec_prefix):
        raise ValueError("spec-prefix must start with a letter and contain only letters, digits or underscores")
    package = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not package:
        raise ValueError("name must contain at least one ASCII letter or digit")
    destination = destination.resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError(f"destination must be a new or empty directory: {destination}")
    templates = files("grf").joinpath("templates")
    fixture = json.loads(templates.joinpath("fixture.tpl").read_text(encoding="utf-8"))
    replacements = {
        "__NAME_JSON__": json.dumps(name.strip(), ensure_ascii=True),
        "__PACKAGE_JSON__": json.dumps(package + "-automation"),
        "__SPEC_PREFIX__": spec_prefix,
        "__SPEC_HASH__": spec_hash(html_to_text(fixture["specifications"][0]["Text"]))[:8],
    }
    rendered = {}
    for template, relative in TEMPLATES.items():
        text = templates.joinpath(template + ".tpl").read_text(encoding="utf-8")
        for token, value in replacements.items():
            text = text.replace(token, value)
        rendered[relative] = text
    destination.mkdir(parents=True, exist_ok=True)
    for relative, text in rendered.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    return destination


def cmd_init(args: argparse.Namespace) -> int:
    try:
        root = init_project(args.destination, args.name, args.spec_prefix)
    except (ValueError, OSError) as err:
        print(f"grf init: {err}", file=sys.stderr)
        return 2
    print(f"Created product starter in {root}. See README.md for installation and the mock demo.")
    return 0


def bootstrap(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="grf", description="General Robot Framework product tooling.", allow_abbrev=False)
    add_parser(parser.add_subparsers(dest="command", required=True))
    return cmd_init(parser.parse_args(argv))
