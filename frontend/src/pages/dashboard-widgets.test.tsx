import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import { useLayoutEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { getDashboardAuthSnapshot, subscribeDashboardAuth } from '@/lib/dashboard-layout-auth-bridge'
import { getPageSessionLease, type PageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import type { SessionStore } from '@/lib/dashboard-layout-session'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding, DashboardWidgetId } from '@/lib/dashboard-layout'
import { WORKSPACE_STORAGE_KEY } from '@/lib/api'

import DashboardPage from '@/pages/dashboard'
import { TooltipProvider } from '@/components/ui/tooltip'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { createTestQueryClient, renderWithProviders, t } from '@/test/utils'
import en from '@/locales/en.json'
import ptBR from '@/locales/pt-BR.json'
import type { User, Workspace } from '@/types'

const A = '11111111-1111-4111-8111-111111111111'
const ACTOR = '33333333-3333-4333-8333-333333333333'

const mocks = vi.hoisted(() => ({
  user: null as User | null,
}))

const api = vi.hoisted(() => ({
  dashboard: {
    saveLayout: vi.fn<(id: string, layout: DashboardLayout, binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
    summary: vi.fn(),
    spendingByCategory: vi.fn(),
    balanceHistory: vi.fn(),
    balanceHistoryMonths: vi.fn(),
    projectedTransactions: vi.fn(),
  },
  transactions: { list: vi.fn(), calendar: vi.fn(), get: vi.fn() },
  budgets: { comparison: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  goals: { summary: vi.fn() },
  groups: { list: vi.fn() },
  payees: { list: vi.fn() },
  auth: { me: vi.fn<() => Promise<User>>(), updateMe: vi.fn(),
    login: vi.fn(), verify2fa: vi.fn(), register: vi.fn() },
}))

// Keep the real module (every export other code imports) and override only
// the endpoints this page calls — no network in jsdom, same shapes as
// dashboard.tsx:160-248.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    dashboard: { ...actual.dashboard, ...api.dashboard },
    transactions: { ...actual.transactions, ...api.transactions },
    budgets: { ...actual.budgets, ...api.budgets },
    categories: { ...actual.categories, ...api.categories },
    categoryGroups: { ...actual.categoryGroups, ...api.categoryGroups },
    accounts: { ...actual.accounts, ...api.accounts },
    goals: { ...actual.goals, ...api.goals },
    groups: { ...actual.groups, ...api.groups },
    payees: { ...actual.payees, ...api.payees },
    auth: { ...actual.auth, ...api.auth },
  }
})

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({
    mask: (value: string) => value,
    privacyMode: false,
    MASK: '••••',
  }),
}))

function makeUser(preferences: User['preferences']): User {
  return {
    id: ACTOR,
    email: 'tester@example.com',
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_2fa_enabled: false,
    preferences,
  }
}

function storedLayout(order: DashboardWidgetId[], hidden: DashboardWidgetId[] = []): DashboardLayout {
  return {
    version: 1,
    workspaceId: A,
    items: order.map((id, index) => ({
      id,
      visible: !hidden.includes(id),
      order: index,
    })),
  }
}

function setUserLayout(layout: DashboardLayout) {
  mocks.user = makeUser({ dashboard_layouts: { [A]: layout } })
}

// Independent literal response and post-GET vectors; never derived from drafts,
// captured requests, production validators, or one another.
const savedReply: CommittedDashboardLayout = { workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'period-transactions', visible: true, order: 0 },
    { id: 'hero-balance', visible: true, order: 1 },
    { id: 'uncategorized-banner', visible: true, order: 2 },
    { id: 'spending-charts', visible: false, order: 3 },
    { id: 'balance-evolution', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} }
const savedPostUser = makeUser({ dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'period-transactions', visible: true, order: 0 },
    { id: 'hero-balance', visible: true, order: 1 },
    { id: 'uncategorized-banner', visible: true, order: 2 },
    { id: 'spending-charts', visible: false, order: 3 },
    { id: 'balance-evolution', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} } })
