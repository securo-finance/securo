import { useLayoutEffect, useState, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'

import { useDashboardLayout, type DashboardLayoutController } from '@/hooks/use-dashboard-layout'
import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { useDashboardLayoutOwner } from '@/contexts/dashboard-layout-context'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { createTestQueryClient, i18n } from '@/test/utils'
import { WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { getPageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import { getDashboardAuthSnapshot } from '@/lib/dashboard-layout-auth-bridge'
import { validateCommittedDashboardLayout, type CommittedDashboardLayout, type DashboardLayout,
  type DashboardRequestBinding, type DashboardWidgetId } from '@/lib/dashboard-layout'
import type { SessionStore } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

const ports = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout,
    binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
}))
// Transport boundaries only: Auth, provider, hook, store and lease are real.
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: ports.me, updateMe: ports.updateMe },
  dashboard: { saveLayout: ports.saveLayout },
}))
const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'
const TOKEN = 'synthetic-canonical-hook-token'
function makeUser(preferences: unknown): User {
  return { id: ACTOR, email: 'tester@example.com', is_active: true,
    is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences } as User
}
const IDS = DASHBOARD_WIDGETS.map((definition) => definition.id)
function defaultItems(visibleIds: string[] = IDS) {
  return IDS.map((id, order) => ({ id, visible: visibleIds.includes(id), order }))
}
function savedLayout(items: DashboardLayout['items'], workspaceId: string): DashboardLayout {
  return { version: 1, workspaceId, items }
}
const unrelated = { language: 'pt-BR', currency_display: 'BRL', display_name: 'Brian',
  timezone: 'America/Sao_Paulo', onboarding_completed: true }
const sibling = savedLayout([
  { id: 'period-transactions', visible: true, order: 0 },
  { id: 'goals-progress', visible: false, order: 1 },
], B)
const customOrder: DashboardWidgetId[] = ['period-transactions', 'goals-progress',
  'hero-balance', 'uncategorized-banner', 'spending-charts', 'balance-evolution']
const customB = savedLayout(customOrder.map((id, order) => ({ id, visible: true, order })), B)

