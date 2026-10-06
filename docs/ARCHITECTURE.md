# AI-TestBench — Project Notes

Repo: `ionut2026/AI-TestBench`. The repository currently contains a single project:
`windchill-mcp-server/` (full user-facing docs in its own `README.md`). This file is a technical/
architecture summary for anyone (human or AI assistant) picking the project back up.

## What it is

A Node.js **MCP (Model Context Protocol) server** named `windchill` (package name `rvs-mcp-server`,
v1.0.0) that gives GitHub Copilot (CLI and VS Code) **read-only** access to **Windchill RV&S**
(formerly PTC Integrity / MKS) — an ALM/requirements-and-test-management system. It exposes projects,
user stories, requirements, specifications, documents, test cases, test sessions/results, complaints,
and saved queries across any RV&S project, via plain-English Copilot queries
(e.g. "list all Draft user stories for PRA in Replicant").

## Architecture (3 layers, stdio pipeline)

```
Copilot (CLI/VS Code) --stdio--> Node MCP server (src/server.js)
                                       | line-delimited JSON
                                       v
                  Java bridge (java/RvsBridge.java) + mksapi.jar
                                       | RV&S Java API
                                       v
                Installed Windchill RV&S client --> RV&S server (alm.stratec.com:7001)
```

- Uses the **locally installed RV&S client** (default `C:\Program Files\Integrity\ILMClient13`) and its
  already logged-in session — no password stored. Compiled with the client's bundled JRE (no separate
  JDK needed).
- `RvsBridge.java` is a long-lived child process: reads JSON requests line-by-line on stdin, runs RV&S
  API commands (`im`/`tm` apps), writes `{"id","ok","result"}` / `{"id","ok":false,"error"}` responses
  on stdout. Multi-threaded (8-thread pool) so requests run concurrently. stdout is reserved for the
  protocol; the API's own `System.out` is redirected to stderr.

## Source files (`windchill-mcp-server/`)

- `src/server.js` (largest file) — defines all MCP tools (zod schemas), the generic filter/query engine
  (`where` clauses, text search across fields, relationship post-filtering, project/type/state
  resolution), pagination, grouping/counting.
- `src/resolve.js` — cached catalogs (TTL 30 min) for projects/types/states/fields/queries/users, plus
  **loose name resolution** ("fuzzy"): singularizes/normalizes words so "User Stories" matches
  "ASD-User Story", "Draft" resolves to a type's own state ("ASD-Draft"), "Replicant" matches every
  project path segment starting with it, etc. Also generates text-search spelling variants
  (`textVariants`) since RV&S `contains` is case-sensitive with no wildcards (e.g. "SDCardCorrupted"
  also tries "SD card corrupted", "sd_card_corrupted" forms).
- `src/bridge.js` — `Bridge` class: spawns/manages the Java child process, compiles it on demand
  (`compileBridge`), request/response correlation by `id`, per-request timeout (default 180s), restart
  on crash.
- `src/format.js` — converts RV&S API values to compact JSON/plain text: `simplify()` flattens users,
  computations, IBPL picks, relationship items; `htmlToText()` strips RV&S's "<!-- MKS HTML -->" rich
  text into readable text.
- `src/instructions.js` — the **agent playbook** (`INSTRUCTIONS` string) sent to MCP clients as server
  instructions and also installed as a standalone Copilot instructions file — tells Copilot how to
  search RV&S autonomously (start with `rvs_find`, then `rvs_search_items`/`rvs_count_items`, use
  `where` for arbitrary fields, widen on empty results, use saved queries, traverse relationships, read
  documents, use `rvs_run_command` as an escape hatch).
- `java/RvsBridge.java` / `java/Compile.java` — the Java bridge source and a small helper to compile it
  using `javac` embedded in the client's JRE.
