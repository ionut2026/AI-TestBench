# Use the Windchill RV&S MCP server without AI

This guide shows how to use the `windchill` MCP server yourself, without GitHub Copilot or another AI assistant.
The recommended way is the included `rvs` terminal command: type a command such as `rvs search --type "user story"
--state Tested --text PRA`, and the CLI calls the matching MCP tool for you. MCP Inspector and custom Node.js scripts
are also available for more advanced use.

The server is **read-only**. It can search and read RV&S data, but it cannot create, edit, or delete items.

## Choose how you want to use it

| Method | Best for | What you do |
|---|---|---|
| `rvs` terminal command (recommended) | Everyday manual queries | Enter readable commands and options; results appear in the terminal |
| MCP Inspector | Exploring tool schemas or less common tools | Select a tool in a local web UI and enter its arguments |
| Custom Node.js MCP client | Repeatable reports and automation | Write a script that calls tools and processes their data |

All three methods use the same MCP server and RV&S account. None uses AI to select tools, interpret requests, or
summarize results.

## Prerequisites

1. Use a Windows PC with the Windchill RV&S client installed. The usual location is
   `C:\Program Files\Integrity\ILMClient13`.
2. Open the RV&S client and log in to the appropriate server using your own Integrity account. The server reuses
   the local client's session; it does not ask you to put a password in this guide's commands.
3. Install Node.js 18 or newer (Node.js 22.19 or newer is required only if you also want the current MCP Inspector).
   Open PowerShell and check:

   ```powershell
   node --version
   npm --version
   ```

4. Have a local copy of the `windchill-mcp-server` folder in a stable location. The examples below assume:
   `D:\projects\AI-TestBench\windchill-mcp-server`.
   If yours is elsewhere, replace that path in the commands.

## One-time setup (without Copilot registration)

Open PowerShell and run:

```powershell
Set-Location 'D:\projects\AI-TestBench\windchill-mcp-server'
.\setup.cmd --no-cli --no-vscode --no-instructions
```

This setup installs the server's npm dependencies, compiles its Java bridge, and tests the RV&S connection. The three
options prevent it from registering anything with Copilot CLI or VS Code, or installing Copilot instructions.
They do **not** disable any server tools.

If the RV&S client is installed somewhere other than its default location, pass that path explicitly:

```powershell
.\setup.cmd --client-home 'D:\PTC\ILMClient13' --no-cli --no-vscode --no-instructions
```

If you need to use a non-default RV&S server, supply its host and port as well:

```powershell
.\setup.cmd --host 'alm.example.com' --port 7001 --no-cli --no-vscode --no-instructions
```

The setup output should report a successful connection and the Integrity username. If it cannot connect, start the
RV&S client, sign in, and re-run setup. See the main README's [installation troubleshooting](../README.md#if-something-goes-wrong)
for other setup errors.

## Recommended: use the `rvs` terminal commands

The CLI is installed with the server and connects directly to its MCP tools. It does not use Copilot, AI, or a hosted
service. Each command starts the local MCP server, makes a request, prints the response, then exits.

### One-time setup: enable the `rvs` command

Register the local package as a global command once:

```powershell
Set-Location 'D:\projects\AI-TestBench\windchill-mcp-server'
npm link
rvs help
```

After that, run `rvs <command> ...` directly from any folder, in any new terminal. No `npm run` is needed.

- `npm link` creates a link to this local folder; it does not copy or publish anything. Code changes in the folder
  take effect immediately.
- If you move the server folder, run `npm link` again from the new location.
- To remove the command: `npm unlink -g rvs-mcp-server`.
- If PowerShell cannot find `rvs`, open a new terminal and check that `%APPDATA%\npm` is on `PATH`. If the execution
  policy blocks `rvs.ps1`, use `rvs.cmd ...` instead.

Without linking, the same commands still work from the server folder as `npm run rvs -- <command> ...` (the `--`
passes the remaining arguments to the CLI) or `node .\src\cli.js <command> ...`.

Examples below use the short `rvs ...` form.

### Search and list items

Example: list PRA user stories in the Tested state:

```powershell
rvs search --type "user story" --state Tested --text PRA --text-field Summary
```

The CLI prints a table with IDs, summaries, states, projects, and modified dates. To include only Replicant projects
(including subprojects by default), add:

