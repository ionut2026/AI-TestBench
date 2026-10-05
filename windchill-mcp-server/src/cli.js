#!/usr/bin/env node
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { fileURLToPath } from "node:url";

const SERVER_PATH = fileURLToPath(new URL("./server.js", import.meta.url));

const HELP = `Windchill RV&S terminal client (no AI)

Usage:
  rvs <command> [arguments] [options]
  npm run rvs -- <command> [arguments] [options]
  node src/cli.js <command> [arguments] [options]

Commands:
  find <text>               Find a term; shows counts per type/project/state
                            (options: --type, --project, --limit; use search/count to filter by state)
  search                    Search and list matching items
  count                     Count matching items, grouped by field
  get <id> [id ...]         Show full details for item IDs
  types [filter]            List item types
  states [type]             List workflow states (optionally for a type)
  queries [filter]           List saved queries
  query <name>              Run a saved query
  help                      Show this help

Search and count filters:
  --type <name>             Repeatable; e.g. --type "user story"
  --project <name>          Repeatable; e.g. --project Replicant
  --state <name>            Repeatable; e.g. --state Tested
  --text <text>             Search text fields
  --text-field <name>       Repeatable; default Summary,Text,Description
  --text-match <mode>       phrase (default), allWords, anyWord
  --assignee <name>         Assigned user, login, or email
  --created-by <name>       Creator, login, or email
  --date-field <name>       Date field for date filters (default Modified Date)
  --from-date <YYYY-MM-DD>  Inclusive date lower bound
  --to-date <YYYY-MM-DD>    Inclusive date upper bound
  --last-days <number>      Date window ending today
  --where <JSON>            Extra field filters, e.g. --where '{"Product":"Replicant B"}'
  --no-subprojects          Do not include descendants of selected projects
  --case-sensitive          Match text case exactly

Search options:
  --field <name>            Output field/column; repeatable
  --sort <field>            Sort by a field
  --ascending               Sort ascending (default is descending)
  --limit <number>          Page size (default 50)
  --offset <number>         Number of items to skip (default 0)

Count options:
  --group-by <field>        Repeatable; default State (maximum 3)
  --max-scan <number>       Maximum items to scan (default 10000)

Other options:
  --json                    Print the complete tool response as JSON
  get: --history, --attachments, --include-empty, --rich-text

Examples:
  rvs find PRA --type "user story"
  rvs search --type "user story" --state Tested --text PRA --text-field Summary
  rvs count --type "user story" --text PRA --group-by State
  rvs get 3309926
  rvs query "All PRA User Stories" --limit 100
`;

const SEARCH_FILTER_OPTIONS = new Set([
  "type", "project", "state", "text-field", "group-by", "field",
]);

function fail(message) {
  console.error(`Error: ${message}`);
  console.error("Run 'rvs help' for usage.");
  process.exitCode = 2;
}

function parseArgs(args) {
  const positional = [];
  const options = new Map();
  const boolOptions = new Set([
    "ascending", "case-sensitive", "no-subprojects", "json",
    "history", "attachments", "include-empty", "rich-text",
  ]);

  for (let i = 0; i < args.length; i++) {
    const token = args[i];
    if (!token.startsWith("--")) {
      positional.push(token);
      continue;
    }

    const name = token.slice(2);
    if (!name) throw new Error("Empty option name.");
    if (boolOptions.has(name)) {
      options.set(name, true);
      continue;
    }
    const value = args[++i];
    if (value === undefined || value.startsWith("--")) throw new Error(`Option --${name} requires a value.`);
    if (SEARCH_FILTER_OPTIONS.has(name)) {
      const values = options.get(name) || [];
      values.push(value);
      options.set(name, values);
    } else {
      if (options.has(name)) throw new Error(`Option --${name} may only be specified once.`);
      options.set(name, value);
    }
  }

  return { positional, options };
}

function scalar(options, name, fallback) {
  const value = options.get(name);
  return value === undefined ? fallback : value;
}

function numberOption(options, name, fallback, min, max = Number.MAX_SAFE_INTEGER) {
  const raw = scalar(options, name);
  if (raw === undefined) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`--${name} must be an integer from ${min} to ${max}.`);
  }
  return value;
}

function values(options, name) {
  return options.get(name);
}

