/**
 * Fault injection for the mock appSMM (mutation testing of the suites, plan 3.1).
 *
 * A fault rule changes what the mock publishes for one message name. The first rule whose
 * ``message`` and ``when`` match an outgoing message decides what happens to it; ``skip`` and
 * ``count`` limit which matches are affected (counted per mock appSMM start). A mutant is a set of
 * rules; a good suite fails when the mock misbehaves this way ("the mutant is killed").
 */
export const FAULT_ACTIONS = ['drop', 'duplicate', 'delay', 'reorder', 'wrongTopic', 'set', 'unset', 'invalid'] as const
export type FaultAction = (typeof FAULT_ACTIONS)[number]

export interface MockFault {
  /** Message name, e.g. ``InitializationResponse``; ``*`` matches every message. */
  message: string
  action: FaultAction
  /** Only messages whose body has these values (dot paths, e.g. ``{"CurrentState": "Idle"}``). */
  when?: Record<string, unknown>
  /** delay: how long to hold the message; reorder: longest wait for a following message (default 2000). */
  ms?: number
  /** wrongTopic: topic to publish on instead of /is/iw/tx (default /is/hca1/tx). */
  topic?: string
  /** set: body fields to overwrite (dot paths). */
  fields?: Record<string, unknown>
  /** unset: body fields to remove (dot paths). */
  remove?: string[]
  /** Leave the first ``skip`` matches alone. */
  skip?: number
  /** Affect at most ``count`` matches (default: all). */
  count?: number
}

/** One MQTT publication the mock makes for an outgoing message. */
export interface Delivery {
  topic: string
  payload: string
  delayMs: number
  /** reorder: publish after the next message (or after ``delayMs`` if none follows). */
  hold?: boolean
}

export class FaultError extends Error {}

/** Body field added by ``invalid``: the ICD schemas forbid unknown properties. */
export const INVALID_FIELD = 'MockFaultUnknownField'
export const DEFAULT_WRONG_TOPIC = '/is/hca1/tx'
const DEFAULT_REORDER_MS = 2000

export function validateFaults(input: unknown): MockFault[] {
  if (input === undefined || input === null) return []
  if (!Array.isArray(input)) throw new FaultError('faults must be a list of fault rules')
  return input.map((raw, i) => {
    const where = `faults[${i}]`
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw new FaultError(`${where} must be an object`)
    const f = raw as Record<string, unknown>
    const known = new Set(['message', 'action', 'when', 'ms', 'topic', 'fields', 'remove', 'skip', 'count'])
    const unknown = Object.keys(f).filter((k) => !known.has(k))
    if (unknown.length) throw new FaultError(`${where}: unknown key(s) ${unknown.join(', ')}`)
    if (typeof f.message !== 'string' || !f.message) throw new FaultError(`${where}.message is required`)
    if (!FAULT_ACTIONS.includes(f.action as FaultAction)) throw new FaultError(`${where}.action must be one of ${FAULT_ACTIONS.join(', ')}`)
    for (const key of ['ms', 'skip', 'count'] as const) {
      if (f[key] !== undefined && (typeof f[key] !== 'number' || !Number.isInteger(f[key]) || (f[key] as number) < 0)) {
        throw new FaultError(`${where}.${key} must be a non-negative integer`)
      }
    }
    for (const key of ['when', 'fields'] as const) {
      if (f[key] !== undefined && (typeof f[key] !== 'object' || f[key] === null || Array.isArray(f[key]))) throw new FaultError(`${where}.${key} must be an object`)
    }
    if (f.topic !== undefined && (typeof f.topic !== 'string' || !f.topic)) throw new FaultError(`${where}.topic must be a topic name`)
    if (f.remove !== undefined && (!Array.isArray(f.remove) || !f.remove.every((p) => typeof p === 'string' && p))) {
      throw new FaultError(`${where}.remove must be a list of field paths`)
    }
    if (f.action === 'delay' && f.ms === undefined) throw new FaultError(`${where}: delay needs ms`)
    if (f.action === 'set' && !f.fields) throw new FaultError(`${where}: set needs fields`)
    if (f.action === 'unset' && !(f.remove as string[] | undefined)?.length) throw new FaultError(`${where}: unset needs remove`)
    return f as unknown as MockFault
  })
}

/** Applies fault rules to outgoing messages; one instance per mock appSMM start. */
export class FaultInjector {
  private readonly matched: number[]
  private readonly applied: number[]

  constructor(readonly faults: MockFault[] = []) {
    this.matched = faults.map(() => 0)
    this.applied = faults.map(() => 0)
  }

  /** How often each rule matched and was applied since this mock appSMM started. */
  stats(): { fault: MockFault; matched: number; applied: number }[] {
    return this.faults.map((fault, i) => ({ fault, matched: this.matched[i], applied: this.applied[i] }))
  }

  deliveries(topic: string, name: string, body: unknown, version = 7): Delivery[] {
    const encode = (b: unknown, t = topic): Delivery => ({ topic: t, payload: JSON.stringify({ Version: version, [name]: b }), delayMs: 0 })
    const i = this.faults.findIndex((f) => (f.message === '*' || f.message === name) && matches(body, f.when))
    if (i < 0) return [encode(body)]
    const fault = this.faults[i]
    this.matched[i]++
    const n = this.matched[i]
    if (n <= (fault.skip ?? 0) || (fault.count !== undefined && this.applied[i] >= fault.count)) return [encode(body)]
    this.applied[i]++
    switch (fault.action) {
      case 'drop':
        return []
      case 'duplicate':
        return [encode(body), encode(body)]
      case 'delay':
        return [{ ...encode(body), delayMs: fault.ms ?? 0 }]
      case 'reorder':
        return [{ ...encode(body), delayMs: fault.ms ?? DEFAULT_REORDER_MS, hold: true }]
      case 'wrongTopic':
        return [encode(body, fault.topic ?? DEFAULT_WRONG_TOPIC)]
      case 'set': {
        const copy = clone(body)
        for (const [path, value] of Object.entries(fault.fields ?? {})) setPath(copy, path, value)
        return [encode(copy)]
      }
      case 'unset': {
        const copy = clone(body)
        for (const path of fault.remove ?? []) unsetPath(copy, path)
        return [encode(copy)]
      }
      case 'invalid':
        return [encode({ ...(clone(body) as object), [INVALID_FIELD]: true })]
    }
  }
}

function matches(body: unknown, when?: Record<string, unknown>): boolean {
  if (!when) return true
  return Object.entries(when).every(([path, value]) => JSON.stringify(getPath(body, path)) === JSON.stringify(value))
}

function clone<T>(value: T): T {
  return value === undefined ? value : (JSON.parse(JSON.stringify(value)) as T)
}

function getPath(obj: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((o, k) => (o !== null && typeof o === 'object' ? (o as Record<string, unknown>)[k] : undefined), obj)
}

function setPath(obj: unknown, path: string, value: unknown): void {
  const keys = path.split('.')
  let o = obj as Record<string, unknown>
  for (const k of keys.slice(0, -1)) {
    if (typeof o[k] !== 'object' || o[k] === null) o[k] = {}
    o = o[k] as Record<string, unknown>
  }
  o[keys[keys.length - 1]] = value
}

function unsetPath(obj: unknown, path: string): void {
  const keys = path.split('.')
  const target = keys.length === 1 ? obj : getPath(obj, keys.slice(0, -1).join('.'))
  if (target !== null && typeof target === 'object') delete (target as Record<string, unknown>)[keys[keys.length - 1]]
}