```powershell
--project Replicant
```

Change page size or retrieve the next page with:

```powershell
rvs search --type "user story" --state Tested --text PRA --text-field Summary --limit 50 --offset 50
```

The response reports the number returned and whether more results are available. `--json` prints the full structured
MCP response, including `resolved`, `queryDefinition`, `hasMore`, and any search hints:

```powershell
rvs search --type "user story" --state Tested --text PRA --text-field Summary --json
```

### Count matches

Count PRA user stories grouped by state:

```powershell
rvs count --type "user story" --text PRA --text-field Summary --group-by State
```

Group by more than one field by repeating `--group-by` (up to three fields), for example
`--group-by State --group-by Project`. Counts scan up to 10,000 matching items by default; increase that cap with
`--max-scan 20000` if the output indicates the scan limit was reached.

### Find terms and inspect items

Use `find` to discover where a keyword occurs across RV&S:

```powershell
rvs find PRA --type "user story"
```

Find returns a structured overview of matching projects, item types, states, saved queries, users, and a sample of
matching items. It prints JSON by default because the result contains several different categories.

`find` accepts only `--type`, `--project` and `--limit`. It always covers every state and reports how many matches
each state has. To filter by state, use `search` or `count`:

```powershell
rvs search --text PRA --type "user story" --state Tested
```

Read full details for one or more known item IDs:

```powershell
rvs get 3309926
rvs get 3309926 3309882 --history
```

### Export all matches with full details

`rvs export` takes the same filters as `search`. It finds every match, fetches each item's full details, and writes
Markdown: an index table, then one section per item. Each section lists the fields, linked specifications, tests and
complaints, and the full Description text (Definition / Acceptance Criteria / Out of Scope).

```powershell
rvs export --type "user story" --state Tested --text PRA --text-field Summary --out pra-tested.md
```

- Files are always saved in the `exports` folder of the repository (`D:\projects\AI-TestBench\exports`), no matter
  which folder you run the command from. The folder is created if needed. Set `RVS_EXPORT_DIR` to use another folder.
- `--out <name>` sets the file name; without it the name is `rvs-export-<yyyyMMdd-HHmm>.md`. An absolute `--out`
  path is used as given.
- `--stdout` prints the Markdown to the terminal instead of saving a file.
- `--field <name>` (repeatable) replaces the default field list.
- `--json` writes JSON instead of Markdown.
- `--limit` defaults to 1000, the maximum. If more items match, a warning is printed.

### Discover types, states, and saved queries

```powershell
rvs types "user story"
rvs states "user story"
rvs queries "PRA"
rvs query "All PRA User Stories" --limit 50
```

Use `rvs help` to print all commands and options. The main command forms are:

