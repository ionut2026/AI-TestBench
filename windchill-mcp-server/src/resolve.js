import { bridge } from "./bridge.js";

const TTL = 30 * 60 * 1000;

/** Caches the result of an async loader for TTL ms (concurrent callers share one request). */
function cached(loader) {
  let entry = { at: 0, value: null, pending: null };
  return async () => {
    if (entry.value && Date.now() - entry.at < TTL) return entry.value;
    if (!entry.pending) {
      entry.pending = loader()
        .then((value) => {
          entry = { at: Date.now(), value, pending: null };
          return value;
        })
        .catch((err) => {
          entry.pending = null;
          throw err;
        });
    }
    return entry.pending;
  };
}

export const allProjects = cached(async () => (await bridge.run({ cmd: "projects" })).workItems.map((w) => w.id));
export const allTypes = cached(async () => (await bridge.run({ cmd: "types" })).workItems.map((w) => w.id));
export const allStates = cached(async () => (await bridge.run({ cmd: "states" })).workItems.map((w) => w.id));
export const allFieldTypes = cached(async () => {
  const r = await bridge.run({ cmd: "fields", options: [["fields", "name,type"]] });
  return new Map(r.workItems.map((w) => [w.id, w.fields?.type || null]));
});
export const allFields = async () => [...(await allFieldTypes()).keys()];
export const allQueries = cached(async () =>
  (await bridge.run({ cmd: "queries" })).workItems.map((w) => ({ name: w.id, owner: w.context || null })),
);
export const allUsers = cached(async () =>
  (await bridge.run({ cmd: "users", options: [["fields", "name,fullname,email"]] })).workItems.map((w) => ({
    id: w.id,
    fullname: w.fields?.fullname || null,
    email: w.fields?.email || null,
  })),
);

const typeInfoCache = new Map();
/** Workflow states and visible fields of an item type. */
async function typeInfo(type) {
  const hit = typeInfoCache.get(type);
  if (hit && Date.now() - hit.at < TTL) return hit;
  const r = await bridge.run({ cmd: "viewtype", selection: [type] });
  const f = r.workItems[0]?.fields || {};
  const states = new Set();
  for (const s of f.stateTransitions || []) {
    if (s?.id) states.add(s.id);
    for (const t of s?.fields?.targetStates || []) if (t?.id) states.add(t.id);
  }
  states.delete("Unspecified");
  const ids = (v) => (Array.isArray(v) ? v.map((x) => (x && typeof x === "object" ? x.id : x)).filter(Boolean) : []);
  const info = { at: Date.now(), states: [...states], fields: [...ids(f.visibleFields), ...ids(f.systemManagedFields)] };
  typeInfoCache.set(type, info);
  return info;
}
export const typeStates = async (type) => (await typeInfo(type)).states;

const fieldInfoCache = new Map();
/** Field type, multi-valued flag and allowed values (labels) for pick-like fields. */
export async function fieldInfo(name) {
  const hit = fieldInfoCache.get(name);
  if (hit && Date.now() - hit.at < TTL) return hit;
  const r = await bridge.run({ cmd: "viewfield", selection: [name] });
  const f = r.workItems[0]?.fields || {};
  const picks = Array.isArray(f.picks) ? f.picks.map((p) => ({ label: p?.fields?.label ?? p?.id, active: p?.fields?.active !== false })) : [];
  const info = { at: Date.now(), name, type: f.type || null, multi: !!f.isMultiValued, picks: picks.filter((p) => p.label).map((p) => p.label) };
  fieldInfoCache.set(name, info);
  return info;
}

/** Resolves a loose field name ('team' -> 'ASD-Team'), preferring fields visible on the given types. */
export async function resolveField(name, types = []) {
  const names = await allFields();
  let hits = [];
  if (types.length) {
    const visible = [...new Set((await Promise.all(types.map((t) => typeInfo(t).catch(() => ({ fields: [] }))))).flatMap((i) => i.fields))];
    hits = fuzzy(name, visible);
  }
  if (!hits.length) hits = fuzzy(name, names);
  if (!hits.length) {
    const s = suggest(name, names, 20);
    throw new Error(`Unknown field '${name}'.${s.length ? ` Similar: ${s.join(", ")}.` : ""} Use rvs_describe_type to list a type's fields.`);
  }
  if (hits.length > 1) throw new Error(`Field '${name}' is ambiguous: ${hits.slice(0, 25).join(", ")}${hits.length > 25 ? " …" : ""}. Use the exact name.`);
  return hits[0];
}

