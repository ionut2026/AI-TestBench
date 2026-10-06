/**
 * Fault injection on the COP link between appSMM and the hardware twin (plan 4.2).
 *
 * The twin is attached to a {@link FaultyCopLink} instead of the real link. Rules name a COP message as
 * the trace shows it (``InitializeCmd`` = appSMM -> hardware, ``DeInitializeRsp`` / ``...Err`` =
 * hardware -> appSMM, optionally with the module: ``AppMan.DeInitializeRsp``). The first matching rule
 * applies; ``skip``/``count`` are counted from the last environment start or rule change.
 *
 * - ``drop``  the message never arrives (a dropped command is still recorded in the trace, marked);
 * - ``delay`` it arrives ``ms`` later;
 * - ``error`` responses only: the reply carries error code ``code`` (default 1) instead of 0.
 *
 * Nothing in the SMM TestBench changes: the proxy only sits between the TestBench's CopLink and twin.
 */
import { EventEmitter } from 'node:events'
import type { Socket } from 'node:net'

export const COP_FAULT_ACTIONS = ['drop', 'delay', 'error'] as const
export type CopFaultAction = (typeof COP_FAULT_ACTIONS)[number]

export interface CopFault {
  message: string
  action: CopFaultAction
  ms?: number
  code?: number
  skip?: number
  count?: number
}

export class CopFaultError extends Error {}

/** The part of the TestBench's CopLink the twin uses. */
export interface CopLinkLike extends EventEmitter {
  readonly socket: Socket
  send(payload: Uint8Array, lowPriority?: boolean): void
}

/** Names a raw ICoL message like the twin's trace does (``AppMan.InitializeCmd``); undefined if unknown. */
export type CopNamer = (payload: Uint8Array, direction: 'command' | 'reply') => string | undefined

/** ``AppMan.DeInitializeRsp`` matches ``DeInitializeRsp`` and ``AppMan.DeInitializeRsp``; ``Initialize`` means ``InitializeCmd``. */
export function copMessageMatches(name: string, wanted: string): boolean {
  const full = /(Cmd|Rsp|Err)$/.test(wanted) ? wanted : `${wanted}Cmd`
  return name === full || name.endsWith(`.${full}`)
}

const isReplyName = (message: string) => /(Rsp|Err)$/.test(message)

export function validateCopFaults(input: unknown): CopFault[] {
  if (input === undefined || input === null) return []
  if (!Array.isArray(input)) throw new CopFaultError('faults must be a list of fault rules')
  return input.map((raw, i) => {
    const where = `faults[${i}]`
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw new CopFaultError(`${where} must be an object`)
    const f = raw as Record<string, unknown>
    const unknown = Object.keys(f).filter((k) => !['message', 'action', 'ms', 'code', 'skip', 'count'].includes(k))
    if (unknown.length) throw new CopFaultError(`${where}: unknown key(s) ${unknown.join(', ')}`)
    if (typeof f.message !== 'string' || !f.message || f.message === '*') throw new CopFaultError(`${where}.message must name a COP message`)
    if (!COP_FAULT_ACTIONS.includes(f.action as CopFaultAction)) throw new CopFaultError(`${where}.action must be one of ${COP_FAULT_ACTIONS.join(', ')}`)
    for (const key of ['ms', 'skip', 'count'] as const) {
      if (f[key] !== undefined && (typeof f[key] !== 'number' || !Number.isInteger(f[key]) || (f[key] as number) < 0)) {
        throw new CopFaultError(`${where}.${key} must be a non-negative integer`)
      }
    }
    if (f.code !== undefined && (typeof f.code !== 'number' || !Number.isInteger(f.code) || f.code < 1 || f.code > 255)) {
      throw new CopFaultError(`${where}.code must be an integer 1..255`)
    }
    if (f.action === 'delay' && f.ms === undefined) throw new CopFaultError(`${where}: delay needs ms`)
    if (f.action === 'error' && !f.message.endsWith('Rsp')) throw new CopFaultError(`${where}: error applies to responses (...Rsp) only`)
    return f as unknown as CopFault
  })
}

export interface CopFaultStat {
  message: string
  action: CopFaultAction
  matched: number
  applied: number
}

export interface CopFaultEvent {
  direction: 'command' | 'reply'
  name: string
  action: CopFaultAction
  payload: Uint8Array
}

/** Decides per message what happens; shared by all links of one environment so counts survive reconnects. */
export class CopFaultInjector {
  private matched: number[]
  private applied: number[]

  constructor(readonly rules: CopFault[] = []) {
    this.matched = rules.map(() => 0)
    this.applied = rules.map(() => 0)
  }

  /** The rule to apply to this message, if any. */
  decide(name: string | undefined, direction: 'command' | 'reply'): CopFault | undefined {
    if (!name) return undefined
    const index = this.rules.findIndex((r) => (direction === 'reply') === isReplyName(r.message) && copMessageMatches(name, r.message))
    if (index < 0) return undefined
    const rule = this.rules[index]
    const n = this.matched[index]++
    if (n < (rule.skip ?? 0)) return undefined
    if (rule.count !== undefined && this.applied[index] >= rule.count) return undefined
    this.applied[index]++
    return rule
  }

  stats(): CopFaultStat[] {
    return this.rules.map((r, i) => ({ message: r.message, action: r.action, matched: this.matched[i], applied: this.applied[i] }))
  }
}

/** Stands in for a CopLink towards the twin and applies the injector's rules in both directions. */
export class FaultyCopLink extends EventEmitter {
  private readonly timers = new Set<NodeJS.Timeout>()

  constructor(
    private readonly link: CopLinkLike,
    private readonly injector: () => CopFaultInjector,
    private readonly namer: CopNamer,
    private readonly onFault: (event: CopFaultEvent) => void = () => undefined,
  ) {
    super()
    link.on('message', (payload: Uint8Array) => this.fromAppSmm(payload))
    link.on('warning', (text: string) => this.emit('warning', text))
    link.on('close', () => {
      this.cancel()
      this.emit('close')
    })
  }

  get socket(): Socket {
    return this.link.socket
  }

  /** Twin -> appSMM. */
  send(payload: Uint8Array, lowPriority = false): void {
    const name = this.namer(payload, 'reply')
    const rule = this.injector().decide(name, 'reply')
    if (!rule) return this.link.send(payload, lowPriority)
    this.onFault({ direction: 'reply', name: name!, action: rule.action, payload })
    if (rule.action === 'drop') return
    if (rule.action === 'error') {
      const changed = Uint8Array.from(payload)
      changed[4] = rule.code ?? 1
      return this.link.send(changed, lowPriority)
    }
    this.later(rule.ms ?? 0, () => this.link.send(payload, lowPriority))
  }

  private fromAppSmm(payload: Uint8Array): void {
    const name = this.namer(payload, 'command')
    const rule = this.injector().decide(name, 'command')
    if (!rule) return void this.emit('message', payload)
    this.onFault({ direction: 'command', name: name!, action: rule.action, payload })
    if (rule.action === 'delay') this.later(rule.ms ?? 0, () => this.emit('message', payload))
  }

  private later(ms: number, action: () => void): void {
    const timer = setTimeout(() => {
      this.timers.delete(timer)
      if (!this.link.socket.destroyed) action()
    }, ms)
    this.timers.add(timer)
  }

  cancel(): void {
    for (const timer of this.timers) clearTimeout(timer)
    this.timers.clear()
  }
}
