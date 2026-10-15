"""grf: command line of a General Robot Framework project (a product command uses the same CLI).

  grf ingest   [--scope catalog/pilot.scope.toml]          RV&S -> catalog/<scope>.json
  grf briefs   [--spec <id> ...]                            catalog -> generated/briefs/<spec_prefix>-<id>.md
  grf drift    [--strict]                                   suites vs catalog and review ledger (stale/orphan/uncovered/unrecorded) + lint
  grf accept   (<spec id> ... | --all)                       accept RV&S state/link changes of specifications as the new baseline
  grf migrate-hashes --from <old catalog.json>               re-tag tests whose spec text is unchanged but whose hash changed
  grf review   <test name | spec tag> ... --reviewer <name | agent:<model>> --verdict <verdict> [--ref PR]
                                                            record a review in the review ledger (at the test's spechash)
  grf lint                                                  test rules (no Sleep, documentation, timing variables, tier tags)
  grf run      [--tier <tier>] [--operator none|console|dialog] [--fail-on new|any|none] [--processes N] [robot args...]
                                                            run suites (tests needing a missing capability are excluded),
                                                            then the traceability report
  grf doctor   [--tier <tier>] [--operator ...] [--deep] [--deploy]
                                                            check that a tier can run (the project's checks, then the generic ones);
                                                            --deploy also checks that this machine can deploy (docs/DEPLOYMENT.md)
  grf report   --output results/.../output.xml [--deployment deployment.json]
                                                            traceability report for an existing run
  grf mutate   [--mutant <id> ...] [--min-score 0.9]          mutation testing of the suites on the mock product
  grf request  check <request.toml>                         validate a run request (no contact with anything)
  grf gate     --request <request.toml> --output .../output.xml [--deployment ...]
                                                            acceptance gate: does the finished run satisfy the request?
                                                            (exit code 0 ACCEPTED, 1 REJECTED; docs/WORKFLOWS.md)
  grf deploy   components|builds|plan|run|status|rollback   put a Jenkins build of a product component on its board
                                                            (only products whose plugin defines components; docs/DEPLOYMENT.md)
  (plus product-owned commands registered by the project's plugin)

The project is the nearest grf.toml (or GRF_PROJECT); see grf.project.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

from grf import project
from grf.capabilities import excluded_tags, tier_capabilities
from grf.operator_prompt import MODES as OPERATOR_MODES


def _default_operator() -> str:
    from grf.operator_prompt import operator_mode

    return operator_mode()


def _catalog_path(args) -> Path:
    return _context().catalog_path(args)


def _context():
    from grf.commands import CommandContext

    return CommandContext(project.current())


def _reviews(args) -> Path:
    return _context().reviews_path(args)


def _suites(args) -> list[Path]:
    return _context().suites(args)


def cmd_ingest(args) -> int:
    from grf.pipeline.ingest import ingest

    out = _catalog_path(args)
    catalog = ingest(_context().scope_path(args), out)
    print(f"Catalog written to {out}: {json.dumps(catalog['counts'])}")
    changes = catalog.get("changes")
    if changes and any(changes.get(k) for k in ("added", "removed", "textChanged", "stateChanged", "linksChanged")):
        print(f"Changes since {changes['since']}: {json.dumps({k: v for k, v in changes.items() if k != 'since'})}")
    return 0


def cmd_accept(args) -> int:
    from grf.pipeline.ingest import accept_changes, load_catalog, write_catalog

    if not args.all and not args.ids:
        print("give specification IDs or --all", file=sys.stderr)
        return 2
    path = _catalog_path(args)
    catalog = load_catalog(path)
    unknown = [i for i in args.ids if str(i) not in catalog["specifications"]]
    if unknown:
        print(f"not in the catalog: {', '.join(map(str, unknown))}", file=sys.stderr)
        return 2
    accepted = accept_changes(catalog, None if args.all else args.ids)
    write_catalog(path, catalog)
    print(f"Accepted the current RV&S state and links as baseline for {len(accepted)} specification(s): "
          f"{', '.join(map(str, accepted)) or '(nothing changed)'}")
    return 0


def cmd_migrate_hashes(args) -> int:
    from grf.pipeline.drift import hash_migrations, migrate_hash_tags
    from grf.pipeline.ingest import load_catalog, normalize_text

    moves, changed = hash_migrations(load_catalog(Path(args.old)), load_catalog(_catalog_path(args)), normalize_text)
    files = sorted({f for s in _suites(args) for f in ([s] if s.is_file() else s.rglob("*.robot"))})
    done = migrate_hash_tags(moves, files, _reviews(args))
    print(f"{len(moves)} specification(s) with the same text and a new hash; "
          f"{sum(done.values())} spechash value(s) replaced in {len(done)} file(s)")
    for f, n in done.items():
        print(f"  {f}: {n}")
    if changed:
        print(f"Text really changed (tests stay STALE until a human re-checks them): {', '.join(map(str, changed))}")
    return 0


def cmd_briefs(args) -> int:
    from grf.pipeline.briefs import write_briefs
    from grf.pipeline.drift import collect_tests
    from grf.pipeline.ingest import load_catalog

    catalog = load_catalog(_catalog_path(args))
    tests = collect_tests(_suites(args))
    name = (catalog.get("scope") or {}).get("name") or "pilot"
    out = _context().path(args, "briefs", "out")
    paths = write_briefs(catalog, out, tests, [int(s) for s in args.spec] if args.spec else None, name)
    print(f"{len(paths)} brief(s) written to {out}")
    return 0


def cmd_drift(args) -> int:
    from grf.pipeline.drift import check, collect_tests, format_text, load_reviews, problems
    from grf.pipeline.ingest import load_catalog
    from grf.pipeline.lint import lint

    catalog = load_catalog(_catalog_path(args))
    result = check(catalog, collect_tests(_suites(args)), load_reviews(_reviews(args)))
    result["lint"] = [v.as_dict() for v in lint(_suites(args), catalog)]
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(format_text(result))
    return 1 if args.strict and problems(result) else 0


def cmd_review(args) -> int:
    from grf.pipeline.drift import collect_tests, is_human, record_review

    tests = collect_tests(_suites(args))
    chosen = []
    for selector in args.tests:
        sid = selector.upper().removeprefix(project.current().spec_prefix.upper() + "-")
        found = [t for t in tests if t.name == selector] or ([t for t in tests if int(sid) in t.specs] if sid.isdigit() else [])
        if not found:
            print(f"No test named or tagged '{selector}'", file=sys.stderr)
            return 2
        chosen += [t for t in found if t not in chosen]
    date = args.date or dt.date.today().isoformat()
    human = is_human({"reviewer": args.reviewer})
    for t in chosen:
        try:
            entry = record_review(_reviews(args), t, args.reviewer, args.verdict, date, args.ref or "", args.notes or "")
        except ValueError as err:
            print(f"{t.name}: {err}", file=sys.stderr)
            return 2
        print(f"Recorded: {entry['test']} [{entry['spechash']}] {entry['verdict']} by {entry['reviewer']}")
        if human and t.pending and entry["verdict"] in ("approved", "approved-with-notes"):
            print("  now remove its review:pending tag in the same change")
    if not human:
        print("Agent review: information for the human reviewer; it does not replace the human review.")
    return 0


def cmd_lint(args) -> int:
    from grf.pipeline.ingest import load_catalog
    from grf.pipeline.lint import format_text, lint

    catalog_path = _catalog_path(args)
    violations = lint(_suites(args), load_catalog(catalog_path) if catalog_path.exists() else None)
    print(format_text(violations, project.current().root))
    return 1 if violations else 0


def _report(catalog_path: Path, output_xml: Path, out_dir: Path, suites: list[Path], reviews: Path | None = None,
            deployments: list[str] | None = None) -> dict:
    from grf.deploy.cli import record_paths
    from grf.deploy.record import load_records
    from grf.pipeline.drift import collect_tests, load_reviews
    from grf.pipeline.ingest import load_catalog
    from grf.pipeline.report import write_report

    reviews = reviews or project.current().path("reviews")
    records = load_records(record_paths(deployments))
    report = write_report(load_catalog(catalog_path), output_xml, out_dir, collect_tests(suites), load_reviews(reviews),
                          deployments=records)
    print(f"Traceability report: {out_dir / 'traceability.html'}")
    print("Specifications: " + ", ".join(f"{k} {v}" for k, v in report["summary"].items() if v))
    print(f"Verified (passed and human-reviewed): {report['coverage']['verified']}; "
          f"tests without human review: {report['review']['unreviewed']} of {report['review']['tests']}")
    if not report["productEvidence"]:
        print("NOTE: mock tier - the verdicts check the framework, they are not evidence about the product.")
    for d in report.get("deployments", []):
        artifact = d.get("artifact") or {}
        print(f"Deployed build: {d.get('component')} {artifact.get('version') or artifact.get('file_name')} on "
              f"{d.get('target')} ({d.get('outcome')})")
    return report


def cmd_report(args) -> int:
    output = Path(args.output)
    _report(_catalog_path(args), output, Path(args.outdir) if args.outdir else output.parent, _suites(args), _reviews(args),
            args.deployment)
    return 0


def _exit_code(rc: int, output: Path, fail_on: str) -> int:
    """Robot's rc counts every failure; with ``--fail-on new`` only failures without ``known-issue:`` count."""
    if rc < 0 or rc > 250:
        return rc
    if not output.exists():
        print(f"Robot produced no output: {output}", file=sys.stderr)
        return rc or 2
    from grf.pipeline.report import classify, is_product_tier, read_results

    tests, meta = read_results(output)
    failures = classify(tests, is_product_tier(meta.get("Tier", "?")))
    if failures["known"]:
        print(f"KNOWN FAIL ({len(failures['known'])}): " + "; ".join(failures["known"]))
    if failures["fixed"]:
        print(f"FIXED? ({len(failures['fixed'])}) known-issue tests passed, re-check and remove the tag: " + "; ".join(failures["fixed"]))
    if failures["new"]:
        print(f"NEW FAIL ({len(failures['new'])}): " + "; ".join(failures["new"]))
    if fail_on == "none":
        return 0
    if fail_on == "new":
        return min(len(failures["new"]), 250)
    return rc


