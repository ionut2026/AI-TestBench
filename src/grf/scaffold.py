"""Create a runnable product starter from the templates shipped in the wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
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
    "coordinator": ".github/agents/integration-coordinator.agent.md",
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
    "deploy-build",
    "coordinate-integration",
)
TEMPLATES.update({f"skill-{name}": f".github/skills/grf-{name}/SKILL.md" for name in SKILLS})
# Only with `grf init --with-deploy`: the site config example and the Jenkins pipeline (docs/DEPLOYMENT.md).
DEPLOY_TEMPLATES = {
    "deploy-config": "robot/environments/deploy.example.toml",
    "jenkinsfile-deploy": "jenkins/Jenkinsfile.deploy",
}
SKILL_MANIFEST = ".github/grf-scaffold.json"


def _managed_skills() -> dict[str, str]:
    templates = files("grf").joinpath("templates")
    return {relative: templates.joinpath(name + ".tpl").read_text(encoding="utf-8")
            for name, relative in TEMPLATES.items() if name.startswith("skill-") or name == "coordinator"}


def _manifest(rendered: dict[str, str]) -> str:
    return json.dumps({"schema": 1, "files": {
        relative: hashlib.sha256(content.encode("utf-8")).hexdigest() for relative, content in rendered.items()
    }}, indent=2, sort_keys=True) + "\n"


def _replace(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update_skills(destination: Path, *, check: bool = False) -> list[Path]:
    """Update only unchanged tracked generic files; refuse product edits before writing anything."""
    root = destination.resolve()
    if not root.is_dir():
        raise ValueError(f"skill destination must be an existing project directory: {root}")
    rendered = _managed_skills()
    manifest = root / SKILL_MANIFEST
    for relative in (*rendered, SKILL_MANIFEST):
        path = root / relative
        if any(parent.is_symlink() or parent.resolve() != parent
               for parent in (path, *path.parents) if parent.is_relative_to(root)):
            raise ValueError(f"managed skill path must not use symlinks or redirected directories: {path}")
    previous: dict[str, str] = {}
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
            raise ValueError(f"invalid skill manifest schema: {manifest}")
        recorded = data.get("files")
        if not isinstance(recorded, dict) or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
                                                 for v in recorded.values()):
            raise ValueError(f"invalid skill manifest files: {manifest}")
        previous = recorded
    changed, conflicts = [], []
    for relative, content in rendered.items():
        path = root / relative
        if not path.exists():
            changed.append(path)
            continue
        current = path.read_bytes()
        normalized = current.decode("utf-8").replace("\r\n", "\n")
        if normalized == content:
            continue
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if previous.get(relative) != digest:
            conflicts.append(str(path))
        else:
            changed.append(path)
    if conflicts:
        raise ValueError("modified or untracked generic files need manual comparison with a fresh grf init; "
                         "no files changed:\n" + "\n".join(conflicts))
    manifest_text = _manifest(rendered)
    if not manifest.exists() or manifest.read_text(encoding="utf-8") != manifest_text:
        changed.append(manifest)
    if not check:
        for path in changed:
            _replace(path, manifest_text if path == manifest else rendered[path.relative_to(root).as_posix()])
    return changed


def add_parser(sub: argparse._SubParsersAction) -> None:
    cmd = sub.add_parser("init", allow_abbrev=False, help="create a runnable product starter (no project needed)")
    cmd.add_argument("destination", type=Path)
    cmd.add_argument("--name", default="Demo Product", help="display name of the product")
    cmd.add_argument("--spec-prefix", default="SPEC", help="specification tag prefix (letters, digits, underscores)")
    cmd.add_argument("--with-deploy", action="store_true",
                     help="also create a deployment site config example and a Jenkins deployment pipeline")
    cmd = sub.add_parser("scaffold", allow_abbrev=False, help="maintain copied generic skills without replacing product overlays")
    cmd.add_argument("destination", nargs="?", type=Path, default=Path("."))
    cmd.add_argument("--update-skills", action="store_true", required=True)
    cmd.add_argument("--check", action="store_true", help="report needed updates without writing (exit 1 if changed)")


def init_project(destination: Path, name: str, spec_prefix: str, with_deploy: bool = False) -> Path:
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
    chosen = {**TEMPLATES, **(DEPLOY_TEMPLATES if with_deploy else {})}
    for template, relative in chosen.items():
        text = templates.joinpath(template + ".tpl").read_text(encoding="utf-8")
        for token, value in replacements.items():
            text = text.replace(token, value)
        rendered[relative] = text
    rendered[SKILL_MANIFEST] = _manifest(_managed_skills())
    destination.mkdir(parents=True, exist_ok=True)
    for relative, text in rendered.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    return destination


def cmd_init(args: argparse.Namespace) -> int:
    try:
        root = init_project(args.destination, args.name, args.spec_prefix, args.with_deploy)
    except (ValueError, OSError) as err:
        print(f"grf init: {err}", file=sys.stderr)
        return 2
    print(f"Created product starter in {root}. See README.md for installation and the mock demo.")
    if args.with_deploy:
        print("Deployment templates: robot/environments/deploy.example.toml (copy it OUTSIDE the repository) and "
              "jenkins/Jenkinsfile.deploy; the plugin must define deploy_components(). Guide: docs/DEPLOYMENT.md.")
    return 0


def bootstrap(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="grf", description="General Robot Framework product tooling.", allow_abbrev=False)
    add_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    return cmd_init(args) if args.command == "init" else cmd_scaffold(args)


def cmd_scaffold(args: argparse.Namespace) -> int:
    paths = update_skills(args.destination, check=args.check)
    for path in paths:
        print(f"{'Would update' if args.check else 'Updated'} {path}")
    return int(args.check and bool(paths))
