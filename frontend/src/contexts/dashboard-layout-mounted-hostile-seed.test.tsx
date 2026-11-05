import { useLayoutEffect } from 'react'
import { act, render } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, useWorkspace } from '@/contexts/workspace-context'
import {
  DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner,
} from '@/contexts/dashboard-layout-provider'
import { getDashboardAuthSnapshot } from '@/lib/dashboard-layout-auth-bridge'
import { auth, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resetLayout, toPersistedLayout, toggleItem, validateCommittedDashboardLayout,
  type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding,
} from '@/lib/dashboard-layout'
import type { SessionSnapshot, SessionStore } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

// Transport only: Auth, owner construction, registration and store remain real.
const ports = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout,
    binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  login: vi.fn<() => Promise<never>>(),
  verify2fa: vi.fn<() => Promise<never>>(),
  register: vi.fn<() => Promise<never>>(),
}))
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: ports.me, updateMe: ports.updateMe, login: ports.login,
    verify2fa: ports.verify2fa, register: ports.register },
  dashboard: { saveLayout: ports.saveLayout },
}))
const W = '11111111-1111-4111-8111-111111111111'
const SIBLING = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'
const TOKEN = 'synthetic-mounted-hostile-seed-token'
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Uninitialized deferred') }
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}
function required<T>(value: T | null, label: string): T {
  if (value === null) throw new Error(`Missing real witness: ${label}`)
  return value
}
function tick(): Promise<void> {
  return new Promise((resolve) => { queueMicrotask(resolve) })
}