function parseWhere(value) {
  if (value === undefined) return undefined;
  let parsed;
  try {
    parsed = JSON.parse(value);
  } catch (error) {
    throw new Error(`--where must be valid JSON: ${error.message}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("--where must be a JSON object, e.g. --where '{\"Product\":\"Replicant B\"}'.");
  }
  return parsed;
}

function filterArguments(options) {
  const filters = {
    types: values(options, "type"),
    projects: values(options, "project"),
    states: values(options, "state"),
    text: scalar(options, "text"),
    textFields: values(options, "text-field"),
    textMatch: scalar(options, "text-match"),
    assignedUser: scalar(options, "assignee"),
    createdBy: scalar(options, "created-by"),
    dateField: scalar(options, "date-field"),
    fromDate: scalar(options, "from-date"),
    toDate: scalar(options, "to-date"),
    inLastDays: options.has("last-days") ? numberOption(options, "last-days", undefined, 1) : undefined,
    where: parseWhere(scalar(options, "where")),
    includeSubprojects: options.has("no-subprojects") ? false : undefined,
    caseSensitive: options.has("case-sensitive") ? true : undefined,
  };
  if (filters.textMatch && !["phrase", "allWords", "anyWord"].includes(filters.textMatch)) {
    throw new Error("--text-match must be phrase, allWords, or anyWord.");
  }
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined));
}

function flatten(value) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map(flatten).join("; ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function printTable(rows, columns) {
  if (!rows.length) {
    console.log("No items returned.");
    return;
  }
  const widths = columns.map((column) =>
    Math.min(60, Math.max(column.length, ...rows.map((row) => Math.min(flatten(row[column]).length, 60)))),
  );
  const format = (row) =>
    columns.map((column, i) => {
      const text = flatten(row[column]).replace(/\s+/g, " ");
      return (text.length > widths[i] ? `${text.slice(0, widths[i] - 1)}…` : text).padEnd(widths[i]);
    }).join("  ");
  console.log(format(Object.fromEntries(columns.map((column) => [column, column]))));
  console.log(widths.map((width) => "-".repeat(width)).join("  "));
  for (const row of rows) console.log(format(row));
}

function output(result, { json = false, toolName, fields } = {}) {
  const blocks = result.content || [];
  const text = blocks.filter((block) => block.type === "text").map((block) => block.text).join("\n");
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = undefined;
  }

  if (result.isError) throw new Error(text || "MCP tool call failed.");
  if (json || data === undefined) {
    console.log(data === undefined ? text : JSON.stringify(data, null, 2));
    return;
  }
  if (data?.items && Array.isArray(data.items)) {
    const columns = toolName === "rvs_get_items"
      ? Object.keys(data.items[0] || {})
      : (fields?.length ? fields : ["ID", "Summary", "State", "Project", "Modified Date"]).filter((key) =>
          data.items.some((item) => Object.hasOwn(item, key)),
        );
    if (columns.length) printTable(data.items, columns);
    else console.log(JSON.stringify(data.items, null, 2));
    if (typeof data.count === "number") console.log(`\n${data.count} item(s) returned.`);
    if (data.hasMore) console.log(`More results are available. Repeat with --offset ${data.nextOffset ?? data.offset + data.count}.`);
    if (data.resolved?.length) console.log(`Resolved: ${data.resolved.join("; ")}`);
    if (data.hints?.length) console.log(`Hints: ${data.hints.join(" ")}`);
    return;
  }
  if (data?.groups && Array.isArray(data.groups)) {
    console.log(`Total: ${data.total} (grouped by ${data.groupBy})`);
    printTable(data.groups, ["value", "count"]);
    if (data.resolved?.length) console.log(`Resolved: ${data.resolved.join("; ")}`);
    return;
  }
  if (data && typeof data === "object" && !Array.isArray(data) &&
      Object.values(data).every((value) => typeof value === "string")) {
    printTable(Object.entries(data).map(([Field, Value]) => ({ Field, Value })), ["Field", "Value"]);
    return;
  }
  console.log(JSON.stringify(data, null, 2));
}

function quote(value) {
  return /^[\w.:@/-]+$/.test(value) ? value : `"${value.replace(/"/g, '\\"')}"`;
}

function reconstructOptions(options) {
  const parts = [];
  for (const [name, value] of options) {
    if (value === true) parts.push(`--${name}`);
    else for (const v of [].concat(value)) parts.push(`--${name} ${quote(v)}`);
  }
  return parts.join(" ");
}

