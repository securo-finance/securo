import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import { resolveAppVersion } from './build/version.ts'

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

  return {
    define: {
      __APP_VERSION__: JSON.stringify(appVersion),
    },
    build: {
      // Translation resources are loaded eagerly so language changes remain
      // synchronous. The largest generated chunk is still below 1 MB raw
      // (roughly 306 KB gzip), so use that as the intentional warning budget.
      chunkSizeWarningLimit: 1000,
      // Emit hashed JS/CSS into `static/` instead of Vite's default `assets/`.
      // The default collides with our `/assets` SPA route: nginx's
      // `try_files $uri $uri/ /index.html` matches the real `dist/assets/`
      // directory before falling back to index.html, so a direct load or
      // refresh of `/assets` 301s into the build dir and renders a blank
      // page instead of booting the app (issue #295).
      assetsDir: 'static',
    },
    plugins: [react(), tailwindcss()],
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
