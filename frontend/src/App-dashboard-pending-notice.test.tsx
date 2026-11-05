import { expect, it, vi } from 'vitest'
// Load the real module graph during collection, not inside the 5000ms behavior budget.
// The test's resetModules still rebuilds App/SDK state after transport spies are installed.
import '@/App'
import '@/pages/dashboard'
import type { AxiosAdapter, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import type { QueryClient } from '@tanstack/react-query'
import type { SessionStore, StoreOptions } from '@/lib/dashboard-layout-session'
import type { CommittedDashboardLayout, DashboardLayout } from '@/lib/dashboard-layout'
import type { DashboardSummary, User, Workspace } from '@/types'

// Internal / Draft / style v1.6 / 2026-10-06.
// AUTHOR_ONLY / TEST_DATA / NOT_RUN. Parent owns compilation and real RED/GREEN.
// Actual App, lazy routes and every native provider/controller/dialog remain intact.
// Only raw Axios transport changes behavior; spies are native passthrough.
const ACTOR = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
const WS = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
const TOKEN = 'synthetic-actual-app-pending-notice-test-data'
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
  { id: 'balance-evolution', visible: false, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const SERVER: DashboardLayout = freeze({ version: 1, workspaceId: WS, items: [
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
const recheckUser: User = freeze({ id: ACTOR, email: 'actual-app-fresh@example.invalid',
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
const replacementCommit: CommittedDashboardLayout = freeze({ workspaceId: WS, layout: {
  version: 1, workspaceId: WS, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: false, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const replacementPost: User = freeze({ id: ACTOR, email: 'pending-replacement-post@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', onboarding_completed: true, dashboard_layouts: { [WS]: {
    version: 1, workspaceId: WS, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: false, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } } },
})
const workspace: Workspace = freeze({ id: WS, name: 'TEST_DATA pending-notice workspace', kind: 'personal',
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

it('actual App keeps the pending notice across route dismissal for ordinary and reviewed Retry replacement saves', async () => {
  vi.resetModules()
  const diagnostics: unknown[] = [], violations: unknown[] = [], asynchronous: unknown[] = [], consoleCalls: unknown[] = []
  const restores: Array<() => void | Promise<void>> = []
  const inputs = [P0, P1, P2, SERVER, seed, postUser, recheckUser, replacementPost, committed, replacementCommit, workspace, summary, startupReplies]
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
    const axiosModule = await import('axios'), axios = axiosModule.default
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
    // Native Auth restoration, entry, ordinary postread, explicit Recheck,
    // replacement postread. Entry alone automatically settles.
    const reads = [slot(seed), slot(seed), slot(postUser), slot(recheckUser), slot(replacementPost)]
    const puts = [slot(committed), slot(undefined), slot(replacementCommit)]
    const ports = [...reads, ...puts]
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
            originalError = new axiosModule.AxiosError('TEST_DATA exact private 503 failure', axiosModule.AxiosError.ERR_BAD_RESPONSE, config, undefined, {
              data: { detail: 'TEST_DATA private backend diagnostic' }, status: 503, statusText: 'Service Unavailable', headers: {}, config,
            })
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
    const create = axios.create.bind(axios)
    const createSpy = vi.spyOn(axios, 'create').mockImplementation((config) => {
      const instance = create(config); http = instance
      restores.push(() => { instance.defaults.adapter = priorAdapter }); return instance
    })
    restores.push(() => { createSpy.mockRestore() })
    const { act, render, screen, within, waitFor } = await import('@testing-library/react')
    const { default: userEvent } = await import('@testing-library/user-event')
    const query = await import('@tanstack/react-query')
    // Observe App's actual module client through its genuine mount call. No
    // exported constructor replacement, nested client, or default option edits.
    const mountSpy = vi.spyOn(query.QueryClient.prototype, 'mount')
    restores.push(() => { mountSpy.mockRestore() })
    const session = await import('@/lib/dashboard-layout-session')
    const factorySpy = vi.spyOn(session, 'createDashboardLayoutSession')
    restores.push(() => { factorySpy.mockRestore() })
    const { getPageSessionLease } = await import('@/lib/dashboard-layout-page-session-lease')
    const { getDashboardAuthSnapshot } = await import('@/lib/dashboard-layout-auth-bridge')
    const { validateDashboardLayoutRequest, validateCommittedDashboardLayout } = await import('@/lib/dashboard-layout')
    const { DASHBOARD_WIDGETS } = await import('@/components/dashboard/widgets')
    const api = await import('@/lib/api')
    const saveSpy = vi.spyOn(api.dashboard, 'saveLayout'), meSpy = vi.spyOn(api.auth, 'me')
    const patchSpy = vi.spyOn(api.auth, 'updateMe'), colorsSpy = vi.spyOn(api.admin, 'defaultColors')
    restores.push(() => { saveSpy.mockRestore(); meSpy.mockRestore(); patchSpy.mockRestore(); colorsSpy.mockRestore() })
    const { default: i18n, i18nReady } = await import('@/lib/i18n')
    await i18nReady
    const oldLanguage = i18n.language
    restores.push(async () => { await i18n.changeLanguage(oldLanguage) })
    await i18n.changeLanguage('en')
    const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options) as string
    const { currentMonth } = await import('@/lib/month-utils')
    const month = currentMonth() // Source-fit real logo return, not a fake clock.
    localStorage.setItem('token', TOKEN); localStorage.setItem(api.WORKSPACE_STORAGE_KEY, WS)
    localStorage.setItem('privacyMode', 'false'); localStorage.setItem('theme', 'dark')
    localStorage.setItem('securo.sidebar.collapsed', 'false')
    localStorage.setItem('updateCheckEnabled', 'false') // Real user setting; prevents external release fetch.
    localStorage.removeItem(`securo.activeCollection.${WS}`)
    window.history.replaceState(null, '', `/?month=${month}`)
    const { default: App } = await import('@/App')
    // Qualify the real lazy dashboard's transitive module transform before
    // mounting App; keep its native lazy route, Suspense and all providers.
    await import('@/pages/dashboard')
    const user = userEvent.setup()
    let root: { unmount(): void } | null = null, client: QueryClient | null = null
    const stores = new Map<SessionStore, StoreOptions>(), unsubscribes: Array<() => void> = []
    const observedPromises = new Set<Promise<unknown>>()
    const publicSpies: Array<ReturnType<typeof vi.spyOn>> = []
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
      for (const spy of [meSpy, saveSpy, ...publicSpies]) for (const result of spy.mock.results) {
        if (result.type !== 'return') continue
        const promise = result.value as Promise<unknown>
        if (!observedPromises.has(promise)) {
          observedPromises.add(promise); tasks.push(promise.then(() => {}, () => {}))
        }
      }
    }
    async function flush() {
      await act(async () => { await tick(); await tick(); discoverNativeDiagnostics() })
    }
    // Held PUTs forbid a whole-store idle wait. Drain only free query/effect
    // work, then prove the same public writer is still unresolved.
    async function drainFreeWork() {
      for (let turn = 0; turn < 16; turn += 1) {
        discoverNativeDiagnostics()
        const before = [calls.length, tasks.length, stores.size, publications, queryEvents]
        await flush()
        if (client) await waitFor(() => { expect(client?.isFetching()).toBe(0); expect(client?.isMutating()).toBe(0) })
        await flush()
        const after = [calls.length, tasks.length, stores.size, publications, queryEvents]
        if (after.every((value, index) => value === before[index])) return
      }
      throw new Error('Actual-App free query/effect fixed point not reached')
    }
    async function drain() {
      for (let turn = 0; turn < 16; turn += 1) {
        discoverNativeDiagnostics()
        const before = [calls.length, tasks.length, stores.size, publications, queryEvents]
        await act(async () => {
          await tick(); discoverNativeDiagnostics(); await Promise.all(tasks)
          for (const store of stores.keys()) { await getPageSessionLease(store).whenIdle(); await store.whenIdle() }
          await tick(); await tick(); discoverNativeDiagnostics()
        })
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
      await step(async () => { await act(async () => { root?.unmount(); root = null; await tick(); await tick() }) })
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
    // Native App supplies ALL providers, routing, lazy pages and Toaster.
    root = render(<App />, { reactStrictMode: false })
    await waitFor(() => expect(reads[0]!.config).not.toBeNull())
    await act(async () => { reads[0]!.settle(); await tick(); await tick() })
    await screen.findByText(workspace.name)
    await screen.findByText(t('dashboard.spendingByCategory'))
    await drain(); await flush()
    // Qualify native entry before the held-write discriminator. No fake owner.
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
    const finalizeSpy = vi.spyOn(store, 'finalize'), replaceSpy = vi.spyOn(store, 'replaceRecovery')
    const reviewSpy = vi.spyOn(store, 'reviewRecovery'), recheckSpy = vi.spyOn(store, 'recheckReviewedRecovery')
    publicSpies.push(finalizeSpy, replaceSpy, recheckSpy)
    restores.push(() => { finalizeSpy.mockRestore(); replaceSpy.mockRestore(); reviewSpy.mockRestore(); recheckSpy.mockRestore() })
    function stableIdentity() {
      expect([...stores.keys()]).toStrictEqual([store]); expect(factorySpy).toHaveBeenCalledTimes(1)
      expect(options.actorId).toBe(ACTOR); expect(options.authSeed).toBe(seed)
      expect(options.getTokenIdentity()).toBe(TOKEN); expect(options.getWorkspaceId()).toBe(WS)
      expect(getDashboardAuthSnapshot(realClient)).toBe(bridge)
      expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(api.WORKSPACE_STORAGE_KEY)).toBe(WS)
      expect(screen.getByText(seed.email)).toBeVisible()
      expect(screen.queryByText(postUser.email)).not.toBeInTheDocument()
      expect(screen.queryByText(recheckUser.email)).not.toBeInTheDocument()
      expect(screen.queryByText(replacementPost.email)).not.toBeInTheDocument()
      cleanEvidence()
    }
    stableIdentity()
    for (const [raw, golden] of [[seed, P0], [postUser, P1], [recheckUser, SERVER], [replacementPost, P2]] as const) {
      const map = required(raw.preferences.dashboard_layouts, 'independent RAW map'), leaf = required(map[WS], 'RAW leaf')
      for (const value of [raw, raw.preferences, map, leaf]) expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
      expect(Object.hasOwn(raw, 'id')).toBe(true); expect(Object.hasOwn(raw, 'preferences')).toBe(true)
      expect(Object.hasOwn(raw.preferences, 'dashboard_layouts')).toBe(true); expect(Object.hasOwn(map, WS)).toBe(true)
      expect(validateDashboardLayoutRequest(WS, leaf, DASHBOARD_WIDGETS)).toStrictEqual(golden)
    }
    expect(validateCommittedDashboardLayout(WS, committed, DASHBOARD_WIDGETS)).toStrictEqual({ workspaceId: WS, layout: P1 })
    expect(validateCommittedDashboardLayout(WS, replacementCommit, DASHBOARD_WIDGETS)).toStrictEqual({ workspaceId: WS, layout: P2 })
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
    const publicOutcomes: Array<{ label: string; value?: unknown; error?: unknown }> = []
    function observePublic(label: string, task: Promise<void>) {
      tasks.push(task.then((value) => { publicOutcomes.push({ label, value }) }, (error: unknown) => { publicOutcomes.push({ label, error }) }))
      return task
    }
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
      const sessionId = required(store.retain(WS), 'native changed edit session')
      await user.click(within(editor).getByRole('button', { name: t('dashboard.customize.done') }))
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
      return sessionId
    }
    const key = 'dashboard.customize.persistence.'
    const pendingText = t(key + 'pending'), reviewTitle = t(key + 'reviewTitle')
    expect(pendingText).toBe('Dashboard layout operation pending.')
    function noPrivatePresentation() {
      for (const secret of [TOKEN, ACTOR, WS, 'TEST_DATA exact private 503 failure', 'TEST_DATA private backend diagnostic']) {
        expect(document.body.textContent).not.toContain(secret)
      }
    }
    function pagePendingNotice() {
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
      // FIRST discriminating functional oracle: on current source this exact
      // translated message is absent after Done. predicted RED, NOT_RUN.
      const message = screen.getByText(pendingText, { exact: true })
      expect(message).toBeVisible()
      const status = required(message.closest<HTMLElement>('[role="status"]'), 'pending page status')
      expect(status).toBeVisible(); expect(status).toHaveAttribute('aria-live', 'polite')
      expect(status).toHaveAttribute('aria-busy', 'true')
      expect(status.closest('[role="dialog"]')).toBeNull()
      expect(status).not.toHaveTextContent(t(key + 'unsaved'))
      expect(screen.queryByText(t(key + 'unsaved'), { exact: true })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: t(key + 'review') })).not.toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: reviewTitle })).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: customizeName })).toBeDisabled()
      noPrivatePresentation()
    }
    async function pendingRoundTrip(witness: () => void, managementTotals: Record<string, number>) {
      const before = [...calls], reviews = reviewSpy.mock.calls.length, rechecks = recheckSpy.mock.calls.length
      const sidebar = required(document.querySelector('aside'), 'native App sidebar')
      await user.click(within(sidebar).getByRole('link', { name: t('nav.categories') }))
      await screen.findByRole('heading', { name: t('categories.title') })
      await drainFreeWork(); witness(); stableIdentity()
      expect(window.location.pathname).toBe('/categories')
      expect(screen.queryByText(pendingText, { exact: true })).not.toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
      expect(totals(calls.slice(before.length))).toStrictEqual(managementTotals)
      const away = [...calls]
      await user.click(within(sidebar).getByRole('link', { name: t('app.name') }))
      await screen.findByRole('button', { name: customizeName })
      await drainFreeWork(); witness(); stableIdentity(); pagePendingNotice()
      expect(window.location.pathname).toBe('/')
      expect(calls).toStrictEqual(away) // No read, replay, or replacement permission on return.
      expect(reviewSpy).toHaveBeenCalledTimes(reviews); expect(recheckSpy).toHaveBeenCalledTimes(rechecks)
      return away
    }
    const ordinarySession = await changeAndDone('dashboard.widgets.spendingCharts', P1)
    await waitFor(() => expect(puts[0]!.config).not.toBeNull()); await flush()
    expect(calls).toStrictEqual([...startup, puts[0]!.config])
    expect(JSON.parse(required(puts[0]!.config, 'ordinary PUT').data as string)).toStrictEqual(P1)
    expect(finalizeSpy).toHaveBeenCalledTimes(1); expect(finalizeSpy.mock.calls[0]![0]).toBe(ordinarySession)
    const ordinarySDK = observeSave(0)
    const ordinaryTask = observePublic('ordinary', finalizeSpy.mock.results[0]!.value as Promise<void>)
    const ordinaryBinding = saveSpy.mock.calls[0]![2]
    const ordinaryCapture = store.getSnapshot(WS), ordinaryPreimage = structuredClone(ordinaryCapture)
    function ordinaryHeld() {
      expect(puts[0]!.settled).toBe(false)
      expect(store.getSnapshot(WS)).toStrictEqual(ordinaryPreimage)
      expect(ordinaryCapture).toMatchObject({ actorId: ACTOR, authGeneration: generation, workspaceId: WS,
        state: 'pending', layout: P0, openedBaseline: P0, draft: P1, recovery: null, pending: true, readPending: false,
        counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
      expect(store.getPersistenceStatus(WS)).toMatchObject({ pending: true, readPending: false, canCustomize: false, failureKind: null })
      expect(store.retain(WS)).toBe(ordinarySession)
      expect(saveSpy.mock.results[0]!.value).toBe(ordinarySDK)
      expect(finalizeSpy.mock.results[0]!.value).toBe(ordinaryTask)
      expect(saveSpy.mock.calls[0]![2]).toBe(ordinaryBinding); expect(ordinaryBinding.isCurrent()).toBe(true)
      expect(outcomes).toStrictEqual([]); expect(publicOutcomes).toStrictEqual([])
      expect(saveSpy).toHaveBeenCalledTimes(1); expect(finalizeSpy).toHaveBeenCalledTimes(1)
      expect(userGets()).toHaveLength(2); expect(layoutPuts()).toHaveLength(1)
      expect(replaceSpy).not.toHaveBeenCalled(); stableIdentity()
    }
    ordinaryHeld(); pagePendingNotice()
    const afterOrdinaryReturn = await pendingRoundTrip(ordinaryHeld, {
      'get:/categories?include_hidden=true': 1, 'get:/category-groups?include_hidden=true': 1,
    })
    await act(async () => { puts[0]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(reads[2]!.config).not.toBeNull())
    expect(store.getSnapshot(WS)).toMatchObject({ state: 'closed-success', layout: P1, pending: false, readPending: true,
      recovery: null, counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
    expect(ordinaryBinding.isCurrent()).toBe(false) // Native successful consumption, not owner revocation.
    await act(async () => { reads[2]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity()
    expect(calls).toStrictEqual([...afterOrdinaryReturn, reads[2]!.config])
    expect(outcomes).toStrictEqual([{ index: 0, value: committed }])
    expect(publicOutcomes).toStrictEqual([{ label: 'ordinary', value: undefined }])
    expect(screen.queryByText(pendingText, { exact: true })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: customizeName })).toBeEnabled()
    expect(screen.queryByText(t('dashboard.spendingByCategory'))).not.toBeInTheDocument()
    expect(store.getSnapshot(WS)).toMatchObject({ layout: P1, writable: true, pending: false, readPending: false, recovery: null })

    // A genuinely next changed editor save fails with an original held 503.
    expect(screen.getByText(t('dashboard.balanceFlow'))).toBeVisible()
    const failedSession = await changeAndDone('dashboard.widgets.balanceEvolution', P2)
    await waitFor(() => expect(puts[1]!.config).not.toBeNull())
    expect(JSON.parse(required(puts[1]!.config, 'failed PUT').data as string)).toStrictEqual(P2)
    expect(finalizeSpy).toHaveBeenCalledTimes(2); expect(finalizeSpy.mock.calls[1]![0]).toBe(failedSession)
    expect(saveSpy.mock.calls[1]![2].isCurrent()).toBe(true)
    const failedSDK = observeSave(1)
    const failedTask = observePublic('original-failure', finalizeSpy.mock.results[1]!.value as Promise<void>)
    const beforeFailure = [...calls]
    await act(async () => { puts[1]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity()
    expect(calls).toStrictEqual(beforeFailure) // Unknown/5xx never silently schedules Recheck.
    expect(originalError).toBeInstanceOf(axiosModule.AxiosError)
    expect(outcomes[1]).toStrictEqual({ index: 1, error: originalError }); expect(outcomes[1]!.error).toBe(originalError)
    expect(publicOutcomes[1]).toStrictEqual({ label: 'original-failure', error: originalError })
    expect(publicOutcomes[1]!.error).toBe(originalError)
    const failure = store.getSnapshot(WS), capture = required(failure.recovery, 'native failed capture')
    const capturePreimage = structuredClone(capture), baseline = capture.baseline, submitted = capture.submitted
    expect(failure).toMatchObject({ state: 'unknown', layout: P1, openedBaseline: P1, draft: P2,
      writable: false, pending: false, readPending: false,
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 1 } })
    expect(capture).toMatchObject({ actorId: ACTOR, authGeneration: generation, workspaceId: WS,
      baseline: P1, submitted: P2, category: 'unknown', phase: 'needs-recheck', recheck: null })
    expectFrozen(capture); expect(store.retain(WS)).toBeNull()
    expect(screen.queryByText(pendingText, { exact: true })).not.toBeInTheDocument()
    expect(screen.getByText(t(key + 'unsaved'), { exact: true })).toBeVisible()
    expect(screen.getByText(t('dashboard.balanceFlow'))).toBeVisible() // Failure never applies the hidden-chart draft.
    expect(screen.getByRole('button', { name: customizeName })).toBeDisabled(); noPrivatePresentation()
    function originalFailureRetained() {
      expect(capture).toStrictEqual(capturePreimage); expect(capture.baseline).toBe(baseline); expect(capture.submitted).toBe(submitted)
      expect(saveSpy.mock.results[1]!.value).toBe(failedSDK); expect(finalizeSpy.mock.results[1]!.value).toBe(failedTask)
      expect(outcomes[1]!.error).toBe(originalError); expect(publicOutcomes[1]!.error).toBe(originalError)
    }
    function compare(dialog: HTMLElement, label: string, golden: DashboardLayout) {
      const rows = within(within(dialog).getByRole('list', { name: label })).getAllByRole('listitem')
      expect(rows).toHaveLength(golden.items.length)
      golden.items.forEach((item, index) => {
        const definition = required(DASHBOARD_WIDGETS.find((widget) => widget.id === item.id), 'comparison label')
        expect(rows[index]).toHaveTextContent(t(definition.translationKey))
        expect(rows[index]).toHaveTextContent(t(key + (item.visible ? 'visible' : 'hidden')))
        expect(rows[index]).toHaveTextContent(t(key + 'position', { position: item.order + 1 }))
      })
    }
    const reviewTrigger = screen.getByRole('button', { name: t(key + 'review') })
    expect(reviewTrigger).toBeEnabled(); await user.click(reviewTrigger); await drain()
    const reviewed = screen.getByRole('dialog', { name: reviewTitle })
    expect(within(reviewed).getByRole('heading', { name: reviewTitle })).toHaveFocus()
    expect(within(reviewed).queryAllByRole('switch')).toStrictEqual([])
    expect(reviewed.querySelectorAll('[draggable="true"], [aria-roledescription="sortable"]')).toHaveLength(0)
    expect(within(reviewed).queryByRole('button', { name: t('dashboard.customize.reset') })).not.toBeInTheDocument()
    compare(reviewed, t(key + 'baselineLayout'), P1); compare(reviewed, t(key + 'draftLayout'), P2)
    expect(within(reviewed).getByRole('list', { name: t(key + 'serverLayout') })).toHaveTextContent(t(key + 'unavailable'))
    expect(within(reviewed).getByRole('button', { name: t(key + 'retry') })).toBeDisabled()
    expect(calls).toStrictEqual(beforeFailure); originalFailureRetained()
    await user.click(within(reviewed).getByRole('button', { name: t(key + 'recheck') }))
    await waitFor(() => expect(reads[3]!.config).not.toBeNull()); await flush()
    expect(calls).toStrictEqual([...beforeFailure, reads[3]!.config])
    expect(store.getSnapshot(WS)).toMatchObject({ readPending: true, recovery: { phase: 'rechecking', baseline: P1, submitted: P2 } })
    const busyReview = screen.getByRole('dialog', { name: reviewTitle })
    for (const action of ['recheck', 'retry', 'discard']) expect(within(busyReview).getByRole('button', { name: t(key + action) })).toBeDisabled()
    expect(recheckSpy).toHaveBeenCalledTimes(1)
    observePublic('recheck', recheckSpy.mock.results[0]!.value as Promise<void>)
    await act(async () => { reads[3]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity(); originalFailureRetained()
    const qualified = required(store.getSnapshot(WS).recovery, 'strict different current Recheck')
    expect(qualified).toMatchObject({ category: 'unknown', phase: 'review-ready', baseline: P1, submitted: P2,
      recheck: { provenance: 'saved', target: SERVER, matchesSubmitted: false } })
    expect(qualified.baseline).toBe(baseline); expect(qualified.submitted).toBe(submitted); expectFrozen(qualified)
    const ready = screen.getByRole('dialog', { name: reviewTitle })
    compare(ready, t(key + 'serverLayout'), SERVER)
    const retry = within(ready).getByRole('button', { name: t(key + 'retry') })
    await waitFor(() => expect(retry).toBeEnabled())
    const liveReview = required(reviewSpy.mock.results.at(-1), 'native readonly review result')
    expect(liveReview.type).toBe('return')
    if (liveReview.type !== 'return') throw new Error('Native review did not return a readonly model')
    expect(liveReview.value.permissions).toStrictEqual({ recheck: true, retry: true, reapply: false, discard: true })
    expect(liveReview.value.recovery).toBe(qualified)
    const beforeReplacement = [...calls]
    // Real user double-click attempts a duplicate Retry; only its first native
    // authorized action may issue a replacement. Never invoke a forged ticket.
    await user.dblClick(retry)
    await waitFor(() => expect(puts[2]!.config).not.toBeNull()); await flush()
    expect(replaceSpy).toHaveBeenCalledTimes(1)
    expect(replaceSpy.mock.calls[0]).toStrictEqual([liveReview.value.ticket, 'retry'])
    expect(JSON.parse(required(puts[2]!.config, 'replacement PUT').data as string)).toStrictEqual(P2)
    expect(calls).toStrictEqual([...beforeReplacement, puts[2]!.config])
    const replacementSDK = observeSave(2)
    const replacementTask = observePublic('replacement', replaceSpy.mock.results[0]!.value as Promise<void>)
    const replacementBinding = saveSpy.mock.calls[2]![2]
    const replacementSession = required(store.retain(WS), 'native replacement session')
    expect(replacementSession).not.toBe(failedSession); expect(replacementSession).not.toBe(ordinarySession)
    const replacementCapture = store.getSnapshot(WS), replacementPreimage = structuredClone(replacementCapture)
    function replacementHeld() {
      expect(puts[2]!.settled).toBe(false); expect(store.retain(WS)).toBe(replacementSession)
      expect(store.getSnapshot(WS)).toStrictEqual(replacementPreimage)
      expect(replacementCapture).toMatchObject({ actorId: ACTOR, authGeneration: generation, workspaceId: WS,
        state: 'pending', layout: P1, openedBaseline: SERVER, draft: P2, pending: true, readPending: false, recovery: null,
        counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 1 } })
      expect(store.getPersistenceStatus(WS)).toMatchObject({ pending: true, readPending: false, canCustomize: false, failureKind: null })
      expect(saveSpy.mock.calls[2]![2]).toBe(replacementBinding); expect(replacementBinding.isCurrent()).toBe(true)
      expect(saveSpy.mock.results[2]!.value).toBe(replacementSDK); expect(replaceSpy.mock.results[0]!.value).toBe(replacementTask)
      expect(outcomes).toHaveLength(2); expect(publicOutcomes.some((outcome) => outcome.label === 'replacement')).toBe(false)
      expect(userGets()).toHaveLength(4); expect(layoutPuts()).toHaveLength(3)
      expect(saveSpy).toHaveBeenCalledTimes(3); expect(finalizeSpy).toHaveBeenCalledTimes(2); expect(replaceSpy).toHaveBeenCalledTimes(1)
      stableIdentity(); originalFailureRetained()
    }
    replacementHeld(); pagePendingNotice()
    const afterReplacementReturn = await pendingRoundTrip(replacementHeld, {}) // Cached native management route.
    await act(async () => { puts[2]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(reads[4]!.config).not.toBeNull())
    expect(store.getSnapshot(WS)).toMatchObject({ state: 'closed-success', pending: false, readPending: true, layout: P2,
      counters: { success: 2, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 1 } })
    expect(replacementBinding.isCurrent()).toBe(false)
    await act(async () => { reads[4]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity(); originalFailureRetained(); noPrivatePresentation()
    expect(calls).toStrictEqual([...afterReplacementReturn, reads[4]!.config])
    expect(store.getSnapshot(WS)).toStrictEqual({ actorId: ACTOR, authGeneration: generation, workspaceId: WS,
      provenance: 'saved', writable: true, state: 'closed-success', pending: false, readPending: false, canUndo: false,
      layout: P2, openedBaseline: null, draft: null, recovery: null, overlay: { [WS]: P2 },
      counters: { success: 2, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 1 } })
    expect(outcomes).toStrictEqual([{ index: 0, value: committed }, { index: 1, error: originalError }, { index: 2, value: replacementCommit }])
    expect(publicOutcomes).toStrictEqual([{ label: 'ordinary', value: undefined }, { label: 'original-failure', error: originalError },
      { label: 'recheck', value: undefined }, { label: 'replacement', value: undefined }])
    expect(screen.queryByText(pendingText, { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText(t(key + 'unsaved'), { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t(key + 'review') })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: customizeName })).toBeEnabled()
    expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: reviewTitle })).not.toBeInTheDocument()
    expect(screen.queryByText(t('dashboard.spendingByCategory'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('dashboard.balanceFlow'))).not.toBeInTheDocument()
    expect(userGets()).toHaveLength(5); expect(layoutPuts()).toHaveLength(3)
    expect(saveSpy).toHaveBeenCalledTimes(3); expect(meSpy).toHaveBeenCalledTimes(5)
    expect(totals()).toStrictEqual({ ...startupTotals, 'get:/users/me': 5, [`put:/dashboard/layouts/${WS}`]: 3,
      'get:/categories?include_hidden=true': 1, 'get:/category-groups?include_hidden=true': 1 })
    expect(ports.every((port) => port.config !== null && port.settled)).toBe(true)
  } catch (error: unknown) { primary = error; failed = true }
  finally {
    try { await nativeCleanup?.() } catch (error: unknown) { diagnostics.push(error) }
    for (const restore of [...restores].reverse()) {
      try { await restore() } catch (error: unknown) { diagnostics.push(error) }
    }
    try { vi.resetModules() } catch (error: unknown) { diagnostics.push(error) }
  }
  if (failed && diagnostics.length) throw new AggregateError([primary, ...diagnostics], 'Primary actual-App failure plus cleanup diagnostics', { cause: primary })
  if (failed) throw primary
  if (diagnostics.length) throw new AggregateError(diagnostics, 'Actual-App cleanup failed')
})
