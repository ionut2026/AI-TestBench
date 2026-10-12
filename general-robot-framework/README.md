# General Robot Framework (GRF)

Reusable infrastructure for specification-traced Robot Framework system tests. Each product owns its adapters,
keywords, specifications and suites; GRF owns configuration, the CLI, read-only RV&S ingestion, briefs, drift,
lint, reviews, reporting, capabilities, rig control and operator/timing helpers.

GRF does not import SMM, start a product service or assume a particular ICD, simulator or instrument.
SMM is the first consumer. The synthetic counter demo is the second.

## Install and start a product (Windows)

From `D:\projects\AI-TestBench\general-robot-framework`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\grf init D:\projects\MyProductAutomation --name "My Product" --spec-prefix SPEC
.\.venv\Scripts\python -m pip install --no-deps -e D:\projects\MyProductAutomation
$env:GRF_PROJECT = "D:\projects\MyProductAutomation"
.\.venv\Scripts\grf demo-ingest
.\.venv\Scripts\grf briefs
.\.venv\Scripts\grf lint
.\.venv\Scripts\grf drift --strict
.\.venv\Scripts\grf run --tier mock --outdir results\my-product-demo
```

`init` works outside any project and refuses a nonempty destination. Templates, including author/reviewer agents,
are shipped in the wheel. The starter README explains how to replace the counter with your real product.
No real RV&S or hardware access is needed for the demo. GRF output is local; it never publishes results to RV&S.

The checked-in demo is in `examples\demo`. Install it with `python -m pip install --no-deps -e examples\demo`,
set `GRF_PROJECT` to its absolute path and use the same commands. Its fixture is entirely synthetic.

## Core/product boundary

| Core | Product |
|---|---|
| `grf.project`: TOML config and project discovery | `grf.toml`: names, paths, tiers, capabilities, lint tag rules |
| `grf.plugin.ProductPlugin`: optional hooks | Known message names, extra brief sections, doctor subclass, extra commands |
| `grf.pipeline`: RV&S catalog, hashes, briefs, drift, lint, reviews, mutation runner, reports | Scope, rules, keyword docs, review ledger, test suites, supported mock faults |
| `grf.library.GrfLibrary`: timing, operator actions, rig helpers, test/segment bookkeeping | Product connection, setup/teardown, stimulus marks, observable assertions |
| `grf.rigcontrol` / `grf.rig_ssh`: generic site protocol | Site commands, restart targets, readiness ports, log formats; credentials outside repositories |

Project selection is `GRF_PROJECT` (file or directory), otherwise the default root registered by a product package,
otherwise the nearest `grf.toml` in the working directory or parents. It is cached per process; `project.use(None)`
resets it. `grf run` passes the chosen root to Robot workers. Paths in TOML are relative to the configuration file;
explicit CLI paths are relative to the current working directory. Run product commands from its root or set
`GRF_PROJECT`. `grf init` does not depend on project selection.

For real ingestion, set `WINDCHILL_MCP_SERVER` to the installed read-only MCP server's JavaScript entry point and
provide its `RVS_*` environment settings. It is an external integration, not a GRF dependency to bundle.
Use `grf ingest` for live data; `demo-ingest` is an example plugin command only.

## Trust and workflow

`ingest -> briefs -> author -> independent review -> human approval -> run -> report`, with `drift --strict`
and `lint` as gates. Tags carry specification IDs and current normalized-text hashes. Linked requirements,
stories and explorative tests come from the catalog, never duplicate tags.

Tests start `review:pending`; agent approvals are information only. A human records a current-hash approval
with `grf review` and removes the pending tag in the same reviewed change. Do not update stale hashes automatically.
Mock tiers have `evidence = false`: a green run proves the framework wiring, not product conformance.
Real-product and hardware runs require the owner's authorization.

Capabilities exclude only tests that explicitly declare the missing capability. Products should configure
lint tag rules so an undeclared need is an error. Never hide a product failure with conditional assertions.
`grf doctor --deep` is a product hook and may affect hardware; generic doctor alone does not certify a product connection.

## Development and CI

```powershell
.\.venv\Scripts\ruff check src tests
.\.venv\Scripts\mypy
New-Item -ItemType Directory -Force results | Out-Null
.\.venv\Scripts\python -m pytest -q --basetemp results\pytest
.\.venv\Scripts\python -m pip wheel --no-deps -w dist .
```

Independent tests scaffold a custom-prefix product, build its fake catalog, produce briefs, run Robot, validate
the report's links/non-evidence status, and check that changed hashes and broken assertions fail.
Another test exercises the real ingest CLI using a fake RV&S client.

`.github\workflows\grf-ci.yml` is reusable with `workflow_call`; the caller supplies `framework-path` (`.` after
extraction, `general-robot-framework` in this staged checkout). It checks GRF on Windows/Linux with no SMM installed,
then installs the built wheel in an isolated environment and runs a generated product.
Product CI must separately install its pinned GRF release and test its own adapter; the SMM workflow remains separate.
The split plan and approval boundaries are in `..\docs\FRAMEWORK-SPLIT-PLAN.md`. GRF is not yet a separate remote repo.
