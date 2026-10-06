import { EventEmitter } from 'node:events'
import mqtt, { type MqttClient } from 'mqtt'
import { FaultInjector, type Delivery, type MockFault } from './faults'

/**
 * Scripted stand-in for appSMM, for the "mock" tier: it lets the framework itself (service,
 * Robot library, suites, reports) be exercised in CI without appSMM.exe or hardware.
 *
 * It is NOT a test oracle for the product. Its behaviour follows the appSMM handlers
 * (InitializationRequestHandler: CanInit only in NotInitialized; RecoverRequestHandler: CanDeinit
 * only in E-Stop; ShutdownHandler: always E-Stop + OK; SystemStatesHandler: notifications only on
 * a change) and the 7251 SDS Workflow / Event Handling texts. Product verdicts come only from the
 * offline tier (real appSMM + hardware twin) and the HIL rig.
 */
export type SystemState = 'PowerOn' | 'NotInitialized' | 'Initializing' | 'Idle' | 'NormalOperation' | 'E-Stop' | 'Configuring' | 'Clearing'

export interface MockSmmOptions {
  brokerUrl: string
  powerUpMs?: number
  initializingMs?: number
  clearingMs?: number
  deinitMs?: number
  /** Fault rules for mutation testing (see faults.ts); none = the scripted behaviour. */
  faults?: MockFault[]
}

/** appSMM event id for "went to E-Stop because the connection to SMMBridge was lost" (ET 2855849). */
export const EVENT_BRIDGE_RECONNECTED = 15859714

const IW_TX = '/is/iw/tx'

export class MockAppSmm extends EventEmitter<{ log: [string] }> {
  private client?: MqttClient
  private state: SystemState = 'PowerOn'
  private timers = new Set<NodeJS.Timeout>()
  private bridgeLost = false
  private readonly o: Required<MockSmmOptions>
  private injector: FaultInjector
  /** Delayed publications (faults): not cancelled by E-Stop, only by stop(). */
  private faultTimers = new Set<NodeJS.Timeout>()
  private held: { delivery: Delivery; timer: NodeJS.Timeout }[] = []

  constructor(options: MockSmmOptions) {
    super()
    this.o = { powerUpMs: 1000, initializingMs: 1500, clearingMs: 800, deinitMs: 500, faults: [], ...options }
    this.injector = new FaultInjector(this.o.faults)
  }

  /** Replaces the fault rules (their counters restart). */
  setFaults(faults: MockFault[]): void {
    this.injector = new FaultInjector(faults)
    if (faults.length) this.emit('log', `fault rules active: ${JSON.stringify(faults)}`)
  }

  get faultStats() {
    return this.injector.stats()
  }

  get systemState(): SystemState {
    return this.state
  }

  get running(): boolean {
    return !!this.client
  }

  async start(): Promise<void> {
    if (this.client) return
    this.state = 'PowerOn'
    this.bridgeLost = false
    const client = mqtt.connect(this.o.brokerUrl, {
      clientId: `mock-appsmm-${Math.random().toString(16).slice(2, 8)}`,
      reconnectPeriod: 1000,
      will: { topic: IW_TX, payload: Buffer.from(JSON.stringify(env('ConnectionNotification', { Source: 'SMM', AnalyzerNumber: -1, Status: 'Disconnected' }))), qos: 0, retain: false },
    })
    this.client = client
    client.on('message', (topic, payload) => this.onMessage(topic, payload.toString('utf8')))
    let first = true
    client.on('connect', () => {
      client.subscribe(['/is/iw/rx', '/is/+/rx'])
      this.publish('ConnectionNotification', { Source: 'SMM', AnalyzerNumber: -1, Status: 'Connected' })
      if (first) {
        first = false
        // SDS 2525388 / ET 2431923: one SystemStatusNotification per state, PowerOn first.
        this.publish('SystemStatusNotification', { PreviousState: 'PowerOn', CurrentState: 'PowerOn' })
        this.after(this.o.powerUpMs, () => this.setState('NotInitialized'))
      } else if (this.bridgeLost) {
        // Broker was down: appSMM treats it as a lost Bridge (SDS 2854109).
        this.setState('E-Stop')
      }
    })
    client.on('close', () => {
      if (this.client === client && this.state !== 'PowerOn') this.bridgeLost = true
    })
    await new Promise<void>((resolve, reject) => {
      client.once('connect', () => resolve())
      client.once('error', reject)
    })
    this.emit('log', `mock appSMM connected to ${this.o.brokerUrl}`)
  }

  /** Stops like a killed process: no goodbye, the broker publishes the last will. */
  async stop(): Promise<void> {
    for (const t of [...this.timers, ...this.faultTimers, ...this.held.map((h) => h.timer)]) clearTimeout(t)
    this.timers.clear()
    this.faultTimers.clear()
    this.held = []
    const client = this.client
    this.client = undefined
    if (!client) return
    client.options.reconnectPeriod = 0
    ;(client as unknown as { stream?: { destroy(): void } }).stream?.destroy()
    await client.endAsync(true).catch(() => undefined)
    this.emit('log', 'mock appSMM stopped')
  }

  // ---------------------------------------------------------------- behaviour

