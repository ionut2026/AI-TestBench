// Locates the SMM TestBench sources (simulator + hwsim) the service is built from and
// resolves their import aliases. Used by build.mjs (esbuild) and vitest.config.ts (vite).
//
// The TestBench is the single source of truth for the Bridge engine, the ICD schemas and the
// hardware twin: nothing is copied, the code is bundled from source at the pinned commit.
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const lockFile = resolve(here, '../testbench.lock.json')

function git(dir, ...args) {
  try {
    return execFileSync('git', ['-C', dir, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch {
    return undefined
  }
}

/** Where the TestBench is, which commit it is on and whether that is the pinned one. */
export function locateTestbench() {
  const lock = JSON.parse(readFileSync(lockFile, 'utf8'))
  const dir = resolve(process.env.SMM_TESTBENCH_DIR || lock.defaultDir)
  const simulator = join(dir, 'simulator')
  const hwsim = join(dir, 'hwsim')
  for (const p of [join(simulator, 'src'), join(hwsim, 'src')]) {
    if (!existsSync(p)) throw new Error(`SMM TestBench not found at ${dir} (missing ${p}). Set SMM_TESTBENCH_DIR.`)
  }
  for (const app of [simulator, hwsim]) {
    if (!existsSync(join(app, 'node_modules'))) throw new Error(`Run "npm ci" in ${app} first (its dependencies are bundled from there).`)
  }
  const commit = git(dir, 'rev-parse', 'HEAD')
  const dirty = !!git(dir, 'status', '--porcelain', '--untracked-files=no')
  const version = (app) => JSON.parse(readFileSync(join(app, 'package.json'), 'utf8')).version
  return {
    dir,
    simulator,
    hwsim,
    commit,
    pinnedCommit: lock.commit,
    pinned: commit === lock.commit,
    dirty,
    simulatorVersion: version(simulator),
    hwsimVersion: version(hwsim),
  }
}

/** Refuses an unpinned TestBench unless explicitly allowed (keeps runs reproducible). */
export function assertPinned(tb) {
  if (tb.pinned) return
  const message = `SMM TestBench at ${tb.dir} is on ${tb.commit ?? 'an unknown commit'}, pinned is ${tb.pinnedCommit} (testbench.lock.json).`
  if (process.env.SMM_TESTBENCH_ALLOW_UNPINNED === '1') console.warn(`WARNING: ${message}`)
  else throw new Error(`${message} Update the lock file or set SMM_TESTBENCH_ALLOW_UNPINNED=1.`)
}

/**
 * Maps an import to a file path, or undefined to let the bundler resolve it.
 *   @tb/sim/x  -> <TestBench>/simulator/src/x
 *   @tb/hw/x   -> <TestBench>/hwsim/src/x
 *   @shared/x  -> the importing app's own src/shared/x (both apps use the same alias)
 */
export function aliasResolver(tb) {
  const inside = (file, root) => !!file && resolve(file).toLowerCase().startsWith((root + sep).toLowerCase())
  return (spec, importer) => {
    if (spec.startsWith('@tb/sim/')) return join(tb.simulator, 'src', spec.slice('@tb/sim/'.length))
    if (spec.startsWith('@tb/hw/')) return join(tb.hwsim, 'src', spec.slice('@tb/hw/'.length))
    if (spec.startsWith('@shared/')) {
      const app = inside(importer, tb.hwsim) ? tb.hwsim : tb.simulator
      return join(app, 'src', 'shared', spec.slice('@shared/'.length))
    }
    return undefined
  }
}

/** Adds the .ts extension the sources omit. */
export function withExtension(path) {
  for (const candidate of [path, `${path}.ts`, `${path}.tsx`, join(path, 'index.ts')]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate
  }
  return path
}
