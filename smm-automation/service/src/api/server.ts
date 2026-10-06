import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http'
import { join } from 'node:path'
import { BridgeSession, ExpectationError, WaitTimeoutError, summarize } from '../bridgeSession'
import { CommandSentError, CommandTimeoutError, Environment, UnavailableError, presetFor, type EnvironmentConfig, type Tier } from '../environment'
import { checkFilter, type MessageFilter } from '../messageFilter'
import { ICD_SCHEMA_VERSION, MESSAGES, testbenchInfo, type BeaconSettings, type ConnectionSettings, type TimelineEntry } from '../testbench'

/**
 * Versioned HTTP/JSON API of the SMM automation service. Breaking changes need a new major
 * API_VERSION and a new /api/vN prefix; the Robot library checks the major version at start-up.
 */
export const API_VERSION = '1.1.0'
const PREFIX = '/api/v1'

export class HttpError extends Error {
  constructor(readonly status: number, message: string, readonly details?: unknown) {
    super(message)
  }
}

type Json = Record<string, unknown>
type Handler = (ctx: { body: Json; params: Record<string, string>; query: URLSearchParams }) => Promise<unknown> | unknown

export interface ServiceOptions {
  testbenchDir?: string
  /** Timeline entries kept per Bridge session (older ones are dropped and reported in /session). */
  timelineCap?: number
  /** COP trace entries kept (older ones are dropped and reported in /environment). */
  traceCap?: number
}

/** The service state: one environment and one Bridge session at a time (one rig per service). */
export class AutomationService {
  readonly env: Environment
  readonly session: BridgeSession
  private routes: { method: string; pattern: RegExp; keys: string[]; handler: Handler }[] = []

  constructor(options: ServiceOptions = {}) {
    const dir = options.testbenchDir ?? testbenchInfo.dir
    this.env = new Environment(join(dir, 'hwsim'), options.traceCap)
    this.session = new BridgeSession(join(dir, 'simulator', 'resources', 'schemas'), options.timelineCap)
    this.defineRoutes()
  }

  private route(method: string, path: string, handler: Handler): void {
    const keys: string[] = []
    const pattern = new RegExp(`^${PREFIX}${path.replace(/:([a-zA-Z]+)/g, (_m, key) => (keys.push(key), '([^/]+)'))}/?$`)
    this.routes.push({ method, pattern, keys, handler })
  }

