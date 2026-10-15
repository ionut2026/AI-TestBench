# Repository boundaries and local data

Separate repositories do not mean separate copies of the framework. Product projects install GRF as a
dependency and keep their own configuration and test code. See
[how products use GRF](GETTING-STARTED.md#how-do-separate-repositories-use-one-framework) for a beginner explanation.

| Repository | Owns |
|---|---|
| [GeneralRobotFramework](https://github.com/ionut2026/GeneralRobotFramework) | Generic Python core, packaged starter, synthetic demo and generic CI |
| [SMMAutomationFramework](https://github.com/ionut2026/SMMAutomationFramework) | SMM service, interfaces, keywords, catalogs, suites and product CI |
| [WindchillMcpServer](https://github.com/ionut2026/WindchillMcpServer) | Read-only RV&S MCP server, Java bridge, server tools and `rvs` CLI |

The split was completed on 2026-10-12. This project's active local checkout is
`D:\projects\GeneralRobotFramework`, used directly on `main`. Historical split plans and one-time extraction
utilities are retained only in Git history; they are not current installation instructions.
The original repository history can still contain pre-split product files.

## Data ownership

The legacy `exports` folder and its product-specific RV&S report were removed at the owner's request.
No GRF runtime module depended on them. Raw exports belong in the product's or external server's storage
policy, not core. `exports` remains ignored to prevent accidental reintroduction of product data.

An MCP tool being available in Copilot does not mean its server lives in this repository. The external server
controls its own export destination with `RVS_EXPORT_DIR`; moving server code does not automatically move
previous exports or rewrite that setting. GRF neither edits the user's MCP registration nor chooses that
server's export directory. Removing the legacy core folder does not change the external server's configuration.

GRF's `grf ingest` writes a product catalog under `[paths] catalog_dir`, while execution/report output goes
under `[paths] results` or `--outdir`. Those are separate from raw MCP/`rvs` exports.
The Python MCP **client SDK** is available through the optional `windchill` extra; it is not a bundled server.
See [Configuration](CONFIGURATION.md#external-windchill-integration) for setup.

## Compatibility

Products pin an approved GRF release independently. A sibling editable install is useful for coordinated local
development but is not a source-provenance guarantee. Core validation does not establish product compatibility:
the product owner must run its own authorized regression before upgrading. See [Contributing](../CONTRIBUTING.md).

## Deployment ownership

Deploying builds to instrument boards is split three ways (see [Deploying builds](DEPLOYMENT.md)):

| Owner | Owns |
|---|---|
| GRF | The generic `deploy` engine, rollback, lock, record, report section and starter templates; no hosts or product names |
| Product repository (SMM) | Its components (Jenkins jobs, artifact patterns, container recipes, health checks), `[deploy]` order and its Jenkins pipeline |
| Site (Jenkins agent) | The site config, `known_hosts` and credentials, outside every repository |
