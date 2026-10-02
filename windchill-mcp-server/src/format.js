const ENTITIES = { nbsp: " ", amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", bull: "•", hellip: "…", ndash: "–", mdash: "—" };

/** Converts RV&S rich text (HTML prefixed with "<!-- MKS HTML -->") into readable plain text. */
export function htmlToText(html) {
  let s = String(html).replace(/<!--[\s\S]*?-->/g, "");
  s = s
    .replace(/<img\b[^>]*>/gi, (tag) => {
      const name = /attachmentname=([^"&]+)/i.exec(tag)?.[1] || /alt="([^"]*)"/i.exec(tag)?.[1];
      return `[image${name ? `: ${decodeURIComponent(name)}` : ""}]`;
    })
    .replace(/<a\b[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/gi, (_, href, text) => (text && text !== href ? `${text} (${href})` : href))
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<li\b[^>]*>/gi, "\n- ")
    .replace(/<\/(p|div|h[1-6]|ul|ol|table|tr)>/gi, "\n")
    .replace(/<\/t[dh]>/gi, " | ")
    .replace(/<[^>]+>/g, "")
    .replace(/&(#x?[0-9a-f]+|\w+);/gi, (m, e) => {
      if (e[0] === "#") return String.fromCodePoint(e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
      return ENTITIES[e.toLowerCase()] ?? m;
    })
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n");
  return s.trim();
}

const isEmpty = (v) => v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0);

/** Flattens RV&S API values (users, computations, relationships, picks…) into compact JSON. */
export function simplify(value, opts = {}) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return !opts.richText && value.startsWith("<!-- MKS HTML -->") ? htmlToText(value) : value;
  if (Array.isArray(value)) return value.map((v) => simplify(v, opts)).filter((v) => v !== undefined);
  if (typeof value !== "object") return value;

  const { id, modelType = "", fields } = value;
  if (!fields) return id;
  if (modelType === "im.User") return fields.fullname && fields.fullname !== id ? `${fields.fullname} (${id})` : id;
  if (modelType === "im.Group") return id;
  if (modelType === "im.Image") return undefined;
  if (modelType === "im.Computation") {
    // Prefer the display string (keeps units such as "0.0 hrs"); dates keep their ISO value.
    if (typeof fields.value === "string" || fields.value === null || fields.value === undefined) return simplify(fields.value ?? (id || null), opts);
    return id && id !== String(fields.value) ? id : fields.value;
  }
  if (modelType === "im.IBPL") return fields.IssueID && !String(id).includes(`(${fields.IssueID})`) ? `${id} (ID ${fields.IssueID})` : id;
  if (modelType.startsWith("im.Issue.Relationship")) {
    const f = fields;
    return [f.ID ?? id, f.Type ? `(${f.Type})` : "", f.Summary || "", f.State ? `[${f.State}]` : ""].filter(Boolean).join(" ");
  }
  return { id, ...simplifyFields(fields, { ...opts, only: undefined, includeEmpty: true }) };
}

export function simplifyFields(fields, opts = {}) {
  const out = {};
  for (const [k, v] of Object.entries(fields || {})) {
    if (opts.only && !opts.only.has(k.toLowerCase())) continue;
    const s = simplify(v, opts);
    if (s === undefined) continue;
    if (!opts.includeEmpty && isEmpty(s)) continue;
    out[k] = s;
  }
  return out;
}

/** Converts a bridge work item to a compact record. */
export function simplifyItem(wi, opts = {}) {
  if (wi.error) return { error: wi.error };
  const rec = simplifyFields(wi.fields, opts);
  if (wi.subRoutines?.length) rec._subRoutines = wi.subRoutines.map((s) => ({ routine: s.routine, items: s.workItems.map((w) => simplifyItem(w, opts)) }));
  return rec;
}

export const toText = (obj) => JSON.stringify(obj, null, 1);
