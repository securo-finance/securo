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

// Transport replacement only: real Auth, owner, store, helper and native queue.
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
const TOKEN = 'synthetic-pending-read-token'
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Deferred not initialized') }
  let reject: (reason: unknown) => void = () => { throw new Error('Deferred not initialized') }
  const promise = new Promise<T>((success, failure) => { resolve = success; reject = failure })
  return { promise, resolve, reject }
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

it('waits for a pending read before saving the captured dialog, then refuses a failed read on the same ordinary binding', async () => {
  const defaults = freezeFixture(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, A))
  const savedReply: CommittedDashboardLayout = freezeFixture({ workspaceId: A, layout: {
    version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } })
  const dialogReply: CommittedDashboardLayout = freezeFixture({ workspaceId: A, layout: {
    version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } })
  const sibling = freezeFixture(toPersistedLayout(B,
    toggleItem(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, B).items,
      'hero-balance', false), DASHBOARD_WIDGETS))
  function userWith(layout: DashboardLayout): User {
    return freezeFixture({ id: ACTOR, email: 'pending-read@example.invalid',
      is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
      preferences: { language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic pending read',
        dashboard_layouts: { [A]: layout, [B]: sibling } },
    })
  }
  const seedLayout = freezeFixture(toPersistedLayout(A,
    toggleItem(defaults.items, 'period-transactions', false), DASHBOARD_WIDGETS))
  const seed = userWith(seedLayout)
  const baselineUser = userWith(defaults)
  // Independent literal post-GETs and held R: never echoed from submitted payloads.
  const savedPostUser = userWith({ version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const refreshedUser = userWith({ version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: false, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const dialogPostUser = userWith({ version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const workspaceA: Workspace = freezeFixture({ id: A, name: 'Pending read', kind: 'personal',
    timezone: null,
    is_archived: false, default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
    icon: null, color: null, created_at: '2026-10-04T00:00:00Z',
    created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
    enabled_modules: ['accounts', 'transactions', 'goals'],
  })
  const users = [seed, baselineUser, savedPostUser, refreshedUser, dialogPostUser]
  const fixtures = [...users, savedReply, dialogReply, defaults, seedLayout, sibling, workspaceA]
  const preimages = structuredClone(fixtures)
  const preferences = users.map((user) => user.preferences)
  // ALL success/failure inputs and both settlement functions precede premise assertions.
  const restoration = deferred<User>()
  const baselineRead = deferred<User>()
  const savedPut = deferred<CommittedDashboardLayout>()
  const savedGet = deferred<User>()
  const heldSuccessRead = deferred<User>()
  const dialogPut = deferred<CommittedDashboardLayout>()
  const dialogGet = deferred<User>()
  const heldFailedRead = deferred<User>()
  const boundaryFailure = new Error('Synthetic held dashboard GET rejection')
  type Phase = 'restore' | 'baseline' | 'pre-save' | 'insert' | 'success-read' | 'success-save' | 'failed-read' | 'remove' | 'root' | 'cleanup'
  let phase: Phase = 'restore'
  const errors: Array<{ phase: Phase; failure: unknown }> = []
  const tasks: Promise<void>[] = []
  const barriers: Promise<void>[] = []
  type Outcome = { completed: boolean; rejected: boolean; failure: unknown; settled: Promise<void> }
  function record(callback: () => void): void {
    try { callback() } catch (failure: unknown) { errors.push({ phase, failure }) }
  }
  function observe<T>(task: Promise<T>, expectedRejection = false): Outcome {
    const issuedPhase = phase
    const outcome: Outcome = { completed: false, rejected: false, failure: null, settled: Promise.resolve() }
    outcome.settled = task.then(() => { outcome.completed = true }, (failure: unknown) => {
      outcome.completed = true; outcome.rejected = true; outcome.failure = failure
      if (!expectedRejection) errors.push({ phase: issuedPhase, failure })
    })
    tasks.push(outcome.settled) // Immediately handles EVERY issued original promise.
    return outcome
  }
  function track<T>(task: Promise<T>): Promise<T> { observe(task); return task }
  function barrier(task: Promise<void>): Promise<void> {
    const issuedPhase = phase
    const handled = task.then(() => {}, (failure: unknown) => { errors.push({ phase: issuedPhase, failure }) })
    barriers.push(handled); return handled
  }
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
  } })
  const state: { owner: DashboardLayoutOwner | null; auth: ReturnType<typeof useAuth> | null;
    current: Workspace | null; show: ((visible: boolean) => void) | null } = {
    owner: null, auth: null, current: null, show: null,
  }
  const portCalls: Array<{ port: string; phase: Phase }> = []
  const reads: Array<{ phase: Phase; user: User | null; outcome: Outcome }> = []
  const requests: Array<{ phase: Phase; id: string; layout: DashboardLayout; current: boolean;
    generation: number; actor: string | null; token: string | null; storedToken: string | null;
    contextTarget: string | null; storageTarget: string | null; binding: DashboardRequestBinding }> = []
  ports.me.mockReset().mockImplementation(() => {
    portCalls.push({ port: 'GET', phase })
    const index = reads.length
    const read = index === 0 ? restoration.promise : index === 1 ? baselineRead.promise
      : index === 2 ? savedGet.promise : index === 3 ? heldSuccessRead.promise
        : index === 4 ? dialogGet.promise : index === 5 ? heldFailedRead.promise
          : Promise.reject(new Error('Unexpected extra GET'))
    const call = { phase, user: null as User | null, outcome: observe(read, index === 5) }
    reads.push(call)
    // A separate handled observer records data only, never assertions in API catches.
    track(read.then((user) => { call.user = user }, () => {}))
    return read
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    portCalls.push({ port: 'PUT', phase })
    const call = { phase, id, layout: structuredClone(layout), current: false,
      generation: getDashboardAuthSnapshot(client).generation, binding,
      actor: state.auth?.user?.id ?? null, token: state.auth?.token ?? null,
      storedToken: localStorage.getItem('token'), contextTarget: state.current?.id ?? null,
      storageTarget: localStorage.getItem(WORKSPACE_STORAGE_KEY) }
    requests.push(call); record(() => { call.current = binding.isCurrent() })
    return track(requests.length === 1 ? savedPut.promise : requests.length === 2 ? dialogPut.promise
      : Promise.reject(new Error('Unexpected extra PUT')))
  })
  ports.updateMe.mockReset().mockImplementation(() => {
    portCalls.push({ port: 'PATCH', phase }); return track(Promise.resolve(seed))
  })
  ports.login.mockReset().mockImplementation(() => track(Promise.reject(new Error('Unexpected login'))))
  ports.verify2fa.mockReset().mockImplementation(() => track(Promise.reject(new Error('Unexpected verify2fa'))))
  ports.register.mockReset().mockImplementation(() => track(Promise.reject(new Error('Unexpected register'))))
  const updateMeSpy = vi.spyOn(auth, 'updateMe')
  const warn = vi.spyOn(console, 'warn') // Passthrough even on failure cleanup.
  const error = vi.spyOn(console, 'error')
  const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
  const contexts = new WeakSet<object>()
  const owners = new Set<DashboardLayoutOwner>()
  const stores = new Set<SessionStore>()
  const helpers = new Set<PageSessionLease>()
  const bindings: Array<{ binding: PageBinding; released: boolean }> = []
  const tickets: DialogTicket[] = []
  const unsubscribes: Array<() => void> = []
  const publications: Array<{ a: SessionSnapshot; retained: string | null }> = []
  const bridgeRecords: DashboardAuthSnapshot[] = []
  type Witness = { owner: DashboardLayoutOwner | null; client: QueryClient; user: User | null;
    token: string | null; loading: boolean; current: Workspace | null; bridge: DashboardAuthSnapshot;
    storedToken: string | null; storedWorkspace: string | null }
  const captures: Witness[] = []
  const life: string[] = []
  function watchAuth(context: ReturnType<typeof useAuth>) {
    if (!contexts.has(context)) { contexts.add(context); writerSpies.push(vi.spyOn(context, 'updateUser')) }
  }
  function witness(owner: DashboardLayoutOwner | null, context: ReturnType<typeof useAuth>,
    current: Workspace | null, queryClient: QueryClient): Witness {
    watchAuth(context)
    if (owner) {
      owners.add(owner)
      if (!stores.has(owner.store)) {
        stores.add(owner.store)
        unsubscribes.push(owner.store.subscribe(() => {
          record(() => { publications.push({ a: owner.store.getSnapshot(A), retained: owner.store.retain(A) }) })
        }))
      }
    }
    return { owner, client: queryClient, user: context.user, token: context.token,
      loading: context.isLoading, current, bridge: getDashboardAuthSnapshot(queryClient),
      storedToken: localStorage.getItem('token'), storedWorkspace: localStorage.getItem(WORKSPACE_STORAGE_KEY) }
  }
  function ObserveAuth({ children }: { children: ReactNode }) {
    const context = useAuth(); watchAuth(context)
    useLayoutEffect(() => { state.auth = context })
    return children
  }
  function OwnerProbe() {
    const owner = useDashboardLayoutOwner()
    const context = useAuth(); const current = useWorkspace().current; const queryClient = useQueryClient()
    witness(owner, context, current, queryClient)
    useLayoutEffect(() => { state.owner = owner; state.current = current })
    return null
  }
  function Page() {
    const owner = useDashboardLayoutOwner()
    const context = useAuth(); const current = useWorkspace().current; const queryClient = useQueryClient()
    const target = current?.id ?? ''
    useLayoutEffect(() => { captures.push(witness(owner, context, current, queryClient)) })
    useEffect(() => {
      const helper = getPageSessionLease(required(owner, 'Page owner').store)
      helpers.add(helper)
      const binding = helper.bind(target)
      const held = { binding, released: false }
      bindings.push(held) // Track even an unexpected nonnull acquisition immediately.
      life.push('setup')
      return () => { life.push('cleanup'); binding.release(); held.released = true }
    }, [owner, target, queryClient])
    return null
  }
  function PagePlacement() {
    const [show, setShow] = useState(false)
    useLayoutEffect(() => { state.show = setShow }, [])
    return show ? <Page /> : null
  }
  function WorkspaceHarness() {
    const [current] = useState(workspaceA) // Stateful real Context; deliberately no selection stimulus.
    return <WorkspaceContext.Provider value={{ current, workspaces: [workspaceA],
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
    // Separate barrier bookkeeping never hides real issued-task growth.
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
    expect(errors).toEqual([]); expect(warn.mock.calls).toEqual([]); expect(error.mock.calls).toEqual([])
    expect(updateMeSpy).not.toHaveBeenCalled(); expect(ports.updateMe).not.toHaveBeenCalled()
    expect(writerSpies.length).toBeGreaterThan(0)
    writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    expect(ports.login).not.toHaveBeenCalled(); expect(ports.verify2fa).not.toHaveBeenCalled()
    expect(ports.register).not.toHaveBeenCalled(); expect(fixtures).toStrictEqual(preimages)
    users.forEach((user, index) => {
      expect(user.preferences).toBe(preferences[index]); expect(Object.isFrozen(user.preferences)).toBe(true)
      expect(user.preferences.dashboard_layouts?.[B]).toStrictEqual(sibling)
    })
  }
  function stable(owner: DashboardLayoutOwner, bridge: DashboardAuthSnapshot, binding: PageBinding | null) {
    expect(state.owner).toBe(owner); expect([...owners]).toEqual([owner]); expect([...stores]).toEqual([owner.store])
    expect(owner.actorId).toBe(ACTOR); expect(owner.authGeneration).toBe(bridge.generation)
    expect(getDashboardAuthSnapshot(client)).toBe(bridge); expect(bridgeRecords).toEqual([])
    const context = required(state.auth, 'Auth')
    expect(context.user).toBe(seed); expect(context.token).toBe(TOKEN); expect(context.isLoading).toBe(false)
    expect(state.current).toBe(workspaceA)
    expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(A)
    captures.forEach((entry) => {
      expect(entry.owner).toBe(owner); expect(entry.client).toBe(client); expect(entry.current).toBe(workspaceA)
      expect(entry.user).toBe(seed); expect(entry.token).toBe(TOKEN); expect(entry.loading).toBe(false)
      expect(entry.bridge).toBe(bridge); expect(entry.storedToken).toBe(TOKEN); expect(entry.storedWorkspace).toBe(A)
    })
    if (binding !== null) {
      expect(bindings).toHaveLength(1); expect(bindings[0]?.binding).toBe(binding)
      expect(bindings[0]?.released).toBe(false); expect(life).toEqual(['setup'])
      expect([...helpers]).toEqual([getPageSessionLease(owner.store)])
    }
  }
  function globals(store: SessionStore, layout: DashboardLayout, success: number) {
    const counters = { success, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 }
    expect(store.getSnapshot(A).overlay).toStrictEqual({ [A]: layout })
    expect(store.getSnapshot(B).overlay).toStrictEqual({ [A]: layout })
    expect(store.getSnapshot(A).counters).toStrictEqual(counters)
    expect(store.getSnapshot(B).counters).toStrictEqual(counters)
    expect(store.getSnapshot(B).layout).toStrictEqual(sibling)
  }
  function saved(store: SessionStore, layout: DashboardLayout, success: number) {
    expect(store.retain(A)).toBeNull()
    expect(store.getSnapshot(A)).toMatchObject({ state: 'closed-success', writable: true, provenance: 'saved',
      pending: false, readPending: false, openedBaseline: null, draft: null })
    expect(store.getSnapshot(A).layout).toStrictEqual(layout); globals(store, layout, success)
  }
  function request(index: number, layout: DashboardLayout, bridge: DashboardAuthSnapshot) {
    expect(requests[index]).toMatchObject({ id: A, layout, current: true, generation: bridge.generation,
      actor: ACTOR, token: TOKEN, storedToken: TOKEN, contextTarget: A, storageTarget: A })
    expect(requests[index]?.binding.isCurrent()).toBe(true)
    expect(validateDashboardLayoutRequest(A, requests[index]?.layout, DASHBOARD_WIDGETS)).toStrictEqual(layout)
  }
  function rejection(outcome: Outcome, message: string) {
    expect(outcome.completed).toBe(true); expect(outcome.rejected).toBe(true)
    expect(outcome.failure).toBeInstanceOf(Error); expect(outcome.failure).toMatchObject({ message })
  }
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
    const owner = required(state.owner, 'established owner'); const store = owner.store
    const bridge = getDashboardAuthSnapshot(client)
    unsubscribeBridge = subscribeDashboardAuth(client, () => {
      record(() => { bridgeRecords.push(getDashboardAuthSnapshot(client)) })
    })
    stable(owner, bridge, null)
    expect(reads).toHaveLength(1); expect(reads[0]?.user).toBe(seed)
    expect(bindings).toEqual([]); expect(requests).toEqual([])
    expect(store.getSnapshot(A).overlay).toStrictEqual({}); expect(store.getSnapshot(A).writable).toBe(false)
    phase = 'baseline'
    await act(async () => { const read = track(store.refresh(A)); baselineRead.resolve(baselineUser); await read; await drain() })
    expect(store.getSnapshot(A)).toMatchObject({ state: 'closed', writable: true, readPending: false })
    expect(store.getSnapshot(A).layout).toStrictEqual(defaults)
    expect(store.getSnapshot(B).layout).toStrictEqual(sibling)
    expect(validateCommittedDashboardLayout(A, savedReply, DASHBOARD_WIDGETS)).toStrictEqual(savedReply)
    expect(validateCommittedDashboardLayout(A, dialogReply, DASHBOARD_WIDGETS)).toStrictEqual(dialogReply)
    expect(savedPostUser.preferences.dashboard_layouts?.[A]).toStrictEqual(savedReply.layout)
    expect(dialogPostUser.preferences.dashboard_layouts?.[A]).toStrictEqual(dialogReply.layout)
    const seedSaveId = store.open(A)
    const s0 = toPersistedLayout(A, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
    expect(s0).not.toEqual(defaults); expect(s0).toStrictEqual(savedReply.layout)
    store.setDraft(seedSaveId, s0.items)
    phase = 'pre-save'
    const preSave = track(store.finalize(seedSaveId))
    await act(async () => { await notifications(); await notifications() })
    expect(requests).toHaveLength(1); request(0, savedReply.layout, bridge)
    await act(async () => { savedPut.resolve(savedReply); savedGet.resolve(savedPostUser); await preSave; await drain() })
    saved(store, s0, 1); stable(owner, bridge, null); cleanEvidence()
    const beforeMount = store.getSnapshot(A); const beforeMountPublications = publications.length
    phase = 'insert'
    act(() => { required(state.show, 'insert Page')(true); pageMounted = true })
    await act(async () => { await drain() })
    const binding = required(bindings[0]?.binding ?? null, 'held binding')
    stable(owner, bridge, binding); expect(store.getSnapshot(A)).toBe(beforeMount)
    expect(publications).toHaveLength(beforeMountPublications); expect(store.retain(A)).toBeNull()
    const prefix = [{ port: 'GET', phase: 'restore' }, { port: 'GET', phase: 'baseline' },
      { port: 'PUT', phase: 'pre-save' }, { port: 'GET', phase: 'pre-save' }]
    expect(portCalls).toEqual(prefix)
    phase = 'success-read'
    const t1 = binding.open(); tickets.push(t1)
    expect(t1).toStrictEqual({ workspaceId: A, sessionId: store.retain(A) })
    expect(t1.sessionId).not.toBe(seedSaveId); expect(Object.isFrozen(t1)).toBe(true)
    const d1 = toPersistedLayout(A, toggleItem(s0.items, 'spending-charts', false), DASHBOARD_WIDGETS)
    const r = required(refreshedUser.preferences.dashboard_layouts?.[A] ?? null, 'literal R')
    expect(d1).toStrictEqual(dialogReply.layout); expect(d1).not.toEqual(s0); expect(d1).not.toEqual(r)
    expect(r).not.toEqual(s0)
    store.setDraft(t1.sessionId, d1.items)
    const successRead = store.refresh(A); const successReadOutcome = observe(successRead)
    const held = store.getSnapshot(A)
    expect(held).toMatchObject({ state: 'editing', readPending: true, pending: false, writable: true,
      openedBaseline: s0, draft: d1, layout: s0 })
    expect(store.retain(A)).toBe(t1.sessionId); globals(store, s0, 1); stable(owner, bridge, binding)
    const successClose = binding.close(t1, true); const successCloseOutcome = observe(successClose)
    const successDuplicate = binding.close(t1, true); const successDuplicateOutcome = observe(successDuplicate)
    expect(successDuplicate).toBe(successClose)
    expect(() => binding.open()).toThrow(new Error('Dashboard page close is pending'))
    // Exactly TWO native barriers while this GET is held; never an idle wait here.
    await act(async () => { await notifications(); await notifications() })
    expect(successReadOutcome.completed).toBe(false); expect(reads[3]?.outcome.completed).toBe(false)
    expect(successCloseOutcome.completed).toBe(false); expect(successDuplicateOutcome.completed).toBe(false)
    expect(store.getSnapshot(A)).toBe(held); expect(store.retain(A)).toBe(t1.sessionId)
    expect(portCalls).toEqual([...prefix, { port: 'GET', phase: 'success-read' }])
    globals(store, s0, 1); stable(owner, bridge, binding); cleanEvidence()
    phase = 'success-save'
    await act(async () => { heldSuccessRead.resolve(refreshedUser); await successRead; await notifications(); await notifications() })
    // Native publication before the read latch settles exposes R without changing capture.
    const refreshed = publications.find((entry) => entry.a.layout.items.some((item) => item.id === 'balance-evolution' && !item.visible))
    expect(refreshed?.a.layout).toStrictEqual(r)
    expect(refreshed?.a.openedBaseline).toStrictEqual(s0); expect(refreshed?.a.draft).toStrictEqual(d1)
    expect(refreshed?.retained).toBe(t1.sessionId)
    expect(successReadOutcome).toMatchObject({ completed: true, rejected: false })
    expect(reads[3]?.user).toBe(refreshedUser); expect(requests).toHaveLength(2)
    request(1, d1, bridge); expect(requests[1]?.layout).not.toEqual(r)
    expect(store.retain(A)).toBe(t1.sessionId)
    expect(store.getSnapshot(A)).toMatchObject({ state: 'pending', openedBaseline: s0, draft: d1, layout: r })
    globals(store, s0, 1); stable(owner, bridge, binding)
    await act(async () => { dialogPut.resolve(dialogReply); dialogGet.resolve(dialogPostUser); await successClose; await successDuplicate; await drain() })
    expect(successCloseOutcome).toMatchObject({ completed: true, rejected: false })
    expect(successDuplicateOutcome).toMatchObject({ completed: true, rejected: false })
    saved(store, d1, 2); stable(owner, bridge, binding); cleanEvidence()
    const successPorts = [...prefix, { port: 'GET', phase: 'success-read' },
      { port: 'PUT', phase: 'success-save' }, { port: 'GET', phase: 'success-save' }]
    expect(portCalls).toEqual(successPorts)
    phase = 'failed-read'
    const t2 = binding.open(); tickets.push(t2)
    expect(t2).not.toBe(t1); expect(t2.sessionId).not.toBe(t1.sessionId)
    expect(t2).toStrictEqual({ workspaceId: A, sessionId: store.retain(A) })
    expect(store.getSnapshot(A)).toMatchObject({ state: 'editing', openedBaseline: d1, draft: d1 })
    const d2 = toPersistedLayout(A, toggleItem(d1.items, 'period-transactions', false), DASHBOARD_WIDGETS)
    expect(d2).not.toEqual(d1); store.setDraft(t2.sessionId, d2.items)
    const failedRead = store.refresh(A); const failedReadOutcome = observe(failedRead, true)
    const heldFailure = store.getSnapshot(A)
    expect(heldFailure).toMatchObject({ state: 'editing', readPending: true, pending: false, writable: true,
      openedBaseline: d1, draft: d2, layout: d1 })
    expect(store.retain(A)).toBe(t2.sessionId); globals(store, d1, 2); stable(owner, bridge, binding)
    const failedClose = binding.close(t2, true); const failedCloseOutcome = observe(failedClose, true)
    const failedDuplicate = binding.close(t2, true); const failedDuplicateOutcome = observe(failedDuplicate, true)
    expect(failedDuplicate).toBe(failedClose)
    expect(() => binding.open()).toThrow(new Error('Dashboard page close is pending'))
    await act(async () => { await notifications(); await notifications() }) // Exactly TWO held barriers again.
    expect(failedReadOutcome.completed).toBe(false); expect(reads[5]?.outcome.completed).toBe(false)
    expect(failedCloseOutcome.completed).toBe(false); expect(failedDuplicateOutcome.completed).toBe(false)
    expect(store.getSnapshot(A)).toBe(heldFailure); expect(store.retain(A)).toBe(t2.sessionId)
    const expectedPorts = [...successPorts, { port: 'GET', phase: 'failed-read' }]
    expect(portCalls).toEqual(expectedPorts); globals(store, d1, 2); stable(owner, bridge, binding)
    // Boundary/read/close rejection handlers are installed BEFORE this one rejection.
    await act(async () => {
      heldFailedRead.reject(boundaryFailure)
      const idle = store.whenIdle(); const idleOutcome = observe(idle)
      await failedReadOutcome.settled; await failedCloseOutcome.settled; await failedDuplicateOutcome.settled
      await idle
      expect(idleOutcome).toMatchObject({ completed: true, rejected: false })
      await drain()
    })
    const failedBoundary = required(reads[5]?.outcome ?? null, 'failed boundary witness')
    rejection(failedBoundary, boundaryFailure.message); expect(failedBoundary.failure).toBe(boundaryFailure)
    rejection(failedReadOutcome, 'Dashboard layout refresh did not establish a current writable target')
    rejection(failedCloseOutcome, 'Dashboard page close target is not current')
    rejection(failedDuplicateOutcome, 'Dashboard page close target is not current')
    expect(failedReadOutcome.failure).not.toBe(boundaryFailure)
    expect(failedCloseOutcome.failure).not.toBe(failedReadOutcome.failure)
    expect(failedDuplicateOutcome.failure).toBe(failedCloseOutcome.failure)
    const residue = store.getSnapshot(A)
    expect(residue).toMatchObject({ state: 'editing', pending: false, readPending: false, writable: false,
      provenance: 'saved', openedBaseline: d1, draft: d2, layout: d1 })
    expect(store.retain(A)).toBeNull(); globals(store, d1, 2)
    expect(() => binding.open()).toThrow(new Error('Dashboard layout session cannot open'))
    const oldCompletion = binding.close(t1, true); const oldOutcome = observe(oldCompletion)
    expect(oldCompletion).toBe(successClose) // Completed-ticket idempotence ONLY.
    await act(async () => { await oldCompletion; await drain() })
    expect(oldOutcome).toMatchObject({ completed: true, rejected: false })
    expect(store.getSnapshot(A)).toBe(residue); expect(tickets).toEqual([t1, t2])
    expect(portCalls).toEqual(expectedPorts); expect(requests).toHaveLength(2); expect(reads).toHaveLength(6)
    stable(owner, bridge, binding); cleanEvidence()
    const residuePublications = publications.length
    phase = 'remove'
    act(() => { required(state.show, 'remove Page')(false); pageMounted = false })
    await act(async () => { await drain() })
    expect(life).toEqual(['setup', 'cleanup']); expect(bindings.every((heldBinding) => heldBinding.released)).toBe(true)
    expect(store.getSnapshot(A)).toBe(residue); expect(publications).toHaveLength(residuePublications)
    expect(portCalls).toEqual(expectedPorts); globals(store, d1, 2); stable(owner, bridge, null); cleanEvidence()
    phase = 'root'
    act(() => { required(unmount, 'root teardown')(); rootUnmounted = true })
    await act(async () => { await drain() })
    ;[A, B].forEach((id) => {
      expect(store.getSnapshot(id)).toMatchObject({ state: 'closed', writable: false, pending: false,
        readPending: false, openedBaseline: null, draft: null })
      expect(store.getSnapshot(id).overlay).toStrictEqual({})
      expect(store.getSnapshot(id).counters).toStrictEqual(residue.counters)
    })
    expect(getDashboardAuthSnapshot(client)).toStrictEqual({ generation: bridge.generation, revision: bridge.revision + 1 })
    expect(bridgeRecords).toEqual([getDashboardAuthSnapshot(client)])
    expect(portCalls).toEqual(expectedPorts); expect(requests).toHaveLength(2)
    expect(reads.every((call) => call.outcome.completed)).toBe(true)
    expect(reads.filter((call) => call.outcome.rejected)).toEqual([reads[5]])
    cleanEvidence()
  } catch (failure: unknown) {
    primary = { phase, failure }
  } finally {
    phase = 'cleanup'
    // Resolve every controlled input even after a premature premise failure; no new rejection.
    restoration.resolve(seed); baselineRead.resolve(baselineUser); savedPut.resolve(savedReply)
    savedGet.resolve(savedPostUser); heldSuccessRead.resolve(refreshedUser)
    dialogPut.resolve(dialogReply); dialogGet.resolve(dialogPostUser); heldFailedRead.resolve(dialogPostUser)
    await cleanupStep('remove actual Page', async () => {
      await act(async () => {
        if (pageMounted && !rootUnmounted && state.show !== null) { state.show(false); pageMounted = false }
        await notifications()
      })
    })
    for (const heldBinding of bindings) {
      await cleanupStep('release captured binding', () => { heldBinding.binding.release(); heldBinding.released = true })
    }
    await cleanupStep('pre-root native/helper/store/tasks', async () => { await act(async () => { await drain() }) })
    await cleanupStep('root teardown', () => {
      if (!rootUnmounted && unmount !== null) { unmount(); rootUnmounted = true }
    })
    await cleanupStep('post-root native/helper/store/tasks', async () => { await act(async () => { await drain() }) })
    await cleanupStep('failure evidence', () => { cleanEvidence(); expect(bindings.every((heldBinding) => heldBinding.released)).toBe(true) })
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
      `Pending-read tracer ${primary === null ? 'cleanup failure' : `body failure in ${primary.phase}`}; cleanup steps: ${cleanupDiagnostics.map((entry) => entry.step).join(', ')}`,
      primary === null ? undefined : { cause: primary.failure })
  }
})
