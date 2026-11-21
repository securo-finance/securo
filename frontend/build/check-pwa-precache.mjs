// Guards what the service worker precaches (issue #201).
//
// `vite build` writes `dist/sw.js` with the Workbox precache manifest inlined.
// Two things the app needs offline live outside the shell: the PDF.js worker
// (emitted as `static/pdf.worker-<hash>.mjs` and larger than Workbox's 2 MiB
// default limit) and the hand-written `manifest.json`. A glob pattern that
// stops matching them, or a size ceiling under the worker, silently drops
// them from the precache, and the failure only shows up offline. This check
// runs right after the build so that regression fails here instead.
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const distDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist')
const swPath = path.join(distDir, 'sw.js')

function fail(message) {
  console.error(`check-pwa-precache: ${message}`)
  process.exit(1)
}

let swSource
try {
  swSource = readFileSync(swPath, 'utf8')
} catch {
  fail(`no service worker at ${swPath}; build the frontend first`)
}

// Workbox writes `precacheAndRoute([{url:"...",revision:"..."},...],{})`.
const precacheCall = swSource.match(/precacheAndRoute\(\s*(\[[\s\S]*?\])/)
if (!precacheCall) {
  fail('could not read the precache manifest from sw.js')
}
const precachedUrls = [...precacheCall[1].matchAll(/url:\s*"([^"]+)"/g)].map((match) => match[1])

const workerFile = readdirSync(path.join(distDir, 'static')).find((name) => /^pdf\.worker-.*\.mjs$/.test(name))
if (!workerFile) {
  fail('no pdf.worker-*.mjs in dist/static; the PDF preview no longer ships a worker')
}

const missing = []
if (!precachedUrls.includes(`static/${workerFile}`)) missing.push(`static/${workerFile}`)
if (!precachedUrls.includes('manifest.json')) missing.push('manifest.json')
if (missing.length > 0) {
  fail(
    `${missing.join(' and ')} missing from the ${precachedUrls.length}-entry precache; ` +
      'check workbox.globPatterns and maximumFileSizeToCacheInBytes in vite.config.ts',
  )
}

console.log(`check-pwa-precache: ${precachedUrls.length} entries, including static/${workerFile} and manifest.json`)