// Independently literal accepted replies; never derived from submitted drafts.
const movedReply: CommittedDashboardLayout = { workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} }
const hiddenReply = (id: string): CommittedDashboardLayout => ({ workspaceId: id, layout: {
  version: 1, workspaceId: id, items: [
    { id: 'hero-balance', visible: false, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const previewReply: CommittedDashboardLayout = { workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: false, order: 1 },
    { id: 'goals-progress', visible: true, order: 2 },
    { id: 'spending-charts', visible: true, order: 3 },
    { id: 'balance-evolution', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} }
const customReply: CommittedDashboardLayout = { workspaceId: B, layout: {
  version: 1, workspaceId: B, items: [
    { id: 'period-transactions', visible: true, order: 0 },
    { id: 'goals-progress', visible: true, order: 1 },
    { id: 'hero-balance', visible: false, order: 2 },
    { id: 'uncategorized-banner', visible: true, order: 3 },
    { id: 'spending-charts', visible: true, order: 4 },
    { id: 'balance-evolution', visible: true, order: 5 },
  ],
} }
const lateReply: CommittedDashboardLayout = { workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} }
// Separate literal postGET documents, not reply projections or request echoes.
function hiddenPost(id: string, other: unknown): User {
  return makeUser({ ...unrelated, dashboard_layouts: { [id]: {
    version: 1, workspaceId: id, items: [
      { id: 'hero-balance', visible: false, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  }, [id === A ? B : A]: other } })
}
const movedPost = makeUser({ ...unrelated, dashboard_layouts: { [B]: sibling, [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} } })
const previewPost = makeUser({ dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: false, order: 1 },
    { id: 'goals-progress', visible: true, order: 2 },
    { id: 'spending-charts', visible: true, order: 3 },
    { id: 'balance-evolution', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} } })
function customPost(a: DashboardLayout): User {
  return makeUser({ dashboard_layouts: { [A]: a, [B]: {
    version: 1, workspaceId: B, items: [
      { id: 'period-transactions', visible: true, order: 0 },
      { id: 'goals-progress', visible: true, order: 1 },
      { id: 'hero-balance', visible: false, order: 2 },
      { id: 'uncategorized-banner', visible: true, order: 3 },
      { id: 'spending-charts', visible: true, order: 4 },
      { id: 'balance-evolution', visible: true, order: 5 },
    ],
  } } })
}
function deferred<T>(value: T) {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { value, promise, settle: () => { resolve(value) } }
}
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })
function required<T>(value: T | null, label: string): T {
  if (value === null) throw new Error(`Missing real witness: ${label}`)
  return value
}
function workspace(id: string): Workspace {
  return { id, name: 'Canonical hook fixture', kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null, icon: null, color: null,
    created_at: '2026-10-05T00:00:00Z', created_by_user_id: ACTOR,
    managed_by_user_id: null, role: 'owner', enabled_modules: ['accounts', 'transactions', 'goals'] }
}

/** File-local real lifetime harness; only controlled API ports are substituted. */
async function fixture(seed: User | null, body: (h: ReturnType<typeof harness>) => Promise<void>) {
  const h = harness(seed)
  let primary: unknown
  let failed = false
  try { await h.start(); await body(h); h.evidence() }
  catch (failure: unknown) { failed = true; primary = failure }
  const cleanupErrors = await h.cleanup()
  if (failed) {
    if (cleanupErrors.length) console.error('Canonical hook cleanup diagnostics; primary preserved', cleanupErrors)
    throw primary
  }
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'Canonical hook cleanup failed')
}
function harness(seed: User | null) {
  const client = createTestQueryClient()
  const reads: ReturnType<typeof deferred<User>>[] = []
  const puts: ReturnType<typeof deferred<CommittedDashboardLayout>>[] = []
  const gets: Array<{ user: User | null; completed: boolean }> = []
  const requests: Array<{ id: string; layout: DashboardLayout; binding: DashboardRequestBinding;
    current: boolean; generation: number | null }> = []
  const tasks: Promise<void>[] = [], errors: unknown[] = [], publications: unknown[] = []
  const stores = new Set<SessionStore>(), owners = new Set<DashboardLayoutOwner>()
  const unsubscribes: Array<() => void> = []
  const writers: Array<ReturnType<typeof vi.spyOn>> = [], contexts = new WeakSet<object>()
  const preserved: Array<{ user: User; preferences: unknown; before: unknown }> = []
  const hostile: Array<{ user: User; descriptor: PropertyDescriptor | undefined }> = []
  const state: { owner: DashboardLayoutOwner | null; hook: DashboardLayoutController | null;
    auth: ReturnType<typeof useAuth> | null; show: ((value: boolean) => void) | null;
    select: ((value: Workspace) => void) | null } = {
    owner: null, hook: null, auth: null, show: null, select: null,
  }
  const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  const warn = vi.spyOn(console, 'warn'), error = vi.spyOn(console, 'error')
  function preserve(user: User) {
    if (preserved.some((entry) => entry.user === user) || hostile.some((entry) => entry.user === user)) return
    const descriptor = Object.getOwnPropertyDescriptor(user, 'preferences')
    if (descriptor?.get) { hostile.push({ user, descriptor }); return }
    preserved.push({ user, preferences: user.preferences, before: structuredClone(user.preferences) })
  }
  function track<T>(task: Promise<T>): Promise<T> {
    tasks.push(task.then(() => {}, (failure: unknown) => { errors.push(failure) }))
    return task
  }
  function read(user: User) { preserve(user); const port = deferred(user); reads.push(port); return port }
  function put(reply: CommittedDashboardLayout) {
    expect(validateCommittedDashboardLayout(reply.workspaceId, reply, DASHBOARD_WIDGETS)).toStrictEqual(reply)
    const port = deferred(reply); puts.push(port); return port
  }
  ports.me.mockReset().mockImplementation(() => {
    const call = { user: null as User | null, completed: false }; gets.push(call)
    const port = reads[gets.length - 1]
    const task = port?.promise ?? Promise.reject(new Error('Unexpected extra GET'))
    track(task.then((user) => { call.user = user; call.completed = true }))
    return task
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    requests.push({ id, layout: structuredClone(layout), binding, current: binding.isCurrent(),
      generation: state.owner?.authGeneration ?? null })
    return track(puts[requests.length - 1]?.promise ?? Promise.reject(new Error('Unexpected extra PUT')))
  })
  ports.updateMe.mockReset().mockImplementation(() => track(Promise.reject(new Error('Forbidden legacy PATCH'))))
  function ObserveAuth({ children }: { children: ReactNode }) {
    const context = useAuth()
    if (!contexts.has(context)) { contexts.add(context); writers.push(vi.spyOn(context, 'updateUser')) }
    useLayoutEffect(() => { state.auth = context })
    return children
  }
  function HookProbe() {
    const hook = useDashboardLayout()
    useLayoutEffect(() => { state.hook = hook })
    useLayoutEffect(() => () => { state.hook = null }, [])
    return null
  }
  function OwnerProbe() {
    const owner = useDashboardLayoutOwner()
    const [show, setShow] = useState(true)
    useLayoutEffect(() => { state.show = setShow; state.owner = owner
      if (!owner) return
      owners.add(owner)
      if (stores.has(owner.store)) return
      stores.add(owner.store)
      unsubscribes.push(owner.store.subscribe(() => {
        try { publications.push([owner.store.getSnapshot(A), owner.store.getSnapshot(B)]) }
        catch (failure: unknown) { errors.push(failure) }
      }))
    }, [owner])
    // Null owner still renders the real hook's signed-out defaults.
    return show ? <HookProbe /> : null
  }
  function WorkspaceHarness() {
    const [current, setCurrent] = useState(workspace(A))
    useLayoutEffect(() => { state.select = setCurrent }, [])
    return <WorkspaceContext.Provider value={{ current, workspaces: [workspace(A), workspace(B)],
      isLoading: false, switchWorkspace: async () => { throw new Error('Unexpected switch port') },
      refresh: async () => { throw new Error('Unexpected list refresh') },
      role: 'owner', canManage: true, canWrite: true, enabledModules: ['accounts', 'transactions', 'goals'],
      hasModule: (id) => current.enabled_modules.includes(id) }}>
      <DashboardLayoutProvider><OwnerProbe /></DashboardLayoutProvider>
    </WorkspaceContext.Provider>
  }
  function Shell() {
    return <I18nextProvider i18n={i18n}><QueryClientProvider client={client}><AuthProvider>
      <ObserveAuth><WorkspaceHarness /></ObserveAuth>
    </AuthProvider></QueryClientProvider></I18nextProvider>
  }
  async function flush() { await act(async () => { await tick(); await tick() }) }
  async function drain() {
    for (let turn = 0; turn < 8; turn += 1) {
      const before = [tasks.length, publications.length, gets.length, requests.length, stores.size]
      await tick(); await tick(); await Promise.all(tasks)
      for (const store of stores) {
        await store.whenIdle(); await getPageSessionLease(store).whenIdle()
      }
      await tick(); await tick(); await Promise.all(tasks)
      expect(client.isMutating()).toBe(0)
      const after = [tasks.length, publications.length, gets.length, requests.length, stores.size]
      if (after.every((value, index) => value === before[index])) return
    }
    throw new Error('Canonical hook work did not quiesce')
  }
  async function settle(...controlled: Array<{ settle(): void }>) {
    await act(async () => { controlled.forEach((port) => { port.settle() }); await drain() })
    await act(async () => { await drain() })
  }
  const hook = () => required(state.hook, 'hook')
  const owner = () => required(state.owner, 'authenticated provider owner')
  function evidence() {
    expect(errors).toEqual([]); expect(warn.mock.calls).toEqual([]); expect(error.mock.calls).toEqual([])
    expect(ports.updateMe).not.toHaveBeenCalled()
    expect(gets).toHaveLength(reads.length)
    gets.forEach((call, index) => { expect(call).toEqual({ user: reads[index]!.value, completed: true }) })
    expect(ports.me.mock.calls).toEqual(reads.map(() => []))
    expect(requests).toHaveLength(puts.length)
    expect(writers.length).toBeGreaterThan(0)
    writers.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    preserved.forEach(({ user, preferences, before }) => {
      expect(user.preferences).toBe(preferences); expect(user.preferences).toStrictEqual(before)
      expect(user.id).toBe(ACTOR)
    })
    hostile.forEach(({ user, descriptor }) => {
      expect(Object.getOwnPropertyDescriptor(user, 'preferences')).toEqual(descriptor)
      expect(() => user.preferences).toThrow('unreadable preferences')
    })
  }
  function stable(expected: DashboardLayoutOwner) {
    expect(state.owner).toBe(expected); expect([...owners]).toEqual([expected])
    expect([...stores]).toEqual([expected.store]); expect(expected.actorId).toBe(ACTOR)
    expect(expected.authGeneration).toBe(getDashboardAuthSnapshot(client).generation)
    expect(state.auth?.user).toBe(seed ?? preserved[0]?.user)
    expect(state.auth?.token).toBe(TOKEN); expect(localStorage.getItem('token')).toBe(TOKEN)
  }
  function request(index: number, reply: CommittedDashboardLayout, expectedOwner: DashboardLayoutOwner) {
    expect(requests[index]).toMatchObject({ id: reply.workspaceId, layout: reply.layout,
      current: true, generation: expectedOwner.authGeneration })
    expect(Reflect.ownKeys(requests[index]!.layout).sort()).toEqual(['items', 'version', 'workspaceId'])
    requests[index]!.layout.items.forEach((item) => {
      expect(Reflect.ownKeys(item).sort()).toEqual(['id', 'order', 'visible'])
    })
  }
  let mounted: { unmount(): void } | null = null
  const onError = (event: ErrorEvent) => { errors.push(event.error ?? event.message) }
  const onRejection = (event: PromiseRejectionEvent) => { errors.push(event.reason) }
  async function start() {
    window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
    localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
    if (seed) localStorage.setItem('token', TOKEN); else localStorage.removeItem('token')
    const initial = seed ? [read(seed), read(seed)] : [] // Real Auth restore, then hook-entry READ.
    mounted = render(<Shell />, { reactStrictMode: false })
    await settle(...initial)
    expect(gets).toHaveLength(initial.length)
    expect(gets.every((call) => call.completed && call.user === seed)).toBe(true)
    if (seed) stable(owner()); else expect(state.owner).toBeNull()
  }
  async function show(value: boolean, user?: User) {
    const next = value && user ? read(user) : null
    act(() => { required(state.show, 'hook placement')(value) })
    await settle(...(next ? [next] : []))
  }
  async function select(id: string, user: User) {
    const next = read(user)
    act(() => { localStorage.setItem(WORKSPACE_STORAGE_KEY, id); required(state.select, 'workspace selection')(workspace(id)) })
    await settle(next)
  }

  async function cleanup() {
    const cleanupErrors: unknown[] = []
    async function step(action: () => void | Promise<void>) {
      try { await action() } catch (failure: unknown) { cleanupErrors.push(failure) }
    }
    // Settle every controlled port before releasing leases; also covers later postGET issuance.
    reads.forEach((port) => { port.settle() }); puts.forEach((port) => { port.settle() })
    await step(async () => { await act(async () => { await drain() }) })
    await step(async () => { await show(false) })
    await step(async () => { await act(async () => { mounted?.unmount(); await drain() }) })
    await step(() => {
      stores.forEach((store) => {
        expect(store.getSnapshot(A)).toMatchObject({ writable: false, pending: false,
          readPending: false, openedBaseline: null, draft: null, overlay: {} })
        expect(store.getSnapshot(B)).toMatchObject({ writable: false, pending: false,
          readPending: false, openedBaseline: null, draft: null, overlay: {} })
        expect(store.getSnapshot(A).overlay).toStrictEqual({})
        expect(store.getSnapshot(B).overlay).toStrictEqual({})
      })
    })
    await step(evidence)
    unsubscribes.forEach((unsubscribe) => { unsubscribe() })
    await step(() => { client.clear() })
    window.removeEventListener('error', onError); window.removeEventListener('unhandledrejection', onRejection)
    writers.forEach((spy) => { spy.mockRestore() }); warn.mockRestore(); error.mockRestore()
    ports.me.mockReset(); ports.saveLayout.mockReset(); ports.updateMe.mockReset()
    storageBefore.forEach(({ key, value }) => {
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value)
    })
    return cleanupErrors
  }
  return { client, start, cleanup, hook, owner, state, read, put, settle, flush, drain, show, select,
    evidence, stable, request, gets, requests }
}