const separatedReply: CommittedDashboardLayout = { workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'goals-progress', visible: true, order: 3 },
    { id: 'balance-evolution', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} }
const separatedPostUser = makeUser({ dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'goals-progress', visible: true, order: 3 },
    { id: 'balance-evolution', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} } })
const defaultReply: CommittedDashboardLayout = { workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} }
const defaultPostUser = makeUser({ dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} } })

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}
function notifications(): Promise<void> {
  return new Promise((resolve) => { queueMicrotask(resolve) })
}

async function withPage(reply: CommittedDashboardLayout, postUser: User,
  body: (page: Awaited<ReturnType<typeof renderPage>>) => Promise<void>) {
  const page = renderPage(reply, postUser)
  let primary: unknown
  let failed = false
  try { await page.ready(); await body(page) }
  catch (failure: unknown) { primary = failure; failed = true }
  const diagnostics = await page.cleanup()
  if (failed || diagnostics.length > 0) {
    throw new AggregateError([...(failed ? [primary] : []), ...diagnostics],
      'Canonical page body/cleanup failure', failed ? { cause: primary } : undefined)
  }
}

function renderPage(reply: CommittedDashboardLayout, postUser: User) {
  const seed: User = mocks.user ?? (() => { throw new Error('Missing page Auth seed') })()
  const client = createTestQueryClient()
  const restoration = deferred<User>()
  const baseline = deferred<User>()
  const put = deferred<CommittedDashboardLayout>()
  const post = deferred<User>()
  const tasks: Promise<void>[] = []
  const barriers: Promise<void>[] = []
  const errors: unknown[] = []
  const owners = new Set<DashboardLayoutOwner>()
  const stores = new Set<SessionStore>()
  const helpers = new Set<PageSessionLease>()
  const subscriptions: Array<() => void> = []
  const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
  const contexts = new WeakSet<object>()
  const reads: Array<{ user: User | null; completed: boolean }> = []
  const requests: Array<{ target: string; body: DashboardLayout; current: boolean;
    actor: string | null; generation: number; client: typeof client;
    storage: string | null; context: string; binding: DashboardRequestBinding }> = []
  let publications = 0
  let bridgeEvents = 0
  let queryEvents = 0
  let observations = 0
  let currentOwner: DashboardLayoutOwner | null = null
  let establishedOwner: DashboardLayoutOwner | null = null
  let generation: number | null = null
  let root: ReturnType<typeof renderWithProviders> | null = null
  function track<T>(task: Promise<T>): Promise<T> {
    tasks.push(task.then(() => {}, (failure: unknown) => { errors.push(failure) }))
    return task
  }
  function barrier(task: Promise<void>) {
    const handled = task.catch((failure: unknown) => { errors.push(failure) })
    barriers.push(handled)
    return handled
  }
  function record(callback: () => void) {
    try { callback() } catch (failure: unknown) { errors.push(failure) }
  }
  const workspace: Workspace = {
    id: A, name: `Selection ${A}`, kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
    icon: null, color: null, created_at: '2026-10-04T00:00:00Z',
    created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
    enabled_modules: ['accounts', 'transactions', 'goals'],
  }
  const fixtures = [seed, reply, postUser, workspace]
  const preimages = structuredClone(fixtures)
  function Probe() {
    const owner = useDashboardLayoutOwner()
    const context = useAuth()
    const queryClient = useQueryClient()
    useLayoutEffect(() => {
      record(() => {
        observations += 1
        currentOwner = owner
        if (queryClient !== client) throw new Error('Page client changed')
        if (!contexts.has(context)) {
          contexts.add(context); writerSpies.push(vi.spyOn(context, 'updateUser'))
        }
        if (owner) {
          owners.add(owner)
          helpers.add(getPageSessionLease(owner.store)) // Read-only; NO bind/holder.
          if (!stores.has(owner.store)) {
            stores.add(owner.store)
            subscriptions.push(owner.store.subscribe(() => {
              record(() => { publications += 1; owner.store.getSnapshot(A) })
            }))
          }
        }
      })
    })
    return null
  }
  const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  const dataEndpoints = [api.dashboard.summary, api.dashboard.spendingByCategory,
    api.dashboard.balanceHistory, api.dashboard.projectedTransactions,
    api.transactions.list, api.transactions.calendar, api.transactions.get,
    api.budgets.comparison, api.categories.list, api.categoryGroups.list,
    api.accounts.list, api.goals.summary, api.groups.list, api.payees.list]
  const implementations = dataEndpoints.map((endpoint) => endpoint.getMockImplementation())
  dataEndpoints.forEach((endpoint, index) => {
    const implementation = implementations[index]
    if (!implementation) throw new Error('Missing preserved endpoint implementation')
    endpoint.mockImplementation((...args: unknown[]) => track(Promise.resolve(implementation(...args))))
  })
  api.auth.me.mockReset().mockImplementation(() => {
    const read = { user: null as User | null, completed: false }
    reads.push(read)
    const promise = reads.length === 1 ? restoration.promise : reads.length === 2 ? baseline.promise
      : reads.length === 3 ? post.promise : Promise.reject(new Error('Unexpected page GET'))
    return track(promise.then((user) => { read.user = user; read.completed = true; return user }))
  })
  api.dashboard.saveLayout.mockReset().mockImplementation((target, layout, binding) => {
    record(() => { requests.push({ target, body: structuredClone(layout), current: binding.isCurrent(),
      actor: currentOwner?.actorId ?? null, generation: getDashboardAuthSnapshot(client).generation,
      client, storage: localStorage.getItem(WORKSPACE_STORAGE_KEY), context: workspace.id, binding }) })
    return track(requests.length === 1 ? put.promise : Promise.reject(new Error('Unexpected page PUT')))
  })
  for (const endpoint of [api.auth.updateMe, api.auth.login, api.auth.verify2fa, api.auth.register]) {
    endpoint.mockReset().mockImplementation(() => track(Promise.reject(new Error('Forbidden generic Auth call'))))
  }
  const onError = (event: ErrorEvent) => { errors.push(event.error ?? event.message) }
  const onRejection = (event: PromiseRejectionEvent) => { errors.push(event.reason) }
  window.addEventListener('error', onError)
  window.addEventListener('unhandledrejection', onRejection)
  subscriptions.push(subscribeDashboardAuth(client, () => { record(() => { bridgeEvents += 1 }) }))
  subscriptions.push(client.getQueryCache().subscribe(() => { record(() => { queryEvents += 1 }) }))
  async function drain() {
    for (let turn = 0; turn < 8; turn += 1) {
      const before = [tasks.length, publications, bridgeEvents, queryEvents, observations,
        requests.length, reads.length, stores.size, helpers.size]
      await act(async () => {
        await Promise.all(tasks)
        await notifications(); await notifications()
        await Promise.all([...helpers].map((helper) => barrier(helper.whenIdle())))
        await Promise.all([...stores].map((store) => barrier(store.whenIdle())))
        await notifications(); await notifications()
        await Promise.all(tasks); await Promise.all(barriers)
      })
      // Query completion is supplementary, never the owner/lease/store barrier.
      await waitFor(() => expect(client.isFetching()).toBe(0))
      const after = [tasks.length, publications, bridgeEvents, queryEvents, observations,
        requests.length, reads.length, stores.size, helpers.size]
      if (after.every((value, index) => value === before[index])) return
    }
    throw new Error('Page did not quiesce within eight native turns')
  }
  function genericZero() {
    expect(api.auth.updateMe).not.toHaveBeenCalled()
    expect(api.auth.login).not.toHaveBeenCalled()
    expect(api.auth.verify2fa).not.toHaveBeenCalled()
    expect(api.auth.register).not.toHaveBeenCalled()
    expect(writerSpies.length).toBeGreaterThan(0)
    writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    expect(errors).toEqual([])
    expect(fixtures).toStrictEqual(preimages)
  }
  function stable() {
    expect(currentOwner).toBe(establishedOwner)
    expect([...owners]).toEqual([establishedOwner])
    expect(currentOwner?.actorId).toBe(ACTOR)
    expect(currentOwner?.authGeneration).toBe(generation)
    expect(getDashboardAuthSnapshot(client).generation).toBe(generation)
    expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(A)
  }
  async function ready() {
    // Private synthetic identity, never a shared-profile credential accessor.
    localStorage.setItem('token', 'synthetic-canonical-page-token')
    localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
    root = renderWithProviders(
      <AuthProvider><WorkspaceContext.Provider value={{ current: workspace, workspaces: [workspace],
        isLoading: false, switchWorkspace: async () => { throw new Error('Unexpected workspace switch') },
        refresh: async () => { throw new Error('Unexpected workspace refresh') },
        role: workspace.role, canManage: true, canWrite: true,
        enabledModules: ['accounts', 'transactions', 'goals'],
        hasModule: (id) => workspace.enabled_modules.includes(id) }}>
        <DashboardLayoutProvider><Probe /><TooltipProvider><DashboardPage /></TooltipProvider></DashboardLayoutProvider>
      </WorkspaceContext.Provider></AuthProvider>, { queryClient: client },
    )
    await act(async () => { restoration.resolve(seed); baseline.resolve(seed); await notifications() })
    await waitFor(() => {
      expect(currentOwner).not.toBeNull()
      expect(reads).toHaveLength(2)
      expect(reads.every((read) => read.completed)).toBe(true)
    })
    await drain()
    establishedOwner = currentOwner
    generation = getDashboardAuthSnapshot(client).generation
    stable()
    expect(reads.map((read) => read.user)).toEqual([seed, seed])
    expect(currentOwner?.store.getSnapshot(A).layout).not.toStrictEqual(reply.layout)
    expect(currentOwner?.store.getSnapshot(A)).toMatchObject({ writable: true, readPending: false, pending: false })
    await zero()
  }
  async function zero() {
    await drain(); stable(); genericZero()
    expect(api.dashboard.saveLayout).not.toHaveBeenCalled()
    expect(requests).toEqual([])
    expect(currentOwner?.store.getSnapshot(A).counters.success).toBe(0)
  }
  async function commit() {
    await waitFor(() => expect(requests).toHaveLength(1))
    expect(requests[0]).toMatchObject({ target: A, body: reply.layout, current: true,
      actor: ACTOR, generation, storage: A, context: A })
    expect(requests[0].client).toBe(client)
    expect(requests[0].binding.isCurrent()).toBe(true)
    await act(async () => { put.resolve(reply); post.resolve(postUser); await notifications() })
    await drain(); stable(); genericZero()
    expect(api.dashboard.saveLayout).toHaveBeenCalledTimes(1)
    expect(reads).toEqual([{ user: seed, completed: true }, { user: seed, completed: true },
      { user: postUser, completed: true }])
    expect(api.auth.me).toHaveBeenCalledTimes(3)
    expect(postUser.preferences.dashboard_layouts?.[A]).toStrictEqual(reply.layout)
    expect(currentOwner?.store.getSnapshot(A)).toMatchObject({ state: 'closed-success',
      writable: true, pending: false, readPending: false, openedBaseline: null, draft: null })
    expect(currentOwner?.store.getSnapshot(A).layout).toStrictEqual(reply.layout)
    expect(currentOwner?.store.getSnapshot(A).overlay).toStrictEqual({ [A]: reply.layout })
    expect(currentOwner?.store.getSnapshot(A).counters).toStrictEqual({
      success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0,
    })
    spendingSectionGone()
    expect(marker.evolution()).toBeInTheDocument()
    expect(marker.hero()).toBeInTheDocument()
  }
  async function positive() {
    await zero()
    if (!root) throw new Error('Page not mounted')
    await root.user.click(screen.getByRole('button', { name: t('dashboard.customize.action') }))
    await root.user.click(screen.getByRole('switch', { name: t('dashboard.customize.hide', {
      name: t('dashboard.widgets.spendingCharts'),
    }) }))
    await waitFor(spendingSectionGone)
    await zero()
    expect(currentOwner?.store.getSnapshot(A).draft).toStrictEqual(reply.layout)
    await root.user.click(screen.getByRole('button', { name: t('dashboard.customize.done') }))
    await commit()
  }
  async function cleanup() {
    const diagnostics: unknown[] = []
    async function step(callback: () => void | Promise<void>) {
      try { await callback() } catch (failure: unknown) { diagnostics.push(failure) }
    }
    restoration.resolve(seed); baseline.resolve(seed); put.resolve(reply); post.resolve(postUser)
    await step(drain)
    await step(() => { root?.unmount() })
    await step(drain)
    await step(() => {
      for (const store of stores) {
        expect(store.getSnapshot(A)).toMatchObject({ state: 'closed', writable: false,
          pending: false, readPending: false, draft: null, openedBaseline: null })
        expect(store.getSnapshot(A).overlay).toStrictEqual({})
      }
      expect(reads.every((read) => read.completed)).toBe(true)
      genericZero()
    })
    for (const unsubscribe of subscriptions) await step(unsubscribe)
    await step(() => { client.clear() })
    await step(drain)
    await step(() => { expect(errors).toEqual([]) })
    await step(() => {
      window.removeEventListener('error', onError)
      window.removeEventListener('unhandledrejection', onRejection)
      writerSpies.forEach((spy) => { spy.mockRestore() })
      dataEndpoints.forEach((endpoint, index) => {
        const implementation = implementations[index]
        if (implementation) endpoint.mockImplementation(implementation)
      })
    })
    for (const { key, value } of storageBefore) await step(() => {
      if (value === null) localStorage.removeItem(key)
      else localStorage.setItem(key, value)
    })
    return diagnostics
  }
  return { ready, zero, positive, commit, cleanup,
    get user() { if (!root) throw new Error('Missing mounted page'); return root.user } }
}

