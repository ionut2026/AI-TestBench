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
version = "0.4.1"

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

Optional `[acceptance.policy]` restrictions apply consistently to request validation, requested runs and
acceptance. See [the canonical policy schema](WORKFLOWS.md#project-acceptance-policy). sections

| Section | Purpose |
|---|---|
| `[project]` | `name`, `product`, `cli` (default `grf`), `spec_prefix` (default `SPEC`), `rule_prefix`/`env_prefix` (default `GRF`), optional `module:attribute` plugin |
| `[framework]` | Approved exact installed distribution `version`, checked by doctor, evidence-tier runs and acceptance |
| `[paths]` | Scope/catalog, reviews, suites, environment files, results, briefs, `brief_target`, keyword docs and mutants |
| `[tiers.<name>]` | String-list `capabilities`; boolean `evidence` (default true), `parallel` and `rig_control` (default false); optional `lock` (local reservation name, default: the tier name for non-parallel tiers) |
| `[capabilities]` | Required test tag to capability, e.g. `"needs:reset" = "reset"` |
| `[operator]` | Capabilities supplied by console/dialog modes (default `["operator"]`) |
| `[rig]` | Restart `targets` mapped to capabilities, `ready_ports`, `log_capability`, `log_files` and `log_pattern` |
| `[[lint.tag_rules]]` | `rule`, `tag`, `keywords`, optional `also`, `satisfied_by` and `what` |
| `[catalog]` | Optional `message_pattern` used by catalog enrichment |
| `[mutation]` | Optional `faults_env` and supported `fault_actions`; implementations belong to the product |
| `[texts]` | Product-specific report labels such as `non_evidence` and `nospec_skip` |
| `[deploy]` | Optional `order` (component list), `default_branch` fallback and `[deploy.branches]` per-component Jenkins branches for deployment/workflow; hosts and credentials belong in the site config ([Deploying builds](DEPLOYMENT.md)) |

Declare `evidence = false` explicitly for every mock/simulator tier. Unknown or missing report tiers are
non-evidence. Exact tier names take priority; legacy metadata with a dash/space suffix may resolve to a known
tier, with the longest matching name first. An arbitrary prefix such as `benchwork` does not resolve to `bench`.

### Deployment branch selection (0.4.1)

In the product's `grf.toml`, configure each Jenkins component independently:

```toml
[deploy]
order = ["controller", "app"]
default_branch = "main"

[deploy.branches]
app = "release/app"
controller = "release/controller"
```

`Project.deploy_branches` exposes this optional string map; `Project.deploy_branch` remains the global
fallback. Branch values and component names must be nonempty strings. Unknown component keys are rejected
when deployment/workflow resolves the plugin's component definitions. There is no implicit environment-variable
expansion: product wrappers may pass their own validated branch variables as CLI overrides.

Precedence is explicit `--branch COMPONENT=BRANCH`, then `[deploy.branches]`, then `[deploy] default_branch`.
For example, `grf deploy plan --component app --branch app=feature/fix` changes only that invocation.
`deploy run` and `workflow deploy-and-test` accept repeated overrides, one per component; `deploy builds`
uses the same selection rules. Unknown components, duplicate overrides and empty names/values fail before
deployment. The legacy **single** `--branch BRANCH` still overrides all components; do not mix it with
qualified overrides or repeat it.

If no branch resolves, GRF reads Jenkins job metadata before listing/resolving builds. A multibranch job
(a parent containing separate jobs for each source branch) or folder is refused, never used as a build job.
Missing/unreadable job-type metadata also fails closed; permit read access or configure the branch explicitly.
A regular build job may still omit the branch. `deploy builds --branches` lists child branches without
requiring branch selection. Branches containing `/` are encoded as one Jenkins URL segment.
The resolved branch and exact job URL are saved as `artifact.branch` and `artifact.job_url` in each deployment
record; the workflow binds both during planning and actual deployment. See [Workflows](WORKFLOWS.md#maintained-deploy-and-test-workflow).

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

Product-evidence tiers require `evidence_preflight`, `evidence_checks` and `assertion_coverage`; see the
[typed evidence contract and 0.4 migration](WORKFLOWS.md#evidence-integrity-and-assertion-coverage-04-migration).
Their defaults are unavailable, not successful checks. Declaring `evidence = true` alone does not validate an instrument.

Product commands use the public `self.context` (`grf.commands.CommandContext`) supplied by
`ProductPlugin`. It resolves `catalog_path(args)`, `scope_path(args)`, `reviews_path(args)` and
`suites(args)` with the same CLI overrides as core; `load_catalog(args)` and `collect_tests(args)`
load those inputs. Missing paths fail only when that feature is used. Do not import underscored
CLI helpers. Public review/identity APIs are in `grf.pipeline.drift`
(`test_key`, `require_unique_tests`, `record_review`, `current_review_problem`; legacy `review_problem`
is not sufficient for current-content approval).
Core commands do not register unrelated optional product commands; extensions must not replace
core safety commands through the returned handler mapping.

Do not select a GRF project while importing a product package. A product executable may call
`grf.cli.main(project_root=product_root)` explicitly (`GRF_PROJECT` still takes priority).
`grf.project.discover(start)` resolves a project without mutating globals, and
`with grf.project.using(project): ...` scopes a selection to one operation and restores the previous one.
`set_default_root` remains legacy compatibility only, not a recommended import-time action.

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

Project loading also validates nested rig targets/ports, lint rule tables and required fields,
catalog regular expressions, mutation settings and exact framework pins. Unknown fields in core
tier/rig/lint/operator/catalog/mutation/deploy/framework tables are errors (a typo such as
`evidnce=false` must not accidentally enable evidence). Product-only configuration may use a
separate top-level table; GRF does not interpret it.

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

## Shared SSH transport and 0.4 migration

Deployment and `grf.rig_ssh` now use the public `grf.ssh` module: `SshConfig`, `open_transport`,
`Transport`, `CommandResult`, `Redactor` and `SshError`. A transport provides `run`, `put`, `get` (one exact
file) and `close`. Close owned connections in `finally`. Deployment translates shared SSH failures into
its existing `DeployError`/outcome contract; consumer integrations should import SSH classes from
`grf.ssh`, not the internal `grf.deploy.transport` implementation.

Both clients require explicit `user` and `known_hosts`. `openssh` uses system `ssh`/`scp`, noninteractive
key authentication and the owned-process timeout helper. `paramiko` supports one of `identity`,
`password_env` or `password_file` and requires the optional `[deploy]` package extra.
`host_key_policy = "pinned"` rejects unknown and changed keys; `"accept-new"` can record a first-seen key,
but still rejects changed keys. Trust a first-seen board only under the owner's approved site process.
Neither client disables checking or prompts for credentials. Secrets, including short/overlapping passwords,
are redacted from command diagnostics. Passwords never belong directly in TOML.

The deployment target schema is unchanged. For a rig SSH file outside the repository, use the same
connection fields at its top level, followed by product-owned stop/start/log/clock sections:

```toml
host = "board.example.invalid"
user = "root"
client = "openssh"
known_hosts = 'D:\site\known_hosts'
host_key_policy = "pinned"
identity = 'D:\site\board_key'
ready_timeout_s = 180

[log]
dir = "/var/log/product"
pattern = "*.txt"
```

The rig defaults to OpenSSH and **pinned** host keys; deployment retains its existing explicit/site-config
defaults. Rig `known_hosts`, keys and password files must be outside the product repository.
For old PuTTY configurations, replace `client = "plink"` with `"paramiko"` for password authentication or
`"openssh"` for keys. Provision the authorized host key in `known_hosts`, add `user`, remove `hostkey`,
`ssh` and `scp`, and use a key format accepted by the chosen client. There is no insecure compatibility
fallback or automatic key conversion. Missing user/known_hosts and obsolete fields fail before rig commands.

Rig log fetch now lists exact files with the board's `find -maxdepth 1 -type f -name ... -print0` and
downloads each through the shared transport under one total budget. The board must support these options.
OpenSSH file transfers explicitly use its legacy SCP protocol (`scp -O`, OpenSSH 8.7 or newer), making
remote path quoting deterministic and avoiding a silent requirement for an SFTP server on the board.
Empty, incomplete, duplicate, traversing or nonportable filenames fail; downloads never overwrite existing
local files, and failed transfers remove their owned partial output. Paramiko uses SCP without requiring SFTP.
These are file-transfer/connection checks, not proof that product logs are complete or a board is safe.

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
