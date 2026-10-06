import { EventEmitter } from 'node:events'
import { createConnection, type Socket } from 'node:net'
import { join } from 'node:path'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { BlockReader, Opt, decodeBlock, encodeBlock } from '@tb/hw/main/cop/cop'
import {
  CopFaultError, CopFaultInjector, FaultyCopLink, copMessageMatches, validateCopFaults, type CopFaultEvent, type CopLinkLike,
} from '../src/copFaults'
import { Environment, copNamer, presetFor } from '../src/environment'
import { IcolCatalog, IcolCodec, testbenchInfo } from '../src/testbench'

// Fault injection between appSMM and the hardware twin (plan 4.2). Framework self-test, not product evidence.

class FakeLink extends EventEmitter implements CopLinkLike {
  readonly socket = { destroyed: false } as Socket
  readonly sent: Uint8Array[] = []
  send(payload: Uint8Array): void {
    this.sent.push(payload)
  }
}

// [name byte] -> name; byte 1 = 0 marks an event as on the wire.
const NAMES: Record<number, string> = { 1: 'AppMan.InitializeCmd', 2: 'AppMan.InitializeRsp', 3: 'AppMan.DeInitializeRsp', 4: 'AppMan.GlobalStateChangeErr' }
const namer = (payload: Uint8Array) => NAMES[payload[0]]
const msg = (id: number) => Uint8Array.from([id, 1, 0, 0, 0])

function linkWith(rules: unknown) {
  const raw = new FakeLink()
  const injector = new CopFaultInjector(validateCopFaults(rules))
  const events: CopFaultEvent[] = []
  const faulty = new FaultyCopLink(raw, () => injector, namer, (e) => events.push(e))
  const toTwin: Uint8Array[] = []
  faulty.on('message', (p: Uint8Array) => toTwin.push(p))
  return { raw, faulty, injector, events, toTwin }
}

