# Plan: split into GeneralRobotFramework + SMMAutomationFramework (+ WindchillMcpServer)

Status: GitHub cutover approved and implemented on 2026-10-12. All work happens on D:.

The implementation is now at the core repository root. AI-TestBench was renamed to GeneralRobotFramework;
private SMMAutomationFramework and WindchillMcpServer repositories retain their filtered histories.
GRF `v0.1.0` is published and SMM pins it. See [current repository layout](REPOSITORIES.md).
The phase notes below preserve the staged plan/history rather than current installation instructions.
Organization transfer has no specified destination and is not performed. Offline/rig revalidation is a separate
operator-authorized activity; the cutover regression uses the mock tier and does not touch the instrument.

P0-P3 complete locally. Repository extraction and remote/governance actions remain pending.
P2 is commit `6babd00`. Local gates: 30 standalone GRF tests, 173 SMM tests (goldens unchanged), SMM coverage above
the 55% floor, clean ruff/mypy and SMM lint/drift/matrix gates. P2 mock: 37 passed / 5 skipped.
The wheel-only demo also passes without SMM installed; its report correctly labels mock verdicts as non-evidence.
CI workflows are wired and YAML-checked; their GitHub runs await a push.
P4 local preparation and P6 local governance are implemented; see
[`FRAMEWORK-SPLIT-CUTOVER.md`](FRAMEWORK-SPLIT-CUTOVER.md) for extraction and approval boundaries.
Extraction candidates from `10d6c8d` are verified from their root layouts: GRF 41 pytest, SMM 173 pytest,
service 50 Vitest, mock 37 passed / 5 skipped, and offline MCP syntax/help. Histories and exact HEAD contents
are retained. P5 is only locally verified: final checkout/remote CI and real-product gates are still pending.

## Goal