/** Lower-cases, singularises each word and strips punctuation, so 'User Stories' ~ 'ASD-User Story'. */
export const norm = (s) =>
  String(s)
    .toLowerCase()
    .split(/[^a-z0-9&]+/)
    .filter(Boolean)
    .map((w) => (w.length > 3 && w.endsWith("ies") ? `${w.slice(0, -3)}y` : w.length > 3 && /[^s]s$/.test(w) ? w.slice(0, -1) : w))
    .join("");

/**
 * Finds the best candidates for a user-supplied name: exact (case-insensitive) > normalised equal >
 * normalised suffix (e.g. 'Draft' -> 'ASD-Draft') > normalised substring. Returns all matches of the best tier.
 */
export function fuzzy(name, candidates) {
  const lower = String(name).toLowerCase().trim();
  const exact = candidates.filter((c) => c.toLowerCase() === lower);
  if (exact.length) return exact;
  const n = norm(name);
  if (!n) return [];
  for (const test of [(c) => c === n, (c) => c.endsWith(n), (c) => c.startsWith(n), (c) => c.includes(n)]) {
    const hits = candidates.filter((c) => test(norm(c)));
    if (hits.length) return hits;
  }
  return [];
}

/** Candidates that share at least one word with the name (for "did you mean" messages). */
export function suggest(name, candidates, max = 15) {
  const words = String(name).toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 1);
  return candidates.filter((c) => words.some((w) => c.toLowerCase().includes(w))).slice(0, max);
}

/** Resolves item type names. Returns { types, notes }. */
export async function resolveTypes(names) {
  const types = await allTypes();
  const out = new Set();
  const notes = [];
  for (const name of names) {
    const hits = fuzzy(name, types);
    if (!hits.length) {
      const s = suggest(name, types);
      throw new Error(`Unknown item type '${name}'.${s.length ? ` Did you mean: ${s.join(", ")}?` : ""} Use rvs_list_item_types.`);
    }
    if (hits.length !== 1 || hits[0] !== name) notes.push(`type '${name}' -> ${hits.join(", ")}`);
    hits.forEach((h) => out.add(h));
  }
  return { types: [...out], notes };
}

/**
 * Resolves workflow state names. When types are given, each name is resolved against every type's own
 * workflow (so 'Draft' becomes 'ASD-Draft' for ASD-User Story but stays 'Draft' for types that use 'Draft').
 */
export async function resolveStates(names, types = []) {
  const out = new Set();
  const notes = [];
  const pools = types.length ? await Promise.all(types.map(async (t) => [t, await typeStates(t).catch(() => [])])) : [];
  const globalStates = await allStates();
  for (const name of names) {
    const hits = new Set();
    for (const [, states] of pools) fuzzy(name, states).forEach((h) => hits.add(h));
    if (!hits.size) fuzzy(name, globalStates).forEach((h) => hits.add(h));
    if (!hits.size) {
      const pool = pools.length ? [...new Set(pools.flatMap(([, s]) => s))] : globalStates;
      throw new Error(
        `Unknown state '${name}'${types.length ? ` for ${types.join(", ")}` : ""}. Valid states: ${(pools.length ? pool : suggest(name, pool, 30)).join(", ") || "(see rvs_list_states)"}`,
      );
    }
    const list = [...hits];
    if (list.length !== 1 || list[0] !== name) notes.push(`state '${name}' -> ${list.join(", ")}`);
    list.forEach((h) => out.add(h));
  }
  return { states: [...out], notes };
}

/**
 * Resolves project names/paths to concrete project paths. Accepts full paths ('/REPLICANT B'), paths without
 * the leading slash, or any path segment / part of it ('Replicant' -> '/REPLICANT A', '/REPLICANT B', …).
 */
