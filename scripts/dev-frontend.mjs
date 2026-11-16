#!/usr/bin/env node
/**
 * Frontend dev watcher — rebuilds on file change and auto-deploys to Docker.
 *
 * Usage:  node scripts/dev-frontend.mjs
 *         bun run dev:frontend   (once the script is added to package.json)
 */
import { spawn, execSync } from 'child_process'
import { watch } from 'fs'
import { resolve, join } from 'path'
import { existsSync } from 'fs'

const ROOT = new URL('..', import.meta.url).pathname
const FRONTEND_DIR = join(ROOT, 'frontend')
const DIST_DIR = join(FRONTEND_DIR, 'dist')
const CONTAINER = 'securo-frontend-1'

function deploy() {
  try {
    execSync(`docker cp ${DIST_DIR}/. ${CONTAINER}:/usr/share/nginx/html/`, { stdio: 'pipe' })
    execSync(`docker exec ${CONTAINER} nginx -s reload`, { stdio: 'pipe' })
    const time = new Date().toLocaleTimeString('pt-BR')
    console.log(`\x1b[32m✓ Deployed to Docker [${time}]\x1b[0m`)
  } catch (e) {
    console.error('\x1b[31m✗ Deploy failed:\x1b[0m', e.message)
  }
}

// Debounce rapid successive builds
let deployTimer = null
function scheduleDeploy() {
  if (deployTimer) clearTimeout(deployTimer)
  deployTimer = setTimeout(deploy, 300)
}

console.log('\x1b[36m→ Starting Vite watch build + Docker auto-deploy\x1b[0m')
console.log(`  Container: ${CONTAINER}`)
console.log(`  Watching:  ${FRONTEND_DIR}/src\n`)

// Start vite build --watch
const vite = spawn('bun', ['run', 'build', '--watch'], {
  cwd: FRONTEND_DIR,
  stdio: ['inherit', 'pipe', 'inherit'],
  shell: true,
})

// Watch vite stdout for build-complete signal
vite.stdout.on('data', (data) => {
  const line = data.toString()
  process.stdout.write(line)
  if (line.includes('built in') || line.includes('✓')) {
    scheduleDeploy()
  }
})

vite.on('error', (err) => console.error('Vite error:', err.message))
vite.on('exit', (code) => {
  console.log(`Vite exited with code ${code}`)
  process.exit(code ?? 0)
})

process.on('SIGINT', () => {
  console.log('\nStopping...')
  vite.kill()
  process.exit(0)
})
