import { EventEmitter } from 'node:events'
import { checkFilter, describeFilter, entryMatches, type MessageFilter } from './messageFilter'
import {
  BeaconEngine, MqttLink, PairChecker, SchemaRegistry, Timeline, envelope, topicToSmm,
  type ConnectionSettings, type PairIssue, type TimelineEntry, type ValidationResult,
} from './testbench'

const MAX_PAIR_ISSUES = 300
export const DEFAULT_TIMELINE_CAP = 20000
/** How long the broker's echo of a message this session published is expected (and not taken for another Bridge's). */
const ECHO_WINDOW_MS = 10000

export class WaitTimeoutError extends Error {
  constructor(message: string, readonly details: Record<string, unknown>) {
    super(message)
  }
}

export class ExpectationError extends Error {
  constructor(message: string, readonly details: Record<string, unknown>) {
    super(message)
  }
}

export interface ForeignBridge {
  count: number
  lastSeen: number
  lastMessage: string
}

/**
 * Headless counterpart of the SMM Testbench main process (simulator/src/main/index.ts):
 * the same MqttLink, BeaconEngine, Timeline, SchemaRegistry and PairChecker, wired the same way,
 * without Electron. In 'bridge' mode it is the SMM Bridge (plus IW and analyzers) towards appSMM.
 */
export class BridgeSession extends EventEmitter<{ entry: [TimelineEntry] }> {
  readonly registry: SchemaRegistry
  readonly timeline = new Timeline()
  readonly link = new MqttLink()
  readonly engine: BeaconEngine
  /**
   * The entries waits and queries look at. The TestBench Timeline parses and validates each message
   * but keeps a fixed 20000; this store has the configurable cap and records what overflow dropped.
   */
  private entries: TimelineEntry[] = []
  /** Highest timeline id dropped by the cap (0 = nothing lost). Ids never restart, so tests compare it with their marks. */
  private droppedThrough = 0
  /** Id of the newest entry ever recorded (kept across clear(), so marks stay comparable). */
  private lastId = 0
  private pairs = new PairChecker()
  private pairIssues: PairIssue[] = []
  /** Expected echoes of own publications: `${topic} ${raw}` -> deadlines (ms), oldest first, one per publication. */
  private readonly ownEchoes = new Map<string, number[]>()
  private otherBridge?: ForeignBridge
  private connection?: ConnectionSettings
  private linkError?: string
  private readonly sweeper: NodeJS.Timeout

  constructor(schemaDir: string, readonly timelineCap = DEFAULT_TIMELINE_CAP) {
    super()
    this.setMaxListeners(100)
    this.registry = new SchemaRegistry(schemaDir)
    this.engine = new BeaconEngine((name, body, analyzer) => {
      try {
        this.send(name, body, analyzer)
      } catch (err) {
        console.error(`Could not send ${name}:`, err)
      }
    })
    this.link.on('message', (topic, payload) => this.onMessage(topic, payload.toString('utf8')))
    this.link.on('status', (status, error) => {
      this.linkError = error
      if (status === 'connected' && this.link.mode === 'bridge') this.engine.start()
      else if (status !== 'connected' && this.engine.active) this.engine.stop()
    })
    this.sweeper = setInterval(() => {
      const now = Date.now()
      const late = this.pairs.sweep(now)
      if (late.length) this.addPairIssues(late)
      for (const [key, deadlines] of this.ownEchoes) {
        while (deadlines.length && deadlines[0] < now) deadlines.shift()
        if (!deadlines.length) this.ownEchoes.delete(key)
      }
    }, 1000)
    this.sweeper.unref()
  }

  // ---------------------------------------------------------------- connection

  async connect(settings: ConnectionSettings, timeoutMs = 10000): Promise<void> {
    this.connection = settings
    this.otherBridge = undefined
    this.engine.reset()
    this.pairs = new PairChecker()
    this.pairIssues = []
    await this.link.connect(settings)
    await new Promise<void>((resolve, reject) => {
      if (this.link.status === 'connected') return resolve()
      const timer = setTimeout(() => {
        this.link.off('status', onStatus)
        reject(new Error(`Could not connect to ${settings.host}:${settings.port} within ${timeoutMs} ms${this.linkError ? `: ${this.linkError}` : ''}`))
      }, timeoutMs)
      const onStatus = (status: string) => {
        if (status !== 'connected') return
        clearTimeout(timer)
        this.link.off('status', onStatus)
        resolve()
      }
      this.link.on('status', onStatus)
    })
  }

