# Product automation starter

This is a **mock demo**, not product evidence. It has no SMM dependency and does not contact an instrument or RV&S.
Replace the example counter library and fake RV&S fixture with your product adapter and real specifications.
The display name, paths, tiers, tag prefix and plugin are configured in `grf.toml`.

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

## Maintain the project

Update all relevant README, authoring rules and configuration documentation after every code or suite change.
Reset product state between tests and isolate worker resources before declaring a tier parallel-safe.
Passing tests become VERIFIED only on an evidence tier with current hashes, recorded human approval and no
specification drift. Removing `review:pending` alone is not approval. New review entries are suite-qualified;
legacy name-only reviews apply only to unique test names.

The generic author/reviewer agents are in `.github\agents`. They preserve human sign-off and read-only RV&S access.
This starter's Python package is `product_automation`; rename it and the plugin/library references if multiple
product adapters need to be installed in the same environment.