| Command | Purpose |
|---|---|
| `rvs find <text>` | Discover matching names and items |
| `rvs search [filters]` | List matching records |
| `rvs count [filters]` | Count records by one or more fields |
| `rvs get <id> [id ...]` | Read full details |
| `rvs export [filters] [--out <name>]` | Export all matches with full details as Markdown to `exports\` |
| `rvs types [filter]` / `rvs states [type]` | Discover type and state names |
| `rvs queries [filter]` / `rvs query <name>` | Find and run saved queries |

Search/count filters include repeatable `--type`, `--project`, `--state`, and `--text-field`; `--text`, `--assignee`,
`--created-by`, `--from-date`, `--to-date`, and `--last-days`; `--where '<JSON object>'`; and `--no-subprojects`.
Search also supports `--field`, `--sort`, `--ascending`, `--limit`, and `--offset`. Count supports repeated
`--group-by` and `--max-scan`. Use `--json` on any command for the complete response. The server's
[filter reference](../README.md#filtering-on-any-field-where) explains the available `--where` operators.

For non-default RV&S installations, configure the environment for the current PowerShell session before running
commands:

```powershell
$env:RVS_CLIENT_HOME = 'D:\PTC\ILMClient13'
$env:RVS_HOSTNAME = 'alm.example.com'
$env:RVS_PORT = '7001'
rvs search --type "user story" --text PRA --text-field Summary
```

Only set values that differ from your defaults. The same variables are read by the setup and server.

## Alternative: manual use with MCP Inspector

MCP Inspector is a generic, non-AI MCP client. Its web interface lets you list the server's tools, enter each tool's
arguments, run it, and inspect the raw response.

1. In PowerShell, set the folder and launch Inspector with this server as its stdio target:

   ```powershell
   Set-Location 'D:\projects\AI-TestBench\windchill-mcp-server'
   npx -y @modelcontextprotocol/inspector node 'D:\projects\AI-TestBench\windchill-mcp-server\src\server.js'
   ```

   The first launch downloads Inspector from npm if it is not already cached, so internet or npm-registry access may
   be required. Do not close this PowerShell window while using the Inspector; it owns the server process.

   If your RV&S client or server uses a non-default location, set the environment variables before launching Inspector.
   Set only the values that apply to your installation:

   ```powershell
   $env:RVS_CLIENT_HOME = 'D:\PTC\ILMClient13'
   $env:RVS_HOSTNAME = 'alm.example.com'
   $env:RVS_PORT = '7001'
   npx -y @modelcontextprotocol/inspector node 'D:\projects\AI-TestBench\windchill-mcp-server\src\server.js'
   ```

2. Open the local address printed in the terminal (the Inspector usually opens its web UI automatically). Keep the
   Inspector local; it is only a client interface and does not host RV&S data.
3. Connect to the stdio server if the UI has not connected automatically. Confirm that the tools list includes names
   such as `rvs_find`, `rvs_search_items`, and `rvs_get_items`.
4. Select a tool, fill in its argument fields, and run it. The Inspector displays the response; it does not turn your
   request into natural language or summarize the results for you.
5. When finished, stop Inspector with **Ctrl+C** in the PowerShell window.

### Example: list PRA user stories in Tested state

Choose `rvs_search_items` and enter the following arguments (the UI may present these as individual fields rather
than one JSON editor):

```json
{
  "types": ["user story"],
  "states": ["Tested"],
  "text": "PRA",
  "textFields": ["Summary"],
  "limit": 50,
  "offset": 0,
  "fields": ["ID", "Summary", "State", "Project", "Modified Date"]
}
```

The server resolves the loose type and state names against RV&S. Read the `resolved` and `queryDefinition` values in
the response to confirm what was searched. `count` is the number returned on this page, not necessarily the total
number of matches. If `hasMore` is true, run the same call again with `offset` increased by the number of items
returned (for example, use `offset: 50` for the next page).

Add a project constraint if you only want a particular project tree, for example:
`"projects": ["Replicant"]`. By default, matching also includes sub-projects. Remove the project constraint to search
all projects.

### Useful tools to call manually

| Tool | When to use it | Example arguments |
|---|---|---|
| `rvs_find` | Discover where a keyword appears and which types, projects, states, and saved queries match | `{"query":"PRA","types":["user story"]}` |
| `rvs_search_items` | List items matching filters | See the PRA example above |
| `rvs_count_items` | Count matching items, optionally grouped by fields | `{"types":["user story"],"text":"PRA","textFields":["Summary"],"groupBy":["State"]}` |
| `rvs_get_items` | Read full details for known item IDs | `{"ids":["3309926"]}` |
| `rvs_list_states` | Check the exact workflow states for an item type | `{"type":"user story"}` |
| `rvs_list_item_types` | Find the exact type name | `{"filter":"user story"}` |
| `rvs_list_queries` | List saved queries visible to your account | `{}` |
| `rvs_run_query` | Execute a saved query by its name | `{"query":"All PRA User Stories","limit":50,"offset":0}` |

Tool argument names and additional filters are defined by each tool's input schema; Inspector displays that schema
when you select a tool. For `where` filters, text matching, sorting, relationships, and pagination, see the main
README's [tool reference](../README.md#tools), [text search notes](../README.md#text-search-and-performance), and
[field filter syntax](../README.md#filtering-on-any-field-where).

## Advanced: call tools from a custom Node.js script

This option is useful if you want a saved, repeatable query or need to process the returned data. The server already
depends on the MCP SDK, so you can use that installation; no AI SDK or AI service is involved.

1. Create a file named `manual-query.mjs` in the `windchill-mcp-server` folder.
2. Paste the following example. It connects to the local server process, lists the tools available, runs the PRA
   search, prints the response as JSON, and closes the connection:

   ```js
   import { Client } from "@modelcontextprotocol/sdk/client/index.js";
   import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

   const serverPath = String.raw`D:\projects\AI-TestBench\windchill-mcp-server\src\server.js`;
   const transport = new StdioClientTransport({
     command: process.execPath,
     args: [serverPath],
     env: {
       ...process.env,
       RVS_HOSTNAME: process.env.RVS_HOSTNAME || "alm.stratec.com",
       RVS_PORT: process.env.RVS_PORT || "7001",
     },
   });

   const client = new Client({ name: "manual-rvs-client", version: "1.0.0" });

   try {
     await client.connect(transport);

     const { tools } = await client.listTools();
     console.log("Available tools:", tools.map((tool) => tool.name).join(", "));

     const result = await client.callTool({
       name: "rvs_search_items",
       arguments: {
         types: ["user story"],
         states: ["Tested"],
         text: "PRA",
         textFields: ["Summary"],
         limit: 50,
         offset: 0,
         fields: ["ID", "Summary", "State", "Project", "Modified Date"],
       },
     });

     console.log(JSON.stringify(result, null, 2));
   } finally {
     await client.close();
   }
   ```

3. If your server directory is not `D:\projects\AI-TestBench\windchill-mcp-server`, update `serverPath`.
4. If your RV&S host or port differs, replace `alm.stratec.com` / `7001`, or set the values in PowerShell before
   running the script:

   ```powershell
   $env:RVS_HOSTNAME = 'alm.example.com'
   $env:RVS_PORT = '7001'
   ```

5. Run it from the server folder so Node can resolve the installed SDK:

   ```powershell
   Set-Location 'D:\projects\AI-TestBench\windchill-mcp-server'
   node .\manual-query.mjs
   ```

The response includes MCP content blocks; this example prints them as JSON. Adapt the script to inspect
`result.content`, parse text blocks, write CSV/JSON files, or page through results. Check `hasMore` in each search
response and increase `offset` to fetch subsequent pages. Keep errors visible rather than treating a failed call as
an empty result.

For repeated automation, use the MCP tool interface (`client.callTool`) instead of building query-definition strings
by hand. Prefer `rvs_count_items` for totals and `rvs_search_items` for records.

## What runs locally and what still needs RV&S

- `src/server.js` is a local stdio MCP process; it starts when the CLI, Inspector, or your script connects and stops
  when that client exits.
- The server starts a Java bridge using the Java runtime and API JAR bundled with the Windchill RV&S client.
- RV&S requests still require access to the configured RV&S server, and results are limited by the logged-in
  Integrity user's permissions.
- The `rvs_*` tools are read-only. In particular, `rvs_run_command` is restricted to an allow-list of read-only
  `im` and `tm` commands.
- The Inspector is a client UI, not a query language or a database. You must choose tools and provide their
  structured arguments; a script can automate those steps without AI.

## Troubleshooting

| Symptom | Check |
|---|---|
| `node` or `npm` is not recognized | Install Node.js and open a new PowerShell window. |
| Setup cannot find the RV&S client | Re-run setup with `--client-home 'C:\path\to\ILMClient13'`. The folder must contain `jre\bin\java.exe` and `lib\mksapi.jar`. |
| Connection test fails or times out | Open the RV&S client, log in, verify network access to the host, then repeat setup. |
| `rvs` is not recognized | Run `npm link` once in the server folder, open a new terminal, and confirm `%APPDATA%\npm` is on `PATH`. Meanwhile use `npm run rvs -- help` from the server folder or `node .\src\cli.js help`. |
| Inspector does not list tools | Keep the launching terminal open, verify that Inspector connected to the server, and check its output for a server startup error. |
| Script says the SDK package cannot be found | Run the script from the `windchill-mcp-server` folder after setup has completed successfully. |
| Search returns no matches | Check the `resolved`, `queryDefinition`, and `hints` in the response. Verify exact type/state with `rvs_list_item_types` and `rvs_list_states`; try removing one filter at a time. |
| Results appear incomplete | Check `hasMore` and page using `offset`; use `rvs_count_items` for counts. |

For advanced settings such as `RVS_CLIENT_HOME` and `RVS_TIMEOUT_MS`, see the main README's
[environment variables](../README.md#environment-variables).
