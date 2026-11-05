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

// Internal / Draft / TEST_DATA. Independent announcement regression.
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
    owned.owner.disconnect?.()
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

it.each(['explicit Done', 'implicit browser Back'] as const)(
  'actual App announces each failed attempt once with %s and failed reviewed Retry', async (closeMode) => {
  const owned = required(active, 'prepared native scenario')
  owned.step = owned.iterator.next(closeMode)
  await owned.step
}, 20_000)

// Only fresh prerequisite evaluation runs in beforeEach. The yield is BEFORE
// render; the named test owns all native behavior, assertions and finally work.
async function* nativeScenario(signal: AbortSignal, owner: ScenarioOwner): AsyncGenerator<void, void, 'explicit Done' | 'implicit browser Back'> {
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
  const inputs = [P0, P1, P2, SERVER, seed, postUser, recheckUser, committed, workspace, summary, startupReplies]
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
    // Native Auth restoration, entry, ordinary postread, explicit Recheck,
    // repeated reviewed Rechecks. Entry alone automatically settles.
    const reads = [slot(seed), slot(seed), slot(postUser), slot(recheckUser), slot(recheckUser), slot(recheckUser)]
    const puts = [slot(committed), slot(undefined), slot(undefined)]
    const ports = [...reads, ...puts]
    owner.inspect = () => ({ cleaning, issued: ports.filter((port) => port.config !== null).length,
      unsettled: ports.filter((port) => port.config !== null && !port.settled).length })
    owner.retire = () => { cleaning = true; ports.forEach((port) => { port.settle() }) }
    const nativeErrors: Array<InstanceType<typeof axiosModule.AxiosError>> = []
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
          if (index === 1 || index === 2) {
            const error = new axiosModule.AxiosError(`TEST_DATA private attempt ${index} 503`, axiosModule.AxiosError.ERR_BAD_RESPONSE, config, undefined, {
              data: { detail: `TEST_DATA private backend attempt ${index}` }, status: 503, statusText: 'Service Unavailable', headers: {}, config,
            })
            nativeErrors.push(error); port.error = error
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
    const { toast } = await prepared(import('sonner'))
    const toastErrorSpy = vi.spyOn(toast, 'error') // Native passthrough; not a DOM oracle.
    restores.push(() => { toastErrorSpy.mockRestore() })
    const failureText = t('dashboard.customize.saveError')
    const renderedFailures = new Map<HTMLElement, { title: string | null; live: string | null; relevant: string | null;
      atomic: string | null; label: string | null; role: string | null }>()
    function collectRenderedFailures() {
      if (owner.retired) return
      document.querySelectorAll<HTMLElement>('[data-sonner-toast][data-type="error"]').forEach((node) => {
        if (renderedFailures.has(node)) return
        const region = node.closest('section[aria-live]')
        renderedFailures.set(node, { title: node.querySelector('[data-title]')?.textContent ?? null,
          live: region?.getAttribute('aria-live') ?? null, relevant: region?.getAttribute('aria-relevant') ?? null,
          atomic: region?.getAttribute('aria-atomic') ?? null, label: region?.getAttribute('aria-label') ?? null,
          role: node.getAttribute('role') })
      })
    }
    const toastDOMObserver = new MutationObserver(collectRenderedFailures)
    owner.disconnect = () => { toastDOMObserver.disconnect() }
    restores.push(owner.disconnect)
    toastDOMObserver.observe(document.body, { childList: true, subtree: true, characterData: true })
    const { currentMonth } = await prepared(import('@/lib/month-utils'))
    const month = currentMonth() // Source-fit real logo return, not a fake clock.
    localStorage.setItem('token', TOKEN); localStorage.setItem(api.WORKSPACE_STORAGE_KEY, WS)
    localStorage.setItem('privacyMode', 'false'); localStorage.setItem('theme', 'dark')
    localStorage.setItem('securo.sidebar.collapsed', 'false')
    localStorage.setItem('updateCheckEnabled', 'false') // Real user setting; prevents external release fetch.
    localStorage.removeItem(`securo.activeCollection.${WS}`)
    // A real history entry lets browser Back dismiss the live modal by route
    // unmount, without calling Done, a controller, or a hidden sidebar action.
    window.history.replaceState(null, '', '/categories')
    window.history.pushState(null, '', `/?month=${month}`)
    const { default: App } = await prepared(import('@/App'))
    // Qualify the real lazy dashboard's transitive module transform before
    // mounting App; keep its native lazy route, Suspense and all providers.
    await prepared(Promise.all([import('@/pages/dashboard'), import('@/pages/categories')]))
    const user = userEvent.setup()
    let root: { unmount(): void; container: HTMLElement } | null = null, client: QueryClient | null = null
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
      checkActive()
      await act(async () => { await tick(); await tick(); discoverNativeDiagnostics() })
      checkActive()
    }
    // Held PUTs forbid a whole-store idle wait. Drain only free query/effect
    // work, then prove the same public writer is still unresolved.
    async function drainFreeWork() {
      for (let turn = 0; turn < 16; turn += 1) {
        discoverNativeDiagnostics()
        const before = [calls.length, tasks.length, stores.size, publications, queryEvents]
        await flush()
        checkActive()
        if (client) await waitFor(() => { expect(client?.isFetching()).toBe(0); expect(client?.isMutating()).toBe(0) })
        await flush()
        const after = [calls.length, tasks.length, stores.size, publications, queryEvents]
        if (after.every((value, index) => value === before[index])) return
      }
      throw new Error('Actual-App free query/effect fixed point not reached')
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
      await step(async () => { await act(async () => {
        for (const result of toastErrorSpy.mock.results) if (result.type === 'return') toast.dismiss(result.value as string | number)
        await tick(); await tick()
      }) })
      for (const unsubscribe of unsubscribes) await step(unsubscribe)
      await step(() => { client?.clear() })
      await step(drain)
      await step(cleanEvidence)
    }
    const closeMode = yield
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
      cleanEvidence()
    }
    stableIdentity()
    for (const [raw, golden] of [[seed, P0], [postUser, P1], [recheckUser, SERVER]] as const) {
      const map = required(raw.preferences.dashboard_layouts, 'independent RAW map'), leaf = required(map[WS], 'RAW leaf')
      for (const value of [raw, raw.preferences, map, leaf]) expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
      expect(Object.hasOwn(raw, 'id')).toBe(true); expect(Object.hasOwn(raw, 'preferences')).toBe(true)
      expect(Object.hasOwn(raw.preferences, 'dashboard_layouts')).toBe(true); expect(Object.hasOwn(map, WS)).toBe(true)
      expect(validateDashboardLayoutRequest(WS, leaf, DASHBOARD_WIDGETS)).toStrictEqual(golden)
    }
    expect(validateCommittedDashboardLayout(WS, committed, DASHBOARD_WIDGETS)).toStrictEqual({ workspaceId: WS, layout: P1 })
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
    function observeSave(index: number) {
      const result = required(saveSpy.mock.results[index], 'native save result')
      expect(result.type).toBe('return')
      const promise = result.value as Promise<CommittedDashboardLayout>
      tasks.push(promise.then((value) => { outcomes.push({ index, value }) }, (error: unknown) => { outcomes.push({ index, error }) }))
      return promise
    }
    function observePublic(label: string, promise: Promise<void>) {
      tasks.push(promise.then((value) => { publicOutcomes.push({ label, value }) }, (error: unknown) => { publicOutcomes.push({ label, error }) }))
      return promise
    }
    const key = 'dashboard.customize.persistence.', reviewTitle = t(key + 'reviewTitle')
    function noPrivatePresentation() {
      for (const secret of [TOKEN, ACTOR, WS, 'TEST_DATA private attempt 1 503', 'TEST_DATA private attempt 2 503',
        'TEST_DATA private backend attempt 1', 'TEST_DATA private backend attempt 2']) expect(document.body.textContent).not.toContain(secret)
    }
    function noNewAnnouncement(count: number) {
      collectRenderedFailures()
      // Cumulative rendered node evidence survives natural expiry; no fake clock,
      // duration override, hover pause or notification synthesized by the fixture.
      expect(renderedFailures.size).toBe(count)
      expect(toastErrorSpy).toHaveBeenCalledTimes(count)
      for (const call of toastErrorSpy.mock.calls) expect(call[0]).toBe(failureText)
      for (const metadata of renderedFailures.values()) {
        expect(metadata.title).toBe(failureText)
        expect(metadata.live).toBe('polite')
        expect(metadata.relevant).toBe('additions text'); expect(metadata.atomic).toBe('false')
        expect(metadata.label).toMatch(/^Notifications /)
        expect(metadata.role).toBeNull() // Installed Sonner uses a live section, not role=alert.
      }
      noPrivatePresentation()
    }
    async function newAnnouncement(count: number) {
      await waitFor(() => { collectRenderedFailures(); expect(renderedFailures.size).toBe(count) })
      const node = required([...renderedFailures.keys()].at(-1), 'real newly rendered failure toast')
      expect(node).toBeVisible()
      expect(node).toHaveAttribute('data-type', 'error')
      expect(within(node).getByText(failureText, { exact: true })).toBeVisible()
      const list = required(node.closest<HTMLElement>('[data-sonner-toaster]'), 'actual Sonner list')
      expect(list.tagName).toBe('OL')
      const region = required(list.closest<HTMLElement>('section[aria-live]'), 'actual Sonner live region')
      expect(region).toHaveAttribute('aria-live', 'polite')
      expect(region).toHaveAttribute('aria-relevant', 'additions text')
      expect(region).toHaveAttribute('aria-atomic', 'false')
      expect(region).not.toHaveAttribute('aria-hidden', 'true')
      expect(region.closest('[aria-hidden="true"]')).toBeNull()
      noNewAnnouncement(count)
    }
    function pageFailureStatus() {
      const message = screen.getByText(t(key + 'unsaved'), { exact: true })
      const status = required(message.closest<HTMLElement>('[role="status"]'), 'native retained failure status')
      expect(status).toBeVisible(); expect(status).toHaveAttribute('aria-live', 'polite')
      expect(status).toHaveAttribute('aria-busy', 'false')
      expect(status.closest('[role="dialog"]')).toBeNull()
      expect(screen.getByRole('button', { name: customizeName })).toBeDisabled()
      expect(screen.getByRole('button', { name: t(key + 'review') })).toBeEnabled()
      noPrivatePresentation()
    }
    async function change(widgetKey: string, golden: DashboardLayout) {
      const before = [...calls]
      await user.click(screen.getByRole('button', { name: customizeName }))
      const editor = screen.getByRole('dialog', { name: editorName })
      const toggle = within(editor).getByRole('switch', { name: t('dashboard.customize.hide', { name: t(widgetKey) }) })
      expect(toggle).toBeChecked(); expect(toggle).toBeEnabled(); await user.click(toggle)
      await flush(); expect(calls).toStrictEqual(before)
      expect(store.getSnapshot(WS)).toMatchObject({ state: 'editing', draft: golden })
      return { editor, sessionId: required(store.retain(WS), 'native changed session') }
    }
    async function done(editor: HTMLElement) {
      await user.click(within(editor).getByRole('button', { name: t('dashboard.customize.done') }))
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    }
    async function away(browserBack = false) {
      if (browserBack) {
        await act(async () => { window.history.back(); await tick(); await tick() })
      } else {
        const sidebar = required(document.querySelector('aside'), 'native sidebar')
        await user.click(within(sidebar).getByRole('link', { name: t('nav.categories') }))
      }
      await screen.findByRole('heading', { name: t('categories.title') })
      await drainFreeWork(); stableIdentity()
      expect(window.location.pathname).toBe('/categories')
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    }
    async function back() {
      const before = [...calls]
      const sidebar = required(document.querySelector('aside'), 'native sidebar')
      await user.click(within(sidebar).getByRole('link', { name: t('app.name') }))
      await screen.findByRole('button', { name: customizeName })
      await drainFreeWork(); stableIdentity()
      expect(window.location.pathname).toBe('/')
      expect(calls).toStrictEqual(before) // Retained recovery forbids an automatic read/replay.
    }
    async function review() {
      await user.click(screen.getByRole('button', { name: t(key + 'review') })); await drain()
      return screen.getByRole('dialog', { name: reviewTitle })
    }
    async function closeReview(dialog: HTMLElement) {
      await user.click(within(dialog).getByRole('button', { name: t('common.close') })); await drain()
      expect(screen.queryByRole('dialog', { name: reviewTitle })).not.toBeInTheDocument()
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
    async function repeatedReview(count: number) {
      const before = [...calls]
      for (let turn = 0; turn < 2; turn += 1) {
        const dialog = await review(); noNewAnnouncement(count)
        expect(within(dialog).queryAllByRole('switch')).toStrictEqual([])
        await closeReview(dialog); pageFailureStatus(); noNewAnnouncement(count)
      }
      expect(calls).toStrictEqual(before)
    }
    async function recheck(index: number, count: number) {
      const dialog = await review(), before = [...calls]
      await user.click(within(dialog).getByRole('button', { name: t(key + 'recheck') }))
      await waitFor(() => expect(reads[index]!.config).not.toBeNull()); await flush()
      expect(calls).toStrictEqual([...before, reads[index]!.config])
      expect(store.getSnapshot(WS)).toMatchObject({ readPending: true, recovery: { phase: 'rechecking' } })
      for (const action of ['recheck', 'retry', 'discard']) {
        expect(within(screen.getByRole('dialog', { name: reviewTitle })).getByRole('button', { name: t(key + action) })).toBeDisabled()
      }
      observePublic(`recheck-${index}`, recheckSpy.mock.results[index - 3]!.value as Promise<void>)
      noNewAnnouncement(count)
      await act(async () => { reads[index]!.settle(); await tick(); await tick() })
      await drain(); stableIdentity(); noNewAnnouncement(count)
      const qualified = required(store.getSnapshot(WS).recovery, 'strict reviewed recovery')
      expect(qualified).toMatchObject({ category: 'unknown', phase: 'review-ready',
        recheck: { provenance: 'saved', target: SERVER, matchesSubmitted: false } })
      expectFrozen(qualified)
      const ready = screen.getByRole('dialog', { name: reviewTitle })
      compare(ready, t(key + 'serverLayout'), SERVER)
      await waitFor(() => expect(within(ready).getByRole('button', { name: t(key + 'retry') })).toBeEnabled())
      return { ready, qualified }
    }

    // Positive control: a real changed explicit Done successfully uses the
    // native SDK/envelope/postread, without announcing a failure.
    owner.phase = 'ordinary-save'
    const positive = await change('dashboard.widgets.spendingCharts', P1)
    await done(positive.editor)
    await waitFor(() => expect(puts[0]!.config).not.toBeNull()); await flush()
    const positiveSDK = observeSave(0)
    const positiveTask = observePublic('positive', finalizeSpy.mock.results[0]!.value as Promise<void>)
    expect(finalizeSpy.mock.calls[0]![0]).toBe(positive.sessionId)
    expect(JSON.parse(required(puts[0]!.config, 'positive PUT').data as string)).toStrictEqual(P1)
    expect(saveSpy.mock.calls[0]![2].isCurrent()).toBe(true)
    expect(store.getSnapshot(WS)).toMatchObject({ state: 'pending', layout: P0, draft: P1, pending: true })
    noNewAnnouncement(0)
    await act(async () => { puts[0]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(reads[2]!.config).not.toBeNull())
    await act(async () => { reads[2]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity(); noNewAnnouncement(0)
    expect(outcomes).toStrictEqual([{ index: 0, value: committed }])
    expect(publicOutcomes).toStrictEqual([{ label: 'positive', value: undefined }])
    expect(saveSpy.mock.results[0]!.value).toBe(positiveSDK); expect(finalizeSpy.mock.results[0]!.value).toBe(positiveTask)
    expect(store.getSnapshot(WS)).toMatchObject({ layout: P1, writable: true, recovery: null, pending: false, readPending: false })
    expect(screen.queryByText(t('dashboard.spendingByCategory'))).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: customizeName })).toBeEnabled()

    owner.phase = 'failed-save'
    const edit = await change('dashboard.widgets.balanceEvolution', P2)
    const beforeOriginal = [...calls]
    if (closeMode === 'explicit Done') await done(edit.editor)
    else await away(true) // Real Back while modal is live: no Done/close hook call.
    await waitFor(() => expect(puts[1]!.config).not.toBeNull()); await drainFreeWork()
    expect(JSON.parse(required(puts[1]!.config, 'original failed PUT').data as string)).toStrictEqual(P2)
    expect(finalizeSpy).toHaveBeenCalledTimes(2); expect(finalizeSpy.mock.calls[1]![0]).toBe(edit.sessionId)
    const originalSDK = observeSave(1)
    const originalTask = observePublic('original', finalizeSpy.mock.results[1]!.value as Promise<void>)
    expect(saveSpy.mock.calls[1]![2].isCurrent()).toBe(true)
    expect(puts[1]!.settled).toBe(false)
    expect(store.getSnapshot(WS)).toMatchObject({ state: 'pending', layout: P1, openedBaseline: P1, draft: P2, pending: true })
    noNewAnnouncement(0)
    const beforeFailure = [...calls]
    await act(async () => { puts[1]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity()
    expect(calls).toStrictEqual(beforeFailure) // 503 never silently Rechecks.
    const originalError = required(nativeErrors[0], 'original native 503')
    expect(originalError).toBeInstanceOf(axiosModule.AxiosError)
    expect(originalError.config).toBe(puts[1]!.config); expect(originalError.response?.config).toBe(puts[1]!.config)
    expect(originalError.response?.status).toBe(503)
    expect(originalError.message).toBe('TEST_DATA private attempt 1 503')
    expect(originalError.response?.data).toStrictEqual({ detail: 'TEST_DATA private backend attempt 1' })
    expect(outcomes[1]!.error).toBe(originalError); expect(publicOutcomes[1]!.error).toBe(originalError)
    const originalCapture = required(store.getSnapshot(WS).recovery, 'original native failure capture')
    const originalPreimage = structuredClone(originalCapture)
    expect(originalCapture).toMatchObject({ actorId: ACTOR, authGeneration: generation, workspaceId: WS,
      baseline: P1, submitted: P2, category: 'unknown', phase: 'needs-recheck', recheck: null })
    expect(originalCapture.attemptId).toBe('dashboard-attempt-2')
    expectFrozen(originalCapture)
    if (closeMode === 'implicit browser Back') {
      expect(window.location.pathname).toBe('/categories')
      expect(screen.queryByText(t(key + 'unsaved'), { exact: true })).not.toBeInTheDocument()
      await back() // First visible retained failure must announce once, never reissue.
    }
    await newAnnouncement(1); pageFailureStatus()
    expect(screen.getByText(t('dashboard.balanceFlow'))).toBeVisible()
    expect(store.getSnapshot(WS).counters).toStrictEqual({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 1 })
    function originalRetained() {
      expect(originalCapture).toStrictEqual(originalPreimage); expectFrozen(originalCapture)
      expect(saveSpy.mock.results[1]!.value).toBe(originalSDK); expect(finalizeSpy.mock.results[1]!.value).toBe(originalTask)
      expect(outcomes[1]!.error).toBe(originalError); expect(publicOutcomes[1]!.error).toBe(originalError)
    }
    owner.phase = 'review'
    await repeatedReview(1)
    const firstReview = await recheck(3, 1)
    expect(firstReview.qualified.attemptId).toBe(originalCapture.attemptId)
    await closeReview(firstReview.ready)
    await away(); await back(); await drain(); pageFailureStatus(); noNewAnnouncement(1); originalRetained()
    // Route release retires qualification, not the attempt. A second real GET
    // qualifies the same failure again and must not emit another announcement.
    const secondReview = await recheck(4, 1)
    expect(secondReview.qualified.attemptId).toBe(originalCapture.attemptId)
    expect(secondReview.qualified.epoch).toBe(originalCapture.epoch)
    const modelResult = required(reviewSpy.mock.results.at(-1), 'native current reviewed ticket')
    expect(modelResult.type).toBe('return')
    if (modelResult.type !== 'return') throw new Error('Native review did not return')
    const model = modelResult.value
    expect(model.recovery).toBe(secondReview.qualified)
    expect(model.permissions).toStrictEqual({ recheck: true, retry: true, reapply: false, discard: true })
    const beforeRetry = [...calls]
    owner.phase = 'replacement-save'
    await user.dblClick(within(secondReview.ready).getByRole('button', { name: t(key + 'retry') }))
    await waitFor(() => expect(puts[2]!.config).not.toBeNull()); await drainFreeWork()
    expect(calls).toStrictEqual([...beforeRetry, puts[2]!.config]); expect(replaceSpy).toHaveBeenCalledTimes(1)
    expect(replaceSpy.mock.calls[0]).toStrictEqual([model.ticket, 'retry'])
    expect(JSON.parse(required(puts[2]!.config, 'replacement PUT').data as string)).toStrictEqual(P2)
    const replacementSDK = observeSave(2)
    const replacementTask = observePublic('retry', replaceSpy.mock.results[0]!.value as Promise<void>)
    const replacementSession = required(store.retain(WS), 'native replacement session')
    expect(replacementSession).not.toBe(edit.sessionId); expect(replacementSession).not.toBe(positive.sessionId)
    expect(saveSpy.mock.calls[2]![2].isCurrent()).toBe(true); expect(puts[2]!.settled).toBe(false)
    expect(store.getSnapshot(WS)).toMatchObject({ state: 'pending', layout: P1, openedBaseline: SERVER,
      draft: P2, recovery: null, pending: true, readPending: false })
    expect(store.getSnapshot(WS).counters).toStrictEqual({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 1 })
    expect(outcomes).toHaveLength(2); noNewAnnouncement(1); originalRetained()
    const beforeRetryFailure = [...calls]
    await act(async () => { puts[2]!.settle(); await tick(); await tick() })
    await drain(); stableIdentity()
    expect(calls).toStrictEqual(beforeRetryFailure)
    const replacementError = required(nativeErrors[1], 'replacement native 503')
    expect(replacementError).toBeInstanceOf(axiosModule.AxiosError); expect(replacementError).not.toBe(originalError)
    expect(replacementError.config).toBe(puts[2]!.config); expect(replacementError.response?.config).toBe(puts[2]!.config)
    expect(replacementError.response?.status).toBe(503)
    expect(replacementError.message).toBe('TEST_DATA private attempt 2 503')
    expect(replacementError.response?.data).toStrictEqual({ detail: 'TEST_DATA private backend attempt 2' })
    expect(outcomes[2]!.error).toBe(replacementError)
    expect(publicOutcomes.find((entry) => entry.label === 'retry')?.error).toBe(replacementError)
    expect(saveSpy.mock.results[2]!.value).toBe(replacementSDK); expect(replaceSpy.mock.results[0]!.value).toBe(replacementTask)
    const replacementCapture = required(store.getSnapshot(WS).recovery, 'new replacement failure capture')
    const replacementPreimage = structuredClone(replacementCapture)
    expect(replacementCapture).not.toBe(originalCapture)
    expect(replacementCapture.attemptId).not.toBe(originalCapture.attemptId)
    expect(replacementCapture.attemptId).toBe('dashboard-attempt-3')
    expect(replacementCapture.epoch).toBe(originalCapture.epoch + 1)
    expect(replacementCapture).toMatchObject({ actorId: ACTOR, authGeneration: generation, workspaceId: WS,
      baseline: SERVER, submitted: P2, category: 'unknown', phase: 'needs-recheck', recheck: null })
    expectFrozen(replacementCapture); originalRetained()
    expect(store.getSnapshot(WS)).toMatchObject({ state: 'unknown', layout: P1, writable: false, pending: false, readPending: false })
    expect(store.getSnapshot(WS).counters).toStrictEqual({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 2 })
    await newAnnouncement(2) // A genuinely NEW failed attempt, not a repeated old capture.
    const stillReviewed = screen.queryByRole('dialog', { name: reviewTitle })
    if (stillReviewed) await closeReview(stillReviewed)
    pageFailureStatus(); await repeatedReview(2)
    const retryReview = await recheck(5, 2)
    expect(retryReview.qualified.attemptId).toBe(replacementCapture.attemptId)
    await closeReview(retryReview.ready)
    await away(); await back(); await drain(); pageFailureStatus(); noNewAnnouncement(2)
    originalRetained(); expect(replacementCapture).toStrictEqual(replacementPreimage)
    expect(store.getSnapshot(WS).recovery).toMatchObject({ attemptId: replacementCapture.attemptId,
      epoch: replacementCapture.epoch, baseline: SERVER, submitted: P2, phase: 'needs-recheck', recheck: null })
    expect(store.getSnapshot(WS).counters).toStrictEqual({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 2 })
    expect(outcomes).toStrictEqual([{ index: 0, value: committed }, { index: 1, error: originalError }, { index: 2, error: replacementError }])
    expect(publicOutcomes).toStrictEqual([{ label: 'positive', value: undefined }, { label: 'original', error: originalError },
      { label: 'recheck-3', value: undefined }, { label: 'recheck-4', value: undefined },
      { label: 'retry', error: replacementError }, { label: 'recheck-5', value: undefined }])
    expect(finalizeSpy).toHaveBeenCalledTimes(2); expect(replaceSpy).toHaveBeenCalledTimes(1)
    expect(recheckSpy).toHaveBeenCalledTimes(3); expect(saveSpy).toHaveBeenCalledTimes(3); expect(meSpy).toHaveBeenCalledTimes(6)
    expect(userGets()).toHaveLength(6); expect(layoutPuts()).toHaveLength(3)
    expect(totals()).toStrictEqual({ ...startupTotals, 'get:/users/me': 6, [`put:/dashboard/layouts/${WS}`]: 3,
      'get:/categories?include_hidden=true': 1, 'get:/category-groups?include_hidden=true': 1 })
    expect(calls.slice(beforeOriginal.length).filter((config) => config.method === 'put')).toStrictEqual([puts[1]!.config, puts[2]!.config])
    expect(ports.every((port) => port.config !== null && port.settled)).toBe(true)
  } catch (error: unknown) { owner.record('failure'); primary = error; failed = true }
  finally {
    owner.phase = 'cleanup'
    owner.record('cleanup')
    inCleanup = true
    owner.retired = true
    owner.disconnect?.()
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