async function noWrite(h: ReturnType<typeof harness>) {
  await h.settle()
  expect(h.requests).toEqual([]); expect(ports.saveLayout).not.toHaveBeenCalled(); h.evidence()
}
async function positive(h: ReturnType<typeof harness>, id: string, post: User, reply = hiddenReply(id)) {
  const owner = h.owner(), count = h.requests.length, write = h.put(reply), after = h.read(post)
  act(() => { h.hook().openCustomize() })
  act(() => { expect(h.hook().toggleVisible('hero-balance')).toBe(true) })
  expect(h.hook().draft).toStrictEqual(reply.layout)
  act(() => { h.hook().closeCustomize(true) })
  await h.flush()
  expect(h.requests).toHaveLength(count + 1); h.request(count, reply, owner)
  expect(owner.store.getSnapshot(id)).toMatchObject({ pending: true, state: 'pending' })
  await h.settle(write, after)
  expect(owner.store.getSnapshot(id)).toMatchObject({ state: 'closed-success', pending: false,
    readPending: false, writable: true, layout: reply.layout })
  expect(owner.store.getSnapshot(id).overlay[id]).toStrictEqual(reply.layout)
  expect(h.gets.at(-1)).toEqual({ user: post, completed: true })
  expect(h.requests).toHaveLength(count + 1); h.stable(owner); h.evidence()
}

