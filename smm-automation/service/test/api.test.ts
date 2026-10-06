import type { AddressInfo } from 'node:net'
import type { Server } from 'node:http'
import { join } from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { API_VERSION, AutomationService } from '../src/api/server'
import { BridgeSession } from '../src/bridgeSession'
import { CommandSentError, CommandTimeoutError, Environment, commandMatches, presetFor } from '../src/environment'
import { testbenchInfo } from '../src/testbench'

// Contract test of the HTTP API (what the Robot library relies on), against the mock tier.
// The mock appSMM is a framework fixture: these tests check the service, not the product.

const BROKER_PORT = 18831
let service: AutomationService
let server: Server
let base: string

async function call(method: string, path: string, body?: unknown): Promise<{ status: number; data: any }> {
  const res = await fetch(`${base}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return { status: res.status, data: await res.json() }
}

beforeAll(async () => {
  service = new AutomationService()
  server = await service.listen(0, '127.0.0.1')
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/v1`
  const started = await call('POST', '/environment/start', {
    tier: 'mock',
    overrides: { broker: { port: BROKER_PORT }, mock: { powerUpMs: 300, initializingMs: 300, clearingMs: 200, deinitMs: 100 } },
  })
  expect(started.status).toBe(200)
})

afterAll(async () => {
  await service?.shutdown()
  await new Promise((resolve) => server?.close(resolve))
})

