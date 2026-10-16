# General Robot Framework (GRF)

Current release: **v0.4.3**. See the [changelog](CHANGELOG.md) for release dates and changes.

**New to Robot Framework? Start with the [beginner walkthrough](docs/GETTING-STARTED.md).**
It explains the terminology, reads a small test line by line, and guides you through a safe first run.

Reusable infrastructure for specification-traced Robot Framework system tests. GRF owns configuration, CLI,
catalog ingestion, briefs, drift/lint gates, review ledgers, reports, capability selection and timing/rig helpers.
Each product owns its adapters, keywords, specifications, environments and suites.
Controlled evidence uses [typed product validation and explicit assertion coverage](docs/WORKFLOWS.md#evidence-integrity-and-assertion-coverage-04-migration).
Owner-authorized deployment/testing can use the [maintained bounded workflow](docs/WORKFLOWS.md#maintained-deploy-and-test-workflow);
it does not grant instrument permission or commission a Jenkins site.

In plain language: Robot Framework runs readable test steps; GRF adds project organization and links those
tests to the written behavior they should check. Product code supplies the steps that interact with your product.
Product projects such as SMMAutomationFramework install GRF as a dependency rather than copying its code.
See [why GRF exists and how products use it](docs/GETTING-STARTED.md#why-does-this-framework-exist)
for an explained SMM example and the workflow for future products.

The direct checkout is **`D:\projects\GeneralRobotFramework`**. Preserve it and its uncommitted work;
coordinated changes honor the user's direct-main or branch/PR workflow, not edits to another session's checkout.
Direct-main work has one writer per repository; inspect existing work and ask on conflicts before transferring edits.
Core does not contain SMM product code or a Windchill MCP server. The counter demo contacts neither.

## Install and try the mock starter (Windows)

First clone the repository (Git and Python 3.11 or newer must already be installed):

```powershell
git clone https://github.com/ionut2026/GeneralRobotFramework.git D:\projects\GeneralRobotFramework
Set-Location D:\projects\GeneralRobotFramework
.\scripts\setup.ps1
```

For an existing checkout, skip `git clone` and run the setup command from its root.
These source-install instructions use the checked-out `main` code, currently v0.4.3; they do not require a
remote release tag. Setup should print GRF `0.4.3`. Starter products pin `general-robot-framework==0.4.3`;
that pin requires an approved package source or supplied wheel, not merely a Git tag.
**Cloning downloads source files; setup installs the packages needed to use them.**
Git does not execute this script automatically. Setup creates or reuses `.venv`, installs GRF,
Robot Framework and development tools, checks dependency compatibility, and prints the installed versions
and Robot's location. It does not install Python itself, select a product, or start tests or services.
See [installation details and troubleshooting](docs/GETTING-STARTED.md#4-prepare-a-safe-practice-project).

After setup, create and run the mock starter from this checkout:

```powershell
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
| [Getting started](docs/GETTING-STARTED.md) | Beginner concepts, explained demo commands, test syntax and reading results |
| [Architecture](docs/ARCHITECTURE.md) | Repository/product trees, component ownership and execution flow |
| [Configuration](docs/CONFIGURATION.md) | Project discovery, paths, tiers, plugins and external Windchill integration |
| [Testing](docs/TESTING.md) | Traceability, human review, evidence rules, mutation testing and validation commands |
| [Copilot skills](docs/SKILLS.md) | Available GRF skills, how to invoke them, product copies and safety limits |
| `grf scaffold --update-skills` | [Reviewed, conflict-safe generic skill updates](docs/SKILLS.md#how-skills-reach-product-projects); product overlays are preserved |
| [Repositories](docs/REPOSITORIES.md) | Independent repositories and data ownership |
| [Deploying builds](docs/DEPLOYMENT.md) | `grf deploy`: Jenkins builds to instrument boards, rollback, site config, Jenkins pipeline |
| [Workflows](docs/WORKFLOWS.md) | Run requests, acceptance (`grf gate`), content-bound reviews, tier reservation and opt-in host-wide resource leases |
| [Contributing](CONTRIBUTING.md) | Change scope, documentation maintenance, compatibility and review policy |
| [Changelog](CHANGELOG.md) | User-visible changes and migration notes |

Controlled product runs use `grf run --request <file>` to bind exact selection and provenance, then
`grf gate --request <file> --output <output.xml>` for acceptance. See [Workflows](docs/WORKFLOWS.md);
neither a request nor a mock pass authorizes an instrument operation.

## Workflow

`ingest -> briefs -> author -> independent review -> human approval -> run -> report`

Use `lint` and `drift --strict` as gates. Mock tiers never supply product evidence; only current-hash human
approval counts as review. Product and instrument-affecting operations require the owner's authorization.
GRF reads RV&S and writes local artifacts; it never publishes results back to RV&S.

Before testing on an instrument, `grf deploy` can install a Jenkins build on its boards, with automatic
rollback, and `--deployment` attaches build provenance to the report; it does not check the running product.
An opt-in `Deployment Record Should Match Product Version` keyword checks a product-supplied version
against the record. Deploying changes the instrument and needs the owner's authorization;
see [Deploying builds](docs/DEPLOYMENT.md#checking-the-observed-product-version).

A finished run is not automatically an accepted result. `grf request check` validates a run request (tier,
required specifications/tests, exact builds, owner authorization) and `grf gate` accepts a run only when it
satisfies the request: see [Workflows](docs/WORKFLOWS.md).

Live ingestion is optional: install `.[windchill]`, explicitly configure an **external** MCP server and provide
its connection settings. Generic development and the demo do not need the server or MCP SDK.

## Development

See [Testing](docs/TESTING.md#core-validation-windows) for the core checks and wheel-only smoke run.
For explicitly requested cross-repository work, select the reusable `main-agent-orchestrator` agent and use
the matching skill; see [invocation, adoption and limits](docs/SKILLS.md#coordinated-integration).
These files do not start workers, grant permissions or automatically reconnect sessions.
`.github\workflows\grf-ci.yml` provides reusable Windows/Linux checks through `workflow_call`. Product CI
separately installs its approved GRF release and validates its own adapter.
