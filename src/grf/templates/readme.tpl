# Product automation starter

This is a **mock demo**, not product evidence. It has no SMM dependency and does not contact an instrument or RV&S.
Replace the example counter library and fake RV&S fixture with your product adapter and real specifications.
The display name, paths, tiers, tag prefix and plugin are configured in `grf.toml`.

## If you are new to Robot Framework

Robot Framework executes readable steps in `.robot` files. A **keyword** is a named action or check.
Here, `Request Increment` changes a Python counter and `Counter Should Be` checks its value.
A **suite** groups tests; this starter's test is in `robot\suites\demo\counter.robot`.
Its **setup**, `Reset Counter`, runs before each test so the starting value is always zero.

GRF adds links between tests and specifications (written statements of required behavior), plus review
checks and reports. A **mock tier** runs against the fake counter, not an instrument.
The library implementing the keywords is `src\product_automation\ProductLibrary.py`.
Robot separates keyword names and arguments with two or more spaces: `Counter Should Be    1`.
For the complete beginner walkthrough, see `docs\GETTING-STARTED.md` in your GRF checkout.

## Run the demo (Windows)

From this directory, with GRF already installed in a virtual environment:

```powershell
python -m pip install --no-deps -e .
grf demo-ingest
grf briefs
grf lint
grf drift --strict
grf doctor --tier mock
grf run --tier mock --outdir results\mock
```

`demo-ingest` creates the catalog (local specification data) from the fake fixture. `briefs` creates a
per-specification explanation for test authors. `lint` checks writing rules; expect `0 rule violation(s)`.
`drift --strict` checks whether the tests still match the specification data; expect `0 problem(s)`.
`doctor` checks prerequisites, and `run` executes the test; expect one pass and zero failures.
The commands use this directory as their working directory. Set `GRF_PROJECT` explicitly if another product
is selected in your terminal. For later runs, choose a new output directory, such as `results\mock-2`.

Open `results\mock\report.html` for Robot's summary and `log.html` for individual steps.
Open `traceability.html` to see GRF's specification links and review status.

Activate your venv or use its full Python and `grf` paths. The demo builds a catalog from `fixtures\rvs.json`
using GRF's real catalog builder; `demo-ingest` is a product plugin command, not a core fixture switch.
Robot runs a reset/increment assertion and GRF writes `traceability.html/json` alongside `output.xml`.
Use a fresh output directory for every run; GRF rejects an existing `output.xml` to prevent stale evidence.
The fixture links specification 1 to requirement 2, user story 3 and explorative test 4.
The test starts with `review:pending`: passing the mock is not human approval.

## Adapt to a real product

1. Replace `product_automation.ProductLibrary` with keywords that observe your product. Extend `GrfLibrary`
   for timing, operator actions and rig-control helpers; the product owns setup/teardown and stimulus marks.
2. Replace the demo scope IDs and remove `demo-ingest` when no longer needed. Install GRF's optional `windchill`
   extra for live ingestion. Set `WINDCHILL_MCP_SERVER` to your
   read-only Windchill MCP server's JavaScript entry point and supply its `RVS_*` environment settings; run `grf ingest`.
3. Add real tiers and environment files. Only set `evidence = true` when tests observe the actual product.
   Map required tags to capabilities and keywords in `grf.toml`. Never silently skip an assertion.
4. Regenerate briefs, write tests against the specification (not the mock), and keep `review:pending` until
   a human has reviewed and approved the current hash with `grf review`. Never auto-update a stale hash.
5. Pin GRF to an approved release for CI, keeping the dependency and `[framework] version` synchronized.
   Validate your adapter before upgrades; mock results alone do not prove product conformance.

## Deploy builds to a board (optional)

If your product's builds come from Jenkins as Docker image tarballs, GRF can put a build on the instrument board,
check that it runs and roll back automatically: `grf deploy`. The plugin declares what can be deployed in
`deploy_components()`; this mock starter declares nothing. `grf init --with-deploy` also creates
`robot\environments\deploy.example.toml` (copy it outside the repository) and `jenkins\Jenkinsfile.deploy`.
Deploying changes the instrument and needs the instrument owner's authorization. See `docs\DEPLOYMENT.md` in
your GRF checkout.

`--deployment` attaches a record to the report only. To check a real application's version, the product
must obtain a fresh observation and call the inherited `Deployment Record Should Match Product Version`
keyword with one explicit record file, component, observed version and target label. The label is not
proof of board identity. See `docs\DEPLOYMENT.md#checking-the-observed-product-version` in your GRF checkout.
The synthetic counter has no deployment or product version and does not call this assertion.

For controlled product workflows, GRF 0.3.0 adds `request check` (validate a saved intention), `gate` (evaluate
finished evidence) and content-aware human reviews. See `docs\WORKFLOWS.md` in your GRF checkout.
The gate never accepts the mock counter as product evidence. Local tier locks only coordinate runs sharing
the same configured results root; a real instrument needs the site's authoritative controller reservation.

## Maintain the project

Update all relevant README, authoring rules and configuration documentation after every code or suite change.
Reset product state between tests and isolate worker resources before declaring a tier parallel-safe.
Passing tests become VERIFIED only on an evidence tier with current hashes, recorded human approval and no
specification drift. Removing `review:pending` alone is not approval. New review entries are suite-qualified;
legacy name-only reviews apply only to unique test names.

The generic author/reviewer agents are in `.github\agents`. They preserve human sign-off and read-only RV&S access.
For requested cross-repository changes, explicitly select `integration-coordinator` in a supporting Copilot
client and ask it to use `grf-coordinate-integration`. The agent discovers available capabilities; files alone
do not start workers or reconnect sessions. Honor the user-selected direct-main or branch/PR workflow,
agree exact contracts first and validate consumers against an immutable framework commit.
Direct-main work requires one writer per repository, preserving user edits and asking on conflicts.
Product mappings stay in your own instructions.
Push/merge/tag need explicit matching authorization; no Jenkins, instrument or credential actions are implied.
See `docs\SKILLS.md#coordinated-integration` in your GRF checkout for invocation, adoption and limits.
Reusable Copilot skills are in `.github\skills`; see `docs\SKILLS.md` in your GRF checkout for how to use them.
This starter's Python package is `product_automation`; rename it and the plugin/library references if multiple
product adapters need to be installed in the same environment.
