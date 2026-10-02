#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { bridge, config } from "./bridge.js";
import { simplify, simplifyFields, simplifyItem, toText } from "./format.js";
import { INSTRUCTIONS } from "./instructions.js";
import {
  allFields,
  allProjects,
  allQueries,
  allStates,
  allTypes,
  allUsers,
  fmtUser,
  fuzzy,
  fieldInfo,
  resolveField,
  resolveProjects,
  resolveStates,
  resolveTypes,
  resolveUsers,
  splitWords,
  textVariants,
  typeStates,
} from "./resolve.js";

const server = new McpServer({ name: "windchill", version: "1.1.0" }, { instructions: INSTRUCTIONS });

const DEFAULT_LIST_FIELDS = ["ID", "Type", "Summary", "State", "Project", "Assigned User", "Modified Date"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Read-only commands allowed through rvs_run_command.
const READ_ONLY = {
  im: new Set([
    "issues", "viewissue", "viewsegment", "relationships", "diffsegments", "projects", "viewproject", "types", "viewtype",
    "fields", "viewfield", "queries", "viewquery", "states", "viewstate", "users", "viewuser", "groups", "viewgroup",
    "dynamicgroups", "viewdynamicgroup", "reports", "viewreport", "charts", "viewchart", "dashboards", "viewdashboard",
    "cps", "viewcp", "timeentries", "viewsourcetraces", "perspectives", "viewduplicates", "about", "servers",
  ]),
  tm: new Set(["results", "stepresults", "testcases", "verdicts", "viewresult", "viewverdict", "viewuntested", "resultfields"]),
};

const ok = (data) => ({ content: [{ type: "text", text: typeof data === "string" ? data : toText(data) }] });
const fail = (err) => ({ isError: true, content: [{ type: "text", text: `RV&S error: ${err.message || err}` }] });

function tool(name, description, inputSchema, handler) {
  server.registerTool(name, { description, inputSchema, annotations: { readOnlyHint: true, openWorldHint: true } }, async (args) => {
    try {
      return ok(await handler(args));
    } catch (err) {
      return fail(err);
    }
  });
}

/** Quotes a value for an RV&S query definition (the grammar has no escape for double quotes). */
const q = (v) => `"${String(v).replace(/"/g, "'")}"`;
const field = (name) => `field[${q(name)}]`;
const fmtDate = (iso) => {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) throw new Error(`Invalid date '${iso}', expected YYYY-MM-DD`);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
};

const matchFilter = (filter) => {
  if (!filter) return () => true;
  const f = filter.toLowerCase();
  return (s) => s.toLowerCase().includes(f);
};

const invalidProjects = new Set();

async function runItems(req, { offset = 0, limit = 50, opts = {} } = {}) {
  const r = await bridge.run({ ...req, limit: offset + limit, maxFieldLength: opts.maxFieldLength ?? 4000 });
  const items = r.workItems.slice(offset).map((w) => simplifyItem(w, opts));
  return {
    count: items.length,
    offset,
    hasMore: r.truncated,
    ...(r.truncated ? { nextOffset: offset + items.length } : {}),
    items,
  };
}

const limitSchema = z.number().int().min(1).max(1000).default(50).describe("Maximum number of items to return");
const offsetSchema = z.number().int().min(0).default(0).describe("Number of items to skip (for paging)");
const fieldsSchema = z.array(z.string()).optional().describe(`Fields (columns) to return. Default: ${DEFAULT_LIST_FIELDS.join(", ")}`);

// ---------- Discovery ----------

tool(
  "rvs_list_projects",
  `List Windchill RV&S projects (hierarchical paths such as '/2PS/Bio-Rad/Requirements').
Without a filter only top-level projects are listed (there are well over a thousand projects); use 'filter' or 'maxDepth' to see more.
Note: search tools accept project names loosely (e.g. 'Replicant' matches '/REPLICANT A' and '/REPLICANT B'), so listing is rarely required.`,
  {
    filter: z.string().optional().describe("Case-insensitive substring filter on the project path"),
    maxDepth: z.number().int().min(1).max(10).optional().describe("Only list projects up to this depth (1 = top-level). Default: 1 without filter, unlimited with filter"),
  },
  async ({ filter, maxDepth }) => {
    const depth = maxDepth ?? (filter ? undefined : 1);
    const all = (await allProjects()).filter(matchFilter(filter));
    const list = depth ? all.filter((p) => p.split("/").filter(Boolean).length <= depth) : all;
    return { count: list.length, ...(list.length < all.length ? { totalMatching: all.length, maxDepth: depth } : {}), projects: list };
  },
);

tool(
  "rvs_list_states",
  "List workflow states. With 'type', lists the states of that item type's workflow (state names differ per type, e.g. ASD-User Story uses 'ASD-Draft', not 'Draft').",
  {
    type: z.string().optional().describe("Item type (fuzzy), e.g. 'User Story'"),
    filter: z.string().optional().describe("Case-insensitive substring filter"),
  },
  async ({ type, filter }) => {
    if (type) {
      const { types } = await resolveTypes([type]);
      const out = {};
      for (const t of types) out[t] = (await typeStates(t)).filter(matchFilter(filter));
      return types.length === 1 ? { type: types[0], states: out[types[0]] } : { statesByType: out };
    }
    const list = (await allStates()).filter(matchFilter(filter));
    return { count: list.length, states: list };
  },
);

