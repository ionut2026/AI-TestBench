# Changelog

GRF uses Semantic Versioning. Additive public APIs use a minor release; compatible fixes use a patch release.
Before 1.0, incompatible API changes also require a minor release. Release tags are `vMAJOR.MINOR.PATCH`.
Tags and publication are explicit maintainer actions.

## 0.4.0 - Unreleased

- Terminate owned descendant process trees on local rig/SSH/SCP and mutation timeout/cancellation,
  including inherited output-pipe holders. Windows ownership uses a kill-on-close job established
  before launching the site command; ownership setup failure aborts without launching it.
- Enforce operator dialog timeout using an owned subprocess; expiry closes the dialog and fails
  the step. Console timeout behavior is unchanged.
- Compensating rollback proceeds even if its first journal checkpoint fails; persistent errors remain
  explicit. Retry only bounded transient Windows sharing/permission failures during atomic snapshots.

- Drain both SSH command streams before waiting for exit, avoiding channel backpressure deadlock.
  Bound SCP and readiness/stability/retries with one monotonic deadline. Expose
  `grf.deadline.Deadline` for product adapters; late successes cannot reset an expired budget.
- Keep read-only deployment status free of evidence-output writes; final snapshots are attempted
  even when transport close fails.

- Always attempt rig restart recovery after stop/downtime failure or cancellation; report both errors
  if recovery fails. Reject invalid downtime before stop.
- Stage each log fetch separately and return only freshly delivered files; reject destination collisions.
  **Migration:** use a fresh log evidence destination on retries.
- Require complete finite clock offset/uncertainty and reject contradictory clock samples.

- Validate deployment/rig numeric ranges before operations: finite positive timeouts/load limits,
  nonnegative downtime/stability/free space, valid integer ports/retention/TTL, and correctly typed schema.
  Component stability must fit readiness budget. Reject unknown deployment and SSH rig fields.
  **Migration:** correct typos rather than relying on silently ignored configuration.

- Reserve deployment output exclusively before connecting. Persist flushed, atomic pre-action and
  completion snapshots (including rollback commands) at every step. Journal write failure aborts normal
  work without blocking compensating cleanup; unfinished records remain explicitly incomplete.
  **Migration:** never reuse deployment output folders; defaults now include microseconds.

- Read preflight, holds, rollback snapshot and unchanged decisions under the board lock.
  Unchanged installations must also pass configured health/stability checks.
- Fail closed on Docker inspection/transport errors; only explicit not-found means absent.
  Label new containers by operation and remove only a verified operation-owned container ID
  during rollback, including a successful start whose SSH response was lost. Name conflicts
  preserve foreign containers and require manual recovery.
- Compare mount read/write mode during drift checks and remove global Docker image pruning;
  deployment retention is limited to the component repository.

- Serialize deployment-lock initialization/takeover/release and require the matching owner token
  on release. Incomplete/legacy metadata is never automatically stale. **Migration:** commission
  one GRF version per board; board owners must inspect abandoned guard/legacy locks manually.

- Split product identity policy into `installed_matches` (requested versus installed builds)
  and `runtime_matches` (installed build versus runtime observation). The Robot deployment binding
  now shares the runtime policy with acceptance. **Migration:** legacy `identity_matches` overrides
  remain a runtime-only fallback; migrate overrides to `runtime_matches`. Installed comparisons
  default to exact text and never invoke runtime protocol comparison.
- Reject duplicate suite-qualified test identities before collection/report joins and at acceptance.
  Rename ambiguous tests before recording human reviews; one approval cannot cover different test bodies.
- Derive deployment identity from the loaded image and roll back filename/image-version contradictions.

## 0.3.0 - Unreleased

