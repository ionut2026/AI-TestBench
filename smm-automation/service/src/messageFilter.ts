import type { TimelineEntry } from './testbench'

/**
 * Selects timeline entries. Every given criterion must hold.
 *   way:   'rx' = received from the broker (published by appSMM), 'tx' = sent by this service
 *   match: partial match on the message body (the object under the message name), see matches()
 */
export interface MessageFilter {
  name?: string | string[]
  way?: 'rx' | 'tx'
  topic?: string
  analyzer?: number
  match?: Record<string, unknown>
  /** Only entries after this timeline id. */
  since?: number
  /** Only schema-valid (true) or schema-invalid (false) entries. */
  valid?: boolean
}

const OPERATORS = new Set(['$eq', '$ne', '$in', '$nin', '$regex', '$exists', '$gt', '$gte', '$lt', '$lte', '$contains', '$size'])

const isPlainObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isOperatorObject = (v: unknown): v is Record<string, unknown> =>
  isPlainObject(v) && Object.keys(v).length > 0 && Object.keys(v).every((k) => OPERATORS.has(k))

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => deepEqual(x, b[i]))
  if (isPlainObject(a) && isPlainObject(b)) {
    const ka = Object.keys(a)
    return ka.length === Object.keys(b).length && ka.every((k) => deepEqual(a[k], b[k]))
  }
  return false
}

/**
 * Partial structural match of `actual` against `expected`:
 *   - plain object: every listed key must match (other keys are ignored);
 *   - array: same length, element-wise match;
 *   - operator object: {$eq, $ne, $in, $nin, $regex, $exists, $gt, $gte, $lt, $lte, $contains, $size};
 *   - anything else: strict equality.
 */
export function matches(actual: unknown, expected: unknown): boolean {
  if (isOperatorObject(expected)) return Object.entries(expected).every(([op, arg]) => applyOperator(op, actual, arg))
  if (isPlainObject(expected)) {
    if (!isPlainObject(actual)) return false
    return Object.entries(expected).every(([key, value]) => {
      if (isOperatorObject(value) && '$exists' in value && !(key in actual)) return value.$exists === false
      return matches(actual[key], value)
    })
  }
  if (Array.isArray(expected)) {
    return Array.isArray(actual) && actual.length === expected.length && expected.every((e, i) => matches(actual[i], e))
  }
  return actual === expected
}

function applyOperator(op: string, actual: unknown, arg: unknown): boolean {
  switch (op) {
    case '$eq':
      return deepEqual(actual, arg)
    case '$ne':
      return !deepEqual(actual, arg)
    case '$in':
      return Array.isArray(arg) && arg.some((a) => deepEqual(actual, a))
    case '$nin':
      return Array.isArray(arg) && !arg.some((a) => deepEqual(actual, a))
    case '$regex':
      return typeof actual === 'string' && new RegExp(String(arg)).test(actual)
    case '$exists':
      return (actual !== undefined) === Boolean(arg)
    case '$gt':
      return typeof actual === 'number' && actual > Number(arg)
    case '$gte':
      return typeof actual === 'number' && actual >= Number(arg)
    case '$lt':
      return typeof actual === 'number' && actual < Number(arg)
    case '$lte':
      return typeof actual === 'number' && actual <= Number(arg)
    case '$contains':
      return Array.isArray(actual) && actual.some((item) => matches(item, arg))
    case '$size':
      return Array.isArray(actual) && actual.length === Number(arg)
    default:
      return false
  }
}

/** The message body: the object under the message name in the ICD envelope. */
export function bodyOf(entry: TimelineEntry): unknown {
  if (!entry.name || !isPlainObject(entry.payload)) return undefined
  return entry.payload[entry.name]
}

export function entryMatches(entry: TimelineEntry, filter: MessageFilter): boolean {
  if (filter.since !== undefined && entry.id <= filter.since) return false
  if (filter.way && entry.way !== filter.way) return false
  if (filter.topic && entry.topic !== filter.topic) return false
  if (filter.analyzer !== undefined && entry.analyzer !== filter.analyzer) return false
  if (filter.name !== undefined) {
    const names = Array.isArray(filter.name) ? filter.name : [filter.name]
    if (!entry.name || !names.includes(entry.name)) return false
  }
  if (filter.valid !== undefined && (entry.validation?.valid ?? false) !== filter.valid) return false
  if (filter.match && !matches(bodyOf(entry), filter.match)) return false
  return true
}

export function describeFilter(filter: MessageFilter): string {
  const parts: string[] = []
  if (filter.name) parts.push(Array.isArray(filter.name) ? filter.name.join('|') : filter.name)
  if (filter.way) parts.push(filter.way === 'rx' ? 'from appSMM' : 'sent by Bridge')
  if (filter.topic) parts.push(`on ${filter.topic}`)
  if (filter.analyzer !== undefined) parts.push(`analyzer ${filter.analyzer}`)
  if (filter.match) parts.push(`matching ${JSON.stringify(filter.match)}`)
  if (filter.since !== undefined) parts.push(`after #${filter.since}`)
  return parts.join(' ') || 'any message'
}

/** Validates a filter coming over HTTP. Returns an error text or undefined. */
export function checkFilter(raw: unknown): string | undefined {
  if (!isPlainObject(raw)) return 'filter must be an object'
  const allowed = new Set(['name', 'way', 'topic', 'analyzer', 'match', 'since', 'valid'])
  const unknown = Object.keys(raw).filter((k) => !allowed.has(k))
  if (unknown.length) return `unknown filter field(s): ${unknown.join(', ')}`
  if (raw.way !== undefined && raw.way !== 'rx' && raw.way !== 'tx') return "way must be 'rx' or 'tx'"
  if (raw.match !== undefined && !isPlainObject(raw.match)) return 'match must be an object'
  return undefined
}
