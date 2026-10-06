import { EventEmitter } from 'node:events'
import { existsSync, readFileSync } from 'node:fs'
import { createServer, Socket, type Server } from 'node:net'
import { dirname, join } from 'node:path'
import { Aedes } from 'aedes'
import { validateFaults, type MockFault } from './mock/faults'
import { MockAppSmm, type MockSmmOptions } from './mock/mockAppSmm'
import {
  CopServer, HardwareTwin, IcolCatalog, IcolCodec, Runner, defaultHwSettings, defaultRack, defaultRunnerSettings, defaultTrays,
  type CopTraceEntry, type HwSettings, type RackConfig, type RunnerSettings, type TrayConfig,
} from './testbench'

/**
 * Execution tiers:
 *   mock    embedded broker + scripted mock appSMM: framework self-test, CI without appSMM
 *   offline Mosquitto + real appSMM.exe + SMM hardware twin (hwsim) on this PC: product tests without hardware
 *   rig     the dedicated automation instrument (broker and appSMM on the RTC board): HIL product tests
 */
export type Tier = 'mock' | 'offline' | 'rig'

export interface EnvironmentConfig {
  tier: Tier
  broker: { kind: 'embedded' | 'mosquitto' | 'external'; host: string; port: number }
  appSmm: 'mock' | 'real' | 'external'
  hardware: 'twin' | 'external' | 'none'
  runner: RunnerSettings
  hw: HwSettings
  mock: Omit<MockSmmOptions, 'brokerUrl'>
}

export function presetFor(tier: Tier, overrides: Partial<EnvironmentConfig> = {}): EnvironmentConfig {
  const base = {
    runner: { ...defaultRunnerSettings(), ...overrides.runner },
    hw: { ...defaultHwSettings(), ...overrides.hw },
    mock: { ...overrides.mock },
  }
  const preset = (broker: EnvironmentConfig['broker'], appSmm: EnvironmentConfig['appSmm'], hardware: EnvironmentConfig['hardware']): EnvironmentConfig => ({
    tier,
    broker: { ...broker, ...overrides.broker },
    appSmm: overrides.appSmm ?? appSmm,
    hardware: overrides.hardware ?? hardware,
    ...base,
  })
  switch (tier) {
    case 'mock':
      return preset({ kind: 'embedded', host: '127.0.0.1', port: 1884 }, 'mock', 'none')
    case 'offline':
      return preset({ kind: 'mosquitto', host: '127.0.0.1', port: 1883 }, 'real', 'twin')
    case 'rig':
      return preset({ kind: 'external', host: '10.0.1.111', port: 1883 }, 'external', 'external')
    default:
      throw new Error(`Unknown tier "${String(tier)}" (mock, offline, rig)`)
  }
}

/** The requested capability does not exist in the running tier (e.g. hardware actions in the mock tier). */
export class UnavailableError extends Error {}

export interface LogLine {
  id: number
  time: number
  source: string
  line: string
}

const MAX_LOG = 10000
export const DEFAULT_TRACE_CAP = 10000

/** ``AppMan.InitializeCmd`` matches ``InitializeCmd``, ``Initialize`` and ``AppMan.InitializeCmd``. */
export function commandMatches(traceName: string, command: string): boolean {
  const wanted = command.endsWith('Cmd') || command.includes('.') ? command : `${command}Cmd`
  return traceName === wanted || traceName.endsWith(`.${wanted}`)
}

/** Brings up and tears down everything around appSMM for one tier, and drives the hardware twin. */
export class Environment extends EventEmitter<{ log: [LogLine]; trace: [CopTraceEntry] }> {
  config?: EnvironmentConfig
  private broker?: { aedes: Aedes; server: Server; sockets: Set<Socket> }
  private runner?: Runner
  private mock?: MockAppSmm
  /** Fault rules of the mock appSMM; kept across restart-appsmm, reset by every environment start. */
  private mockFaults: MockFault[] = []
  private twin?: HardwareTwin
  private cop?: CopServer
  private logs: LogLine[] = []
  private trace: CopTraceEntry[] = []
  /** Service-wide trace ids: each environment start creates a new twin whose own ids restart at 1. */
  private traceSeq = 0
  /** Highest trace id dropped by the cap (0 = nothing lost). */
  private traceDroppedThrough = 0
  private nextLogId = 1

  constructor(private readonly hwsimDir: string, readonly traceCap = DEFAULT_TRACE_CAP) {
    super()
    this.setMaxListeners(100)
  }

  get running(): boolean {
    return !!this.config
  }

  get brokerUrl(): string | undefined {
    return this.config && `mqtt://${this.config.broker.host}:${this.config.broker.port}`
  }