  /**
   * Leaves the broker. In bridge mode appSMM is told first that analyzers, IW and Bridge are
   * leaving (like the Testbench does), unless `abrupt` is set to emulate a lost Bridge.
   */
  async disconnect(abrupt = false): Promise<void> {
    if (abrupt) {
      // Kill the socket without an MQTT DISCONNECT so the broker publishes the Bridge's last will
      // ("SMMBridge Disconnected"), exactly like a crashed or unplugged Bridge.
      const client = (this.link as unknown as { client?: { options: { reconnectPeriod: number }; stream?: { destroy(): void } } }).client
      if (client) {
        client.options.reconnectPeriod = 0
        client.stream?.destroy()
        await new Promise((resolve) => setTimeout(resolve, 200))
      }
      this.engine.stop()
    } else if (this.link.mode === 'bridge' && this.link.status === 'connected') {
      this.engine.farewell()
      await new Promise((resolve) => setTimeout(resolve, 300))
    }
    await this.link.disconnect()
  }

  // ---------------------------------------------------------------- sending

  /** Publishes an ICD message to appSMM (analyzer -1 = IW topic) and records it on the timeline. */
  send(name: string, body: unknown, analyzer = -1): TimelineEntry {
    const raw = JSON.stringify(envelope(name, body))
    return this.publishRaw(topicToSmm(name, analyzer), raw)
  }

  /** Publishes any text on any topic: for negative tests (invalid JSON, wrong schema, wrong topic). */
  publishRaw(topic: string, raw: string): TimelineEntry {
    this.link.publish(topic, raw)
    const key = `${topic} ${raw}`
    // Each publication expects its own echo: a counter decremented by a timer would let an old publication's timer
    // (whose echo already came) cancel a newer identical publication's echo, which then looked like another Bridge.
    const deadlines = this.ownEchoes.get(key) ?? []
    deadlines.push(Date.now() + ECHO_WINDOW_MS)
    this.ownEchoes.set(key, deadlines)
    return this.record('tx', topic, raw)
  }

  validate(name: string, body: unknown): ValidationResult {
    return this.registry.validate(envelope(name, body))
  }

  // ---------------------------------------------------------------- receiving

  private onMessage(topic: string, raw: string): void {
    if (this.link.mode === 'bridge' && topic.endsWith('/rx')) {
      const key = `${topic} ${raw}`
      const deadlines = this.ownEchoes.get(key)
      const now = Date.now()
      while (deadlines?.length && deadlines[0] < now) deadlines.shift()
      if (deadlines?.length) {
        deadlines.shift()
        if (!deadlines.length) this.ownEchoes.delete(key)
        return
      }
      this.otherBridge = { count: (this.otherBridge?.count ?? 0) + 1, lastSeen: Date.now(), lastMessage: raw.slice(0, 300) }
    }
    const entry = this.record('rx', topic, raw)
    if (typeof entry.payload === 'object') this.engine.handle(topic, entry.payload)
  }

  private record(way: 'rx' | 'tx', topic: string, raw: string): TimelineEntry {
    const entry = this.timeline.add(way, topic, raw, (p) => this.registry.validate(p))
    this.entries.push(entry)
    this.lastId = entry.id
    if (this.entries.length > this.timelineCap) {
      const lost = this.entries.splice(0, this.entries.length - this.timelineCap)
      this.droppedThrough = lost[lost.length - 1].id
    }
    if (entry.name && typeof entry.payload === 'object' && entry.payload !== null) {
      const body = (entry.payload as Record<string, unknown>)[entry.name]
      if (body && typeof body === 'object') {
        const issues = [...this.pairs.sweep(entry.time), ...this.pairs.feed(topic, entry.name, body as Record<string, unknown>, entry.time)]
        if (issues.length) {
          entry.pairing = issues
          this.addPairIssues(issues)
        }
      }
    }
    this.emit('entry', entry)
    return entry
  }

  private addPairIssues(issues: PairIssue[]): void {
    this.pairIssues = [...this.pairIssues, ...issues].slice(-MAX_PAIR_ISSUES)
  }

  // ---------------------------------------------------------------- queries and waits

  /** Id of the newest timeline entry: pass it as `since` to look only at what comes next. */
  mark(): number {
    return this.lastId
  }

  query(filter: MessageFilter = {}, limit = 1000): TimelineEntry[] {
    const found = this.entries.filter((e) => entryMatches(e, filter))
    return found.slice(-limit)
  }

