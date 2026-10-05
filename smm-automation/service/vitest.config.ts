import { defineConfig } from 'vitest/config'
// @ts-expect-error plain JS helper shared with build.mjs
import { aliasResolver, locateTestbench, withExtension } from './testbench.mjs'

const tb = locateTestbench()
const resolveAlias = aliasResolver(tb)

export default defineConfig({
  define: { __TESTBENCH__: JSON.stringify({ ...tb, builtAt: 'vitest' }) },
  plugins: [
    {
      name: 'smm-testbench-aliases',
      enforce: 'pre',
      resolveId(source: string, importer?: string) {
        const target = resolveAlias(source, importer)
        return target ? withExtension(target) : null
      },
    },
  ],
  server: { fs: { allow: [tb.dir, '.'] } },
  test: {
    include: ['test/**/*.test.ts'],
    testTimeout: 20000,
    hookTimeout: 20000,
    pool: 'forks',
  },
})
