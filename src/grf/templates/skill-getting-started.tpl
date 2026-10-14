---
name: grf-getting-started
description: Helps beginners install GRF or a GRF-based product project and run the synthetic mock demo safely on Windows.
---

# GRF getting started

Use this skill when someone has just cloned GRF or a product project, is new to Robot Framework, or asks how to
run the demo. Canonical guide: `docs\GETTING-STARTED.md` in the GRF checkout.

## Explain the roles first

- **Robot Framework** runs `.robot` test files and produces `output.xml`, `log.html` and `report.html`.
- **GRF** (GeneralRobotFramework) adds specification tracing, briefs, lint, drift, review bookkeeping and reports.
- **Product project** code supplies the real product keywords, adapters, suites and product knowledge.
- A **mock tier** uses a fake product. Mock results are never product evidence.

## Steps

1. Confirm the working directory is the checkout root (the folder containing `pyproject.toml` or `grf.toml`).
2. In GRF, run `.\scripts\setup.ps1` from PowerShell. Success ends with `Setup complete.` It creates `.venv`
   and installs Robot Framework into that environment, not into Git.
3. In a product starter, follow its README: install the project, then run `grf demo-ingest`, `grf briefs`,
   `grf lint`, `grf drift --strict`, `grf doctor --tier mock` and `grf run --tier mock --outdir results\mock`.
4. Explain each command's purpose and the expected success output before or after running it.
5. Point to `results\mock\report.html`, `log.html` and `traceability.html`, and explain what each shows.

## Safety

- Use a new output directory for every run; GRF refuses stale `output.xml` files.
- RV&S access is read-only. Never write product data into GRF core.
- Do not run real-product, rig or instrument tiers without the instrument owner's authorization.
- A passing test is not approved until a human records approval for its current specification hash.