tool(
  "rvs_list_users",
  "Find users by login ID, full name or e-mail (e.g. to get the login ID for an 'Assigned User' filter).",
  { filter: z.string().describe("Part of the login, full name or e-mail; multiple words must all match") },
  async ({ filter }) => {
    const words = filter.toLowerCase().split(/[\s,]+/).filter(Boolean);
    const list = (await allUsers()).filter((u) => words.every((w) => `${u.id} ${u.fullname || ""} ${u.email || ""}`.toLowerCase().includes(w)));
    return { count: list.length, users: list.slice(0, 100).map((u) => ({ id: u.id, name: u.fullname, email: u.email })) };
  },
);

tool(
  "rvs_list_item_types",
  "List item types (e.g. 'ASD-User Story', 'Requirement', 'Requirement Document', 'Specification', 'Test Case', 'Test Session').",
  { filter: z.string().optional().describe("Case-insensitive substring filter") },
  async ({ filter }) => {
    const types = await allTypes();
    let list = types.filter(matchFilter(filter));
    if (!list.length && filter) list = fuzzy(filter, types);
    return { count: list.length, types: list };
  },
);

tool(
  "rvs_describe_type",
  "Describe an item type: description, document role, visible fields, mandatory fields per state, workflow, relationships. Use it to learn which field names exist before searching.",
  { type: z.string().describe("Item type name, e.g. 'Test Case'") },
  async ({ type }) => {
    const { types } = await resolveTypes([type]);
    const r = await bridge.run({ cmd: "viewtype", selection: [types[0]], maxFieldLength: 2000 });
    const f = r.workItems[0]?.fields || {};
    const names = (v) => (Array.isArray(v) ? v.map((x) => (x && typeof x === "object" ? x.id : x)) : v);
    const out = {};
    for (const [k, v] of Object.entries(f)) {
      if (/^(image|permittedGroups|permittedAdministrators|notificationFields|created|createdBy|lastModified|modifiedBy)$/.test(k)) continue;
      if (/fields$/i.test(k) && k !== "mandatoryFields") out[k] = names(v);
      else out[k] = simplify(v);
    }
    return out;
  },
);

tool(
  "rvs_describe_field",
  "Describe a field: data type, allowed values/picks, relationship target, etc.",
  { field: z.string().describe("Field name, e.g. 'State' or 'Category'") },
  async ({ field: name }) => {
    const hits = fuzzy(name, await allFields());
    if (hits.length > 1) return { ambiguous: `'${name}' matches several fields; call again with one of them`, fields: hits.slice(0, 50) };
    const r = await bridge.run({ cmd: "viewfield", selection: [hits[0] || name], maxFieldLength: 2000 });
    return simplifyFields(r.workItems[0]?.fields, {});
  },
);

tool(
  "rvs_list_queries",
  "List saved queries visible to the current user (personal and shared).",
  { filter: z.string().optional().describe("Case-insensitive substring filter on query name") },
  async ({ filter }) => {
    const list = (await allQueries()).filter((x) => matchFilter(filter)(x.name)).map((x) => (x.owner ? `${x.name} (owner: ${x.owner})` : x.name));
    return { count: list.length, queries: list };
  },
);

// ---------- Filtering engine (shared by search / count / find) ----------

const scalar = z.union([z.string(), z.number(), z.boolean()]);
const whereValue = z.union([
  scalar,
  z.array(scalar),
  z.object({
    op: z.enum(["=", "!=", "contains", "notContains", ">", ">=", "<", "<=", "between", "empty", "notEmpty", "inLastDays"]),
    value: z.union([scalar, z.array(scalar)]).optional(),
  }),
]);

