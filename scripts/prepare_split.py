"""Make history-preserving local extraction candidates. Never touches remotes or the source checkout."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

LAYOUTS = {
    "GeneralRobotFramework": {
        "general-robot-framework/": "",
        ".github/workflows/grf-ci.yml": ".github/workflows/grf-ci.yml",
        ".github/workflows/general-robot-framework.yml": ".github/workflows/general-robot-framework.yml",
        "docs/FRAMEWORK-SPLIT-PLAN.md": "docs/FRAMEWORK-SPLIT-PLAN.md",
        "docs/FRAMEWORK-SPLIT-CUTOVER.md": "docs/FRAMEWORK-SPLIT-CUTOVER.md",
        ".github/CODEOWNERS": ".github/CODEOWNERS",
    },
    "SMMAutomationFramework": {
        "smm-automation/": "",
        "docs/SMM-AUTOMATION-HANDBOOK.md": "docs/SMM-AUTOMATION-HANDBOOK.md",
        ".github/agents/smm-test-author.agent.md": ".github/agents/smm-test-author.agent.md",
        ".github/agents/smm-test-reviewer.agent.md": ".github/agents/smm-test-reviewer.agent.md",
        ".github/workflows/smm-automation.yml": ".github/workflows/smm-automation.yml",
        ".github/CODEOWNERS": ".github/CODEOWNERS",
    },
    "WindchillMcpServer": {"windchill-mcp-server/": ""},
}


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
                            encoding="utf-8")
    return result.stdout.strip()


def selected_files(tree: dict[str, str], paths: dict[str, str]) -> dict[str, str]:
    """Map original paths to extracted paths and retain Git blob IDs as an exact content check."""
    selected = {}
    for source, target in paths.items():
        for path, blob in tree.items():
            if path == source or source.endswith("/") and path.startswith(source):
                dest = target + path[len(source):] if source.endswith("/") else target
                if dest in selected:
                    raise ValueError(f"extraction paths collide at {dest}")
                selected[dest] = blob
    return selected


def tree(root: Path) -> dict[str, str]:
    out = {}
    for entry in git(root, "ls-tree", "-r", "HEAD").splitlines():
        header, path = entry.split("\t", 1)
        out[path] = header.split()[2]
    return out


def prepare(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if git(source, "rev-parse", "--show-toplevel").replace("\\", "/").lower() != source.as_posix().lower():
        raise ValueError("source must be the repository root")
    if git(source, "branch", "--show-current") != "main":
        raise ValueError("source must be on main")
    if not destination.is_relative_to(source / "general-robot-framework" / "results"):
        raise ValueError("destination must be inside general-robot-framework/results in the source checkout")
    if destination.exists():
        raise ValueError(f"destination already exists; choose a new directory: {destination}")
    # Check the tool before creating any output; installation is a separate explicit setup command.
    subprocess.run([sys.executable, "-m", "git_filter_repo", "--version"], check=True, capture_output=True)
    sha = git(source, "rev-parse", "HEAD")
    original = tree(source)
    report: dict = {"source": str(source), "sourceCommit": sha, "repositories": {}}
    destination.mkdir(parents=True)
    for name, paths in LAYOUTS.items():
        target = destination / name
        subprocess.run(["git", "clone", "--quiet", "--no-local", "--single-branch", "--branch", "main",
                        str(source), str(target)], check=True)
        argv = [sys.executable, "-m", "git_filter_repo"]
        for old, new in paths.items():
            argv += ["--path", old]
            if old != new:
                argv += ["--path-rename", f"{old}:{new}"]
        subprocess.run(argv, cwd=target, check=True, capture_output=True)
        # filter-repo normally removes origin. Ensure no extracted repo can accidentally push to the source.
        for remote in git(target, "remote").splitlines():
            git(target, "remote", "remove", remote)
        expected = selected_files(original, paths)
        actual = tree(target)
        if actual != expected:
            raise RuntimeError(f"{name}: extracted content does not match the source tree")
        if git(target, "status", "--porcelain"):
            raise RuntimeError(f"{name}: extracted checkout is dirty")
        commits = int(git(target, "rev-list", "--count", "HEAD"))
        if commits < 1:
            raise RuntimeError(f"{name}: no history retained")
        report["repositories"][name] = {"path": str(target), "head": git(target, "rev-parse", "HEAD"),
                                         "commits": commits, "files": len(actual), "contentVerified": True}
    (destination / "extraction.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    source = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=source / "general-robot-framework/results/split")
    args = parser.parse_args()
    try:
        result = prepare(source, args.destination)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as err:
        print(f"Split preparation failed: {err}", file=sys.stderr)
        if isinstance(err, subprocess.CalledProcessError) and err.stderr:
            print(err.stderr, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    print("Local candidates only: no remotes, no publication and no active-checkout removal. Cutover rewiring is pending.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
