import { expect, it, vi } from 'vitest'
// Preload the real page graph during collection; resetModules below still rebuilds
// the SDK/providers under the native transport spies inside the 5000ms test.
import '@/pages/dashboard'
import type { AxiosAdapter, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import type { DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import type { CommittedDashboardLayout, DashboardLayout } from '@/lib/dashboard-layout'
import type { PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import type { DashboardSummary, User, Workspace } from '@/types'

// TEST_AUTHOR_ONLY / NOT_RUN / synthetic TEST_DATA.
// Native Query/Auth/Workspace/Collection/provider/page/store/lease/hook/Radix.
// Only the Axios adapter supplies server responses. All spies are passthrough.
const ACTOR = '88888888-8888-4888-8888-888888888888'
const A = '99999999-9999-4999-8999-999999999999'
const TOKEN = 'synthetic-page-editor-pending-test-data'
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}
function required<T>(value: T | null | undefined, label: string): T {
  if (value === null || value === undefined) throw new Error(`Missing native editor pending witness: ${label}`)
  return value
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
  { id: 'uncategorized-banner', visible: true, order: 0 },
  { id: 'hero-balance', visible: true, order: 1 },
  { id: 'spending-charts', visible: false, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
// RAW literals independently authored from the goldens and PUT input.
const seed: User = freeze({ id: ACTOR, email: 'editor-seed@example.invalid',
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
const commit1: CommittedDashboardLayout = freeze({ workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const post1: User = freeze({ id: ACTOR, email: 'editor-post1@example.invalid',
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
const heldUser: User = freeze({ id: ACTOR, email: 'editor-held-read@example.invalid',
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
const commit0: CommittedDashboardLayout = freeze({ workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const post0: User = freeze({ id: ACTOR, email: 'editor-post0@example.invalid',
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
const workspace: Workspace = freeze({ id: A, name: 'TEST_DATA editor pending', kind: 'personal',
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
  '/workspaces': [workspace], '/collections': [], '/dashboard/summary': summary,
  '/dashboard/spending-by-category': [],
  '/dashboard/balance-history/months': [],
  '/dashboard/balance-history': { current: [{ day: 1, balance: 1000 }], opening_balance: 900, baseline: [], comparison: null, comparison_month: null, comparison_opening_balance: null },
  '/dashboard/projected-transactions': [], '/transactions': { items: [], total: 0, page: 1, size: 500 },
  '/groups': [], '/budgets/comparison': [], '/categories': [], '/category-groups': [],
  '/accounts': [], '/payees': [], '/goals/summary': [],
  '/admin/number-format': { format: 'auto' }, '/admin/date-format': { format: 'auto' },
  '/admin/accounting-mode': { mode: 'cash' },
})
const counters0 = freeze({ success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
const counters1 = freeze({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
const counters2 = freeze({ success: 2, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })

it('native focus read freezes the real editor without false keyboard announcements and dismissal waits before one changed save', async () => {
  vi.resetModules()
  const diagnostics: unknown[] = [], violations: unknown[] = [], asynchronous: unknown[] = [], consoleCalls: unknown[] = []
  const restores: Array<() => void | Promise<void>> = []
  const inputs = [P0, P1, P2, seed, commit1, post1, heldUser, commit0, post0, workspace, setupReplies, counters0, counters1, counters2]
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
    const outcomes: Array<{ label: string; value?: unknown; error?: unknown }> = []
    function observe<T>(label: string, task: Promise<T>): Promise<T> {
      tasks.push(task.then((value) => { outcomes.push({ label, value }) }, (error: unknown) => { outcomes.push({ label, error }) }))
      return task // Never wraps/replaces the promise returned to the product caller.
    }
    function slot(label: string, data: unknown) {
      let resolve!: (response: AxiosResponse<unknown>) => void
      const task = new Promise<AxiosResponse<unknown>>((yes) => { resolve = yes })
      return { label, task, data, config: null as InternalAxiosRequestConfig | null, settled: false,
        settle() {
          if (!this.config || this.settled) return
          this.settled = true
          resolve({ data: this.data, status: 200, statusText: 'OK', headers: {}, config: this.config })
        } }
    }
    const reads = [slot('auth', seed), slot('entry', seed), slot('post1', post1), slot('focus', heldUser), slot('post0', post0)]
    const puts = [slot('put1', commit1), slot('put0', commit0)], ports = [...reads, ...puts]
    const userGets = () => calls.filter((config) => config.method === 'get' && config.url === '/users/me')
    const layoutPuts = () => calls.filter((config) => config.method === 'put' && config.url === `/dashboard/layouts/${A}`)
    function forbid(label: string) {
      const error = new Error(`Editor pending TEST_DATA transport violation: ${label}`)
      violations.push(error)
      return Promise.reject<AxiosResponse<unknown>>(error)
    }
    const adapter: AxiosAdapter = (config) => {
      calls.push(config)
      let task: Promise<AxiosResponse<unknown>>
      if (config.baseURL !== '/api' || config.headers.get('Authorization') !== `Bearer ${TOKEN}` ||
          config.headers.get('X-Workspace-Id') !== A) task = forbid('real SDK headers/baseURL')
      else if ((config.method === 'get' && config.url === '/users/me' && config.data === undefined) ||
               (config.method === 'put' && config.url === `/dashboard/layouts/${A}`)) {
        const port = config.method === 'get' ? reads[userGets().length - 1] : puts[layoutPuts().length - 1]
        if (!port || port.config) task = forbid('extra/reused controlled transport')
        else {
          port.config = config; task = port.task
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
    const { DashboardLayoutProvider, useDashboardLayoutOwner } = await import('@/contexts/dashboard-layout-provider')
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
    const saveSpy = vi.spyOn(api.dashboard, 'saveLayout'), patchSpy = vi.spyOn(api.auth, 'updateMe')
    restores.push(() => { saveSpy.mockRestore(); patchSpy.mockRestore() })
    const client = createTestQueryClient(), owners = new Set<DashboardLayoutOwner>(), contexts = new WeakSet<object>()
    const writers: Array<ReturnType<typeof vi.spyOn>> = [], nativeSpies: Array<ReturnType<typeof vi.spyOn>> = []
    const unsubscribes: Array<() => void> = []
    const state: { owner: DashboardLayoutOwner | null; auth: ReturnType<typeof useAuth> | null;
      workspace: Workspace | null; client: unknown; bind: ReturnType<typeof vi.spyOn> | null } =
      { owner: null, auth: null, workspace: null, client: null, bind: null }
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
          // Observe the page's actual passive bind; never allocate another holder.
          state.bind = vi.spyOn(getPageSessionLease(owner.store), 'bind'); nativeSpies.push(state.bind)
        }
      })
      return null
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
      throw new Error('Editor pending native task/publication fixed point not reached')
    }
    function cleanEvidence() {
      expect(violations).toStrictEqual([]); expect(asynchronous).toStrictEqual([]); expect(consoleCalls).toStrictEqual([])
      expect(outcomes.filter((outcome) => Object.hasOwn(outcome, 'error'))).toStrictEqual([])
      expect(inputs).toStrictEqual(preimages)
      expect(calls.filter((config) => config.method !== 'get' && config.method !== 'put')).toStrictEqual([])
      expect(layoutPuts().length).toBeLessThanOrEqual(2); expect(patchSpy).not.toHaveBeenCalled()
      expect(writers.length).toBeGreaterThan(0); writers.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    }
    nativeCleanup = async () => {
      async function step(action: () => void | Promise<void>) {
        try { await action() } catch (error: unknown) { diagnostics.push(error) }
      }
      cleaning = true
      await step(async () => { await act(async () => { ports.forEach((port) => { port.settle() }); await tick(); await tick() }) })
      await step(drain)
      // If body failed with editor open, genuine final release may elect its one
      // save. The adapter settles newly issued cleanup ports, still inside act.
      const beforeUnmount = [...calls]
      await step(async () => { await act(async () => { root?.unmount(); root = null; await tick(); await tick() }) })
      await step(drain)
      if (!failed) await step(() => { expect(calls).toStrictEqual(beforeUnmount) })
      const finalCalls = [...calls]
      await step(drain)
      await step(() => {
        expect(calls).toStrictEqual(finalCalls)
        ports.filter((port) => port.config !== null).forEach((port) => { expect(port.settled).toBe(true) })
        cleanEvidence()
      })
      for (const unsubscribe of unsubscribes) await step(unsubscribe)
      for (const spy of [...nativeSpies, ...writers].reverse()) await step(() => { spy.mockRestore() })
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
    const owner = required(state.owner, 'native owner'), generation = owner.authGeneration
    const bridge = getDashboardAuthSnapshot(client), authUser = required(state.auth?.user, 'Auth user')
    const bindSpy = required(state.bind, 'page bind passthrough')
    expect(bindSpy).toHaveBeenCalledTimes(1)
    expect(bindSpy.mock.calls).toStrictEqual([[A]])
    expect(bindSpy.mock.results[0]!.type).toBe('return')
    const binding = bindSpy.mock.results[0]!.value as PageBinding
    const refreshSpy = vi.spyOn(binding, 'refreshDisplay'), closeSpy = vi.spyOn(binding, 'close')
    nativeSpies.push(refreshSpy, closeSpy)
    function stableOwner() {
      expect(state.owner).toBe(owner); expect([...owners]).toStrictEqual([owner])
      expect(owner.actorId).toBe(ACTOR); expect(owner.authGeneration).toBe(generation)
      expect(getDashboardAuthSnapshot(client)).toBe(bridge); expect(state.auth?.user).toBe(authUser); expect(authUser).toBe(seed)
      expect(state.auth?.token).toBe(TOKEN); expect(state.auth?.isLoading).toBe(false)
      expect(state.workspace).toBe(workspace); expect(state.client).toBe(client); expect(bindSpy).toHaveBeenCalledTimes(1)
      expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(api.WORKSPACE_STORAGE_KEY)).toBe(A)
    }
    stableOwner(); cleanEvidence()
    const financial = calls.filter((config) => config.url !== '/users/me')
    expect(financial.map((config) => config.url).sort()).toStrictEqual(Object.keys(setupReplies).sort())
    for (const [raw, golden] of [[seed, P0], [post1, P1], [heldUser, P1], [post0, P0]] as const) {
      const map = required(raw.preferences.dashboard_layouts, 'RAW map'), leaf = map[A]
      for (const value of [raw, raw.preferences, map, leaf]) expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
      expect(Object.hasOwn(raw, 'id')).toBe(true); expect(Object.hasOwn(raw, 'preferences')).toBe(true)
      expect(Object.hasOwn(raw.preferences, 'dashboard_layouts')).toBe(true); expect(Object.hasOwn(map, A)).toBe(true)
      expect(validateDashboardLayoutRequest(A, leaf, DASHBOARD_WIDGETS)).toStrictEqual(golden)
    }
    expect(validateCommittedDashboardLayout(A, commit1, DASHBOARD_WIDGETS)).toStrictEqual({ workspaceId: A, layout: P1 })
    expect(validateCommittedDashboardLayout(A, commit0, DASHBOARD_WIDGETS)).toStrictEqual({ workspaceId: A, layout: P0 })
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed', writable: true, layout: P0,
      pending: false, readPending: false, recovery: null, counters: counters0 })
    const mounted = required(root, 'real page root'), editorName = t('dashboard.customize.title')
    const customizeName = t('dashboard.customize.action')
    function editor() { return screen.getByRole('dialog', { name: editorName }) }
    function button(name: string) { return within(editor()).getByRole('button', { name }) }
    function liveRegions(element: HTMLElement) { return Array.from(element.querySelectorAll<HTMLElement>('[aria-live="polite"]')) }
    async function openEditor() {
      const customize = screen.getByRole('button', { name: customizeName })
      expect(customize).toBeEnabled(); await mounted.user.click(customize)
      expect(editor()).toBeVisible()
    }
    // Changed positive save: no fake disabled owner can pass this prerequisite.
    await openEditor()
    const spendingToggle = within(editor()).getByRole('switch', { name: t('dashboard.customize.hide', { name: t('dashboard.widgets.spendingCharts') }) })
    expect(spendingToggle).toBeChecked(); expect(spendingToggle).toBeEnabled(); await mounted.user.click(spendingToggle)
    expect(within(editor()).getByRole('switch', { name: t('dashboard.customize.show', { name: t('dashboard.widgets.spendingCharts') }) })).not.toBeChecked()
    await mounted.user.click(button(t('dashboard.customize.done')))
    await waitFor(() => expect(puts[0]!.config).not.toBeNull())
    expect(JSON.parse(required(puts[0]!.config, 'PUT1').data as string)).toStrictEqual(P1)
    expect(saveSpy.mock.calls[0]![2].isCurrent()).toBe(true)
    observe('sdk1', saveSpy.mock.results[0]!.value as Promise<CommittedDashboardLayout>)
    const close1 = observe('close1', closeSpy.mock.results[0]!.value as Promise<void>)
    await act(async () => { puts[0]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(reads[2]!.config).not.toBeNull())
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed-success', layout: P1,
      pending: false, readPending: true, counters: counters1 })
    await act(async () => { reads[2]!.settle(); await tick(); await tick() })
    await drain(); await flush(); stableOwner(); cleanEvidence()
    expect(closeSpy.mock.results[0]!.value).toBe(close1)
    expect(outcomes.find((outcome) => outcome.label === 'sdk1')).toStrictEqual({ label: 'sdk1', value: commit1 })
    expect(screen.queryByText(t('dashboard.spendingByCategory'))).not.toBeInTheDocument()
    expect(userGets()).toHaveLength(3); expect(layoutPuts()).toHaveLength(1)

    // Fresh editor, actual keyboard reorder/focus/announcement before pending.
    await openEditor()
    const heroName = t('dashboard.widgets.heroBalance')
    const heroHandle = button(t('dashboard.customize.dragHandle', { name: heroName }))
    await act(async () => { heroHandle.focus() })
    expect(heroHandle).toHaveFocus(); await mounted.user.keyboard('{ArrowDown}'); await flush()
    expect(owner.store.getSnapshot(A).draft).toStrictEqual(P2)
    expect(button(t('dashboard.customize.dragHandle', { name: heroName }))).toHaveFocus()
    const rowsAfterMove = within(editor()).getAllByRole('listitem')
    expect(rowsAfterMove).toHaveLength(6)
    expect(within(rowsAfterMove[0]!).getByRole('button', { name: t('dashboard.customize.dragHandle', { name: t('dashboard.widgets.uncategorizedBanner') }) })).toBeVisible()
    expect(within(rowsAfterMove[1]!).getByRole('button', { name: t('dashboard.customize.dragHandle', { name: heroName }) })).toBeVisible()
    expect(liveRegions(editor()).some((region) => region.textContent === t('dashboard.customize.announceMoved', { name: heroName, position: 2, total: 6 }))).toBe(true)
    const resetName = t('dashboard.customize.reset'), undoName = t('dashboard.customize.undo')
    await mounted.user.click(button(resetName)); await flush()
    expect(owner.store.getSnapshot(A)).toMatchObject({ draft: P0, canUndo: true })
    const undo = button(undoName); expect(undo).toBeEnabled()
    await mounted.user.click(undo); await flush()
    expect(owner.store.getSnapshot(A)).toMatchObject({ draft: P2, canUndo: false })
    await mounted.user.click(button(resetName)); await flush()
    expect(owner.store.getSnapshot(A)).toMatchObject({ openedBaseline: P1, draft: P0, canUndo: true })
    const staleUndo = button(undoName) // Capture the real pre-pending node; it may be detached by the native canUndo projection.
    const sessionId = required(owner.store.retain(A), 'fresh native editing session')
    const beforeFocus = [...calls]
    // Source-valid: hook lines 129-139 register a genuine native window focus read.
    // No invented refresh event; no extra hook consumer or lease holder.
    await act(async () => { window.dispatchEvent(new Event('focus')); await tick(); await tick() })
    await waitFor(() => expect(reads[3]!.config).not.toBeNull()); await flush()
    expect(refreshSpy).toHaveBeenCalledTimes(1)
    expect(refreshSpy.mock.results[0]!.type).toBe('return')
    const readTask = observe('focus-read', refreshSpy.mock.results[0]!.value as Promise<void>)
    const frozen = owner.store.getSnapshot(A), frozenPreimage = structuredClone(frozen)
    expect(frozen).toMatchObject({ state: 'editing', writable: true, layout: P1, openedBaseline: P1,
      draft: P0, pending: false, readPending: true, canUndo: false, recovery: null, counters: counters1 })
    expect(calls).toStrictEqual([...beforeFocus, reads[3]!.config]); expect(reads[3]!.settled).toBe(false)
    const heldPublications = publications
    await act(async () => { window.dispatchEvent(new Event('focus')); await tick(); await tick() })
    await flush()
    expect(refreshSpy).toHaveBeenCalledTimes(2); expect(refreshSpy.mock.results[1]!.value).toBe(readTask)
    expect(owner.store.getSnapshot(A)).toBe(frozen); expect(publications).toBe(heldPublications)
    expect(userGets()).toHaveLength(4); expect(layoutPuts()).toHaveLength(1)
    stableOwner(); cleanEvidence()

    // FIRST NEW FUNCTIONAL ORACLE: real checked-state switch must be disabled.
    // Current source lacks this wiring; actual failure is for parent to qualify.
    const switches = within(editor()).getAllByRole('switch')
    expect(switches).toHaveLength(6)
    expect(switches[0]).toBeDisabled()
    for (const item of P0.items) {
      const definition = required(DASHBOARD_WIDGETS.find((widget) => widget.id === item.id), 'widget definition')
      const name = t(definition.translationKey)
      const toggle = within(editor()).getByRole('switch', { name: t(item.visible ? 'dashboard.customize.hide' : 'dashboard.customize.show', { name }) })
      expect(toggle).toBeChecked(); expect(toggle).toBeDisabled()
      for (const key of ['dragHandle', 'moveUp', 'moveDown']) expect(button(t(`dashboard.customize.${key}`, { name }))).toBeDisabled()
    }
    expect(button(resetName)).toBeDisabled()
    const pendingUndo = within(editor()).queryByRole('button', { name: undoName })
    if (pendingUndo) expect(pendingUndo).toBeDisabled() // Source hides Undo when read makes session uneditable.
    const pendingRegion = required(editor().matches('[aria-busy="true"]') ? editor() : editor().querySelector<HTMLElement>('[aria-busy="true"]'), 'busy editor region')
    expect(pendingRegion).toHaveAttribute('aria-busy', 'true')
    // Proposed English presentation text; no lookup of a nonexistent locale key.
    const pendingStatus = within(editor()).getAllByRole('status').find((status) => status.textContent === 'Dashboard layout operation pending.')
    expect(required(pendingStatus, 'visible pending status')).toBeVisible()
    expect(pendingStatus).toHaveAttribute('aria-live', 'polite')
    const done = button(t('dashboard.customize.done')), close = button('Close')
    expect(done).toBeEnabled(); expect(close).toBeEnabled()
    await act(async () => { done.focus() }); expect(done).toHaveFocus()
    const announcements = liveRegions(editor()).map((region) => region.textContent)
    const controls = within(editor()).getAllByRole('button').filter((control) => control !== done && control !== close)
    // Direct events guard against stale callbacks even where disabled DOM already
    // blocks ordinary user events. Keydown is deliberately dispatched on handles.
    for (const toggle of switches) fireEvent.click(toggle)
    for (const control of controls) {
      fireEvent.click(control); fireEvent.keyDown(control, { key: 'ArrowDown' }); fireEvent.keyDown(control, { key: 'ArrowUp' })
    }
    fireEvent.click(staleUndo)
    await flush()
    expect(owner.store.getSnapshot(A)).toBe(frozen); expect(frozen).toStrictEqual(frozenPreimage)
    expect(owner.store.retain(A)).toBe(sessionId)
    expect(liveRegions(editor()).map((region) => region.textContent)).toStrictEqual(announcements)
    expect(done).toHaveFocus(); expect(publications).toBe(heldPublications)
    // Tab cannot enter any frozen switch/handle/move/reset/undo affordance.
    for (let step = 0; step < 4; step += 1) {
      await mounted.user.tab()
      expect([done, close, editor()]).toContain(document.activeElement)
    }
    expect(calls).toStrictEqual([...beforeFocus, reads[3]!.config]); cleanEvidence()

    // Escape remains native dismissal, but elected close waits for this exact read.
    await mounted.user.keyboard('{Escape}'); await flush()
    expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    expect(closeSpy).toHaveBeenCalledTimes(2)
    const close2 = observe('close2', closeSpy.mock.results[1]!.value as Promise<void>)
    expect(closeSpy.mock.calls[1]![0].sessionId).toBe(sessionId); expect(closeSpy.mock.calls[1]![1]).toBe(true)
    expect(close2).not.toBe(close1)
    expect(owner.store.getSnapshot(A)).toBe(frozen)
    expect(outcomes.some((outcome) => outcome.label === 'close2')).toBe(false)
    expect(layoutPuts()).toHaveLength(1); expect(saveSpy).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: customizeName })).toBeDisabled()
    expect(calls).toStrictEqual([...beforeFocus, reads[3]!.config])
    // Only now release the ordinary GET; the original close elects one real PUT.
    await act(async () => { reads[3]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(puts[1]!.config).not.toBeNull()); await flush()
    expect(outcomes.find((outcome) => outcome.label === 'focus-read')).toStrictEqual({ label: 'focus-read', value: undefined })
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'pending', readPending: false, pending: true,
      draft: P0, openedBaseline: P1, layout: P1, recovery: null, counters: counters1 })
    expect(JSON.parse(required(puts[1]!.config, 'PUT2').data as string)).toStrictEqual(P0)
    expect(saveSpy).toHaveBeenCalledTimes(2); expect(saveSpy.mock.calls[1]![2].isCurrent()).toBe(true)
    observe('sdk0', saveSpy.mock.results[1]!.value as Promise<CommittedDashboardLayout>)
    expect(calls).toStrictEqual([...beforeFocus, reads[3]!.config, puts[1]!.config])
    await act(async () => { puts[1]!.settle(); await tick(); await tick() })
    await waitFor(() => expect(reads[4]!.config).not.toBeNull())
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed-success', pending: false, readPending: true, layout: P0, counters: counters2 })
    await act(async () => { reads[4]!.settle(); await tick(); await tick() })
    await drain(); await flush(); stableOwner(); cleanEvidence()
    expect(owner.store.getSnapshot(A)).toStrictEqual({ actorId: ACTOR, authGeneration: generation, workspaceId: A,
      provenance: 'saved', writable: true, state: 'closed-success', pending: false, readPending: false,
      canUndo: false, layout: P0, openedBaseline: null, draft: null, recovery: null,
      overlay: { [A]: P0 }, counters: counters2 })
    expect(owner.store.retain(A)).toBeNull()
    expect(outcomes.find((outcome) => outcome.label === 'sdk0')).toStrictEqual({ label: 'sdk0', value: commit0 })
    expect(outcomes.filter((outcome) => outcome.label.startsWith('close'))).toStrictEqual([
      { label: 'close1', value: undefined }, { label: 'close2', value: undefined },
    ])
    expect(closeSpy.mock.results[1]!.value).toBe(close2)
    expect([...outcomes].sort((left, right) => left.label.localeCompare(right.label))).toStrictEqual([
      { label: 'close1', value: undefined }, { label: 'close2', value: undefined },
      { label: 'focus-read', value: undefined }, { label: 'sdk0', value: commit0 }, { label: 'sdk1', value: commit1 },
    ])
    expect(screen.getByText(t('dashboard.spendingByCategory'))).toBeVisible()
    expect(screen.getByRole('button', { name: customizeName })).toBeEnabled()
    expect(screen.queryByRole('dialog', { name: editorName })).not.toBeInTheDocument()
    expect(calls).toStrictEqual([...beforeFocus, reads[3]!.config, puts[1]!.config, reads[4]!.config])
    expect(calls.filter((config) => config.url !== '/users/me' && config.method !== 'put')).toStrictEqual(financial)
    expect(userGets()).toHaveLength(5); expect(layoutPuts()).toHaveLength(2)
    expect(saveSpy).toHaveBeenCalledTimes(2); expect(closeSpy).toHaveBeenCalledTimes(2); expect(refreshSpy).toHaveBeenCalledTimes(2)
    expect(ports.every((port) => port.config !== null && port.settled)).toBe(true)
  } catch (error: unknown) { primary = error; failed = true }
  finally {
    try { await nativeCleanup?.() } catch (error: unknown) { diagnostics.push(error) }
    for (const restore of [...restores].reverse()) {
      try { await restore() } catch (error: unknown) { diagnostics.push(error) }
    }
    try { vi.resetModules() } catch (error: unknown) { diagnostics.push(error) }
  }
  // No throw in finally; report original assertion separately from cleanup.
  if (failed && diagnostics.length) throw new AggregateError([primary, ...diagnostics], 'Primary editor pending failure plus cleanup diagnostics', { cause: primary })
  if (failed) throw primary
  if (diagnostics.length) throw new AggregateError(diagnostics, 'Editor pending cleanup failed')
})
