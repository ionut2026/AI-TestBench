"""``<cli> deploy``: put a Jenkins build of a product component on its board (docs/DEPLOYMENT.md).

  deploy components                                     what the product can deploy and where it goes
  deploy builds   --component C [--branch B] [--branches] [--limit N]   Jenkins builds or branches (read-only)
  deploy plan     --component C [--target T] [--build N | --local-tar F] [--branch B]   dry run, changes nothing
  deploy run      --component C[,C2] [--target T] [build options] [--yes] [--force] [--trigger manual|nightly]
                  [--ignore-hold] [--break-stale-lock]            deploy (changes the instrument)
  deploy status   [--component C] [--target T] [--json]         what runs on the board (read-only, logs in)
  deploy rollback --component C [--target T] [--yes]            back to the newest retained earlier build

Every command reads the site config from --config or <ENV_PREFIX>_DEPLOY_CONFIG (a file outside the repository).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from grf.deploy.branches import resolve_branches
from grf.deploy.config import SiteConfig, config_env, load
from grf.deploy.engine import Deployment, Options
from grf.deploy.jenkins import Jenkins
from grf.deploy.model import EXIT_CODES, TRIGGERS, Component, DeployError, Outcome


def add_parser(sub: argparse._SubParsersAction, env_prefix: str) -> None:
    cmd = sub.add_parser("deploy", allow_abbrev=False, help="deploy a Jenkins build of a component to its board (docs/DEPLOYMENT.md)",
                         description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    dsub = cmd.add_subparsers(dest="deploy_command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help=f"site deployment config (default: ${config_env(env_prefix)})")

    def build_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--target", help="board name from the site config (default: [components] of the site config)")
        p.add_argument("--build", default="lastSuccessfulBuild", help="Jenkins build number or lastSuccessfulBuild (default)")
        p.add_argument("--branch", action="append", metavar="COMPONENT=BRANCH",
                       help="repeatable per-component branch; legacy single BRANCH also accepted; "
                            "default: [deploy.branches], then [deploy] default_branch")
        p.add_argument("--local-tar", type=Path, help="deploy this image tarball instead of a Jenkins build")
        p.add_argument("--sha256", help="expected artifact SHA-256 (64 lowercase hex characters); single component, "
                                       "checked before connecting to the board")
        p.add_argument("--outdir", type=Path, help="where the record and the downloaded artifact go "
                                                    "(default: results/deploy/<time>-<component>)")
        p.add_argument("--trigger", choices=TRIGGERS, default="cli", help="recorded as who/what started it")
        p.add_argument("--ignore-hold", action="store_true", help="deploy although the owner put a hold file on the board")

    p = dsub.add_parser("components", allow_abbrev=False, help="list the deployable components")
    common(p)
    p = dsub.add_parser("builds", allow_abbrev=False, help="list Jenkins builds (or branches) of a component")
    common(p)
    p.add_argument("--component", required=True)
    p.add_argument("--branch", action="append", metavar="COMPONENT=BRANCH",
                   help="per-component branch override; legacy single BRANCH also accepted")
    p.add_argument("--branches", action="store_true", help="list the branches of a multibranch job instead")
    p.add_argument("--limit", type=int, default=10)
    p = dsub.add_parser("plan", allow_abbrev=False, help="dry run: resolve, download, verify and preflight; changes nothing")
    common(p)
    p.add_argument("--component", required=True)
    build_options(p)
    p = dsub.add_parser("run", allow_abbrev=False, help="deploy (changes the instrument; needs the owner's authorization)")
    common(p)
    p.add_argument("--component", required=True, help="component, or several separated by commas")
    build_options(p)
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation (Jenkins)")
    p.add_argument("--force", action="store_true", help="deploy even if the board already runs this artifact")
    p.add_argument("--break-stale-lock", action="store_true", help="remove a deployment lock older than the lock TTL")
    p = dsub.add_parser("status", allow_abbrev=False, help="what runs on the board (read-only; logs in to the board)")
    common(p)
    p.add_argument("--component")
    p.add_argument("--target")
    p.add_argument("--json", action="store_true")
    p = dsub.add_parser("rollback", allow_abbrev=False, help="redeploy the newest retained earlier build from the board")
    common(p)
    p.add_argument("--component", required=True)
    p.add_argument("--target")
    p.add_argument("--outdir", type=Path)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--trigger", choices=TRIGGERS, default="cli")
    p.add_argument("--ignore-hold", action="store_true")
    p.add_argument("--break-stale-lock", action="store_true")


def _components(available: list[Component], names: str, order: list[str]) -> list[Component]:
    by_name = {c.name: c for c in available}
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    unknown = [n for n in wanted if n not in by_name]
    if unknown or not wanted:
        raise DeployError(f"unknown component {', '.join(unknown) or names!r}; available: {', '.join(by_name) or 'none'}")
    rank = {n: i for i, n in enumerate(order)}
    return [by_name[n] for n in sorted(dict.fromkeys(wanted), key=lambda n: (rank.get(n, len(rank)), n))]


def _confirm(text: str, target: str, stdin: Any = None) -> bool:
    stdin = stdin or sys.stdin
    if not stdin.isatty():
        print(f"{text}\nRefused: not an interactive terminal. Pass --yes (only with the instrument owner's authorization).",
              file=sys.stderr)
        return False
    print(text)
    answer = input(f"This changes the instrument. Type the target name ({target}) to continue: ")
    return answer.strip() == target


def _print_record(record: Any, path: Path) -> None:
    data = record.as_dict()
    a = data.get("artifact") or {}
    print(f"\n{data['outcome']}: {data['message']}")
    if a:
        print(f"  artifact: {a.get('file_name')} sha256 {a.get('sha256')}"
              + (f" (Jenkins build {a['build_number']}: {a['build_url']})" if a.get("build_number") else f" ({a.get('source')})"))
    for line in data.get("recovery") or []:
        print(f"  recovery: {line}")
    print(f"Deployment record: {path}")


def cmd_deploy(args: argparse.Namespace, project: Any, *, jenkins_factory: Callable[..., Jenkins] = Jenkins,
               deployment_factory: Callable[..., Deployment] = Deployment) -> int:
    components = project.plugin.deploy_components()
    try:
        if args.deploy_command == "components":
            return _list_components(components, args, project)
        if not components:
            raise DeployError(f"{project.name} defines no deployable components (its plugin's deploy_components())")
        site = load(args.config, project.env_prefix, project.root)
        if args.deploy_command == "builds":
            branches = resolve_branches(components, default_branch=project.deploy_branch,
                                        configured=project.deploy_branches, overrides=args.branch)
            return _builds(site, _components(components, args.component, [])[0], args, branches, jenkins_factory)
        if args.deploy_command == "status":
            return _status(site, components, args, project, deployment_factory)
        return _deploy(site, components, args, project, deployment_factory)
    except DeployError as err:
        print(f"{project.cli} deploy: {err}", file=sys.stderr)
        return EXIT_CODES[err.outcome] if err.outcome != Outcome.FAILED else 2


def _list_components(components: list[Component], args: argparse.Namespace, project: Any) -> int:
    if not components:
        print(f"{project.name} defines no deployable components.")
        return 0
    site: SiteConfig | None = None
    try:
        site = load(args.config, project.env_prefix, project.root)
    except DeployError as err:
        print(f"(no site config: {err})")
    for c in components:
        where = site.components.get(c.name, "-") if site else "-"
        print(f"{c.name:<12} {c.description}\n{'':12} job {c.jenkins_job}, artifact /{c.artifact_pattern}/, "
              f"image {c.recipe.image}, container {c.recipe.name}, target {where}")
    return 0


def _builds(site: SiteConfig, component: Component, args: argparse.Namespace, branches: dict[str, str | None],
            jenkins_factory: Callable[..., Jenkins]) -> int:
    if site.jenkins is None:
        raise DeployError(f"{site.path} has no [jenkins] section")
    client = jenkins_factory(site.jenkins)
    if args.branches:
        for name in client.branches(component):
            print(name)
        return 0
    branch = branches[component.name]
    for b in client.builds(component, branch, args.limit):
        print(f"{b['number']:>6}  {str(b['result']):<9} {b['timestamp'] or '':<25} {b['url']}")
    return 0


def _status(site: SiteConfig, components: list[Component], args: argparse.Namespace, project: Any,
            deployment_factory: Callable[..., Deployment]) -> int:
    chosen = _components(components, args.component, project.deploy_order) if args.component else components
    results = []
    for c in chosen:
        if not args.target and c.name not in site.components:
            continue
        target = site.target_for(c.name, args.target)
        d = deployment_factory(project.name, c, site, target, work_dir=Path("."), echo=lambda _text: None)
        results.append(d.status())
    if args.json:
        print(json.dumps(results, indent=1))
        return 0
    for r in results:
        i = r["installed"]
        print(f"{r['component']} on {r['target']} ({r['host']}): {i['container']} {i['status']}, image {i['image']}, "
              f"version {i['version']}" + (f", deployed {i['extra'].get('deployedAt')} by {i['extra'].get('deployedBy')}"
                                          if i["extra"].get("deployedAt") else ", not deployed by GRF"))
        for line in r["drift"]:
            print(f"  drift: {line}")
        if r["unfinishedDeployment"]:
            print("  WARNING: an earlier deployment did not finish (container *_grf_previous exists)")
        if r["hold"]:
            print(f"  HOLD: {r['hold']}")
        if r["lock"]:
            print(f"  LOCKED: {r['lock']}")
        print(f"  retained earlier builds: {', '.join(r['previousBuilds']) or 'none'}")
    if not results:
        print("no component has a target (pass --target or set [components] in the site config)")
    return 0


def _deploy(site: SiteConfig, components: list[Component], args: argparse.Namespace, project: Any,
            deployment_factory: Callable[..., Deployment]) -> int:
    command = args.deploy_command
    chosen = _components(components, args.component, project.deploy_order)
    branches = resolve_branches(components, default_branch=project.deploy_branch,
                                configured=project.deploy_branches, overrides=getattr(args, "branch", None))
    restore = command == "rollback"
    if restore and len(chosen) > 1:
        raise DeployError("rollback takes one component")
    build = getattr(args, "build", "lastSuccessfulBuild")
    local_tar = getattr(args, "local_tar", None)
    sha256 = getattr(args, "sha256", None)
    if sha256 is not None and (not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)):
        raise DeployError("--sha256 must be 64 lowercase hex characters")
    if sha256 is not None and len(chosen) != 1:
        raise DeployError("--sha256 needs a single --component")
    if len(chosen) > 1 and (local_tar or build.isdigit()):
        raise DeployError("--local-tar and a build number need a single --component (every component has its own job)")
    targets = [site.target_for(c.name, args.target) for c in chosen]
    if command in ("run", "rollback") and not args.yes:
        what = (f"roll back {chosen[0].name} to its newest retained build" if restore else
                f"deploy {', '.join(c.name for c in chosen)} ({local_tar or f'Jenkins {build}'})")
        names = sorted({t.name for t in targets})
        if not _confirm(f"About to {what} on {', '.join(f'{t.name} ({t.host})' for t in targets)}.", ",".join(names)):
            return 2
    if site.jenkins and site.jenkins.insecure_tls and not local_tar and not restore:
        print("WARNING: [jenkins] insecure_tls = true: the Jenkins certificate is not verified", file=sys.stderr)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    previous_handler = None
    if command != "plan" and hasattr(signal, "SIGTERM"):
        def terminate(_signum: int, _frame: Any) -> None:
            raise KeyboardInterrupt  # a Jenkins abort becomes the same automatic rollback as Ctrl+C

        previous_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        code = 0
        for component, target in zip(chosen, targets, strict=True):
            options = Options(build=build, branch=branches[component.name], local_tar=local_tar,
                              dry_run=command == "plan", force=getattr(args, "force", False),
                              ignore_hold=args.ignore_hold, break_stale_lock=getattr(args, "break_stale_lock", False),
                              restore=restore, sha256=sha256)
            if args.outdir:
                out_dir = args.outdir / component.name if len(chosen) > 1 else args.outdir
            else:
                out_dir = project.path("results") / "deploy" / f"{stamp}-{component.name}"
            print(f"== {command} {component.name} -> {target.name} ({target.user}@{target.host})")
            d = deployment_factory(project.name, component, site, target, work_dir=out_dir, trigger=args.trigger)
            record = d.deploy(options)
            if record.outcome is None:
                record.outcome, record.message = Outcome.FAILED, record.message or "ended without an outcome"
            path = record.write(out_dir, site.audit_log)
            _print_record(record, path)
            code = EXIT_CODES[record.outcome]
            if code:
                if len(chosen) > 1:
                    print(f"Stopped: the remaining components were not deployed ({record.outcome.value}).", file=sys.stderr)
                break
        if command == "run" and code == 0:
            print(f"Show it in the test report: {project.cli} run ... --deployment <deployment.json> "
                  f"(or set GRF_DEPLOYMENT_RECORD)")
        return code
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)


def record_paths(values: list[str] | None, env: dict[str, str] | None = None) -> list[Path]:
    """``--deployment`` values plus ``GRF_DEPLOYMENT_RECORD`` (several separated by the OS path separator)."""
    env = dict(os.environ) if env is None else env
    paths = [Path(v) for v in values or []]
    paths += [Path(v) for v in env.get("GRF_DEPLOYMENT_RECORD", "").split(os.pathsep) if v.strip()]
    return list(dict.fromkeys(paths))
