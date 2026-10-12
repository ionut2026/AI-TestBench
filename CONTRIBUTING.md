# Contributing

## Where a change belongs

Put product-independent configuration, pipeline, reporting, capability and timing/rig protocol behavior in GRF.
Keep product services, interfaces/schemas, log parsers, state models, fault implementations, test catalogs and
suites in the product repository. Use `ProductPlugin` only for actual product extension points.
Do not add a generic abstraction based on a single hypothetical consumer; demonstrate it with the neutral demo.

## Gates

Run ruff, mypy, standalone pytest and the wheel-only starter demo described in README.
Changes used by SMM also need its pytest/goldens, lint, drift and matrix gates and mock regression.
Never regenerate goldens just to make a failure go away: explain and review an intentional output change.
Hardware/offline runs need the owner's authorization; never modify the pinned SMM TestBench checkout.
Do not publish data to RV&S or introduce archive/history/publishing features excluded from the approved scope.

## Compatibility and releases

Record user-visible changes in CHANGELOG. Keep public keyword names, configuration and CLI behavior stable
within a release line. Deprecate compatibility shims deliberately, with migration instructions, not silently.
Use SemVer (`vX.Y.Z` tags); release creation/push requires a maintainer decision.
Products pin an approved GRF version in both `pyproject.toml` and `[framework] version` in `grf.toml`.
`grf doctor` checks the installed distribution against the latter. During local development install the sibling
checkout editable; version equality is not a commit-identity check. After cutover CI must also pin the approved
Git commit/release tag and use read-only authentication for private repositories.

Propose upgrades by PR: update both pins, install the proposed core, run all product gates and review the
CHANGELOG. The automatic bump-PR workflow requires the final GitHub repository/owner and authentication policy;
it is provided in SMMAutomationFramework as a manually dispatched release-bump workflow and never auto-merges.

## Review ownership

Core changes require a GRF maintainer review; product suites and human review ledgers require product test-owner
review. CODEOWNERS lists the current known maintainer; organization teams replace it only after their names
and access are confirmed. CODEOWNERS alone does not enforce review: branch protection must be explicitly enabled.
Agent review never constitutes human approval or permission to remove `review:pending`.