- Added the opt-in `integration-coordinator` custom agent and `grf-coordinate-integration` shared skill,
  shipped in core, starter templates and demo. They guide affected-repository-only delegation, exact contracts,
  immutable-commit consumer validation and framework/consumer release sequencing; they do not launch workers,
  reconnect sessions, grant permissions or provide authenticated/human approval.
  **Adoption:** existing products copy both files from one reviewed immutable GRF commit and retain product
  mappings separately; package upgrades do not rewrite them. Coordination honors user-selected direct-main
  or branch/PR work, preserving uncommitted work and protections. Direct-main uses one writer per repository
  and asks on conflicts. Push/merge/tag each require matching explicit authorization.

- Added schema-1 TOML run requests (`grf request check FILE [--json FILE]`) and the post-run
  `grf gate --request FILE --output output.xml [--deployment FILE]` command, writing `acceptance.json`.
  Exit codes distinguish ACCEPTED (0), REJECTED (1), and input/integration errors (2).
  Strict gates require actual evidence tiers, required tests/specs, current human content reviews, result/skip
  policy and matching deployment/runtime identities. Request authorization text is not authentication.
- Human reviews now record `grf-content-1` content fingerprints covering the test, suite settings and transitive
  Robot resources. **Migration:** preserve old reviews, but have a human review/re-record them before strict
  acceptance; never manufacture approvals. Python/external configuration still need protected source pins.
- Added optional tier `lock`, defaulting to the tier name for non-parallel tiers. `grf run` holds a local OS
  file lock through Robot execution and rejects competing runs using the same results root/name.
  **Migration:** configure shared local resources with the same trusted results root and lock name.
  This is not a distributed rig reservation; the protected controller must lock deployment through acceptance.
- Added `ProductPlugin.runtime_identity(meta)` and `identity_matches(component, expected, observed)` hooks.
  **Migration:** consuming adapters needing runtime identity must extract fresh run metadata; the default
  empty identity fails required checks. Products own endpoint mapping and documented version semantics.
- Added optional `--sha256` to single-component `deploy plan`/`run` (`Options.sha256` in Python): exact 64
  lowercase hex digest checked after download and before board connection. Mismatch returns
  `ARTIFACT_FAILED`/4; malformed CLI values return 2. Pin numeric build plus digest when promoting a plan.
- Added workflow guidance and a post-test Jenkins acceptance stage; starter pipelines remain uncommissioned
  templates requiring protected request/source policy and an authoritative site lock.
- Made Windows setup diagnostic tests robust to long worktree paths and corrected the unsupported-Python
  fixture's PowerShell/native quoting; setup behavior is unchanged.
- Added the read-only `grf.deploy.binding.assert_product_version` helper and inherited
  `Deployment Record Should Match Product Version` Robot keyword. They check an explicit deployment file's
  component, target, optional expected project, successful installed outcome and exact observed version.
  Only `DEPLOYED`/`UNCHANGED`/`RESTORED`, explicit non-dry-run records and running installed versions qualify;
  requested artifact versions are never a fallback. Product adapters own fresh observation and endpoint mapping.
- Clarified that `--deployment` attaches provenance only, not proof of runtime version or board identity.
  No deployment or network action is performed by the new assertion. Existing suite behavior and configuration
  are unchanged. See `docs\DEPLOYMENT.md#checking-the-observed-product-version`.
- Migration: update both the dependency pin and `[framework] version` to an approved 0.3.0 commit/release
  together, then validate the consuming adapter. Existing products must explicitly call the new keyword or
  helper to use it; no automatic setup is added. Starter and checked-in demo pins now use 0.3.0.
  No release tag is created by this change.

## 0.2.0 - 2026-10-15

Released as `v0.2.0`; the next release remains a maintainer decision after review.

- Added `grf deploy` (`<cli> deploy` in products): deploys a Jenkins build (an image tarball) of a product
  component to an instrument board over SSH with Docker, and records it. Subcommands `components`, `builds`,
  `plan` (dry run), `run`, `status` and `rollback`. Only `SUCCESS` builds with exactly one matching artifact,
  SHA-256 checked locally and on the board; read-only preflight (hold file, docker, disk, load, companions);
  board lock; health check and configuration verification; automatic rollback to the previous container
  (`ROLLED_BACK`), `ROLLBACK_FAILED` with recovery commands; retained earlier builds and `deploy rollback`.
  Distinct exit codes 0/2/3/4/5/6/7/8. Real deployments need the instrument owner's authorization; agents
  never start one. See `docs\DEPLOYMENT.md`.