it('mounts the original configurable hostile Auth seed safely and saves one changed leaf on that same owner', async () => {
  const defaults = resetLayout(DASHBOARD_WIDGETS, W)
  const hostile: User = { id: ACTOR, email: 'mounted-hostile@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {},
  }
  const unreadable = new Error('Original H4 mounted Auth preferences getter')
  const getterHits: Error[] = []
  const getter = () => { getterHits.push(unreadable); throw unreadable }
  Object.defineProperty(hostile, 'preferences', {
    get: getter, enumerable: true, configurable: true,
  })
  // Descriptor inspection never reads, freezes or benign-substitutes the hostile seed.
  const hostileDescriptors = Object.getOwnPropertyDescriptors(hostile)
  const descriptor = Object.getOwnPropertyDescriptor(hostile, 'preferences')
  const entry: User & { preferences: { timezone: string } } = { id: ACTOR, email: 'mounted-hostile@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: { language: 'pt-BR', timezone: 'UTC', display_name: 'Mounted H4',
      dashboard_layouts: {
        [W]: { version: 1, workspaceId: W, items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
        [SIBLING]: { version: 1, workspaceId: SIBLING, items: [
          { id: 'hero-balance', visible: true, order: 0 },
        ] },
      },
    },
  }
  // Independent accepted envelope; not an echo of the submitted draft.
  const reply: CommittedDashboardLayout = { workspaceId: W, layout: {
    version: 1, workspaceId: W, items: [
      { id: 'hero-balance', visible: false, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } }
  // Independently literal postGET, including unchanged nonlayout/workspace siblings.
  const post: User & { preferences: { timezone: string } } = { id: ACTOR, email: 'mounted-hostile@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: { language: 'pt-BR', timezone: 'UTC', display_name: 'Mounted H4',
      dashboard_layouts: {
        [W]: { version: 1, workspaceId: W, items: [
          { id: 'hero-balance', visible: false, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
        [SIBLING]: { version: 1, workspaceId: SIBLING, items: [
          { id: 'hero-balance', visible: true, order: 0 },
        ] },
      },
    },
  }
  const positiveFixtures = [entry, reply, post]
  const positiveBefore = structuredClone(positiveFixtures)
  const positiveBytes = JSON.stringify(positiveFixtures)
  const preferences = [entry.preferences, post.preferences]
  const siblings = [entry.preferences.dashboard_layouts?.[SIBLING], post.preferences.dashboard_layouts?.[SIBLING]]
  const workspace: Workspace = { id: W, name: 'Mounted hostile seed', kind: 'personal',
    timezone: null,
    is_archived: false, default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
    icon: null, color: null, created_at: '2026-10-05T00:00:00Z',
    created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
    enabled_modules: ['accounts', 'transactions', 'goals'],
  }
  const restoration = deferred<User>(), read = deferred<User>(), postGet = deferred<User>()
  const put = deferred<CommittedDashboardLayout>()
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
  } })
  const errors: unknown[] = [], cleanupErrors: unknown[] = []
  const tasks: Promise<void>[] = [], publications: SessionSnapshot[] = []
  function track<T>(task: Promise<T>): Promise<T> {
    tasks.push(task.then(() => {}, (error: unknown) => { errors.push(error) }))
    return task
  }
  const gets: Array<{ user: User | null }> = []
  const requests: Array<{ id: string; layout: DashboardLayout;
    binding: DashboardRequestBinding; current: boolean; generation: number }> = []
  ports.me.mockReset().mockImplementation(() => {
    const call: { user: User | null } = { user: null }; gets.push(call)
    // Auth restoration + explicit store.refresh + finalize's postGET, and nothing else.
    const task = gets.length === 1 ? restoration.promise : gets.length === 2 ? read.promise
      : gets.length === 3 ? postGet.promise : Promise.reject(new Error('Unexpected extra GET'))
    track(task.then((user) => { call.user = user }))
    return task
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    const call = { id, layout: structuredClone(layout), binding, current: false,
      generation: getDashboardAuthSnapshot(client).generation }
    requests.push(call)
    try { call.current = binding.isCurrent() } catch (error: unknown) { errors.push(error) }
    return track(requests.length === 1 ? put.promise : Promise.reject(new Error('Unexpected extra PUT')))
  })
  ports.updateMe.mockReset().mockImplementation(() => track(Promise.reject(new Error('Forbidden legacy PATCH'))))
  ports.login.mockReset().mockImplementation(() => track(Promise.reject(new Error('Unexpected login'))))
  ports.verify2fa.mockReset().mockImplementation(() => track(Promise.reject(new Error('Unexpected verify2fa'))))
  ports.register.mockReset().mockImplementation(() => track(Promise.reject(new Error('Unexpected register'))))
  const patchSpy = vi.spyOn(auth, 'updateMe')
  const warn = vi.spyOn(console, 'warn'), error = vi.spyOn(console, 'error') // Passthrough.
  const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
  const contexts = new WeakSet<object>(), owners = new Set<DashboardLayoutOwner>()
  const stores = new Set<SessionStore>(), unsubscribes: Array<() => void> = []
  const captures: Array<{ owner: DashboardLayoutOwner; context: ReturnType<typeof useAuth>;
    client: QueryClient; workspace: Workspace | null; generation: number }> = []
  const state: { owner: DashboardLayoutOwner | null; context: ReturnType<typeof useAuth> | null } = {
    owner: null, context: null,
  }
  function Probe() {
    const owner = useDashboardLayoutOwner(), context = useAuth()
    const queryClient = useQueryClient(), current = useWorkspace().current
    if (!contexts.has(context)) {
      contexts.add(context); writerSpies.push(vi.spyOn(context, 'updateUser'))
    }
    useLayoutEffect(() => {
      state.owner = owner; state.context = context
      if (!owner) return
      owners.add(owner)
      captures.push({ owner, context, client: queryClient, workspace: current,
        generation: getDashboardAuthSnapshot(queryClient).generation })
      if (!stores.has(owner.store)) {
        stores.add(owner.store)
        publications.push(owner.store.getSnapshot(W))
        unsubscribes.push(owner.store.subscribe(() => {
          try { publications.push(owner.store.getSnapshot(W)) }
          catch (error: unknown) { errors.push(error) }
        }))
      }
    }, [owner, context, queryClient, current])
    return null
  }
  function Shell() {
    return <QueryClientProvider client={client}><AuthProvider>
      <WorkspaceContext.Provider value={{ current: workspace, workspaces: [workspace],
        isLoading: false, switchWorkspace: async () => {}, refresh: async () => {},
        role: workspace.role, canManage: true, canWrite: true,
        enabledModules: ['accounts', 'transactions', 'goals'],
        hasModule: (id) => workspace.enabled_modules.includes(id) }}>
        <DashboardLayoutProvider><Probe /></DashboardLayoutProvider>
      </WorkspaceContext.Provider>
    </AuthProvider></QueryClientProvider>
  }
  async function drain() {
    for (let turn = 0; turn < 8; turn += 1) {
      const before = [tasks.length, publications.length, gets.length, requests.length, stores.size]
      await Promise.all(tasks); await tick(); await tick()
      await Promise.all([...stores].map((store) => store.whenIdle()))
      await tick(); await tick(); await Promise.all(tasks)
      const after = [tasks.length, publications.length, gets.length, requests.length, stores.size]
      if (after.every((value, index) => value === before[index])) return
    }
    throw new Error('Mounted work failed to quiesce')
  }
  function evidence() {
    expect(errors).toEqual([]); expect(warn.mock.calls).toEqual([]); expect(error.mock.calls).toEqual([])
    expect(patchSpy).not.toHaveBeenCalled(); expect(ports.updateMe).not.toHaveBeenCalled()
    expect(writerSpies.length).toBeGreaterThan(0)
    writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    expect(ports.login).not.toHaveBeenCalled(); expect(ports.verify2fa).not.toHaveBeenCalled()
    expect(ports.register).not.toHaveBeenCalled()
    expect(Object.getOwnPropertyDescriptors(hostile)).toEqual(hostileDescriptors)
    expect(Object.getOwnPropertyDescriptor(hostile, 'preferences')).toEqual(descriptor)
    expect(Object.getOwnPropertyDescriptor(hostile, 'preferences')?.get).toBe(getter)
    expect(descriptor).toMatchObject({ configurable: true, enumerable: true, get: getter })
    expect(Object.isFrozen(hostile)).toBe(false); expect(getterHits).toEqual([unreadable])
    expect(positiveFixtures).toStrictEqual(positiveBefore); expect(JSON.stringify(positiveFixtures)).toBe(positiveBytes)
    ;[entry, post].forEach((user, index) => {
      expect(user.preferences).toBe(preferences[index])
      expect(user.preferences.dashboard_layouts?.[SIBLING]).toBe(siblings[index])
    })
  }
  function readEvidence(expected: User[]) {
    expect(gets).toHaveLength(expected.length)
    gets.forEach((call, index) => { expect(call.user).toBe(expected[index]) })
  }
  const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let unmount: (() => void) | null = null, bodyFailed = false
  const onError = (event: ErrorEvent) => { errors.push(event.error ?? event.message) }
  const onRejection = (event: PromiseRejectionEvent) => { errors.push(event.reason) }
  window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
  try {
    localStorage.setItem('token', TOKEN); localStorage.setItem(WORKSPACE_STORAGE_KEY, W)
    unmount = render(<Shell />, { reactStrictMode: false }).unmount
    await act(async () => { restoration.resolve(hostile); await drain() })
    await act(async () => { await drain() })
    const owner = required(state.owner, 'registered mounted owner'), store = owner.store
    const context = required(state.context, 'restored Auth context')
    const bridge = getDashboardAuthSnapshot(client)
    function stable() {
      expect(state.owner).toBe(owner); expect(state.context).toBe(context)
      expect([...owners]).toEqual([owner]); expect([...stores]).toEqual([store])
      expect(owner.actorId).toBe(ACTOR); expect(owner.authGeneration).toBe(bridge.generation)
      expect(getDashboardAuthSnapshot(client)).toBe(bridge)
      expect(context.user).toBe(hostile); expect(context.token).toBe(TOKEN); expect(context.isLoading).toBe(false)
      expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(W)
      captures.forEach((capture) => {
        expect(capture).toMatchObject({ generation: bridge.generation })
        expect(capture.owner).toBe(owner); expect(capture.context).toBe(context)
        expect(capture.client).toBe(client); expect(capture.workspace).toBe(workspace)
      })
    }
    const initial = store.getSnapshot(W)
    expect(publications[0]).toBe(initial); expect(publications).toHaveLength(1)
    expect(initial).toMatchObject({ actorId: ACTOR, authGeneration: bridge.generation, workspaceId: W,
      provenance: 'default', state: 'closed', writable: false, pending: false, readPending: false,
      canUndo: false, openedBaseline: null, draft: null, overlay: {},
      counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
    expect(initial.layout).toStrictEqual(defaults); expect(store.retain(W)).toBeNull()
    readEvidence([hostile]); expect(requests).toEqual([])
    stable(); evidence()
    await act(async () => { const task = track(store.refresh(W)); read.resolve(entry); await task; await drain() })
    expect(store.getSnapshot(W)).toMatchObject({ writable: true, state: 'closed', layout: defaults })
    readEvidence([hostile, entry]); stable(); evidence()
    expect(validateCommittedDashboardLayout(W, reply, DASHBOARD_WIDGETS)).toStrictEqual(reply)
    const id = store.open(W)
    expect(store.getSnapshot(W)).toMatchObject({ state: 'editing', openedBaseline: defaults, draft: defaults })
    const changed = toPersistedLayout(W, toggleItem(defaults.items, 'hero-balance', false), DASHBOARD_WIDGETS)
    expect(changed).toStrictEqual(reply.layout); expect(changed).not.toEqual(defaults)
    expect(changed.items.filter((item, index) => item.visible !== defaults.items[index].visible))
      .toEqual([{ id: 'hero-balance', visible: false, order: 0 }])
    store.setDraft(id, changed.items)
    expect(store.getSnapshot(W).draft).toStrictEqual(changed)
    await act(async () => {
      const task = track(store.finalize(id)); put.resolve(reply); postGet.resolve(post); await task; await drain()
    })
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ id: W, layout: changed, current: true, generation: bridge.generation })
    expect(requests[0]?.binding.isCurrent()).toBe(false)
    expect(Reflect.ownKeys(requests[0]!.layout).sort()).toEqual(['items', 'version', 'workspaceId'])
    requests[0]!.layout.items.forEach((item) => { expect(Reflect.ownKeys(item).sort()).toEqual(['id', 'order', 'visible']) })
    const saved = store.getSnapshot(W)
    expect(saved).toMatchObject({ state: 'closed-success', provenance: 'saved', writable: true,
      pending: false, readPending: false, canUndo: false, openedBaseline: null, draft: null })
    expect(saved.layout).toStrictEqual(changed); expect(saved.overlay).toStrictEqual({ [W]: changed })
    expect(saved.counters).toEqual({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
    expect(publications.at(-1)).toBe(saved); expect(store.retain(W)).toBeNull()
    await act(async () => { await drain() })
    expect(store.getSnapshot(W)).toBe(saved)
    readEvidence([hostile, entry, post])
    expect(ports.me.mock.calls).toEqual([[], [], []]); expect(ports.saveLayout).toHaveBeenCalledTimes(1)
    stable(); evidence()
  } catch (failure: unknown) { bodyFailed = true; throw failure }
  finally {
    restoration.resolve(hostile); read.resolve(entry); put.resolve(reply); postGet.resolve(post)
    async function cleanup(action: () => void | Promise<void>) {
      try { await action() } catch (failure: unknown) { cleanupErrors.push(failure) }
    }
    await cleanup(async () => { await act(async () => { await drain() }) })
    await cleanup(async () => { await act(async () => { unmount?.(); await drain() }) })
    await cleanup(() => {
      stores.forEach((store) => {
        expect(store.getSnapshot(W)).toMatchObject({ writable: false, pending: false,
          readPending: false, openedBaseline: null, draft: null, overlay: {} })
      })
    })
    await cleanup(() => { evidence() })
    await cleanup(() => { unsubscribes.forEach((unsubscribe) => { unsubscribe() }); client.clear() })
    await cleanup(async () => { await tick(); await tick() })
    await cleanup(() => {
      window.removeEventListener('error', onError); window.removeEventListener('unhandledrejection', onRejection)
      vi.restoreAllMocks()
      storageBefore.forEach(({ key, value }) => {
        if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value)
      })
    })
    if (bodyFailed && cleanupErrors.length) console.error('Mounted H4 cleanup diagnostics; primary failure preserved', cleanupErrors)
  }
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'Mounted hostile seed cleanup failed')
})
