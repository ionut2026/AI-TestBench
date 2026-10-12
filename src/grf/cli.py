"""grf: command line of a General Robot Framework project (a product's command, e.g. smm-auto, is the same CLI).

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
  grf doctor   [--tier <tier>] [--operator ...] [--deep]    check that a tier can run (the project's checks, then the generic ones)
  grf report   --output results/.../output.xml              traceability report for an existing run
  grf mutate   [--mutant <id> ...] [--min-score 0.9]          mutation testing of the suites on the mock product
  (plus the commands of the project's plugin, e.g. SMM: matrix, service)

The project is the nearest grf.toml (or GRF_PROJECT); see grf.project.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from grf import project
from grf.capabilities import excluded_tags, tier_capabilities
from grf.operator_prompt import MODES as OPERATOR_MODES


def _default_operator() -> str:
    from grf.operator_prompt import operator_mode

    try:
        return operator_mode()
    except ValueError:
        return "none"


def _catalog_path(args) -> Path:
    if args.catalog:
        return Path(args.catalog)
    import tomllib

    with open(args.scope, "rb") as f:
        name = tomllib.load(f).get("name", "pilot")
    return project.current().path("catalog_dir") / f"{name}.json"


def _suites(args) -> list[Path]:
    return [Path(s) for s in args.suites] if args.suites else project.current().paths("suites")


def cmd_ingest(args) -> int:
    from grf.pipeline.ingest import ingest

    out = _catalog_path(args)
    catalog = ingest(Path(args.scope), out)
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
    done = migrate_hash_tags(moves, files, Path(args.reviews))
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
    paths = write_briefs(catalog, Path(args.out), tests, [int(s) for s in args.spec] if args.spec else None, name)
    print(f"{len(paths)} brief(s) written to {args.out}")
    return 0


def cmd_drift(args) -> int:
    from grf.pipeline.drift import check, collect_tests, format_text, load_reviews, problems
    from grf.pipeline.ingest import load_catalog
    from grf.pipeline.lint import lint

    catalog = load_catalog(_catalog_path(args))
    result = check(catalog, collect_tests(_suites(args)), load_reviews(Path(args.reviews)))
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
            entry = record_review(Path(args.reviews), t, args.reviewer, args.verdict, date, args.ref or "", args.notes or "")
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


def _report(catalog_path: Path, output_xml: Path, out_dir: Path, suites: list[Path], reviews: Path | None = None) -> dict:
    from grf.pipeline.drift import collect_tests, load_reviews
    from grf.pipeline.ingest import load_catalog
    from grf.pipeline.report import write_report

    reviews = reviews or project.current().path("reviews")
    report = write_report(load_catalog(catalog_path), output_xml, out_dir, collect_tests(suites), load_reviews(reviews))
    print(f"Traceability report: {out_dir / 'traceability.html'}")
    print("Specifications: " + ", ".join(f"{k} {v}" for k, v in report["summary"].items() if v))
    print(f"Verified (passed and human-reviewed): {report['coverage']['verified']}; "
          f"tests without human review: {report['review']['unreviewed']} of {report['review']['tests']}")
    if not report["productEvidence"]:
        print("NOTE: mock tier - the verdicts check the framework, they are not evidence about the product.")
    return report


def cmd_report(args) -> int:
    output = Path(args.output)
    _report(_catalog_path(args), output, Path(args.outdir) if args.outdir else output.parent, _suites(args), Path(args.reviews))
    return 0


def _exit_code(rc: int, output: Path, fail_on: str) -> int:
    """Robot's rc counts every failure; with ``--fail-on new`` only failures without ``known-issue:`` count."""
    if rc > 250 or not output.exists():
        return rc
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
    from robot.run import run_cli

    p = project.current()
    if args.processes < 1:
        print("--processes must be at least 1", file=sys.stderr)
        return 2
    parallel = [t.name for t in p.tiers.values() if t.parallel]
    if args.processes > 1 and args.tier not in parallel:
        # which tiers may run in parallel and why the others may not: [tiers] in grf.toml
        print(f"--processes {args.processes}: parallel runs are only possible on the {', '.join(parallel) or 'no'} tier "
              f"(see [tiers] in {p.root / project.CONFIG_FILE})", file=sys.stderr)
        return 2
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.outdir) if args.outdir else p.path("results") / f"{args.tier}-{stamp}"
    from grf.operator_prompt import env_name as operator_env_name
    from grf.rigcontrol import RigControlError

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
    options = {
        "variablefile": [str(p.path("environments") / f"{args.tier}.py")],
        "exclude": excludes,
        "outputdir": str(out_dir),
        "name": f"{p.name} {args.tier}",
        "metadata": [f"Run:{stamp}", f"Tier:{args.tier}"],
    }
    argv: list[str] = []
    for key, values in options.items():
        for value in values if isinstance(values, list) else [values]:
            argv += [f"--{key}", str(value)]
    argv += robot_args + [str(suite) for suite in _suites(args)]
    if args.processes > 1:
        from pabot.pabot import main_program

        # One robot process per suite, with this Python (the venv need not be on PATH); PabotLib (port 8270) is not used.
        argv = ["--processes", str(args.processes), "--no-pabotlib",
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
    output = out_dir / "output.xml"
    if output.exists() and not args.no_report:
        _report(_catalog_path(args), output, out_dir, _suites(args), Path(args.reviews))
    return _exit_code(rc, output, args.fail_on)


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

    try:
        mutants = load_mutants(Path(args.mutants))
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
    if sc["errors"]:
        return 2
    return 1 if sc["score"] is not None and sc["score"] < args.min_score else 0


def cmd_doctor(args) -> int:
    from grf.doctor import format_checks, run_doctor

    checks = run_doctor(args.tier, args.operator, args.deep)
    print(format_checks(checks))
    failed = [c.name for c in checks if c.status == "FAIL"]
    print(f"{args.tier} tier: " + (f"NOT READY ({', '.join(failed)})" if failed else "ready"))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    from grf.scaffold import add_parser, bootstrap, cmd_init

    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "init":
        return bootstrap(argv)
    try:
        p = project.current()
    except project.ProjectError as err:
        if argv in (["--help"], ["-h"]):
            return bootstrap(argv)
        print(f"grf: {err}", file=sys.stderr)
        return 2
    tiers = list(p.tiers)
    parser = argparse.ArgumentParser(prog=p.cli, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)
    scope, reviews = p.raw_paths("scope")[0], p.raw_paths("reviews")[0]
    parser.add_argument("--scope", default=str(p.path("scope")), help=f"scope file (default: {scope})")
    parser.add_argument("--catalog", help="catalog JSON (default: catalog/<scope name>.json)")
    parser.add_argument("--suites", action="append", help=f"suite file/directory, repeatable (default: {', '.join(p.raw_paths('suites'))})")
    parser.add_argument("--reviews", default=str(p.path("reviews")), help=f"review ledger (default: {reviews})")
    sub = parser.add_subparsers(dest="command", required=True)
    add_parser(sub)

    sub.add_parser("ingest", allow_abbrev=False, help="read specifications, requirements, user stories and ETs from RV&S")
    cmd = sub.add_parser("briefs", allow_abbrev=False, help="write generation briefs for the test-authoring agent")
    cmd.add_argument("--spec", nargs="*", help="only these specification IDs")
    cmd.add_argument("--out", default=str(p.path("briefs")))
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
    cmd.add_argument("--tier", choices=tiers, default=tiers[0])
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
                     help="run suites in N parallel processes with pabot (mock tier only; each worker gets its own service and broker)")
    cmd = sub.add_parser("report", allow_abbrev=False, help="traceability report for an existing output.xml")
    cmd.add_argument("--output", required=True)
    cmd.add_argument("--outdir")
    cmd = sub.add_parser("mutate", allow_abbrev=False, help=f"mutation testing: inject defects into the mock {p.product} and check that the tests notice")
    mock_tiers = [t.name for t in p.tiers.values() if not t.evidence]
    cmd.add_argument("--tier", choices=mock_tiers, default=mock_tiers[0] if mock_tiers else None,
                     help=f"only the mock {p.product} supports fault injection")
    cmd.add_argument("--mutants", default=str(p.path("mutants")))
    cmd.add_argument("--mutant", action="append", help="only this mutant id (repeatable)")
    cmd.add_argument("--outdir")
    cmd.add_argument("--min-score", type=float, default=0.9, help="exit code 1 when the share of killed mutants is lower (default 0.9)")
    cmd.add_argument("--no-baseline", action="store_true", help="skip the run without faults (the selected tests must pass without faults)")
    cmd = sub.add_parser("doctor", allow_abbrev=False, help="check that a tier can run before a long run (exit code 1 on FAIL)")
    cmd.add_argument("--tier", choices=tiers, default=tiers[0])
    cmd.add_argument("--operator", choices=OPERATOR_MODES, default=_default_operator())
    cmd.add_argument("--deep", action="store_true", help="also start the environment and talk to the product (see the project's doctor; "
                                                       "on a rig this may connect to the instrument)")
    plugin_commands = p.plugin.add_commands(sub)

    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "run":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if args.command == "run":
        return cmd_run(args, extra)
    commands = {"init": cmd_init, "ingest": cmd_ingest, "briefs": cmd_briefs, "drift": cmd_drift, "accept": cmd_accept, "migrate-hashes": cmd_migrate_hashes,
                "review": cmd_review, "lint": cmd_lint, "report": cmd_report, "mutate": cmd_mutate, "doctor": cmd_doctor}
    return {**commands, **plugin_commands}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
