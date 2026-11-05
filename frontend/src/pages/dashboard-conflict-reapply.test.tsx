import { expect, it, vi } from 'vitest'
// Preload the real page graph during collection; resetModules below still rebuilds
// the SDK/providers under the native transport spies inside the 5000ms test.
import '@/pages/dashboard'
import type { AxiosAdapter, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import type { PageBinding, DialogTicket } from '@/lib/dashboard-layout-page-session-lease'
import type { LivePageLeaseWitness, RecoveryReview, SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import type { CommittedDashboardLayout, DashboardLayout } from '@/lib/dashboard-layout'
import type { DashboardSummary, User, Workspace } from '@/types'

// AUTHOR_ONLY / TEST_DATA / NOT_RUN. Unchanged-product characterization, not predicted RED.
// Actual page/jsdom only; no App, browser, or accessibility certification.
// Actual Auth + Workspace + Collection + Query + owner + lease + hook + page + Radix.
const ACTOR = 'abababab-abab-4bab-8bab-abababababab'
const A = 'cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd'
const TOKEN = 'synthetic-conflict-reapply-test-data'
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}
function required<T>(value: T | null | undefined, label: string): T {
  if (value === null || value === undefined) throw new Error(`Missing real conflict Reapply witness: ${label}`)
  return value
}
function expectFrozen(value: unknown): void {
  if (value !== null && typeof value === 'object') {
    expect(Object.isFrozen(value)).toBe(true)
    Object.values(value).forEach(expectFrozen)
  }
}
const P0: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const P1: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: false, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const P2: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: false, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const P3: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'period-transactions', visible: true, order: 0 },
  { id: 'hero-balance', visible: false, order: 1 },
  { id: 'uncategorized-banner', visible: true, order: 2 },
  { id: 'spending-charts', visible: true, order: 3 },
  { id: 'balance-evolution', visible: true, order: 4 },
  { id: 'goals-progress', visible: true, order: 5 },
] })
// Independently authored RAW user/root/map/leaves and strict commit envelope.
// No response uses a submitted request, normalization, resolver or golden-layout alias.
const seed: User = freeze({ id: ACTOR, email: 'conflict-reapply@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', dashboard_layouts: { [A]: {
    version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } } },
})
const committed: CommittedDashboardLayout = freeze({ workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const postUser: User = freeze({ id: ACTOR, email: 'conflict-reapply-post@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', dashboard_layouts: { [A]: {
    version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } } },
})
const serverUser: User = freeze({ id: ACTOR, email: 'conflict-reapply-server@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', dashboard_layouts: { [A]: {
    version: 1, workspaceId: A, items: [
      { id: 'period-transactions', visible: true, order: 0 },
      { id: 'hero-balance', visible: false, order: 1 },
      { id: 'uncategorized-banner', visible: true, order: 2 },
      { id: 'spending-charts', visible: true, order: 3 },
      { id: 'balance-evolution', visible: true, order: 4 },
      { id: 'goals-progress', visible: true, order: 5 },
    ],
  } } },
})
const replacementCommit: CommittedDashboardLayout = freeze({ workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const replacementPost: User = freeze({ id: ACTOR, email: 'replacement-post@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', dashboard_layouts: { [A]: {
    version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } } },
})
const conflictData = freeze({ detail: 'TEST_DATA private original conflict diagnostic' })
const conflictCounters = freeze({ success: 1, suppressed: 0, conflict409: 1, other4xx: 0, server5xx: 0 })
const finalCounters = freeze({ success: 2, suppressed: 0, conflict409: 1, other4xx: 0, server5xx: 0 })
const workspace: Workspace = freeze({ id: A, name: 'TEST_DATA conflict Reapply', kind: 'personal',
  timezone: null,
  is_archived: false, default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
  icon: null, color: null, created_at: '2026-10-06T00:00:00Z', created_by_user_id: ACTOR,
  managed_by_user_id: null, role: 'owner', enabled_modules: ['accounts', 'transactions', 'goals'],
})
const summary: DashboardSummary = freeze({ primary_currency: 'USD', balance_date: '2026-10-31',
  accounts_count: 0, pending_shares_net: 0, total_balance: { USD: 1000 }, total_balance_primary: 1000,
  projected_balance: { USD: 1000 }, projected_balance_primary: 1000,
  assets_value: { USD: 1200 }, assets_value_primary: 1200,
  monthly_income: 500, monthly_income_primary: 500, monthly_expenses: 300, monthly_expenses_primary: 300,
  projected_income: 500, projected_income_primary: 500, projected_expenses: 300, projected_expenses_primary: 300,
  pending_categorization: 0, pending_categorization_amount: 0,
})
const setupReplies = freeze({
  '/workspaces': [workspace], '/collections': [],
  '/dashboard/summary': summary, '/dashboard/spending-by-category': [],
  '/dashboard/balance-history/months': [],
  '/dashboard/balance-history': { current: [{ day: 1, balance: 1000 }], opening_balance: 900, baseline: [], comparison: null, comparison_month: null, comparison_opening_balance: null },
  '/dashboard/projected-transactions': [], '/transactions': { items: [], total: 0, page: 1, size: 500 },
  '/groups': [], '/budgets/comparison': [], '/categories': [], '/category-groups': [],
  '/accounts': [], '/payees': [], '/goals/summary': [],
  '/admin/number-format': { format: 'auto' }, '/admin/date-format': { format: 'auto' },
  '/admin/accounting-mode': { mode: 'cash' },
})
const savedCounters = freeze({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })

it('retained unknown save exposes readonly conflict Reapply and an explicit held Recheck without replaying the draft', async () => {
  vi.resetModules()
  const diagnostics: unknown[] = [], violations: unknown[] = [], asynchronous: unknown[] = [], consoleCalls: unknown[] = []
  const restores: Array<() => void | Promise<void>> = []
  const inputs = [P0, P1, P2, P3, seed, committed, postUser, serverUser, workspace, setupReplies, savedCounters, replacementCommit, replacementPost, conflictData, conflictCounters, finalCounters]
  const preimages = structuredClone(inputs)
  const storage = ['token', 'workspace_id', 'i18nextLng', 'privacyMode', `securo.activeCollection.${A}`]
    .map((key) => ({ key, value: localStorage.getItem(key) }))
  const htmlLanguage = document.documentElement.lang
  for (const { key, value } of storage) restores.push(() => {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  })
  restores.push(() => { document.documentElement.lang = htmlLanguage })
  let primary: unknown, failed = false, nativeCleanup: (() => Promise<void>) | null = null
  try {
    for (const method of ['warn', 'error'] as const) {
      const original = console[method].bind(console)
      const spy = vi.spyOn(console, method).mockImplementation((...args: unknown[]) => {
        consoleCalls.push({ method, args }); original(...args)
      })
      restores.push(() => { spy.mockRestore() })
    }
    const axiosModule = await import('axios'), axios = axiosModule.default
    const priorAdapter = axios.defaults.adapter
    let http: AxiosInstance | null = null, cleaning = false
    const calls: InternalAxiosRequestConfig[] = [], tasks: Promise<void>[] = []
    const issuance: Array<{ config: InternalAxiosRequestConfig; owner: DashboardLayoutOwner | null; snapshot: SessionSnapshot | null }> = []
    let observeIssuance: (config: InternalAxiosRequestConfig) => void = () => {}
    function slot(data: unknown) {
      let resolve!: (response: AxiosResponse<unknown>) => void, reject!: (error: unknown) => void
      const task = new Promise<AxiosResponse<unknown>>((yes, no) => { resolve = yes; reject = no })
      return { task, data, config: null as InternalAxiosRequestConfig | null, settled: false,
        error: undefined as unknown,
        settle() {
          if (!this.config || this.settled) return
          this.settled = true
          if (this.error !== undefined) reject(this.error)
          else resolve({ data: this.data, status: 200, statusText: 'OK', headers: {}, config: this.config })
        } }
    }
    // Five finite native reads: Auth, entry, post-save1, autonomous current409, replacement postread.
    const reads = [slot(seed), slot(seed), slot(postUser), slot(serverUser), slot(replacementPost)]
    const puts = [slot(committed), slot(undefined), slot(replacementCommit)]
    const allPorts = [...reads, ...puts]
    let originalError: InstanceType<typeof axiosModule.AxiosError> | null = null
    const userGets = () => calls.filter((config) => config.method === 'get' && config.url === '/users/me')
    const layoutPuts = () => calls.filter((config) => config.method === 'put' && config.url === `/dashboard/layouts/${A}`)
    function forbid(label: string) {
      const error = new Error(`Conflict Reapply TEST_DATA transport violation: ${label}`)
      violations.push(error)
      return Promise.reject<AxiosResponse<unknown>>(error)
    }
    const adapter: AxiosAdapter = (config) => {
      calls.push(config); observeIssuance(config)
      let task: Promise<AxiosResponse<unknown>>
      if (config.baseURL !== '/api' || config.headers.get('Authorization') !== `Bearer ${TOKEN}` ||
          config.headers.get('X-Workspace-Id') !== A) task = forbid('real SDK headers/baseURL')
      else if (config.method === 'get' && config.url === '/users/me' && config.data === undefined) {
        const index = userGets().length - 1, port = reads[index]
        if (!port || port.config) task = forbid('extra/reused user GET')
        else {
          port.config = config; task = port.task
          if (cleaning) port.settle()
        }
      } else if (config.method === 'put' && config.url === `/dashboard/layouts/${A}`) {
        const index = layoutPuts().length - 1, port = puts[index]
        if (!port || port.config) task = forbid('PUT4/reused write')
        else {
          port.config = config
          const current = (config as InternalAxiosRequestConfig & { dashboardLayoutIsCurrent?: () => boolean }).dashboardLayoutIsCurrent
          if (typeof current !== 'function' || !current()) return forbid('native current write capability')
          if (typeof config.data !== 'string' || config.data !== JSON.stringify(index === 0 ? P1 : P2)) return forbid('independent exact write body')
          if (index === 1) {
            originalError = new axiosModule.AxiosError('TEST_DATA original native HTTP409', axiosModule.AxiosError.ERR_BAD_REQUEST, config,
              { testRequest: 'original-PUT2' }, { data: conflictData, status: 409, statusText: 'Conflict',
                headers: { 'x-test-conflict': 'original' }, config })
            port.error = originalError
          }
          task = port.task
          if (cleaning) port.settle()
        }
      } else if (config.method === 'get' && config.data === undefined && config.url && Object.hasOwn(setupReplies, config.url)) {
        task = Promise.resolve({ data: Reflect.get(setupReplies, config.url), status: 200, statusText: 'OK', headers: {}, config })
      } else task = forbid(`unexpected ${config.method}:${config.url}`)
      tasks.push(task.then(() => {}, () => {}))
      return task
    }
    axios.defaults.adapter = adapter
    restores.push(() => { axios.defaults.adapter = priorAdapter })
    const create = axios.create.bind(axios)
    const createSpy = vi.spyOn(axios, 'create').mockImplementation((config) => {
      const instance = create(config); http = instance
      restores.push(() => { instance.defaults.adapter = priorAdapter })
      return instance
    })
    restores.push(() => { createSpy.mockRestore() })
    const { useLayoutEffect } = await import('react')
    const { act, screen, waitFor, within } = await import('@testing-library/react')
    const { useQueryClient } = await import('@tanstack/react-query')
    const { createTestQueryClient, renderWithProviders, t, i18n } = await import('@/test/utils')
    const { AuthProvider } = await import('@/contexts/auth-provider')
    const { useAuth } = await import('@/contexts/auth-context')
    const { WorkspaceProvider } = await import('@/contexts/workspace-provider')
    const { useWorkspace } = await import('@/contexts/workspace-context')
    const { CollectionFilterProvider } = await import('@/contexts/collection-filter-provider')
    const { DashboardLayoutProvider } = await import('@/contexts/dashboard-layout-provider')
    const { useDashboardLayoutOwner } = await import('@/contexts/dashboard-layout-context')
    const { getPageSessionLease, isLivePageLeaseWitness } = await import('@/lib/dashboard-layout-page-session-lease')
    const { getDashboardAuthSnapshot } = await import('@/lib/dashboard-layout-auth-bridge')
    const { validateDashboardLayoutRequest, validateCommittedDashboardLayout } = await import('@/lib/dashboard-layout')
    const { DASHBOARD_WIDGETS } = await import('@/components/dashboard/widgets')
    const { TooltipProvider } = await import('@/components/ui/tooltip')
    const { default: DashboardPage } = await import('@/pages/dashboard')
    const api = await import('@/lib/api')
    const language = i18n.language
    restores.push(async () => { await i18n.changeLanguage(language) })
    await i18n.changeLanguage('en')
    expect(createSpy).toHaveBeenCalledTimes(1)
    expect(required<AxiosInstance>(http, 'actual SDK client').defaults.adapter).toBe(adapter)
    const errorDescriptors: Array<{ value: object; descriptors: PropertyDescriptorMap }> = []
    const nativeSave = api.dashboard.saveLayout.bind(api.dashboard)
    const saveSpy = vi.spyOn(api.dashboard, 'saveLayout').mockImplementation((...args: Parameters<typeof nativeSave>) => {
      const task = nativeSave(...args)
      // Observe after native Axios transformations, before the native store's await resumes.
      tasks.push(task.then(() => {}, (error: unknown) => {
        if (error === originalError && originalError) {
          for (const value of [originalError, originalError.response, originalError.config, originalError.response?.headers]) {
            if (value && typeof value === 'object') errorDescriptors.push({ value, descriptors: Object.getOwnPropertyDescriptors(value) })
          }
        }
      }))
      return task // Exact original SDK promise, not an async wrapper.
    })
    const patchSpy = vi.spyOn(api.auth, 'updateMe')
    restores.push(() => { saveSpy.mockRestore(); patchSpy.mockRestore() })
    const sdkOutcomes: Array<{ index: number; value?: CommittedDashboardLayout; error?: unknown }> = []
    function observeSave(index: number): Promise<CommittedDashboardLayout> {
      const result = required(saveSpy.mock.results[index], 'real SDK save result')
      expect(result.type).toBe('return')
      const promise = result.value as Promise<CommittedDashboardLayout>
      tasks.push(promise.then((value) => { sdkOutcomes.push({ index, value }) }, (error: unknown) => { sdkOutcomes.push({ index, error }) }))
      return promise
    }
    const client = createTestQueryClient()
    const owners = new Set<DashboardLayoutOwner>(), contexts = new WeakSet<object>()
    const writers: Array<ReturnType<typeof vi.spyOn>> = [], unsubscribes: Array<() => void> = []
    const native = new Map<DashboardLayoutOwner, {
      finalize: ReturnType<typeof vi.spyOn>; bind: ReturnType<typeof vi.spyOn>;
      review: ReturnType<typeof vi.spyOn>; recheck: ReturnType<typeof vi.spyOn>;
      reviewedRecheck: ReturnType<typeof vi.spyOn>; replace: ReturnType<typeof vi.spyOn>;
    }>()
    const bindingSpies: Array<ReturnType<typeof vi.spyOn>> = []
    const installSnapshots: SessionSnapshot[] = [], installSDKCounts: number[] = []
    const publicOutcomes: Array<{ label: string; value?: unknown; error?: unknown }> = []
    function observePublic(label: string, task: Promise<void>) {
      tasks.push(task.then((value) => { publicOutcomes.push({ label, value }) }, (error: unknown) => { publicOutcomes.push({ label, error }) }))
      return task
    }
    const state: { owner: DashboardLayoutOwner | null; auth: ReturnType<typeof useAuth> | null;
      workspace: Workspace | null; client: unknown } = { owner: null, auth: null, workspace: null, client: null }
    let publications = 0, observations = 0, queryEvents = 0
    observeIssuance = (config) => { issuance.push({ config, owner: state.owner, snapshot: state.owner?.store.getSnapshot(A) ?? null }) }
    let root: ReturnType<typeof renderWithProviders> | null = null
    unsubscribes.push(client.getQueryCache().subscribe(() => { queryEvents += 1 }))
    function ReadonlyOwnerProbe() {
      const owner = useDashboardLayoutOwner(), auth = useAuth(), selection = useWorkspace().current, observedClient = useQueryClient()
      useLayoutEffect(() => {
        observations += 1; state.owner = owner; state.auth = auth; state.workspace = selection; state.client = observedClient
        if (!contexts.has(auth)) { contexts.add(auth); for (const key of ['updateUser', 'login', 'verify2fa', 'loginWithToken', 'register', 'logout'] as const) writers.push(vi.spyOn(auth, key)) }
        if (owner && !owners.has(owner)) {
          owners.add(owner); unsubscribes.push(owner.store.subscribe(() => { publications += 1 }))
          const finalize = vi.spyOn(owner.store, 'finalize'), review = vi.spyOn(owner.store, 'reviewRecovery')
          const recheck = vi.spyOn(owner.store, 'recheckRecovery'), reviewedRecheck = vi.spyOn(owner.store, 'recheckReviewedRecovery')
          const replaceNative = owner.store.replaceRecovery.bind(owner.store)
          const replace = vi.spyOn(owner.store, 'replaceRecovery').mockImplementation((...args: Parameters<typeof replaceNative>) => {
            const task = replaceNative(...args)
            installSnapshots.push(owner.store.getSnapshot(A)); installSDKCounts.push(saveSpy.mock.calls.length) // Before queued SDK dispatch.
            return task
          })
          const lease = getPageSessionLease(owner.store), bindNative = lease.bind.bind(lease)
          const bind = vi.spyOn(lease, 'bind').mockImplementation((id) => {
            const binding = bindNative(id)
            for (const key of ['open', 'close', 'replaceRecovery', 'recheckRecovery', 'release'] as const) bindingSpies.push(vi.spyOn(binding, key))
            return binding
          })
          native.set(owner, { finalize, bind, review, recheck, reviewedRecheck, replace })
        }
      })
      return null // Never bind another page holder or call a store/controller action.
    }
    const onError = (event: ErrorEvent) => { asynchronous.push(event.error ?? event.message) }
    const onRejection = (event: PromiseRejectionEvent) => { asynchronous.push(event.reason) }
    window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
    restores.push(() => { window.removeEventListener('error', onError); window.removeEventListener('unhandledrejection', onRejection) })
    async function flush() { await act(async () => { await tick(); await tick() }) }
    // Never wait for whole-store idle while a deliberately held input is unresolved.
    async function drainFreeWork() {
      for (let turn = 0; turn < 16; turn += 1) {
        const before = [calls.length, publications, observations, queryEvents]
        await flush(); await waitFor(() => expect(client.isFetching()).toBe(0)); await flush()
        const after = [calls.length, publications, observations, queryEvents]
        if (after.every((value, index) => value === before[index])) return
      }
      throw new Error('Conflict Reapply free-work fixed point not reached')
    }
    async function drain() {
      for (let turn = 0; turn < 12; turn += 1) {
        const before = [tasks.length, calls.length, publications, observations, queryEvents, owners.size]
        await act(async () => {
          await tick(); await Promise.all(tasks)
          for (const owner of owners) {
            await getPageSessionLease(owner.store).whenIdle(); await owner.store.whenIdle()
          }
          await tick(); await tick()
        })
        await waitFor(() => expect(client.isFetching()).toBe(0))
        const after = [tasks.length, calls.length, publications, observations, queryEvents, owners.size]
        if (after.every((value, index) => value === before[index])) return
      }
      throw new Error('Conflict Reapply native task/publication fixed point not reached')
    }
    function cleanEvidence() {
      expect(violations).toStrictEqual([]); expect(asynchronous).toStrictEqual([]); expect(consoleCalls).toStrictEqual([])
      expect(inputs).toStrictEqual(preimages); inputs.forEach(expectFrozen)
      expect(calls.filter((config) => config.method !== 'get' && config.method !== 'put')).toStrictEqual([])
      expect(layoutPuts().length).toBeLessThanOrEqual(3)
      expect(patchSpy).not.toHaveBeenCalled()
      expect(writers.length).toBeGreaterThan(0)
      writers.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    }
    nativeCleanup = async () => {
      async function step(action: () => void | Promise<void>) {
        try { await action() } catch (error: unknown) { diagnostics.push(error) }
      }
      cleaning = true
      await step(async () => { await act(async () => { allPorts.forEach((port) => { port.settle() }); await tick(); await tick() }) })
      await step(drain)
      const beforeUnmount = calls.length
      await step(async () => { await act(async () => { root?.unmount(); root = null; await tick(); await tick() }) })
      await step(drain)
      await step(() => {
        expect(calls).toHaveLength(beforeUnmount)
        allPorts.filter((port) => port.config !== null).forEach((port) => { expect(port.settled).toBe(true) })
        cleanEvidence()
      })
      for (const unsubscribe of unsubscribes) await step(unsubscribe)
      await step(drain)
      for (const ports of native.values()) for (const spy of Object.values(ports)) await step(() => { spy.mockRestore() })
      for (const spy of bindingSpies) await step(() => { spy.mockRestore() })
      for (const spy of writers) await step(() => { spy.mockRestore() })
      await step(() => { client.clear() })
    }
    localStorage.setItem('token', TOKEN); localStorage.setItem(api.WORKSPACE_STORAGE_KEY, A)
    localStorage.setItem('privacyMode', 'false'); localStorage.removeItem(`securo.activeCollection.${A}`)
    root = renderWithProviders(<AuthProvider><WorkspaceProvider><CollectionFilterProvider><DashboardLayoutProvider>
      <ReadonlyOwnerProbe /><TooltipProvider><DashboardPage /></TooltipProvider>
    </DashboardLayoutProvider></CollectionFilterProvider></WorkspaceProvider></AuthProvider>,
    { queryClient: client, route: '/?month=2026-10', reactStrictMode: false })
    await waitFor(() => expect(reads[0]!.config).not.toBeNull())
    await act(async () => { reads[0]!.settle(); await tick(); await tick() })
    await waitFor(() => { expect(state.workspace).toBe(workspace); expect(reads[1]!.config).not.toBeNull() })
    await act(async () => { reads[1]!.settle(); await tick(); await tick() })
    await drain(); await flush()
    const owner = required(state.owner, 'actual authenticated owner'), generation = owner.authGeneration
    const bridge = getDashboardAuthSnapshot(client), authUser = required(state.auth?.user, 'restored user')
    function stableOwner() {
      expect(state.owner).toBe(owner); expect([...owners]).toStrictEqual([owner])
      expect(owner.actorId).toBe(ACTOR); expect(owner.authGeneration).toBe(generation)
      expect(getDashboardAuthSnapshot(client)).toBe(bridge); expect(state.auth?.user).toBe(authUser)
      expect(authUser).toBe(seed); expect(state.auth?.token).toBe(TOKEN); expect(state.auth?.isLoading).toBe(false)
      expect(state.workspace).toBe(workspace); expect(state.client).toBe(client)
      expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(api.WORKSPACE_STORAGE_KEY)).toBe(A)
    }
    stableOwner(); cleanEvidence()
    expect(userGets()).toHaveLength(2); expect(layoutPuts()).toHaveLength(0)
    expect(calls.filter((config) => config.url === '/workspaces')).toHaveLength(1)
    expect(calls.filter((config) => config.url === '/collections')).toHaveLength(1)
    expect(new Set(calls.filter((config) => config.url !== '/users/me').map((config) => config.url)))
      .toStrictEqual(new Set(Object.keys(setupReplies)))
    for (const [raw, golden] of [[seed, P0], [postUser, P1], [serverUser, P3], [replacementPost, P2]] as const) {
      const map = required(raw.preferences.dashboard_layouts, 'RAW map'), leaf = map[A]
      for (const value of [raw, raw.preferences, map, leaf]) expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
      expect(Object.hasOwn(raw, 'id')).toBe(true); expect(Object.hasOwn(raw, 'preferences')).toBe(true)
      expect(Object.hasOwn(raw.preferences, 'dashboard_layouts')).toBe(true); expect(Object.hasOwn(map, A)).toBe(true)
      expect(validateDashboardLayoutRequest(A, leaf, DASHBOARD_WIDGETS)).toStrictEqual(golden)
    }
    expect(validateCommittedDashboardLayout(A, committed, DASHBOARD_WIDGETS)).toStrictEqual({ workspaceId: A, layout: P1 })
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed', writable: true, layout: P0,
      pending: false, readPending: false, recovery: null, counters: { success: 0, suppressed: 0 } })
    expect(screen.getByText(t('dashboard.spendingByCategory'))).toBeVisible()
    expect(validateCommittedDashboardLayout(A, replacementCommit, DASHBOARD_WIDGETS)).toStrictEqual({ workspaceId: A, layout: P2 })
    const ports = required(native.get(owner), 'native passthrough ports')
    const pageBinding = required(ports.bind.mock.results[0]?.value as PageBinding | undefined, 'actual sole page binding')
    const mounted = required(root, 'real page root')
    const customizeName = t('dashboard.customize.action'), editorName = t('dashboard.customize.title')
    async function changeAndDone(widgetKey: string) {
      const customize = screen.getByRole('button', { name: customizeName })
      expect(customize).toBeEnabled(); await mounted.user.click(customize)
      const editor = screen.getByRole('dialog', { name: editorName })
      const toggle = within(editor).getByRole('switch', { name: t('dashboard.customize.hide', { name: t(widgetKey) }) })
      expect(toggle).toBeEnabled(); expect(toggle).toBeChecked(); await mounted.user.click(toggle)
      const session = required(owner.store.retain(A), 'native changed editor session')
      await mounted.user.click(within(editor).getByRole('button', { name: t('dashboard.customize.done') }))
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
      return session
    }
    // Real changed-save positive precedes every prospective Review assertion.
    const firstSession = await changeAndDone('dashboard.widgets.spendingCharts')
    await waitFor(() => expect(puts[0]!.config).not.toBeNull())
    expect(JSON.parse(required(puts[0]!.config, 'PUT1').data as string)).toStrictEqual(P1)
    expect(saveSpy.mock.calls[0]![2].isCurrent()).toBe(true)
    observeSave(0)
    observePublic('save1', ports.finalize.mock.results[0]!.value as Promise<void>)
    await act(async () => { puts[0]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(reads[2]!.config).not.toBeNull())
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed-success', layout: P1,
      pending: false, readPending: true, recovery: null, counters: savedCounters })
    expect(screen.queryByText(t('dashboard.spendingByCategory'))).not.toBeInTheDocument()
    await act(async () => { reads[2]!.settle(); await tick(); await tick() })
    await drain(); await flush(); stableOwner(); cleanEvidence()
    expect(owner.store.getSnapshot(A)).toMatchObject({ writable: true, layout: P1, pending: false,
      readPending: false, recovery: null, counters: savedCounters })
    expect(sdkOutcomes).toStrictEqual([{ index: 0, value: committed }])
    const failedSession = await changeAndDone('dashboard.widgets.goalsProgress')
    await waitFor(() => expect(puts[1]!.config).not.toBeNull()); await flush()
    const failedSDK = observeSave(1)
    const failedCore = observePublic('original409', ports.finalize.mock.results[1]!.value as Promise<void>)
    const closeSpy = vi.mocked(pageBinding.close), openSpy = vi.mocked(pageBinding.open)
    const oldDialogTicket = required(openSpy.mock.results[1]?.value as DialogTicket | undefined, 'real second editor ticket')
    expect(oldDialogTicket).toStrictEqual({ sessionId: failedSession, workspaceId: A })
    expect(failedSession).not.toBe(firstSession)
    const failedLease = observePublic('original409-lease', closeSpy.mock.results[1]!.value)
    const failedBinding = saveSpy.mock.calls[1]![2]
    expect(failedBinding.isCurrent()).toBe(true)
    const originalConfig = required(puts[1]!.config, 'original PUT2 transport')
    expect(originalConfig.data).toBe(JSON.stringify(P2))
    expect(required<AxiosInstance>(http, 'SDK instance').getUri(originalConfig)).toBe(`/api/dashboard/layouts/${A}`)
    expect(originalConfig.headers.get('Authorization')).toBe(`Bearer ${TOKEN}`)
    expect(originalConfig.headers.get('X-Workspace-Id')).toBe(A)
    const writeWitness = ports.finalize.mock.calls[1]![1] as LivePageLeaseWitness
    expect(isLivePageLeaseWitness(writeWitness, owner.store, A)).toBe(true)
    expect(writeWitness.store).toBe(owner.store); expect(writeWitness.workspaceId).toBe(A)
    const continuity = writeWitness.continuity
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'pending', pending: true, readPending: false,
      openedBaseline: P1, draft: P2, layout: P1, recovery: null, counters: savedCounters })
    expect(ports.finalize).toHaveBeenCalledTimes(2); expect(closeSpy).toHaveBeenCalledTimes(2)
    const beforeFailure = [...calls]
    await act(async () => { puts[1]!.settle(); await tick(); await tick() })
    // No manual action or idle wait can create/settle the automatic current409 read.
    await waitFor(() => expect(reads[3]!.config).not.toBeNull()); await drainFreeWork()
    expect(calls).toStrictEqual([...beforeFailure, reads[3]!.config])
    expect(ports.recheck).toHaveBeenCalledTimes(1); expect(ports.reviewedRecheck).not.toHaveBeenCalled()
    expect(reads[3]!.settled).toBe(false)
    expect(publicOutcomes).toStrictEqual([{ label: 'save1', value: undefined },
      { label: 'original409', error: originalError }, { label: 'original409-lease', error: originalError }])
    expect(sdkOutcomes).toStrictEqual([{ index: 0, value: committed }, { index: 1, error: originalError }])
    const nativeError = required<InstanceType<typeof axiosModule.AxiosError>>(originalError, 'native original409')
    expect(nativeError).toBeInstanceOf(axiosModule.AxiosError); expect(nativeError.response?.status).toBe(409)
    expect(nativeError.code).toBe(axiosModule.AxiosError.ERR_BAD_REQUEST)
    expect(nativeError.config).toBe(originalConfig); expect(nativeError.response?.config).toBe(originalConfig)
    expect(nativeError.request).toStrictEqual({ testRequest: 'original-PUT2' })
    expect(nativeError.response?.data).toBe(conflictData)
    expect(nativeError.response?.headers).toBeInstanceOf(axiosModule.AxiosHeaders)
    if (!(nativeError.response?.headers instanceof axiosModule.AxiosHeaders)) throw new Error('Native Axios response headers missing')
    expect(nativeError.response.headers.get('x-test-conflict')).toBe('original')
    const originalResponse = nativeError.response, originalHeaders = nativeError.response?.headers
    function originalEvidence() {
      stableOwner(); cleanEvidence()
      expect(saveSpy.mock.results[1]!.value).toBe(failedSDK)
      expect(ports.finalize.mock.results[1]!.value).toBe(failedCore)
      expect(closeSpy.mock.results[1]!.value).toBe(failedLease)
      expect(sdkOutcomes[1]!.error).toBe(nativeError)
      expect(publicOutcomes.find((outcome) => outcome.label === 'original409')?.error).toBe(nativeError)
      expect(publicOutcomes.find((outcome) => outcome.label === 'original409-lease')?.error).toBe(nativeError)
      expect(nativeError.response).toBe(originalResponse); expect(nativeError.response?.headers).toBe(originalHeaders)
      expect(nativeError.response?.data).toBe(conflictData); expect(nativeError.config).toBe(originalConfig)
      expect(errorDescriptors).toHaveLength(4)
      for (const { value, descriptors } of errorDescriptors) {
        const current = Object.getOwnPropertyDescriptors(value)
        expect(Reflect.ownKeys(current)).toStrictEqual(Reflect.ownKeys(descriptors))
        for (const key of Reflect.ownKeys(descriptors)) {
          const before = Reflect.get(descriptors, key) as PropertyDescriptor
          const after = Reflect.get(current, key) as PropertyDescriptor
          expect(Reflect.ownKeys(after)).toStrictEqual(Reflect.ownKeys(before))
          for (const field of Reflect.ownKeys(before)) expect(Reflect.get(after, field)).toBe(Reflect.get(before, field))
        }
      }
      expect(writeWitness.continuity).toBe(continuity); expect(writeWitness.isCurrent()).toBe(true)
      expect(ports.bind).toHaveBeenCalledTimes(1); expect(vi.mocked(pageBinding.release)).not.toHaveBeenCalled()
      expect(ports.reviewedRecheck).not.toHaveBeenCalled()
    }
    const held = owner.store.getSnapshot(A), capture = required(held.recovery, 'autonomous retained capture')
    const baseline = capture.baseline, submitted = capture.submitted, captureCopy = structuredClone(capture)
    expect(capture).toStrictEqual({ attemptId: 'dashboard-attempt-2', actorId: ACTOR, authGeneration: generation,
      workspaceId: A, epoch: 2, baseline: P1, submitted: P2, category: 'conflict', phase: 'rechecking', recheck: null })
    expect(held).toMatchObject({ state: 'conflict', writable: false, pending: false, readPending: true,
      layout: P1, openedBaseline: P1, draft: P2, counters: conflictCounters, overlay: { [A]: P1 } })
    expectFrozen(held); expect(owner.store.retain(A)).toBeNull()
    const readIssuance = required(issuance.find((event) => event.config === reads[3]!.config), 'autonomous read owner at adapter')
    expect(readIssuance.owner).toBe(owner); expect(readIssuance.snapshot?.recovery).toBe(capture)
    expect(readIssuance.snapshot?.readPending).toBe(true)
    const autonomous = observePublic('autonomous409-read', ports.recheck.mock.results[0]!.value as Promise<void>)
    expect(ports.recheck.mock.calls[0]).toStrictEqual([A, capture.attemptId])
    // Deduplication forwards ONLY the already-proven autonomous task: no new permission/read.
    const deduplicated = pageBinding.recheckRecovery(capture.attemptId)
    expect(deduplicated).toBe(autonomous)
    observePublic('deduplicated-read', deduplicated)
    await drainFreeWork(); originalEvidence()
    expect(ports.recheck).toHaveBeenCalledTimes(2)
    expect(ports.recheck.mock.results[1]!.value).toBe(autonomous)
    expect(userGets()).toHaveLength(4); expect(layoutPuts()).toHaveLength(2)
    expect(reads[3]!.settled).toBe(false); expect(owner.store.getSnapshot(A)).toBe(held)
    expect(screen.getByRole('button', { name: customizeName })).toBeDisabled()
    expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    expect(screen.getByText(t('dashboard.balanceFlow'))).toBeVisible()
    expect(screen.queryByText(t('dashboard.spendingByCategory'))).not.toBeInTheDocument()
    await act(async () => { reads[3]!.settle(); await tick(); await tick() })
    await drain(); await flush(); originalEvidence()
    const ready = owner.store.getSnapshot(A), qualified = required(ready.recovery, 'strict different RAW target')
    expect(ready).toMatchObject({ state: 'conflict', writable: false, pending: false, readPending: false,
      layout: P1, openedBaseline: P1, draft: P2, counters: conflictCounters })
    expect(qualified).toMatchObject({ attemptId: capture.attemptId, actorId: ACTOR, authGeneration: generation,
      workspaceId: A, epoch: capture.epoch, category: 'conflict', phase: 'review-ready',
      recheck: { provenance: 'saved', target: P3, matchesSubmitted: false } })
    expect(qualified.baseline).toBe(baseline); expect(qualified.submitted).toBe(submitted)
    expect(qualified.recheck?.target).not.toStrictEqual(P2); expect(qualified.recheck?.target).not.toStrictEqual(P1)
    expect(capture).toStrictEqual(captureCopy); expectFrozen(qualified)
    const key = 'dashboard.customize.persistence.', reviewTitle = t(key + 'reviewTitle')
    const trigger = screen.getByRole('button', { name: 'Review' })
    expect(trigger).toBeVisible(); expect(trigger).toBeEnabled()
    const unsavedNotice = required(trigger.closest<HTMLElement>('[role="status"]'), 'page unsaved notice')
    expect(unsavedNotice).toHaveTextContent(t(key + 'unsaved')); expect(unsavedNotice).toHaveTextContent(t(key + 'retention'))
    const beforeReview = [...calls]
    await mounted.user.click(trigger); await drain(); await flush(); originalEvidence()
    expect(calls).toStrictEqual(beforeReview)
    const dialog = screen.getByRole('dialog', { name: reviewTitle })
    expect(dialog).toBeVisible(); expect(within(dialog).getByRole('heading', { name: reviewTitle })).toHaveFocus()
    expect(within(dialog).queryAllByRole('switch')).toStrictEqual([])
    expect(dialog.querySelectorAll('[draggable="true"], [aria-roledescription="sortable"]')).toHaveLength(0)
    expect(within(dialog).queryByRole('button', { name: t('dashboard.customize.reset') })).not.toBeInTheDocument()
    function comparison(label: string, golden: DashboardLayout) {
      const rows = within(within(dialog).getByRole('list', { name: label })).getAllByRole('listitem')
      expect(rows).toHaveLength(golden.items.length)
      golden.items.forEach((item, index) => {
        const definition = required(DASHBOARD_WIDGETS.find((widget) => widget.id === item.id), 'registry comparison')
        expect(rows[index]).toHaveTextContent(t(definition.translationKey))
        expect(rows[index]).toHaveTextContent(t(key + (item.visible ? 'visible' : 'hidden')))
        expect(rows[index]).toHaveTextContent(t(key + 'position', { position: item.order + 1 }))
      })
    }
    comparison('Opened baseline', P1); comparison('Submitted draft', P2); comparison('Server layout', P3)
    const reapply = within(dialog).getByRole('button', { name: 'Reapply' })
    await waitFor(() => expect(reapply).toBeEnabled()); expect(reapply).toBeVisible()
    expect(within(dialog).queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    const review = required(ports.review.mock.results.at(-1)?.value as RecoveryReview | undefined, 'native page-issued readonly review')
    const reviewCall = ports.review.mock.calls.at(-1) as [string, string, LivePageLeaseWitness]
    expect(reviewCall.slice(0, 2)).toStrictEqual([A, capture.attemptId])
    expect(isLivePageLeaseWitness(reviewCall[2], owner.store, A)).toBe(true)
    expect(reviewCall[2].continuity).toBe(continuity)
    expect(review.recovery).toBe(qualified)
    expect(review.permissions).toStrictEqual({ recheck: true, discard: true, reapply: true, retry: false })
    expect(review.ticket).toStrictEqual({ attemptId: capture.attemptId, actorId: ACTOR, authGeneration: generation,
      workspaceId: A, epoch: capture.epoch, readId: qualified.recheck!.readId })
    expectFrozen(review)
    const beforeReplacement = [...calls]
    // Real visible double-click; the second UI activation must not dispatch another replacement.
    await mounted.user.dblClick(reapply)
    await waitFor(() => expect(puts[2]!.config).not.toBeNull()); await drainFreeWork()
    expect(ports.replace).toHaveBeenCalledTimes(1)
    expect(vi.mocked(pageBinding.replaceRecovery)).toHaveBeenCalledTimes(1)
    expect(ports.replace.mock.calls[0]).toStrictEqual([review.ticket, 'reapply'])
    expect(calls).toStrictEqual([...beforeReplacement, puts[2]!.config])
    const replacementSDK = observeSave(2)
    const replacementTask = observePublic('replacement', ports.replace.mock.results[0]!.value as Promise<void>)
    const replacementBinding = saveSpy.mock.calls[2]![2]
    const replacementSession = required(owner.store.retain(A), 'native replacement session')
    expect(replacementSession).not.toBe(failedSession); expect(replacementSession).not.toBe(firstSession)
    expect(installSnapshots).toHaveLength(1); expect(installSDKCounts).toStrictEqual([2])
    const pending = owner.store.getSnapshot(A), pendingCopy = structuredClone(pending)
    // Native replacement installs a new session and increments its epoch before queued transport.
    // Epoch is not a public snapshot field: old binding invalidation + new current binding is its public witness.
    expect(installSnapshots[0]).toBe(pending)
    expect(failedBinding.isCurrent()).toBe(false); expect(replacementBinding.isCurrent()).toBe(true)
    expect(pending).toMatchObject({ actorId: ACTOR, authGeneration: generation, workspaceId: A,
      state: 'pending', writable: true, pending: true, readPending: false, layout: P1,
      openedBaseline: P3, draft: P2, recovery: null, counters: conflictCounters, canUndo: false, overlay: { [A]: P1 } })
    expectFrozen(pending)
    const replacementIssuance = required(issuance.find((event) => event.config === puts[2]!.config), 'replacement at adapter')
    expect(replacementIssuance.owner).toBe(owner); expect(replacementIssuance.snapshot).toBe(pending)
    expect(JSON.parse(required(puts[2]!.config, 'replacement transport').data as string)).toStrictEqual(P2)
    expect(saveSpy.mock.calls[2]!.slice(0, 2)).toStrictEqual([A, P2])
    function pendingNotice() {
      const text = screen.getByText('Dashboard layout operation pending.', { exact: true })
      expect(text).toBeVisible()
      const status = required(text.closest<HTMLElement>('[role="status"]'), 'visible page pending status')
      expect(status.closest('[role="dialog"]')).toBeNull()
      expect(status).toHaveAttribute('aria-live', 'polite'); expect(status).toHaveAttribute('aria-busy', 'true')
      expect(screen.getByRole('button', { name: customizeName })).toBeDisabled()
      expect(screen.queryByText(t(key + 'unsaved'), { exact: true })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: reviewTitle })).not.toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
      for (const privateValue of [ACTOR, A, TOKEN, nativeError.message, conflictData.detail]) expect(document.body.textContent).not.toContain(privateValue)
    }
    pendingNotice(); originalEvidence()
    await mounted.user.click(screen.getByRole('button', { name: customizeName })); await drainFreeWork()
    pendingNotice(); expect(ports.finalize).toHaveBeenCalledTimes(2); expect(layoutPuts()).toHaveLength(3)
    expect(puts[2]!.settled).toBe(false)
    expect(sdkOutcomes).toHaveLength(2); expect(publicOutcomes.some((outcome) => outcome.label === 'replacement')).toBe(false)
    const heldReplacementCalls = [...calls]
    // Public native capability negative control: consumed original ticket cannot replay while held.
    await expect(pageBinding.replaceRecovery(review.ticket, 'reapply')).rejects.toThrow('Dashboard recovery replacement unavailable')
    await drainFreeWork(); pendingNotice(); originalEvidence()
    expect(calls).toStrictEqual(heldReplacementCalls)
    expect(owner.store.getSnapshot(A)).toStrictEqual(pendingCopy); expect(owner.store.retain(A)).toBe(replacementSession)
    expect(ports.replace).toHaveBeenCalledTimes(2); expect(installSnapshots[1]).toBe(pending)
    expect(saveSpy).toHaveBeenCalledTimes(3); expect(ports.finalize).toHaveBeenCalledTimes(2)
    expect(saveSpy.mock.results[2]!.value).toBe(replacementSDK); expect(ports.replace.mock.results[0]!.value).toBe(replacementTask)
    expect(userGets()).toHaveLength(4); expect(layoutPuts()).toHaveLength(3)
    await act(async () => { puts[2]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(reads[4]!.config).not.toBeNull())
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed-success', pending: false, readPending: true,
      layout: P2, recovery: null, counters: finalCounters })
    expect(replacementBinding.isCurrent()).toBe(false) // Successful consumption, not owner retirement.
    expect(reads[4]!.settled).toBe(false)
    await act(async () => { reads[4]!.settle(); await tick(); await tick() })
    await drain(); await flush(); originalEvidence()
    expect(calls).toStrictEqual([...heldReplacementCalls, reads[4]!.config])
    expect(owner.store.getSnapshot(A)).toStrictEqual({ actorId: ACTOR, authGeneration: generation, workspaceId: A,
      provenance: 'saved', writable: true, state: 'closed-success', pending: false, readPending: false, canUndo: false,
      layout: P2, openedBaseline: null, draft: null, recovery: null, overlay: { [A]: P2 }, counters: finalCounters })
    expect(sdkOutcomes).toStrictEqual([{ index: 0, value: committed }, { index: 1, error: nativeError }, { index: 2, value: replacementCommit }])
    expect(publicOutcomes).toStrictEqual([{ label: 'save1', value: undefined }, { label: 'original409', error: nativeError },
      { label: 'original409-lease', error: nativeError }, { label: 'autonomous409-read', value: undefined },
      { label: 'deduplicated-read', value: undefined }, { label: 'replacement', value: undefined }])
    expect(capture).toStrictEqual(captureCopy); expect(qualified.baseline).toBe(baseline); expect(qualified.submitted).toBe(submitted)
    expect(screen.getByRole('button', { name: customizeName })).toBeEnabled()
    expect(screen.queryByText(t(key + 'pending'), { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText(t(key + 'unsaved'), { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: reviewTitle })).not.toBeInTheDocument()
    expect(screen.queryByText(t('dashboard.spendingByCategory'))).not.toBeInTheDocument()
    const totals = calls.reduce<Record<string, number>>((counts, config) => {
      const name = `${config.method}:${config.url}`; counts[name] = (counts[name] ?? 0) + 1; return counts
    }, {})
    const expectedTotals: Record<string, number> = { 'get:/users/me': 5, [`put:/dashboard/layouts/${A}`]: 3 }
    Object.keys(setupReplies).forEach((url) => { expectedTotals[`get:${url}`] = 1 })
    expect(totals).toStrictEqual(expectedTotals)
    expect(allPorts.every((port) => port.config !== null && port.settled)).toBe(true)
  } catch (error: unknown) { primary = error; failed = true }
  finally {
    try { await nativeCleanup?.() } catch (error: unknown) { diagnostics.push(error) }
    for (const restore of [...restores].reverse()) {
      try { await restore() } catch (error: unknown) { diagnostics.push(error) }
    }
    try { vi.resetModules() } catch (error: unknown) { diagnostics.push(error) }
  }
  if (failed && diagnostics.length) throw new AggregateError([primary, ...diagnostics], 'Primary conflict Reapply failure plus cleanup diagnostics', { cause: primary })
  if (failed) throw primary
  if (diagnostics.length) throw new AggregateError(diagnostics, 'Conflict Reapply cleanup failed')
})