/** Section markers, queried the way a user (or test) sees them. */
const marker = {
  hero: () => screen.getAllByText(t('dashboard.availableBalance'))[0],
  banner: () => screen.getByText(t('dashboard.allCategorized')),
  spending: () => screen.getByText(t('dashboard.spendingByCategory')),
  evolution: () => screen.getByText(t('dashboard.balanceFlow')),
  transactions: () => screen.getByText(t('dashboard.periodTransactions')),
  goals: () => screen.queryByText(t('goals.dashboardTitle')),
}

function spendingSectionGone() {
  expect(
    screen.queryByText(t('dashboard.spendingByCategory')),
  ).not.toBeInTheDocument()
}

function cardOf(node: HTMLElement): HTMLElement {
  const card = node.closest('.bg-card')
  if (!card) throw new Error('section marker is not inside a .bg-card')
  return card as HTMLElement
}

function expectInOrder(nodes: HTMLElement[]) {
  for (let i = 0; i < nodes.length - 1; i++) {
    expect(
      nodes[i].compareDocumentPosition(nodes[i + 1]) &
        Node.DOCUMENT_POSITION_FOLLOWING,
      `expected "${nodes[i].textContent}" before "${nodes[i + 1].textContent}"`,
    ).toBeTruthy()
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.user = makeUser({})

  api.dashboard.summary.mockResolvedValue({
    primary_currency: 'USD',
    total_balance: { USD: 1000 },
    total_balance_primary: 1000,
    projected_balance: { USD: 1000 },
    projected_balance_primary: 1000,
    assets_value: { USD: 1200 },
    assets_value_primary: 1200,
    monthly_income: 500,
    monthly_income_primary: 500,
    monthly_expenses: 300,
    monthly_expenses_primary: 300,
    projected_income: 500,
    projected_income_primary: 500,
    projected_expenses: 300,
    projected_expenses_primary: 300,
    pending_categorization: 0,
    pending_categorization_amount: 0,
  })
  api.dashboard.spendingByCategory.mockResolvedValue([])
  api.dashboard.balanceHistoryMonths.mockResolvedValue([])
  api.dashboard.balanceHistory.mockResolvedValue({
    current: [
      { day: 1, balance: 1000 },
      { day: 2, balance: 1010 },
    ],
    opening_balance: 900,
    baseline: [],
    comparison: null,
    comparison_month: null,
    comparison_opening_balance: null,
  })
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    size: 500,
  })
  api.transactions.calendar.mockResolvedValue({ days: [] })
  api.transactions.get.mockResolvedValue({})
  api.budgets.comparison.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([
    {
      id: 'acc-1',
      name: 'Checking',
      type: 'checking',
      current_balance: 500,
      balance_primary: 500,
    },
  ])
  api.goals.summary.mockResolvedValue([]) // empty → goals self-hides (A-6)
  api.groups.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
})

