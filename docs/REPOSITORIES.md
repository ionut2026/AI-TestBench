# Repository layout

The framework split is complete. Each repository has a separate responsibility and release workflow.

| Repository | Visibility | Contents |
|---|---|---|
| [GeneralRobotFramework](https://github.com/ionut2026/GeneralRobotFramework) | Public | Generic Python core, starter/demo, generic CI and agents |
| [SMMAutomationFramework](https://github.com/ionut2026/SMMAutomationFramework) | Private | SMM service, product adapters and keywords, specifications, suites, handbook and product CI |
| [WindchillMcpServer](https://github.com/ionut2026/WindchillMcpServer) | Private | Read-only RV&S MCP server and CLI |

SMM and Windchill retain filtered Git histories. GeneralRobotFramework retains the original repository history
and has the generic core at its root; prior revisions can still contain files that were split out.

## Development and integration

- Add reusable functionality to GeneralRobotFramework. Keep SMM-specific service, ICD/COP, product keywords,
  catalog and suites in SMMAutomationFramework. Keep the Windchill API bridge and RV&S server tools in
  WindchillMcpServer.
- SMM uses the released GRF package and pins its approved Git tag. Keep the exact `[framework] version` in SMM's
  `grf.toml` synchronized with that dependency.
- The GRF checkout path is supplied by the active development environment. Do not assume a fixed local path or
  modify another checkout when working in a session workspace.
- Windchill MCP registration and connection settings are user-managed. Set the server entry to the selected
  WindchillMcpServer checkout and configure its exports directory explicitly when setting up the integration;
  preserve existing RVS connection settings.

## Safety and verification

The GRF demo uses a synthetic counter and fixture and is not product evidence. Mock test reports have
`productEvidence = false`. Generic CI verifies the core independently and runs a generated consumer; each product
repository is responsible for its own CI. Historical split verification is summarized in
[FRAMEWORK-SPLIT-CUTOVER.md](FRAMEWORK-SPLIT-CUTOVER.md); consult current repository CI for present results.

No organization transfer or MCP registration change is implied by the repository split. Product and
instrument-affecting test runs require authorization from the instrument owner.