const PICK_TYPES = new Set(["pick", "ibpl", "state", "type", "user", "group", "project", "siproject", "fva"]);
const NUMERIC_TYPES = new Set(["integer", "float", "id"]);
const TEXT_TYPES = new Set(["shorttext", "longtext"]);
const addDays = (iso, n) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/** Turns one 'where' entry into an RV&S query clause, resolving the field name and pick values. */
async function whereClause(rawName, spec, types, notes) {
  const name = await resolveField(rawName, types);
  if (name !== rawName) notes.push(`field '${rawName}' -> ${name}`);
  const info = await fieldInfo(name).catch(() => ({ type: null, picks: [] }));
  const ftype = (info.type || "").toLowerCase();
  const pickLike = PICK_TYPES.has(ftype) || info.picks.length > 0;
  const isDate = ftype === "date";
  const { op: rawOp, value } = spec && typeof spec === "object" && !Array.isArray(spec) ? spec : { op: undefined, value: spec };
  const values = value === undefined ? [] : Array.isArray(value) ? value : [value];
  const op = rawOp || (pickLike || NUMERIC_TYPES.has(ftype) || ftype === "logical" ? "=" : isDate ? "between" : "contains");
  const f = field(name);
  const need = (n) => {
    if (values.length < n) throw new Error(`Field '${name}' with op '${op}' needs ${n === 1 ? "a value" : `${n} values`}.`);
  };

  // Relationship fields can't be used in RV&S query definitions; they are filtered client-side.
  if (ftype === "relationship") {
    if (!["empty", "notEmpty", "=", "contains", "!=", "notContains"].includes(op)) {
      throw new Error(`Relationship field '${name}' supports ops empty, notEmpty, = / contains (item IDs), != / notContains.`);
    }
    if (!["empty", "notEmpty"].includes(op)) need(1);
    return { post: { field: name, op, ids: values.map(String) } };
  }

  if (op === "empty") return `(${f} is empty)`;
  if (op === "notEmpty") return `(not (${f} is empty))`;
  if (isDate) {
    const iso = (v) => String(v).slice(0, 10);
    const span = (a, b) => `(${f} between ${fmtDate(a)} and ${fmtDate(b)})`;
    if (op === "inLastDays") return need(1), `(${f} in the last ${Number(values[0])} days)`;
    if (op === "between") return values.length === 1 ? span(iso(values[0]), iso(values[0])) : (need(2), span(iso(values[0]), iso(values[1])));
    need(1);
    const d = iso(values[0]);
    if (op === ">") return span(addDays(d, 1), "2099-12-31");
    if (op === ">=") return span(d, "2099-12-31");
    if (op === "<") return span("1970-01-01", addDays(d, -1));
    if (op === "<=") return span("1970-01-01", d);
    if (op === "=") return span(d, d);
    if (op === "!=") return `(not ${span(d, d)})`;
    throw new Error(`Operator '${op}' is not supported for date field '${name}'.`);
  }
  need(1);

  let resolved = values.map(String);
  if (ftype === "user" || ftype === "group") {
    if (ftype === "user") {
      resolved = [];
      for (const v of values) resolved.push(...(await resolveUsers(String(v)).then((r) => (notes.push(...r.notes), r.users))));
    }
  } else if (ftype === "state") {
    resolved = (await resolveStates(resolved, types).then((r) => (notes.push(...r.notes), r))).states;
  } else if (ftype === "type") {
    resolved = (await resolveTypes(resolved).then((r) => (notes.push(...r.notes), r))).types;
  } else if (info.picks.length && ["=", "!=", "contains", "notContains"].includes(op)) {
    resolved = [];
    for (const v of values) {
      const s = String(v);
      // IBPL labels look like 'Replicant B - (2837382)'; also accept the bare backing item ID.
      let hits = info.picks.filter((p) => p === s || p.endsWith(`(${s})`));
      if (!hits.length) hits = fuzzy(s, info.picks);
      if (!hits.length) {
        throw new Error(`'${s}' is not a valid value for '${name}'. Allowed: ${info.picks.slice(0, 60).join("; ")}${info.picks.length > 60 ? " …" : ""}`);
      }
      if (hits.length !== 1 || hits[0] !== s) notes.push(`${name} '${s}' -> ${hits.join("; ")}`);
      resolved.push(...hits);
    }
  }

  const eq = () => (pickLike ? `(${f} = ${resolved.map(q).join(",")})` : `(${resolved.map((v) => `(${f} = ${NUMERIC_TYPES.has(ftype) ? Number(v) : q(v)})`).join(" or ")})`);
  const contains = () => (pickLike ? eq() : `(${resolved.map((v) => `(${f} contains ${q(v)})`).join(" or ")})`);
  switch (op) {
    case "=":
      return eq();
    case "!=":
      return `(not ${eq()})`;
    case "contains":
      return contains();
    case "notContains":
      return `(not ${contains()})`;
    case ">":
    case ">=":
    case "<":
    case "<=":
      if (!NUMERIC_TYPES.has(ftype)) throw new Error(`Operator '${op}' needs a numeric or date field; '${name}' is ${ftype || "unknown"}.`);
      return `(${f} ${op} ${Number(values[0])})`;
    case "between":
      if (!NUMERIC_TYPES.has(ftype)) throw new Error(`'between' needs a numeric or date field; '${name}' is ${ftype || "unknown"}.`);
      need(2);
      return `((${f} >= ${Number(values[0])}) and (${f} <= ${Number(values[1])}))`;
    default:
      throw new Error(`Operator '${op}' is not supported for field '${name}' (${ftype || "unknown"}).`);
  }
}

const filterSchema = {
  types: z.array(z.string()).optional().describe("Item types; loose names are resolved, e.g. 'user story' -> 'ASD-User Story'"),
  projects: z
    .array(z.string())
    .optional()
    .describe("Projects: full paths ('/REPLICANT B') or loose names ('Replicant' matches every project segment starting with it)"),
  includeSubprojects: z.boolean().default(true).describe("Also match sub-projects of the given projects"),
  states: z.array(z.string()).optional().describe("Workflow states; resolved per type, e.g. 'Draft' -> 'ASD-Draft' for user stories"),
  text: z.string().optional().describe("Text to look for (case-insensitive 'contains') in textFields, e.g. a module tag like 'PRA'"),
  textMatch: z
    .enum(["phrase", "allWords", "anyWord"])
    .default("phrase")
    .describe("phrase = the whole text must appear; allWords = every word must appear (in any text field); anyWord = at least one word"),
  textFields: z.array(z.string()).default(["Summary", "Text", "Description"]).describe("Fields searched by 'text'"),
  caseSensitive: z
    .boolean()
    .default(false)
    .describe("RV&S matching is case-sensitive; by default common spellings are also tried (lower/UPPER/Title case, 'SDCard' <-> 'SD card')"),
  assignedUser: z.string().optional().describe("Assignee: login ID, full name or e-mail"),
  createdBy: z.string().optional().describe("Creator: login ID, full name or e-mail"),
  dateField: z.string().default("Modified Date").describe("Date field used by the date filters (e.g. 'Created Date')"),
  fromDate: z.string().optional().describe("Inclusive lower bound YYYY-MM-DD on dateField"),
  toDate: z.string().optional().describe("Inclusive upper bound YYYY-MM-DD on dateField"),
  inLastDays: z.number().int().min(1).optional().describe("Only items whose dateField is within the last N days"),
  queryDefinition: z.string().optional().describe("Additional raw RV&S query definition AND-ed with the other filters"),
  where: z
    .record(z.string(), whereValue)
    .optional()
    .describe(
      `Filter on any field (names and pick values resolved loosely). Value forms: "x" | ["x","y"] (any of) | {op, value}.
ops: = (default for picks/users/numbers), contains (default for text), != , notContains, >, >=, <, <=, between ([a,b]), empty, notEmpty, inLastDays (dates).
Examples: {"Product":"Replicant B"}, {"team":"Replicant Team"}, {"Priority":["High","Critical"]}, {"Relevant Explorative Test":{"op":"empty"}}, {"Created Date":{"op":">=","value":"2026-01-01"}}`,
    ),
};

