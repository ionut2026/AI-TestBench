# Changelog

GRF uses Semantic Versioning. Before 1.0, incompatible API changes require a minor release; compatible fixes use
a patch release. Release tags are `vMAJOR.MINOR.PATCH`. Tags and publication are explicit maintainer actions.

## Unreleased

- Consolidated current documentation into architecture, configuration, testing and repository-boundary guides.
  Removed completed split plans and one-time migration scripts; historical versions remain in Git history.
- Required updates to all relevant documentation after every code change. Removed the irrelevant legacy
  `exports` folder and product-specific report at the owner's request; kept exports ignored to prevent
  accidental reintroduction. External MCP configuration is unchanged.
- Rejected malformed project tables, paths, capability lists and tier flags with explicit CLI diagnostics.
  Registered relative project roots now remain stable across working-directory changes.
- Made missing/unknown report tiers non-evidence. VERIFIED now requires an actual evidence tier, current
  hashes, current human approval and no specification drift; duplicate names cannot hide missing child-suite tests.
- Recorded suite-qualified reviews. The latest human verdict for the current hash wins; legacy name-only
  entries remain valid for unique names and must be re-recorded when names are ambiguous.
- Rejected reused Robot/mutation outputs and overrides of GRF-managed run options. Use fresh output directories
  and `grf report` for existing output. Parallel-run help now reflects configured tiers rather than product assumptions.
- Hardened mutation baselines and process-error handling. Empty/untested analyses fail; thresholds are checked
  against the unrounded score. Mutation workers receive the selected project root.
- Followed nested Robot resource imports in lint, rejected nonfinite timing inputs, and stopped swallowing
  unexpected Robot helper errors.
- Validated site-command quoting and finite positive rig timeouts; invalid operator modes fail explicitly.
  Robot's native parser enforces managed options even through abbreviations, argument files or `ROBOT_OPTIONS`;
  additional suite sources must use `--suites`. Missing output cannot turn a run into success.
  Dry runs require a non-evidence tier so syntax-only passes cannot become product evidence.
- Made the MCP client SDK optional through `general-robot-framework[windchill]`. Live ingestion now requires
  an explicit external server path and connection environment; it no longer reads home-directory MCP settings
  or guesses a legacy checkout. Malformed/incomplete/pagination responses fail explicitly, and failed
  initialization closes transports. Generic installs and demos need no MCP SDK.

These changes are not a published release. Consumers must review these behavior changes and run their
authorized regression before adopting a new approved tag.

## 0.1.0 - 2026-10-12

- Extracted generic project configuration, plugins, CLI and read-only RV&S traceability pipeline from SMM.
- Added reusable Robot timing/operator/rig helpers, preserving SMM compatibility shims.
- Added packaged `grf init`, a synthetic demo, independent tests and reusable Windows/Linux CI.
- Added exact approved-version checks in doctor via `[framework] version`.

First extracted core release. Product dependencies pin the `v0.1.0` Git tag.
