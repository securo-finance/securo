import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  define: {
    // vite.config.ts injects this from the package version at build time.
    // Without it here, anything importing lib/build-info throws on import,
    // which takes the whole app shell down with it.
    __APP_VERSION__: JSON.stringify('0.0.0-test'),
  },
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  test: {
    // jsdom for everything, not only the component tests. The pure-function
    // suites under src/lib run fine in it and keeping one environment means a
    // new test file works wherever it is dropped, with no per-file docblock to
    // forget.
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Vite serves the app's CSS through Tailwind; none of it affects what the
    // tests assert, so skip the transform and keep the suite fast.
    css: false,
  },
})
