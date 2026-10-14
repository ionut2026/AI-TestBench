# Product configuration

New to GRF? Complete the [beginner walkthrough](GETTING-STARTED.md) first.
`grf.toml` is the product's settings file: it tells GRF where the tests live and which environment to use.
TOML is a text configuration format; `[project]` and `[paths]` are named sections, not executable code.
This guide is the detailed reference once you have run the demo.

## Discovery and paths

`grf init` and top-level help work without a product. Other commands load `grf.toml` in this order:

1. `GRF_PROJECT`, containing a configuration file or its directory.
2. A product package's registered default root (`project.set_default_root`).
3. The nearest `grf.toml` in the working directory or a parent.

The loaded project is cached per process. `project.use(None)` resets that cache. Registered roots are resolved
when registered; changing the working directory afterward does not move them. `grf run` and mutation workers
receive the selected root through `GRF_PROJECT`.

TOML paths are relative to the configuration file; explicit CLI paths are relative to the working directory.
`--scope`, `--catalog`, repeatable `--suites` and `--reviews` are global options, placed **before** the subcommand.
Names must be nonempty strings. `spec_prefix` starts with a letter and uses letters, digits or underscores.
Path entries are strings or lists of nonempty strings. `keyword_docs` may contain Python library module names.
Malformed tables, path values, capabilities and tier boolean flags fail explicitly rather than being coerced.

## Minimal starter configuration

```toml
[project]
name = "My Product"
spec_prefix = "SPEC"
plugin = "product_automation.plugin:DemoPlugin"

[framework]
version = "0.1.0"

[paths]
scope = "catalog/demo.scope.toml"
catalog_dir = "catalog"
reviews = "catalog/reviews.toml"
suites = ["robot/suites"]
environments = "robot/environments"
results = "results"
briefs = "generated/briefs"
brief_rules = "catalog/brief-rules.md"
keyword_docs = ["product_automation.ProductLibrary"]
mutants = "catalog/mutants.toml"

[tiers.mock]
capabilities = []
evidence = false
parallel = true
```

TOML examples use portable slash-delimited values; Windows shell commands use backslashes.
The starter supplies the referenced files and plugin. See `examples\demo\grf.toml` for the full generated example.

## Configuration sections

| Section | Purpose |
|---|---|
| `[project]` | `name`, `product`, `cli` (default `grf`), `spec_prefix` (default `SPEC`), `rule_prefix`/`env_prefix` (default `GRF`), optional `module:attribute` plugin |
| `[framework]` | Approved exact installed distribution `version`, checked by doctor |
| `[paths]` | Scope/catalog, reviews, suites, environment files, results, briefs, `brief_target`, keyword docs and mutants |
| `[tiers.<name>]` | String-list `capabilities`; boolean `evidence` (default true), `parallel` and `rig_control` (default false) |
| `[capabilities]` | Required test tag to capability, e.g. `"needs:reset" = "reset"` |
| `[operator]` | Capabilities supplied by console/dialog modes (default `["operator"]`) |
| `[rig]` | Restart `targets` mapped to capabilities, `ready_ports`, `log_capability`, `log_files` and `log_pattern` |
| `[[lint.tag_rules]]` | `rule`, `tag`, `keywords`, optional `also`, `satisfied_by` and `what` |
| `[catalog]` | Optional `message_pattern` used by catalog enrichment |
| `[mutation]` | Optional `faults_env` and supported `fault_actions`; implementations belong to the product |
| `[texts]` | Product-specific report labels such as `non_evidence` and `nospec_skip` |

Declare `evidence = false` explicitly for every mock/simulator tier. Unknown or missing report tiers are
non-evidence. Exact tier names take priority; legacy metadata with a dash/space suffix may resolve to a known
tier, with the longest matching name first. An arbitrary prefix such as `benchwork` does not resolve to `bench`.

Only tiers marked `parallel = true` can use `grf run --processes N`. This flag is a product-owner assertion of
resource isolation, not something GRF can prove. Do not mark an instrument-sharing tier parallel-safe.

Lint tag rules bind capability selection to actual keyword use. For example:

```toml
[capabilities]
"needs:reset" = "reset"

[[lint.tag_rules]]
rule = 4
tag = "needs:reset"
keywords = ["Reset Product"]
what = "resets the product"
```

## Plugins and libraries

`ProductPlugin` subclasses may implement `known_messages`, `brief_sections`, `doctor_class` and `add_commands`.
Use hooks for product behavior, not static configuration. Product keyword libraries extend `GrfLibrary` and
own their connections, lifecycle, state isolation and assertions. Timing keywords require finite timestamps;
invalid values such as NaN cannot silently satisfy a timing requirement.

Site rig commands are provided through `<ENV_PREFIX>_RIG_CONTROL` as a command line or JSON argument list.
They implement `capabilities`, configured `restart-<target>`, `fetch-log` and optional `clock`. GRF invokes them
without a shell. `<ENV_PREFIX>_RIG_CONTROL_TIMEOUT` must be finite and positive and bounds calls.
Invalid operator modes fail explicitly rather than silently becoming `none`. `grf.rig_ssh` provides an optional configured
SSH implementation; site credentials stay outside repositories. Capability discovery may contact a rig, and
`doctor --deep` may invoke product code: both are owner-authorized product activities.

## External Windchill integration

The server must be installed separately; GRF does not implement or bundle it.

```powershell
.\.venv\Scripts\python -m pip install -e ".[windchill]"
$env:WINDCHILL_MCP_SERVER = "D:\projects\WindchillMcpServer\src\server.js"
# Supply the external server's required RVS_* connection settings in this process.
.\.venv\Scripts\python -m grf ingest
```

Alternatively set `[paths] rvs_server` to the external entry point (relative to the product configuration).
Node.js must be on PATH. There is no guessed legacy server location or fallback to the user's home-directory
MCP configuration. Existing users relying on that fallback must explicitly provide their server path and
connection environment. Copilot's MCP registration and `grf ingest` are separate clients.

Responses must be valid JSON with expected item IDs. Missing, unexpected or duplicate IDs, malformed pages
and pagination with no progress fail ingestion rather than quietly producing an incomplete catalog.
All server calls are read-only. No live connection is needed for the synthetic demo or core validation.