def cmd_run(args, robot_args: list[str]) -> int:
    from robot.run import RobotFramework

    p = project.current()
    request = _load_request(args.request, args.request_sha256) if args.request else None
    if args.request_sha256 and request is None:
        raise ValueError("--request-sha256 requires --request")
    if request:
        if args.tier is not None and args.tier != request.tier:
            raise ValueError("--tier does not match the request")
        args.tier = request.tier
        if request.valid_until is None or dt.date.today() > request.valid_until:
            raise ValueError(f"request authorization expired on {request.valid_until}")
    elif args.tier is None:
        args.tier = next(iter(p.tiers), None)
    if p.tier(args.tier).evidence:
        p.require_framework()
    forwarded, sources = RobotFramework().parse_arguments([*robot_args, "__grf_managed_suite__.robot"])
    for key in ("outputdir", "output", "variablefile", "name"):
        if key in forwarded:
            raise ValueError(f"Robot option {key!r} is managed by GRF; use --outdir and tier configuration")
    if any(value.split(":", 1)[0].lower() == "tier" or value.split(":", 1)[0].lower().startswith("grf ")
           for value in forwarded.get("metadata", [])):
        raise ValueError("Tier metadata and GRF provenance metadata are managed by GRF")
    if request and any(key in forwarded for key in ("include", "exclude", "test", "suite", "prerunmodifier")):
        raise ValueError("request selection is managed by GRF; do not forward Robot selectors or prerun modifiers")
    if forwarded.get("dryrun") and p.tier(args.tier).evidence:
        raise ValueError("Robot dry runs require a non-evidence tier")
    if sources != ["__grf_managed_suite__.robot"]:
        raise ValueError("Robot suite sources are managed by GRF; use the global --suites option")
    if args.processes < 1:
        print("--processes must be at least 1", file=sys.stderr)
        return 2
    parallel = [t.name for t in p.tiers.values() if t.parallel]
    if args.processes > 1 and args.tier not in parallel:
        # which tiers may run in parallel and why the others may not: [tiers] in grf.toml
        print(f"--processes {args.processes}: parallel runs are only possible on the {', '.join(parallel) or 'no'} tier "
              f"(see [tiers] in {p.root / project.CONFIG_FILE})", file=sys.stderr)
        return 2
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out_dir = Path(args.outdir) if args.outdir else p.path("results") / f"{args.tier}-{stamp}"
    if (out_dir / "output.xml").exists():
        raise ValueError(f"refusing to reuse existing Robot output: {out_dir / 'output.xml'}; choose a new --outdir")
    chosen = None
    deployment_digests: list[str] = []
    if request:
        from grf.acceptance import deployment_identity, file_sha256, git_state
        from grf.deploy.cli import record_paths
        from grf.deploy.record import load_records, record_files
        from grf.selection import select

        chosen = select(request, _context().collect_tests(args))
        files = record_files(record_paths(args.deployment))
        records = load_records(files)
        unexpected = {record["component"] for record in records} - {c.name for c in request.components}
        if unexpected:
            raise ValueError(f"request has unexpected deployment components: {', '.join(sorted(unexpected))}")
        for component in request.components:
            _, problems = deployment_identity(component, request.action, records, project_name=p.name,
                                              installed_matches=p.plugin.installed_matches)
            if problems:
                raise ValueError(f"request deployment {component.name}: {'; '.join(problems)}")
        deployment_digests = sorted(file_sha256(path) for path in files)
        if request.source_revision:
            head, dirty = git_state(p.root)
            if head != request.source_revision or dirty is not False:
                raise ValueError("request requires the pinned clean source revision before execution")
        if request.catalog_sha256 and file_sha256(_catalog_path(args)) != request.catalog_sha256:
            raise ValueError("request catalog digest does not match before execution")
    from grf.evidence import PREFLIGHT, PREFLIGHT_METADATA, require_checks
    from grf.operator_prompt import env_name as operator_env_name
    from grf.rigcontrol import RigControlError

    preflight = p.plugin.evidence_preflight(args.tier)
    if preflight is not None or p.is_evidence(args.tier):
        preflight = require_checks(preflight, PREFLIGHT)
    try:
        caps, notes = tier_capabilities(args.tier, args.operator)
    except RigControlError as err:
        print(f"Rig control: {err}", file=sys.stderr)
        return 2
    excludes = excluded_tags(caps)
    print("Capabilities: " + "; ".join(notes))
    print(f"Excluded (capability missing): {', '.join(excludes) or 'nothing'}")
    os.environ[operator_env_name()] = args.operator
    os.environ[project.PROJECT_ENV] = str(p.root)
    if args.exclude_pending:
        excludes.append("review:pending")
    from grf import __version__

    metadata = [f"Run:{stamp}", f"Tier:{args.tier}", f"GRF Version:{__version__}"]
    if preflight is not None:
        metadata.append(PREFLIGHT_METADATA + ":" + json.dumps(preflight.as_dict(), separators=(",", ":")))
    if (product_version := p.plugin.product_version()) is not None:
        metadata.append(f"GRF Product Version:{product_version}")
    if request:
        metadata += [f"GRF Request ID:{request.id}", f"GRF Request SHA256:{request.sha256}"]
        metadata.append("GRF Deployment SHA256:" + json.dumps(deployment_digests, separators=(",", ":")))
    options = {
        "variablefile": [str(p.path("environments") / f"{args.tier}.py")],
        "exclude": excludes,
        "outputdir": str(out_dir),
        "name": f"{p.name} {args.tier}",
        "metadata": metadata,
    }
    argv: list[str] = []
    for key, values in options.items():
        for value in values if isinstance(values, list) else [values]:
            argv += [f"--{key}", str(value)]
    argv += robot_args + [str(suite) for suite in _suites(args)]
    if chosen is not None:
        from grf.selection import modifier

        argv = ["--prerunmodifier", modifier(chosen), *argv]
    from grf.reservation import ReservationError, exclusive

    lock = p.tier(args.tier).lock
    try:
        with exclusive(lock, p.path("results") / ".locks", f"{p.cli} run --tier {args.tier} -> {out_dir}") if lock \
                else contextlib.nullcontext():
            if lock:
                print(f"Reserved '{lock}' on this machine for the run")
            if request:
                out_dir.mkdir(parents=True, exist_ok=True)
                if request.source_bytes is None:
                    raise ValueError("request run requires the original file bytes")
                with (out_dir / "request.toml").open("xb") as snapshot:
                    snapshot.write(request.source_bytes)
                    snapshot.flush()
                    os.fsync(snapshot.fileno())
            rc = _robot(argv, args.processes)
    except ReservationError as err:
        print(f"Reservation: {err}", file=sys.stderr)
        return 2
    output = out_dir / "output.xml"
    if output.exists() and not args.no_report:
        _report(_catalog_path(args), output, out_dir, _suites(args), _reviews(args), args.deployment)
    if p.is_evidence(args.tier) and output.exists():
        from grf.evidence import assess
        from grf.pipeline.report import read_results

        tests, meta = read_results(output)
        assessment = assess(p.plugin, args.tier, tests, meta)
        problems = list(assessment.problems) + assessment.coverage_problems(
            [sid for test in tests for sid in test["specs"]])
        if meta.get("Tier") != args.tier:
            problems.append("Robot output tier differs from validated run tier")
        if problems:
            print("INVALID EVIDENCE: " + "; ".join(problems), file=sys.stderr)
            return rc if rc < 0 or rc > 250 else 2
    return _exit_code(rc, output, args.fail_on)