  private defineRoutes(): void {
    const { env, session } = this

    // ---- service
    this.route('GET', '/health', () => ({ ok: true, apiVersion: API_VERSION, testbench: testbenchInfo, icdVersion: ICD_SCHEMA_VERSION }))
    this.route('GET', '/icd', () => ({ version: ICD_SCHEMA_VERSION, messages: MESSAGES, schemas: session.registry.messageNames }))
    this.route('GET', '/schemas/:name', ({ params }) => {
      const schema = session.registry.schemaFor(params.name)
      if (!schema) throw new HttpError(404, `No ICD schema for ${params.name}`)
      return schema
    })

    // ---- environment (tiers)
    this.route('GET', '/environment', () => env.status())
    this.route('POST', '/environment/start', async ({ body }) => {
      const tier = String(body.tier ?? 'mock') as Tier
      if (!['mock', 'offline', 'rig'].includes(tier)) throw new HttpError(400, `Unknown tier "${tier}" (mock, offline, rig)`)
      await env.start(presetFor(tier, (body.overrides ?? {}) as Partial<EnvironmentConfig>))
      return env.status()
    })
    this.route('POST', '/environment/stop', async () => {
      await session.disconnect().catch(() => undefined)
      await env.stop()
      return env.status()
    })
    this.route('POST', '/environment/restart-appsmm', async ({ body }) => {
      await env.restartAppSmm(num(body.downMs, 1000))
      return env.status()
    })
    this.route('POST', '/environment/restart-broker', async ({ body }) => {
      await env.restartBroker(num(body.downMs, 2000))
      return env.status()
    })
    this.route('GET', '/environment/logs', ({ query }) => env.getLogs(num(query.get('since'), 0), query.get('source') ?? undefined))

    // ---- hardware twin
    this.route('GET', '/hardware', () => env.hardwareSnapshot())
    this.route('GET', '/hardware/trace', ({ query }) => env.getTrace(num(query.get('since'), 0)))
    this.route('POST', '/hardware/trace/wait', async ({ body }) => {
      requireTwin(env)
      return env.waitForCommand(requireString(body, 'command'), num(body.since, 0), num(body.timeoutMs, 30000))
    })
    this.route('POST', '/hardware/trace/expect-none', async ({ body }) => {
      requireTwin(env)
      await env.expectNoCommand(requireString(body, 'command'), num(body.since, 0), num(body.durationMs, 3000))
      return { ok: true }
    })
    this.route('POST', '/hardware/actions/:action', ({ params, body }) => {
      const refused = env.hardwareAction(params.action, body)
      if (refused) throw new HttpError(409, refused)
      return { ok: true, state: env.hardwareSnapshot().state }
    })

    // ---- Bridge session
    this.route('GET', '/session', () => session.snapshot())
    this.route('POST', '/session/connect', async ({ body }) => {
      const broker = env.config?.broker
      const settings: ConnectionSettings = {
        host: String(body.host ?? broker?.host ?? '127.0.0.1'),
        port: num(body.port, broker?.port ?? 1883),
        mode: (body.mode === 'listen' ? 'listen' : 'bridge') as ConnectionSettings['mode'],
      }
      if (body.clear !== false) session.clear()
      await session.connect(settings, num(body.timeoutMs, 10000))
      return session.snapshot()
    })
    this.route('POST', '/session/disconnect', async ({ body }) => {
      await session.disconnect(body.abrupt === true)
      return session.snapshot()
    })
    this.route('POST', '/session/clear', () => {
      session.clear()
      return { ok: true }
    })
    this.route('PATCH', '/session/settings', ({ body }) => {
      session.engine.updateSettings(body as Partial<BeaconSettings>)
      return session.snapshot().settings
    })
    this.route('PATCH', '/session/analyzers/:number', ({ params, body }) => {
      const n = num(params.number, NaN)
      if (typeof body.connected === 'boolean') session.engine.setAnalyzerConnected(n, body.connected)
      const { connected: _ignored, ...changes } = body
      if (Object.keys(changes).length) session.engine.updateAnalyzer(n, changes)
      return session.snapshot().analyzers.find((a) => a.number === n)
    })

    // ---- messages
    this.route('POST', '/messages', ({ body }) => {
      const name = requireString(body, 'name')
      const payloadBody = body.body ?? {}
      if (body.strict === true) {
        const result = session.validate(name, payloadBody)
        if (!result.valid) throw new HttpError(400, `${name} does not match its ICD schema`, result.errors)
      }
      requireConnected(session)
      return entryOut(session.send(name, payloadBody, num(body.analyzer, -1)))
    })
    this.route('POST', '/messages/raw', ({ body }) => {
      requireConnected(session)
      return entryOut(session.publishRaw(requireString(body, 'topic'), String(body.raw ?? '')))
    })
    this.route('POST', '/messages/validate', ({ body }) => session.validate(requireString(body, 'name'), body.body ?? {}))

    // ---- timeline, waits and expectations
    this.route('GET', '/timeline', ({ query }) => {
      const filter: MessageFilter = {}
      if (query.get('since')) filter.since = num(query.get('since'), 0)
      if (query.get('name')) filter.name = query.get('name')!.split(',')
      if (query.get('way')) filter.way = query.get('way') as 'rx' | 'tx'
      return session.query(filter, num(query.get('limit'), 1000)).map(entryOut)
    })
    this.route('POST', '/timeline/query', ({ body }) => session.query(requireFilter(body.filter ?? {}), num(body.limit, 1000)).map(entryOut))
    this.route('POST', '/timeline/mark', () => ({ mark: session.mark(), traceMark: env.traceMark() }))
    this.route('POST', '/timeline/wait', async ({ body }) =>
      entryOut(await session.waitFor(requireFilter(body.filter), num(body.timeoutMs, 10000))),
    )
    this.route('POST', '/timeline/sequence', async ({ body }) => {
      if (!Array.isArray(body.filters) || !body.filters.length) throw new HttpError(400, 'filters must be a non-empty array')
      const filters = body.filters.map((f, i) => requireFilter(f, `filters[${i}]`))
      const since = body.since === undefined ? undefined : num(body.since, 0)
      return (await session.waitForSequence(filters, num(body.timeoutMs, 10000), since)).map(entryOut)
    })
    this.route('POST', '/timeline/expect-none', async ({ body }) => {
      await session.expectNone(requireFilter(body.filter), num(body.durationMs, 3000))
      return { ok: true }
    })
    this.route('GET', '/pair-issues', () => session.getPairIssues())
  }

