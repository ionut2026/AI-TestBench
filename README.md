# General Robot Framework (GRF)

Reusable infrastructure for specification-traced Robot Framework system tests. GRF owns configuration, CLI,
catalog ingestion, briefs, drift/lint gates, review ledgers, reports, capability selection and timing/rig helpers.
Each product owns its adapters, keywords, specifications, environments and suites.

This checkout is **`D:\projects\GeneralRobotFramework`**, used directly on **`main`**. Core does not contain SMM
product code or a Windchill MCP server. The included counter demo is synthetic and does not contact either.

## Install and try the mock starter (Windows)

From this checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m grf init results\my-product --name "My Product" --spec-prefix SPEC
.\.venv\Scripts\python -m pip install --no-deps -e results\my-product
$env:GRF_PROJECT = (Resolve-Path results\my-product).Path
.\.venv\Scripts\python -m grf demo-ingest
.\.venv\Scripts\python -m grf briefs
.\.venv\Scripts\python -m grf lint
.\.venv\Scripts\python -m grf drift --strict
.\.venv\Scripts\python -m grf doctor --tier mock
.\.venv\Scripts\python -m grf run --tier mock --outdir results\my-product-mock
```

`init` refuses a nonempty destination and works outside a configured product. Use a new output directory on
each run: GRF refuses to reuse an existing `output.xml`. The starter README describes replacing the counter
with a real adapter. For a persistent product, scaffold its own repository rather than keeping it under `results`.

The checked-in equivalent is `examples\demo`: install it with `python -m pip install --no-deps -e examples\demo`
and set `GRF_PROJECT` to its absolute path. Starter templates are packaged in the wheel and tested against this
example. Both examples use the real catalog/report pipeline with fake input; neither proves product conformance.

## Documentation

| Guide | Canonical topic |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Repository/product trees, component ownership and execution flow |
| [Configuration](docs/CONFIGURATION.md) | Project discovery, paths, tiers, plugins and external Windchill integration |
| [Testing](docs/TESTING.md) | Traceability, human review, evidence rules, mutation testing and validation commands |
| [Repositories](docs/REPOSITORIES.md) | Independent repositories and data ownership |
| [Contributing](CONTRIBUTING.md) | Change scope, documentation maintenance, compatibility and review policy |
| [Changelog](CHANGELOG.md) | User-visible changes and migration notes |

## Workflow

`ingest -> briefs -> author -> independent review -> human approval -> run -> report`

Use `lint` and `drift --strict` as gates. Mock tiers never supply product evidence; only current-hash human
approval counts as review. Product and instrument-affecting operations require the owner's authorization.
GRF reads RV&S and writes local artifacts; it never publishes results back to RV&S.

Live ingestion is optional: install `.[windchill]`, explicitly configure an **external** MCP server and provide
its connection settings. Generic development and the demo do not need the server or MCP SDK.

## Development

See [Testing](docs/TESTING.md#core-validation-windows) for the core checks and wheel-only smoke run.
`.github\workflows\grf-ci.yml` provides reusable Windows/Linux checks through `workflow_call`. Product CI
separately installs its approved GRF release and validates its own adapter.
