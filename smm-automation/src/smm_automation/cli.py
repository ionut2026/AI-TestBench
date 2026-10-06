"""smm-auto: command line of the SMM test automation framework.

  smm-auto ingest   [--scope catalog/pilot.scope.toml]          RV&S -> catalog/<scope>.json
  smm-auto briefs   [--spec 2528698 ...]                         catalog -> generated/briefs/SDS-<id>.md
  smm-auto drift    [--strict]                                   suites vs catalog and review ledger (stale/orphan/uncovered/unrecorded) + lint
  smm-auto accept   (<spec id> ... | --all)                       accept RV&S state/link changes of specifications as the new baseline
  smm-auto migrate-hashes --from <old catalog.json>               re-tag tests whose spec text is unchanged but whose hash changed
  smm-auto lint                                                  test rules (no Sleep, documentation, timing variables, tier tags)
  smm-auto run      [--tier mock|offline|rig] [--fail-on new|any|none] [robot args...]
                                                                 run suites, then the traceability report
  smm-auto report   --output results/.../output.xml              traceability report for an existing run
  smm-auto service                                               run the automation service in the foreground
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from smm_automation import FRAMEWORK_ROOT

DEFAULT_SCOPE = FRAMEWORK_ROOT / "catalog" / "pilot.scope.toml"
REVIEWS = FRAMEWORK_ROOT / "catalog" / "reviews.toml"
TIER_EXCLUDES = {"mock": ["needs:twin"], "offline": [], "rig": ["needs:twin", "requires:restart"]}


def _catalog_path(args) -> Path:
    if args.catalog:
        return Path(args.catalog)
    import tomllib

    with open(args.scope, "rb") as f:
        name = tomllib.load(f).get("name", "pilot")
    return FRAMEWORK_ROOT / "catalog" / f"{name}.json"


def _suites(args) -> list[Path]:
    return [Path(s) for s in args.suites] if args.suites else [FRAMEWORK_ROOT / "robot" / "suites"]


def cmd_ingest(args) -> int:
    from smm_automation.pipeline.ingest import ingest

    out = _catalog_path(args)
    catalog = ingest(Path(args.scope), out)
    print(f"Catalog written to {out}: {json.dumps(catalog['counts'])}")
    changes = catalog.get("changes")
    if changes and any(changes.get(k) for k in ("added", "removed", "textChanged", "stateChanged", "linksChanged")):
        print(f"Changes since {changes['since']}: {json.dumps({k: v for k, v in changes.items() if k != 'since'})}")
    return 0


def cmd_accept(args) -> int:
    from smm_automation.pipeline.ingest import accept_changes, load_catalog, write_catalog

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
    from smm_automation.pipeline.drift import hash_migrations, migrate_hash_tags
    from smm_automation.pipeline.ingest import load_catalog, normalize_text

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
    from smm_automation.pipeline.briefs import write_briefs
    from smm_automation.pipeline.drift import collect_tests
    from smm_automation.pipeline.ingest import load_catalog

    catalog = load_catalog(_catalog_path(args))
    tests = collect_tests(_suites(args))
    name = (catalog.get("scope") or {}).get("name") or "pilot"
    paths = write_briefs(catalog, Path(args.out), tests, [int(s) for s in args.spec] if args.spec else None, name)
    print(f"{len(paths)} brief(s) written to {args.out}")
    return 0


def cmd_drift(args) -> int:
    from smm_automation.pipeline.drift import check, collect_tests, format_text, load_reviews, problems
    from smm_automation.pipeline.ingest import load_catalog
    from smm_automation.pipeline.lint import lint

    catalog = load_catalog(_catalog_path(args))
    result = check(catalog, collect_tests(_suites(args)), load_reviews(Path(args.reviews)))
    result["lint"] = [v.as_dict() for v in lint(_suites(args), catalog)]
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(format_text(result))
    return 1 if args.strict and problems(result) else 0


def cmd_lint(args) -> int:
    from smm_automation.pipeline.ingest import load_catalog
    from smm_automation.pipeline.lint import format_text, lint

    catalog_path = _catalog_path(args)
    violations = lint(_suites(args), load_catalog(catalog_path) if catalog_path.exists() else None)
    print(format_text(violations, FRAMEWORK_ROOT))
    return 1 if violations else 0


def _report(catalog_path: Path, output_xml: Path, out_dir: Path, suites: list[Path], reviews: Path = REVIEWS) -> dict:
    from smm_automation.pipeline.drift import collect_tests, load_reviews
    from smm_automation.pipeline.ingest import load_catalog
    from smm_automation.pipeline.report import write_report

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
    from smm_automation.pipeline.report import classify, is_product_tier, read_results

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

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.outdir) if args.outdir else FRAMEWORK_ROOT / "results" / f"{args.tier}-{stamp}"
    excludes = list(TIER_EXCLUDES[args.tier])
    if args.exclude_pending:
        excludes.append("review:pending")
    options = {
        "variablefile": [str(FRAMEWORK_ROOT / "robot" / "environments" / f"{args.tier}.py")],
        "exclude": excludes,
        "outputdir": str(out_dir),
        "name": f"SMM {args.tier}",
        "metadata": [f"Run:{stamp}"],
    }
    argv: list[str] = []
    for key, values in options.items():
        for value in values if isinstance(values, list) else [values]:
            argv += [f"--{key}", str(value)]
    argv += robot_args + [str(p) for p in _suites(args)]
    print("robot " + " ".join(argv))
    rc = run_cli(argv, exit=False)
    output = out_dir / "output.xml"
    if output.exists() and not args.no_report:
        _report(_catalog_path(args), output, out_dir, _suites(args), Path(args.reviews))
    return _exit_code(rc, output, args.fail_on)


def cmd_service(args) -> int:
    dist = FRAMEWORK_ROOT / "service" / "dist" / "smm-automation-service.mjs"
    if not dist.exists():
        print(f"{dist} not found: run `npm install && npm run build` in {dist.parent.parent}", file=sys.stderr)
        return 2
    return subprocess.call(["node", str(dist), "--port", str(args.port)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smm-auto", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)
    parser.add_argument("--scope", default=str(DEFAULT_SCOPE), help="scope file (default: catalog/pilot.scope.toml)")
    parser.add_argument("--catalog", help="catalog JSON (default: catalog/<scope name>.json)")
    parser.add_argument("--suites", action="append", help="suite file/directory, repeatable (default: robot/suites)")
    parser.add_argument("--reviews", default=str(REVIEWS), help="review ledger (default: catalog/reviews.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ingest", allow_abbrev=False, help="read specifications, requirements, user stories and ETs from RV&S")
    p = sub.add_parser("briefs", allow_abbrev=False, help="write generation briefs for the test-authoring agent")
    p.add_argument("--spec", nargs="*", help="only these specification IDs")
    p.add_argument("--out", default=str(FRAMEWORK_ROOT / "generated" / "briefs"))
    p = sub.add_parser("drift", allow_abbrev=False, help="compare suites with the catalog")
    p.add_argument("--strict", action="store_true", help="exit code 1 on stale/orphan/untagged/uncovered/unrecorded review or a lint violation")
    p.add_argument("--json", help="also write the result as JSON")
    p = sub.add_parser("accept", allow_abbrev=False, help="accept RV&S state/link changes of specifications as the new baseline")
    p.add_argument("ids", nargs="*", type=int, help="specification IDs")
    p.add_argument("--all", action="store_true", help="every specification")
    p = sub.add_parser("migrate-hashes", allow_abbrev=False, help="update spechash tags after a hashing change (same text, new hash)")
    p.add_argument("--from", dest="old", required=True, help="the catalog JSON before the re-ingestion")
    sub.add_parser("lint", allow_abbrev=False, help="check the test rules (exit code 1 on violations)")
    p = sub.add_parser("run", allow_abbrev=False, help="run suites against a tier and write the traceability report (extra args go to robot)")
    p.add_argument("--tier", choices=sorted(TIER_EXCLUDES), default="mock")
    p.add_argument("--outdir")
    p.add_argument("--fail-on", choices=["new", "any", "none"], default="new",
                   help="exit code: number of NEW failures (default; known-issue failures do not count), of ANY failures, or 0")
    p.add_argument("--exclude-pending", action="store_true", help="do not run tests tagged review:pending (they run and are labelled UNREVIEWED by default)")
    p.add_argument("--include-pending", action="store_true", help=argparse.SUPPRESS)  # pre-1.1 option, now the default
    p.add_argument("--no-report", action="store_true")
    p = sub.add_parser("report", allow_abbrev=False, help="traceability report for an existing output.xml")
    p.add_argument("--output", required=True)
    p.add_argument("--outdir")
    p = sub.add_parser("service", allow_abbrev=False, help="run the automation service in the foreground")
    p.add_argument("--port", type=int, default=8765)

    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "run":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if args.command == "run":
        return cmd_run(args, extra)
    return {"ingest": cmd_ingest, "briefs": cmd_briefs, "drift": cmd_drift, "accept": cmd_accept, "migrate-hashes": cmd_migrate_hashes,
            "lint": cmd_lint, "report": cmd_report, "service": cmd_service}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