def _robot(argv: list[str], processes: int) -> int:
    from robot.run import run_cli

    if processes > 1:
        from pabot.pabot import main_program

        # One robot process per suite, with this Python (the venv need not be on PATH); PabotLib (port 8270) is not used.
        argv = ["--processes", str(processes), "--no-pabotlib",
                "--command", sys.executable, "-m", "robot", "--end-command", *argv]
        print("pabot " + " ".join(argv))
        cache = Path(".pabotsuitenames")  # pabot's suite-name cache, always in the current directory
        cached = cache.exists()
        try:
            rc = main_program(argv)
        finally:
            if not cached:
                cache.unlink(missing_ok=True)
    else:
        print("robot " + " ".join(argv))
        rc = run_cli(argv, exit=False)
    return rc


def cmd_mutate(args) -> int:
    from grf.pipeline.drift import collect_tests
    from grf.pipeline.mutation import (
        MutantError,
        load_mutants,
        mutate,
        specs_without_mutants,
        survivors_markdown,
        write_report,
    )

    if args.tier is None:
        raise ValueError("mutation testing requires a configured non-evidence tier")
    if not math.isfinite(args.min_score) or not 0 <= args.min_score <= 1:
        raise ValueError("--min-score must be a finite number between 0 and 1")
    try:
        mutants = load_mutants(_context().path(args, "mutants"))
    except MutantError as err:
        print(err, file=sys.stderr)
        return 2
    if args.mutant:
        unknown = set(args.mutant) - {m["id"] for m in mutants}
        if unknown:
            print(f"unknown mutant(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        mutants = [m for m in mutants if m["id"] in args.mutant]
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    p = project.current()
    out_dir = Path(args.outdir) if args.outdir else p.path("results") / f"mutation-{stamp}"
    suites = _suites(args)
    tests = collect_tests(suites)
    excludes = excluded_tags(tier_capabilities(args.tier)[0])
    print(f"{len(mutants)} mutant(s) on the {args.tier} tier -> {out_dir}")
    result = mutate(mutants, tests, out_dir, tier=args.tier, variablefile=p.path("environments") / f"{args.tier}.py",
                    excludes=excludes, suites=suites, baseline=not args.no_baseline)
    if not args.mutant:
        result["specsWithoutMutants"] = specs_without_mutants(mutants, tests, excludes)
    report = write_report(result, out_dir)
    survivors = survivors_markdown(result)
    if survivors:
        (out_dir / "survivors.md").write_text(survivors, encoding="utf-8")
    print(f"Mutation report: {report}")
    if result.get("aborted"):
        print(f"ABORTED: {result['aborted']} ({result['baseline']['failed']})", file=sys.stderr)
        return 2
    sc = result["score"]
    print(f"Mutation score: {sc['score']} (killed {sc['killed']}, survived {sc['survived']}, no tests {sc['noTests']}, errors {sc['errors']})")
    for m in result["mutants"]:
        if m["status"] == "survived":
            print(f"  SURVIVED {m['id']} ({', '.join(p.spec_tag(s) for s in m['specs'])}): {m['description']}")
    if result.get("specsWithoutMutants"):
        print(f"Specifications with tests but no mutant: {', '.join(map(str, result['specsWithoutMutants']))}")
    if sc["errors"] or sc["noTests"] or sc["score"] is None:
        return 2
    measured = sc["killed"] / (sc["killed"] + sc["survived"])
    return 1 if measured < args.min_score else 0


def _load_request(path: str, expected_sha256: str | None = None):
    from grf.acceptance import RequestError, load_request, require_policy

    p = project.current()
    request = load_request(Path(path), p.tiers, [c.name for c in p.plugin.deploy_components()])
    if expected_sha256 is not None and request.sha256 != expected_sha256:
        raise RequestError("protected request digest does not match")
    require_policy(request, p.data.get("acceptance", {}).get("policy", {}))
    return request


def cmd_request(args) -> int:
    from grf.acceptance import RequestError

    try:
        request = _load_request(args.file)
    except RequestError as err:
        print(f"INVALID: {err}", file=sys.stderr)
        return 1
    print("\n".join(request.summary()))
    if request.valid_until and dt.date.today() > request.valid_until:
        print(f"INVALID: the authorization expired on {request.valid_until}", file=sys.stderr)
        return 1
    print("Request is valid (this checks the file only; nothing was contacted).")
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(request.as_dict(), indent=1), encoding="utf-8")
    return 0