function commandArguments(command, positional, options) {
  const commandOptions = {
    find: ["type", "project", "limit", "json"],
    search: ["type", "project", "state", "text", "text-field", "text-match", "assignee", "created-by",
      "date-field", "from-date", "to-date", "last-days", "where", "no-subprojects", "case-sensitive",
      "field", "sort", "ascending", "limit", "offset", "json"],
    count: ["type", "project", "state", "text", "text-field", "text-match", "assignee", "created-by",
      "date-field", "from-date", "to-date", "last-days", "where", "no-subprojects", "case-sensitive",
      "group-by", "max-scan", "json"],
    get: ["field", "history", "attachments", "include-empty", "rich-text", "json"],
    types: ["json"],
    states: ["json"],
    queries: ["json"],
    query: ["field", "limit", "offset", "json"],
  };
  const allowed = commandOptions[command];
  if (!allowed) throw new Error(`Unknown command '${command}'.`);
  const unknown = [...options.keys()].filter((key) => !allowed.includes(key));
  if (unknown.length) {
    let message = `Option(s) --${unknown.join(", --")} are not supported by '${command}'.`;
    if (command === "find" && unknown.every((key) => commandOptions.search.includes(key))) {
      message += `\n'find' searches all states and shows counts per state. To filter, use 'search' (list) or 'count', e.g.:\n` +
        `  rvs search --text ${quote(positional.join(" "))} ${reconstructOptions(options)}`;
    }
    throw new Error(message);
  }

  switch (command) {
    case "find": {
      if (!positional.length) throw new Error("find requires a text argument.");
      return {
        name: "rvs_find",
        arguments: {
          query: positional.join(" "),
          types: values(options, "type"),
          projects: values(options, "project"),
          limit: numberOption(options, "limit", 20, 1, 200),
        },
      };
    }
    case "search":
    case "count": {
      if (positional.length) throw new Error(`${command} takes options, not positional arguments.`);
      const filters = filterArguments(options);
      if (command === "search") {
        return {
          name: "rvs_search_items",
          arguments: {
            ...filters,
            fields: values(options, "field"),
            sortField: scalar(options, "sort"),
            sortAscending: options.has("ascending") ? true : undefined,
            limit: numberOption(options, "limit", 50, 1, 1000),
            offset: numberOption(options, "offset", 0, 0),
          },
        };
      }
      const groupBy = values(options, "group-by") || ["State"];
      if (groupBy.length > 3) throw new Error("count accepts at most three --group-by fields.");
      return {
        name: "rvs_count_items",
        arguments: {
          ...filters,
          groupBy,
          maxScan: numberOption(options, "max-scan", 10000, 1, 50000),
        },
      };
    }
    case "get": {
      const ids = positional.flatMap((part) => part.split(/[,\s;]+/).filter(Boolean));
      if (!ids.length) throw new Error("get requires one or more item IDs.");
      if (ids.length > 50) throw new Error("get accepts at most 50 item IDs per call.");
      return {
        name: "rvs_get_items",
        arguments: {
          ids,
          fields: values(options, "field"),
          includeHistory: options.has("history") || undefined,
          includeAttachments: options.has("attachments") || undefined,
          includeEmpty: options.has("include-empty") || undefined,
          richText: options.has("rich-text") || undefined,
        },
      };
    }
    case "types":
      return { name: "rvs_list_item_types", arguments: { filter: positional.join(" ") || undefined } };
    case "states":
      return { name: "rvs_list_states", arguments: { type: positional.join(" ") || undefined } };
    case "queries":
      return { name: "rvs_list_queries", arguments: { filter: positional.join(" ") || undefined } };
    case "query":
      if (!positional.length) throw new Error("query requires a saved query name.");
      return {
        name: "rvs_run_query",
        arguments: {
          query: positional.join(" "),
          fields: values(options, "field"),
          limit: numberOption(options, "limit", 50, 1, 1000),
          offset: numberOption(options, "offset", 0, 0),
        },
      };
    default:
      throw new Error(`Unknown command '${command}'.`);
  }
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  if (!command || command === "help" || command === "--help" || command === "-h" || args.includes("--help") || args.includes("-h")) {
    console.log(HELP);
    return;
  }

  const { positional, options } = parseArgs(args);
  const request = commandArguments(command, positional, options);
  const env = Object.fromEntries(Object.entries(process.env).filter(([, value]) => value !== undefined));
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env,
  });
  const client = new Client({ name: "windchill-terminal-client", version: "1.0.0" });

  try {
    await client.connect(transport);
    const result = await client.callTool(request);
    output(result, {
      json: options.has("json"),
      toolName: request.name,
      fields: request.arguments.fields,
    });
  } finally {
    await client.close();
  }
}

main().catch((error) => fail(error.message || String(error)));
