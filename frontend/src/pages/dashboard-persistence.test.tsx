import { expect, it, vi } from 'vitest'
import type { AxiosAdapter, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import type { DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import type { SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { DashboardLayout } from '@/lib/dashboard-layout'
import type { DashboardSummary, User, Workspace } from '@/types'

// TEST_DATA only. Real Auth/provider/page/store/lease/SDK, adapter substitution only.
// Controlled WorkspaceContext and CollectionFilterContext are consumer seams,
// NOT actual WorkspaceProvider/App integration or browser acceptance.
const ACTOR = '88888888-8888-4888-8888-888888888888'
const A = '99999999-9999-4999-8999-999999999999'
const TOKEN = 'synthetic-visible-refresh-test-data'
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}
function required<T>(value: T | null | undefined, label: string): T {
  if (value === null || value === undefined) throw new Error(`Missing native Refresh witness: ${label}`)
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
  { id: 'period-transactions', visible: true, order: 0 },
  { id: 'hero-balance', visible: true, order: 1 },
  { id: 'uncategorized-banner', visible: true, order: 2 },
  { id: 'spending-charts', visible: false, order: 3 },
  { id: 'balance-evolution', visible: true, order: 4 },
  { id: 'goals-progress', visible: true, order: 5 },
] })
// RAW independent user/map/leaf literals, not request echoes or normalized fixtures.
const restoredUser: User = freeze({ id: ACTOR, email: 'restore-refresh@example.invalid',
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
const refreshedUser: User = freeze({ id: ACTOR, email: 'display-refresh@example.invalid',
  is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', dashboard_layouts: { [A]: {
    version: 1, workspaceId: A, items: [
      { id: 'period-transactions', visible: true, order: 0 },
      { id: 'hero-balance', visible: true, order: 1 },
      { id: 'uncategorized-banner', visible: true, order: 2 },
      { id: 'spending-charts', visible: false, order: 3 },
      { id: 'balance-evolution', visible: true, order: 4 },
      { id: 'goals-progress', visible: true, order: 5 },
    ],
  } } },
})
const workspace: Workspace = freeze({ id: A, name: 'TEST_DATA visible Refresh', kind: 'personal',
  timezone: null,
  is_archived: false, default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
  icon: null, color: null, created_at: '2026-10-06T00:00:00Z',
  created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
  enabled_modules: ['accounts', 'transactions', 'goals'],
})
const summary: DashboardSummary = freeze({ primary_currency: 'USD',
  balance_date: '2026-10-31', accounts_count: 0, pending_shares_net: 0,
  total_balance: { USD: 1000 }, total_balance_primary: 1000,
  projected_balance: { USD: 1000 }, projected_balance_primary: 1000,
  assets_value: { USD: 1200 }, assets_value_primary: 1200,
  monthly_income: 500, monthly_income_primary: 500,
  monthly_expenses: 300, monthly_expenses_primary: 300,
  projected_income: 500, projected_income_primary: 500,
  projected_expenses: 300, projected_expenses_primary: 300,
  pending_categorization: 0, pending_categorization_amount: 0,
})
const pageReplies = freeze({
  '/dashboard/summary': summary,
  '/dashboard/spending-by-category': [],
  '/dashboard/balance-history/months': [],
  '/dashboard/balance-history': { current: [{ day: 1, balance: 1000 }], opening_balance: 900, baseline: [], comparison: null, comparison_month: null, comparison_opening_balance: null },
  '/dashboard/projected-transactions': [],
  '/transactions': { items: [], total: 0, page: 1, size: 500 },
  '/groups': [], '/budgets/comparison': [], '/categories': [], '/category-groups': [],
  '/accounts': [], '/payees': [], '/goals/summary': [],
  '/admin/number-format': { format: 'auto' },
  '/admin/date-format': { format: 'auto' },
  '/admin/accounting-mode': { mode: 'cash' },
})
const zeroCounters = freeze({ success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })

