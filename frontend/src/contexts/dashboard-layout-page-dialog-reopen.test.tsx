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
import { getPageSessionLease, type PageBinding, type PageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import { auth, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout, toPersistedLayout, toggleItem, validateCommittedDashboardLayout,
  type DashboardLayout, type DashboardRequestBinding, type CommittedDashboardLayout,
} from '@/lib/dashboard-layout'
import type { SessionStore, SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

// Replace transport boundaries only; all lifecycle authority remains real.
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
const TOKEN = 'synthetic-idle-reopen-token'
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

it('keeps an idle page binding inert and explicitly saves two dialogs on that same binding without old-ticket interference', async () => {
  const defaults = freezeFixture(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, W))
  const firstReply: CommittedDashboardLayout = freezeFixture({ workspaceId: W, layout: {
    version: 1, workspaceId: W, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } })
  const secondReply: CommittedDashboardLayout = freezeFixture({ workspaceId: W, layout: {
    version: 1, workspaceId: W, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } })
  const sibling = freezeFixture(toPersistedLayout(SIBLING,
    toggleItem(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, SIBLING).items,
      'hero-balance', false), DASHBOARD_WIDGETS))
  function userWith(layout: DashboardLayout): User {
    return freezeFixture({ id: ACTOR, email: 'idle-reopen@example.invalid',
      is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
      preferences: { language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic idle reopen',
        dashboard_layouts: { [W]: layout, [SIBLING]: sibling } },
    })
  }
  const seedLayout = freezeFixture(toPersistedLayout(W,
    toggleItem(defaults.items, 'period-transactions', false), DASHBOARD_WIDGETS))
  const seed = userWith(seedLayout)
  const baselineUser = userWith(defaults)
  // Each post-GET leaf is independently literal, not echoed from a PUT or reply.
  const firstPostUser = userWith({ version: 1, workspaceId: W, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const secondPostUser = userWith({ version: 1, workspaceId: W, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const workspace: Workspace = freezeFixture({
    id: W, name: 'Idle reopen', kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
    icon: null, color: null, created_at: '2026-10-04T00:00:00Z',
    created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
    enabled_modules: ['accounts', 'transactions', 'goals'],
  })
  const fixtures = [seed, baselineUser, firstPostUser, secondPostUser,
    firstReply, secondReply, defaults, seedLayout, sibling, workspace]
  const preimages = structuredClone(fixtures)
  const users = [seed, baselineUser, firstPostUser, secondPostUser]
  const preferences = users.map((user) => user.preferences)
  const restoration = deferred<User>()
  const baselineRead = deferred<User>()
  const firstPut = deferred<CommittedDashboardLayout>()
  const secondPut = deferred<CommittedDashboardLayout>()
  const firstGet = deferred<User>()
  const secondGet = deferred<User>()
  const errors: unknown[] = []
  const tasks: Promise<void>[] = []
  function track<T>(task: Promise<T>): Promise<T> {
    tasks.push(task.then(() => {}, (failure: unknown) => { errors.push(failure) }))
    return task
  }
  function record(callback: () => void): void {
    try { callback() } catch (failure: unknown) { errors.push(failure) }
  }
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
  } })
  type Phase = 'restore' | 'refresh' | 'insert' | 'first' | 'reopen' | 'old' | 'second' | 'remove' | 'root' | 'cleanup'
  let phase: Phase = 'restore'
  const portCalls: Array<{ port: string; phase: Phase }> = []
  const reads: Array<{ phase: Phase; user: User | null; completed: boolean }> = []
  const requests: Array<{ phase: Phase; id: string; layout: DashboardLayout;
    current: boolean; generation: number; binding: DashboardRequestBinding }> = []
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
    // Capture actual transport guard/target before any assertions outside this callback.
    const call = { phase, id, layout: structuredClone(layout), current: false,
      generation: getDashboardAuthSnapshot(client).generation, binding }
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
  const warn = vi.spyOn(console, 'warn') // Passthrough.
  const error = vi.spyOn(console, 'error') // Passthrough.
  const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
  const contexts = new WeakSet<object>()
  const owners = new Set<DashboardLayoutOwner>()
  const stores = new Set<SessionStore>()
  const helpers = new Set<PageSessionLease>()
  const bindings: Array<{ binding: PageBinding; released: boolean }> = []
  const unsubscribes: Array<() => void> = []
  const publications: SessionSnapshot[] = []
  const bridgeRecords: DashboardAuthSnapshot[] = []
  type Witness = {
    owner: DashboardLayoutOwner | null; client: QueryClient; user: User | null
    token: string | null; loading: boolean; workspace: Workspace | null
    bridge: DashboardAuthSnapshot; storedToken: string | null; storedWorkspace: string | null
  }
  const captures: Witness[] = []
  const life: string[] = []
  const state: { owner: DashboardLayoutOwner | null; auth: ReturnType<typeof useAuth> | null;
    show: ((visible: boolean) => void) | null; helper: PageSessionLease | null } = {
    owner: null, auth: null, show: null, helper: null,
  }
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
          record(() => { publications.push(owner.store.getSnapshot(W)) })
        }))
      }
    }
    return { owner, client: queryClient, user: context.user, token: context.token,
      loading: context.isLoading, workspace: current, bridge: getDashboardAuthSnapshot(queryClient),
      storedToken: localStorage.getItem('token'), storedWorkspace: localStorage.getItem(WORKSPACE_STORAGE_KEY) }
  }
  function ObserveAuth({ children }: { children: ReactNode }) {
    const context = useAuth()
    watchAuth(context)
    useLayoutEffect(() => { state.auth = context })
    return children
  }
  function OwnerProbe() {
    const owner = useDashboardLayoutOwner()
    witness(owner, useAuth(), useWorkspace().current, useQueryClient())
    useLayoutEffect(() => { state.owner = owner })
    return null
  }
  function Page() {
    const owner = useDashboardLayoutOwner()
    const context = useAuth()
    const current = useWorkspace().current
    const queryClient = useQueryClient()
    captures.push(witness(owner, context, current, queryClient))
    const [mount] = useState(() => () => {
      const binding = required(state.helper, 'helper before insertion').bind(W)
      const held = { binding, released: false }
      bindings.push(held)
      life.push('setup')
      captures.push(witness(owner, context, current, queryClient))
      return () => {
        life.push('cleanup')
        binding.release(); held.released = true
      }
    })
    useEffect(mount, [mount]) // Mount-only bind/release; no dialog intent or promise.
    return null
  }
  function PagePlacement() {
    const [show, setShow] = useState(false)
    useLayoutEffect(() => { state.show = setShow }, [])
    return show ? <Page /> : null
  }
  function Shell() {
    return <QueryClientProvider client={client}><AuthProvider><ObserveAuth>
      <WorkspaceContext.Provider value={{ current: workspace, workspaces: [workspace],
        isLoading: false, switchWorkspace: async () => {}, refresh: async () => {},
        role: workspace.role, canManage: true, canWrite: true,
        enabledModules: ['accounts', 'transactions', 'goals'],
        hasModule: (id) => workspace.enabled_modules.includes(id) }}>
        <DashboardLayoutProvider><OwnerProbe /><PagePlacement /></DashboardLayoutProvider>
      </WorkspaceContext.Provider>
    </ObserveAuth></AuthProvider></QueryClientProvider>
  }
  async function settle(work: Promise<unknown>[]) {
    const results = await Promise.allSettled(work)
    results.forEach((result) => { if (result.status === 'rejected') errors.push(result.reason) })
  }
  async function drain() {
    for (let turn = 0; turn < 8; turn += 1) {
      const before = [tasks.length, publications.length, bridgeRecords.length,
        requests.length, reads.length, life.length, stores.size]
      await Promise.all(tasks)
      await notifications(); await notifications()
      await settle([...helpers].map((helper) => track(helper.whenIdle())))
      await settle([...stores].map((store) => store.whenIdle()))
      await notifications(); await notifications()
      // Drain tasks added during this turn; helper barrier tasks are excluded from growth.
      await Promise.all(tasks)
      const after = [tasks.length - helpers.size, publications.length, bridgeRecords.length,
        requests.length, reads.length, life.length, stores.size]
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
  function stable(owner: DashboardLayoutOwner, bridge: DashboardAuthSnapshot, binding: PageBinding | null) {
    expect(state.owner).toBe(owner); expect([...owners]).toEqual([owner]); expect([...stores]).toEqual([owner.store])
    expect(owner.actorId).toBe(ACTOR); expect(owner.authGeneration).toBe(bridge.generation)
    expect(getDashboardAuthSnapshot(client)).toBe(bridge); expect(bridgeRecords).toEqual([])
    const context = required(state.auth, 'Auth')
    expect(context.user).toBe(seed); expect(context.token).toBe(TOKEN); expect(context.isLoading).toBe(false)
    expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(W)
    captures.forEach((entry) => {
      expect(entry.owner).toBe(owner); expect(entry.client).toBe(client); expect(entry.workspace).toBe(workspace)
      expect(entry.user).toBe(seed); expect(entry.token).toBe(TOKEN); expect(entry.loading).toBe(false)
      expect(entry.bridge).toBe(bridge); expect(entry.storedToken).toBe(TOKEN); expect(entry.storedWorkspace).toBe(W)
    })
    if (binding !== null) {
      expect(bindings).toHaveLength(1); expect(bindings[0]?.binding).toBe(binding)
      expect(bindings[0]?.released).toBe(false); expect(life).toEqual(['setup'])
    }
  }
  function saved(store: SessionStore, layout: DashboardLayout, success: number) {
    expect(store.retain(W)).toBeNull()
    expect(store.getSnapshot(W)).toMatchObject({ state: 'closed-success', writable: true, provenance: 'saved',
      pending: false, readPending: false, openedBaseline: null, draft: null })
    expect(store.getSnapshot(W).layout).toStrictEqual(layout)
    expect(store.getSnapshot(W).overlay).toStrictEqual({ [W]: layout })
    expect(store.getSnapshot(W).counters).toStrictEqual({ success, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
    expect(store.getSnapshot(SIBLING).layout).toStrictEqual(sibling)
  }
  const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let unmount: (() => void) | null = null
  let rootUnmounted = false
  let pageMounted = false
  let unsubscribeBridge = () => {}
  const onAsyncError = (event: ErrorEvent) => { errors.push(event.error ?? event.message) }
  const onRejection = (event: PromiseRejectionEvent) => { errors.push(event.reason) }
  window.addEventListener('error', onAsyncError); window.addEventListener('unhandledrejection', onRejection)
  try {
    localStorage.setItem('token', TOKEN); localStorage.setItem(WORKSPACE_STORAGE_KEY, W)
    unmount = render(<Shell />, { reactStrictMode: false }).unmount
    await act(async () => { restoration.resolve(seed); await drain() })
    await act(async () => { await drain() })
    const owner = required(state.owner, 'established owner')
    const store = owner.store
    const bridge = getDashboardAuthSnapshot(client)
    unsubscribeBridge = subscribeDashboardAuth(client, () => {
      record(() => { bridgeRecords.push(getDashboardAuthSnapshot(client)) })
    })
    stable(owner, bridge, null)
    expect(reads).toEqual([{ phase: 'restore', user: seed, completed: true }])
    expect(bindings).toEqual([]); expect(life).toEqual([]); expect(requests).toEqual([])
    phase = 'refresh'
    await act(async () => { const read = track(store.refresh(W)); baselineRead.resolve(baselineUser); await read; await drain() })
    const idle = store.getSnapshot(W)
    expect(idle).toMatchObject({ writable: true, state: 'closed', pending: false, readPending: false,
      openedBaseline: null, draft: null, overlay: {}, counters: { success: 0, suppressed: 0 } })
    expect(idle.layout).toStrictEqual(defaults); expect(store.retain(W)).toBeNull()
    expect(store.getSnapshot(SIBLING).layout).toStrictEqual(sibling)
    expect(validateCommittedDashboardLayout(W, firstReply, DASHBOARD_WIDGETS)).toStrictEqual(firstReply)
    expect(validateCommittedDashboardLayout(W, secondReply, DASHBOARD_WIDGETS)).toStrictEqual(secondReply)
    expect(firstPostUser.preferences.dashboard_layouts?.[W]).toStrictEqual(firstReply.layout)
    expect(secondPostUser.preferences.dashboard_layouts?.[W]).toStrictEqual(secondReply.layout)
    const helper = getPageSessionLease(store)
    helpers.add(helper); state.helper = helper
    const idlePublications = publications.length
    phase = 'insert'
    act(() => { required(state.show, 'insert page')(true); pageMounted = true })
    await act(async () => { await drain() })
    const binding = required(bindings[0]?.binding ?? null, 'issued binding')
    stable(owner, bridge, binding)
    expect(store.retain(W)).toBeNull(); expect(store.getSnapshot(W)).toBe(idle)
    expect(publications).toHaveLength(idlePublications); expect(requests).toEqual([])
    expect(portCalls).toEqual([{ port: 'GET', phase: 'restore' }, { port: 'GET', phase: 'refresh' }])
    cleanEvidence()
    phase = 'first'
    const firstTicket = binding.open()
    expect(Object.isFrozen(firstTicket)).toBe(true)
    expect(firstTicket).toStrictEqual({ workspaceId: W, sessionId: store.retain(W) })
    expect(store.getSnapshot(W)).toMatchObject({ state: 'editing', openedBaseline: defaults, draft: defaults })
    const firstDraft = toPersistedLayout(W, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
    expect(firstDraft).not.toEqual(defaults); expect(firstDraft).toStrictEqual(firstReply.layout)
    store.setDraft(firstTicket.sessionId, firstDraft.items)
    const firstClose = track(binding.close(firstTicket, true))
    const firstDuplicate = track(binding.close(firstTicket, true))
    expect(firstDuplicate).toBe(firstClose)
    await act(async () => { await notifications(); await notifications() })
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ phase: 'first', id: W, layout: firstReply.layout, current: true, generation: bridge.generation })
    expect(requests[0]?.binding.isCurrent()).toBe(true)
    const pending = store.getSnapshot(W)
    expect(pending).toMatchObject({ state: 'pending', pending: true, openedBaseline: defaults, draft: firstDraft })
    expect(store.retain(W)).toBe(firstTicket.sessionId)
    expect(() => binding.open()).toThrow('Dashboard page close is pending')
    expect(store.getSnapshot(W)).toBe(pending); expect(store.retain(W)).toBe(firstTicket.sessionId)
    expect(reads).toHaveLength(2); expect(requests).toHaveLength(1)
    // Never wait for helper/store idle while a controlled transport input is held.
    await act(async () => { firstPut.resolve(firstReply); firstGet.resolve(firstPostUser); await firstClose; await drain() })
    saved(store, firstReply.layout, 1); stable(owner, bridge, binding); cleanEvidence()
    expect(reads[2]).toEqual({ phase: 'first', user: firstPostUser, completed: true })
    expect(requests).toHaveLength(1)
    phase = 'reopen'
    const firstSaved = store.getSnapshot(W)
    const secondTicket = binding.open()
    expect(Object.isFrozen(secondTicket)).toBe(true); expect(secondTicket).not.toBe(firstTicket)
    expect(secondTicket.sessionId).not.toBe(firstTicket.sessionId)
    expect(secondTicket).toStrictEqual({ workspaceId: W, sessionId: store.retain(W) })
    expect(store.getSnapshot(W)).toMatchObject({ state: 'editing', openedBaseline: firstReply.layout, draft: firstReply.layout })
    expect(store.getSnapshot(W).overlay).toStrictEqual(firstSaved.overlay)
    const secondDraft = toPersistedLayout(W, toggleItem(store.getSnapshot(W).layout.items, 'spending-charts', false), DASHBOARD_WIDGETS)
    expect(secondDraft).not.toEqual(firstReply.layout); expect(secondDraft).toStrictEqual(secondReply.layout)
    store.setDraft(secondTicket.sessionId, secondDraft.items)
    await act(async () => { await drain() })
    const reopened = store.getSnapshot(W)
    const oldPublications = publications.length
    phase = 'old'
    const oldClose = track(binding.close(firstTicket, true))
    expect(oldClose).toBe(firstClose)
    await act(async () => { await oldClose; await notifications(); await drain() })
    expect(store.retain(W)).toBe(secondTicket.sessionId); expect(store.getSnapshot(W)).toBe(reopened)
    expect(reopened.openedBaseline).toStrictEqual(firstReply.layout); expect(reopened.draft).toStrictEqual(secondDraft)
    expect(reopened.overlay).toStrictEqual(firstSaved.overlay)
    expect(publications).toHaveLength(oldPublications); expect(requests).toHaveLength(1); expect(reads).toHaveLength(3)
    stable(owner, bridge, binding); cleanEvidence()
    phase = 'second'
    const secondClose = track(binding.close(secondTicket, true))
    await act(async () => { await notifications(); await notifications() })
    expect(requests).toHaveLength(2)
    expect(requests[1]).toMatchObject({ phase: 'second', id: W, layout: secondReply.layout, current: true, generation: bridge.generation })
    expect(requests[1]?.binding.isCurrent()).toBe(true)
    expect(store.getSnapshot(W)).toMatchObject({ state: 'pending', pending: true, draft: secondDraft })
    expect(store.retain(W)).toBe(secondTicket.sessionId)
    await act(async () => { secondPut.resolve(secondReply); secondGet.resolve(secondPostUser); await secondClose; await drain() })
    saved(store, secondReply.layout, 2); stable(owner, bridge, binding); cleanEvidence()
    const expectedPorts = [{ port: 'GET', phase: 'restore' }, { port: 'GET', phase: 'refresh' },
      { port: 'PUT', phase: 'first' }, { port: 'GET', phase: 'first' },
      { port: 'PUT', phase: 'second' }, { port: 'GET', phase: 'second' }]
    expect(portCalls).toEqual(expectedPorts)
    expect(reads[3]).toEqual({ phase: 'second', user: secondPostUser, completed: true })
    const finalSaved = store.getSnapshot(W)
    const finalPublications = publications.length
    await act(async () => { await notifications(); await drain() })
    expect(store.getSnapshot(W)).toBe(finalSaved); expect(portCalls).toEqual(expectedPorts)
    expect(publications).toHaveLength(finalPublications)
    phase = 'remove'
    act(() => { required(state.show, 'remove page')(false); pageMounted = false })
    await act(async () => { await drain() })
    stable(owner, bridge, null)
    expect(life).toEqual(['setup', 'cleanup']); expect(bindings).toHaveLength(1)
    expect(bindings[0]?.released).toBe(true); expect(store.getSnapshot(W)).toBe(finalSaved)
    expect(publications).toHaveLength(finalPublications); expect(portCalls).toEqual(expectedPorts)
    saved(store, secondReply.layout, 2); cleanEvidence()
    phase = 'root'
    act(() => { required(unmount, 'root teardown')(); rootUnmounted = true })
    await act(async () => { await drain() })
    expect(store.getSnapshot(W)).toMatchObject({ writable: false, pending: false, readPending: false,
      openedBaseline: null, draft: null })
    expect(store.getSnapshot(W).overlay).toStrictEqual({})
    expect(store.getSnapshot(W).counters.success).toBe(2)
    expect(getDashboardAuthSnapshot(client)).toStrictEqual({ generation: bridge.generation, revision: bridge.revision + 1 })
    expect(bridgeRecords).toEqual([getDashboardAuthSnapshot(client)])
    expect(portCalls).toEqual(expectedPorts); expect(requests).toHaveLength(2)
    expect(reads.every((call) => call.completed)).toBe(true); cleanEvidence()
  } finally {
    phase = 'cleanup'
    restoration.resolve(seed); baselineRead.resolve(baselineUser)
    firstPut.resolve(firstReply); secondPut.resolve(secondReply)
    firstGet.resolve(firstPostUser); secondGet.resolve(secondPostUser)
    try {
      try {
        await act(async () => {
          if (pageMounted && !rootUnmounted && state.show !== null) { state.show(false); pageMounted = false }
          await notifications()
        })
        await act(async () => {
          bindings.forEach((held) => { held.binding.release(); held.released = true })
          await drain()
        })
      } finally {
        await act(async () => {
          if (!rootUnmounted && unmount !== null) { unmount(); rootUnmounted = true }
          await drain()
        })
      }
      cleanEvidence()
    } finally {
      unsubscribeBridge(); unsubscribes.forEach((unsubscribe) => { unsubscribe() }); client.clear()
      try { await notifications(); await drain(); cleanEvidence() } finally {
        window.removeEventListener('error', onAsyncError); window.removeEventListener('unhandledrejection', onRejection)
        vi.restoreAllMocks()
        storageBefore.forEach(({ key, value }) => {
          if (value === null) localStorage.removeItem(key)
          else localStorage.setItem(key, value)
        })
      }
    }
  }
})