- Added the `ProductPlugin.deploy_components()` hook (default: none) and the optional `[deploy]` section
  (`order`, `default_branch`) in `grf.toml`. Hosts and credentials live only in a site config outside the
  repository (`<ENV_PREFIX>_DEPLOY_CONFIG`); secret keys and in-repository credential files are rejected.
- Added the optional extra `general-robot-framework[deploy]` (paramiko) for the default SSH client; the
  `openssh` client needs no extra. Jenkins is read with the standard library (no new runtime dependency).
- `grf run` and `grf report` accept `--deployment <file or folder>` (or `GRF_DEPLOYMENT_RECORD`) and show a
  "Deployed builds" section in the HTML report and a `deployments` list in the JSON report.
- `grf doctor --deploy [--deploy-config F] [--deep]` checks a machine's deployment setup (read-only).
- `grf init --with-deploy` also creates `robot\environments\deploy.example.toml` and
  `jenkins\Jenkinsfile.deploy` (parameters, nightly cron, credentials, deploy then test). Added the
  `grf-deploy-build` skill and deployment guidance in `grf-doctor-tier`.
- Added Copilot skills for setup, authoring, review, report interpretation, drift, mutation testing, tier checks,
  product plugins, template upgrades, GRF validation and deployment. `grf init` now creates ten generic skills in
  `.github\skills`; existing products must adopt them manually. See `docs\SKILLS.md`.
- Checked explicit Python executable paths directly during Windows setup and disabled module auto-discovery
  for named-command lookup, avoiding unnecessary discovery when Python is missing. Added a regression test
  for the missing-path failure that timed out on Windows CI.
- Added `scripts\setup.ps1` for explicit one-command Windows setup after cloning: creates/reuses `.venv`,
  installs GRF/Robot Framework/development dependencies and checks compatibility and imports. Installation
  scratch/cache paths stay under `results`; no product operations or system-policy changes are performed.
  Explained why cloning alone does not install dependencies and documented manual installation alternatives.
- Added a beginner walkthrough covering Robot/GRF/product responsibilities, vocabulary, a line-by-line test,
  explained Windows setup/run commands, report reading and safe failure practice. Linked reference guides
  and added introductory explanations to the starter. Copilot instructions now require beginner-friendly explanations.
- Explained GRF's purpose and contribution to consumer projects, with an illustrative SMM workflow,
  new-product adoption steps, dependency/version concepts and explicit limits of framework automation.
- Clarified why shared infrastructure is optional, its reuse/maintenance tradeoffs, the distinction between
  generated briefs and product tests, and the separate ownership/update paths of generic and product agent instructions.
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

These changes are not yet a tagged release. Consumers must review these behavior changes and run their
authorized regression before adopting a new approved tag.

Migrating a product to 0.2.0:

1. Change the GRF dependency pin and `[framework] version` in `grf.toml` to `0.2.0` together.
2. Nothing else is required: without `deploy_components()` the product has no deployable components, and
   `--deployment` is optional.
3. To deploy: implement `deploy_components()`, optionally add `[deploy]`, install `general-robot-framework[deploy]`
   on the Jenkins agent, and create the site config and Jenkins job (`docs\DEPLOYMENT.md`).
4. Adopt the new or changed skills (`grf-deploy-build`, `grf-doctor-tier`) with `grf-template-upgrade`.

## 0.1.0 - 2026-10-12

- Extracted generic project configuration, plugins, CLI and read-only RV&S traceability pipeline from SMM.
- Added reusable Robot timing/operator/rig helpers, preserving SMM compatibility shims.
- Added packaged `grf init`, a synthetic demo, independent tests and reusable Windows/Linux CI.
- Added exact approved-version checks in doctor via `[framework] version`.

First extracted core release. Product dependencies pin the `v0.1.0` Git tag.