it('visible Refresh restores ordinary Customize after a failed native display read without saving or granting recovery', async () => {
  vi.resetModules()
  const diagnostics: unknown[] = [], violations: unknown[] = [], asynchronous: unknown[] = []
  const restores: Array<() => void | Promise<void>> = []
  const fixtureInputs = [P0, P1, restoredUser, refreshedUser, workspace, pageReplies, zeroCounters]
  const preimages = structuredClone(fixtureInputs)
  const storageBefore = ['token', 'workspace_id', 'i18nextLng', 'privacyMode'].map((key) => ({ key, value: localStorage.getItem(key) }))
  const htmlLanguage = document.documentElement.lang
  let primary: unknown, failed = false, nativeCleanup: (() => Promise<void>) | null = null
  for (const { key, value } of storageBefore) restores.push(() => {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  })
  restores.push(() => { document.documentElement.lang = htmlLanguage })
  try {
    const axiosModule = await import('axios'), axios = axiosModule.default
    const priorAdapter = axios.defaults.adapter
    let http: AxiosInstance | null = null, cleaning = false
    const calls: InternalAxiosRequestConfig[] = []
    const tasks: Promise<void>[] = []
    const settledOutcomes: Array<{ index: number; error: unknown | null }> = []
    function slot() {
      let resolve!: (response: AxiosResponse<unknown>) => void, reject!: (reason: unknown) => void
      const task = new Promise<AxiosResponse<unknown>>((yes, no) => { resolve = yes; reject = no })
      return { task, config: null as InternalAxiosRequestConfig | null, settled: false,
        settle(data: unknown, error?: unknown) {
          if (!this.config || this.settled) return
          this.settled = true
          if (error !== undefined) reject(error)
          else resolve({ data, status: 200, statusText: 'OK', headers: {}, config: this.config })
        } }
    }
    const reads = [slot(), slot(), slot()]
    let networkError: InstanceType<typeof axiosModule.AxiosError> | null = null
    const state: { owner: DashboardLayoutOwner | null; auth: ReturnType<typeof import('@/contexts/auth-context').useAuth> | null;
      client: unknown; workspace: Workspace | null } = { owner: null, auth: null, client: null, workspace: null }
    const owners = new Set<DashboardLayoutOwner>(), contexts = new WeakSet<object>()
    const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
    const unsubscribes: Array<() => void> = []
    let publications = 0, queryEvents = 0, observations = 0
    const userGets = () => calls.filter((config) => config.method === 'get' && config.url === '/users/me')
    function forbid(label: string) {
      const error = new Error(`Refresh TEST_DATA fixture violation: ${label}`)
      violations.push(error)
      return Promise.reject<AxiosResponse<unknown>>(error)
    }
    const adapter: AxiosAdapter = (config) => {
      calls.push(config)
      let task: Promise<AxiosResponse<unknown>>
      if (config.baseURL !== '/api' || config.headers.get('Authorization') !== `Bearer ${TOKEN}` ||
          config.headers.get('X-Workspace-Id') !== A) task = forbid('real SDK headers/baseURL')
      else if (config.method !== 'get' || config.data !== undefined) task = forbid(`${config.method}:${config.url} (all writes forbidden)`)
      else if (config.url === '/users/me') {
        const index = userGets().length - 1, port = reads[index]
        if (!port || port.config) task = forbid('GET4 or reused transport slot')
        else {
          port.config = config
          if (index === 1) networkError = new axiosModule.AxiosError('TEST_DATA ordinary display network failure',
            axiosModule.AxiosError.ERR_NETWORK, config)
          task = port.task
          tasks.push(task.then(() => { settledOutcomes.push({ index, error: null }) }, (error: unknown) => {
            settledOutcomes.push({ index, error })
          }))
          if (cleaning) port.settle(index === 2 ? refreshedUser : restoredUser, index === 1 ? networkError : undefined)
        }
      } else if (config.url && Object.hasOwn(pageReplies, config.url)) {
        task = Promise.resolve({ data: Reflect.get(pageReplies, config.url), status: 200, statusText: 'OK', headers: {}, config })
      } else task = forbid(`unmatched ${config.method}:${config.url}`)
      tasks.push(task.then(() => {}, () => {})) // Observe without replacing SDK/native error handling.
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
    const { WorkspaceContext, useWorkspace } = await import('@/contexts/workspace-context')
    const { CollectionFilterContext } = await import('@/contexts/collection-filter-context')
    const { DashboardLayoutProvider, useDashboardLayoutOwner } = await import('@/contexts/dashboard-layout-provider')
    const { getPageSessionLease } = await import('@/lib/dashboard-layout-page-session-lease')
    const { getDashboardAuthSnapshot } = await import('@/lib/dashboard-layout-auth-bridge')
    const { validateDashboardLayoutRequest } = await import('@/lib/dashboard-layout')
    const { DASHBOARD_WIDGETS } = await import('@/components/dashboard/widgets')
    const { TooltipProvider } = await import('@/components/ui/tooltip')
    const { default: DashboardPage } = await import('@/pages/dashboard')
    const api = await import('@/lib/api')
    const languageBefore = i18n.language
    restores.push(async () => { await i18n.changeLanguage(languageBefore) })
    await i18n.changeLanguage('en')
    expect(createSpy).toHaveBeenCalledTimes(1)
    expect(required<AxiosInstance>(http, 'installed real SDK client').defaults.adapter).toBe(adapter)
    const updateMe = vi.spyOn(api.auth, 'updateMe') // Passthrough observation, no API substitution.
    restores.push(() => { updateMe.mockRestore() })
    const client = createTestQueryClient()
    let root: ReturnType<typeof renderWithProviders> | null = null
    unsubscribes.push(client.getQueryCache().subscribe(() => { queryEvents += 1 }))
    function ReadonlyOwnerProbe() {
      const owner = useDashboardLayoutOwner(), auth = useAuth(), observedClient = useQueryClient(), selection = useWorkspace().current
      useLayoutEffect(() => {
        observations += 1; state.owner = owner; state.auth = auth; state.client = observedClient; state.workspace = selection
        if (!contexts.has(auth)) {
          contexts.add(auth)
          writerSpies.push(vi.spyOn(auth, 'updateUser')) // Original implementation preserved.
        }
        if (owner && !owners.has(owner)) {
          owners.add(owner)
          unsubscribes.push(owner.store.subscribe(() => { publications += 1 }))
          getPageSessionLease(owner.store) // Readonly identity lookup; never bind/select/open.
        }
      })
      return null
    }
    const seam = { current: workspace, workspaces: [workspace], isLoading: false,
      role: workspace.role, canManage: true, canWrite: true,
      enabledModules: ['accounts', 'transactions', 'goals'] as import('@/lib/modules').ModuleId[],
      hasModule: (id: import('@/lib/modules').ModuleId) => workspace.enabled_modules.includes(id),
      switchWorkspace: async () => { throw new Error('Unexpected TEST_DATA workspace switch') },
      refresh: async () => { throw new Error('Unexpected TEST_DATA workspace refresh') } }
    const collection = { collections: [], activeCollectionId: null, activeCollection: null,
      activeAccountIds: null, activeWalletIds: null,
      setActiveCollectionId: () => { throw new Error('Unexpected TEST_DATA collection switch') } }
    const onError = (event: ErrorEvent) => { asynchronous.push(event.error ?? event.message) }
    const onRejection = (event: PromiseRejectionEvent) => { asynchronous.push(event.reason) }
    window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
    restores.push(() => { window.removeEventListener('error', onError) })
    restores.push(() => { window.removeEventListener('unhandledrejection', onRejection) })
    async function flush() { await act(async () => { await tick(); await tick() }) }
    async function drain() {
      for (let turn = 0; turn < 12; turn += 1) {
        const before = [tasks.length, calls.length, publications, queryEvents, observations, owners.size]
        await act(async () => {
          await tick(); await tick(); await Promise.all(tasks)
          for (const owner of owners) {
            await getPageSessionLease(owner.store).whenIdle(); await owner.store.whenIdle()
          }
          await tick(); await tick()
        })
        await waitFor(() => expect(client.isFetching()).toBe(0))
        const after = [tasks.length, calls.length, publications, queryEvents, observations, owners.size]
        if (after.every((value, index) => value === before[index])) return
      }
      throw new Error('Refresh native task/publication fixed point not reached')
    }
    function zeroWrites() {
      expect(calls.filter((config) => config.method !== 'get')).toStrictEqual([])
      expect(updateMe).not.toHaveBeenCalled()
      expect(writerSpies.length).toBeGreaterThan(0)
      writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
      expect(violations).toStrictEqual([]); expect(asynchronous).toStrictEqual([])
      expect(fixtureInputs).toStrictEqual(preimages)
    }
    nativeCleanup = async () => {
      async function step(action: () => void | Promise<void>) {
        try { await action() } catch (error: unknown) { diagnostics.push(error) }
      }
      cleaning = true
      await step(async () => { await act(async () => {
        reads.forEach((port, index) => { port.settle(index === 2 ? refreshedUser : restoredUser, index === 1 ? networkError : undefined) })
        await tick(); await tick()
      }) })
      await step(drain)
      const countBeforeUnmount = calls.length
      await step(async () => { await act(async () => { root?.unmount(); root = null; await tick(); await tick() }) })
      await step(drain)
      await step(() => {
        expect(calls).toHaveLength(countBeforeUnmount)
        reads.filter((port) => port.config !== null).forEach((port) => { expect(port.settled).toBe(true) })
        zeroWrites()
      })
      for (const unsubscribe of unsubscribes) await step(unsubscribe)
      for (const spy of writerSpies) await step(() => { spy.mockRestore() })
      await step(() => { client.clear() })
    }
    localStorage.setItem('token', TOKEN); localStorage.setItem(api.WORKSPACE_STORAGE_KEY, A)
    localStorage.setItem('privacyMode', 'false')
    root = renderWithProviders(<AuthProvider><WorkspaceContext.Provider value={seam}>
      <CollectionFilterContext.Provider value={collection}><DashboardLayoutProvider>
        <ReadonlyOwnerProbe /><TooltipProvider><DashboardPage /></TooltipProvider>
      </DashboardLayoutProvider></CollectionFilterContext.Provider>
    </WorkspaceContext.Provider></AuthProvider>, { queryClient: client, route: '/?month=2026-10', reactStrictMode: false })
    await waitFor(() => expect(reads[0]!.config).not.toBeNull())
    await act(async () => { reads[0]!.settle(restoredUser); await tick(); await tick() })
    await waitFor(() => { expect(state.owner).not.toBeNull(); expect(reads[1]!.config).not.toBeNull() })
    const owner = required(state.owner, 'legitimately restored owner')
    const generation = getDashboardAuthSnapshot(client).generation
    const authWitness = required(state.auth?.user, 'restored Auth user')
    const bridgeBefore = getDashboardAuthSnapshot(client)
    function stable() {
      expect(state.owner).toBe(owner); expect([...owners]).toStrictEqual([owner])
      expect(owner.actorId).toBe(ACTOR); expect(owner.authGeneration).toBe(generation)
      expect(getDashboardAuthSnapshot(client)).toBe(bridgeBefore)
      expect(state.auth?.user).toBe(authWitness); expect(authWitness).toBe(restoredUser)
      expect(state.auth?.token).toBe(TOKEN); expect(state.auth?.isLoading).toBe(false)
      expect(state.client).toBe(client); expect(state.workspace).toBe(workspace)
      expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(api.WORKSPACE_STORAGE_KEY)).toBe(A)
    }
    stable()
    expect(owner.store.getSnapshot(A)).toMatchObject({ readPending: true, layout: P0, recovery: null, counters: zeroCounters })
    expect(networkError).toBeInstanceOf(axiosModule.AxiosError)
    await act(async () => { reads[1]!.settle(undefined, required(networkError, 'genuine adapter network error')); await tick(); await tick() })
    await drain(); await flush()
    stable(); zeroWrites()
    expect(userGets()).toHaveLength(2)
    expect(settledOutcomes).toStrictEqual([{ index: 0, error: null }, { index: 1, error: networkError }])
    const financial = calls.filter((config) => config.url !== '/users/me')
    expect(financial.map((config) => config.url).sort()).toStrictEqual(Object.keys(pageReplies).sort())
    for (const [raw, golden] of [[restoredUser, P0], [refreshedUser, P1]] as const) {
      const map = required(raw.preferences.dashboard_layouts, 'RAW preference map'), leaf = map[A]
      for (const value of [raw, raw.preferences, map, leaf]) expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
      expect(Object.hasOwn(raw, 'id')).toBe(true); expect(Object.hasOwn(raw, 'preferences')).toBe(true)
      expect(Object.hasOwn(raw.preferences, 'dashboard_layouts')).toBe(true); expect(Object.hasOwn(map, A)).toBe(true)
      expect(validateDashboardLayoutRequest(A, leaf, DASHBOARD_WIDGETS)).toStrictEqual(golden)
    }
    // Legacy exact shape remains immutable; safe display is P0, not a fake writable fallback.
    const failedRead: SessionSnapshot = owner.store.getSnapshot(A)
    expect(failedRead).toStrictEqual({ actorId: ACTOR, authGeneration: generation, workspaceId: A,
      provenance: 'saved', writable: false, state: 'closed', pending: false, readPending: false,
      canUndo: false, layout: P0, openedBaseline: null, draft: null, recovery: null, overlay: {}, counters: zeroCounters })
    expect(Object.isFrozen(failedRead)).toBe(true)
    const spending = screen.getByText(t('dashboard.spendingByCategory'))
    const period = screen.getByText(t('dashboard.periodTransactions'))
    expect(spending.compareDocumentPosition(period) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByRole('dialog', { name: t('dashboard.customize.title') })).not.toBeInTheDocument()

    // FIRST prospective behavior oracle: visible labelled Refresh, not a missing export/getter/key.
    const refresh = within(required(screen.getByText(t('dashboard.customize.persistence.stale')).closest<HTMLElement>('[role="status"]'), 'layout refresh status')).getByRole('button', { name: 'Refresh' })
    expect(refresh).toBeVisible(); expect(refresh).toBeEnabled()
    const customize = screen.getByRole('button', { name: t('dashboard.customize.action') })
    expect(customize).toBeDisabled()
    await required(root, 'page root').user.click(refresh)
    await waitFor(() => expect(reads[2]!.config).not.toBeNull())
    await flush()
    stable(); zeroWrites(); expect(userGets()).toHaveLength(3)
    expect(owner.store.getSnapshot(A)).toMatchObject({ readPending: true, pending: false, layout: P0, recovery: null, counters: zeroCounters })
    const heldRefresh = within(required(screen.getByText(t('dashboard.customize.persistence.stale')).closest<HTMLElement>('[role="status"]'), 'layout refresh status')).getByRole('button', { name: 'Refresh' })
    expect(heldRefresh).toBeDisabled(); expect(customize).toBeDisabled()
    // Real disabled UI direct event; not a fabricated native exact-task characterization.
    fireEvent.click(heldRefresh); await flush()
    expect(userGets()).toHaveLength(3); expect(reads[2]!.settled).toBe(false)
    await act(async () => { reads[2]!.settle(refreshedUser); await tick(); await tick() })
    await drain(); await flush()
    stable(); zeroWrites(); expect(userGets()).toHaveLength(3)
    expect(calls.filter((config) => config.url !== '/users/me')).toStrictEqual(financial)
    expect(owner.store.getSnapshot(A)).toStrictEqual({ actorId: ACTOR, authGeneration: generation, workspaceId: A,
      provenance: 'saved', writable: true, state: 'closed', pending: false, readPending: false,
      canUndo: false, layout: P1, openedBaseline: null, draft: null, recovery: null, overlay: {}, counters: zeroCounters })
    expect(screen.queryByText(t('dashboard.spendingByCategory'))).not.toBeInTheDocument()
    const periodP1 = screen.getByText(t('dashboard.periodTransactions'))
    const heroP1 = screen.getAllByText(t('dashboard.availableBalance'))[0]!
    expect(periodP1.compareDocumentPosition(heroP1) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const enabledCustomize = screen.getByRole('button', { name: t('dashboard.customize.action') })
    expect(enabledCustomize).toBeEnabled()
    await required(root, 'page root').user.click(enabledCustomize)
    const dialog = screen.getByRole('dialog', { name: t('dashboard.customize.title') })
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'editing', writable: true, openedBaseline: P1, draft: P1 })
    expect(within(dialog).getByRole('switch', { name: t('dashboard.customize.show', { name: t('dashboard.widgets.spendingCharts') }) })).toBeEnabled()
    await required(root, 'page root').user.click(within(dialog).getByRole('button', { name: t('dashboard.customize.done') }))
    await drain(); await flush()
    expect(screen.queryByRole('dialog', { name: t('dashboard.customize.title') })).not.toBeInTheDocument()
    stable(); zeroWrites(); expect(userGets()).toHaveLength(3)
    // Unchanged Done owns ONE native no-op suppression, not a Refresh/save success.
    expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed', writable: true, layout: P1,
      draft: null, openedBaseline: null, recovery: null, counters: { ...zeroCounters, suppressed: 1 } })
    expect(settledOutcomes).toStrictEqual([{ index: 0, error: null }, { index: 1, error: networkError }, { index: 2, error: null }])
  } catch (error: unknown) { primary = error; failed = true }
  finally {
    try { await nativeCleanup?.() } catch (error: unknown) { diagnostics.push(error) }
    // Restore language before storage/HTML preimages; preserve original primary assertion.
    for (const restore of [...restores].reverse()) {
      try { await restore() } catch (error: unknown) { diagnostics.push(error) }
    }
    try { vi.resetModules() } catch (error: unknown) { diagnostics.push(error) }
  }
  if (failed) {
    if (diagnostics.length) console.error('Refresh cleanup diagnostics; primary failure preserved', diagnostics)
    throw primary
  }
  if (diagnostics.length) throw new AggregateError(diagnostics, 'Refresh cleanup failed')
})