/** Resolves loose names in the filter arguments into exact RV&S names. */
async function resolveFilter(a) {
  const notes = [];
  const r = {};
  if (a.types?.length) ({ types: r.types } = collect(await resolveTypes(a.types), notes));
  if (a.projects?.length) ({ projects: r.projects } = collect(await resolveProjects(a.projects, a.includeSubprojects), notes));
  if (a.states?.length) ({ states: r.states } = collect(await resolveStates(a.states, r.types || []), notes));
  if (a.assignedUser) r.assignedUsers = collect(await resolveUsers(a.assignedUser), notes).users;
  if (a.createdBy) r.createdBy = collect(await resolveUsers(a.createdBy), notes).users;
  if (a.where && Object.keys(a.where).length) {
    r.where = [];
    r.post = [];
    for (const [k, v] of Object.entries(a.where)) {
      const c = await whereClause(k, v, r.types || [], notes);
      if (typeof c === "string") r.where.push(c);
      else r.post.push(c.post);
    }
  }
  r.projects = r.projects?.filter((p) => !invalidProjects.has(p));
  return { r, notes };
}
function collect(res, notes) {
  notes.push(...res.notes);
  return res;
}

function buildDefinition(a, r) {
  const clauses = [];
  if (r.types) clauses.push(`(${field("Type")} = ${r.types.map(q).join(",")})`);
  if (r.projects) clauses.push(`(${r.projects.map((p) => `(${field("Project")} = ${q(p)})`).join(" or ")})`);
  if (r.states) clauses.push(`(${field("State")} = ${r.states.map(q).join(",")})`);
  if (a.text) {
    const forms = (t) => (a.caseSensitive ? [t] : textVariants(t));
    const anyField = (t) => `(${a.textFields.flatMap((f) => forms(t).map((v) => `(${field(f)} contains ${q(v)})`)).join(" or ")})`;
    const words = a.textMatch === "phrase" ? [a.text] : a.text.split(/\s+/).filter(Boolean);
    if (words.length < 2) clauses.push(anyField(a.text));
    else clauses.push(`(${words.map(anyField).join(a.textMatch === "allWords" ? " and " : " or ")})`);
  }
  if (r.assignedUsers) clauses.push(`(${field("Assigned User")} = ${r.assignedUsers.map(q).join(",")})`);
  if (r.createdBy) clauses.push(`(${field("Created By")} = ${r.createdBy.map(q).join(",")})`);
  if (a.inLastDays) clauses.push(`(${field(a.dateField)} in the last ${a.inLastDays} days)`);
  if (a.fromDate || a.toDate) {
    clauses.push(`(${field(a.dateField)} between ${fmtDate(a.fromDate || "1970-01-01")} and ${fmtDate(a.toDate || "2099-12-31")})`);
  }
  if (r.where) clauses.push(...r.where);
  if (a.queryDefinition) clauses.push(a.queryDefinition.trim().startsWith("(") ? a.queryDefinition : `(${a.queryDefinition})`);
  if (!clauses.length) throw new Error("Provide at least one filter (types, projects, states, text, …).");
  return `(${clauses.join(" and ")})`;
}

/** Runs 'im issues' for the filter, dropping inactive projects (listed by 'im projects' but rejected in queries). */
async function queryFiltered(a, opts) {
  // RV&S is very slow (10 s+) when one query ORs 'contains' over several long-text fields, but fast per field,
  // so search each text field in its own (parallel) query and merge the results.
  if (!a.text || !a.textFields || a.textFields.length < 2) return queryFilteredOnce(a, opts);
  const { limit, offset = 0, sort, maxScan = 20000 } = opts;
  const settled = await Promise.allSettled(
    a.textFields.map((f) => queryFilteredOnce({ ...a, textFields: [f] }, { ...opts, offset: 0, limit: offset + limit, maxScan })),
  );
  const ok = settled.filter((s) => s.status === "fulfilled").map((s) => s.value);
  if (!ok.length) throw settled[0].reason;
  const notes = [...new Set(ok.flatMap((x) => x.notes))];
  settled.forEach((s, i) => s.status === "rejected" && notes.push(`search in ${a.textFields[i]} failed: ${s.reason.message}`));
  if (a.textMatch === "allWords") notes.push(`all words must occur in the same field (${a.textFields.join(" or ")})`);
  const seen = new Set();
  const merged = [];
  for (const x of ok) for (const it of x.res.items) if (!seen.has(it.ID)) seen.add(it.ID), merged.push(it);
  const key = (it) => (sort?.field ? getField(it, sort.field) ?? "" : Number(it.ID) || 0);
  const dir = sort?.field && sort.ascending ? 1 : -1;
  merged.sort((x, y) => (key(x) > key(y) ? dir : key(x) < key(y) ? -dir : 0));
  const items = merged.slice(offset, offset + limit);
  const more = merged.length > offset + limit || ok.some((x) => x.res.hasMore);
  return {
    definition: ok.map((x) => x.definition).join("  |  "),
    notes,
    res: { count: items.length, offset, hasMore: more, ...(more ? { nextOffset: offset + items.length } : {}), items },
  };
}

