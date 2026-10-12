# Framework split cutover record

**Status: completed on 2026-10-12.** The user approved repository creation, publication and rename. The former
AI-TestBench repository is now GeneralRobotFramework; SMM and Windchill are maintained in separate repositories.
This document records the cutover and its historical verification. It does not authorize local checkout moves,
MCP configuration changes, product runs or instrument operations.

See [REPOSITORIES.md](REPOSITORIES.md) for repository ownership and responsibilities, and
[FRAMEWORK-SPLIT-PLAN.md](FRAMEWORK-SPLIT-PLAN.md) for the completed design.

## Published repositories

| Repository | Visibility | Contents |
|---|---|---|
| [GeneralRobotFramework](https://github.com/ionut2026/GeneralRobotFramework) | Public | Generic Python core, starter/demo, generic CI and agents |
| [SMMAutomationFramework](https://github.com/ionut2026/SMMAutomationFramework) | Private | SMM service, product keywords, suites, catalog, handbook and CI |
| [WindchillMcpServer](https://github.com/ionut2026/WindchillMcpServer) | Private | Read-only RV&S MCP server and CLI |

SMM and Windchill histories were filtered from the source at commit `3292490`. GeneralRobotFramework retains
the original repository history and uses a normal root-layout cutover commit. The historical source may therefore
contain product files in earlier Git revisions even though they are not part of the current core tree.

## Cutover verification recorded at the time

- GeneralRobotFramework's Windows/Linux CI passed; core checks run without SMM installed and exercise a generated
  consumer from the built wheel.
- SMM regression passed 173 Python tests, 50 service tests and 37 mock tests (5 skipped), using the published GRF
  tag. The mock report marked product evidence false.
- The independent Windchill MCP Java bridge built successfully. Offline syntax/help checks were also recorded.
- No real product or instrument operation was performed. Existing user-managed MCP registration was preserved.
- Extraction candidates had clean root layouts, no remotes and Git blob comparisons against their mapped source
  paths. Candidate/check details are historical evidence, not an assertion about current CI status.

## Ongoing ownership boundaries

- Make generic framework changes in GeneralRobotFramework; make SMM and MCP changes in their respective
  repositories. Old monorepo paths or local compatibility copies are not the source of truth.
- SMM pins an approved GRF release tag and keeps it synchronized with its `[framework] version`.
- Windchill MCP installation and connection configuration remain user-managed. Update an MCP entry only as an
  explicit, separately authorized configuration task; preserve its existing connection settings.
- Organization transfer, branch protection, live ingestion and offline/rig regression are not automatic effects
  of the split. Product and instrument-affecting runs require the instrument owner's authorization.