def cmd_gate(args) -> int:
    from grf.acceptance import RequestError, evaluate, file_sha256, format_result, write_result
    from grf.deploy.cli import record_paths
    from grf.deploy.record import load_records, record_files
    from grf.evidence import assess
    from grf.pipeline.drift import collect_tests, load_reviews
    from grf.pipeline.ingest import load_catalog
    from grf.pipeline.report import read_results

    p = project.current()
    p.require_framework()
    try:
        request = _load_request(args.request, args.request_sha256)
    except RequestError as err:
        print(f"INVALID request: {err}", file=sys.stderr)
        return 2
    output = Path(args.output)
    if not output.is_file():
        print(f"no Robot output: {output}", file=sys.stderr)
        return 2
    tests, meta = read_results(output)
    files = record_files(record_paths(args.deployment))
    digests = [{"file": str(path.resolve()), "sha256": file_sha256(path)} for path in files]
    catalog_path = _catalog_path(args)
    result = evaluate(request, tests=tests, meta=meta, known=collect_tests(_suites(args)), reviews=load_reviews(_reviews(args)),
                      catalog=load_catalog(catalog_path), catalog_path=catalog_path, records=load_records(files),
                      observed=p.plugin.runtime_identity(meta), matches=p.plugin.runtime_matches, root=p.root,
                      installed_matches=p.plugin.installed_matches,
                      output_sha256=file_sha256(output), record_digests=digests, product_version=p.plugin.product_version(),
                      evidence_assessment=assess(p.plugin, meta.get("Tier", "?"), tests, meta),
                      evidence=p.is_evidence(meta.get("Tier", "?")), project_name=p.name)
    target = Path(args.json) if args.json else output.parent / "acceptance.json"
    write_result(result, target)
    print(format_result(result))
    print(f"Acceptance record: {target}")
    return 0 if result["status"] == "ACCEPTED" else 1