async function queryFilteredOnce(a, { fields, limit, offset = 0, sort, maxScan = 20000 }) {
  const { r, notes } = await resolveFilter(a);
  const post = r.post || [];
  const fetchFields = [...new Set([...fields, ...post.map((p) => p.field)])];
  const options = [["fields", fetchFields.join(",")]];
  if (sort?.field) options.push(["sortField", sort.field], [sort.ascending ? "sortAscending" : "nosortAscending"]);
  for (let attempt = 0; ; attempt++) {
    if (r.projects && !r.projects.length) throw new Error(`None of the projects ${a.projects.join(", ")} is active/queryable.`);
    const definition = buildDefinition(a, r);
    try {
      if (!post.length) {
        const res = await runItems({ cmd: "issues", options: [["queryDefinition", definition], ...options] }, { offset, limit });
        return { definition, notes, res };
      }
      const scan = await runItems({ cmd: "issues", options: [["queryDefinition", definition], ...options] }, { limit: maxScan });
      const extra = fetchFields.filter((f) => !fields.includes(f));
      const kept = scan.items.filter((it) => post.every((p) => relationMatches(getField(it, p.field), p)));
      for (const it of kept) for (const f of extra) delete it[Object.keys(it).find((k) => k.toLowerCase() === f.toLowerCase())];
      const items = kept.slice(offset, offset + limit);
      const more = kept.length > offset + limit;
      notes.push(`relationship filter on ${post.map((p) => p.field).join(", ")} applied client-side to ${scan.items.length} items${scan.hasMore ? ` (scan capped at ${maxScan}; results may be incomplete)` : ""}`);
      return {
        definition,
        notes,
        res: { count: items.length, offset, hasMore: more || scan.hasMore, ...(more ? { nextOffset: offset + items.length } : {}), items },
      };
    } catch (err) {
      const bad = /The value ""(.+?)"" is not valid/.exec(err.message)?.[1];
      if (attempt < 50 && bad && r.projects?.includes(bad)) {
        invalidProjects.add(bad);
        r.projects = r.projects.filter((p) => p !== bad);
        continue;
      }
      throw err;
    }
  }
}

function relationMatches(value, p) {
  const ids = (Array.isArray(value) ? value : value === null || value === undefined || value === "" ? [] : [value]).map((v) => String(v).split(/\s/)[0]);
  if (p.op === "empty") return ids.length === 0;
  if (p.op === "notEmpty") return ids.length > 0;
  const hit = p.ids.some((id) => ids.includes(id));
  return p.op === "=" || p.op === "contains" ? hit : !hit;
}

function emptyHints(a) {
  const hints = [];
  if (a.text && a.textMatch === "phrase" && /\s/.test(a.text.trim())) hints.push("Try textMatch='allWords' or 'anyWord'.");
  if (a.text && a.textFields.length <= 3) hints.push("Text is only searched in " + a.textFields.join(", ") + "; add more textFields if needed.");
  if (a.states?.length) hints.push("Check valid states with rvs_list_states(type).");
  if (a.projects?.length && !a.includeSubprojects) hints.push("Set includeSubprojects=true.");
  hints.push("Use rvs_find with a keyword to see where matching items live (projects, types, states).");
  return hints;
}

const valueKey = (v) => {
  const s = simplify(v);
  if (s === null || s === undefined || s === "" || (Array.isArray(s) && !s.length)) return "(empty)";
  return Array.isArray(s) ? s.join(", ") : typeof s === "object" ? JSON.stringify(s) : String(s);
};
const getField = (fields, name) => {
  const key = Object.keys(fields || {}).find((k) => k.toLowerCase() === name.toLowerCase());
  return key ? fields[key] : null;
};
function tally(items, keyOf) {
  const m = new Map();
  for (const it of items) {
    const k = keyOf(it);
    m.set(k, (m.get(k) || 0) + 1);
  }
  return [...m.entries()].sort((x, y) => y[1] - x[1]).map(([value, count]) => ({ value, count }));
}

// ---------- Searching ----------

tool(
  "rvs_search_items",
  `Search items (user stories, requirements, specifications, test cases, test sessions, complaints, documents…) in any project with structured filters.
Filters are AND-ed. Names are resolved loosely: types ('user story'), states ('Draft' -> the type's own draft state), projects ('Replicant'),
users (full name or e-mail). The response lists any such resolutions under 'resolved'.
Sorted by the server default (newest ID first) unless sortField is given.
Tip: modules/components are often tagged in the Summary, e.g. text='(PRA)' with textFields=['Summary'].
For complex conditions pass a raw RV&S 'queryDefinition', e.g. (field["Category"] = "Requirement").`,
  {
    ...filterSchema,
    fields: fieldsSchema,
    sortField: z.string().optional().describe("Field to sort by, e.g. 'Modified Date'"),
    sortAscending: z.boolean().default(false),
    limit: limitSchema,
    offset: offsetSchema,
  },
  async (a) => {
    const { definition, notes, res } = await queryFiltered(a, {
      fields: a.fields?.length ? a.fields : DEFAULT_LIST_FIELDS,
      limit: a.limit,
      offset: a.offset,
      sort: { field: a.sortField, ascending: a.sortAscending },
    });
    return {
      ...(notes.length ? { resolved: notes } : {}),
      queryDefinition: definition,
      ...res,
      ...(!res.count && !a.offset ? { hints: emptyHints(a) } : {}),
    };
  },
);

tool(
  "rvs_count_items",
  `Count items matching the same filters as rvs_search_items, grouped by one or more fields (e.g. State, Type, Project, Assigned User, Product).
Use it for overviews such as "how many PRA user stories per state" or "open defects per assignee in Replicant".`,
  {
    ...filterSchema,
    groupBy: z.array(z.string()).min(1).max(3).default(["State"]).describe("Fields to group by"),
    maxScan: z.number().int().min(1).max(50000).default(10000).describe("Maximum number of items to scan"),
  },
  async (a) => {
    const { definition, notes, res } = await queryFiltered(a, { fields: ["ID", ...a.groupBy], limit: a.maxScan, maxScan: a.maxScan });
    const scanned = res.items.length;
    // runItems returns simplified records; re-read the raw value by case-insensitive field name.
    const groups = tally(res.items, (it) => a.groupBy.map((g) => valueKey(getField(it, g))).join(" | "));
    return {
      ...(notes.length ? { resolved: notes } : {}),
      queryDefinition: definition,
      total: res.hasMore ? `>= ${scanned} (scan limit reached; raise maxScan)` : scanned,
      groupBy: a.groupBy.join(" | "),
      groups,
    };
  },
);