  log(source: string, line: string): void {
    const entry = { id: this.nextLogId++, time: Date.now(), source, line }
    this.logs.push(entry)
    if (this.logs.length > MAX_LOG) this.logs.splice(0, this.logs.length - MAX_LOG)
    this.emit('log', entry)
  }

  // ---------------------------------------------------------------- lifecycle

  async start(config: EnvironmentConfig): Promise<void> {
    const faults = validateFaults(config.mock?.faults)
    if (faults.length && config.appSmm !== 'mock') throw new UnavailableError('Fault injection exists only for the mock appSMM (tier mock)')
    if (this.config) await this.stop()
    this.config = config
    this.mockFaults = faults
    this.log('Service', `Starting ${config.tier} environment`)
    try {
      await this.startBroker()
      if (config.hardware === 'twin') await this.startTwin()
      await this.startAppSmm()
    } catch (err) {
      this.log('Service', `Start failed: ${(err as Error).message}`)
      await this.stop()
      throw err
    }
  }

  async stop(): Promise<void> {
    if (!this.config) return
    this.log('Service', `Stopping ${this.config.tier} environment`)
    await this.stopAppSmm()
    if (this.twin) {
      this.twin.stop()
      this.twin = undefined
    }
    if (this.cop) {
      await this.cop.close().catch(() => undefined)
      this.cop = undefined
    }
    await this.stopBroker()
    this.runner = undefined
    this.config = undefined
  }

  // ---------------------------------------------------------------- broker

  private async startBroker(): Promise<void> {
    const { broker } = this.config!
    if (broker.kind === 'embedded') {
      const aedes = await Aedes.createBroker()
      const sockets = new Set<Socket>()
      const server = createServer((socket) => {
        sockets.add(socket)
        socket.on('close', () => sockets.delete(socket))
        aedes.handle(socket)
      })
      await new Promise<void>((resolve, reject) => {
        server.once('error', reject)
        server.listen(broker.port, broker.host, () => resolve())
      })
      this.broker = { aedes, server, sockets }
      this.log('Broker', `Embedded MQTT broker on ${broker.host}:${broker.port}`)
    } else if (broker.kind === 'mosquitto') {
      if (await portOpen(broker.host, broker.port)) throw new Error(`Port ${broker.port} is already in use: stop the other broker first`)
      this.ensureRunner().startMosquitto()
      await waitForPort(broker.host, broker.port, 10000, 'Mosquitto')
    } else {
      if (!(await portOpen(broker.host, broker.port, 3000))) throw new Error(`Broker ${broker.host}:${broker.port} is not reachable`)
      this.log('Broker', `Using external broker ${broker.host}:${broker.port}`)
    }
  }

  private async stopBroker(): Promise<void> {
    if (this.broker) {
      const { aedes, server, sockets } = this.broker
      this.broker = undefined
      for (const s of sockets) s.destroy()
      await new Promise<void>((resolve) => server.close(() => resolve()))
      await new Promise<void>((resolve) => aedes.close(() => resolve()))
    }
    if (this.runner) await this.runner.mosquitto.stop()
  }

  /** Takes the broker down for downMs and brings it back: every client loses its connection. */
  async restartBroker(downMs = 2000): Promise<void> {
    const kind = this.config?.broker.kind
    if (kind !== 'embedded' && kind !== 'mosquitto') throw new UnavailableError('Only a broker started by this service can be restarted')
    await this.stopBroker()
    this.log('Broker', `Broker down for ${downMs} ms`)
    await sleep(downMs)
    await this.startBroker()
  }

  // ---------------------------------------------------------------- appSMM

  private ensureRunner(): Runner {
    if (!this.runner) {
      this.runner = new Runner(this.config!.runner)
      this.runner.on('output', (source, line) => this.log(source, line))
    }
    return this.runner
  }

  private async startAppSmm(): Promise<void> {
    const config = this.config!
    if (config.appSmm === 'mock') {
      this.mock = new MockAppSmm({ ...config.mock, faults: this.mockFaults, brokerUrl: this.brokerUrl! })
      this.mock.on('log', (l) => this.log('MockAppSMM', l))
      await this.mock.start()
    } else if (config.appSmm === 'real') {
      const runner = this.ensureRunner()
      for (const change of runner.startAppSmm()) this.log('appSMM', `Prepared: ${change}`)
      const status = runner.appSmm.status()
      if (!status.running) throw new Error(`appSMM did not start: ${status.error ?? 'unknown error'}`)
    }
  }

  private async stopAppSmm(): Promise<void> {
    if (this.mock) {
      await this.mock.stop()
      this.mock = undefined
    }
    if (this.runner) await this.runner.appSmm.stop()
  }

