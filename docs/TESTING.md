# Testing and evidence

Start with [Getting started](GETTING-STARTED.md) for a small test explained line by line and a first mock run.
Here, **evidence** means results that can support a claim about an actual product, not just the demo.
A **gate** is a check that must succeed before you proceed; this guide describes GRF's detailed testing rules.

## Authoring contract

Specifications are the oracle (the source of the expected behavior), not mock behavior.
Each traced Robot test carries `<SPEC_PREFIX>-<id>` and
`spechash:<hash>`; multi-specification tests use `spechash:<id>:<hash>` for each specification.
Hashes anchor normalized specification text. Requirements, stories and explorative-test links come from the
catalog, not duplicated tags. Use `nospec:<kind>` only for explicitly documented behavior without a specification.

Tests start with `review:pending`. An independent agent review is informational. A human records approval
with `grf review`, then removes the pending tag in the same reviewed change. Newly recorded entries include
the suite as well as the test name. Legacy name-only reviews count only when that name is unique across
the selected suites; otherwise re-record suite-qualified reviews. The latest human verdict for the current
hash wins, so an old approval cannot override a later rejection. Never auto-update stale hashes to turn gates green.

Since GRF 0.3, `grf review` also records the test's content `fingerprint`: a SHA-256 over the test (without
`review:pending`, `spechash:` tags and line numbers), its suite's settings, keywords and variables, and every Robot
resource it imports (transitively). Editing the test or a resource it imports after the review makes the review
count no more (`drift --strict` reports "changed after the human review"). Reviews recorded before 0.3 have no
fingerprint: `drift` still counts them, the acceptance gate (`grf gate`) does not; the reviewer records them again.
No command creates or migrates an approval automatically. See [Workflows](WORKFLOWS.md).

`lint` checks documentation, forbidden sleeps, literal timing values and configured capability/keyword rules,
following nested resource imports with cycle protection. `drift --strict` checks hashes, missing coverage,
orphan/untagged tests, changed state/links, retired specifications, missing human approval and lint violations.

Static resource imports support `${CURDIR}`, `${/}` and either `.resource` or keyword-only `.robot` files.
Keyword resolution is local to each suite/import context, including qualified resource names and transitive
calls. Duplicate callable definitions are never overwritten: ambiguous user calls report rule `00` and
all possible bodies are conservatively analyzed. Missing or dynamically unresolved resources also report
`00` rather than implying complete analysis. Use resolvable static resource paths for strict lint.
Default test setup/teardown, suite setup/teardown and inherited directory initializers participate in
timing/sleep and capability checks; explicit `NONE` cancels an inherited test lifecycle setting.

Catalogs, review ledgers and deployment records are validated when loaded, with the input path in
malformed-file diagnostics. Unknown schemas, boolean schema numbers and malformed nested tables/lists
are rejected before drift/report/gate processing. Schema 1 (including unversioned legacy) catalogs remain
readable for baseline/hash migration; schema 2 requires `hashVersion`. Legacy reviews without a verdict
or content fingerprint remain readable for drift, but do not become current-content acceptance approvals.
Deployment structural validation does not promote an unsuccessful outcome: binding and strict acceptance
still apply their distinct installed-build policies.
Pending and suspect entries are reported for review; pending alone does not fail the strict drift gate.

## Execution and reports

Choose the tier explicitly for product work. Capabilities exclude only tests declaring an unavailable need.
Do not hide failures behind conditional assertions. Products own setup/teardown, state reset and worker isolation.
Run product/instrument tiers only with owner authorization.

`grf run` owns result paths, environment files, root name and Tier metadata; do not override them with forwarded
Robot options. Filters such as `--include`, custom non-Tier metadata and console/report formatting remain available.
GRF uses Robot's own parser to validate abbreviated flags, argument files and `ROBOT_OPTIONS` as well.
Select suite sources through the global `--suites` option, not forwarded Robot positional arguments.
Robot `--dryrun` is allowed only on non-evidence tiers; syntax validation cannot become product evidence.
An output directory containing `output.xml` is rejected before execution to prevent stale results being mistaken
for a new run. Use a new directory, or process existing output with `grf report`.

| Result | Meaning |
|---|---|
| PASS | Every known test for the specification ran and passed |
| PARTIAL | Some tests passed, but others were skipped or did not run |
| VERIFIED | PASS on a known evidence tier, current hashes, human-approved tests and no stale/state/link/retired/suspect specification issues |
| Mock PASS | Framework/adapter wiring only; never VERIFIED product evidence |

Unrecorded reviews, missing hashes and stale hashes must not produce VERIFIED even if pending tags were removed.
Suite-qualified identities prevent equally named tests in different child suites from hiding missing tests.
Identity uses the actual child hierarchy, not punctuation in a renamed root; dotted product names remain valid.
The report preserves raw verdicts, review warnings, drift details and `productEvidence`.

`--fail-on new` counts untagged failures but ignores `known-issue:<id>` failures only on evidence tiers.
Every mock failure is new. `--fail-on any` preserves Robot's failure count; `none` ignores test verdict failures
but not execution errors. CLI input/configuration/integration errors return 2 with a diagnostic; drift/lint
gate failures return 1. Robot execution error codes above 250 are preserved.