tool(
  "rvs_find",
  `START HERE when you don't know where something lives. Looks up a keyword (or item IDs) across all of RV&S at once:
matching projects, item types, workflow states, fields, saved queries, users, and items whose Summary, Text or Description
contains the keyword (case and spelling variants included, e.g. 'SDCardCorrupted' also finds 'SdCardCorrupted' and 'SD card corrupted';
falls back to matching all words), with counts per type/project/state so you can narrow down with rvs_search_items.
Examples: 'PRA', 'Replicant', 'user story', 'calibration', 'SdCardCorrupted', 'jdoe', '3080491'.`,
  {
    query: z.string().min(1).describe("Keyword, name, or one or more item IDs (comma/space separated)"),
    types: z.array(z.string()).optional().describe("Restrict item matches to these types (loose names)"),
    projects: z.array(z.string()).optional().describe("Restrict item matches to these projects (loose names)"),
    limit: z.number().int().min(1).max(200).default(20).describe("Maximum number of items to list"),
  },
  async (a) => {
    const term = a.query.trim();
    if (/^\d+([\s,;]+\d+)*$/.test(term)) {
      const ids = term.split(/[\s,;]+/);
      const r = await bridge.run({ cmd: "issues", selection: ids, options: [["fields", DEFAULT_LIST_FIELDS.join(",")]] });
      return { kind: "item IDs", items: r.workItems.map((w) => simplifyItem(w)) };
    }
    const lower = term.toLowerCase();
    const has = (s) => s?.toLowerCase().includes(lower);
    const safe = (p) => p.catch((e) => ({ error: e.message }));

    // Catalog lookup and item searches all run in parallel.
    const scanFields = ["ID", "Type", "Summary", "State", "Project", "Modified Date"];
    const base = { types: a.types, projects: a.projects, includeSubprojects: true, textMatch: "phrase", dateField: "Modified Date", caseSensitive: false };
    const search = (textFields, extra = {}) =>
      queryFiltered({ ...base, text: term, textFields, ...extra }, { fields: scanFields, limit: 2000 }).catch((e) => ({ error: e.message }));
    const words = splitWords(term);
    const [catalogLists, inSummary, inBody] = await Promise.all([
      Promise.all([allProjects, allTypes, allStates, allFields, allQueries, allUsers].map((f) => safe(f()))),
      search(["Summary"]),
      search(["Text", "Description"]),
    ]);
    const [projects, types, states, fields, queries, users] = catalogLists;
    const topMost = (list) => list.filter((p) => !list.some((o) => o !== p && p.startsWith(`${o}/`)));
    const cap = (list, n) => (Array.isArray(list) ? (list.length > n ? [...list.slice(0, n), `… ${list.length - n} more`] : list) : list);
    const catalog = {
      projects: Array.isArray(projects) ? cap(topMost(projects.filter(has)), 30) : projects,
      itemTypes: Array.isArray(types) ? [...new Set([...types.filter(has), ...fuzzy(term, types)])] : types,
      states: Array.isArray(states) ? cap(states.filter(has), 30) : states,
      fields: Array.isArray(fields) ? cap(fields.filter(has), 30) : fields,
      savedQueries: Array.isArray(queries) ? cap(queries.filter((x) => has(x.name)).map((x) => (x.owner ? `${x.name} (owner: ${x.owner})` : x.name)), 30) : queries,
      users: Array.isArray(users)
        ? cap(users.filter((u) => has(u.id) || has(u.fullname) || has(u.email)).map(fmtUser), 15)
        : users,
    };
    for (const k of Object.keys(catalog)) if (Array.isArray(catalog[k]) && !catalog[k].length) delete catalog[k];

    // Summary hits first, then items that only mention the term in Text/Description.
    const ok = (r) => (r && !r.error ? r : null);
    let matchedIn = [];
    let items = [];
    let truncated = false;
    const seen = new Set();
    const add = (r, label) => {
      if (!ok(r) || !r.res.items.length) return;
      matchedIn.push(label);
      truncated ||= r.res.hasMore;
      for (const it of r.res.items) if (!seen.has(it.ID)) seen.add(it.ID), items.push(it);
    };
    add(inSummary, "Summary");
    add(inBody, "Text/Description");
    if (!items.length && words.length > 1) {
      // Fallback: every word somewhere in Summary/Text/Description (e.g. 'SD card corruption check').
      add(await search(["Summary", "Text", "Description"], { text: words.join(" "), textMatch: "allWords" }), `all words (${words.join(", ")})`);
    }
    const errors = [inSummary, inBody].filter((r) => r?.error).map((r) => r.error);
    const notes = ok(inSummary)?.notes || ok(inBody)?.notes || [];
    const top = (key) => cap(tally(items, (it) => valueKey(it[key])).map((g) => `${g.value}: ${g.count}`), 15);
    return {
      query: term,
      searchedSpellings: textVariants(term),
      ...(notes.length ? { resolved: notes } : {}),
      ...(errors.length ? { errors } : {}),
      ...(Object.keys(catalog).length ? { namesMatching: catalog } : {}),
      items: items.length
        ? {
            matchedIn: matchedIn.join(", "),
            total: truncated ? `>= ${items.length}` : items.length,
            byType: top("Type"),
            byProject: top("Project"),
            byState: top("State"),
            newest: items.slice(0, a.limit),
          }
        : `No items mention '${term}' in Summary/Text/Description${a.types || a.projects ? " within the given types/projects" : ""}.`,
    };
  },
);
tool(
  "rvs_run_query",
  "Run a saved RV&S query by name and return its items (using the query's own columns unless 'fields' is given).",
  {
    query: z.string().describe("Saved query name. Use 'user:query' to disambiguate by owner."),
    fields: fieldsSchema,
    limit: limitSchema,
    offset: offsetSchema,
  },
  async ({ query, fields, limit, offset }) => {
    if (!query.includes(":")) {
      const names = [...new Set((await allQueries()).map((x) => x.name))];
      if (!names.includes(query)) {
        const hits = fuzzy(query, names);
        if (hits.length === 1) query = hits[0];
        else if (hits.length > 1) throw new Error(`Query '${query}' is ambiguous: ${hits.slice(0, 20).join("; ")}`);
      }
    }
    let cols = fields;
    let definition;
    if (!cols?.length) {
      const name = query.includes(":") ? query.slice(query.indexOf(":") + 1) : query;
      try {
        const v = await bridge.run({ cmd: "viewquery", selection: [name] });
        const f = v.workItems[0]?.fields || {};
        cols = Array.isArray(f.fields) && f.fields.length ? f.fields : DEFAULT_LIST_FIELDS;
        definition = f.queryDefinition;
      } catch {
        cols = DEFAULT_LIST_FIELDS;
      }
    }
    const res = await runItems({ cmd: "issues", options: [["query", query], ["fields", cols.join(",")]] }, { offset, limit });
    return definition ? { queryDefinition: definition, ...res } : res;
  },
);

