# Windchill RV&S MCP Server

An [MCP](https://modelcontextprotocol.io) server that gives GitHub Copilot (CLI and VS Code) **read-only** access
to Windchill RV&S (formerly PTC Integrity / MKS): projects, user stories, requirements, specifications, documents,
test cases, test sessions/results, complaints, saved queries, and more — in **any** project.

You ask Copilot in plain English ("list all Draft user stories for PRA in Replicant"); Copilot finds the right
projects, types, states and fields by itself and answers from live RV&S data.

## Contents

* [How it works](#how-it-works)
* [Installation](#installation)
* [Autonomous use with Copilot](#autonomous-use-with-copilot)
* [Using it to query Integrity / Windchill RV&S](#using-it-to-query-integrity--windchill-rvs)
* [Tools](#tools)
* [Loose name resolution](#loose-name-resolution)
* [Text search and performance](#text-search-and-performance)
* [Filtering on any field (`where`)](#filtering-on-any-field-where)
* [Troubleshooting](#troubleshooting)

## How it works

```
Copilot (CLI / VS Code) ──stdio──> Node MCP server (src/server.js)
                                          │ line-delimited JSON
                                          ▼
                     Java bridge (java/RvsBridge.java) + mksapi.jar
                                          │ RV&S Java API (local integration point)
                                          ▼
                   Installed Windchill RV&S client ──> RV&S server
```

The bridge uses the **locally installed RV&S client** (`C:\Program Files\Integrity\ILMClient13`) and its
already-authenticated session, so no password is stored. It is compiled with the JRE bundled with the client,
so no separate JDK is required.

| File                                    | Purpose                                                                 |
|-----------------------------------------|-------------------------------------------------------------------------|
| `src/server.js`                         | MCP tools, filter engine (`where`, relationship filters, counting)      |
| `src/resolve.js`                        | Cached catalogs and loose name resolution (types, states, projects, fields, users) |
| `src/instructions.js`                   | Search playbook sent to Copilot (also installed as a Copilot instructions file) |
| `src/bridge.js`                         | Starts/compiles the Java bridge and talks to it                         |
| `src/format.js`                         | Converts RV&S values / rich text into compact JSON and plain text       |
| `java/RvsBridge.java`                   | Runs RV&S API commands through the local client                         |
| `scripts/install-copilot-instructions.mjs` | Installs the playbook as a global Copilot CLI instructions file      |
| `setup.cmd`, `scripts/setup.mjs`        | One-command setup: checks, `npm install`, Copilot/VS Code registration, connection test |

## Installation

Installation takes about 5 minutes per machine. Each person installs it under their own Windows login, and Copilot
then sees RV&S data with **their own Integrity user** and permissions. No passwords are stored.

### Before you start

Make sure you have:

- [ ] **Windows 10 or 11**
- [ ] **Windchill RV&S client** installed (normally in `C:\Program Files\Integrity\ILMClient13`)
- [ ] **An Integrity account** that can log in to `alm.stratec.com:7001`
- [ ] **GitHub Copilot**: Copilot CLI and/or VS Code with Copilot Chat, signed in
- [ ] **Node.js 18+**. Check with `node -v`. If it's missing, the setup offers to install it.

### Install in 4 steps

#### Step 1: Log in to the RV&S client

Open the Windchill RV&S client and log in to **alm.stratec.com**, port **7001**, with your own user.
You only need to do this once. The MCP server reuses this login.

#### Step 2: Copy the server folder to your PC

Get the `rvs-mcp-server` folder from a colleague or a shared drive and put it in a permanent place, for example
`C:\tools\rvs-mcp-server`. You don't need the `node_modules` and `build` sub-folders, because the setup creates them.

> To copy it from a PC that already has it:
> `robocopy C:\projects\rvs-mcp-server \\OTHER-PC\c$\tools\rvs-mcp-server /E /XD node_modules build`

#### Step 3: Run the setup

Open PowerShell or a Command Prompt and run:

```powershell
cd C:\tools\rvs-mcp-server
.\setup.cmd
```

Every step should show `OK`. The last lines tell you which Integrity user you're connected as:

```
[7/7] Testing the connection to alm.stratec.com:7001
      OK   Windchill RV&S 13 13.2.0.4_986 (API 4.16.2)
      OK   connected to stratec-ptc11.intern.stratec.com:7001 as Integrity user 'IPop'
      OK   102 item types visible

Setup finished successfully.
```

#### Step 4: Restart Copilot and try it

| Where       | What to do                                                                                     |
|-------------|------------------------------------------------------------------------------------------------|
| Copilot CLI | Type `/restart` (or close and start `copilot` again). `/mcp` should show `windchill`.       |
| VS Code     | `Ctrl+Shift+P` → **Developer: Reload Window**. Then `Ctrl+Shift+P` → **MCP: List Servers** → `windchill` → **Start**. Open Copilot Chat and pick **Agent** mode. Click the tools icon and check that `windchill` is enabled. |
| VS Code, **Copilot CLI** session | If you picked the *Copilot CLI* session type in the chat, the server starts only after you send the **first message**. Send a message and wait; don't cancel it. |

Then ask Copilot: **"Is the windchill MCP server OK?"** It should answer with the RV&S version.

Done. Continue with [Using it to query Integrity](#using-it-to-query-integrity--windchill-rvs).

### If something goes wrong

| Message / symptom                                    | Fix                                                                                                 |
|------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| `Node.js was not found` (setup installed it)         | Close the terminal, open a new one, and run `.\setup.cmd` again.                                    |
| `No Windchill RV&S client found`                     | Install the client, or point to it: `.\setup.cmd --client-home "D:\PTC\ILMClient13"`.               |
| `'npm install' failed`                               | No access to the npm registry. Set your proxy (`npm config set proxy http://proxy:port` and `npm config set https-proxy http://proxy:port`), then run the setup again. |
| `WARN Connection test failed` / timeout              | Open the RV&S client, log in, and run `.\setup.cmd` again.                                           |
| `WARN Could not update ...mcp.json`                  | The file contains comments. Add the entry by hand (see [Manual registration](#manual-registration)). |
| Copilot doesn't show `windchill`                 | Restart Copilot or VS Code. In the CLI, check `/mcp`. In VS Code, open **MCP: List Servers** → `windchill` → **Show Output** to see the log. |
| VS Code chat is slow or hangs, with `git status timed out` in the logs | Don't open VS Code on a whole drive (for example `C:\`). Open a project folder instead (**File → Open Folder**). |
| Wrong Integrity user                                 | Log out and log in as the right user in the RV&S client, then restart Copilot.                       |

Setup exit codes: `0` = OK, `1` = failed, `2` = finished with warnings.

### More details

#### What the setup does

| Step              | Action                                                                                                   |
|-------------------|----------------------------------------------------------------------------------------------------------|
| 1. Node.js        | Checks that Node.js 18+ is installed.                                                                     |
| 2. RV&S client    | Finds the client in `Program Files\Integrity`, `PTC` or `MKS` (or uses `--client-home`).                  |
| 3. npm install    | Installs the two dependencies and compiles the Java bridge with the client's own Java (no JDK needed).    |
| 4. Copilot CLI    | Adds `windchill` to `%USERPROFILE%\.copilot\mcp-config.json`.                                         |
| 5. VS Code        | Adds `windchill` to `%APPDATA%\Code\User\mcp.json` (and VS Code Insiders, if installed).              |
| 6. Playbook       | Installs `%USERPROFILE%\.copilot\instructions\windchill.instructions.md` so Copilot searches RV&S on its own. |
| 7. Test           | Connects to RV&S and shows the version, the server and your Integrity user.                               |

Existing entries for other MCP servers are kept, and the previous config files are saved as `.bak`.
The setup can be run again at any time, for example after an update. `npm run setup` does the same as `.\setup.cmd`.

#### Setup options

| Option                | Default            | Use it to                                              |
|-----------------------|--------------------|--------------------------------------------------------|
| `--host <name>`       | `alm.stratec.com`  | connect to another RV&S server                         |
| `--port <n>`          | `7001`             | use another port                                       |
| `--client-home <dir>` | auto-detected      | point to a client installed in an unusual folder       |
| `--no-cli`            |                    | skip Copilot CLI registration                          |
| `--no-vscode`         |                    | skip VS Code registration                              |
| `--no-instructions`   |                    | skip the Copilot playbook                              |
| `--skip-test`         |                    | skip the connection test (e.g. when offline)           |
| `--help`              |                    | show all options                                       |

Example: `.\setup.cmd --host alm-test.stratec.com --port 7001 --no-vscode`

#### Several users and machines

* **One setup per Windows user.** Copilot and VS Code settings are stored per user, so every person runs `.\setup.cmd`
  once, even on a shared PC. They can all use the same copy of the folder.
* **Which Integrity user is used?** The one logged in to the RV&S client on that PC. The setup's connection test shows it.
* **Permissions:** the server is read-only and only sees what that Integrity user may see.
* **Moved the folder?** Run `.\setup.cmd` again, because the configuration stores the folder path.

#### Update or uninstall

* **Update:** copy the new files over the folder, run `.\setup.cmd`, and restart Copilot.
* **Uninstall:**
  1. Remove the `windchill` entry from `%USERPROFILE%\.copilot\mcp-config.json` and `%APPDATA%\Code\User\mcp.json`.
  2. Delete `%USERPROFILE%\.copilot\instructions\windchill.instructions.md`.
  3. Delete the folder.

#### Environment variables

The setup writes these into the MCP configuration. To change them, edit them there or re-run the setup.

| Variable          | Default                                  | Description                              |
|-------------------|------------------------------------------|------------------------------------------|
| `RVS_HOSTNAME`    | client default server                    | RV&S server host, e.g. `alm.stratec.com` |
| `RVS_PORT`        | client default port                      | RV&S server port, e.g. `7001`            |
| `RVS_CLIENT_HOME` | `C:\Program Files\Integrity\ILMClient13` | RV&S client install folder               |
| `RVS_TIMEOUT_MS`  | `180000`                                 | Per-command timeout                      |
| `RVS_PREWARM`     | `1`                                      | `0` disables pre-loading the bridge and name catalogs at startup |

#### Manual registration

You only need this if the setup couldn't edit a config file. Replace the path with your folder.

Copilot CLI: `%USERPROFILE%\.copilot\mcp-config.json`

```json
{
  "mcpServers": {
    "windchill": {
      "type": "local",
      "command": "node",
      "args": ["C:\\tools\\rvs-mcp-server\\src\\server.js"],
      "env": { "RVS_HOSTNAME": "alm.stratec.com", "RVS_PORT": "7001" },
      "tools": ["*"]
    }
  }
}
```

VS Code: `%APPDATA%\Code\User\mcp.json`

```json
{
  "servers": {
    "windchill": {
      "type": "stdio",
      "command": "node",
      "args": ["C:\\tools\\rvs-mcp-server\\src\\server.js"],
      "env": { "RVS_HOSTNAME": "alm.stratec.com", "RVS_PORT": "7001" }
    }
  }
}
```

Fully manual install: run `npm install` (set `RVS_CLIENT_HOME` first if the client is not in the default folder),
then `npm run install-instructions`, then register the server as shown above.
## Autonomous use with Copilot

So Copilot can find RV&S data without your help:

* **Search playbook:** the server sends search guidance to Copilot (`src/instructions.js`):
  1. start with `rvs_find` for unknown terms;
  2. use loose names;
  3. filter on any field with `where`;
  4. widen the search step by step when results are empty;
  5. say which projects, types and filters were searched;
  6. don't ask the user to clarify unless the tools can't settle it.

  `npm run install-instructions` installs the same text as
  `%USERPROFILE%\.copilot\instructions\windchill.instructions.md`, so it applies in every folder.
  Re-run it after you edit `src/instructions.js`.
* **No approval prompts:** all tools are marked read-only. If your permission mode still asks before running
  them, start Copilot with `copilot --allow-tool windchill`, or choose "approve for the rest of the
  session" the first time a tool runs.
* **After changing the server code**, restart Copilot (or reload the server via `/mcp`) so the new process is used.

## Using it to query Integrity / Windchill RV&S

You don't call the tools directly. You chat with Copilot and it calls the tools for you.

1. **Check the prerequisites:** the server is registered (see [Installation](#installation)) and you've logged in to the RV&S client at
   least once on this machine.
2. **Open Copilot:** run `copilot` in a terminal, or open Copilot Chat in VS Code. The `rvs_*` tools are
   available automatically.
3. **Ask in plain English.** Copilot picks the tools and parameters. Examples:

   | You ask                                                              | Copilot calls                                                                                  |
   |----------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
   | "Where is PRA in RV&S?"                                              | `rvs_find` `query: "PRA"` → shows PRA items live in `/REPLICANT A` and `/REPLICANT B`, by type and state |
   | "List all Draft user stories for PRA in Replicant."                  | `rvs_search_items` `types: ["user story"]`, `projects: ["Replicant"]`, `states: ["Draft"]`, `text: "PRA"`, `textFields: ["Summary"]` |
   | "How many PRA user stories are there per state?"                     | `rvs_count_items` `types: ["user story"]`, `text: "PRA"`, `textFields: ["Summary"]`, `groupBy: ["State"]` |
   | "Which PRA user stories have no explorative test?"                   | `rvs_search_items` … `where: {"Relevant Explorative Test": {"op": "empty"}}`                   |
   | "Replicant Team user stories for product Replicant B, by state."     | `rvs_count_items` `where: {"team": "Replicant Team", "Product": "Replicant B"}`, `groupBy: ["State"]` |
   | "Complaints in Replicant from the last year, per state."             | `rvs_count_items` `types: ["complaint"]`, `projects: ["Replicant"]`, `inLastDays: 365`         |
   | "Open requirements assigned to John Doe changed in the last 30 days." | `rvs_search_items` `types: ["Requirement"]`, `assignedUser: "John Doe"`, `inLastDays: 30`, `where: {"State": {"op": "!=", "value": ["Closed"]}}` |
   | "Which fields does a Test Case have?"                                | `rvs_describe_type` `type: "Test Case"`                                                        |
   | "Show me item 3080491."                                              | `rvs_find` / `rvs_get_items` `ids: ["3080491"]`                                                |
   | "Trace requirement 123456 down to its test cases and steps."         | `rvs_get_relationships` `ids: ["123456"]`, `direction: "forward"`, `expandLevel: 3`            |
   | "Read Requirement Document 98765."                                   | `rvs_get_document` `documentId: 98765` (Markdown, paged)                                       |
   | "Run the saved query 'All PRA User Stories'."                        | `rvs_run_query` `query: "all pra user stories"`                                                |
   | "Show the test results of test session 123456."                      | `rvs_run_command` `app: "tm"`, `command: "results"`, `selection: ["123456"]`                   |

4. **Refine with follow-ups,** e.g. "show the next 50", "only the ones in Defined", "add the Product column".
   Copilot calls the tool again with new `offset`, `where`, or `fields` values.

## Tools

| Tool                    | Purpose                                                                                       |
|-------------------------|-----------------------------------------------------------------------------------------------|
| `rvs_find`              | **Start here.** Look up a keyword or item IDs everywhere: matching projects, types, states, fields, saved queries, users, and items (with counts per type / project / state) |
| `rvs_search_items`      | Structured search in any project: types, projects (+ sub-projects), states, text, assignee, creator, dates, any field (`where`), raw query |
| `rvs_count_items`       | Same filters as search; returns counts grouped by up to 3 fields (State, Type, Project, Product, Assigned User…) |
| `rvs_list_projects`     | List project paths (top-level by default; use `filter` / `maxDepth`)                         |
| `rvs_list_item_types`   | List item types (User Story, Requirement, Test Case, …)                                       |
| `rvs_list_states`       | List workflow states, optionally for one type (state names differ per type)                  |
| `rvs_list_users`        | Find users by login, full name or e-mail                                                      |
| `rvs_describe_type`     | Fields, mandatory fields, workflow of a type                                                  |
| `rvs_describe_field`    | Data type / allowed values / relationship details of a field                                  |
| `rvs_list_queries`      | Saved queries visible to you                                                                  |
| `rvs_run_query`         | Run a saved query (name matched loosely)                                                      |
| `rvs_get_items`         | Full item details (rich text → plain text), optional history / attachments / as-of baseline   |
| `rvs_get_relationships` | Traverse traceability (requirement → specification → test case → test steps …)               |
| `rvs_get_document`      | Read a Requirement / Specification / Test Design document as Markdown (paged, filterable)    |
| `rvs_run_command`       | Any other read-only `im` / `tm` command (allow-listed)                                        |

All tools are read-only; there is no way to create, edit, or delete RV&S items through this server.

## Loose name resolution

You don't need exact RV&S names. The search tools resolve them and list each resolution under `resolved` in the response:

| You write                            | Resolved to                                                                     |
|--------------------------------------|---------------------------------------------------------------------------------|
| type `user stories`                  | `ASD-User Story`                                                                |
| state `Draft` (for user stories)     | `ASD-Draft` (resolved against each type's own workflow)                         |
| project `Replicant`                  | `/REPLICANT A`, `/REPLICANT B`, `/Replicant IWS`, `/Vigilant Online/Replicant` (+ sub-projects) |
| project `REPLICANT B/V&V`            | `/REPLICANT B/V&V`                                                              |
| user `Pradeau`                       | login `cpradeau` (login, full name or e-mail)                                   |
| saved query `all pra user stories`   | `All PRA User Stories`                                                          |
| field `team`                         | `ASD-Team` (fields visible on the searched types are preferred)                 |
| `where: {"Product": "Replicant B"}`  | `Replicant B - (2837382)` (pick-list / linked-item values)                      |

Unknown or ambiguous names fail with a list of valid or similar values instead of silently returning nothing.
Empty results include `hints` on how to widen the search. Catalogs of projects, types, states, fields, users and saved queries are
cached for 30 minutes.

## Text search and performance

* **Case and spelling variants:** RV&S `contains` is case-sensitive, so the server searches several spellings of the text at once
  (as typed, lower/UPPER/Title case, joined and spaced CamelCase): `SDCardCorrupted` also finds `SdCardCorrupted` and `SD card corrupted`.
  Pass `caseSensitive: true` to search only the exact text. `rvs_find` lists the spellings it tried under `searchedSpellings`.
* **Word fallback:** if `rvs_find` finds no item for the whole phrase, it searches for items that contain all of the words.
* **Per-field queries:** a single RV&S query that ORs `contains` over several long-text fields (e.g. Text or Description) takes 10 s or more,
  so each text field is queried separately in parallel and the results are merged (sorted by ID, or by `sortField`).
  With `textMatch: "allWords"` and several `textFields`, all words must occur in the same field.
* **Parallel and pre-warmed:** `rvs_find` runs its catalog lookups and item searches in parallel. At startup the server starts the Java bridge
  and loads the name catalogs in the background, so the first question doesn't wait for them. A typical `rvs_find` takes about 1 s once warm.

## Filtering on any field (`where`)

`rvs_search_items` and `rvs_count_items` accept `where: { <field>: <value> }` (all entries are AND-ed):

| Form                                                       | Meaning                                                   |
|------------------------------------------------------------|-----------------------------------------------------------|
| `{"Product": "Replicant B"}`                               | equals (pick-list, user, number fields) / contains (text) |
| `{"Priority": ["High", "Critical"]}`                       | any of                                                    |
| `{"State": {"op": "!=", "value": ["Tested", "Accepted"]}}` | not equal (`notContains` for text)                        |
| `{"Created Date": {"op": ">=", "value": "2026-01-01"}}`    | date / number comparison: `>`, `>=`, `<`, `<=`, `between` (`[a, b]`), `inLastDays` |
| `{"Product": {"op": "empty"}}`                             | `empty` / `notEmpty`                                      |
| `{"Relevant Explorative Test": {"op": "empty"}}`           | relationship fields: `empty`, `notEmpty`, `=` / `!=` item IDs |

RV&S can't query relationship fields directly, so the server applies those filters after the query runs.
It checks up to 20,000 items, and the response notes when this happened.

For anything else, pass a raw RV&S `queryDefinition`, e.g. `(field["Category"] = "Requirement")`.

## Troubleshooting

* **Installation problems:** see [If something goes wrong](#if-something-goes-wrong).
* **Is the server running?** Ask Copilot "is the windchill MCP server OK?". It calls
  `rvs_run_command` with `command: "about"`, which returns the RV&S version when the connection works.
* **New tools or changes not visible:** restart Copilot or reload the server via `/mcp`.
* **Login prompts / errors:** the RV&S client must be installed and logged in at least once (its cached credentials are reused).
  If the session expires, the client may ask you to log in again.
* **Large results:** queries can match tens of thousands of items. Results are streamed and cut at `limit` (use
  `offset` to page). Use `rvs_count_items` for totals.
* **Inactive projects:** `im projects` lists some inactive projects that RV&S rejects in queries. The search tools
  drop them automatically.