describe('service API v1', () => {
  it('reports its version and the pinned TestBench', async () => {
    const { status, data } = await call('GET', '/health')
    expect(status).toBe(200)
    expect(data.apiVersion).toBe(API_VERSION)
    expect(data.icdVersion).toBe(7)
    expect(data.testbench.pinnedCommit).toMatch(/^[0-9a-f]{40}$/)
  })

  it('serves ICD schemas and rejects unknown ones', async () => {
    expect((await call('GET', '/schemas/SystemStatusNotification')).status).toBe(200)
    expect((await call('GET', '/schemas/NoSuchMessage')).status).toBe(404)
  })

  it('validates messages against the ICD', async () => {
    const ok = await call('POST', '/messages/validate', { name: 'InitializationResponse', body: { Status: 'OK' } })
    expect(ok.data.valid).toBe(true)
    const bad = await call('POST', '/messages/validate', { name: 'InitializationResponse', body: { Status: 'Maybe' } })
    expect(bad.data.valid).toBe(false)
    expect(bad.data.errors.length).toBeGreaterThan(0)
  })

  it('refuses to send while disconnected', async () => {
    const res = await call('POST', '/messages', { name: 'SystemStatusRequest', body: {} })
    expect(res.status).toBe(409)
  })

  it('connects as the Bridge and announces Bridge, IW and analyzers', async () => {
    const res = await call('POST', '/session/connect', {})
    expect(res.status).toBe(200)
    expect(res.data.link).toBe('connected')
    const seq = await call('POST', '/timeline/sequence', {
      timeoutMs: 3000,
      filters: [
        { name: 'ConnectionNotification', way: 'tx', match: { Source: 'SMMBridge', Status: 'Connected' } },
        { name: 'ConnectionNotification', way: 'tx', match: { Source: 'IW', Status: 'Connected' } },
      ],
    })
    expect(seq.status).toBe(200)
  })

  it('waits for a message and returns it', async () => {
    const res = await call('POST', '/timeline/wait', {
      filter: { name: 'SystemStatusNotification', match: { CurrentState: 'NotInitialized' } },
      timeoutMs: 3000,
    })
    expect(res.status).toBe(200)
    expect(res.data.body).toEqual({ PreviousState: 'PowerOn', CurrentState: 'NotInitialized' })
    expect(res.data.valid).toBe(true)
  })

  it('sends a request and matches the ordered answer sequence after a mark', async () => {
    const { data: m } = await call('POST', '/timeline/mark')
    const sent = await call('POST', '/messages', { name: 'InitializationRequest', body: {}, strict: true })
    expect(sent.status).toBe(200)
    expect(sent.data.topic).toBe('/is/iw/rx')
    const seq = await call('POST', '/timeline/sequence', {
      since: m.mark,
      timeoutMs: 5000,
      filters: [
        { name: 'InitializationResponse', match: { Status: 'OK' } },
        { name: 'SystemStatusNotification', match: { PreviousState: 'NotInitialized', CurrentState: 'Initializing' } },
        { name: 'SystemStatusNotification', match: { PreviousState: 'Clearing', CurrentState: 'Idle' } },
      ],
    })
    expect(seq.status).toBe(200)
    expect(seq.data).toHaveLength(3)
  })

  it('times out with 408 and diagnostic context', async () => {
    const res = await call('POST', '/timeline/wait', { filter: { name: 'NeverSent' }, timeoutMs: 200 })
    expect(res.status).toBe(408)
    expect(res.data.kind).toBe('timeout')
    expect(res.data.details.recentMessages.length).toBeGreaterThan(0)
  })

  it('reports an unexpected message with 409', async () => {
    const { data: m } = await call('POST', '/timeline/mark')
    await call('POST', '/messages', { name: 'InitializationRequest', body: {} })
    const res = await call('POST', '/timeline/expect-none', {
      filter: { name: 'InitializationResponse', match: { Status: 'OK' }, since: m.mark },
      durationMs: 500,
    })
    expect(res.status).toBe(200)
    const err = await call('POST', '/timeline/expect-none', { filter: { name: 'InitializationResponse', since: m.mark }, durationMs: 500 })
    expect(err.status).toBe(409)
    expect(err.data.details.entry.body).toEqual({ Status: 'Error' })
  })

  it('rejects malformed requests with 400', async () => {
    expect((await call('POST', '/timeline/wait', { filter: { nmae: 'x' } })).status).toBe(400)
    expect((await call('POST', '/timeline/sequence', { filters: [] })).status).toBe(400)
    expect((await call('POST', '/messages', { body: {} })).status).toBe(400)
    expect((await call('POST', '/messages', { name: 'InitializationResponse', body: { Status: 1 }, strict: true })).status).toBe(400)
    expect((await call('GET', '/nope')).status).toBe(404)
  })

  it('records raw (invalid) payloads as schema-invalid', async () => {
    const res = await call('POST', '/messages/raw', { topic: '/is/iw/rx', raw: '{"Version":7,"SystemStatusRequest":{"Bogus":1}}' })
    expect(res.status).toBe(200)
    expect(res.data.valid).toBe(false)
  })

  it('emulates a lost Bridge through the broker last will', async () => {
    const { data: m } = await call('POST', '/timeline/mark')
    expect((await call('POST', '/session/disconnect', { abrupt: true })).status).toBe(200)
    await new Promise((r) => setTimeout(r, 300))
    const env = await call('GET', '/environment')
    expect(env.data.appSmm.mockState).toBe('E-Stop')
    await call('POST', '/session/connect', { clear: false })
    const ev = await call('POST', '/timeline/wait', {
      filter: { name: 'EventNotification', since: m.mark, match: { EventId: 15859714, Severity: 'Warning', Category: 'Operator' } },
      timeoutMs: 3000,
    })
    expect(ev.status).toBe(200)
  })

  it('restarts the broker and the session reconnects', async () => {
    const res = await call('POST', '/environment/restart-broker', { downMs: 300 })
    expect(res.status).toBe(200)
    const deadline = Date.now() + 8000
    let link = ''
    while (Date.now() < deadline) {
      link = (await call('GET', '/session')).data.link
      if (link === 'connected') break
      await new Promise((r) => setTimeout(r, 200))
    }
    expect(link).toBe('connected')
  })

  it('answers SetConfigurationRequest outside NotInitialized with Error for every key', async () => {
    const { data: m } = await call('POST', '/timeline/mark')
    await call('POST', '/messages', { name: 'SetConfigurationRequest', body: { 'STI.Barcode.Code128': 'Enabled' } })
    const res = await call('POST', '/timeline/wait', { filter: { name: 'SetConfigurationResponse', since: m.mark }, timeoutMs: 3000 })
    expect(res.status).toBe(200)
    expect(res.data.body).toEqual({ 'STI.Barcode.Code128': 'Error' })
  })

  it('has no hardware twin in the mock tier', async () => {
    expect((await call('GET', '/hardware')).status).toBe(409)
    const wait = await call('POST', '/hardware/trace/wait', { command: 'InitializeCmd', timeoutMs: 100 })
    expect(wait.status).toBe(409)
    expect(wait.data.kind).toBe('unavailable')
    expect((await call('POST', '/hardware/trace/expect-none', { command: 'InitializeCmd', durationMs: 100 })).status).toBe(409)
    expect((await call('GET', '/hardware/faults')).status).toBe(409)
    expect((await call('POST', '/hardware/faults', { faults: [{ message: 'DeInitializeRsp', action: 'drop' }] })).status).toBe(409)
  })

  it('returns the timeline and COP trace marks together', async () => {
    const { data } = await call('POST', '/timeline/mark')
    expect(data.mark).toBeGreaterThan(0)
    expect(data.traceMark).toBe(0)
  })

  it('skips excluded entries in waits (a message is returned once)', async () => {
    const { data: m } = await call('POST', '/timeline/mark')
    await call('POST', '/messages', { name: 'SystemStatusRequest', body: {} })
    const first = await call('POST', '/timeline/wait', { filter: { name: 'SystemStatusResponse', since: m.mark }, timeoutMs: 3000 })
    expect(first.status).toBe(200)
    const again = await call('POST', '/timeline/wait', {
      filter: { name: 'SystemStatusResponse', since: m.mark, exclude: [first.data.id] },
      timeoutMs: 300,
    })
    expect(again.status).toBe(408)
  })

  it('reports the timeline size, cap and overflow in /session', async () => {
    const { data } = await call('GET', '/session')
    expect(data.timeline.cap).toBe(20000)
    expect(data.timeline.droppedThrough).toBe(0)
    expect(data.timeline.size).toBeGreaterThan(0)
  })

  it('injects mock appSMM faults and clears them', async () => {
    const request = async () => {
      const { data: m } = await call('POST', '/timeline/mark')
      await call('POST', '/messages', { name: 'SystemStatusRequest', body: {} })
      return m.mark as number
    }
    expect((await call('POST', '/mock/faults', { faults: [{ message: 'X', action: 'explode' }] })).status).toBe(400)
    const set = await call('POST', '/mock/faults', {
      faults: [
        { message: 'SystemStatusResponse', action: 'invalid', count: 1 },
        { message: 'TubeIdStatusResponse', action: 'wrongTopic' },
      ],
    })
    expect(set.status).toBe(200)
    expect((await call('GET', '/environment')).data.appSmm.mockFaults).toBe(2)

    let since = await request()
    const invalid = await call('POST', '/timeline/wait', { filter: { name: 'SystemStatusResponse', since }, timeoutMs: 3000 })
    expect(invalid.data.valid).toBe(false)
    since = await request()
    expect((await call('POST', '/timeline/wait', { filter: { name: 'SystemStatusResponse', since }, timeoutMs: 3000 })).data.valid).toBe(true)

    const { data: m } = await call('POST', '/timeline/mark')
    await call('POST', '/messages', { name: 'TubeIdStatusRequest', body: {} })
    const routed = await call('POST', '/timeline/wait', { filter: { name: 'TubeIdStatusResponse', since: m.mark }, timeoutMs: 3000 })
    expect(routed.data.topic).toBe('/is/hca1/tx')

    const status = await call('GET', '/mock/faults')
    expect(status.data.stats.map((s: any) => s.applied)).toEqual([1, 1])

    await call('POST', '/mock/faults', { faults: [{ message: 'SystemStatusResponse', action: 'reorder', ms: 5000, count: 1 }] })
    since = await request()
    expect((await call('POST', '/timeline/expect-none', { filter: { name: 'SystemStatusResponse', since }, durationMs: 300 })).status).toBe(200)
    await request()
    const both = await call('POST', '/timeline/sequence', { since, timeoutMs: 3000, filters: [{ name: 'SystemStatusResponse' }, { name: 'SystemStatusResponse' }] })
    expect(both.status).toBe(200)

    expect((await call('DELETE', '/mock/faults')).data.faults).toEqual([])
    expect((await call('GET', '/environment')).data.appSmm.mockFaults).toBe(0)
  })

  it('refuses faults outside the mock appSMM', async () => {
    const env = new Environment('unused')
    expect(() => env.setMockFaults([])).toThrow(/mock/)
    await expect(env.start(presetFor('offline', { mock: { faults: [{ message: 'X', action: 'drop' }] } }))).rejects.toThrow(/mock/)
  })
})