// ---------- Item details ----------

tool(
  "rvs_get_items",
  "Get full details of one or more items by ID (all fields, rich text converted to plain text, relationships summarised as 'ID (Type) Summary [State]'). Works for any item type: user stories, requirements, specifications, test cases, test steps, test sessions, test step results, documents…",
  {
    ids: z.array(z.union([z.string(), z.number()])).min(1).max(50).describe("Item IDs"),
    fields: z.array(z.string()).optional().describe("Only return these fields (default: all non-empty fields)"),
    includeEmpty: z.boolean().default(false).describe("Include fields with empty values"),
    includeHistory: z.boolean().default(false).describe("Include the change history"),
    includeAttachments: z.boolean().default(false).describe("Include attachment details"),
    richText: z.boolean().default(false).describe("Return rich-text fields as HTML instead of plain text"),
    asOf: z.string().optional().describe("Historical view: a date ('MMM d, yyyy h:mm:ss a') or 'label:<label>'"),
    maxFieldLength: z.number().int().min(100).default(20000).describe("Truncate long text fields to this many characters"),
  },
  async (a) => {
    const options = [];
    if (a.includeHistory) options.push(["showHistory"]);
    if (a.includeAttachments) options.push(["showAttachments"], ["showAttachmentDetails"]);
    if (a.richText) options.push(["showRichContent"]);
    if (a.asOf) options.push(["asOf", a.asOf]);
    const r = await bridge.run({ cmd: "viewissue", selection: a.ids.map(String), options, maxFieldLength: a.maxFieldLength });
    const only = a.fields?.length ? new Set(a.fields.map((f) => f.toLowerCase()).concat(["id", "type"])) : undefined;
    const items = r.workItems.map((w) => {
      const rec = simplifyItem(w, { includeEmpty: a.includeEmpty, richText: a.richText, only });
      if (rec.MKSIssueHistory) {
        rec.History = rec.MKSIssueHistory;
        delete rec.MKSIssueHistory;
      }
      return rec;
    });
    return items.length === 1 ? items[0] : { count: items.length, items };
  },
);

tool(
  "rvs_get_relationships",
  "Traverse relationships of items (e.g. requirement -> specifications -> test cases -> test steps). Returns each reached item with its relationship fields listing related IDs.",
  {
    ids: z.array(z.union([z.string(), z.number()])).min(1).max(50),
    relationshipFields: z.array(z.string()).optional().describe("Relationship fields to expand, e.g. ['Validated By','Test Steps'] (default: all structural relationships)"),
    direction: z.enum(["forward", "backward", "both"]).optional(),
    expandLevel: z.number().int().min(1).max(10).default(1).describe("Traversal depth"),
    fields: z.array(z.string()).optional().describe("Extra fields to show for each item, e.g. ['ID','Type','Summary','State']"),
    limit: z.number().int().min(1).max(2000).default(200),
  },
  async (a) => {
    const options = [["expandLevel", String(a.expandLevel)]];
    if (a.relationshipFields?.length) options.push(["expandRelationshipFields", a.relationshipFields.join(",")]);
    if (a.direction) options.push(["expandRelationshipDirection", a.direction]);
    if (a.fields?.length) options.push(["fields", a.fields.join(",")]);
    return runItems({ cmd: "relationships", selection: a.ids.map(String), options }, { limit: a.limit });
  },
);

// ---------- Documents ----------