describe('useDashboardLayout', () => {
  it('persists the layout under the current workspace key and leaves other workspace layouts intact', async () => {
    const user = makeUser({ ...unrelated, dashboard_layouts: { [B]: sibling } })
    await fixture(user, async (h) => {
      const owner = h.owner(), write = h.put(movedReply), post = h.read(movedPost)
      expect(h.hook().status).toBe('default')
      act(() => { h.hook().openCustomize() })
      act(() => { h.hook().move('period-transactions', 'up') })
      act(() => { h.hook().closeCustomize(true) })
      await h.flush(); expect(h.requests).toHaveLength(1); h.request(0, movedReply, owner)
      expect(h.requests[0]!.layout.items.map((item) => item.id)).toEqual([
        'hero-balance', 'uncategorized-banner', 'spending-charts', 'balance-evolution',
        'period-transactions', 'goals-progress',
      ])
      expect(h.requests[0]!.layout.items.map((item) => item.order)).toEqual([0, 1, 2, 3, 4, 5])
      expect(h.requests[0]!.layout.items.every((item) => item.visible)).toBe(true)
      await h.settle(write, post)
      expect(owner.store.getSnapshot(A)).toMatchObject({ layout: movedReply.layout,
        state: 'closed-success', pending: false, readPending: false, overlay: { [A]: movedReply.layout } })
      expect(movedPost.preferences).toMatchObject(unrelated)
      expect(owner.store.getSnapshot(B).layout).toStrictEqual(
        savedLayout([
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'period-transactions', visible: true, order: 4 },
          { id: 'goals-progress', visible: false, order: 5 },
        ], B),
      )
      expect(movedPost.preferences.dashboard_layouts?.[B]).toBe(sibling)
      expect(user.preferences.dashboard_layouts?.[B]).toBe(sibling)
      expect(h.requests).toHaveLength(1); h.stable(owner); h.evidence()
    })
  })

  it('resolves a different layout after switching workspaces without a reload', async () => {
    const a = savedLayout(defaultItems(IDS.filter((id) => id !== 'hero-balance')), A)
    const user = makeUser({ dashboard_layouts: { [A]: a, [B]: customB } })
    await fixture(user, async (h) => {
      const owner = h.owner()
      expect(h.hook().draft.workspaceId).toBe(A)
      expect(h.hook().orderedVisible.map((d) => d.id)).toEqual(IDS.filter((id) => id !== 'hero-balance'))
      act(() => { h.hook().openCustomize() })
      act(() => { h.hook().move('period-transactions', 'up'); h.hook().reset() })
      expect(h.hook().canUndo).toBe(true)
      await h.select(B, user)
      expect(h.hook().draft.workspaceId).toBe(B)
      expect(h.hook().orderedVisible.map((d) => d.id)).toEqual(customOrder)
      expect(h.hook().status).toBe('saved'); expect(h.hook().canUndo).toBe(false)
      expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed', draft: null,
        openedBaseline: null, canUndo: false })
      await noWrite(h); h.stable(owner)
      // Separately issue A, then switch while its accepted reply is deferred.
      await h.select(A, user)
      const late = h.put(lateReply)
      act(() => { h.hook().openCustomize() })
      act(() => { expect(h.hook().toggleVisible('hero-balance')).toBe(true) })
      act(() => { h.hook().closeCustomize(true) })
      await h.flush(); expect(h.requests).toHaveLength(1); h.request(0, lateReply, owner)
      const bRead = h.read(user)
      act(() => { localStorage.setItem(WORKSPACE_STORAGE_KEY, B); required(h.state.select, 'select B')(workspace(B)) })
      await act(async () => { bRead.settle(); await tick(); await tick() })
      await h.flush()
      act(() => { h.hook().openCustomize() })
      act(() => { expect(h.hook().toggleVisible('hero-balance')).toBe(true) })
      const draft = structuredClone(h.hook().draft)
      const readsBeforeInactiveCompletion = h.gets.length
      const countersBeforeInactiveCompletion = { ...owner.store.getSnapshot(A).counters }
      await h.settle(late)
      expect(h.hook().draft).toStrictEqual(draft)
      expect(owner.store.getSnapshot(B)).toMatchObject({ state: 'editing', draft, openedBaseline: customB })
      expect(owner.store.getSnapshot(B).overlay).toStrictEqual({ [A]: lateReply.layout })
      expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed-success', layout: lateReply.layout,
        pending: false, readPending: false, recovery: null })
      expect(owner.store.getSnapshot(A).counters).toStrictEqual({ ...countersBeforeInactiveCompletion,
        success: countersBeforeInactiveCompletion.success + 1 })
      expect(h.gets).toHaveLength(readsBeforeInactiveCompletion)
      expect(h.requests).toHaveLength(1)
      const write = h.put(customReply), post = h.read(customPost(a))
      act(() => { h.hook().closeCustomize(true) })
      await h.flush(); expect(h.requests).toHaveLength(2); h.request(1, customReply, owner)
      await h.settle(write, post)
      expect(owner.store.getSnapshot(B)).toMatchObject({ state: 'closed-success', layout: customReply.layout,
        pending: false, readPending: false, overlay: { [A]: lateReply.layout, [B]: customReply.layout } })
      expect(owner.store.getSnapshot(B).overlay).toStrictEqual({ [A]: lateReply.layout, [B]: customReply.layout })
      expect(h.requests).toHaveLength(2)
      h.stable(owner); h.evidence()
    })
  })

  it('save carries unrelated preference keys verbatim', async () => {
    const user = makeUser({ ...unrelated, dashboard_layouts: { [A]: savedLayout(defaultItems(), A), [B]: sibling } })
    await fixture(user, async (h) => {
      const owner = h.owner()
      act(() => { h.hook().openCustomize() })
      act(() => { h.hook().closeCustomize(true) })
      await noWrite(h)
      expect(user.preferences).toMatchObject(unrelated)
      expect(user.preferences.dashboard_layouts?.[B]).toBe(sibling)
      const post = hiddenPost(A, sibling)
      await positive(h, A, post)
      expect(post.preferences).toMatchObject(unrelated)
      expect(post.preferences.dashboard_layouts?.[B]).toBe(sibling)
      expect(owner.store.getSnapshot(A).overlay).toStrictEqual({ [A]: hiddenReply(A).layout })
      h.stable(owner)
    })
  })

  it('falls back to the default layout when preferences cannot be read', async () => {
    const hostile = makeUser({})
    Object.defineProperty(hostile, 'preferences', {
      get() { throw new Error('unreadable preferences') }, configurable: true,
    })
    // All original branches have isolated real providers/clients; none is dropped.
    for (const seed of [null, makeUser(null), makeUser({ language: 'en' }), hostile]) {
      await fixture(seed, async (h) => {
        const initialOwner = h.state.owner
        expect(h.hook().status).toBe('default')
        expect(h.hook().draft.workspaceId).toBe(A)
        expect(h.hook().orderedVisible.map((d) => d.id)).toEqual(IDS)
        expect(h.hook().orderedVisible.every((d) => d.defaultVisible)).toBe(true)
        expect(h.hook().draft.items).toEqual(defaultItems())
        if (seed === hostile) expect(h.owner().store.getSnapshot(A).writable).toBe(false)
        act(() => { h.hook().openCustomize() })
        act(() => { h.hook().closeCustomize(true) })
        await noWrite(h)
        act(() => { h.hook().openCustomize() })
        await h.show(false); await noWrite(h)
        const valid = makeUser({ dashboard_layouts: { [B]: sibling } })
        if (seed === null) {
          expect(h.state.owner).toBeNull()
          const generation = getDashboardAuthSnapshot(h.client).generation
          // Native Auth storage event revokes generation and authenticates this SAME provider/client.
          const restored = h.read(valid)
          // Hook is absent: only Auth's restore GET is expected here.
          act(() => {
            localStorage.setItem('token', TOKEN)
            window.dispatchEvent(new StorageEvent('storage', { key: 'token', oldValue: null, newValue: TOKEN }))
          })
          await h.settle(restored)
          expect(h.owner().authGeneration).toBeGreaterThan(generation)
        } else expect(h.owner()).toBe(initialOwner)
        await h.show(true, valid)
        expect(h.owner().store.getSnapshot(A)).toMatchObject({ writable: true, readPending: false })
        if (initialOwner) h.stable(initialOwner)
        await positive(h, A, hiddenPost(A, sibling))
        if (initialOwner) expect(h.owner()).toBe(initialOwner)
      })
    }
  })

  it('persists exactly once when the customize panel closes', async () => {
    await fixture(makeUser({ dashboard_layouts: { [A]: savedLayout(defaultItems(), A) } }), async (h) => {
      const owner = h.owner()
      act(() => { h.hook().openCustomize() })
      act(() => { h.hook().move('goals-progress', 'up') })
      act(() => { h.hook().move('goals-progress', 'up') })
      act(() => { expect(h.hook().toggleVisible('uncategorized-banner')).toBe(true) })
      expect(h.hook().draft).toStrictEqual(previewReply.layout)
      await noWrite(h)
      const write = h.put(previewReply), post = h.read(previewPost)
      act(() => { h.hook().closeCustomize(true) })
      await h.flush(); expect(h.requests).toHaveLength(1); h.request(0, previewReply, owner)
      act(() => { h.hook().closeCustomize(true) })
      act(() => { required(h.state.show, 'hook-only unmount')(false) })
      await h.flush()
      expect(h.state.hook).toBeNull(); expect(h.requests).toHaveLength(1)
      expect(owner.store.getSnapshot(A)).toMatchObject({ pending: true })
      await h.settle(write, post)
      expect(owner.store.getSnapshot(A)).toMatchObject({ state: 'closed-success', pending: false,
        readPending: false, draft: null, openedBaseline: null, counters: { success: 1 } })
      expect(h.requests).toHaveLength(1); h.stable(owner); h.evidence()
    })
  })

  it('suppresses auto-persist for foreign (newer-version) layouts until the user edits', async () => {
    const foreign = {
      version: 99,
      workspaceId: A,
      items: [{ id: 'hero-balance', visible: true, order: 0 }],
      futureExtras: { someNewThing: true },
    }
    await protectedTarget(foreign)
  })

  it('refuses malformed target edits without overwriting the leaf and saves valid sibling B', async () => {
    await protectedTarget({ version: 1, workspaceId: A, items: 'malformed' })
  })
})

