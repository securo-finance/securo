import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { readdirSync } from 'node:fs'
import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'
import { resolveAppVersion } from './build/version.ts'

// Must match the statically bundled locales in src/lib/i18n.ts; every other
// file in src/locales/*.json is emitted as a lazy chunk and loaded on demand.
const BUNDLED_LOCALES = new Set(['en', 'pt-BR'])

function getLazyLocaleCodes(): string[] {
  const localesDir = path.resolve(import.meta.dirname, './src/locales')
  return readdirSync(localesDir)
    .filter((file) => file.endsWith('.json'))
    .map((file) => file.replace(/\.json$/, ''))
    .filter((code) => !BUNDLED_LOCALES.has(code))
    .sort()
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function getFrontendHost(frontendUrl?: string) {
  if (!frontendUrl) return []
  const withoutProto = frontendUrl.replace(/^https?:\/\//, '')
  return [withoutProto.split(/[:/]/)[0]]
}

export default defineConfig(async ({ mode }) => {
  const env = loadEnv(mode, import.meta.dirname, '')
  const frontendUrl = env.FRONTEND_URL || process.env.FRONTEND_URL
  const backendUrl = env.BACKEND_URL || process.env.BACKEND_URL
  const appVersionRoot = env.APP_VERSION_ROOT || process.env.APP_VERSION_ROOT
  const appVersion = await resolveAppVersion(
    appVersionRoot || import.meta.dirname,
    env.VITE_APP_VERSION || process.env.VITE_APP_VERSION,
  )
  const lazyLocaleCodes = getLazyLocaleCodes()
  const lazyLocaleUrlPattern = new RegExp(
    `/static/(?:${lazyLocaleCodes.map(escapeRegExp).join('|')})-[^/]+\\.js$`,
  )

  return {
    define: {
      __APP_VERSION__: JSON.stringify(appVersion),
    },
    build: {
      // Keep the existing per-chunk budget; optional locales load on demand.
      chunkSizeWarningLimit: 1000,
      // Emit hashed JS/CSS into `static/` instead of Vite's default `assets/`.
      // The default collides with our `/assets` SPA route: nginx's
      // `try_files $uri $uri/ /index.html` matches the real `dist/assets/`
      // directory before falling back to index.html, so a direct load or
      // refresh of `/assets` 301s into the build dir and renders a blank
      // page instead of booting the app (issue #295).
      assetsDir: 'static',
    },
    plugins: [
      react(),
      tailwindcss(),
      // Service worker (Workbox generateSW). The web manifest is the
      // hand-written public/manifest.json, so the plugin doesn't generate one.
      // The whole build output is precached, so the app opens offline right
      // after install. /api is never cached nor answered with the shell
      // fallback, so no financial data is stored offline.
      VitePWA({
        registerType: 'autoUpdate',
        manifest: false,
        workbox: {
          // `mjs` covers the PDF.js worker emitted into static/ (the attachment
          // preview needs it offline); `json` covers the hand-written
          // manifest.json copied from public/ (issue #201).
          globPatterns: ['**/*.{js,css,html,png,ico,mjs,json}'],
          // Optional locales load on demand in src/lib/i18n.ts; skip them in
          // the eager precache so every SW update doesn't download megabytes of
          // unused translations, and cache whichever locale the user picks via
          // runtimeCaching below.
          globIgnores: [
            '**/node_modules/**/*',
            'sw.js',
            'workbox-*.js',
            ...lazyLocaleCodes.map((code) => `static/${code}-*.js`),
          ],
          runtimeCaching: [
            {
              urlPattern: lazyLocaleUrlPattern,
              handler: 'CacheFirst',
              options: {
                cacheName: 'securo-locales',
                expiration: {
                  maxEntries: 32,
                  maxAgeSeconds: 60 * 60 * 24 * 365,
                },
                cacheableResponse: {
                  statuses: [0, 200],
                },
              },
            },
          ],
          // Workbox skips any asset over 2 MiB by default, and the PDF.js
          // worker is ~2.17 MiB, so the default would leave it out even with
          // the pattern above. Leave headroom above its current size.
          maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
          navigateFallback: '/index.html',
          navigateFallbackDenylist: [/^\/api\//, /^\/i\//],
          cleanupOutdatedCaches: true,
        },
      }),
    ],
    resolve: {
      alias: {
        '@': path.resolve(import.meta.dirname, './src'),
      },
    },
    server: {
      port: 5173,
      host: '0.0.0.0',
      allowedHosts: getFrontendHost(frontendUrl),
      proxy: {
        '/api': {
          target: backendUrl ?? 'http://localhost:8000',
          changeOrigin: true,
        },
      },
      watch: {
        usePolling: true,
      },
    },
  }
})