  /** Stops appSMM (killed, no goodbye) and starts it again. */
  async restartAppSmm(downMs = 1000): Promise<void> {
    if (!this.config || this.config.appSmm === 'external') throw new UnavailableError('appSMM is not managed by this service in this tier')
    await this.stopAppSmm()
    await sleep(downMs)
    await this.startAppSmm()
  }

  /** Replaces the fault rules of the mock appSMM (mutation testing); [] restores the scripted behaviour. */
  setMockFaults(input: unknown): MockFault[] {
    if (this.config?.appSmm !== 'mock') throw new UnavailableError('Fault injection exists only for the mock appSMM (tier mock)')
    this.mockFaults = validateFaults(input)
    this.mock?.setFaults(this.mockFaults)
    this.log('Service', `Mock fault rules: ${this.mockFaults.length}`)
    return this.mockFaults
  }

  /** The fault rules and how often each matched/was applied since the mock appSMM (re)started. */
  mockFaultStatus() {
    if (this.config?.appSmm !== 'mock') throw new UnavailableError('Fault injection exists only for the mock appSMM (tier mock)')
    return { faults: this.mockFaults, stats: this.mock?.faultStats ?? [] }
  }

  // ---------------------------------------------------------------- hardware twin

  private async startTwin(): Promise<void> {
    const { hw, runner } = this.config!
    const catalog = IcolCatalog.fromDirectory(join(this.hwsimDir, 'resources', 'icol'))
    const local = join(dirname(runner.appSmmExe), 'InstrumentModules.config')
    const bundled = join(this.hwsimDir, 'resources', 'InstrumentModules.config')
    const instances = existsSync(local) ? local : bundled
    if (existsSync(instances)) catalog.addInstances(readFileSync(instances, 'utf8'))
    this.twin = new HardwareTwin(new IcolCodec(catalog), hw)
    this.twin.on('log', (line) => this.log('Hardware', line))
    this.twin.on('trace', (entry) => {
      const recorded = { ...entry, id: ++this.traceSeq }
      this.trace.push(recorded)
      if (this.trace.length > this.traceCap) {
        const lost = this.trace.splice(0, this.trace.length - this.traceCap)
        this.traceDroppedThrough = lost[lost.length - 1].id
      }
      this.emit('trace', recorded)
    })
    this.cop = new CopServer()
    this.cop.on('connection', (link) => {
      this.log('Hardware', 'appSMM connected to the hardware (COP)')
      this.twin?.attach(link)
    })
    this.cop.on('error', (err) => this.log('Hardware', `COP server error: ${err.message}`))
    await this.cop.listen(hw.port, hw.host)
    this.twin.start()
    this.log('Hardware', `Hardware twin listening on ${hw.host}:${hw.port}`)
  }

  private requireTwin(): HardwareTwin {
    if (!this.twin) throw new UnavailableError('No hardware twin in this environment (tier offline only)')
    return this.twin
  }

  hardwareSnapshot() {
    return this.requireTwin().snapshot()
  }

  /**
   * Operator/hardware actions on the twin. Returns the twin's refusal text, if any.
   *   emergencyStop, loadInputTray {tray?: TrayConfig | trayIndex}, removeInputTray, insertOutputTray,
   *   removeOutputTray, insertFrontIn {rack?: RackConfig, rackId?}, removeFrontIn, removeFrontOut,
   *   pauseLane/resumeLane/toggleLaneError {area: Input|Output}, toggleOutputAvailable
   */
  hardwareAction(action: string, arg: Record<string, unknown> = {}): string | undefined {
    const twin = this.requireTwin()
    const area = (arg.area === 'Output' ? 'Output' : 'Input') as 'Input' | 'Output'
    const done = (fn: () => void) => {
      fn()
      return undefined
    }
    switch (action) {
      case 'emergencyStop':
        return done(() => twin.emergencyStop())
      case 'loadInputTray': {
        const tray = (arg.tray as TrayConfig | undefined) ?? defaultTrays()[Number(arg.trayIndex ?? 0)]
        if (!tray) throw new Error('No such tray')
        return twin.loadInputTray(tray)
      }
      case 'removeInputTray':
        return twin.removeInputTray()
      case 'insertOutputTray':
        return twin.insertOutputTray()
      case 'removeOutputTray':
        return twin.removeOutputTray()
      case 'insertFrontIn':
        return twin.insertFrontIn((arg.rack as RackConfig | undefined) ?? defaultRack(String(arg.rackId ?? 'F001'), Number(arg.firstSample ?? 1)))
      case 'removeFrontIn':
        return twin.removeFrontIn()
      case 'removeFrontOut':
        return twin.removeFrontOut()
      case 'removeRack':
        return done(() => twin.removeRack(Number(arg.key)))
      case 'pauseLane':
        return done(() => twin.pauseLane(area))
      case 'resumeLane':
        return done(() => twin.resumeLane(area))
      case 'toggleLaneError':
        return done(() => twin.toggleLaneError(area))
      case 'toggleOutputAvailable':
        return done(() => twin.toggleOutputAvailable())
      default:
        throw new Error(`Unknown hardware action "${action}"`)
    }
  }

