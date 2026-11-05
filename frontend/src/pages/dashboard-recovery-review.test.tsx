import { expect, it, vi } from 'vitest'
// Preload the real page graph during collection; resetModules below still rebuilds
// the SDK/providers under the native transport spies inside the 5000ms test.
import '@/pages/dashboard'
import type { AxiosAdapter, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import type { DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import type { CommittedDashboardLayout, DashboardLayout } from '@/lib/dashboard-layout'
import type { DashboardSummary, User, Workspace } from '@/types'

// TEST_AUTHOR_ONLY / TEST_DATA. No future dialog import or controller substitution.
// Actual Auth + Workspace + Collection + Query + owner + lease + hook + page + Radix.
const ACTOR = '88888888-8888-4888-8888-888888888888'
const A = '99999999-9999-4999-8999-999999999999'
const TOKEN = 'synthetic-page-review-test-data'
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}
function required<T>(value: T | null | undefined, label: string): T {
  if (value === null || value === undefined) throw new Error(`Missing real page Review witness: ${label}`)
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
const seed: User = freeze({ id: ACTOR, email: 'page-review@example.invalid',
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
const postUser: User = freeze({ id: ACTOR, email: 'page-review-post@example.invalid',
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
const serverUser: User = freeze({ id: ACTOR, email: 'page-review-server@example.invalid',
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
const workspace: Workspace = freeze({ id: A, name: 'TEST_DATA page Review', kind: 'personal',
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

it('retained unknown save exposes readonly page Review and an explicit held Recheck without replaying the draft', async () => {
  vi.resetModules()
  const diagnostics: unknown[] = [], violations: unknown[] = [], asynchronous: unknown[] = [], consoleCalls: unknown[] = []
  const restores: Array<() => void | Promise<void>> = []
  const inputs = [P0, P1, P2, P3, seed, committed, postUser, serverUser, workspace, setupReplies, savedCounters]
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
    // Four distinct native reads: authentication, page entry, post-save, reviewed Recheck.
    const reads = [slot(seed), slot(seed), slot(postUser), slot(serverUser)]
    const puts = [slot(committed), slot(undefined)]
    const allPorts = [...reads, ...puts]
    let originalError: InstanceType<typeof axiosModule.AxiosError> | null = null
    const userGets = () => calls.filter((config) => config.method === 'get' && config.url === '/users/me')
    const layoutPuts = () => calls.filter((config) => config.method === 'put' && config.url === `/dashboard/layouts/${A}`)
    function forbid(label: string) {
      const error = new Error(`Page Review TEST_DATA transport violation: ${label}`)
      violations.push(error)
      return Promise.reject<AxiosResponse<unknown>>(error)
    }
    const adapter: AxiosAdapter = (config) => {
      calls.push(config)
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
        if (!port || port.config) task = forbid('PUT3/reused write')
        else {
          port.config = config
          if (index === 1) {
            originalError = new axiosModule.AxiosError('TEST_DATA original unknown network failure', axiosModule.AxiosError.ERR_NETWORK, config)
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
    const { act, screen, waitFor, fireEvent, within } = await import('@testing-library/react')
    const { useQueryClient } = await import('@tanstack/react-query')
    const { createTestQueryClient, renderWithProviders, t, i18n } = await import('@/test/utils')
    const { AuthProvider } = await import('@/contexts/auth-provider')
    const { useAuth } = await import('@/contexts/auth-context')
    const { WorkspaceProvider } = await import('@/contexts/workspace-provider')
    const { useWorkspace } = await import('@/contexts/workspace-context')
    const { CollectionFilterProvider } = await import('@/contexts/collection-filter-provider')
    const { DashboardLayoutProvider } = await import('@/contexts/dashboard-layout-provider')
    const { useDashboardLayoutOwner } = await import('@/contexts/dashboard-layout-context')
    const { getPageSessionLease } = await import('@/lib/dashboard-layout-page-session-lease')
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
    const saveSpy = vi.spyOn(api.dashboard, 'saveLayout') // Native passthrough; original returned promise.
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
    const state: { owner: DashboardLayoutOwner | null; auth: ReturnType<typeof useAuth> | null;
      workspace: Workspace | null; client: unknown } = { owner: null, auth: null, workspace: null, client: null }
    let publications = 0, observations = 0, queryEvents = 0
    let root: ReturnType<typeof renderWithProviders> | null = null
    unsubscribes.push(client.getQueryCache().subscribe(() => { queryEvents += 1 }))
    function ReadonlyOwnerProbe() {
      const owner = useDashboardLayoutOwner(), auth = useAuth(), selection = useWorkspace().current, observedClient = useQueryClient()
      useLayoutEffect(() => {
        observations += 1; state.owner = owner; state.auth = auth; state.workspace = selection; state.client = observedClient
        if (!contexts.has(auth)) { contexts.add(auth); writers.push(vi.spyOn(auth, 'updateUser')) }
        if (owner && !owners.has(owner)) {
          owners.add(owner); unsubscribes.push(owner.store.subscribe(() => { publications += 1 }))
        }
      })
      return null // Never bind another page holder or call a store/controller action.
    }
    const onError = (event: ErrorEvent) => { asynchronous.push(event.error ?? event.message) }
    const onRejection = (event: PromiseRejectionEvent) => { asynchronous.push(event.reason) }
    window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
    restores.push(() => { window.removeEventListener('error', onError); window.removeEventListener('unhandledrejection', onRejection) })
    async function flush() { await act(async () => { await tick(); await tick() }) }
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
      throw new Error('Page Review native task/publication fixed point not reached')
    }
    function cleanEvidence() {
      expect(violations).toStrictEqual([]); expect(asynchronous).toStrictEqual([]); expect(consoleCalls).toStrictEqual([])
      expect(inputs).toStrictEqual(preimages)
      expect(calls.filter((config) => config.method !== 'get' && config.method !== 'put')).toStrictEqual([])
      expect(layoutPuts().length).toBeLessThanOrEqual(2)
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
    for (const [raw, golden] of [[seed, P0], [postUser, P1], [serverUser, P3]] as const) {
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
    const mounted = required(root, 'real page root')
    const customizeName = t('dashboard.customize.action'), editorName = t('dashboard.customize.title')
    async function changeAndDone(widgetKey: string) {
      const customize = screen.getByRole('button', { name: customizeName })
      expect(customize).toBeEnabled(); await mounted.user.click(customize)
      const editor = screen.getByRole('dialog', { name: editorName })
      const toggle = within(editor).getByRole('switch', { name: t('dashboard.customize.hide', { name: t(widgetKey) }) })
      expect(toggle).toBeEnabled(); expect(toggle).toBeChecked(); await mounted.user.click(toggle)
      await mounted.user.click(within(editor).getByRole('button', { name: t('dashboard.customize.done') }))
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    }
    // Real changed-save positive precedes every prospective Review assertion.
    await changeAndDone('dashboard.widgets.spendingCharts')
    await waitFor(() => expect(puts[0]!.config).not.toBeNull())
    expect(JSON.parse(required(puts[0]!.config, 'PUT1').data as string)).toStrictEqual(P1)
    expect(saveSpy.mock.calls[0]![2].isCurrent()).toBe(true)
    observeSave(0)
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
    await changeAndDone('dashboard.widgets.goalsProgress')
    await waitFor(() => expect(puts[1]!.config).not.toBeNull())
    expect(JSON.parse(required(puts[1]!.config, 'PUT2').data as string)).toStrictEqual(P2)
    expect(saveSpy.mock.calls[1]![2].isCurrent()).toBe(true)
    const failedSDKPromise = observeSave(1)
    expect(originalError).toBeInstanceOf(axiosModule.AxiosError)
    await act(async () => { puts[1]!.settle(); await tick(); await tick() })
    await drain(); await flush(); stableOwner(); cleanEvidence()
    expect(userGets()).toHaveLength(3); expect(layoutPuts()).toHaveLength(2); expect(saveSpy).toHaveBeenCalledTimes(2)
    expect(sdkOutcomes).toStrictEqual([{ index: 0, value: committed }, { index: 1, error: originalError }])
    expect(saveSpy.mock.results[1]!.value).toBe(failedSDKPromise)
    const failedSnapshot = owner.store.getSnapshot(A), capture = required(failedSnapshot.recovery, 'native retained failure')
    expect(failedSnapshot).toMatchObject({ state: 'unknown', writable: false, pending: false, readPending: false,
      layout: P1, openedBaseline: P1, draft: P2, counters: savedCounters })
    expect(capture).toMatchObject({ actorId: ACTOR, authGeneration: generation, workspaceId: A,
      baseline: P1, submitted: P2, category: 'unknown', phase: 'needs-recheck', recheck: null })
    expectFrozen(failedSnapshot); expectFrozen(capture)
    expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    expect(owner.store.retain(A)).toBeNull() // Completed editor ticket cannot recover an editable draft.
    const capturedBaseline = capture.baseline, capturedDraft = capture.submitted, capturePreimage = structuredClone(capture)
    const beforeReview = [...calls], immutableCounters = failedSnapshot.counters
    function retainedEvidence() {
      stableOwner(); cleanEvidence()
      expect(layoutPuts()).toHaveLength(2); expect(saveSpy).toHaveBeenCalledTimes(2)
      expect(saveSpy.mock.results[1]!.value).toBe(failedSDKPromise)
      expect(sdkOutcomes[1]!.error).toBe(originalError)
      expect(capture).toStrictEqual(capturePreimage); expect(capture.baseline).toBe(capturedBaseline); expect(capture.submitted).toBe(capturedDraft)
      expect(owner.store.getSnapshot(A).counters).toStrictEqual(immutableCounters)
      expect(owner.store.getSnapshot(A).layout).toStrictEqual(P1)
      expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    }

    // FIRST NEW FUNCTIONAL RED: actual visible page button, not a missing module/port.
    const reviewTrigger = screen.getByRole('button', { name: 'Review' })
    expect(reviewTrigger).toBeVisible(); expect(reviewTrigger).toBeEnabled()
    const notice = required(reviewTrigger.closest<HTMLElement>('[role="status"], [role="alert"]'), 'scoped retained-unsaved notice')
    expect(notice).toHaveTextContent(/unsaved/i)
    expect(notice).toHaveTextContent(/reload/i); expect(notice).toHaveTextContent(/sign.?out/i)
    expect(screen.getByRole('button', { name: customizeName })).toBeDisabled()
    await mounted.user.click(within(notice).getByRole('button', { name: 'Review' }))
    await flush(); retainedEvidence()
    expect(calls).toStrictEqual(beforeReview); expect(owner.store.getSnapshot(A)).toBe(failedSnapshot)
    // Proposed explicit English presentation strings; no existing locale keys edited.
    const reviewTitle = 'Review unsaved dashboard changes'
    let dialog = screen.getByRole('dialog', { name: reviewTitle })
    const heading = within(dialog).getByRole('heading', { name: reviewTitle })
    expect(heading).toHaveAttribute('tabindex', '-1'); expect(heading).toHaveFocus()
    function readonlyDialog(element: HTMLElement) {
      expect(within(element).queryAllByRole('switch')).toStrictEqual([])
      expect(element.querySelectorAll('[draggable="true"], [aria-roledescription="sortable"]')).toHaveLength(0)
      expect(within(element).queryByRole('button', { name: t('dashboard.customize.reset') })).not.toBeInTheDocument()
    }
    function comparison(label: string, golden: DashboardLayout) {
      const list = within(dialog).getByRole('list', { name: label })
      const rows = within(list).getAllByRole('listitem')
      expect(rows).toHaveLength(golden.items.length)
      golden.items.forEach((item, index) => {
        const definition = required(DASHBOARD_WIDGETS.find((widget) => widget.id === item.id), 'registry label')
        expect(rows[index]).toHaveTextContent(t(definition.translationKey))
        expect(rows[index]).toHaveTextContent(item.visible ? /\bVisible\b/ : /\bHidden\b/)
        expect(rows[index]).toHaveTextContent(new RegExp(`\\bPosition\\s+${item.order + 1}\\b`, 'i'))
      })
    }
    readonlyDialog(dialog); comparison('Opened baseline', P1); comparison('Submitted draft', P2)
    const unavailable = within(dialog).getByRole('list', { name: 'Server layout' })
    expect(unavailable).toHaveTextContent(/unavailable/i)
    expect(within(unavailable).queryAllByRole('listitem')).toStrictEqual([])
    expect(dialog).toHaveTextContent(/reload/i); expect(dialog).toHaveTextContent(/sign.?out/i)
    expect(within(dialog).getByRole('button', { name: 'Recheck' })).toBeEnabled()
    expect(within(dialog).getByRole('button', { name: 'Discard draft' })).toBeEnabled()
    function forbiddenReplacement(element: HTMLElement, name: string) {
      const button = within(element).queryByRole('button', { name })
      if (button) expect(button).toBeDisabled()
    }
    forbiddenReplacement(dialog, 'Retry'); forbiddenReplacement(dialog, 'Reapply')
    await mounted.user.click(within(dialog).getByRole('button', { name: 'Recheck' }))
    await waitFor(() => expect(reads[3]!.config).not.toBeNull()); await flush()
    retainedEvidence(); expect(userGets()).toHaveLength(4); expect(reads[3]!.settled).toBe(false)
    expect(calls).toStrictEqual([...beforeReview, reads[3]!.config])
    expect(owner.store.getSnapshot(A)).toMatchObject({ readPending: true, pending: false,
      recovery: { phase: 'rechecking', category: 'unknown', baseline: P1, submitted: P2 } })
    dialog = screen.getByRole('dialog', { name: reviewTitle }); readonlyDialog(dialog)
    comparison('Opened baseline', P1); comparison('Submitted draft', P2)
    for (const name of ['Recheck', 'Discard draft']) {
      const button = within(dialog).getByRole('button', { name })
      expect(button).toBeDisabled(); fireEvent.click(button)
    }
    forbiddenReplacement(dialog, 'Retry'); forbiddenReplacement(dialog, 'Reapply')
    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeEnabled()
    await flush(); expect(userGets()).toHaveLength(4); expect(reads[3]!.settled).toBe(false)
    // Settle only the independently authored qualified target; explicitly drain real lease/store.
    await act(async () => { reads[3]!.settle(); await tick(); await tick() })
    await drain(); await flush(); retainedEvidence()
    const qualified = owner.store.getSnapshot(A), qualifiedRecovery = required(qualified.recovery, 'qualified recovery')
    expect(qualified).toMatchObject({ state: 'unknown', readPending: false, pending: false, writable: false,
      recovery: { phase: 'review-ready', category: 'unknown', recheck: { provenance: 'saved', target: P3, matchesSubmitted: false } } })
    expect(qualifiedRecovery.baseline).toBe(capturedBaseline); expect(qualifiedRecovery.submitted).toBe(capturedDraft)
    expectFrozen(qualifiedRecovery)
    dialog = screen.getByRole('dialog', { name: reviewTitle }); readonlyDialog(dialog)
    comparison('Opened baseline', P1); comparison('Submitted draft', P2); comparison('Server layout', P3)
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Retry' })).toBeEnabled())
    expect(within(dialog).getByRole('button', { name: 'Recheck' })).toBeEnabled()
    expect(within(dialog).getByRole('button', { name: 'Discard draft' })).toBeEnabled()
    forbiddenReplacement(dialog, 'Reapply')
    // Escape hides only readonly Review. No disabled-Customize focus-restoration fiction.
    await mounted.user.keyboard('{Escape}'); await drain(); await flush(); retainedEvidence()
    expect(screen.queryByRole('dialog', { name: reviewTitle })).not.toBeInTheDocument()
    expect(owner.store.getSnapshot(A)).toBe(qualified)
    const reopenedTrigger = screen.getByRole('button', { name: 'Review' })
    expect(reopenedTrigger).toBeVisible(); expect(reopenedTrigger).toBeEnabled()
    await mounted.user.click(reopenedTrigger); await drain(); await flush(); retainedEvidence()
    dialog = screen.getByRole('dialog', { name: reviewTitle }); readonlyDialog(dialog)
    expect(within(dialog).getByRole('heading', { name: reviewTitle })).toHaveFocus()
    comparison('Server layout', P3)
    expect(owner.store.getSnapshot(A)).toBe(qualified)
    expect(calls).toStrictEqual([...beforeReview, reads[3]!.config])
    const totals = calls.reduce<Record<string, number>>((counts, config) => {
      const key = `${config.method}:${config.url}`; counts[key] = (counts[key] ?? 0) + 1; return counts
    }, {})
    expect(totals['get:/users/me']).toBe(4); expect(totals[`put:/dashboard/layouts/${A}`]).toBe(2)
    expect(Object.keys(totals).filter((key) => !key.startsWith('get:') && key !== `put:/dashboard/layouts/${A}`)).toStrictEqual([])
    expect(sdkOutcomes).toStrictEqual([{ index: 0, value: committed }, { index: 1, error: originalError }])
    expect(allPorts.every((port) => port.config !== null && port.settled)).toBe(true)
  } catch (error: unknown) { primary = error; failed = true }
  finally {
    try { await nativeCleanup?.() } catch (error: unknown) { diagnostics.push(error) }
    for (const restore of [...restores].reverse()) {
      try { await restore() } catch (error: unknown) { diagnostics.push(error) }
    }
    try { vi.resetModules() } catch (error: unknown) { diagnostics.push(error) }
  }
  if (failed && diagnostics.length) throw new AggregateError([primary, ...diagnostics], 'Primary page Review failure plus cleanup diagnostics', { cause: primary })
  if (failed) throw primary
  if (diagnostics.length) throw new AggregateError(diagnostics, 'Page Review cleanup failed')
})