async function protectedTarget(target: unknown) {
  const user = makeUser({ ...unrelated, dashboard_layouts: { [A]: target, [B]: savedLayout(defaultItems(), B) } })
  const before = structuredClone(target)
  await fixture(user, async (h) => {
    const owner = h.owner()
    expect(h.hook().status).toBe('foreign')
    expect(h.hook().orderedVisible.map((d) => d.id)).toEqual(IDS)
    expect(owner.store.getSnapshot(A)).toMatchObject({ writable: false, readPending: false })
    act(() => { h.hook().openCustomize() })
    act(() => { h.hook().closeCustomize(true) })
    await noWrite(h)
    const displayed = structuredClone(h.hook().draft)
    act(() => { h.hook().openCustomize() })
    act(() => { h.hook().move('period-transactions', 'up') })
    act(() => { expect(h.hook().toggleVisible('hero-balance')).toBe(false) })
    act(() => { h.hook().closeCustomize(true) })
    await noWrite(h)
    expect(h.hook().draft).toStrictEqual(displayed)
    expect(h.hook().customizeOpen).toBe(false)
    expect(user.preferences.dashboard_layouts?.[A]).toStrictEqual(before)
    // Ordinary READ repeats protection; explicit recovery recheck/reapply ports remain OPEN.
    const repeat = h.read(user)
    await act(async () => { const task = owner.store.refresh(A); repeat.settle(); await task; await h.drain() })
    await noWrite(h)
    expect(owner.store.getSnapshot(A).writable).toBe(false)
    await h.select(B, user); h.stable(owner)
    await positive(h, B, hiddenPost(B, target))
    expect(owner.store.getSnapshot(B).overlay).toStrictEqual({ [B]: hiddenReply(B).layout })
    expect(user.preferences.dashboard_layouts?.[A]).toStrictEqual(before)
    expect(owner.store.getSnapshot(A)).toMatchObject({ writable: false, provenance: 'foreign' })
  })
}