- `scripts/setup.mjs`, `setup.cmd` — one-command setup: checks Node 18+, finds the RV&S client, runs
  `npm install` (which triggers `postinstall` → `build-bridge.mjs` to compile the Java bridge),
  registers the `windchill` MCP server in Copilot CLI (`%USERPROFILE%\.copilot\mcp-config.json`) and
  VS Code (`%APPDATA%\Code\User\mcp.json`), installs the instructions playbook, and tests the RV&S
  connection.
- `scripts/install-copilot-instructions.mjs` — installs `instructions.js`'s playbook as
  `%USERPROFILE%\.copilot\instructions\windchill.instructions.md`.
- `scripts/build-bridge.mjs` — thin wrapper calling `compileBridge()` (runs as npm
  `postinstall`/`build`).

## MCP tools exposed (all prefixed `rvs_`, all read-only, `readOnlyHint: true`)

- **Discovery**: `rvs_list_projects`, `rvs_list_states`, `rvs_list_users`, `rvs_list_item_types`,
  `rvs_describe_type`, `rvs_describe_field`, `rvs_list_queries`.
- **Search/count**: `rvs_search_items` (structured filters: types/projects/states/text/assignedUser/
  createdBy/date range/arbitrary `where` clauses with ops `=, !=, contains, notContains, >, >=, <, <=,
  between, empty, notEmpty, inLastDays` — resolved loosely and translated into RV&S query-definition
  syntax), `rvs_count_items` (same filters, grouped tallies).
- **`rvs_find`** — the recommended entry point: one call searches catalogs (projects/types/states/
  fields/queries/users) and item text (Summary/Text/Description, with spelling variants)
  simultaneously, or looks up item IDs directly.
- `rvs_run_query` — runs a saved RV&S query by (fuzzy) name.
- **Item details**: `rvs_get_items` (full field dump, history, attachments, as-of/baseline views),
  `rvs_get_relationships` (traceability graph traversal, e.g. requirement → specification → test case →
  test steps), `rvs_get_document` (renders a document/segment tree as Markdown or JSON, paginated).
- **Escape hatch**: `rvs_run_command` — runs any other read-only `im`/`tm` RV&S CLI command against an
  explicit allow-list (`READ_ONLY.im` / `READ_ONLY.tm` sets in `server.js`).

## Key design points worth remembering

- Everything is **read-only by design** (tool annotations + a hard allow-list in `rvs_run_command`).
- Heavy emphasis on **loose/fuzzy resolution** of user-facing names (types, states, projects, users,
  fields) so Copilot/users don't need exact RV&S naming; mismatches are reported back via a
  `resolved`/`notes` array in responses for transparency.
- Text search works around two RV&S limitations: (1) `contains` is case-sensitive with no wildcards →
  spelling variant generation; (2) OR-ing `contains` across many long-text fields is slow (10s+) → each
  field is queried in its own parallel request and results are merged client-side.
- Relationship fields can't be filtered in RV&S query definitions, so those filters are applied
  **client-side** after an initial scan (capped by `maxScan`, default 10000–20000).
- Catalogs (projects/types/states/fields/queries/users) are cached for 30 minutes and pre-warmed at
  startup (`RVS_PREWARM` env var can disable this) so the first real query is fast.
- Config via env vars: `RVS_CLIENT_HOME`, `RVS_HOSTNAME`, `RVS_PORT`, `RVS_TIMEOUT_MS` (default
  180000ms), `RVS_PREWARM`.
- Connects to `alm.stratec.com:7001` by default — this is a Stratec-internal RV&S instance.
- Target users: any teammate on Windows with the RV&S client installed; each person's own Integrity
  login/permissions apply (no shared credentials).

## Open questions / things to verify when resuming work

- No automated tests found in the repo (no test runner configured in `package.json` beyond
  build/setup/start scripts).
- Repo history is a single "initial commit" — most context lives in the code itself.
- Setup/connection against a live RV&S server has not been verified from this environment (requires the
  actual Windchill RV&S client + `alm.stratec.com` access).
  *Update:* verified live. `smm-auto ingest` reads 26 specifications, 22 requirements, 10 user stories and
  16 Explorative Tests through this server over MCP stdio.

