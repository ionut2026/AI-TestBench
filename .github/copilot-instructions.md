# Copilot working instructions

- Always work on the D: drive. Use the active checkout supplied by the session; Copilot
  worktree sessions must stay in their workspace and must not modify the main checkout.
  Never assume a fixed checkout path. Never read from or write to the C: drive for task
  output, scratch files, or any other work related to this project.
- Save every file generated from Windchill RV&S / MCP server data (exports, reports, lists) in
  the active checkout's `exports` directory. `rvs export` does this automatically.

- This repository is GeneralRobotFramework; its former name was AI-TestBench. The generic Python package is at
  `src\grf`. SMM and Windchill have moved to `ionut2026/SMMAutomationFramework` and
  `ionut2026/WindchillMcpServer`; do not add product code back into core.
- Product test runs and instrument-affecting actions require the instrument owner's authorization.

## Repository map

- This repository is the reusable Python 3.11+ core for specification-traced Robot Framework system testing.
  Installable package/CLI: `src\grf` / `grf`; entry point and command wiring are in `cli.py`.
- `project.py` loads each product's `grf.toml`; `plugin.py` provides optional product hooks. Generic RV&S
  ingestion, catalog/hash handling, briefs, drift, lint, human-review ledger, mutation orchestration and
  traceability reports are in `src\grf\pipeline`.
- `library.py`, `capabilities.py`, `doctor.py`, `rigcontrol.py` and `rig_ssh.py` supply generic Robot keywords,
  capability gating, checks and rig/operator helpers. `scaffold.py` and `templates` implement `grf init`.
- `examples\demo` is a synthetic counter consumer and fake RV&S fixture; it is not product evidence.
  `tests` cover package behavior, project/scaffold workflows, library behavior and CLI ingestion.
- `docs\ARCHITECTURE.md` and `docs\REPOSITORIES.md` describe the design and split. SMM adapters, product
  suites/catalogs and the Windchill server belong in their separate repositories, not here.
- For development, use `python -m pytest -q`, `ruff check src tests` and `mypy` (dev tools are in `.[dev]`).
  `.github\workflows\grf-ci.yml` is reusable CI; `grf-ci` tests this core and a generated consumer on
  Windows/Linux. Do not run product or hardware tests without the instrument owner's authorization.
