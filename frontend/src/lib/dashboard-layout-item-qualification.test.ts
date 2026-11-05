import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { api, auth, dashboard } from '@/lib/api'
import type { DashboardLayout } from '@/lib/dashboard-layout'
import { getPageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

const ACTOR = '33333333-3333-4333-8333-333333333333'
const WORKSPACE = '11111111-1111-4111-8111-111111111111'
const TOKEN = 'synthetic-item-qualification-token'

function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}

// Independent literal layouts: no production resolver/sanitizer as the oracle.
const BASELINE: DashboardLayout = freeze({
  version: 1, workspaceId: WORKSPACE,
  items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
})
const DRAFT: DashboardLayout = freeze({
  version: 1, workspaceId: WORKSPACE,
  items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
})

function user(layout: unknown): User {
  // The GET boundary is intentionally unknown JSON, not a prequalified layout.
  return freeze({
    id: ACTOR, email: 'item-qualification@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', currency_display: 'USD', unrelated: { retained: true },
      dashboard_layouts: { [WORKSPACE]: layout },
    },
  }) as unknown as User
}

function withExtra(layout: DashboardLayout, location: 'item' | 'target') {
  const raw = structuredClone(layout)
  return freeze(location === 'target'
    ? { ...raw, future: { retained: true } }
    : { ...raw, items: raw.items.map((item, index) => index === 0
      ? { ...item, future: { retained: true } } : item) })
}

const originalAdapter = api.defaults.adapter
let storage: Map<string, string>
let calls: InternalAxiosRequestConfig[]
beforeEach(() => {
  storage = new Map([['token', TOKEN], ['workspace_id', WORKSPACE]])
  calls = []
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, value) },
    removeItem: (key: string) => { storage.delete(key) },
  })
})
afterEach(() => {
  api.defaults.adapter = originalAdapter
  vi.unstubAllGlobals()
})

function storeFor(seed: User) {
  return createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: 7, authSeed: seed,
    getTokenIdentity: () => storage.get('token') ?? null,
    getWorkspaceId: () => storage.get('workspace_id') ?? '',
    api: { me: auth.me, saveLayout: dashboard.saveLayout },
  })
}

function response(config: InternalAxiosRequestConfig, data: unknown, status = 200) {
  return { config, data: structuredClone(data), status, statusText: String(status), headers: {} }
}
function assertNativeRequests() {
  for (const call of calls) {
    expect(call.baseURL).toBe('/api')
    expect(call.headers.get('Authorization') === `Bearer ${TOKEN}`).toBe(true)
    expect(call.headers.get('X-Workspace-Id')).toBe(WORKSPACE)
    expect(call.url).toBe(call.method === 'get' ? '/users/me' : `/dashboard/layouts/${WORKSPACE}`)
  }
}

it.each(['item', 'target'] as const)('ordinary native GET with %s extras preserves display and qualifies only server-writable targets', async (location) => {
  const raw = withExtra(DRAFT, location)
  const seed = user(raw)
  const preimage = structuredClone(seed)
  api.defaults.adapter = (async (config) => {
    calls.push(config)
    if (config.method !== 'get') throw new Error('Unexpected write on initial qualification')
    return response(config, seed)
  }) satisfies AxiosAdapter
  const store = storeFor(seed)
  const lease = getPageSessionLease(store)
  const binding = lease.bind(WORKSPACE)
  try {
    expect(store.getSnapshot(WORKSPACE)).toMatchObject({ writable: false, layout: DRAFT })
    await binding.refreshDisplay()
    await lease.whenIdle()
    expect(store.getSnapshot(WORKSPACE)).toMatchObject({
      writable: location === 'target', provenance: location === 'target' ? 'saved' : 'foreign',
      layout: DRAFT, recovery: null, overlay: {}, pending: false, readPending: false,
    })
    expect(store.getPersistenceStatus(WORKSPACE)).toMatchObject({
      stale: location === 'item', canCustomize: location === 'target', unsaved: false,
    })
    if (location === 'item') expect(() => binding.open()).toThrow('cannot open')
    else {
      const ticket = binding.open()
      await binding.close(ticket, false)
    }
    expect(calls.map((call) => call.method)).toEqual(['get'])
    assertNativeRequests()
    expect(seed).toStrictEqual(preimage)
    expect(seed.preferences?.dashboard_layouts?.[WORKSPACE]).toStrictEqual(raw)
    expect(storage).toEqual(new Map([['token', TOKEN], ['workspace_id', WORKSPACE]]))
  } finally {
    store.revoke()
    binding.release()
    await lease.whenIdle()
    await store.whenIdle()
  }
})

const RECOVERY_CASES = [
  { location: 'item', matches: false },
  { location: 'item', matches: true },
  { location: 'target', matches: false },
] as const