  // ---------------------------------------------------------------- status and logs

  status() {
    const c = this.config
    return {
      running: !!c,
      tier: c?.tier,
      broker: c && { ...c.broker, url: this.brokerUrl },
      appSmm: c && {
        kind: c.appSmm,
        running: c.appSmm === 'mock' ? !!this.mock?.running : c.appSmm === 'real' ? !!this.runner?.appSmm.status().running : undefined,
        mockState: this.mock?.systemState,
        mockFaults: c.appSmm === 'mock' ? this.mockFaults.length : undefined,
        process: this.runner?.appSmm.status(),
      },
      hardware: c && { kind: c.hardware, state: this.twin?.snapshot().state, connected: this.twin?.connected },
      trace: { size: this.trace.length, cap: this.traceCap, droppedThrough: this.traceDroppedThrough, lastId: this.traceSeq },
      processes: this.runner?.statuses() ?? [],
    }
  }

  getLogs(since = 0, source?: string): LogLine[] {
    return this.logs.filter((l) => l.id > since && (!source || l.source === source))
  }

  getTrace(since = 0): CopTraceEntry[] {
    return this.trace.filter((t) => t.id > since)
  }

  /** Id of the newest COP trace entry: pass it as `since` to look only at what comes next. */
  traceMark(): number {
    return this.traceSeq
  }

  /** Resolves with the first command `command` appSMM sends to the hardware after `since` (already traced or within timeoutMs). */
  waitForCommand(command: string, since: number, timeoutMs: number): Promise<CopTraceEntry> {
    const isIt = (t: CopTraceEntry) => t.id > since && t.way === 'rx' && commandMatches(t.name, command)
    const existing = this.trace.find(isIt)
    if (existing) return Promise.resolve(existing)
    return new Promise((resolve, reject) => {
      const onTrace = (t: CopTraceEntry) => {
        if (!isIt(t)) return
        clearTimeout(timer)
        this.off('trace', onTrace)
        resolve(t)
      }
      const timer = setTimeout(() => {
        this.off('trace', onTrace)
        const after = this.getTrace(since)
        const seen = [...new Set(after.filter((t) => t.way === 'rx').map((t) => t.name))].sort()
        reject(new CommandTimeoutError(
          `appSMM did not send ${command} to the hardware within ${timeoutMs} ms ` +
            `(trace after #${since}: ${after.length} entries; commands seen: ${seen.join(', ') || 'none'})`,
          { since, commandsSeen: seen, hardware: this.config?.hardware },
        ))
      }, timeoutMs)
      this.on('trace', onTrace)
    })
  }

  /** Resolves if appSMM sends no `command` to the hardware after `since` for durationMs; rejects with the command otherwise. */
  expectNoCommand(command: string, since: number, durationMs: number): Promise<void> {
    const isIt = (t: CopTraceEntry) => t.id > since && t.way === 'rx' && commandMatches(t.name, command)
    const existing = this.trace.find(isIt)
    if (existing) return Promise.reject(new CommandSentError(`appSMM sent ${command}`, { entry: traceOut(existing) }))
    return new Promise((resolve, reject) => {
      const onTrace = (t: CopTraceEntry) => {
        if (!isIt(t)) return
        clearTimeout(timer)
        this.off('trace', onTrace)
        reject(new CommandSentError(`appSMM sent ${command}`, { entry: traceOut(t) }))
      }
      const timer = setTimeout(() => {
        this.off('trace', onTrace)
        resolve()
      }, durationMs)
      this.on('trace', onTrace)
    })
  }
}

export class CommandTimeoutError extends Error {
  constructor(message: string, readonly details: Record<string, unknown>) {
    super(message)
  }
}

export class CommandSentError extends Error {
  constructor(message: string, readonly details: Record<string, unknown>) {
    super(message)
  }
}

function traceOut(t: CopTraceEntry) {
  return { id: t.id, time: new Date(t.time).toISOString(), way: t.way, name: t.name, text: t.text }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export function portOpen(host: string, port: number, timeoutMs = 1000): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new Socket()
    const done = (open: boolean) => {
      socket.destroy()
      resolve(open)
    }
    socket.setTimeout(timeoutMs, () => done(false))
    socket.once('error', () => done(false))
    socket.connect(port, host, () => done(true))
  })
}

async function waitForPort(host: string, port: number, timeoutMs: number, what: string): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await portOpen(host, port, 500)) return
    await sleep(250)
  }
  throw new Error(`${what} did not open ${host}:${port} within ${timeoutMs} ms`)
}