def cmd_doctor(args) -> int:
    from grf.doctor import format_checks, run_doctor

    checks = run_doctor(args.tier, args.operator, args.deep)
    if args.deploy:
        from grf.deploy.checks import deploy_checks

        p = project.current()
        checks += deploy_checks(p.plugin.deploy_components(), args.deploy_config, p.env_prefix, p.root, args.deep)
    print(format_checks(checks))
    failed = [c.name for c in checks if c.status == "FAIL"]
    print(f"{args.tier} tier: " + (f"NOT READY ({', '.join(failed)})" if failed else "ready"))
    return 1 if failed else 0


def _main(argv: list[str] | None = None) -> int:
    from grf.scaffold import add_parser, bootstrap, cmd_init, cmd_scaffold

    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("init", "scaffold"):
        return bootstrap(argv)
    try:
        p = project.current()
    except project.ProjectError as err:
        if argv in (["--help"], ["-h"]):
            return bootstrap(argv)
        print(f"grf: {err}", file=sys.stderr)
        return 2
    tiers = list(p.tiers) if "tiers" in p.data else []
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--scope", help="scope file (default: [paths] scope)")
    common.add_argument("--catalog", help="catalog JSON (default: catalog/<scope name>.json)")
    common.add_argument("--suites", action="append", help="suite file/directory, repeatable (default: [paths] suites)")
    common.add_argument("--reviews", help="review ledger (default: [paths] reviews)")
    parser = argparse.ArgumentParser(prog=p.cli, parents=[common], description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    add_parser(sub)

    sub.add_parser("ingest", allow_abbrev=False, help="read specifications, requirements, user stories and ETs from RV&S")
    cmd = sub.add_parser("briefs", allow_abbrev=False, help="write generation briefs for the test-authoring agent")
    cmd.add_argument("--spec", nargs="*", help="only these specification IDs")
    cmd.add_argument("--out", help="output directory (default: [paths] briefs)")
    cmd = sub.add_parser("drift", allow_abbrev=False, help="compare suites with the catalog")
    cmd.add_argument("--strict", action="store_true", help="exit code 1 on stale/orphan/untagged/uncovered/unrecorded review or a lint violation")
    cmd.add_argument("--json", help="also write the result as JSON")
    cmd = sub.add_parser("accept", allow_abbrev=False, help="accept RV&S state/link changes of specifications as the new baseline")
    cmd.add_argument("ids", nargs="*", type=int, help="specification IDs")
    cmd.add_argument("--all", action="store_true", help="every specification")
    cmd = sub.add_parser("migrate-hashes", allow_abbrev=False, help="update spechash tags after a hashing change (same text, new hash)")
    cmd.add_argument("--from", dest="old", required=True, help="the catalog JSON before the re-ingestion")
    cmd = sub.add_parser("review", allow_abbrev=False, help="record a review of tests in the review ledger")
    cmd.add_argument("tests", nargs="+", help=f"exact test name(s) or {p.spec_prefix}-<id> (every test tagged with it)")
    cmd.add_argument("--reviewer", required=True, help="the person's name, or agent:<model> for an agent review")
    cmd.add_argument("--verdict", required=True, choices=["approved", "approved-with-notes", "changes-requested", "rejected"])
    cmd.add_argument("--ref", help="PR / commit")
    cmd.add_argument("--notes")
    cmd.add_argument("--date", help="YYYY-MM-DD (default: today)")
    sub.add_parser("lint", allow_abbrev=False, help="check the test rules (exit code 1 on violations)")
    cmd = sub.add_parser("run", allow_abbrev=False, help="run suites against a tier and write the traceability report (extra args go to robot)")
    cmd.add_argument("--tier", choices=tiers or None, default=None)
    cmd.add_argument("--request", help="validated run request; applies exact selection and binds run metadata to its digest")
    cmd.add_argument("--request-sha256", help="protected expected digest; checked against the exact bytes parsed")
    cmd.add_argument("--operator", choices=OPERATOR_MODES, default=_default_operator(),
                     help=f"who does manual steps at the instrument (default: ${p.env('OPERATOR')} or none); console/dialog also run "
                          "needs:operator and needs:hardware-action tests")
    cmd.add_argument("--outdir")
    cmd.add_argument("--fail-on", choices=["new", "any", "none"], default="new",
                     help="exit code: number of NEW failures (default; known-issue failures do not count), of ANY failures, or 0")
    cmd.add_argument("--exclude-pending", action="store_true", help="do not run tests tagged review:pending (they run and are labelled UNREVIEWED by default)")
    cmd.add_argument("--include-pending", action="store_true", help=argparse.SUPPRESS)  # pre-1.1 option, now the default
    cmd.add_argument("--no-report", action="store_true")
    cmd.add_argument("--processes", type=int, default=1,
                     help="run suites in N parallel processes with pabot (only tiers configured with parallel = true)")
    cmd.add_argument("--deployment", action="append",
                     help="deployment record (deployment.json or its folder) to show as 'Deployed builds' in the report; "
                          "repeatable (also: GRF_DEPLOYMENT_RECORD)")
    cmd = sub.add_parser("report", allow_abbrev=False, help="traceability report for an existing output.xml")
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--outdir")
    cmd.add_argument("--deployment", action="append", help="deployment record to show in the report (repeatable)")
    cmd = sub.add_parser("mutate", allow_abbrev=False, help=f"mutation testing: inject defects into the mock {p.product} and check that the tests notice")
    mock_tiers = [p.tier(name).name for name in tiers if not p.tier(name).evidence]
    cmd.add_argument("--tier", choices=mock_tiers, default=mock_tiers[0] if mock_tiers else None,
                     help=f"only the mock {p.product} supports fault injection")
    cmd.add_argument("--mutants", help="mutation definitions (default: [paths] mutants)")
    cmd.add_argument("--mutant", action="append", help="only this mutant id (repeatable)")
    cmd.add_argument("--outdir")
    cmd.add_argument("--min-score", type=float, default=0.9, help="exit code 1 when the share of killed mutants is lower (default 0.9)")
    cmd.add_argument("--no-baseline", action="store_true", help="skip the run without faults (the selected tests must pass without faults)")
    cmd = sub.add_parser("request", allow_abbrev=False, help="validate a run request file (docs/WORKFLOWS.md); contacts nothing")
    cmd.add_argument("action", choices=["check"])
    cmd.add_argument("file", help="the request (TOML)")
    cmd.add_argument("--json", help="write the validated request as normalized JSON (only on success)")
    cmd = sub.add_parser("gate", allow_abbrev=False, help="acceptance gate: does a finished run satisfy its request? (exit code 0 ACCEPTED, 1 REJECTED)")
    cmd.add_argument("--request", required=True, help="the run request (TOML)")
    cmd.add_argument("--request-sha256", help="protected expected digest; checked against the exact bytes parsed")
    cmd.add_argument("--output", required=True, help="the run's Robot output.xml")
    cmd.add_argument("--deployment", action="append", help="deployment record(s) of the run (repeatable; also GRF_DEPLOYMENT_RECORD)")
    cmd.add_argument("--json", help="where to write the acceptance record (default: acceptance.json next to output.xml)")
    cmd = sub.add_parser("doctor", allow_abbrev=False, help="check that a tier can run before a long run (exit code 1 on FAIL)")
    cmd.add_argument("--tier", choices=tiers or None, default=tiers[0] if tiers else None)
    cmd.add_argument("--operator", choices=OPERATOR_MODES, default=_default_operator())
    cmd.add_argument("--deep", action="store_true", help="also start the environment and talk to the product (see the project's doctor; "
                                                       "on a rig this may connect to the instrument)")
    cmd.add_argument("--deploy", action="store_true", help="also check that this machine can deploy (site config, credentials "
                                                         "present, Jenkins, host keys; with --deep: ssh + docker version)")
    cmd.add_argument("--deploy-config", help=f"site deployment config (default: ${p.env('DEPLOY_CONFIG')})")
    from grf.deploy.cli import add_parser as add_deploy_parser
    from grf.deploy.cli import cmd_deploy

    add_deploy_parser(sub, p.env_prefix)
    from grf.workflow import add_parser as add_workflow_parser
    from grf.workflow import command as workflow_command

    add_workflow_parser(sub)
    _, requested = common.parse_known_args(argv)
    core_commands = set(sub.choices)
    plugin_commands = p.plugin.add_commands(sub) if not requested or requested[0] not in core_commands else {}

    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "run":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if args.command == "run":
        return cmd_run(args, extra)
    commands = {"init": cmd_init, "ingest": cmd_ingest, "briefs": cmd_briefs, "drift": cmd_drift, "accept": cmd_accept, "migrate-hashes": cmd_migrate_hashes,
                "review": cmd_review, "lint": cmd_lint, "report": cmd_report, "mutate": cmd_mutate, "doctor": cmd_doctor,
                "request": cmd_request, "gate": cmd_gate, "scaffold": cmd_scaffold,
                "deploy": lambda a: cmd_deploy(a, p), "workflow": lambda a: workflow_command(a, p, main)}
    return {**commands, **plugin_commands}[args.command](args)


def main(argv: list[str] | None = None, *, project_root: Path | str | None = None) -> int:
    from robot.errors import DataError, Information

    from grf.pipeline.rvs import RvsError
    from grf.rigcontrol import RigControlError

    try:
        if project_root is not None:
            selected = project.Project.load(os.environ.get(project.PROJECT_ENV) or project_root)
            with project.using(selected):
                return _main(argv)
        return _main(argv)
    except Information as info:
        print(info.message)
        return 0
    except (OSError, ValueError, DataError, RvsError, RigControlError) as err:
        print(f"grf: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