  private onMessage(topic: string, raw: string): void {
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(raw)
    } catch {
      return
    }
    if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return
    const name = Object.keys(payload).find((k) => k !== 'Version')
    if (!name) return
    const body = (payload[name] ?? {}) as Record<string, unknown>
    switch (name) {
      case 'ConnectionNotification':
        if (body.Source !== 'SMMBridge') return
        if (body.Status === 'Disconnected' && this.state !== 'PowerOn') {
          this.bridgeLost = true
          this.setState('E-Stop')
        } else if (body.Status === 'Connected' && this.bridgeLost) {
          this.bridgeLost = false
          this.publish('EventNotification', {
            Category: 'Operator',
            EventId: EVENT_BRIDGE_RECONNECTED,
            Severity: 'Warning',
            TimeStamp: new Date().toISOString(),
            Message: 'The SMM system went to E-Stop because the connection to SMMBridge was lost. Connection is re-established now.',
            EventArgs: [],
          })
        }
        return
      case 'SystemStatusRequest':
        return this.publish('SystemStatusResponse', { CurrentState: this.state })
      case 'InitializationRequest':
        if (this.state !== 'NotInitialized') return this.publish('InitializationResponse', { Status: 'Error' })
        this.publish('InitializationResponse', { Status: 'OK' })
        return this.initialize()
      case 'ShutdownRequest':
        this.publish('ShutdownResponse', { Status: 'OK' })
        return this.setState('E-Stop')
      case 'RecoverRequest':
        if (this.state !== 'E-Stop') return this.publish('RecoverResponse', { Status: 'Error' })
        this.after(this.o.deinitMs, () => {
          this.publish('RecoverResponse', { Status: 'OK' })
          this.setState('NotInitialized')
          this.initialize()
        })
        return
      case 'SetConfigurationRequest': {
        // SDS 2427781 / 2551270: NotInitialized -> Configuring, SetConfigurationResponse, Configuring -> NotInitialized.
        const accepted = this.state === 'NotInitialized'
        const result = Object.fromEntries(Object.keys(body).map((k) => [k, accepted ? 'OK' : 'Error']))
        if (!accepted) return this.publish('SetConfigurationResponse', result)
        this.setState('Configuring')
        this.after(this.o.clearingMs, () => {
          this.publish('SetConfigurationResponse', result)
          this.setState('NotInitialized')
        })
        return
      }
      case 'GetVersionRequest':
        return this.publish('GetVersionResponse', { Integration: { Major: 0, Minor: 0, Build: 0, Revision: 0 }, Modules: [{ Name: 'mock-appSMM', Version: '0.0.0' }] })
      case 'TubeIdStatusRequest':
        return this.publish('TubeIdStatusResponse', { Status: this.state === 'E-Stop' ? 'Offline' : 'Online' })
      case 'InputLaneRequest':
      case 'OutputLaneRequest':
        return this.publish(name.replace('Request', 'Response'), {
          Module: { Status: this.state === 'Idle' ? 'Ready' : 'NotInitialized' },
          Tray: { Status: 'AbsentUnlocked' },
          Front: { Status: 'Empty' },
          Rear: { Status: 'Empty' },
        })
      case 'FrontLoadInRequest':
      case 'FrontLoadOutRequest':
        return this.publish(name.replace('Request', 'Response'), { Status: 'Empty' })
      case 'TrackStatusRequest':
        return this.publish('TrackStatusResponse', { TrackNumber: Number(body.TrackNumber ?? 0), Status: this.state === 'E-Stop' ? 'Offline' : 'Online' })
      default:
        return
    }
  }

  /** NotInitialized -> Initializing -> Idle -> Clearing -> Idle (SDS 2528698, 2528706, 2528703). */
  private initialize(): void {
    this.setState('Initializing')
    this.after(this.o.initializingMs, () => {
      if (this.state !== 'Initializing') return
      this.setState('Idle')
      this.setState('Clearing')
      this.after(this.o.clearingMs, () => this.state === 'Clearing' && this.setState('Idle'))
    })
  }

  private setState(next: SystemState): void {
    if (next === this.state) return
    const previous = this.state
    this.state = next
    if (next === 'E-Stop') {
      for (const t of this.timers) clearTimeout(t)
      this.timers.clear()
    }
    this.publish('SystemStatusNotification', { PreviousState: previous, CurrentState: next })
  }

  private after(ms: number, fn: () => void): void {
    const t = setTimeout(() => {
      this.timers.delete(t)
      fn()
    }, ms)
    this.timers.add(t)
  }

  private publish(name: string, body: unknown): void {
    for (const d of this.injector.deliveries(IW_TX, name, body)) {
      if (d.hold) {
        const timer = setTimeout(() => this.release(d), d.delayMs)
        this.held.push({ delivery: d, timer })
      } else if (d.delayMs > 0) {
        const timer = setTimeout(() => {
          this.faultTimers.delete(timer)
          this.send(d)
        }, d.delayMs)
        this.faultTimers.add(timer)
      } else {
        this.send(d)
        this.releaseHeld()
      }
    }
  }

  private send(d: Delivery): void {
    this.client?.publish(d.topic, d.payload)
  }

  /** reorder: held messages go out right after the next message. */
  private releaseHeld(): void {
    const held = this.held
    this.held = []
    for (const h of held) {
      clearTimeout(h.timer)
      this.send(h.delivery)
    }
  }

  private release(d: Delivery): void {
    this.held = this.held.filter((h) => h.delivery !== d)
    this.send(d)
  }
}

const env = (name: string, body: unknown) => ({ Version: 7, [name]: body })