it.each(RECOVERY_CASES.flatMap((target) => [
  { ...target, category: 'conflict' as const },
  { ...target, category: 'unknown' as const },
]))('native $category recovery with $location extras (matches=$matches) cannot authorize an unqualified replacement', async ({ location, matches, category }) => {
  const seed = user(BASELINE)
  const reviewed = user(withExtra(matches ? DRAFT : BASELINE, location))
  const preimage = structuredClone({ seed, reviewed, BASELINE, DRAFT })
  const failure = category === 'conflict'
    ? new AxiosError('Stored target conflict', AxiosError.ERR_BAD_REQUEST)
    : new AxiosError('Outcome unconfirmed', AxiosError.ERR_NETWORK)
  let reads = 0
  let writes = 0
  api.defaults.adapter = (async (config) => {
    calls.push(config)
    if (config.method === 'get') {
      reads += 1
      return response(config, reads === 1 ? seed : reviewed)
    }
    if (config.method !== 'put') throw new Error('Unexpected non-layout mutation')
    writes += 1
    if (category === 'conflict') failure.response = response(config, { detail: 'LAYOUT_TARGET_CONFLICT' }, 409)
    throw failure
  }) satisfies AxiosAdapter
  const store = storeFor(seed)
  const lease = getPageSessionLease(store)
  const binding = lease.bind(WORKSPACE)
  try {
    await binding.refreshDisplay()
    expect(store.getSnapshot(WORKSPACE).writable).toBe(true)
    const ticket = binding.open()
    store.setDraft(ticket.sessionId, DRAFT.items)
    const completion = binding.close(ticket, true)
    await expect(completion).rejects.toBe(failure)
    await lease.whenIdle()
    expect(writes).toBe(1)
    expect(reads).toBe(category === 'conflict' ? 2 : 1)
    const retained = store.getSnapshot(WORKSPACE).recovery
    if (!retained) throw new Error('Missing native retained failure')
    expect(retained).toMatchObject({ category, baseline: BASELINE, submitted: DRAFT })
    if (category === 'unknown') {
      const review = binding.reviewRecovery(retained.attemptId)
      await binding.recheckReviewedRecovery(review.ticket)
      await lease.whenIdle()
    }
    expect(reads).toBe(2)
    const review = binding.reviewRecovery(retained.attemptId)
    expect(review.recovery).toMatchObject({
      category, phase: location === 'item' ? 'protected' : 'review-ready',
      baseline: BASELINE, submitted: DRAFT,
    })
    expect(review.permissions).toStrictEqual({
      recheck: true, discard: true,
      reapply: location === 'target' && category === 'conflict',
      retry: location === 'target' && category === 'unknown',
    })
    if (location === 'item') {
      expect(review.recovery.recheck).toBeNull()
      const snapshot = store.getSnapshot(WORKSPACE)
      await expect(binding.replaceRecovery(review.ticket, 'reapply')).rejects.toThrow('replacement unavailable')
      await expect(binding.replaceRecovery(review.ticket, 'retry')).rejects.toThrow('replacement unavailable')
      expect(store.getSnapshot(WORKSPACE)).toBe(snapshot)
    } else {
      expect(review.recovery.recheck).toMatchObject({ provenance: 'saved', target: BASELINE, matchesSubmitted: false })
    }
    binding.discardRecovery(review.ticket)
    expect(store.getSnapshot(WORKSPACE)).toMatchObject({
      writable: location === 'target', recovery: null, layout: BASELINE, overlay: {},
      counters: { success: 0, suppressed: 0, conflict409: category === 'conflict' ? 1 : 0, other4xx: 0, server5xx: 0 },
    })
    if (location === 'item') {
      expect(() => binding.open()).toThrow('cannot open')
      // A later ordinary GET must not revive the discarded unqualified target.
      await binding.refreshDisplay()
      expect(store.getSnapshot(WORKSPACE)).toMatchObject({ writable: false, provenance: 'foreign', layout: matches ? DRAFT : BASELINE })
    }
    await lease.whenIdle()
    expect(writes).toBe(1)
    expect(reads).toBe(location === 'item' ? 3 : 2)
    const put = calls.filter((call) => call.method === 'put')
    expect(put).toHaveLength(1)
    expect(JSON.parse(put[0].data as string)).toStrictEqual(DRAFT)
    assertNativeRequests()
    await expect(completion).rejects.toBe(failure)
    expect({ seed, reviewed, BASELINE, DRAFT }).toStrictEqual(preimage)
    expect(storage).toEqual(new Map([['token', TOKEN], ['workspace_id', WORKSPACE]]))
  } finally {
    store.revoke()
    binding.release()
    await lease.whenIdle()
    await store.whenIdle()
  }
})
