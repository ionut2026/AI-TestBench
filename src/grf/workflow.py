"""Bounded deploy-and-test orchestration; site authentication and instrument reservation remain external."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from grf.acceptance import deployment_identity, file_sha256, git_state, load_request, require_policy
from grf.commands import CommandContext
from grf.deploy.config import load as load_site
from grf.deploy.engine import Deployment, Options
from grf.deploy.model import EXIT_CODES, DeployError, Outcome
from grf.deploy.record import load_records
from grf.selection import select

if TYPE_CHECKING:
    from grf.project import Project


def add_parser(sub: argparse._SubParsersAction) -> None:
    cmd = sub.add_parser("workflow", allow_abbrev=False, help="maintained bounded workflow (owner-authorized operations only)")
    actions = cmd.add_subparsers(dest="workflow_action", required=True)
    cmd = actions.add_parser("deploy-and-test", allow_abbrev=False)
    cmd.add_argument("--request", required=True)
    cmd.add_argument("--request-sha256", required=True, help="expected digest from the protected approval boundary")
    cmd.add_argument("--config", help="external deployment site config")
    cmd.add_argument("--outdir", type=Path, required=True, help="new workflow directory; must not exist")
    cmd.add_argument("--operator", choices=("none", "console", "dialog"), default="none")
    cmd.add_argument("--yes", action="store_true", help="confirm owner authorization; never grants it")


def deploy_and_test(args: argparse.Namespace, project: Project, invoke: Callable[[list[str]], int], *,
                    deployment_factory=Deployment) -> int:
    """Plan all pinned artifacts, deploy, verify records, run the exact request and always attempt its gate."""
    if not args.yes:
        raise ValueError("workflow changes the instrument; --yes requires the instrument owner's authorization")
    project.require_framework()
    definitions = project.plugin.deploy_components()
    components = {component.name: component for component in definitions}
    if len(components) != len(definitions):
        raise ValueError("duplicate deployable component names")
    request = load_request(Path(args.request), project.tiers, list(components))
    if not re.fullmatch(r"[0-9a-f]{64}", args.request_sha256) or request.sha256 != args.request_sha256:
        raise ValueError("protected request digest does not match")
    require_policy(request, project.data.get("acceptance", {}).get("policy", {}))
    if request.action != "deploy-and-test" or request.valid_until is None or dt.date.today() > request.valid_until:
        raise ValueError("workflow requires an unexpired deploy-and-test request")
    if not request.source_revision or not request.catalog_sha256:
        raise ValueError("workflow requires immutable source_revision and catalog_sha256 pins")
    context = CommandContext(project)
    catalog = context.catalog_path(args)
    head, dirty = git_state(project.root)
    if head != request.source_revision or dirty is not False or file_sha256(catalog) != request.catalog_sha256:
        raise ValueError("workflow requires the pinned clean source and catalog before any deployment")
    select(request, context.collect_tests(args))
    site = load_site(args.config, project.env_prefix, project.root)
    targets = {component.name: site.target_for(component.name) for component in request.components}
    # Preflight is read-only; the run repeats it after deployment against the actual test environment.
    from grf.evidence import PREFLIGHT, require_checks

    require_checks(project.plugin.evidence_preflight(request.tier), PREFLIGHT)
    if request.source_bytes is None:
        raise ValueError("workflow requires original request bytes")
    root = args.outdir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    snapshot = root / "request.toml"
    with snapshot.open("xb") as output:
        output.write(request.source_bytes)
        output.flush()
        os.fsync(output.fileno())
    if file_sha256(snapshot) != request.sha256:
        raise ValueError("workflow request snapshot differs from the approved request")
    with (root / "workflow.jsonl").open("x", encoding="utf-8") as journal:
        def event(stage: str, **values) -> None:
            journal.write(json.dumps({"stage": stage, "at": dt.datetime.now(dt.UTC).isoformat(),
                                      "requestSha256": request.sha256, **values}) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        event("validated", sourceRevision=head, catalogSha256=request.catalog_sha256)
        planned = {}
        record_paths = []
        rank = {name: index for index, name in enumerate(project.deploy_order)}
        requested = sorted(request.components, key=lambda component: (rank.get(component.name, len(rank)), component.name))
        for dry_run in (True, False):
            for component_request in requested:
                head, dirty = git_state(project.root)
                if head != request.source_revision or dirty is not False \
                        or file_sha256(catalog) != request.catalog_sha256 or dt.date.today() > request.valid_until:
                    raise ValueError("source, catalog or authorization changed before the next deployment stage")
                name = component_request.name
                out = root / ("plan" if dry_run else "deploy") / name
                event("planning" if dry_run else "deploying", component=name)
                deployment = deployment_factory(project.name, components[name], site, targets[name], work_dir=out,
                                                trigger="manual")
                record = deployment.deploy(Options(build=str(component_request.build), sha256=component_request.sha256,
                                                   branch=project.deploy_branch, dry_run=dry_run))
                path = record.write(out, site.audit_log)
                data = load_records([path])[0]
                code = EXIT_CODES[record.outcome or Outcome.FAILED]
                if code or (dry_run and record.outcome != Outcome.DRY_RUN):
                    event("stopped", component=name, outcome=str(record.outcome), code=code or 2)
                    return code or 2
                if dry_run:
                    artifact = data.get("artifact") or {}
                    if data.get("project") != project.name or data.get("component") != name \
                            or data.get("target") != targets[name].name or data.get("host") != targets[name].host \
                            or artifact.get("component") != name:
                        raise ValueError(f"plan of {name} has an unexpected project, component or target")
                    if artifact.get("build_number") != component_request.build \
                            or artifact.get("sha256") != component_request.sha256 \
                            or data.get("dryRun") is not True:
                        raise ValueError(f"plan of {name} does not match the approved numeric build and digest")
                    version = artifact.get("version")
                    if component_request.expected_version and (
                        not isinstance(version, str) or not project.plugin.installed_matches(
                            name, component_request.expected_version, version)
                    ):
                        raise ValueError(f"plan artifact version of {name} differs from the request")
                    planned[name] = data
                else:
                    _, problems = deployment_identity(component_request, request.action, [data],
                                                      project_name=project.name,
                                                      installed_matches=project.plugin.installed_matches)
                    if any(data.get(key) != planned[name].get(key) for key in ("target", "host")):
                        problems.append("actual deployment target differs from the plan")
                    if problems:
                        raise ValueError(f"deployment {name}: {'; '.join(problems)}")
                    record_paths.append(path)
                event("planned" if dry_run else "deployed", component=name, record=str(path),
                      recordSha256=file_sha256(path))
            if dry_run:
                event("promoted", artifacts=[{"component": c.name, "build": c.build, "sha256": c.sha256}
                                            for c in request.components])
        common = ["--catalog", str(catalog), "--reviews", str(context.reviews_path(args))]
        for suite in context.suites(args):
            common += ["--suites", str(suite)]
        binding = ["--request", str(snapshot), "--request-sha256", request.sha256]
        deployment_args = [value for path in record_paths for value in ("--deployment", str(path))]
        event("testing")
        failure: BaseException | None = None
        run_code = 2
        try:
            run_code = invoke([*common, "run", *binding, "--operator", args.operator,
                               "--outdir", str(root / "test"), *deployment_args])
        except BaseException as err:
            failure = err
        try:
            gate_code = invoke([*common, "gate", *binding, "--output", str(root / "test" / "output.xml"),
                                "--json", str(root / "acceptance.json"), *deployment_args])
            event("evaluated", runCode=run_code, gateCode=gate_code)
        except BaseException as err:
            if failure is not None:
                failure.add_note(f"Acceptance evaluation/journal also failed: {type(err).__name__}: {err}")
                raise failure from err
            raise
        if failure is not None:
            failure.add_note(f"Acceptance evaluation returned {gate_code}; evidence retained at {root}")
            raise failure
        return gate_code or run_code


def command(args: argparse.Namespace, project: Project, invoke: Callable[[list[str]], int]) -> int:
    try:
        return deploy_and_test(args, project, invoke)
    except DeployError as err:
        print(f"workflow: {err}", file=sys.stderr)
        return EXIT_CODES[err.outcome] or 2