  // ---------------------------------------------------------------- HTTP plumbing

  async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? '/', 'http://localhost')
    try {
      const candidates = this.routes.filter((r) => r.pattern.test(url.pathname))
      if (!candidates.length) throw new HttpError(404, `No route ${url.pathname}`)
      const route = candidates.find((r) => r.method === req.method)
      if (!route) throw new HttpError(405, `${req.method} not allowed on ${url.pathname}`)
      const match = route.pattern.exec(url.pathname)!
      const params = Object.fromEntries(route.keys.map((k, i) => [k, decodeURIComponent(match[i + 1])]))
      const body = await readJson(req)
      send(res, 200, (await route.handler({ body, params, query: url.searchParams })) ?? { ok: true })
    } catch (err) {
      if (err instanceof HttpError) send(res, err.status, { error: err.message, details: err.details })
      else if (err instanceof WaitTimeoutError || err instanceof CommandTimeoutError) send(res, 408, { error: err.message, kind: 'timeout', details: err.details })
      else if (err instanceof UnavailableError) send(res, 409, { error: err.message, kind: 'unavailable' })
      else if (err instanceof ExpectationError || err instanceof CommandSentError) send(res, 409, { error: err.message, kind: 'expectation', details: err.details })
      else send(res, 500, { error: (err as Error).message ?? String(err), kind: 'internal' })
    }
  }

  listen(port: number, host: string): Promise<Server> {
    const server = createServer((req, res) => void this.handle(req, res))
    // Waits can legitimately take minutes (initialization, recover): no server-side request timeout.
    server.requestTimeout = 0
    server.headersTimeout = 60000
    server.keepAliveTimeout = 5000
    return new Promise((resolve, reject) => {
      server.once('error', reject)
      server.listen(port, host, () => resolve(server))
    })
  }

  async shutdown(): Promise<void> {
    await this.session.dispose()
    await this.env.stop()
  }
}

// ---------------------------------------------------------------- helpers

function entryOut(e: TimelineEntry) {
  return { ...summarize(e), analyzer: e.analyzer, epochMs: e.time, pairing: e.pairing }
}

function num(value: unknown, fallback: number): number {
  if (value === undefined || value === null || value === '') return fallback
  const n = Number(value)
  if (Number.isNaN(n)) throw new HttpError(400, `Not a number: ${String(value)}`)
  return n
}

function requireString(body: Json, key: string): string {
  const value = body[key]
  if (typeof value !== 'string' || !value) throw new HttpError(400, `"${key}" is required`)
  return value
}

function requireFilter(value: unknown, label = 'filter'): MessageFilter {
  const problem = checkFilter(value)
  if (problem) throw new HttpError(400, `${label}: ${problem}`)
  return value as MessageFilter
}

function requireConnected(session: BridgeSession): void {
  if (session.link.status !== 'connected') throw new HttpError(409, `The Bridge session is not connected (link: ${session.link.status})`)
}

function requireTwin(env: Environment): void {
  if (env.config?.hardware !== 'twin') throw new UnavailableError('No COP trace in this environment (hardware twin, offline tier only)')
}

async function readJson(req: IncomingMessage): Promise<Json> {
  if (req.method === 'GET' || req.method === 'HEAD') return {}
  const chunks: Buffer[] = []
  for await (const chunk of req) chunks.push(chunk as Buffer)
  const text = Buffer.concat(chunks).toString('utf8').trim()
  if (!text) return {}
  try {
    const parsed = JSON.parse(text)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('not an object')
    return parsed as Json
  } catch (err) {
    throw new HttpError(400, `Request body is not a JSON object: ${(err as Error).message}`)
  }
}

function send(res: ServerResponse, status: number, data: unknown): void {
  const text = JSON.stringify(data)
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'content-length': Buffer.byteLength(text) })
  res.end(text)
}
