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
version = "0.3.0"

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
| `[tiers.<name>]` | String-list `capabilities`; boolean `evidence` (default true), `parallel` and `rig_control` (default false); optional `lock` (local reservation name, default: the tier name for non-parallel tiers) |
| `[capabilities]` | Required test tag to capability, e.g. `"needs:reset" = "reset"` |
| `[operator]` | Capabilities supplied by console/dialog modes (default `["operator"]`) |
| `[rig]` | Restart `targets` mapped to capabilities, `ready_ports`, `log_capability`, `log_files` and `log_pattern` |
| `[[lint.tag_rules]]` | `rule`, `tag`, `keywords`, optional `also`, `satisfied_by` and `what` |
| `[catalog]` | Optional `message_pattern` used by catalog enrichment |
| `[mutation]` | Optional `faults_env` and supported `fault_actions`; implementations belong to the product |
| `[texts]` | Product-specific report labels such as `non_evidence` and `nospec_skip` |
| `[deploy]` | Optional `order` (component list) and `default_branch` for `grf deploy`; hosts and credentials belong in the site config ([Deploying builds](DEPLOYMENT.md)) |

Declare `evidence = false` explicitly for every mock/simulator tier. Unknown or missing report tiers are
non-evidence. Exact tier names take priority; legacy metadata with a dash/space suffix may resolve to a known
tier, with the longest matching name first. An arbitrary prefix such as `benchwork` does not resolve to `bench`.

Only tiers marked `parallel = true` can use `grf run --processes N`. This flag is a product-owner assertion of
resource isolation, not something GRF can prove. Do not mark an instrument-sharing tier parallel-safe.

`grf run` on a non-parallel tier holds an operating-system file lock `<results>/.locks/<lock>.lock` for the whole
run; a second run on the same machine stops with "in use by another run". Give two tiers the same `lock` when they
share a resource (for example one local installation of the product). The lock covers one machine only; several
machines sharing an instrument need the controller's reservation ([Workflows](WORKFLOWS.md#reservation)).
The local lock is shared only when the configured results root is shared; different checkouts do not automatically
reserve the same resource. It covers Robot execution, not separate deployments or an entire pipeline.

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

`ProductPlugin` subclasses may implement `known_messages`, `brief_sections`, `doctor_class`, `add_commands` and
`deploy_components` (the deployable applications; see [Deploying builds](DEPLOYMENT.md)).
The acceptance gate also uses `runtime_identity(meta)`, `installed_matches` and `runtime_matches`;
see their [exact contract and limits](WORKFLOWS.md#product-identity-hooks).
Use hooks for product behavior, not static configuration. Product keyword libraries extend `GrfLibrary` and
own their connections, lifecycle, state isolation and assertions. Timing keywords require finite timestamps;
invalid values such as NaN cannot silently satisfy a timing requirement.

Site rig commands are provided through `<ENV_PREFIX>_RIG_CONTROL` as a command line or JSON argument list.
They implement `capabilities`, configured `restart-<target>`, `fetch-log` and optional `clock`. GRF invokes them
without a shell. `<ENV_PREFIX>_RIG_CONTROL_TIMEOUT` must be finite and positive and bounds calls.
Invalid operator modes fail explicitly rather than silently becoming `none`. `grf.rig_ssh` provides an optional configured
SSH implementation; site credentials stay outside repositories. Capability discovery may contact a rig, and
`doctor --deep` may invoke product code: both are owner-authorized product activities.

Deployment and SSH rig configuration reject unknown fields and malformed sections. Numeric budgets must
be finite (not NaN or infinity), timeouts positive, downtime/stability and free-space floors nonnegative,
and ports integers from 1 to 65535. Booleans and numeric strings are not numbers. Deployment retention and
lock TTL must be positive integers. Component stability time cannot exceed its readiness timeout.
**Migration:** correct misspelled fields or put product-only settings in the product's own configuration;
GRF no longer silently ignores unknown deployment/SSH rig keys.

Rig restart validates downtime before stopping anything and always attempts the configured start
after a failed stop, cancelled wait or other downtime error. If start recovery also fails, both errors
are reported and the target may need owner recovery. `fetch-log` uses a fresh staging folder for each
invocation and never returns pre-existing destination files or overwrites earlier evidence.
The `clock` response must contain exactly two finite numbers with nonnegative uncertainty;
contradictory SSH clock samples are unavailable, not repaired by swapping bounds.

Readiness uses one monotonic deadline (elapsed time unaffected by wall-clock adjustments)
shared by every command, stability wait and port probe, not a fresh timeout per retry.
Paramiko drains stdout and stderr while a command is running to avoid SSH channel backpressure;
SCP has a total transfer budget rather than a new allowance for each chunk.
Product integrations can share `grf.deadline.Deadline(seconds)` across substeps:
pass `remaining()` to blocking calls and use `sleep(seconds)` for bounded polling.
Expiry raises `TimeoutError`; a late successful response does not renew the budget.

Local rig/SSH/SCP and mutation subprocesses run in an owned process tree. Timeout or cancellation
terminates descendants as well as the initial command, so inherited output pipes cannot keep the call
blocked or leave a local restart script running. Windows uses a helper inside a kill-on-close Job Object
(an OS-managed process group); failure to establish ownership fails before spawning the site command.
This controls local processes, not a remote command already accepted by an SSH server: product/site
recovery must still account for uncertain remote effects.

Console and dialog operator prompts both enforce the requested timeout. Dialogs run in an owned
subprocess so expiry closes the dialog and fails the step rather than waiting indefinitely.
No response, a closed/failed dialog and an explicit operator failure never count as confirmation.

Deployment reads its site config from `<ENV_PREFIX>_DEPLOY_CONFIG` (a file outside the repository), the optional
Jenkins login from `JENKINS_USER`/`JENKINS_TOKEN` and records for reports from `GRF_DEPLOYMENT_RECORD`; see
[Deploying builds](DEPLOYMENT.md#environment-variables). Install `general-robot-framework[deploy]` for the default
SSH client.

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
