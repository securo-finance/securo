import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import { act, render } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, useWorkspace } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { useDashboardLayoutOwner } from '@/contexts/dashboard-layout-context'
import { getDashboardAuthSnapshot, subscribeDashboardAuth, type DashboardAuthSnapshot } from '@/lib/dashboard-layout-auth-bridge'
import { getPageSessionLease, type DialogTicket, type PageBinding, type PageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import { auth, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout, toPersistedLayout, toggleItem, validateCommittedDashboardLayout,
  validateDashboardLayoutRequest, type DashboardLayout, type DashboardRequestBinding, type CommittedDashboardLayout,
} from '@/lib/dashboard-layout'
import type { SessionStore, SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

// Only transport boundaries are replaced; selection, release and publication stay native.
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
const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'
const TOKEN = 'synthetic-selection-token'
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Deferred not initialized') }
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}
function required<T>(value: T | null, label: string): T {
  if (value === null) throw new Error(`Missing witness: ${label}`)
  return value
}
function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}
function notifications(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('discards captured unsent A through ordinary committed A→B→A selection, rejects stale cleanup, and explicitly saves fresh A', async () => {
  const defaults = freezeFixture(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, A))
  const firstReply: CommittedDashboardLayout = freezeFixture({ workspaceId: A, layout: {
    version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } })
  const secondReply: CommittedDashboardLayout = freezeFixture({ workspaceId: A, layout: {
    version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: false, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } })
  const sibling = freezeFixture(toPersistedLayout(B,
    toggleItem(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, B).items,
      'hero-balance', false), DASHBOARD_WIDGETS))
  function userWith(layout: DashboardLayout): User {
    return freezeFixture({ id: ACTOR, email: 'selection@example.invalid',
      is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
      preferences: { language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic selection',
        dashboard_layouts: { [A]: layout, [B]: sibling } },
    })
  }
  const seedLayout = freezeFixture(toPersistedLayout(A,
    toggleItem(defaults.items, 'period-transactions', false), DASHBOARD_WIDGETS))
  const seed = userWith(seedLayout)
  const baselineUser = userWith(defaults)
  // Post-GET leaves are independent literal fixtures, never echoed from requests/replies.
  const firstPostUser = userWith({ version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const secondPostUser = userWith({ version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: false, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  function workspace(id: string): Workspace {
    return freezeFixture({ id, name: `Selection ${id}`, kind: 'personal', is_archived: false,
      timezone: null,
      default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
      icon: null, color: null, created_at: '2026-10-04T00:00:00Z',
      created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
      enabled_modules: ['accounts', 'transactions', 'goals'],
    })
  }
  const workspaceA = workspace(A)
  const workspaceB = workspace(B)
  const fixtures = [seed, baselineUser, firstPostUser, secondPostUser, firstReply,
    secondReply, defaults, seedLayout, sibling, workspaceA, workspaceB]
  const preimages = structuredClone(fixtures)
  const users = [seed, baselineUser, firstPostUser, secondPostUser]
  const preferences = users.map((user) => user.preferences)
  // All controlled inputs exist before any premise can fail.
  const restoration = deferred<User>()
  const baselineRead = deferred<User>()
  const firstPut = deferred<CommittedDashboardLayout>()
  const secondPut = deferred<CommittedDashboardLayout>()
  const firstGet = deferred<User>()
  const secondGet = deferred<User>()
  type Phase = 'restore' | 'refresh' | 'first' | 'prepare' | 'insert' | 'to-B' | 'to-A' | 'fresh' | 'stale' | 'second' | 'remove' | 'root' | 'cleanup'
  let phase: Phase = 'restore'
  const errors: Array<{ phase: Phase; failure: unknown }> = []
  const tasks: Promise<void>[] = []
  const barriers: Promise<void>[] = []
  function record(callback: () => void): void {
    try { callback() } catch (failure: unknown) { errors.push({ phase, failure }) }
  }
  function track<T>(task: Promise<T>): Promise<T> {
    const issuedPhase = phase
    tasks.push(task.then(() => {}, (failure: unknown) => { errors.push({ phase: issuedPhase, failure }) }))
    return task
  }
  function barrier(task: Promise<void>): Promise<void> {
    const issuedPhase = phase
    const handled = task.then(() => {}, (failure: unknown) => { errors.push({ phase: issuedPhase, failure }) })
    barriers.push(handled)
    return handled
  }
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
  } })
  const portCalls: Array<{ port: string; phase: Phase }> = []
  const reads: Array<{ phase: Phase; user: User | null; completed: boolean }> = []
  const requests: Array<{ phase: Phase; id: string; layout: DashboardLayout;
    current: boolean; generation: number; contextTarget: string | null; storageTarget: string | null;
    binding: DashboardRequestBinding }> = []
  const state: { owner: DashboardLayoutOwner | null; auth: ReturnType<typeof useAuth> | null;
    current: Workspace | null; show: ((visible: boolean) => void) | null;
    select: ((workspace: Workspace) => void) | null } = {
    owner: null, auth: null, current: null, show: null, select: null,
  }
  ports.me.mockReset().mockImplementation(() => {
    portCalls.push({ port: 'GET', phase })
    const call: { phase: Phase; user: User | null; completed: boolean } = { phase, user: null, completed: false }
    reads.push(call)
    const read = reads.length === 1 ? restoration.promise : reads.length === 2 ? baselineRead.promise
      : reads.length === 3 ? firstGet.promise : reads.length === 4 ? secondGet.promise
        : Promise.reject(new Error('Unexpected extra GET'))
    track(read.then((user) => { call.user = user; call.completed = true }))
    return read
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    portCalls.push({ port: 'PUT', phase })
    const call = { phase, id, layout: structuredClone(layout), current: false,
      generation: getDashboardAuthSnapshot(client).generation, binding,
      contextTarget: state.current?.id ?? null, storageTarget: localStorage.getItem(WORKSPACE_STORAGE_KEY) }
    requests.push(call)
    record(() => { call.current = binding.isCurrent() })
    return track(requests.length === 1 ? firstPut.promise : requests.length === 2 ? secondPut.promise
      : Promise.reject(new Error('Unexpected extra PUT')))
  })
  ports.updateMe.mockReset().mockImplementation(() => {
    portCalls.push({ port: 'PATCH', phase }); return track(Promise.resolve(seed))
  })
  ports.login.mockReset().mockImplementation(() => track(Promise.reject(new Error('Unexpected login'))))
  ports.verify2fa.mockReset().mockImplementation(() => track(Promise.reject(new Error('Unexpected verify2fa'))))
  ports.register.mockReset().mockImplementation(() => track(Promise.reject(new Error('Unexpected register'))))
  const updateMeSpy = vi.spyOn(auth, 'updateMe')
  const warn = vi.spyOn(console, 'warn') // Passthrough, including during failure cleanup.
  const error = vi.spyOn(console, 'error')
  const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
  const contexts = new WeakSet<object>()
  const owners = new Set<DashboardLayoutOwner>()
  const stores = new Set<SessionStore>()
  const helpers = new Set<PageSessionLease>()
  const bindings: Array<{ binding: PageBinding; workspaceId: string; released: boolean }> = []
  const tickets: DialogTicket[] = []
  const sessionIds: string[] = []
  const unsubscribes: Array<() => void> = []
  const publications: Array<{ store: SessionStore; a: SessionSnapshot; b: SessionSnapshot }> = []
  const bridgeRecords: DashboardAuthSnapshot[] = []
  type Witness = {
    owner: DashboardLayoutOwner | null; store: SessionStore | null; client: QueryClient;
    user: User | null; token: string | null; loading: boolean; current: Workspace | null;
    bridge: DashboardAuthSnapshot; storedToken: string | null; storedWorkspace: string | null
  }
  const life: Array<{ event: 'setup' | 'cleanup'; capturedWorkspace: string; witness: Witness;
    retainedCaptured: string | null }> = []
  const layouts: Witness[] = []
  function watchAuth(context: ReturnType<typeof useAuth>) {
    if (!contexts.has(context)) {
      contexts.add(context); writerSpies.push(vi.spyOn(context, 'updateUser'))
    }
  }
  function witness(owner: DashboardLayoutOwner | null, context: ReturnType<typeof useAuth>,
    current: Workspace | null, queryClient: QueryClient): Witness {
    watchAuth(context)
    if (owner) {
      owners.add(owner)
      if (!stores.has(owner.store)) {
        stores.add(owner.store)
        unsubscribes.push(owner.store.subscribe(() => {
          record(() => { publications.push({ store: owner.store,
            a: owner.store.getSnapshot(A), b: owner.store.getSnapshot(B) }) })
        }))
      }
    }
    return { owner, store: owner?.store ?? null, client: queryClient,
      user: context.user, token: context.token, loading: context.isLoading, current,
      bridge: getDashboardAuthSnapshot(queryClient), storedToken: localStorage.getItem('token'),
      storedWorkspace: localStorage.getItem(WORKSPACE_STORAGE_KEY) }
  }
  function ObserveAuth({ children }: { children: ReactNode }) {
    const context = useAuth()
    watchAuth(context)
    useLayoutEffect(() => { state.auth = context })
    return children
  }
  function OwnerProbe() {
    const owner = useDashboardLayoutOwner()
    const context = useAuth()
    const current = useWorkspace().current
    const queryClient = useQueryClient()
    witness(owner, context, current, queryClient)
    useLayoutEffect(() => { state.owner = owner; state.current = current })
    return null
  }
  function Page() {
    const owner = useDashboardLayoutOwner()
    const context = useAuth()
    const current = useWorkspace().current
    const queryClient = useQueryClient()
    const target = current?.id ?? ''
    useLayoutEffect(() => { layouts.push(witness(owner, context, current, queryClient)) })
    useEffect(() => {
      const store = required(owner, 'Page owner').store
      const helper = getPageSessionLease(store)
      helpers.add(helper)
      helper.select(target)
      const binding = helper.bind(target)
      const held = { binding, workspaceId: target, released: false }
      bindings.push(held) // Track issuance immediately, including unexpected acquisitions.
      life.push({ event: 'setup', capturedWorkspace: target,
        witness: witness(owner, required(state.auth, 'committed Auth'), state.current, queryClient),
        retainedCaptured: store.retain(target) })
      return () => {
        // Captured target is old; committed Context/storage witness is deliberately current.
        record(() => { life.push({ event: 'cleanup', capturedWorkspace: target,
          witness: witness(state.owner, required(state.auth, 'cleanup Auth'), state.current, queryClient),
          retainedCaptured: store.retain(target) }) })
        binding.release(); held.released = true
      }
    }, [owner, target, queryClient])
    return null
  }
  function PagePlacement() {
    const [show, setShow] = useState(false)
    useLayoutEffect(() => { state.show = setShow }, [])
    return show ? <Page /> : null
  }
  function WorkspaceHarness() {
    const [current, setCurrent] = useState(workspaceA)
    useLayoutEffect(() => { state.select = setCurrent }, [])
    return <WorkspaceContext.Provider value={{ current, workspaces: [workspaceA, workspaceB],
      isLoading: false, switchWorkspace: async () => {}, refresh: async () => {},
      role: current.role, canManage: true, canWrite: true,
      enabledModules: ['accounts', 'transactions', 'goals'],
      hasModule: (id) => current.enabled_modules.includes(id) }}>
      <DashboardLayoutProvider><OwnerProbe /><PagePlacement /></DashboardLayoutProvider>
    </WorkspaceContext.Provider>
  }
  function Shell() {
    return <QueryClientProvider client={client}><AuthProvider><ObserveAuth>
      <WorkspaceHarness />
    </ObserveAuth></AuthProvider></QueryClientProvider>
  }
  async function drain() {
    // Barrier bookkeeping is separate: real task growth is never subtracted away.
    for (let turn = 0; turn < 8; turn += 1) {
      const before = [tasks.length, publications.length, bridgeRecords.length,
        requests.length, reads.length, life.length, stores.size, helpers.size]
      await Promise.all(tasks)
      await notifications(); await notifications()
      await Promise.all([...helpers].map((helper) => barrier(helper.whenIdle())))
      await Promise.all([...stores].map((store) => barrier(store.whenIdle())))
      await notifications(); await notifications()
      await Promise.all(tasks); await Promise.all(barriers)
      const after = [tasks.length, publications.length, bridgeRecords.length,
        requests.length, reads.length, life.length, stores.size, helpers.size]
      if (after.every((value, index) => value === before[index])) return
    }
    throw new Error('Work did not quiesce within eight native turns')
  }
  function cleanEvidence() {
    expect(errors).toEqual([])
    expect(warn.mock.calls).toEqual([]); expect(error.mock.calls).toEqual([])
    expect(updateMeSpy).not.toHaveBeenCalled(); expect(ports.updateMe).not.toHaveBeenCalled()
    expect(writerSpies.length).toBeGreaterThan(0)
    writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    expect(ports.login).not.toHaveBeenCalled(); expect(ports.verify2fa).not.toHaveBeenCalled()
    expect(ports.register).not.toHaveBeenCalled()
    expect(fixtures).toStrictEqual(preimages)
    users.forEach((user, index) => {
      expect(user.preferences).toBe(preferences[index]); expect(Object.isFrozen(user.preferences)).toBe(true)
    })
  }
  function stable(owner: DashboardLayoutOwner, bridge: DashboardAuthSnapshot, current: Workspace) {
    expect(state.owner).toBe(owner); expect([...owners]).toEqual([owner]); expect([...stores]).toEqual([owner.store])
    expect(owner.actorId).toBe(ACTOR); expect(owner.authGeneration).toBe(bridge.generation)
    expect(getDashboardAuthSnapshot(client)).toBe(bridge); expect(bridgeRecords).toEqual([])
    const context = required(state.auth, 'Auth')
    expect(context.user).toBe(seed); expect(context.token).toBe(TOKEN); expect(context.isLoading).toBe(false)
    expect(state.current).toBe(current)
    expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(current.id)
    ;[...layouts, ...life.map((entry) => entry.witness)].forEach((entry) => {
      expect(entry.owner).toBe(owner); expect(entry.store).toBe(owner.store); expect(entry.client).toBe(client)
      expect(entry.user).toBe(seed); expect(entry.token).toBe(TOKEN); expect(entry.loading).toBe(false)
      expect(entry.bridge).toBe(bridge); expect(entry.storedToken).toBe(TOKEN)
      expect(entry.storedWorkspace).toBe(entry.current?.id)
      expect([workspaceA, workspaceB]).toContain(entry.current)
    })
  }
  function preserved(store: SessionStore, layout: DashboardLayout, success: number) {
    expect(store.getSnapshot(A).layout).toStrictEqual(layout)
    expect(store.getSnapshot(A).overlay).toStrictEqual({ [A]: layout })
    const counters = { success, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 }
    expect(store.getSnapshot(A).counters).toStrictEqual(counters)
    expect(store.getSnapshot(B).counters).toStrictEqual(counters)
    expect(store.getSnapshot(B).overlay).toStrictEqual({ [A]: layout })
    expect(store.getSnapshot(B).layout).toStrictEqual(sibling)
  }
  function closed(store: SessionStore, id: string, stateValue: 'closed' | 'closed-success') {
    expect(store.getSnapshot(id)).toMatchObject({ state: stateValue, writable: true, provenance: 'saved',
      pending: false, readPending: false, openedBaseline: null, draft: null })
  }
  const prefix = [{ port: 'GET', phase: 'restore' }, { port: 'GET', phase: 'refresh' },
    { port: 'PUT', phase: 'first' }, { port: 'GET', phase: 'first' }]
  const expectedPorts = [...prefix, { port: 'PUT', phase: 'second' }, { port: 'GET', phase: 'second' }]
  const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let unmount: (() => void) | null = null
  let rootUnmounted = false
  let pageMounted = false
  let unsubscribeBridge = () => {}
  let primary: { phase: Phase; failure: unknown } | null = null
  const cleanupDiagnostics: Array<{ step: string; failure: unknown }> = []
  async function cleanupStep(step: string, callback: () => void | Promise<void>) {
    try { await callback() } catch (failure: unknown) { cleanupDiagnostics.push({ step, failure }) }
  }
  const onAsyncError = (event: ErrorEvent) => { errors.push({ phase, failure: event.error ?? event.message }) }
  const onRejection = (event: PromiseRejectionEvent) => { errors.push({ phase, failure: event.reason }) }
  window.addEventListener('error', onAsyncError); window.addEventListener('unhandledrejection', onRejection)
  try {
    localStorage.setItem('token', TOKEN); localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
    unmount = render(<Shell />, { reactStrictMode: false }).unmount
    await act(async () => { restoration.resolve(seed); await drain() })
    await act(async () => { await drain() })
    const owner = required(state.owner, 'established owner')
    const store = owner.store
    const bridge = getDashboardAuthSnapshot(client)
    unsubscribeBridge = subscribeDashboardAuth(client, () => {
      record(() => { bridgeRecords.push(getDashboardAuthSnapshot(client)) })
    })
    stable(owner, bridge, workspaceA)
    expect(reads).toEqual([{ phase: 'restore', user: seed, completed: true }])
    expect(bindings).toEqual([]); expect(life).toEqual([]); expect(requests).toEqual([])
    expect(store.getSnapshot(A).overlay).toStrictEqual({}); expect(store.getSnapshot(A).writable).toBe(false)
    phase = 'refresh'
    await act(async () => { const read = track(store.refresh(A)); baselineRead.resolve(baselineUser); await read; await drain() })
    closed(store, A, 'closed'); expect(store.getSnapshot(A).layout).toStrictEqual(defaults)
    expect(store.getSnapshot(B).layout).toStrictEqual(sibling)
    expect(validateCommittedDashboardLayout(A, firstReply, DASHBOARD_WIDGETS)).toStrictEqual(firstReply)
    expect(validateCommittedDashboardLayout(A, secondReply, DASHBOARD_WIDGETS)).toStrictEqual(secondReply)
    expect(firstPostUser.preferences.dashboard_layouts?.[A]).toStrictEqual(firstReply.layout)
    expect(secondPostUser.preferences.dashboard_layouts?.[A]).toStrictEqual(secondReply.layout)
    const firstId = store.open(A); sessionIds.push(firstId)
    const firstDraft = toPersistedLayout(A, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
    expect(firstDraft).not.toEqual(defaults); expect(firstDraft).toStrictEqual(firstReply.layout)
    store.setDraft(firstId, firstDraft.items)
    phase = 'first'
    const firstClose = track(store.finalize(firstId))
    await act(async () => { await notifications(); await notifications() })
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ phase: 'first', id: A, layout: firstReply.layout,
      current: true, generation: bridge.generation, contextTarget: A, storageTarget: A })
    expect(requests[0]?.binding.isCurrent()).toBe(true)
    await act(async () => { firstPut.resolve(firstReply); firstGet.resolve(firstPostUser); await firstClose; await drain() })
    closed(store, A, 'closed-success'); preserved(store, firstReply.layout, 1)
    stable(owner, bridge, workspaceA); cleanEvidence()
    phase = 'prepare'
    const oldId = store.open(A); sessionIds.push(oldId)
    expect(oldId).not.toBe(firstId)
    const unsent = toPersistedLayout(A, toggleItem(firstDraft.items, 'spending-charts', false), DASHBOARD_WIDGETS)
    store.setDraft(oldId, unsent.items)
    await act(async () => { await drain() })
    const editing = store.getSnapshot(A)
    expect(editing).toMatchObject({ state: 'editing', openedBaseline: firstReply.layout, draft: unsent })
    expect(editing.openedBaseline?.items.find((item) => item.id === 'spending-charts')?.visible).toBe(true)
    expect(editing.draft?.items.find((item) => item.id === 'spending-charts')?.visible).toBe(false)
    expect(bindings).toEqual([])
    phase = 'insert'
    act(() => { required(state.show, 'insert page')(true); pageMounted = true })
    await act(async () => { await drain() })
    const oldBinding = required(bindings[0]?.binding ?? null, 'old A binding')
    const oldTicket = oldBinding.open(); tickets.push(oldTicket)
    expect(oldTicket.sessionId).toBe(oldId); expect(oldTicket.workspaceId).toBe(A)
    expect(store.retain(A)).toBe(oldId); expect(store.getSnapshot(A)).toBe(editing)
    expect(life.map((entry) => [entry.event, entry.capturedWorkspace])).toEqual([['setup', A]])
    expect(bindings).toHaveLength(1); expect(bindings[0]?.released).toBe(false)
    expect(life[0]?.retainedCaptured).toBe(oldId)
    expect(portCalls).toEqual(prefix); preserved(store, firstReply.layout, 1)
    stable(owner, bridge, workspaceA); cleanEvidence()
    phase = 'to-B'
    act(() => {
      localStorage.setItem(WORKSPACE_STORAGE_KEY, B)
      required(state.select, 'Context selection')(workspaceB)
    })
    await act(async () => { await drain() })
    expect(life.map((entry) => [entry.event, entry.capturedWorkspace])).toEqual([['setup', A], ['cleanup', A], ['setup', B]])
    expect(life[1]?.witness.current).toBe(workspaceB); expect(life[1]?.witness.storedWorkspace).toBe(B)
    expect(life[1]?.retainedCaptured).toBeNull() // Actual committed getter before passive select; not deletion proof.
    expect(life[2]?.witness.current).toBe(workspaceB); expect(bindings[0]?.released).toBe(true)
    expect(bindings).toHaveLength(2); expect(bindings[1]?.released).toBe(false)
    closed(store, A, 'closed'); closed(store, B, 'closed'); expect(store.retain(B)).toBeNull()
    preserved(store, firstReply.layout, 1); expect(portCalls).toEqual(prefix)
    stable(owner, bridge, workspaceB); cleanEvidence()
    phase = 'to-A'
    act(() => {
      localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
      required(state.select, 'Context return')(workspaceA)
    })
    await act(async () => { await drain() })
    expect(life.map((entry) => [entry.event, entry.capturedWorkspace])).toEqual([
      ['setup', A], ['cleanup', A], ['setup', B], ['cleanup', B], ['setup', A],
    ])
    expect(life[3]?.witness.current).toBe(workspaceA); expect(life[3]?.witness.storedWorkspace).toBe(A)
    expect(life[3]?.retainedCaptured).toBeNull(); expect(life[4]?.retainedCaptured).toBeNull()
    expect(bindings).toHaveLength(3); expect(bindings.map((held) => held.released)).toEqual([true, true, false])
    expect([...helpers]).toEqual([getPageSessionLease(store)])
    closed(store, A, 'closed'); expect(store.retain(A)).toBeNull()
    // Under returned current A and the SAME generation, this proves removal, not target filtering.
    expect(() => store.setDraft(oldId, unsent.items)).toThrow('Dashboard layout session is not editable')
    preserved(store, firstReply.layout, 1); expect(portCalls).toEqual(prefix)
    stable(owner, bridge, workspaceA); cleanEvidence()
    phase = 'fresh'
    const freshBinding = required(bindings[2]?.binding ?? null, 'fresh A binding')
    expect(freshBinding).not.toBe(oldBinding)
    const freshTicket = freshBinding.open(); tickets.push(freshTicket); sessionIds.push(freshTicket.sessionId)
    expect(freshTicket).not.toBe(oldTicket); expect(freshTicket.sessionId).not.toBe(oldId)
    expect(freshTicket.sessionId).not.toBe(firstId); expect(Object.isFrozen(freshTicket)).toBe(true)
    expect(store.retain(A)).toBe(freshTicket.sessionId)
    expect(store.getSnapshot(A)).toMatchObject({ state: 'editing', openedBaseline: firstReply.layout, draft: firstReply.layout })
    const freshDraft = toPersistedLayout(A, toggleItem(store.getSnapshot(A).draft!.items, 'balance-evolution', false), DASHBOARD_WIDGETS)
    expect(freshDraft).toStrictEqual(secondReply.layout); expect(freshDraft).not.toEqual(unsent)
    store.setDraft(freshTicket.sessionId, freshDraft.items)
    await act(async () => { await drain() })
    const fresh = store.getSnapshot(A)
    const publicationCount = publications.length
    phase = 'stale'
    oldBinding.release() // Already released: idempotence only, not first release of a held stale handle.
    const expectedStale: { completed: boolean; rejected: boolean; failure: unknown } = {
      completed: false, rejected: false, failure: null,
    }
    const staleClose = oldBinding.close(oldTicket, true)
    const staleObservation = staleClose.then(() => { expectedStale.completed = true }, (failure: unknown) => {
      expectedStale.completed = true; expectedStale.rejected = true; expectedStale.failure = failure
    })
    track(staleObservation) // Expected rejection is observed separately from unexpected errors.
    await act(async () => { await staleObservation; await drain() })
    expect(expectedStale.completed).toBe(true); expect(expectedStale.rejected).toBe(true)
    expect(expectedStale.failure).toBeInstanceOf(Error)
    expect(expectedStale.failure).toMatchObject({ message: 'Dashboard page ticket is stale' })
    expect(store.getSnapshot(A)).toBe(fresh); expect(store.retain(A)).toBe(freshTicket.sessionId)
    expect(fresh.openedBaseline).toStrictEqual(firstReply.layout); expect(fresh.draft).toStrictEqual(freshDraft)
    expect(publications).toHaveLength(publicationCount); expect(portCalls).toEqual(prefix)
    const sameTicket = freshBinding.open(); tickets.push(sameTicket)
    expect(sameTicket).toBe(freshTicket); expect(store.getSnapshot(A)).toBe(fresh)
    preserved(store, firstReply.layout, 1); stable(owner, bridge, workspaceA); cleanEvidence()
    phase = 'second'
    const secondClose = track(freshBinding.close(freshTicket, true))
    await act(async () => { await notifications(); await notifications() })
    expect(requests).toHaveLength(2)
    expect(requests[1]).toMatchObject({ phase: 'second', id: A, layout: secondReply.layout,
      current: true, generation: bridge.generation, contextTarget: A, storageTarget: A })
    expect(requests[1]?.binding.isCurrent()).toBe(true)
    expect(store.retain(A)).toBe(freshTicket.sessionId)
    expect(store.getSnapshot(A)).toMatchObject({ state: 'pending', pending: true, draft: freshDraft, openedBaseline: firstReply.layout })
    await act(async () => { secondPut.resolve(secondReply); secondGet.resolve(secondPostUser); await secondClose; await drain() })
    closed(store, A, 'closed-success'); expect(store.retain(A)).toBeNull()
    preserved(store, secondReply.layout, 2); stable(owner, bridge, workspaceA); cleanEvidence()
    expect(portCalls).toEqual(expectedPorts)
    expect(reads).toEqual([{ phase: 'restore', user: seed, completed: true },
      { phase: 'refresh', user: baselineUser, completed: true },
      { phase: 'first', user: firstPostUser, completed: true },
      { phase: 'second', user: secondPostUser, completed: true }])
    requests.forEach((request, index) => {
      expect(validateDashboardLayoutRequest(A, request.layout, DASHBOARD_WIDGETS)).toStrictEqual(
        index === 0 ? firstReply.layout : secondReply.layout)
    })
    expect(sessionIds).toEqual([firstId, oldId, freshTicket.sessionId])
    expect(tickets).toEqual([oldTicket, freshTicket, freshTicket])
    const finalSaved = store.getSnapshot(A)
    const finalPublications = publications.length
    phase = 'remove'
    act(() => { required(state.show, 'remove page')(false); pageMounted = false })
    await act(async () => { await drain() })
    expect(life.map((entry) => [entry.event, entry.capturedWorkspace])).toEqual([
      ['setup', A], ['cleanup', A], ['setup', B], ['cleanup', B], ['setup', A], ['cleanup', A],
    ])
    expect(bindings.every((held) => held.released)).toBe(true)
    expect(store.getSnapshot(A)).toBe(finalSaved); expect(publications).toHaveLength(finalPublications)
    expect(portCalls).toEqual(expectedPorts); preserved(store, secondReply.layout, 2)
    stable(owner, bridge, workspaceA); cleanEvidence()
    phase = 'root'
    act(() => { required(unmount, 'root teardown')(); rootUnmounted = true })
    await act(async () => { await drain() })
    expect(store.getSnapshot(A)).toMatchObject({ writable: false, pending: false, readPending: false,
      state: 'closed', openedBaseline: null, draft: null, overlay: {} })
    expect(store.getSnapshot(B)).toMatchObject({ writable: false, pending: false, readPending: false,
      state: 'closed', openedBaseline: null, draft: null, overlay: {} })
    expect(store.getSnapshot(A).overlay).toStrictEqual({})
    expect(store.getSnapshot(B).overlay).toStrictEqual({})
    expect(store.getSnapshot(A).counters).toStrictEqual(finalSaved.counters)
    expect(store.getSnapshot(B).counters).toStrictEqual(finalSaved.counters)
    expect(getDashboardAuthSnapshot(client)).toStrictEqual({ generation: bridge.generation, revision: bridge.revision + 1 })
    expect(bridgeRecords).toEqual([getDashboardAuthSnapshot(client)])
    expect(portCalls).toEqual(expectedPorts); expect(requests).toHaveLength(2)
    expect(reads.every((call) => call.completed)).toBe(true)
    expect(bindings.every((held) => held.released)).toBe(true); cleanEvidence()
  } catch (failure: unknown) {
    primary = { phase, failure }
  } finally {
    phase = 'cleanup'
    restoration.resolve(seed); baselineRead.resolve(baselineUser)
    firstPut.resolve(firstReply); secondPut.resolve(secondReply)
    firstGet.resolve(firstPostUser); secondGet.resolve(secondPostUser)
    // Each cleanup step is independent; no teardown exception can replace body attribution.
    await cleanupStep('remove actual Page', async () => {
      await act(async () => {
        if (pageMounted && !rootUnmounted && state.show !== null) { state.show(false); pageMounted = false }
        await notifications()
      })
    })
    for (const held of bindings) {
      await cleanupStep(`release ${held.workspaceId}`, () => { held.binding.release(); held.released = true })
    }
    await cleanupStep('pre-root native/helper/store/tasks', async () => { await act(async () => { await drain() }) })
    await cleanupStep('root teardown', () => {
      if (!rootUnmounted && unmount !== null) { unmount(); rootUnmounted = true }
    })
    await cleanupStep('post-root native/helper/store/tasks', async () => { await act(async () => { await drain() }) })
    await cleanupStep('failure evidence', () => { cleanEvidence(); expect(bindings.every((held) => held.released)).toBe(true) })
    await cleanupStep('unsubscribe bridge', unsubscribeBridge)
    for (const unsubscribe of unsubscribes) await cleanupStep('unsubscribe store', unsubscribe)
    await cleanupStep('clear client', () => { client.clear() })
    await cleanupStep('final native/helper/store/tasks', async () => { await notifications(); await drain(); cleanEvidence() })
    await cleanupStep('remove error listeners', () => {
      window.removeEventListener('error', onAsyncError); window.removeEventListener('unhandledrejection', onRejection)
    })
    await cleanupStep('restore spies', () => { vi.restoreAllMocks() })
    for (const { key, value } of storageBefore) {
      await cleanupStep(`restore storage ${key}`, () => {
        if (value === null) localStorage.removeItem(key)
        else localStorage.setItem(key, value)
      })
    }
  }
  if (primary !== null || cleanupDiagnostics.length > 0) {
    const failures = [...(primary === null ? [] : [primary.failure]), ...cleanupDiagnostics.map((entry) => entry.failure)]
    throw new AggregateError(failures,
      `Selection tracer ${primary === null ? 'cleanup failure' : `body failure in ${primary.phase}`}; cleanup steps: ${cleanupDiagnostics.map((entry) => entry.step).join(', ')}`,
      primary === null ? undefined : { cause: primary.failure })
  }
})
