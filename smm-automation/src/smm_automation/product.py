"""The SMM project's plugin (``[project] plugin`` in grf.toml): what the generic framework does not know about SMM —
the ICD (message names and schemas), the SMM doctor checks and the ``matrix`` and ``service`` commands."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from smm_automation import icd
from smm_automation.plugin import Command, ProductPlugin


class SmmPlugin(ProductPlugin):
    def known_messages(self) -> set[str] | None:
        return set(icd.message_names())

    def brief_sections(self, spec: dict) -> list[str]:
        out = ["## ICD schemas", ""]
        for name in spec["messages"]:
            schema = icd.schema(name)
            if schema is not None:
                out += [f"### {name}", "", "```json", json.dumps(schema, indent=2), "```", ""]
        return out

    def doctor_class(self):
        from smm_automation.smm_doctor import SmmDoctor

        return SmmDoctor

    def add_commands(self, sub: argparse._SubParsersAction) -> dict[str, Command]:
        p = self.project
        cmd = sub.add_parser("matrix", allow_abbrev=False, help="generate the state x request matrix suite and its open questions")
        cmd.add_argument("--model", default=str(p.path("matrix_model")))
        cmd.add_argument("--suite", default=str(p.path("matrix_suite")))
        cmd.add_argument("--questions", default=str(p.path("matrix_questions")))
        cmd.add_argument("--check", action="store_true", help="only check that the generated files are up to date (exit code 1 if not)")
        cmd = sub.add_parser("service", allow_abbrev=False, help="run the automation service in the foreground")
        cmd.add_argument("--port", type=int, default=8765)
        return {"matrix": self.cmd_matrix, "service": self.cmd_service}

    def cmd_matrix(self, args: argparse.Namespace) -> int:
        from smm_automation.cli import _catalog_path, _suites
        from smm_automation.pipeline.drift import collect_tests, load_reviews
        from smm_automation.pipeline.ingest import load_catalog
        from smm_automation.pipeline.matrix import MatrixError, generate, load_model, validate, write

        try:
            model = load_model(Path(args.model))
        except (MatrixError, TypeError) as err:
            print(f"{args.model}: {err}", file=sys.stderr)
            return 2
        catalog = load_catalog(_catalog_path(args))
        errors = validate(model, catalog, collect_tests(_suites(args)))
        if errors:
            print("\n".join(f"{args.model}: {e}" for e in errors), file=sys.stderr)
            return 2
        paths = {"suite": Path(args.suite), "questions": Path(args.questions)}
        differ = write(generate(model, catalog, load_reviews(Path(args.reviews))), paths, check=args.check)
        if args.check:
            for path in differ:
                print(f"OUT OF DATE: {path} (run {self.project.cli} matrix)")
            return 1 if differ else 0
        generated = sum(1 for c in model.cells if c.generated)
        print(f"{len(model.cells)} cells, {generated} generated test(s); written: {', '.join(map(str, differ)) or '(unchanged)'}")
        return 0

    def cmd_service(self, args: argparse.Namespace) -> int:
        dist = self.project.root / "service" / "dist" / "smm-automation-service.mjs"
        if not dist.exists():
            print(f"{dist} not found: run `npm install && npm run build` in {dist.parent.parent}", file=sys.stderr)
            return 2
        return subprocess.call(["node", str(dist), "--port", str(args.port)])
