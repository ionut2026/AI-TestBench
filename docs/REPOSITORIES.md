# Repository split

The user approved GitHub creation, pushes and rename on 2026-10-12.

| Repository | Visibility | Contents |
|---|---|---|
| [GeneralRobotFramework](https://github.com/ionut2026/GeneralRobotFramework) | Public (preserved) | Generic Python core, starter/demo, generic CI and agents |
| [SMMAutomationFramework](https://github.com/ionut2026/SMMAutomationFramework) | Private | SMM service, keywords, suites, catalog, handbook, product CI and agents |
| [WindchillMcpServer](https://github.com/ionut2026/WindchillMcpServer) | Private | Read-only RV&S MCP server and CLI |

SMM and Windchill are filtered from main at `3292490`; their original path histories are retained.
GRF keeps the original repository history, with a normal root-layout cutover commit (no force push).
The existing local checkout stays `D:\projects\AI-TestBench`; its origin points to GeneralRobotFramework.
The original history still includes SMM/MCP files because the source repository was already public.

Current extraction/publication candidates live in the ignored
`general-robot-framework\results\cutover` directory inside the active checkout. Fresh independent checkouts may
be placed as siblings on D: once the user is ready; do not move the active checkout while sessions use it.
The user's modified `exports\pra-tested.md` is preserved and is not part of cutover commits.

For SMM development, install the GRF sibling editable via `scripts\setup.ps1 -GrfPath <GRF checkout>`.
CI installs GRF from its approved `v0.1.0` Git tag. Keep `[framework] version` and the dependency tag synchronized.
The public core needs no private read token. Existing SMM-TestBench access remains separately controlled.

The Windchill server configuration remains user-managed. After choosing the final local server checkout,
update the MCP entry's `args` to `<checkout>\src\server.js`, preserve existing RVS connection settings, and set
`RVS_EXPORT_DIR` explicitly. No active MCP registration is changed automatically.

An organization transfer is not performed: no destination organization was specified. Existing `ionut2026`
ownership and CODEOWNERS are retained. Private-repository branch protection may depend on the account plan.