describe('timeline cap', () => {
  it('drops the oldest entries and records the highest dropped id', () => {
    const session = new BridgeSession(join(testbenchInfo.dir, 'simulator', 'resources', 'schemas'), 3)
    const record = (session as any).record.bind(session)
    for (let i = 0; i < 5; i++) record('tx', '/is/iw/rx', JSON.stringify({ Version: 7, SystemStatusRequest: {} }))
    const snap = session.snapshot()
    expect(snap.timeline).toEqual({ size: 3, cap: 3, droppedThrough: 2 })
    expect(session.query().map((e) => e.id)).toEqual([3, 4, 5])
    session.clear()
    expect(session.snapshot().timeline.droppedThrough).toBe(2)
    void session.dispose()
  })
})

describe('COP trace waits', () => {
  const traced = (env: Environment, name: string, way: 'rx' | 'tx' = 'rx') => {
    const t = env as any
    const entry = { id: ++t.traceSeq, time: Date.now(), way, hex: '', name, text: '' }
    t.trace.push(entry)
    env.emit('trace', entry)
  }

  it('matches commands with and without module prefix and Cmd suffix', () => {
    expect(commandMatches('AppMan.InitializeCmd', 'InitializeCmd')).toBe(true)
    expect(commandMatches('AppMan.InitializeCmd', 'Initialize')).toBe(true)
    expect(commandMatches('AppMan.InitializeCmd', 'AppMan.InitializeCmd')).toBe(true)
    expect(commandMatches('AppMan.DeInitializeCmd', 'InitializeCmd')).toBe(false)
  })

  it('waits for a command sent after the mark, ignoring responses and older commands', async () => {
    const env = new Environment('unused')
    traced(env, 'AppMan.InitializeCmd')
    const since = env.traceMark()
    const pending = env.waitForCommand('InitializeCmd', since, 1000)
    traced(env, 'AppMan.InitializeCmd', 'tx')
    traced(env, 'AppMan.InitializeCmd')
    expect((await pending).id).toBe(3)
    await expect(env.waitForCommand('DeInitializeCmd', since, 50)).rejects.toBeInstanceOf(CommandTimeoutError)
  })

  it('fails a quiet period as soon as the command is sent', async () => {
    const env = new Environment('unused')
    await expect(env.expectNoCommand('InitializeCmd', 0, 30)).resolves.toBeUndefined()
    const started = Date.now()
    const pending = env.expectNoCommand('InitializeCmd', 0, 5000)
    setTimeout(() => traced(env, 'AppMan.InitializeCmd'), 20)
    await expect(pending).rejects.toBeInstanceOf(CommandSentError)
    expect(Date.now() - started).toBeLessThan(2000)
    await expect(env.expectNoCommand('InitializeCmd', 0, 10)).rejects.toBeInstanceOf(CommandSentError)
  })
})