## SMM test automation framework (`smm-automation/`)

The first consumer of the MCP server. Full details are in `smm-automation/README.md`. The complete handbook for
users and maintainers (non-technical friendly: installation, running, writing tests, extending, internals,
troubleshooting) is [`docs/SMM-AUTOMATION-HANDBOOK.md`](SMM-AUTOMATION-HANDBOOK.md).

- **Ingest.** `smm-automation/src/smm_automation/pipeline/ingest.py` starts `windchill-mcp-server/src/server.js`
  as an MCP stdio client and uses `rvs_get_items` / `rvs_search_items`. It builds `catalog/<scope>.json` with:
  - the specifications in scope and a SHA-256 of each normalized text;
  - their requirements (`Satisfies`) and user stories (`Described In`);
  - the Explorative Tests, parsed into preconditions, steps and expected results.
- **Generation.** `smm-auto briefs` turns each catalog entry into a brief for the
  `.github/agents/smm-test-author.agent.md` agent. The agent writes the Robot tests tagged `review:pending`, the
  read-only `.github/agents/smm-test-reviewer.agent.md` agent can give an independent review, and a human approves
  them.
- **Execution.** `smm-automation/service/` is a headless TypeScript service bundled from the SMM TestBench sources
  at a pinned commit:
  - It acts as the SMM Bridge and runs the tier environment: a mock appSMM, the real appSMM.exe plus the hardware
    twin, or the instrument.
  - It exposes HTTP API v1, which the Robot library `SMMTestbench.py` drives. Every request except `/health` needs
    the service's API token (`SMM_AUTOMATION_TOKEN`, or the one the service generates into `.service/token-<port>`).
    The ICD message names (ingest) and schemas (briefs) also come from this API, so only the service reads the
    TestBench.
  - `smm-auto run --processes N` runs the mock tier in parallel with pabot; each worker process claims a slot by file
    lock and gets its own service and embedded broker. Offline and rig runs stay serial (one appSMM installation with
    fixed ports; one instrument).
- **Traceability.** Tests only carry `SDS-<id>` and `spechash:`; all other links come from RV&S at report time.
  - `smm-auto drift` flags stale, orphan and uncovered tests, RV&S state/link changes of covered specifications
    (against a baseline accepted with `smm-auto accept`) and retired specifications.
  - `smm-auto run` writes `traceability.html/json` next to Robot's log, with specification verdicts rolled up to
    requirements and user stories.
- **Test effectiveness.** `smm-auto mutate` injects defects (fault rules from `catalog/mutants.toml`) into the mock
  appSMM and checks that the tests of the affected specifications fail; a nightly CI job reports surviving mutants.
- **Coverage around the specifications.** `smm-auto matrix` generates a state x request suite from
  `catalog/state-matrix.toml` and lists what the specifications leave open as questions for their owner; a
  robustness suite sends malformed and misrouted input. Tests without a specification carry `nospec:<kind>` and are
  reported apart from the specification coverage.
- **Fault injection and logs (offline tier).** The service wraps the COP link between the real appSMM and the
  hardware twin (`service/src/copFaults.ts`, `/hardware/faults`) to drop, delay or answer with an error, so
  "no reply within N s" specifications are testable; `smm_automation/sil.py` reads appSMM's SmartInspect `.sil`
  logs for specifications about what appSMM logs.
- **Capabilities and the rig.** Tests declare what they need with tags (`needs:twin`, `needs:hardware-action`,
  `needs:operator`, `needs:applog`, `requires:restart`, `requires:broker-restart`); `smm-auto run` excludes the tags
  whose capability the tier lacks (`smm_automation/capabilities.py`). On the rig, restarts and log fetching go through
  a site program named by `SMM_RIG_CONTROL` (`smm_automation/rigcontrol.py`) and E-Stop/other manual steps through an
  operator (`--operator console|dialog`). `smm-auto doctor --tier <tier> [--deep]` checks an environment before a run.
