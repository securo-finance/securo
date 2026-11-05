import { afterEach, beforeEach, expect, it, vi } from 'vitest'
// Qualify every real route used below before the per-case behavior budget.
// resetModules still rebuilds App/SDK state under the native transport spies.
import '@/App'
import '@/pages/dashboard'
import '@/pages/categories'
import type { AxiosAdapter, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import type { QueryClient } from '@tanstack/react-query'
import type { SessionStore, StoreOptions } from '@/lib/dashboard-layout-session'
import type { CommittedDashboardLayout, DashboardLayout } from '@/lib/dashboard-layout'
import type { DashboardSummary, User, Workspace } from '@/types'

// TEST_AUTHOR_ONLY / TEST_DATA. NOT_RUN: see the accompanying 1ba9 receipt.
// Only transport changes behavior. Every spy below is native passthrough.
// No renderWithProviders, reconstructed routes, extra owner, or observation lease.
// SOURCE HOLD: App currently omits the provider and the owner hook throws. The
// requested first visible-Customize oracle cannot be reached on those bytes;
// do not suppress that exception or label it a qualified disabled-button RED.
const ACTOR = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const WS = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const TOKEN = 'synthetic-actual-app-persistence-test-data'
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}
function required<T>(value: T | null | undefined, label: string): T {
  if (value === null || value === undefined) throw new Error(`Missing actual-App witness: ${label}`)
  return value
}
function expectFrozen(value: unknown): void {
  if (value !== null && typeof value === 'object') {
    expect(Object.isFrozen(value)).toBe(true)
    Object.values(value).forEach(expectFrozen)
  }
}
const P0: DashboardLayout = freeze({ version: 1, workspaceId: WS, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const P1: DashboardLayout = freeze({ version: 1, workspaceId: WS, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: false, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const P2: DashboardLayout = freeze({ version: 1, workspaceId: WS, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: false, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const FRESH: DashboardLayout = freeze({ version: 1, workspaceId: WS, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: false, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
// RAW envelopes, maps, leaves and commits are independently literal: never
// request-derived, resolver-derived, or assembled from a golden layout.
const seed: User = freeze({ id: ACTOR, email: 'actual-app-seed@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', onboarding_completed: true, dashboard_layouts: { [WS]: {
    version: 1, workspaceId: WS, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } } },
})
const postUser: User = freeze({ id: ACTOR, email: 'actual-app-post@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', onboarding_completed: true, dashboard_layouts: { [WS]: {
    version: 1, workspaceId: WS, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } } },
})
const serverUser: User = freeze({ id: ACTOR, email: 'actual-app-reviewed@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', onboarding_completed: true, dashboard_layouts: { [WS]: {
    version: 1, workspaceId: WS, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } } },
})
const freshUser: User = freeze({ id: ACTOR, email: 'actual-app-fresh@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', onboarding_completed: true, dashboard_layouts: { [WS]: {
    version: 1, workspaceId: WS, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: false, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } } },
})
const committed: CommittedDashboardLayout = freeze({ workspaceId: WS, layout: {
  version: 1, workspaceId: WS, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const freshCommit: CommittedDashboardLayout = freeze({ workspaceId: WS, layout: {
  version: 1, workspaceId: WS, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: false, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const workspace: Workspace = freeze({ id: WS, name: 'TEST_DATA actual App workspace', kind: 'personal',
  timezone: null,
  is_archived: false, default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
  icon: null, color: null, created_at: '2026-10-06T00:00:00Z', created_by_user_id: ACTOR,
  managed_by_user_id: null, role: 'owner', enabled_modules: ['accounts', 'transactions', 'goals', 'categories'],
})
const summary: DashboardSummary = freeze({ primary_currency: 'USD', balance_date: '2026-10-31',
  accounts_count: 0, pending_shares_net: 0, total_balance: { USD: 1000 }, total_balance_primary: 1000,
  projected_balance: { USD: 1000 }, projected_balance_primary: 1000,
  assets_value: { USD: 1200 }, assets_value_primary: 1200,
  monthly_income: 500, monthly_income_primary: 500, monthly_expenses: 300, monthly_expenses_primary: 300,
  projected_income: 500, projected_income_primary: 500, projected_expenses: 300, projected_expenses_primary: 300,
  pending_categorization: 0, pending_categorization_amount: 0,
})
const startupReplies = freeze({
  '/workspaces': [workspace], '/collections': [], '/accounts': [],
  '/dashboard/summary': summary, '/dashboard/spending-by-category': [],
  '/dashboard/balance-history/months': [],
  '/dashboard/balance-history': { current: [{ day: 1, balance: 1000 }], opening_balance: 900, baseline: [], comparison: null, comparison_month: null, comparison_opening_balance: null },
  '/dashboard/projected-transactions': [], '/transactions': { items: [], total: 0, page: 1, size: 500 },
  '/groups': [], '/budgets/comparison': [], '/categories': [], '/category-groups': [],
  '/payees': [], '/goals/summary': [], '/admin/number-format': { format: 'auto' },
  '/admin/date-format': { format: 'auto' }, '/admin/accounting-mode': { mode: 'cash' },
  '/admin/default-colors': { light: null, dark: null },
  '/info': { features: { agents: false } },
  '/auth/oidc/config': { enabled: false, provider_name: 'TEST_DATA', local_auth_enabled: true },
})
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })

type ScenarioPhase = 'setup' | 'startup' | 'ordinary-save' | 'failed-save' | 'review' | 'replacement-save' | 'fresh-save' | 'cleanup'
type ScenarioPorts = { cleaning: boolean; issued: number; unsettled: number; readPending?: boolean | null; postreadIssued?: boolean; postreadSettled?: boolean }
type ScenarioEvent = 'abort' | 'failure' | 'cleanup' | 'joined' | 'replacement-held' | 'replacement-postread'
type ScenarioSample = ScenarioPorts & { event: ScenarioEvent; elapsedMs: number; behaviorElapsedMs: number | null; phase: ScenarioPhase; retired: boolean }
type ScenarioOwner = { retired: boolean; retire?: () => void; disconnect?: () => void; phase: ScenarioPhase; bodyStartedAt?: number; inspect?: () => ScenarioPorts; readPending?: () => boolean; diagnosticUnavailable?: boolean; record(event: ScenarioEvent): void }
let active: { iterator: ReturnType<typeof nativeScenario>; step: Promise<IteratorResult<void>>; owner: ScenarioOwner } | undefined

beforeEach(async ({ signal, task, onTestFinished }) => {
  if (active) throw new Error('Previous native scenario is still owned')
  const startedAt = performance.now(), samples: ScenarioSample[] = []
  const owner: ScenarioOwner = { retired: false, phase: 'setup', record(event) {
    // Only our observer is guarded; original assertions/errors remain untouched.
    try {
      if (samples.length >= 6) return
      const now = performance.now()
      samples.push({ event, elapsedMs: now - startedAt,
        behaviorElapsedMs: owner.bodyStartedAt === undefined ? null : now - owner.bodyStartedAt,
        phase: owner.phase, retired: owner.retired,
        ...(owner.inspect?.() ?? { cleaning: false, issued: 0, unsettled: 0 }) })
    } catch { owner.diagnosticUnavailable = true }
  } }
  const onAbort = () => { owner.record('abort') }
  signal.addEventListener('abort', onAbort, { once: true })
  // Registered early: the later joined-error callback runs first in stack order.
  onTestFinished(() => {
    signal.removeEventListener('abort', onAbort)
    if (task.result?.state !== 'fail' || vi.isMockFunction(console.error) || vi.isMockFunction(console.warn)) return
    try {
      console.error('ACTUAL_APP_DIAGNOSTIC', JSON.stringify({ case: task.name, budgetMs: 20_000,
        elapsedMs: performance.now() - startedAt, phase: owner.phase, retired: owner.retired,
        diagnosticUnavailable: owner.diagnosticUnavailable === true, samples }))
    } catch { owner.diagnosticUnavailable = true }
  })
  const iterator = nativeScenario(signal, owner)
  const step = iterator.next()
  active = { iterator, step, owner }
  await step
})
afterEach(async ({ signal, onTestFinished }) => {
  const owned = active
  if (!owned) return
  // A runner timeout does not cancel an async body. Retire its ports, then join
  // that exact step before global DOM cleanup or a successor captures its spies.
  if (signal.aborted && !owned.owner.retired) {
    owned.owner.retired = true
    owned.owner.retire?.()
  }
  try {
    const progress = await owned.step
    if (!progress.done) await owned.iterator.throw(signal.reason ?? new Error('Native scenario did not run'))
  } catch (error: unknown) {
    // Joining is complete. Report its error after shared cleanup rather than
    // aborting the remaining afterEach hooks (including global DOM cleanup).
    onTestFinished(() => { throw error })
  } finally { owned.owner.record('joined'); active = undefined }
})

it('actual App retains an unknown dashboard save across native navigation until reviewed discard admits a distinct fresh save', async () => {
  const owned = required(active, 'prepared native scenario')
  owned.step = owned.iterator.next()
  await owned.step
}, 20_000)

// Only fresh prerequisite evaluation runs in beforeEach. The yield is BEFORE
// render; the named test owns all native behavior, assertions and finally work.
async function* nativeScenario(signal: AbortSignal, owner: ScenarioOwner): AsyncGenerator<void, void, void> {
  let inCleanup = false
  const checkActive = () => {
    if (inCleanup) return
    if (owner.retired) throw signal.reason ?? new Error('Native scenario retired')
    signal.throwIfAborted()
  }
  async function prepared<T>(work: Promise<T>): Promise<T> {
    checkActive()
    const value = await work
    checkActive()
    return value
  }
  vi.resetModules()
  const diagnostics: unknown[] = [], violations: unknown[] = [], asynchronous: unknown[] = [], consoleCalls: unknown[] = []
  const restores: Array<() => void | Promise<void>> = []
  const inputs = [P0, P1, P2, FRESH, seed, postUser, serverUser, freshUser, committed, freshCommit, workspace, summary, startupReplies]
  const preimages = structuredClone(inputs)
  const oldStorage = Array.from({ length: localStorage.length }, (_, index) => {
    const key = required(localStorage.key(index), 'storage key'); return [key, required(localStorage.getItem(key), key)] as const
  })
  const oldLocation = window.location.href, oldHistory = window.history.state as unknown
  const html = ['lang', 'class', 'style'].map((name) => [name, document.documentElement.getAttribute(name)] as const)
  restores.push(() => {
    localStorage.clear(); oldStorage.forEach(([key, value]) => { localStorage.setItem(key, value) })
    window.history.replaceState(oldHistory, '', oldLocation)
    html.forEach(([name, value]) => {
      if (value === null) document.documentElement.removeAttribute(name)
      else document.documentElement.setAttribute(name, value)
    })
  })
  let primary: unknown, failed = false, nativeCleanup: (() => Promise<void>) | null = null
  try {
    for (const method of ['warn', 'error'] as const) {
      const original = console[method].bind(console)
      const spy = vi.spyOn(console, method).mockImplementation((...args: unknown[]) => {
        consoleCalls.push({ method, args }); original(...args)
      })
      restores.push(() => { spy.mockRestore() })
    }
    const onError = (event: ErrorEvent) => { asynchronous.push(event.error ?? event.message) }
    const onRejection = (event: PromiseRejectionEvent) => { asynchronous.push(event.reason) }
    window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
    restores.push(() => { window.removeEventListener('error', onError); window.removeEventListener('unhandledrejection', onRejection) })
    const axiosModule = await prepared(import('axios')), axios = axiosModule.default
    const priorAdapter = axios.defaults.adapter
    const calls: InternalAxiosRequestConfig[] = [], tasks: Promise<void>[] = []
    let cleaning = false, http: AxiosInstance | null = null
    function slot(data: unknown) {
      let resolve!: (value: AxiosResponse<unknown>) => void, reject!: (error: unknown) => void
      const task = new Promise<AxiosResponse<unknown>>((yes, no) => { resolve = yes; reject = no })
      return { task, data, config: null as InternalAxiosRequestConfig | null, settled: false, error: undefined as unknown,
        settle() {
          if (!this.config || this.settled) return
          this.settled = true
          if (this.error !== undefined) reject(this.error)
          else resolve({ data: this.data, status: 200, statusText: 'OK', headers: {}, config: this.config })
        } }
    }
    // Auth restoration, ordinary entry, post-save, EXPLICIT reviewed read,
    // fresh post-save. Entry automatically settles: never await a missing owner GET.
    const reads = [slot(seed), slot(seed), slot(postUser), slot(serverUser), slot(freshUser)]
    const puts = [slot(committed), slot(undefined), slot(freshCommit)]
    const ports = [...reads, ...puts]
    owner.inspect = () => ({ cleaning, issued: ports.filter((port) => port.config !== null).length,
      unsettled: ports.filter((port) => port.config !== null && !port.settled).length })
    owner.retire = () => { cleaning = true; ports.forEach((port) => { port.settle() }) }
    let originalError: InstanceType<typeof axiosModule.AxiosError> | null = null
    const userGets = () => calls.filter((config) => config.method === 'get' && config.url === '/users/me')
    const layoutPuts = () => calls.filter((config) => config.method === 'put' && config.url === `/dashboard/layouts/${WS}`)
    const totals = (history = calls) => history.reduce<Record<string, number>>((counts, config) => {
      const suffix = config.params?.include_hidden === true ? '?include_hidden=true' : ''
      const key = `${config.method}:${config.url}${suffix}`
      counts[key] = (counts[key] ?? 0) + 1; return counts
    }, {})
    function forbid(label: string) {
      const error = new Error(`Actual-App TEST_DATA transport violation: ${label}`)
      violations.push(error); return Promise.reject<AxiosResponse<unknown>>(error)
    }
    const adapter: AxiosAdapter = (config) => {
      calls.push(config)
      let task: Promise<AxiosResponse<unknown>>
      if (config.baseURL !== '/api' || config.headers.get('Authorization') !== `Bearer ${TOKEN}` ||
          config.headers.get('X-Workspace-Id') !== WS) task = forbid('native SDK scope/headers')
      else if (config.method === 'get' && config.url === '/users/me' && config.data === undefined) {
        const index = userGets().length - 1, port = reads[index]
        if (!port || port.config) task = forbid('extra/reused users/me GET')
        else { port.config = config; task = port.task; if (index === 1 || cleaning) port.settle() }
      } else if (config.method === 'put' && config.url === `/dashboard/layouts/${WS}`) {
        const index = layoutPuts().length - 1, port = puts[index]
        if (!port || port.config) task = forbid('extra/reused PUT')
        else {
          port.config = config
          if (index === 1) {
            originalError = new axiosModule.AxiosError('TEST_DATA exact unknown network failure', axiosModule.AxiosError.ERR_NETWORK, config)
            port.error = originalError
          }
          task = port.task; if (cleaning) port.settle()
        }
      } else if (config.method === 'get' && config.data === undefined && config.url && Object.hasOwn(startupReplies, config.url)) {
        task = Promise.resolve({ data: Reflect.get(startupReplies, config.url), status: 200, statusText: 'OK', headers: {}, config })
      } else task = forbid(`unexpected ${config.method}:${config.url}`)
      tasks.push(task.then(() => {}, () => {})); return task
    }
    axios.defaults.adapter = adapter
    restores.push(() => { axios.defaults.adapter = priorAdapter })
    expect(vi.isMockFunction(axios.create)).toBe(false)
    const create = axios.create.bind(axios)
    const createSpy = vi.spyOn(axios, 'create').mockImplementation((config) => {
      const instance = create(config); http = instance
      restores.push(() => { instance.defaults.adapter = priorAdapter }); return instance
    })
    restores.push(() => { createSpy.mockRestore() })
    const { act, render, screen, within, waitFor } = await prepared(import('@testing-library/react'))
    const { default: userEvent } = await prepared(import('@testing-library/user-event'))
    const query = await prepared(import('@tanstack/react-query'))
    // Observe App's actual module client through its genuine mount call. No
    // exported constructor replacement, nested client, or default option edits.
    const mountSpy = vi.spyOn(query.QueryClient.prototype, 'mount')
    restores.push(() => { mountSpy.mockRestore() })
    const session = await prepared(import('@/lib/dashboard-layout-session'))
    const factorySpy = vi.spyOn(session, 'createDashboardLayoutSession')
    restores.push(() => { factorySpy.mockRestore() })
    const { getPageSessionLease } = await prepared(import('@/lib/dashboard-layout-page-session-lease'))
    const { getDashboardAuthSnapshot } = await prepared(import('@/lib/dashboard-layout-auth-bridge'))
    const { validateDashboardLayoutRequest, validateCommittedDashboardLayout } = await prepared(import('@/lib/dashboard-layout'))
    const { DASHBOARD_WIDGETS } = await prepared(import('@/components/dashboard/widgets'))
    const api = await prepared(import('@/lib/api'))
    const saveSpy = vi.spyOn(api.dashboard, 'saveLayout'), meSpy = vi.spyOn(api.auth, 'me')
    const patchSpy = vi.spyOn(api.auth, 'updateMe'), colorsSpy = vi.spyOn(api.admin, 'defaultColors')
    restores.push(() => { saveSpy.mockRestore(); meSpy.mockRestore(); patchSpy.mockRestore(); colorsSpy.mockRestore() })
    const { default: i18n, i18nReady } = await prepared(import('@/lib/i18n'))
    await prepared(i18nReady)
    const oldLanguage = i18n.language
    restores.push(async () => { await i18n.changeLanguage(oldLanguage) })
    await prepared(i18n.changeLanguage('en'))
    const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options) as string
    const { currentMonth } = await prepared(import('@/lib/month-utils'))
    const month = currentMonth() // Source-fit real logo return, not a fake clock.
    localStorage.setItem('token', TOKEN); localStorage.setItem(api.WORKSPACE_STORAGE_KEY, WS)
    localStorage.setItem('privacyMode', 'false'); localStorage.setItem('theme', 'dark')
    localStorage.setItem('securo.sidebar.collapsed', 'false')
    localStorage.setItem('updateCheckEnabled', 'false') // Real user setting; prevents external release fetch.
    localStorage.removeItem(`securo.activeCollection.${WS}`)
    window.history.replaceState(null, '', `/?month=${month}`)
    const { default: App } = await prepared(import('@/App'))
    // Qualify the real lazy dashboard's transitive module transform before
    // mounting App; keep its native lazy route, Suspense and all providers.
    await prepared(import('@/pages/dashboard'))
    const user = userEvent.setup()
    let root: { unmount(): void; container: HTMLElement } | null = null, client: QueryClient | null = null
    const stores = new Map<SessionStore, StoreOptions>(), unsubscribes: Array<() => void> = []
    const observedPromises = new Set<Promise<unknown>>()
    let publications = 0, queryEvents = 0
    function discoverNativeDiagnostics() {
      for (const context of mountSpy.mock.contexts) {
        const mountedClient = context as QueryClient
        if (!client) {
          client = mountedClient
          unsubscribes.push(client.getQueryCache().subscribe(() => { queryEvents += 1 }))
        } else expect(mountedClient).toBe(client)
      }
      factorySpy.mock.results.forEach((result, index) => {
        if (result.type !== 'return') return
        const store = result.value as SessionStore
        if (!stores.has(store)) {
          stores.set(store, required(factorySpy.mock.calls[index]?.[0], 'factory options'))
          unsubscribes.push(store.subscribe(() => { publications += 1 }))
        }
      })
      for (const spy of [meSpy, saveSpy]) for (const result of spy.mock.results) {
        if (result.type !== 'return') continue
        const promise = result.value as Promise<unknown>
        if (!observedPromises.has(promise)) {
          observedPromises.add(promise); tasks.push(promise.then(() => {}, () => {}))
        }
      }
    }
    async function flush() {
      checkActive()
      await act(async () => { await tick(); await tick(); discoverNativeDiagnostics() })
      checkActive()
    }
    async function drain() {
      checkActive()
      for (let turn = 0; turn < 16; turn += 1) {
        discoverNativeDiagnostics()
        const before = [calls.length, tasks.length, stores.size, publications, queryEvents]
        await act(async () => {
          await tick(); discoverNativeDiagnostics(); await Promise.all(tasks)
          for (const store of stores.keys()) { await getPageSessionLease(store).whenIdle(); await store.whenIdle() }
          await tick(); await tick(); discoverNativeDiagnostics()
        })
        checkActive()
        if (client) await waitFor(() => { expect(client?.isFetching()).toBe(0); expect(client?.isMutating()).toBe(0) })
        const after = [calls.length, tasks.length, stores.size, publications, queryEvents]
        if (after.every((value, index) => value === before[index])) return
      }
      throw new Error('Actual-App native transport/query/store/lease fixed point not reached')
    }
    function cleanEvidence() {
      expect(violations).toStrictEqual([]); expect(asynchronous).toStrictEqual([]); expect(consoleCalls).toStrictEqual([])
      expect(inputs).toStrictEqual(preimages); inputs.forEach(expectFrozen)
      expect(patchSpy).not.toHaveBeenCalled()
      expect(calls.filter((config) => config.method !== 'get' && config.method !== 'put')).toStrictEqual([])
    }
    nativeCleanup = async () => {
      async function step(action: () => void | Promise<void>) {
        try { await action() } catch (error: unknown) { diagnostics.push(error) }
      }
      cleaning = true
      await step(async () => { await act(async () => { ports.forEach((port) => { port.settle() }); await tick(); await tick() }) })
      await step(drain)
      const beforeUnmount = [...calls]
      await step(async () => { await act(async () => { root?.unmount(); root?.container.remove(); root = null; await tick(); await tick() }) })
      await step(drain)
      await step(() => {
        expect(calls).toStrictEqual(beforeUnmount)
        ports.filter((port) => port.config !== null).forEach((port) => { expect(port.settled).toBe(true) })
        cleanEvidence()
      })
      for (const unsubscribe of unsubscribes) await step(unsubscribe)
      await step(() => { client?.clear() })
      await step(drain)
    }
    yield
    owner.bodyStartedAt = performance.now()
    owner.phase = 'startup'
    checkActive()
    // Native App supplies ALL providers, routing, lazy pages and Toaster.
    root = render(<App />, { reactStrictMode: false })
    await waitFor(() => expect(reads[0]!.config).not.toBeNull())
    await act(async () => { reads[0]!.settle(); await tick(); await tick() })
    await screen.findByText(workspace.name)
    await screen.findByText(t('dashboard.spendingByCategory'))
    await drain(); await flush()
    // First prospective FUNCTIONAL oracle after native Auth/workspace/dashboard
    // qualification. Do NOT wait for reads[1], a missing owner or factory count.
    // Current source throws before this point: record prerequisite HOLD honestly.
    const customizeName = t('dashboard.customize.action'), editorName = t('dashboard.customize.title')
    expect(screen.getByRole('button', { name: customizeName })).toBeEnabled()

    const realClient = required<QueryClient>(client, 'App module QueryClient')
    expect(createSpy).toHaveBeenCalledTimes(1)
    expect(required<AxiosInstance>(http, 'native SDK instance').defaults.adapter).toBe(adapter)
    expect(factorySpy).toHaveBeenCalledTimes(1)
    const store = required([...stores.keys()][0], 'App-owned store')
    const options = required(stores.get(store), 'App-owned options')
    const bridge = getDashboardAuthSnapshot(realClient)
    const generation = options.authGeneration
    function stableIdentity() {
      expect([...stores.keys()]).toStrictEqual([store]); expect(factorySpy).toHaveBeenCalledTimes(1)
      expect(options.actorId).toBe(ACTOR); expect(options.authSeed).toBe(seed)
      expect(options.getTokenIdentity()).toBe(TOKEN); expect(options.getWorkspaceId()).toBe(WS)
      expect(getDashboardAuthSnapshot(realClient)).toBe(bridge)
      expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(api.WORKSPACE_STORAGE_KEY)).toBe(WS)
      expect(screen.getByText(seed.email)).toBeVisible()
      expect(screen.queryByText(postUser.email)).not.toBeInTheDocument()
      expect(screen.queryByText(serverUser.email)).not.toBeInTheDocument()
      expect(screen.queryByText(freshUser.email)).not.toBeInTheDocument()
      cleanEvidence()
    }
    stableIdentity()
    for (const [raw, golden] of [[seed, P0], [postUser, P1], [serverUser, P0], [freshUser, FRESH]] as const) {
      const map = required(raw.preferences.dashboard_layouts, 'independent RAW map'), leaf = required(map[WS], 'RAW leaf')
      for (const value of [raw, raw.preferences, map, leaf]) expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
      expect(Object.hasOwn(raw, 'id')).toBe(true); expect(Object.hasOwn(raw, 'preferences')).toBe(true)
      expect(Object.hasOwn(raw.preferences, 'dashboard_layouts')).toBe(true); expect(Object.hasOwn(map, WS)).toBe(true)
      expect(validateDashboardLayoutRequest(WS, leaf, DASHBOARD_WIDGETS)).toStrictEqual(golden)
    }
    expect(validateCommittedDashboardLayout(WS, committed, DASHBOARD_WIDGETS)).toStrictEqual({ workspaceId: WS, layout: P1 })
    expect(validateCommittedDashboardLayout(WS, freshCommit, DASHBOARD_WIDGETS)).toStrictEqual({ workspaceId: WS, layout: FRESH })
    expect(store.getSnapshot(WS)).toMatchObject({ state: 'closed', writable: true, layout: P0, recovery: null, pending: false, readPending: false })
    const startup = [...calls], startupTotals = totals(startup)
    const expectedStartup: Record<string, number> = { 'get:/users/me': 2 }
    for (const url of Object.keys(startupReplies)) expectedStartup[`get:${url}`] = url === '/admin/default-colors' ? colorsSpy.mock.calls.length : 1
    // Theme effects use native SDK call evidence, not a guessed duplicate count.
    expect(colorsSpy.mock.calls.length).toBeGreaterThan(0)
    expect(startupTotals).toStrictEqual(expectedStartup)
    // ProtectedRoute consults Auth; setup/status and privileged admin calls are
    // NOT part of this authenticated non-superuser route. Strict transport denies them.
    const outcomes: Array<{ index: number; value?: CommittedDashboardLayout; error?: unknown }> = []
    function observeSave(index: number): Promise<CommittedDashboardLayout> {
      const result = required(saveSpy.mock.results[index], 'native save result')
      expect(result.type).toBe('return')
      const promise = result.value as Promise<CommittedDashboardLayout>
      tasks.push(promise.then((value) => { outcomes.push({ index, value }) }, (error: unknown) => { outcomes.push({ index, error }) }))
      return promise
    }
    async function changeAndDone(widgetKey: string, golden: DashboardLayout) {
      const before = [...calls]
      await user.click(screen.getByRole('button', { name: customizeName }))
      const editor = screen.getByRole('dialog', { name: editorName })
      const toggle = within(editor).getByRole('switch', { name: t('dashboard.customize.hide', { name: t(widgetKey) }) })
      expect(toggle).toBeChecked(); expect(toggle).toBeEnabled(); await user.click(toggle)
      await flush(); expect(calls).toStrictEqual(before)
      expect(store.getSnapshot(WS)).toMatchObject({ state: 'editing', draft: golden })
      await user.click(within(editor).getByRole('button', { name: t('dashboard.customize.done') }))
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    }
    owner.phase = 'ordinary-save'
    await changeAndDone('dashboard.widgets.spendingCharts', P1)
    await waitFor(() => expect(puts[0]!.config).not.toBeNull())
    expect(calls).toStrictEqual([...startup, puts[0]!.config])
    expect(JSON.parse(required(puts[0]!.config, 'PUT1').data as string)).toStrictEqual(P1)
    expect(saveSpy.mock.calls[0]![2].isCurrent()).toBe(true)
    observeSave(0)
    await act(async () => { puts[0]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(reads[2]!.config).not.toBeNull())
    expect(store.getSnapshot(WS)).toMatchObject({ layout: P1, pending: false, readPending: true, recovery: null })
    await act(async () => { reads[2]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity()
    expect(calls).toStrictEqual([...startup, puts[0]!.config, reads[2]!.config])
    expect(outcomes).toStrictEqual([{ index: 0, value: committed }])
    expect(store.getSnapshot(WS)).toMatchObject({ layout: P1, writable: true, pending: false, readPending: false, recovery: null })
    expect(screen.queryByText(t('dashboard.spendingByCategory'))).not.toBeInTheDocument()

    owner.phase = 'failed-save'
    await changeAndDone('dashboard.widgets.goalsProgress', P2)
    await waitFor(() => expect(puts[1]!.config).not.toBeNull())
    expect(JSON.parse(required(puts[1]!.config, 'PUT2').data as string)).toStrictEqual(P2)
    expect(saveSpy.mock.calls[1]![2].isCurrent()).toBe(true)
    const failedPromise = observeSave(1)
    await act(async () => { puts[1]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity()
    expect(originalError).toBeInstanceOf(axiosModule.AxiosError)
    expect(outcomes).toStrictEqual([{ index: 0, value: committed }, { index: 1, error: originalError }])
    expect(outcomes[1]!.error).toBe(originalError); expect(saveSpy.mock.results[1]!.value).toBe(failedPromise)
    const failure = store.getSnapshot(WS), capture = required(failure.recovery, 'immutable native recovery')
    const capturePreimage = structuredClone(capture), baseline = capture.baseline, submitted = capture.submitted
    expect(failure).toMatchObject({ state: 'unknown', writable: false, pending: false, readPending: false, layout: P1, openedBaseline: P1, draft: P2 })
    expect(capture).toMatchObject({ actorId: ACTOR, authGeneration: generation, workspaceId: WS,
      category: 'unknown', phase: 'needs-recheck', baseline: P1, submitted: P2, recheck: null })
    expectFrozen(failure); expect(store.retain(WS)).toBeNull()
    const afterFailure = [...calls]
    expect(afterFailure).toStrictEqual([...startup, puts[0]!.config, reads[2]!.config, puts[1]!.config])
    const reviewTitle = 'Review unsaved dashboard changes'
    function retained() {
      stableIdentity(); expect(store.getSnapshot(WS).layout).toStrictEqual(P1)
      expect(store.getSnapshot(WS).recovery).toBe(capture)
      expect(capture).toStrictEqual(capturePreimage); expect(capture.baseline).toBe(baseline); expect(capture.submitted).toBe(submitted)
      expect(saveSpy.mock.results[1]!.value).toBe(failedPromise); expect(outcomes[1]!.error).toBe(originalError)
      expect(store.getSnapshot(WS).counters).toStrictEqual({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    }
    function notice() {
      const trigger = screen.getByRole('button', { name: 'Review' })
      expect(trigger).toBeVisible(); expect(trigger).toBeEnabled()
      const element = required(trigger.closest<HTMLElement>('[role="status"]'), 'current-workspace notice')
      expect(element).toHaveTextContent(/unsaved/i); expect(element).toHaveTextContent(/reload/i); expect(element).toHaveTextContent(/sign.?out/i)
      expect(screen.getByRole('button', { name: customizeName })).toBeDisabled()
      return trigger
    }
    function compare(dialog: HTMLElement, label: string, golden: DashboardLayout) {
      const rows = within(within(dialog).getByRole('list', { name: label })).getAllByRole('listitem')
      expect(rows).toHaveLength(golden.items.length)
      golden.items.forEach((item, index) => {
        const definition = required(DASHBOARD_WIDGETS.find((widget) => widget.id === item.id), 'widget label')
        expect(rows[index]).toHaveTextContent(t(definition.translationKey))
        expect(rows[index]).toHaveTextContent(item.visible ? /\bVisible\b/ : /\bHidden\b/)
        expect(rows[index]).toHaveTextContent(new RegExp(`\\bPosition\\s+${item.order + 1}\\b`, 'i'))
      })
    }
    function readonlyReview() {
      const dialog = screen.getByRole('dialog', { name: reviewTitle })
      const heading = within(dialog).getByRole('heading', { name: reviewTitle })
      expect(heading).toHaveAttribute('tabindex', '-1'); expect(heading).toHaveFocus()
      expect(within(dialog).queryAllByRole('switch')).toStrictEqual([])
      expect(dialog.querySelectorAll('[draggable="true"], [aria-roledescription="sortable"]')).toHaveLength(0)
      expect(within(dialog).queryByRole('button', { name: t('dashboard.customize.reset') })).not.toBeInTheDocument()
      compare(dialog, 'Opened baseline', P1); compare(dialog, 'Submitted draft', P2)
      return dialog
    }
    owner.phase = 'review'
    await user.click(notice()); await drain(); retained(); readonlyReview()
    expect(calls).toStrictEqual(afterFailure)
    await user.click(within(screen.getByRole('dialog', { name: reviewTitle })).getByRole('button', { name: 'Close' }))
    await drain(); retained(); expect(calls).toStrictEqual(afterFailure)
    expect(screen.queryByRole('dialog', { name: reviewTitle })).not.toBeInTheDocument()
    expect(notice()).toHaveFocus() // Connected Review fallback, NEVER disabled Customize.
    const sidebar = required(document.querySelector('aside'), 'native App sidebar')
    await user.click(within(sidebar).getByRole('link', { name: t('nav.categories') }))
    await screen.findByRole('heading', { name: t('categories.title') })
    await drain(); retained()
    expect(window.location.pathname).toBe('/categories')
    expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument()
    const management = calls.slice(afterFailure.length)
    expect(totals(management)).toStrictEqual({ 'get:/categories?include_hidden=true': 1, 'get:/category-groups?include_hidden=true': 1 })
    const away = [...calls]
    await user.click(within(sidebar).getByRole('link', { name: t('app.name') }))
    await screen.findByRole('button', { name: 'Review' }); await drain(); retained(); notice()
    expect(window.location.pathname).toBe('/')
    expect(calls).toStrictEqual(away) // No auto recovery read, replay, or ordinary GET on remount.
    for (let reopen = 0; reopen < 2; reopen += 1) {
      await user.click(notice()); await drain(); retained(); const dialog = readonlyReview()
      expect(within(dialog).getByRole('button', { name: 'Retry' })).toBeDisabled()
      expect(within(dialog).getByRole('list', { name: 'Server layout' })).toHaveTextContent(/unavailable/i)
      expect(calls).toStrictEqual(away)
      await user.click(within(dialog).getByRole('button', { name: 'Close' }))
      await drain(); retained(); expect(calls).toStrictEqual(away); expect(notice()).toHaveFocus()
    }
    await user.click(notice()); await drain(); const reviewed = readonlyReview()
    await user.click(within(reviewed).getByRole('button', { name: 'Recheck' }))
    await waitFor(() => expect(reads[3]!.config).not.toBeNull()); await flush()
    expect(calls).toStrictEqual([...away, reads[3]!.config])
    expect(reads[3]!.settled).toBe(false)
    expect(store.getSnapshot(WS)).toMatchObject({ readPending: true, recovery: { phase: 'rechecking', baseline: P1, submitted: P2 } })
    const busy = screen.getByRole('dialog', { name: reviewTitle })
    for (const name of ['Recheck', 'Retry', 'Discard draft']) expect(within(busy).getByRole('button', { name })).toBeDisabled()
    await act(async () => { reads[3]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity()
    const qualified = required(store.getSnapshot(WS).recovery, 'qualified reviewed read')
    expect(qualified).toMatchObject({ category: 'unknown', phase: 'review-ready', baseline: P1, submitted: P2,
      recheck: { provenance: 'saved', target: P0, matchesSubmitted: false } })
    expect(qualified.baseline).toBe(baseline); expect(qualified.submitted).toBe(submitted); expectFrozen(qualified)
    const ready = screen.getByRole('dialog', { name: reviewTitle })
    compare(ready, 'Server layout', P0)
    await waitFor(() => expect(within(ready).getByRole('button', { name: 'Discard draft' })).toBeEnabled())
    const beforeDiscard = [...calls]
    await user.click(within(ready).getByRole('button', { name: 'Discard draft' }))
    await drain(); stableIdentity(); expect(calls).toStrictEqual(beforeDiscard)
    expect(store.getSnapshot(WS)).toMatchObject({ state: 'closed', writable: true, pending: false, readPending: false,
      layout: P0, draft: null, openedBaseline: null, recovery: null })
    expect(store.getSnapshot(WS).overlay).toStrictEqual({})
    expect(screen.queryByRole('dialog', { name: reviewTitle })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: customizeName })).toBeEnabled()
    expect(screen.getByText(t('dashboard.spendingByCategory'))).toBeVisible()
    // Positive after discard is a genuinely NEW editor, not failed-PUT replay.
    owner.phase = 'fresh-save'
    await changeAndDone('dashboard.widgets.balanceEvolution', FRESH)
    await waitFor(() => expect(puts[2]!.config).not.toBeNull())
    expect(JSON.parse(required(puts[2]!.config, 'fresh PUT').data as string)).toStrictEqual(FRESH)
    expect(saveSpy.mock.calls[2]![2].isCurrent()).toBe(true); observeSave(2)
    expect(calls).toStrictEqual([...beforeDiscard, puts[2]!.config])
    await act(async () => { puts[2]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(reads[4]!.config).not.toBeNull())
    await act(async () => { reads[4]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity()
    expect(calls).toStrictEqual([...beforeDiscard, puts[2]!.config, reads[4]!.config])
    expect(store.getSnapshot(WS)).toMatchObject({ layout: FRESH, writable: true, pending: false, readPending: false,
      draft: null, openedBaseline: null, recovery: null,
      counters: { success: 2, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
    expect(outcomes).toStrictEqual([{ index: 0, value: committed }, { index: 1, error: originalError }, { index: 2, value: freshCommit }])
    expect(outcomes[1]!.error).toBe(originalError); expect(saveSpy.mock.results[1]!.value).toBe(failedPromise)
    expect(userGets()).toHaveLength(5); expect(layoutPuts()).toHaveLength(3)
    expect(saveSpy).toHaveBeenCalledTimes(3); expect(meSpy).toHaveBeenCalledTimes(5)
    expect(totals()).toStrictEqual({ ...startupTotals, 'get:/users/me': 5, [`put:/dashboard/layouts/${WS}`]: 3,
      'get:/categories?include_hidden=true': 1, 'get:/category-groups?include_hidden=true': 1 })
    expect(ports.every((port) => port.config !== null && port.settled)).toBe(true)
  } catch (error: unknown) { owner.record('failure'); primary = error; failed = true }
  finally {
    owner.phase = 'cleanup'
    owner.record('cleanup')
    inCleanup = true
    owner.retired = true
    try { await nativeCleanup?.() } catch (error: unknown) { diagnostics.push(error) }
    for (const restore of [...restores].reverse()) {
      try { await restore() } catch (error: unknown) { diagnostics.push(error) }
    }
    try { vi.resetModules() } catch (error: unknown) { diagnostics.push(error) }
  }
  if (failed && diagnostics.length) throw new AggregateError([primary, ...diagnostics], 'Primary actual-App failure plus cleanup diagnostics', { cause: primary })
  if (failed) throw primary
  if (diagnostics.length) throw new AggregateError(diagnostics, 'Actual-App cleanup failed')
}