| Repo | Content | Local checkout |
|---|---|---|
| **GeneralRobotFramework** (AI-TestBench, renamed) | Generic base: RV&S traceability pipeline, tiers/capabilities, rig control, operator steps, doctor, CLI, Robot core library, lint engine, reports, agent and CI templates, `grf init` scaffold | `D:\projects\GeneralRobotFramework` |
| **SMMAutomationFramework** (new, history of `smm-automation\` kept) | TypeScript service and SMM TestBench pin, SMM keywords, ICD, `.sil`, catalog, suites, findings | `D:\projects\SMMAutomationFramework` |
| **WindchillMcpServer** (new, history kept) | RV&S MCP server and `rvs` CLI | `D:\projects\WindchillMcpServer` |

To start a project you clone GRF first, then the project framework, as siblings. Development uses an editable
install of the sibling GRF; CI pins a GRF release tag.

## Assessment

- **Feasible.** About 60–70 % of the Python code is generic in substance and only carries SMM names: pipeline,
  capabilities, rigcontrol, rig_ssh, operator_prompt, the doctor framework, the CLI skeleton, the service client base,
  and the lifecycle and timing keywords.
- **Beneficial.** The reusable core is RV&S → catalog → brief → test → drift → report, with the review ledger and
  the agents.
- **Risk: premature abstraction.** Mitigation: a demo product inside GRF acts as a second consumer and as its
  end-to-end test, and code is promoted to core only when it is generic today.
- **Stays SMM-specific:**
  - the TypeScript service, SMMTestbench keywords, `sil.py` and `icd.py`;
  - the catalog, suites, environments, findings and TestBench pin.

## Design

- **Package and CLI:** `general-robot-framework` / `grf`, CLI `grf`. SMM keeps `smm-auto` as a wrapper.
- **`grf.toml` per product:** tag prefix, paths, RV&S scope, tag→capability map, lint keyword→tag rules, rig restart
  targets, service command/URL and the plugin entry point.
- **`ProductPlugin` (entry point):** catalog enrichment, doctor checks, tier capabilities, mutation faults, brief
  keyword docs and rules text. Core never imports product code.
- **Robot:** `GrfLibrary` covers lifecycle, evidence, timing, Operator Action and the timeline HTML; `SMMTestbench`
  builds on it. SMM keyword names stay unchanged.
- **Behaviour preserved:**
  - tags, spechashes and drift baselines are unchanged;
  - the `SMM0x` lint ids, `SMM_*` env vars and the `smm_automation.rig_ssh` shim keep working, so
    `D:\rig\rig_ssh.toml` needs no change.

## Phases

- **P0 Baseline:**
  - tag `pre-split`;
  - record the green baseline;
  - add golden-file tests for drift, lint, report, matrix and briefs.
- **P1 Config and plugin seam, in place:** remove the hard-coded SMM constants from the generic modules. Gate: green,
  goldens equal.
- **P2 Carve `general-robot-framework\src\grf` inside the repo:**
  - move the generic modules and their tests;
  - add shims for the old module paths;
  - split the Robot library.
  - Gate: green, goldens equal, mock tier unchanged.
- **P3 GRF self-sufficient:**
  - demo product with a fake RV&S fixture, as the end-to-end test;
  - `grf init` template, verified by scaffolding a throwaway project;
  - reusable workflows (`workflow_call`);
  - generic agent templates.
- **P4 Extract (needs your OK for GitHub repo creation, push and rename):**
  1. Use `git filter-repo` on fresh clones in `D:\projects\_split\`: SMMAutomationFramework gets `smm-automation`,
     the SMM docs, agents and workflow; WindchillMcpServer gets `windchill-mcp-server`.
  2. Turn AI-TestBench into GRF: remove the two folders, move GRF to the root, update the docs, rename the repo.
  3. Rename the local folders. You do the rename of `D:\projects\AI-TestBench` or make a fresh clone, because open
     sessions lock it.
  4. Wire them up: `scripts\setup.ps1` (sibling editable install), a CI read token, `WINDCHILL_MCP_SERVER`.
- **P5 Verify:**
  - GRF CI green;
  - SMM: pytest, vitest, lints, matrix `--check`, drift with zero diffs, mock 37 passed / 5 skipped, offline with only
    the known findings failing, and a rig subset with you;
  - update `copilot-instructions.md`, the exports path, the handbooks and the example TOML;
  - you edit the windchill entry in `~/.copilot/mcp-config.json`; I provide the exact line.
- **P6 Governance:**
  - SemVer, CHANGELOG and tags;
  - the pinned version is checked by `grf doctor`;
  - a GRF-bump PR workflow;
  - CONTRIBUTING (generic vs product), CODEOWNERS, branch protection;
  - move to a GitHub organisation.

## Decisions

- The split is staged.
- WindchillMcpServer gets its own repo.
- AI-TestBench becomes GRF, and SMMAutomationFramework is a new repo.

## Deviations from the plan (recorded as they happen)

- **P4/P6 local preparation:**
  - Fresh-clone history filtering is scripted and verifies every extracted HEAD blob against the source tree.
    Candidates stay inside `general-robot-framework\results\split`, rather than `D:\projects\_split`, to honor
    the instruction that all ongoing work remains inside the active D: checkout. They have no remotes.
  - A sibling-aware SMM setup script installs the lock and editable packages, with scratch/cache paths on D:.
    Remote dependency URLs/read tokens and final workflow/doc path rewiring await the owner/publication approval.
  - Local SemVer/CHANGELOG/CONTRIBUTING policy and known-maintainer CODEOWNERS are present. Products pin GRF
    `0.1.0` exactly and doctor checks installed-version equality; this is not a Git commit provenance check.
    Release tags, bump-PR automation, organization transfer and branch protection remain gated.

- **P3:**
  - The wheel ships the runnable starter, including generic author/reviewer agents; `grf init` can run without a
    project and never overwrites a nonempty destination. `examples\demo` is generated from the same templates.
  - The demo uses a synthetic counter and a fake RV&S fixture (no new SMM coverage). Its `demo-ingest` plugin
    command uses the real catalog builder; an independent test also exercises normal `ingest` with a fake client.
  - GRF owns the root `Tier` run metadata. Previously SMM wrote it from its library, which masked a generic bug:
    a new product's child-suite metadata was not propagated, so its mock report was incorrectly treated as evidence.
    The generic CLI now supplies the tier; SMM goldens are unchanged.
  - `grf-ci.yml` is a reusable Windows/Linux core check workflow, including a wheel-only generated-product smoke
    run. Product CI remains separate; remote release pins and private repository read tokens await P4.

- **P2:**
  - Fifteen generic modules now live in `general-robot-framework\src\grf`; old imports alias the same module
    objects so existing callers and monkeypatches remain compatible. SMM-specific integration tests and goldens
    stay in SMM; independent neutral tests cover GRF without importing SMM.
  - `GrfLibrary` owns timing/operator/lifecycle helpers. Thin SMM keyword wrappers preserve original documentation
    used by the golden briefs. The product service client stays SMM-specific.
  - MCP is a GRF dependency. SMM coverage includes GRF to preserve its existing gate. Before GRF is published,
    lock regeneration resolves a local wheel and excludes GRF itself from the third-party lock.
  - Mock fault actions are configured, not hard-coded in core. The SSH compatibility module still runs;
    its argparse program name is now `grf.rig_ssh`.

- **P1:**
  - The seam is `smm_automation\project.py`, which reads `smm-automation\grf.toml` (`GRF_PROJECT` selects another
    project), and `plugin.py` (`ProductPlugin`). It still lives in `smm_automation`; P2 moves it to `grf`.
  - `ProductPlugin` hooks: `known_messages`, `brief_sections`, `doctor_class` and `add_commands`. Tier
    capabilities, mutation faults (env name), the brief rules (`catalog\brief-rules.md`) and the keyword docs are plain
    configuration, not hooks.
  - The SMM plugin is `product.py` (`SmmPlugin`). The SMM doctor checks (service, pin, environment, offline,
    rig broker, `--deep`) are in `smm_doctor.py` (`SmmDoctor`). `matrix` and `service` are plugin commands, so the
    state matrix (`pipeline\matrix.py`) and `client.py` stay SMM-specific.
  - The rig control API is `restart(target, down_ms)` for the `[rig] targets` (`restart_appsmm`/`restart_broker` are
    gone). The ssh rig control waits for `[rig] ready_ports` (the broker's 1883) unless the target sets `port`.
  - Texts, lint codes, env vars, tags and the help of `smm-auto` are unchanged for SMM, except the parallel-tier error,
    which now points to `[tiers]` in `grf.toml`, the generic `doctor --deep` help, and the order of the commands in
    `--help` (plugin commands last).

## Open points (proposed defaults)

1. **Visibility:** all three private, in a GitHub organisation, for multi-person access and shared secrets.
2. **Names:** `grf` / `general-robot-framework`, with `smm-auto` kept.
3. **Exports:** `D:\projects\WindchillMcpServer\exports` through `RVS_EXPORT_DIR`.
4. **Unpushed commits:** push the current unpushed `main` commits before P0.

## Out of scope

- No changes to the SMM TestBench or appSMM.
- No new test coverage.
- No generic TypeScript service yet.