export async function resolveProjects(names, includeSubprojects = true) {
  const projects = await allProjects();
  const roots = new Set();
  const notes = [];
  for (const raw of names) {
    const path = (raw.startsWith("/") ? raw : `/${raw}`).replace(/\/+$/, "").toLowerCase();
    let hits = projects.filter((p) => p.toLowerCase() === path);
    if (!hits.length && path.lastIndexOf("/") > 0) hits = projects.filter((p) => p.toLowerCase().endsWith(path));
    if (!hits.length) {
      const n = norm(raw);
      const word = raw.toLowerCase().trim();
      const segMatch = (test) => projects.filter((p) => p.split("/").filter(Boolean).some(test));
      // 'Replicant' should match 'Replicant', 'REPLICANT A' and 'Replicant IWS' alike (whole-word prefix).
      hits = segMatch((seg) => {
        const s = seg.toLowerCase();
        return s === word || (s.startsWith(word) && /[^a-z0-9]/.test(s[word.length]));
      });
      if (!hits.length) hits = segMatch((seg) => norm(seg).startsWith(n));
      if (!hits.length) hits = segMatch((seg) => norm(seg).includes(n));
      // Keep only the top-most matches; sub-projects are added below.
      hits = hits.filter((h) => !hits.some((o) => o !== h && h.toLowerCase().startsWith(`${o.toLowerCase()}/`)));
    }
    if (!hits.length) {
      const s = suggest(raw, projects);
      throw new Error(`No project matches '${raw}'.${s.length ? ` Similar: ${s.join(", ")}.` : ""} Use rvs_list_projects or rvs_find.`);
    }
    if (hits.length !== 1 || hits[0] !== raw) notes.push(`project '${raw}' -> ${hits.join(", ")}`);
    hits.forEach((h) => roots.add(h.toLowerCase()));
  }
  const list = projects.filter((p) => {
    const lp = p.toLowerCase();
    return [...roots].some((r) => lp === r || (includeSubprojects && lp.startsWith(`${r}/`)));
  });
  return { projects: list, notes };
}

/** Resolves a user by login, full name or e-mail. */
export async function resolveUsers(name) {
  const users = await allUsers();
  const lower = name.toLowerCase().trim();
  let hits = users.filter((u) => u.id.toLowerCase() === lower || u.email?.toLowerCase() === lower);
  if (!hits.length) hits = users.filter((u) => u.fullname?.toLowerCase() === lower);
  if (!hits.length) {
    const words = lower.split(/[\s,.]+/).filter(Boolean);
    hits = users.filter((u) => {
      const hay = `${u.id} ${u.fullname || ""} ${u.email || ""}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
  }
  if (!hits.length) throw new Error(`No user matches '${name}'. Use rvs_list_users.`);
  if (hits.length > 10) throw new Error(`'${name}' matches ${hits.length} users, be more specific: ${hits.slice(0, 10).map(fmtUser).join("; ")} …`);
  const ids = hits.map((u) => u.id);
  return { users: ids, notes: ids.length !== 1 || ids[0] !== name ? [`user '${name}' -> ${hits.map(fmtUser).join("; ")}`] : [] };
}

export const fmtUser = (u) => (u.fullname ? `${u.fullname} (${u.id})` : u.id);

/** Splits 'SDCardCorrupted' / 'sd_card-corrupted' / 'SD card corrupted' into words: ['SD','Card','Corrupted']. */
export function splitWords(term) {
  return String(term)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .split(/[\s_\-.]+/)
    .filter(Boolean);
}

/**
 * RV&S 'contains' is case-sensitive and has no wildcards, so a term is searched as a set of common spellings:
 * as typed, lower/UPPER/Title/Sentence case, and for multi-word or camelCase terms the spaced and joined forms
 * (e.g. 'SDCardCorrupted' -> 'SdCardCorrupted', 'SD card corrupted', 'sd card corrupted', …).
 */
export function textVariants(term, max = 14) {
  const t = String(term).trim();
  if (!t) return [];
  const out = new Set([t]);
  const cap = (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
  const keepAcronym = (w) => (/^[A-Z0-9]{2,5}$/.test(w) ? w : w.toLowerCase());
  const caseForms = (s) => [s.toLowerCase(), s.toUpperCase(), s.split(" ").map(cap).join(" "), cap(s.split(" ")[0]) + s.slice(s.split(" ")[0].length).toLowerCase()];
  caseForms(t).forEach((v) => out.add(v));
  const words = splitWords(t);
  if (words.length > 1) {
    const acr = words.map(keepAcronym);
    const spaced = [
      words.join(" "),
      acr.join(" "),
      [acr[0] === acr[0].toLowerCase() ? cap(acr[0]) : acr[0], ...acr.slice(1)].join(" "),
      words.map(cap).join(" "),
      words.join(" ").toLowerCase(),
    ];
    const joined = [words.join(""), words.map(cap).join(""), words.join("").toLowerCase(), words.join("").toUpperCase()];
    [...joined, ...spaced].forEach((v) => out.add(v));
  }
  return [...out].slice(0, max);
}