describe('COP fault rules', () => {
  it('matches names with and without module, Cmd by default', () => {
    expect(copMessageMatches('AppMan.DeInitializeRsp', 'DeInitializeRsp')).toBe(true)
    expect(copMessageMatches('AppMan.DeInitializeRsp', 'AppMan.DeInitializeRsp')).toBe(true)
    expect(copMessageMatches('AppMan.InitializeCmd', 'Initialize')).toBe(true)
    expect(copMessageMatches('AppMan.DeInitializeCmd', 'Initialize')).toBe(false)
    expect(copMessageMatches('AppMan.InitializeRsp', 'Initialize')).toBe(false)
  })

  it('rejects malformed rules', () => {
    const bad = (rules: unknown) => () => validateCopFaults(rules)
    expect(validateCopFaults(undefined)).toEqual([])
    expect(bad({})).toThrow(CopFaultError)
    expect(bad([1])).toThrow(/must be an object/)
    expect(bad([{ message: 'X', action: 'drop', foo: 1 }])).toThrow(/unknown key/)
    expect(bad([{ message: '*', action: 'drop' }])).toThrow(/name a COP message/)
    expect(bad([{ message: 'X', action: 'explode' }])).toThrow(/action must be/)
    expect(bad([{ message: 'X', action: 'delay' }])).toThrow(/delay needs ms/)
    expect(bad([{ message: 'X', action: 'delay', ms: -1 }])).toThrow(/non-negative/)
    expect(bad([{ message: 'InitializeCmd', action: 'error' }])).toThrow(/responses/)
    expect(bad([{ message: 'InitializeRsp', action: 'error', code: 0 }])).toThrow(/1\.\.255/)
  })

  it('applies skip and count per rule and keeps statistics', () => {
    const inj = new CopFaultInjector([{ message: 'DeInitializeRsp', action: 'drop', skip: 1, count: 1 }])
    expect(inj.decide('AppMan.DeInitializeRsp', 'reply')).toBeUndefined()
    expect(inj.decide('AppMan.DeInitializeRsp', 'reply')?.action).toBe('drop')
    expect(inj.decide('AppMan.DeInitializeRsp', 'reply')).toBeUndefined()
    expect(inj.decide('AppMan.DeInitializeRsp', 'command')).toBeUndefined()
    expect(inj.decide(undefined, 'reply')).toBeUndefined()
    expect(inj.stats()).toEqual([{ message: 'DeInitializeRsp', action: 'drop', matched: 3, applied: 1 }])
  })

  it('passes unmatched traffic through in both directions', () => {
    const { raw, faulty, toTwin, events } = linkWith([{ message: 'DeInitializeRsp', action: 'drop' }])
    raw.emit('message', msg(1))
    faulty.send(msg(2))
    expect(toTwin).toEqual([msg(1)])
    expect(raw.sent).toEqual([msg(2)])
    expect(events).toEqual([])
  })

  it('drops, corrupts and delays replies', () => {
    vi.useFakeTimers()
    try {
      const { raw, faulty, events } = linkWith([
        { message: 'DeInitializeRsp', action: 'drop' },
        { message: 'InitializeRsp', action: 'error', code: 7 },
        { message: 'GlobalStateChangeErr', action: 'delay', ms: 1500 },
      ])
      faulty.send(msg(3))
      faulty.send(msg(2))
      faulty.send(msg(4))
      expect(raw.sent.map((p) => [...p])).toEqual([[2, 1, 0, 0, 7]])
      vi.advanceTimersByTime(1499)
      expect(raw.sent).toHaveLength(1)
      vi.advanceTimersByTime(1)
      expect(raw.sent[1]).toEqual(msg(4))
      expect(events.map((e) => `${e.direction} ${e.action} ${e.name}`)).toEqual([
        'reply drop AppMan.DeInitializeRsp', 'reply error AppMan.InitializeRsp', 'reply delay AppMan.GlobalStateChangeErr',
      ])
    } finally {
      vi.useRealTimers()
    }
  })

  it('drops and delays commands, and cancels pending deliveries on close', () => {
    vi.useFakeTimers()
    try {
      const dropped = linkWith([{ message: 'Initialize', action: 'drop' }])
      dropped.raw.emit('message', msg(1))
      expect(dropped.toTwin).toEqual([])
      expect(dropped.events[0]).toMatchObject({ direction: 'command', action: 'drop', name: 'AppMan.InitializeCmd' })

      const delayed = linkWith([{ message: 'InitializeCmd', action: 'delay', ms: 200 }])
      const closed = vi.fn()
      delayed.faulty.on('close', closed)
      delayed.raw.emit('message', msg(1))
      vi.advanceTimersByTime(200)
      expect(delayed.toTwin).toEqual([msg(1)])
      delayed.raw.emit('message', msg(1))
      delayed.raw.emit('close')
      vi.advanceTimersByTime(1000)
      expect(delayed.toTwin).toHaveLength(1)
      expect(closed).toHaveBeenCalledOnce()
    } finally {
      vi.useRealTimers()
    }
  })

  it('names real ICoL messages like the twin trace does', () => {
    const catalog = IcolCatalog.fromDirectory(join(testbenchInfo.dir, 'hwsim', 'resources', 'icol'))
    const codec = new IcolCodec(catalog)
    const name = copNamer(codec)
    expect(name(codec.encode(catalog.message('7251_AppMan', 'DeInitializeCmd')), 'command')).toBe('AppMan.DeInitializeCmd')
    expect(name(codec.encode(catalog.message('7251_AppMan', 'DeInitializeRsp')), 'reply')).toBe('AppMan.DeInitializeRsp')
    expect(name(Uint8Array.from([0xee, 0xee, 0, 0]), 'command')).toBeUndefined()
  })
})

// ---- end to end: a raw COP client stands in for appSMM in front of the real hardware twin

const HW_PORT = 17791
const BROKER_PORT = 18851

class CopClient {
  private readonly reader = new BlockReader()
  private seq = 0
  readonly received: { time: number; payload: Uint8Array }[] = []
  private constructor(private readonly socket: Socket) {
    socket.on('data', (chunk) => {
      for (const block of this.reader.push(chunk)) {
        const p = decodeBlock(block)
        if (p.payload.length) this.received.push({ time: Date.now(), payload: p.payload })
      }
    })
  }

