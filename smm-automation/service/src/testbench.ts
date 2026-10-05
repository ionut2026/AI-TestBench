// The only place the service touches SMM TestBench code. Everything below is bundled from the
// TestBench sources at the pinned commit (see testbench.lock.json), so the automation service and
// the Testbench UI always run the same Bridge engine, ICD schemas and hardware twin.

// ---- Bridge side (simulator)
export { BeaconEngine } from '@tb/sim/main/beacon/beaconEngine'
export { MqttLink } from '@tb/sim/main/mqtt/mqttLink'
export { SchemaRegistry } from '@tb/sim/main/icd/schemaRegistry'
export { Timeline } from '@tb/sim/main/timeline'
export { envelope, topicToSmm, messageNameOf, analyzerOfTopic, MESSAGES, TOPICS, ICD_SCHEMA_VERSION } from '@tb/sim/shared/icd'
export { PairChecker, type PairIssue } from '@tb/sim/shared/pairing'
export type {
  AnalyzerSettings,
  BeaconSettings,
  ConnectionSettings,
  LinkStatus,
  TimelineEntry,
  ValidationResult,
} from '@tb/sim/shared/types'

// ---- Hardware side (hwsim): digital twin of rtc_appl, COP server, appSMM/Mosquitto runner
export { HardwareTwin } from '@tb/hw/main/twin/twin'
export { CopServer } from '@tb/hw/main/cop/cop'
export { IcolCodec } from '@tb/hw/main/icol/codec'
export { IcolCatalog } from '@tb/hw/main/icol/model'
export { Runner, defaultRunnerSettings } from '@tb/hw/main/runner/appSmm'
export { defaultSettings as defaultHwSettings, defaultTrays, defaultRack } from '@tb/hw/shared/hwTypes'
export type { HwSettings, RackConfig, TrayConfig, CopTraceEntry } from '@tb/hw/shared/hwTypes'
export type { RunnerSettings, ProcessStatus } from '@tb/hw/shared/api'

export interface TestbenchInfo {
  dir: string
  commit?: string
  pinnedCommit: string
  pinned: boolean
  dirty: boolean
  simulatorVersion: string
  hwsimVersion: string
  builtAt: string
}

declare const __TESTBENCH__: TestbenchInfo
export const testbenchInfo: TestbenchInfo = __TESTBENCH__