A finished run is not yet an accepted result. `grf gate --request <request.toml> --output <output.xml>` checks the
run against a run request (tier, required tests and specifications, skips/failures, current content-bound human
reviews, drift, exact deployed builds and runtime versions) and writes `acceptance.json`; see [Workflows](WORKFLOWS.md).

## Mutation testing

Mutation testing deliberately introduces a defect into a mock to see whether your tests notice it.
That deliberate defect is a **mutant**; if an assertion catches it, the mutant is called **killed**.
This is an advanced check of test effectiveness, not a prerequisite for your first demo run.

Mutation runs require a configured non-evidence tier and product-supported fault rules.
A clean baseline must execute tests and return success; skipped or failed baseline tests abort the analysis.
Setup/teardown failures, missing output, timeouts and abnormal process exits are infrastructure errors, not killed mutants.
Mixed passing/skipped mutant tests are an incomplete analysis, not a surviving mutant.
Existing mutation `output.xml` files are never reused. Mutation children receive the selected `GRF_PROJECT`.

The configured minimum score must be finite and between 0 and 1. No evaluated tests, untested mutants or
infrastructure errors fail the CLI gate with code 2; a measured score below the threshold returns 1.
This prevents an empty analysis from looking successful.

## Core validation (Windows)

From the assigned GRF checkout root, following the user's selected Git workflow, after installing `.[dev,deploy]`:
the deployment extra is needed to exercise the default SSH backend without silently skipped tests.

On Windows, `.\scripts\setup.ps1` automates that installation; see the
[beginner installation guide](GETTING-STARTED.md#4-prepare-a-safe-practice-project).

```powershell
.\.venv\Scripts\ruff check src tests
.\.venv\Scripts\mypy --cache-dir results\mypy-cache
.\.venv\Scripts\python -m pytest -q --basetemp results\pytest --cov=grf --cov-report=term-missing
.\.venv\Scripts\python -m pip wheel --no-deps -w dist .
```

Pytest uses synthetic fixtures and temporary products; no product service, live RV&S or instrument is needed.
Pytest's cache and coverage data default to `results`. CI checks Python 3.11 and 3.12 on both Windows and Linux,
installs the deployment extra, and enforces 75% combined line/branch coverage. Coverage is an in-process
regression floor, not proof of all failure paths: subprocess-only smoke checks are deliberately separate.
The reusable workflow's optional `python-version` input selects one version when a caller explicitly needs it.
Regression tests cover configuration, capabilities, version pins, timing boundaries, human reviews, reporting,
mutation orchestration, nested resources, MCP response contracts and a generated product's real CLI/Robot flow,
including parallel pabot execution and mock non-evidence metadata.
Windows setup tests check explicit errors for missing Python, an unsupported interpreter, missing checkout
metadata, incomplete environments and failed installation commands without downloading dependencies or running products.
They also verify that a nonexistent explicit Python path fails without invoking PowerShell command discovery.
Diagnostic assertions tolerate PowerShell's line wrapping in long worktree paths; the unsupported-interpreter
fixture preserves native Python string quoting on Windows PowerShell 5.1.
Skill tests check frontmatter, required safety rules, starter registration, agent skill references and that GRF's
shared skills match `grf init` output. See [Copilot skills](SKILLS.md#change-a-skill).
Coordinator checks also enforce supported minimal frontmatter, root/template/demo parity, generic content,
contract/authorization boundaries and the immutable-commit consumer handoff procedure.
Deployment tests (`test_deploy_engine.py`, `test_deploy_parts.py`) use a simulated board (`tests\deploy_fakes.py`),
a local HTTP server instead of Jenkins and a fake SSH client: success, unchanged, dry run, hold, lock, preflight,
checksum, every rollback point, rollback failure, restore, redaction, config validation, CLI exit codes, report and
starter. They need no board, Jenkins or network, and passing them is not evidence that a real board deploys.
See [Deploying builds](DEPLOYMENT.md).

`test_deploy_binding.py` exercises strict local record/version assertions, including real Robot execution
of the inherited keyword. These are synthetic checks, not deployments or product observations.
Attaching a deployment record to a report is not a runtime check; products may opt into the
[observed-version assertion](DEPLOYMENT.md#checking-the-observed-product-version) and own fresh version
acquisition and endpoint mapping.

## Wheel-only smoke run

This checks packaging separately from an editable source install. Choose fresh output directories if rerunning.

```powershell
python -m venv results\wheel-env
$wheel = (Get-ChildItem dist -Filter general_robot_framework*.whl | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
.\results\wheel-env\Scripts\python -m pip install $wheel
.\results\wheel-env\Scripts\python -m grf init results\wheel-product --name "Wheel Demo"
.\results\wheel-env\Scripts\python -m pip install --no-deps -e results\wheel-product
$env:GRF_PROJECT = (Resolve-Path results\wheel-product).Path
.\results\wheel-env\Scripts\python -m grf demo-ingest
.\results\wheel-env\Scripts\python -m grf briefs
.\results\wheel-env\Scripts\python -m grf lint
.\results\wheel-env\Scripts\python -m grf drift --strict
.\results\wheel-env\Scripts\python -m grf run --tier mock --outdir results\wheel-mock
```

Reusable CI runs core/static checks and this wheel workflow on Windows and Linux. Core tests do not prove
product conformance or cross-repository compatibility. Each consumer must validate its pinned adapter and
authorized product tiers separately before a release upgrade.
