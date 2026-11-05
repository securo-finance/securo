import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import { act, render } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, useWorkspace } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
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

// Transport boundaries only. The proposed helper intentionally does not yet exist.
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
const TOKEN = 'synthetic-page-binding-token'
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
// Session publications and bridge/helper release use native microtasks.
function notifications(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('preserves a preestablished saved overlay and unsent edit through ordinary then full-root Strict page binding, and saves on actual page removal', async () => {
  async function scenario(strict: boolean) {
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
      return freezeFixture({ id: ACTOR, email: 'page-binding@example.invalid',
        is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
        preferences: { language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic page binding',
          dashboard_layouts: { [W]: layout, [SIBLING]: sibling } },
      })
    }
    const seedLayout = freezeFixture(toPersistedLayout(W,
      toggleItem(defaults.items, 'period-transactions', false), DASHBOARD_WIDGETS))
    const seed = userWith(seedLayout)
    const baselineUser = userWith(defaults)
    // Independently literal post-GET leaves, not copies of requests or replies.
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
    const fixtures = [seed, baselineUser, firstPostUser, secondPostUser,
      firstReply, secondReply, defaults, seedLayout, sibling]
    const preimages = structuredClone(fixtures)
    const users = [seed, baselineUser, firstPostUser, secondPostUser]
    const preferences = users.map((user) => user.preferences)
    const restoration = deferred<User>()
    type Phase = 'restore' | 'refresh' | 'first-save' | 'prepare' | 'insert' | 'remove' | 'root' | 'cleanup'
    let phase: Phase = 'restore'
    let restoring = true
    let transportUser = baselineUser
    const errors: unknown[] = []
    const reads: Promise<User>[] = []
    const completions: Promise<void>[] = []
    const portCalls: Array<{ port: string; phase: Phase }> = []
    const readCalls: Array<{ phase: Phase; completed: boolean; user: User | null }> = []
    const requests: Array<{ phase: Phase; id: string; layout: DashboardLayout;
      current: boolean; generation: number }> = []
    function track(task: Promise<void>): Promise<void> {
      const handled = task.then(() => {}, (failure: unknown) => { errors.push(failure) })
      completions.push(handled)
      return handled
    }
    function observe<T>(task: Promise<T>): Promise<T> {
      track(task.then(() => {}))
      return task
    }
    const client = new QueryClient({ defaultOptions: {
      queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
    } })
    ports.me.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'GET', phase })
      const call: { phase: Phase; completed: boolean; user: User | null } = { phase, completed: false, user: null }
      const read = restoring ? restoration.promise : Promise.resolve(transportUser)
      readCalls.push(call); reads.push(read)
      track(read.then((user) => { call.completed = true; call.user = user }))
      return read
    })
    ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
      portCalls.push({ port: 'PUT', phase })
      requests.push({ phase, id, layout: structuredClone(layout), current: binding.isCurrent(),
        generation: getDashboardAuthSnapshot(client).generation })
      if (requests.length === 1) {
        transportUser = firstPostUser
        return observe(Promise.resolve(firstReply))
      }
      if (requests.length === 2) {
        // A deficient Strict replay PUT remains truthfully recorded as insert, not setup failure.
        transportUser = secondPostUser
        return observe(Promise.resolve(secondReply))
      }
      return observe(Promise.reject(new Error('Unexpected extra PUT')))
    })
    ports.updateMe.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'PATCH', phase })
      return observe(Promise.resolve(seed))
    })
    ports.login.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'login', phase })
      return observe(Promise.reject(new Error('Unexpected login')))
    })
    ports.verify2fa.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'verify2fa', phase })
      return observe(Promise.reject(new Error('Unexpected verify2fa')))
    })
    ports.register.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'register', phase })
      return observe(Promise.reject(new Error('Unexpected register')))
    })
    const updateMeSpy = vi.spyOn(auth, 'updateMe')
    const warn = vi.spyOn(console, 'warn') // Passthrough; no suppression.
    const error = vi.spyOn(console, 'error')
    const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
    const contexts = new WeakSet<object>()
    const stores = new Set<SessionStore>()
    const owners = new Set<DashboardLayoutOwner>()
    const helpers = new Set<PageSessionLease>()
    const bindings: Array<{ binding: PageBinding; released: boolean }> = []
    const unsubscribes: Array<() => void> = []
    const publications: Array<{ store: SessionStore; snapshot: SessionSnapshot }> = []
    const bridgeRecords: DashboardAuthSnapshot[] = []
    const workspace: Workspace = freezeFixture({
      id: W, name: 'Page binding', kind: 'personal', is_archived: false,
      timezone: null,
      default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
      icon: null, color: null, created_at: '2026-10-03T00:00:00Z',
      created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
      enabled_modules: ['accounts', 'transactions', 'goals'],
    })
    const workspaceBefore = structuredClone(workspace)
    type Witness = {
      owner: DashboardLayoutOwner | null; store: SessionStore | null; client: QueryClient
      user: User | null; token: string | null; loading: boolean; workspace: Workspace | null
      bridge: DashboardAuthSnapshot; storedToken: string | null; storedWorkspace: string | null
      snapshot: SessionSnapshot | null
    }
    const captures: Array<{ stage: 'render' | 'layout' | 'passive'; witness: Witness }> = []
    const life: Array<{ event: 'setup' | 'cleanup'; witness: Witness }> = []
    const state: { auth: ReturnType<typeof useAuth> | null; owner: DashboardLayoutOwner | null;
      show: ((visible: boolean) => void) | null; helper: PageSessionLease | null } = {
      auth: null, owner: null, show: null, helper: null,
    }
    function watchAuth(context: ReturnType<typeof useAuth>) {
      if (!contexts.has(context)) {
        contexts.add(context)
        writerSpies.push(vi.spyOn(context, 'updateUser'))
      }
    }
    function witness(owner: DashboardLayoutOwner | null, context: ReturnType<typeof useAuth>,
      current: Workspace | null, queryClient: QueryClient): Witness {
      watchAuth(context)
      // Track every real nonnull store before any test assertion, including surprises.
      if (owner) {
        owners.add(owner)
        if (!stores.has(owner.store)) {
          stores.add(owner.store)
          unsubscribes.push(owner.store.subscribe(() => {
            publications.push({ store: owner.store, snapshot: owner.store.getSnapshot(W) })
          }))
        }
      }
      return { owner, store: owner?.store ?? null, client: queryClient,
        user: context.user, token: context.token, loading: context.isLoading, workspace: current,
        bridge: getDashboardAuthSnapshot(queryClient),
        storedToken: localStorage.getItem('token'), storedWorkspace: localStorage.getItem(WORKSPACE_STORAGE_KEY),
        snapshot: owner?.store.getSnapshot(W) ?? null }
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
      useLayoutEffect(() => { state.owner = owner })
      return null
    }
    function Page() {
      const owner = useDashboardLayoutOwner()
      const context = useAuth()
      const current = useWorkspace().current
      const queryClient = useQueryClient()
      captures.push({ stage: 'render', witness: witness(owner, context, current, queryClient) })
      const [mount] = useState(() => () => {
        const entry = witness(owner, context, current, queryClient)
        captures.push({ stage: 'passive', witness: entry })
        const binding = required(state.helper, 'helper before insertion').bind(W)
        const held = { binding, released: false }
        bindings.push(held) // Before evidence/assertions; no external counted binding.
        life.push({ event: 'setup', witness: entry })
        return () => {
          life.push({ event: 'cleanup', witness: witness(owner, context, current, queryClient) })
          binding.release()
          held.released = true
        }
      })
      useLayoutEffect(() => {
        captures.push({ stage: 'layout', witness: witness(owner, context, current, queryClient) })
      }, [owner, context, current, queryClient])
      useEffect(mount, [mount]) // Mount-only bind/release; NEVER open/close/finalize.
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
    async function settle(tasks: Promise<unknown>[]) {
      const results = await Promise.allSettled(tasks)
      results.forEach((result) => { if (result.status === 'rejected') errors.push(result.reason) })
    }
    async function drain() {
      for (let turn = 0; turn < 8; turn += 1) {
        const before = [reads.length, completions.length, publications.length,
          bridgeRecords.length, requests.length, life.length, stores.size]
        await settle(reads)
        await Promise.all(completions)
        await notifications(); await notifications()
        await settle([...helpers].map((helper) => helper.whenIdle()))
        await settle([...stores].map((store) => store.whenIdle()))
        await notifications(); await notifications()
        const after = [reads.length, completions.length, publications.length,
          bridgeRecords.length, requests.length, life.length, stores.size]
        if (after.every((value, index) => value === before[index])) return
      }
      throw new Error('Work did not quiesce within eight native turns')
    }
    function cleanEvidence() {
      expect(errors).toEqual([])
      expect(warn.mock.calls).toEqual([])
      expect(error.mock.calls).toEqual([])
      expect(updateMeSpy).not.toHaveBeenCalled()
      expect(ports.updateMe).not.toHaveBeenCalled()
      expect(writerSpies.length).toBeGreaterThan(0)
      writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
      expect(portCalls.filter((call) => !['GET', 'PUT'].includes(call.port))).toEqual([])
      expect(fixtures).toStrictEqual(preimages)
      expect(workspace).toStrictEqual(workspaceBefore)
      users.forEach((user, index) => {
        expect(user.preferences).toBe(preferences[index])
        expect(Object.isFrozen(user.preferences)).toBe(true)
      })
    }
    function stable(owner: DashboardLayoutOwner, bridge: DashboardAuthSnapshot) {
      expect(state.owner).toBe(owner)
      expect([...owners]).toEqual([owner])
      expect([...stores]).toEqual([owner.store])
      expect(owner.actorId).toBe(ACTOR)
      expect(owner.authGeneration).toBe(bridge.generation)
      expect(getDashboardAuthSnapshot(client)).toBe(bridge)
      expect(bridgeRecords).toEqual([])
      const context = required(state.auth, 'eligible Auth')
      expect(context.user).toBe(seed)
      expect(context.token).toBe(TOKEN)
      expect(context.isLoading).toBe(false)
      expect(localStorage.getItem('token')).toBe(TOKEN)
      expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(W)
      ;[...captures, ...life].forEach(({ witness: entry }) => {
        expect(entry.owner).toBe(owner)
        expect(entry.store).toBe(owner.store)
        expect(entry.client).toBe(client)
        expect(entry.user).toBe(seed)
        expect(entry.workspace).toBe(workspace)
        expect(entry.token).toBe(TOKEN)
        expect(entry.loading).toBe(false)
        expect(entry.bridge).toBe(bridge)
        expect(entry.storedToken).toBe(TOKEN)
        expect(entry.storedWorkspace).toBe(W)
        expect(entry.snapshot?.authGeneration).toBe(bridge.generation)
        expect(entry.snapshot?.workspaceId).toBe(W)
      })
    }
    const oneSuccess = { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 }
    const twoSuccess = { ...oneSuccess, success: 2 }
    const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
    let unmount: (() => void) | null = null
    let rootUnmounted = false
    let pageMounted = false
    let unsubscribeBridge = () => {}
    const onAsyncError = (event: ErrorEvent) => { errors.push(event.error ?? event.message) }
    const onRejection = (event: PromiseRejectionEvent) => { errors.push(event.reason) }
    window.addEventListener('error', onAsyncError)
    window.addEventListener('unhandledrejection', onRejection)
    try {
      localStorage.setItem('token', TOKEN)
      localStorage.setItem(WORKSPACE_STORAGE_KEY, W)
      unmount = render(<Shell />, { reactStrictMode: strict }).unmount
      await act(async () => { restoration.resolve(seed); await drain() })
      restoring = false
      // Finish provider initial replay/exposure before baseline; no page, timers or owner rescue.
      await act(async () => { await drain() })
      const owner = required(state.owner, 'established provider owner')
      const store = owner.store
      const bridge = getDashboardAuthSnapshot(client)
      const generation = bridge.generation
      unsubscribeBridge = subscribeDashboardAuth(client, () => { bridgeRecords.push(getDashboardAuthSnapshot(client)) })
      const restoreCount = strict ? 2 : 1
      const restoreCalls = Array.from({ length: restoreCount }, () => ({ phase: 'restore', completed: true, user: seed }))
      stable(owner, bridge)
      expect(readCalls).toEqual(restoreCalls)
      expect(captures).toEqual([])
      expect(life).toEqual([])
      expect(bindings).toEqual([])
      expect(requests).toEqual([])
      expect(store.getSnapshot(W).overlay).toStrictEqual({})
      expect(store.getSnapshot(W).writable).toBe(false)
      phase = 'refresh'
      await act(async () => { await track(store.refresh(W)); await drain() })
      expect(store.getSnapshot(W).layout).toStrictEqual(defaults)
      expect(store.getSnapshot(W).writable).toBe(true)
      expect(validateCommittedDashboardLayout(W, firstReply, DASHBOARD_WIDGETS)).toStrictEqual(firstReply)
      expect(validateCommittedDashboardLayout(W, secondReply, DASHBOARD_WIDGETS)).toStrictEqual(secondReply)
      expect(firstPostUser.preferences.dashboard_layouts?.[W]).toStrictEqual(firstReply.layout)
      expect(secondPostUser.preferences.dashboard_layouts?.[W]).toStrictEqual(secondReply.layout)
      const firstId = store.open(W)
      const changed = toPersistedLayout(W, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
      expect(changed).not.toEqual(defaults)
      expect(changed).toStrictEqual(firstReply.layout)
      store.setDraft(firstId, changed.items)
      phase = 'first-save'
      await act(async () => { await track(store.finalize(firstId)); await drain() })
      expect(requests).toEqual([{ phase: 'first-save', id: W, layout: firstReply.layout, current: true, generation }])
      expect(store.getSnapshot(W)).toMatchObject({ state: 'closed-success', provenance: 'saved',
        writable: true, pending: false, readPending: false, openedBaseline: null, draft: null })
      expect(store.getSnapshot(W).layout).toStrictEqual(firstReply.layout)
      expect(store.getSnapshot(W).overlay).toStrictEqual({ [W]: firstReply.layout })
      expect(store.getSnapshot(W).counters).toStrictEqual(oneSuccess)
      expect(store.getSnapshot(SIBLING).layout).toStrictEqual(sibling)
      stable(owner, bridge)
      cleanEvidence()
      phase = 'prepare'
      const editingId = store.open(W)
      expect(editingId).not.toBe(firstId)
      const unsent = toPersistedLayout(W, toggleItem(changed.items, 'spending-charts', false), DASHBOARD_WIDGETS)
      expect(unsent).not.toEqual(firstReply.layout)
      expect(unsent).toStrictEqual(secondReply.layout)
      store.setDraft(editingId, unsent.items)
      await act(async () => { await drain() })
      const editing = store.getSnapshot(W)
      const capturedEditing = structuredClone(editing)
      expect(editing).toMatchObject({ state: 'editing', openedBaseline: firstReply.layout, draft: unsent })
      expect(editing.overlay).toStrictEqual({ [W]: firstReply.layout })
      expect(editing.counters).toStrictEqual(oneSuccess)
      const publicationsBeforeHelper = publications.length
      const helper = getPageSessionLease(store)
      helpers.add(helper); state.helper = helper
      await act(async () => { await drain() })
      expect(bindings).toEqual([])
      expect(store.retain(W)).toBe(editingId)
      expect(store.getSnapshot(W)).toBe(editing)
      expect(publications).toHaveLength(publicationsBeforeHelper)
      const readsBeforePage = readCalls.length
      const requestsBeforePage = requests.length
      const expectedPrefix = [...Array.from({ length: restoreCount }, () => ({ port: 'GET', phase: 'restore' })),
        { port: 'GET', phase: 'refresh' }, { port: 'PUT', phase: 'first-save' }, { port: 'GET', phase: 'first-save' }]
      expect(portCalls).toEqual(expectedPrefix)
      expect(readCalls).toEqual([...restoreCalls, { phase: 'refresh', completed: true, user: baselineUser },
        { phase: 'first-save', completed: true, user: firstPostUser }])
      phase = 'insert'
      act(() => { pageMounted = true; required(state.show, 'page insertion')(true) })
      await act(async () => { await drain() })
      // Qualify real mounted replay and stable ownership before the FIRST preservation assertion.
      const replay = strict ? ['setup', 'cleanup', 'setup'] : ['setup']
      expect(life.map((entry) => entry.event)).toEqual(replay)
      expect(bindings).toHaveLength(strict ? 2 : 1)
      expect(bindings.filter((entry) => !entry.released)).toHaveLength(1)
      expect(new Set(captures.map((entry) => entry.stage))).toEqual(new Set(['render', 'layout', 'passive']))
      stable(owner, bridge)
      expect(store.retain(W)).toBe(editingId)
      expect(store.getSnapshot(W)).toStrictEqual(capturedEditing)
      expect(store.getSnapshot(W).openedBaseline).toStrictEqual(firstReply.layout)
      expect(store.getSnapshot(W).draft).toStrictEqual(unsent)
      expect(store.getSnapshot(W).layout).toStrictEqual(editing.layout)
      expect(store.getSnapshot(W).overlay).toStrictEqual(editing.overlay)
      expect(store.getSnapshot(W).counters).toStrictEqual(oneSuccess)
      expect(requests).toHaveLength(requestsBeforePage)
      expect(readCalls).toHaveLength(readsBeforePage)
      expect(portCalls).toEqual(expectedPrefix)
      cleanEvidence()
      phase = 'remove'
      act(() => { required(state.show, 'page removal')(false); pageMounted = false })
      await act(async () => { await drain() })
      expect(life.map((entry) => entry.event)).toEqual([...replay, 'cleanup'])
      expect(bindings.every((entry) => entry.released)).toBe(true)
      stable(owner, bridge)
      expect(requests).toEqual([
        { phase: 'first-save', id: W, layout: firstReply.layout, current: true, generation },
        { phase: 'remove', id: W, layout: secondReply.layout, current: true, generation },
      ])
      const expectedPorts = [...expectedPrefix, { port: 'PUT', phase: 'remove' }, { port: 'GET', phase: 'remove' }]
      expect(portCalls).toEqual(expectedPorts)
      expect(readCalls).toEqual([...restoreCalls, { phase: 'refresh', completed: true, user: baselineUser },
        { phase: 'first-save', completed: true, user: firstPostUser },
        { phase: 'remove', completed: true, user: secondPostUser }])
      expect(store.retain(W)).toBeNull()
      expect(store.getSnapshot(W)).toMatchObject({ state: 'closed-success', provenance: 'saved',
        writable: true, pending: false, readPending: false, openedBaseline: null, draft: null })
      expect(store.getSnapshot(W).layout).toStrictEqual(secondReply.layout)
      expect(store.getSnapshot(W).overlay).toStrictEqual({ [W]: secondReply.layout })
      expect(store.getSnapshot(W).counters).toStrictEqual(twoSuccess)
      expect(store.getSnapshot(SIBLING).layout).toStrictEqual(sibling)
      const finalSaved = store.getSnapshot(W)
      await act(async () => { await notifications(); await drain() })
      expect(store.getSnapshot(W)).toBe(finalSaved)
      expect(portCalls).toEqual(expectedPorts)
      expect(requests).toHaveLength(2)
      stable(owner, bridge)
      cleanEvidence()
      // Actual full-root teardown is distinct from page cleanup and happens only afterwards.
      phase = 'root'
      act(() => { required(unmount, 'root unmount')(); rootUnmounted = true })
      await act(async () => { await drain() })
      expect(life.map((entry) => entry.event)).toEqual([...replay, 'cleanup'])
      expect(bindings.every((entry) => entry.released)).toBe(true)
      expect(getDashboardAuthSnapshot(client).generation).toBe(generation)
      expect(getDashboardAuthSnapshot(client).revision).toBe(bridge.revision + 1)
      expect(bridgeRecords).toEqual([getDashboardAuthSnapshot(client)])
      expect(store.getSnapshot(W).writable).toBe(false)
      expect(store.getSnapshot(W).overlay).toStrictEqual({})
      expect(store.getSnapshot(W).draft).toBeNull()
      expect(portCalls).toEqual(expectedPorts)
      expect(readCalls.every((call) => call.completed)).toBe(true)
      cleanEvidence()
    } finally {
      // Failure cleanup never manufactures checkpoint 6: remove the real page while owner lives.
      phase = 'cleanup'; restoration.resolve(seed)
      try {
        await act(async () => {
          try {
            if (pageMounted && !rootUnmounted && state.show !== null) {
              state.show(false); pageMounted = false
            }
          } finally {
            await notifications()
          }
        })
        await act(async () => {
          try {
            bindings.forEach((held) => { held.binding.release(); held.released = true })
            await drain()
          } finally {
            if (!rootUnmounted && unmount !== null) { unmount(); rootUnmounted = true }
            await drain()
          }
        })
        cleanEvidence()
      } finally {
        unsubscribeBridge(); unsubscribes.forEach((unsubscribe) => { unsubscribe() }); client.clear()
        try { await notifications(); await drain(); cleanEvidence() } finally {
          window.removeEventListener('error', onAsyncError)
          window.removeEventListener('unhandledrejection', onRejection)
          vi.restoreAllMocks()
          storageBefore.forEach(({ key, value }) => {
            if (value === null) localStorage.removeItem(key)
            else localStorage.setItem(key, value)
          })
        }
      }
    }
  }
  await scenario(false)
  await scenario(true)
})
