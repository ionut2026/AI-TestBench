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
