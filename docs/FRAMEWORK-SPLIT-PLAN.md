# Framework split: completed plan

**Status: implemented on 2026-10-12.** The former AI-TestBench repository was cut over to
GeneralRobotFramework, and the SMM and Windchill components were published as separate repositories. This document
records the completed goals and decisions; it is not a pending work plan or a set of local setup instructions.
See [REPOSITORIES.md](REPOSITORIES.md) for the current repository layout and
[FRAMEWORK-SPLIT-CUTOVER.md](FRAMEWORK-SPLIT-CUTOVER.md) for the cutover record.

## Result

| Repository | Responsibility |
|---|---|
| [GeneralRobotFramework](https://github.com/ionut2026/GeneralRobotFramework) | Reusable Python core, generic CLI and pipeline, product starter/demo, generic agents and CI |
| [SMMAutomationFramework](https://github.com/ionut2026/SMMAutomationFramework) | SMM service and product-specific adapters, keywords, specifications, suites and CI |
| [WindchillMcpServer](https://github.com/ionut2026/WindchillMcpServer) | Read-only Windchill RV&S MCP server and CLI |

The products retain filtered Git histories. GeneralRobotFramework retains the original repository history and uses
a root-layout cutover commit. Its historical Git objects may include files that are no longer in the current tree.

## Design decisions

- The generic package is `general-robot-framework`, imported as `grf`, with the `grf` CLI.
- Product-specific adapters and suites remain in their product repositories; core never imports SMM.
- Products use their own `grf.toml` and optional `ProductPlugin` hooks. The `examples/demo` counter is synthetic
  and serves as a second consumer, not as product-conformance evidence.
- GRF provides generic RV&S ingestion, catalog and hash handling, briefs, drift, lint, human review, mutation
  orchestration, reporting, Robot helpers, rig/operator protocols, project configuration and scaffolding.
- Product CI pins an approved GRF release and keeps that dependency version aligned with `[framework] version`.
  Generic CI tests GRF independently and exercises a generated consumer.
- Mock-tier results are explicitly non-evidence. Product and instrument operations require the owner's
  authorization.

## Historical verification

The split was verified in extracted root layouts. At cutover, the recorded checks included GRF tests and
Windows/Linux CI, SMM Python and service tests plus mock regression, and an offline MCP syntax/help check.
Those results describe the cutover verification at that time; consult each repository's current CI for present
status. Live ingestion, product operation and instrument testing are not requirements for the generic core.

## Historical scope exclusions

The split did not move product code back into GRF, publish RV&S results, add a generic TypeScript service, transfer
repositories to an organization, or change user-managed MCP registrations. Organization governance and private
repository branch protections remain separate decisions.