tool(
  "rvs_get_document",
  `Read a document (Requirement Document, Specification Document, Test Design Document, …) by its document (segment) ID, in document order with section numbers.
Returns Markdown by default. Large documents are paged with offset/limit.`,
  {
    documentId: z.union([z.string(), z.number()]).describe("ID of the document (segment) item"),
    format: z.enum(["markdown", "json"]).default("markdown"),
    fields: z.array(z.string()).optional().describe("Extra fields to include per node (e.g. ['Validated By','Priority'])"),
    filterQueryDefinition: z.string().optional().describe("Only show nodes matching this query definition, e.g. (field[\"Category\"] = \"Requirement\")"),
    includeSubDocuments: z.boolean().default(false).describe("Include content of included documents"),
    asOf: z.string().optional().describe("Historical view: date ('MMM d, yyyy h:mm:ss a') or 'label:<label>' (baseline)"),
    limit: z.number().int().min(1).max(2000).default(300).describe("Maximum number of nodes"),
    offset: offsetSchema,
    maxFieldLength: z.number().int().min(100).default(10000),
  },
  async (a) => {
    const base = ["ID", "Type", "Summary", "Category", "Section", "State", "Text", "Contains"];
    const extra = (a.fields || []).filter((f) => !base.some((b) => b.toLowerCase() === f.toLowerCase()));
    const options = [["fields", [...base, ...extra].join(",")]];
    if (a.filterQueryDefinition) options.push(["filterQueryDefinition", a.filterQueryDefinition], ["showParentage"]);
    if (a.includeSubDocuments) options.push(["recurseInclude"]);
    if (a.asOf) options.push(["asOf", a.asOf]);
    const r = await bridge.run({
      cmd: "viewsegment",
      selection: [String(a.documentId)],
      options,
      limit: a.offset + a.limit,
      maxFieldLength: a.maxFieldLength,
    });

    // Derive nesting depth from the Contains relationships (items arrive in document order).
    const depth = new Map();
    const nodes = r.workItems.map((w) => {
      const f = simplifyFields(w.fields, { includeEmpty: true });
      const d = depth.get(String(f.ID)) ?? 0;
      for (const child of f.Contains || []) depth.set(String(child), d + 1);
      delete f.Contains;
      return { depth: d, ...f };
    });
    const page = nodes.slice(a.offset);
    const meta = { documentId: String(a.documentId), offset: a.offset, count: page.length, hasMore: r.truncated, ...(r.truncated ? { nextOffset: a.offset + page.length } : {}) };
    if (a.format === "json") return { ...meta, nodes: page };

    const md = [];
    for (const n of page) {
      const extras = extra
        .map((k) => [Object.keys(n).find((x) => x.toLowerCase() === k.toLowerCase()), k])
        .filter(([key]) => key && n[key] !== null && !(Array.isArray(n[key]) && !n[key].length))
        .map(([key]) => `- ${key}: ${Array.isArray(n[key]) ? n[key].join("; ") : n[key]}`);
      if (n.depth === 0 && !n.Section) {
        md.push(`# ${n.Summary || n.Text || "Document"} (ID ${n.ID}, ${n.Type}${n.State ? `, ${n.State}` : ""})`, ...extras, "");
      } else if (/heading/i.test(n.Category || "")) {
        md.push(`${"#".repeat(Math.min(n.depth + 1, 6))} ${n.Section ? `${n.Section} ` : ""}${n.Text || n.Summary || ""} [ID ${n.ID}]`, ...extras, "");
      } else {
        md.push(`**${n.Section ? `${n.Section} ` : ""}[${n.Category || n.Type} ${n.ID}${n.State ? `, ${n.State}` : ""}]**`, n.Text || n.Summary || "", ...extras, "");
      }
    }
    if (r.truncated) md.push(`_…document continues. Call again with offset=${meta.nextOffset} to read more._`);
    return `<!-- ${JSON.stringify(meta)} -->\n${md.join("\n")}`;
  },
);

// ---------- Escape hatch ----------

tool(
  "rvs_run_command",
  `Run an arbitrary READ-ONLY Windchill RV&S API command (same as the 'im'/'tm' CLI) for anything not covered by the other tools.
Allowed: im ${[...READ_ONLY.im].join(", ")}; tm ${[...READ_ONLY.tm].join(", ")}.
Example: app='im', command='users', options={}; app='im', command='viewissue', selection=['123'], options={showTestResults:true}.`,
  {
    app: z.enum(["im", "tm"]).default("im"),
    command: z.string(),
    options: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])).default({}).describe("Command options without leading dashes; true = flag, false = --no<flag>"),
    selection: z.array(z.string()).default([]).describe("Operands, e.g. item IDs"),
    limit: z.number().int().min(1).max(2000).default(200),
    raw: z.boolean().default(false).describe("Return raw API structures instead of simplified records"),
  },
  async (a) => {
    if (!READ_ONLY[a.app].has(a.command)) throw new Error(`Command '${a.app} ${a.command}' is not in the read-only allow-list.`);
    const options = Object.entries(a.options).map(([k, v]) => (v === true ? [k] : v === false ? [`no${k}`] : [k, String(v)]));
    const r = await bridge.run({ app: a.app, cmd: a.command, options, selection: a.selection, limit: a.limit, maxFieldLength: 20000 });
    if (a.raw) return r;
    return {
      count: r.workItems.length,
      hasMore: r.truncated,
      ...(r.result ? { result: r.result } : {}),
      items: r.workItems.map((w) => ({ _id: w.id, _model: w.modelType, ...(w.context ? { _context: w.context } : {}), ...simplifyItem(w) })),
    };
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
process.stderr.write(`windchill MCP server ready (client: ${config.clientHome}${config.hostname ? `, server: ${config.hostname}:${config.port}` : ""})\n`);

// Warm up the Java bridge and name catalogs in the background so the first question is answered quickly.
if (process.env.RVS_PREWARM !== "0") {
  Promise.allSettled([allProjects, allTypes, allStates, allFields, allQueries, allUsers].map((f) => f())).then((r) => {
    const failed = r.filter((x) => x.status === "rejected").length;
    process.stderr.write(`windchill catalogs pre-loaded${failed ? ` (${failed} failed, will retry on demand)` : ""}\n`);
  });
}

const shutdown = () => {
  bridge.stop();
  process.exit(0);
};
process.stdin.on("close", shutdown);
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