  static async connect(): Promise<CopClient> {
    const socket = createConnection(HW_PORT, '127.0.0.1')
    await new Promise<void>((resolve, reject) => socket.once('connect', resolve).once('error', reject))
    const client = new CopClient(socket)
    socket.write(encodeBlock({ seq: 0, options: Opt.ResetSequenceIdsRxTx | Opt.SlidingWindowAcknowledgement | Opt.PacketComplete, payload: new Uint8Array() }))
    return client
  }

  send(payload: Uint8Array): number {
    this.socket.write(encodeBlock({ seq: ++this.seq, options: Opt.PacketComplete | Opt.ApplicationMode, payload }))
    return Date.now()
  }

  async waitFor(id: number, timeoutMs = 3000): Promise<{ time: number; payload: Uint8Array } | undefined> {
    const end = Date.now() + timeoutMs
    while (Date.now() < end) {
      const hit = this.received.find((r) => r.payload[1] === id)
      if (hit) return hit
      await new Promise((resolve) => setTimeout(resolve, 20))
    }
    return undefined
  }

  close(): void {
    this.socket.destroy()
  }
}

describe('COP faults through the hardware twin', () => {
  const env = new Environment(join(testbenchInfo.dir, 'hwsim'))
  const catalog = IcolCatalog.fromDirectory(join(testbenchInfo.dir, 'hwsim', 'resources', 'icol'))
  const codec = new IcolCodec(catalog)
  const cmd = (name: string) => codec.encode(catalog.message('7251_AppMan', name))
  const GLOBAL_STATE = catalog.message('7251_AppMan', 'GetGlobalStateCmd').id
  let client: CopClient

  beforeAll(async () => {
    await env.start(presetFor('mock', { hardware: 'twin', broker: { kind: 'embedded', host: '127.0.0.1', port: BROKER_PORT }, hw: { ...presetFor('offline').hw, host: '127.0.0.1', port: HW_PORT } }))
    client = await CopClient.connect()
  })

  afterAll(async () => {
    client?.close()
    await env.stop()
  })

  it('answers normally without rules', async () => {
    expect(env.hardwareFaultStatus()).toEqual({ faults: [], stats: [] })
    client.send(cmd('GetGlobalStateCmd'))
    const reply = await client.waitFor(GLOBAL_STATE)
    expect(reply?.payload[4]).toBe(0)
    client.received.length = 0
  })

  it('delays and corrupts the twin reply, and drops a command before the twin', async () => {
    env.setHardwareFaults([{ message: 'GetGlobalStateRsp', action: 'delay', ms: 600 }])
    const sent = client.send(cmd('GetGlobalStateCmd'))
    const delayed = await client.waitFor(GLOBAL_STATE)
    expect(delayed!.time - sent).toBeGreaterThanOrEqual(550)
    client.received.length = 0

    env.setHardwareFaults([{ message: 'GetGlobalStateRsp', action: 'error', code: 9 }])
    client.send(cmd('GetGlobalStateCmd'))
    expect((await client.waitFor(GLOBAL_STATE))?.payload[4]).toBe(9)
    client.received.length = 0

    const mark = env.getTrace().at(-1)?.id ?? 0
    env.setHardwareFaults([{ message: 'GetGlobalState', action: 'drop' }])
    client.send(cmd('GetGlobalStateCmd'))
    expect(await client.waitFor(GLOBAL_STATE, 500)).toBeUndefined()
    const dropped = env.getTrace(mark).find((t) => t.name === 'AppMan.GetGlobalStateCmd')
    expect(dropped).toMatchObject({ way: 'rx', error: 'dropped by a COP fault rule' })
    expect(env.hardwareFaultStatus().stats).toEqual([{ message: 'GetGlobalState', action: 'drop', matched: 1, applied: 1 }])
    expect(env.status().hardware).toMatchObject({ copFaults: 1 })

    env.setHardwareFaults([])
    client.send(cmd('GetGlobalStateCmd'))
    expect(await client.waitFor(GLOBAL_STATE)).toBeDefined()
  })
})