describe('dashboard widget composition', () => {
  it('every widget and customize translation key exists in en and pt-BR', () => {
    // Real shipped bundles (same philosophy as test/utils.tsx:8-11): a
    // missing or empty key fails here, in both locales. The other 13
    // locales fall back to English (fallbackLng: 'en', i18n.ts).
    const CUSTOMIZE_KEYS = [
      'dashboard.customize.action',
      'dashboard.customize.title',
      'dashboard.customize.description',
      'dashboard.customize.reset',
      'dashboard.customize.undo',
      'dashboard.customize.done',
      'dashboard.customize.dragHandle',
      'dashboard.customize.handleHint',
      'dashboard.customize.moveUp',
      'dashboard.customize.moveDown',
      'dashboard.customize.show',
      'dashboard.customize.hide',
      'dashboard.customize.lastVisible',
      'dashboard.customize.announceMoved',
      'dashboard.customize.announceShown',
      'dashboard.customize.announceHidden',
      'dashboard.customize.announceReset',
      'dashboard.customize.saveError',
    ]
    const keys = [
      ...DASHBOARD_WIDGETS.map((definition) => definition.translationKey),
      ...CUSTOMIZE_KEYS,
    ]

    const lookup = (doc: unknown, path: string): unknown => {
      let acc: unknown = doc
      for (const key of path.split('.')) {
        if (!acc || typeof acc !== 'object') return undefined
        acc = (acc as Record<string, unknown>)[key]
      }
      return acc
    }

    for (const [localeName, locale] of [
      ['en', en],
      ['pt-BR', ptBR],
    ] as const) {
      for (const key of keys) {
        const value = lookup(locale, key)
        expect(typeof value, `${key} missing in ${localeName}`).toBe('string')
        expect(String(value).trim(), `${key} empty in ${localeName}`).not.toBe(
          '',
        )
      }
    }
  })

  it('renders widgets in the customized order', async () => {
    setUserLayout(
      storedLayout([
        'period-transactions',
        'hero-balance',
        'uncategorized-banner',
        'spending-charts',
        'balance-evolution',
        'goals-progress',
      ]),
    )
    await withPage(savedReply, savedPostUser, async (page) => {

    await waitFor(() => {
      expect(marker.transactions()).toBeInTheDocument()
      expect(marker.banner()).toBeInTheDocument()
    })
    expectInOrder([
      marker.transactions(),
      marker.hero(),
      marker.banner(),
      marker.spending(),
      marker.evolution(),
    ])
    await page.positive()
    })
  })

  it('renders the two charts side by side when adjacent and standalone when separated', async () => {
    // Phase 1 — default layout: charts adjacent → shared two-column grid.
    await withPage(defaultReply, defaultPostUser, async (page) => {
    await waitFor(() => expect(marker.spending()).toBeInTheDocument())
    const pairedSpending = cardOf(marker.spending())
    const pairedEvolution = cardOf(marker.evolution())
    expect(pairedSpending.parentElement).toBe(pairedEvolution.parentElement)
    expect(
      pairedSpending.parentElement?.classList.contains('lg:grid-cols-2'),
    ).toBe(true)
    await page.positive()
    })

    // Phase 2 — goals placed between the charts → each renders standalone
    // full-width in its own min-h-[380px] wrapper (ResponsiveContainer keeps
    // a definite height).
    setUserLayout(
      storedLayout([
        'hero-balance',
        'uncategorized-banner',
        'spending-charts',
        'goals-progress',
        'balance-evolution',
        'period-transactions',
      ]),
    )
    await withPage(separatedReply, separatedPostUser, async (page) => {
    await waitFor(() => expect(marker.evolution()).toBeInTheDocument())
    const soloSpending = cardOf(marker.spending())
    const soloEvolution = cardOf(marker.evolution())
    expect(soloSpending.parentElement).not.toBe(soloEvolution.parentElement)
    expect(soloSpending.parentElement?.classList.contains('lg:grid-cols-2')).toBe(
      false,
    )
    expect(
      soloEvolution.parentElement?.classList.contains('lg:grid-cols-2'),
    ).toBe(false)
    expect(soloSpending.parentElement).toHaveClass('min-h-[380px]')
    expect(soloEvolution.parentElement).toHaveClass('min-h-[380px]')
    await page.positive()
    })
  })

  it('renders exactly the current fixed composition for users without a saved layout', async () => {
    // No dashboard_layouts key at all: AC-5 — today's dashboard, byte for
    // byte: same five renderable sections in the shipped order, the two
    // charts paired in the original grid, the banner in its all-clear
    // variant, and goals self-hiding on an empty summary (A-6).
    await withPage(defaultReply, defaultPostUser, async (page) => {

    await waitFor(() => {
      expect(marker.evolution()).toBeInTheDocument()
      expect(marker.banner()).toBeInTheDocument()
    })
    expectInOrder([
      marker.hero(),
      marker.banner(),
      marker.spending(),
      marker.evolution(),
      marker.transactions(),
    ])

    // All-clear variant (pending_categorization: 0).
    expect(marker.banner()).toHaveTextContent(t('dashboard.allCategorized'))

    // Charts paired in the original grid (dashboard.tsx:904 classes).
    const spendingCard = cardOf(marker.spending())
    const evolutionCard = cardOf(marker.evolution())
    expect(spendingCard.parentElement).toBe(evolutionCard.parentElement)
    expect(spendingCard.parentElement?.classList.contains('lg:grid-cols-2')).toBe(
      true,
    )

    // Goals self-hides with an empty summary — configured visible, renders
    // nothing (the at-least-one rule counts configuration, not pixels).
    expect(marker.goals()).not.toBeInTheDocument()
    await page.positive()
    })
  })

  it('hiding a widget removes its section from the dashboard without a reload', async () => {
    await withPage(defaultReply, defaultPostUser, async (page) => {
    const { user } = page
    await waitFor(() => expect(marker.spending()).toBeInTheDocument())

    // Open the customize panel from the header entry point (§7.1).
    const customize = screen.getByRole('button', {
      name: t('dashboard.customize.action'),
    })
    expect(customize).toHaveAttribute('aria-haspopup', 'dialog')
    await user.click(customize)

    // Immediate preview: hiding the widget unmounts its section while the
    // panel is still open — no reload, no navigation.
    await user.click(
      screen.getByRole('switch', {
        name: t('dashboard.customize.hide', {
          name: t('dashboard.widgets.spendingCharts'),
        }),
      }),
    )
    await waitFor(spendingSectionGone)
    // The other sections stay put.
    expect(marker.evolution()).toBeInTheDocument()
    expect(marker.hero()).toBeInTheDocument()

    await page.zero()
    // Closing persists once (§4.5).
    await user.click(
      screen.getByRole('button', { name: t('dashboard.customize.done') }),
    )
    await page.commit()
    spendingSectionGone()
    })
  })
})