  /** Resolves with the first entry matching the filter (already recorded or arriving within timeoutMs). */
  waitFor(filter: MessageFilter, timeoutMs: number): Promise<TimelineEntry> {
    const problem = checkFilter(filter)
    if (problem) return Promise.reject(new Error(problem))
    const existing = this.entries.find((e) => entryMatches(e, filter))
    if (existing) return Promise.resolve(existing)
    return new Promise((resolve, reject) => {
      const onEntry = (entry: TimelineEntry) => {
        if (!entryMatches(entry, filter)) return
        clearTimeout(timer)
        this.off('entry', onEntry)
        resolve(entry)
      }
      const timer = setTimeout(() => {
        this.off('entry', onEntry)
        reject(new WaitTimeoutError(`No ${describeFilter(filter)} within ${timeoutMs} ms`, this.context(filter)))
      }, timeoutMs)
      this.on('entry', onEntry)
    })
  }

  /**
   * Waits for the filters to match in this order (each one after the previous match).
   * Other messages may come in between.
   */
  async waitForSequence(filters: MessageFilter[], timeoutMs: number, since?: number): Promise<TimelineEntry[]> {
    const deadline = Date.now() + timeoutMs
    const found: TimelineEntry[] = []
    let after = since
    for (const [index, filter] of filters.entries()) {
      const left = Math.max(0, deadline - Date.now())
      try {
        const entry = await this.waitFor({ ...filter, since: Math.max(after ?? 0, filter.since ?? 0) }, left)
        found.push(entry)
        after = entry.id
      } catch (err) {
        if (err instanceof WaitTimeoutError) {
          throw new WaitTimeoutError(`Sequence step ${index + 1}/${filters.length} not seen: ${err.message}`, {
            ...err.details,
            matchedSoFar: found,
          })
        }
        throw err
      }
    }
    return found
  }

  /** Resolves if nothing matches for durationMs; rejects with the offending entry otherwise. */
  expectNone(filter: MessageFilter, durationMs: number): Promise<void> {
    const problem = checkFilter(filter)
    if (problem) return Promise.reject(new Error(problem))
    const existing = this.entries.find((e) => entryMatches(e, filter))
    if (existing) return Promise.reject(new ExpectationError(`Unexpected ${describeFilter(filter)}`, { entry: summarize(existing) }))
    return new Promise((resolve, reject) => {
      const onEntry = (entry: TimelineEntry) => {
        if (!entryMatches(entry, filter)) return
        clearTimeout(timer)
        this.off('entry', onEntry)
        reject(new ExpectationError(`Unexpected ${describeFilter(filter)}`, { entry: summarize(entry) }))
      }
      const timer = setTimeout(() => {
        this.off('entry', onEntry)
        resolve()
      }, durationMs)
      this.on('entry', onEntry)
    })
  }

  /** The last messages around a failed wait, to put into the report. */
  private context(filter: MessageFilter): Record<string, unknown> {
    const recent = this.entries.filter((e) => filter.since === undefined || e.id > filter.since).slice(-25)
    const sameName = filter.name ? this.query({ name: filter.name, since: filter.since }, 10) : []
    return {
      filter,
      link: this.link.status,
      smm: this.engine.snapshot().smm,
      sameNameMessages: sameName.map(summarize),
      recentMessages: recent.map(summarize),
    }
  }

  // ---------------------------------------------------------------- state

  getPairIssues(): PairIssue[] {
    return this.pairIssues
  }

  clear(): void {
    this.timeline.clear()
    this.entries = []
    this.pairIssues = []
  }

  snapshot() {
    return {
      link: this.link.status,
      linkError: this.linkError,
      connection: this.connection,
      otherBridge: this.otherBridge,
      lastEntryId: this.mark(),
      timeline: { size: this.entries.length, cap: this.timelineCap, droppedThrough: this.droppedThrough },
      pairIssueCount: this.pairIssues.length,
      ...this.engine.snapshot(),
    }
  }

  async dispose(): Promise<void> {
    clearInterval(this.sweeper)
    await this.disconnect().catch(() => undefined)
  }
}

export function summarize(e: TimelineEntry) {
  return {
    id: e.id,
    time: new Date(e.time).toISOString(),
    way: e.way,
    topic: e.topic,
    name: e.name,
    body: e.name && typeof e.payload === 'object' && e.payload ? (e.payload as Record<string, unknown>)[e.name] : e.raw,
    valid: e.validation?.valid,
    errors: e.validation?.valid ? undefined : e.validation?.errors,
  }
}
