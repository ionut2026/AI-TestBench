import { AutomationService, API_VERSION } from './api/server'
import { testbenchInfo } from './testbench'

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : undefined
}

const port = Number(arg('port') ?? process.env.SMM_AUTOMATION_PORT ?? 8765)
const host = arg('host') ?? process.env.SMM_AUTOMATION_HOST ?? '127.0.0.1'
const testbenchDir = arg('testbench') ?? process.env.SMM_TESTBENCH_DIR ?? testbenchInfo.dir

const service = new AutomationService({ testbenchDir })
service.env.on('log', (l) => {
  if (process.env.SMM_AUTOMATION_QUIET !== '1') console.log(`[${new Date(l.time).toISOString()}] ${l.source}: ${l.line}`)
})

const server = await service.listen(port, host)
const tb = testbenchInfo
console.log(`SMM automation service ${API_VERSION} on http://${host}:${port}/api/v1`)
console.log(`SMM TestBench ${tb.commit?.slice(0, 12) ?? '?'}${tb.dirty ? ' (dirty)' : ''}${tb.pinned ? '' : ` (NOT the pinned ${tb.pinnedCommit.slice(0, 12)})`} from ${testbenchDir}`)

let stopping = false
async function shutdown(signal: string) {
  if (stopping) return
  stopping = true
  console.log(`${signal}: stopping`)
  const force = setTimeout(() => process.exit(1), 15000)
  force.unref()
  server.close()
  await service.shutdown().catch((err) => console.error(err))
  process.exit(0)
}
process.on('SIGINT', () => void shutdown('SIGINT'))
process.on('SIGTERM', () => void shutdown('SIGTERM'))
