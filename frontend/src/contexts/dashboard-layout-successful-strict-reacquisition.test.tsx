import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import { act, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, useWorkspace } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { getDashboardAuthSnapshot, subscribeDashboardAuth, type DashboardAuthSnapshot } from '@/lib/dashboard-layout-auth-bridge'
import { auth, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout, toPersistedLayout, toggleItem,
  type DashboardLayout, type DashboardRequestBinding, type CommittedDashboardLayout,
} from '@/lib/dashboard-layout'
import type { SessionStore, SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'
// Transport only: real Auth methods, factory, registration and guards.
const ports = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout,
    binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  login: vi.fn(), verify2fa: vi.fn(), register: vi.fn(),
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
const TOKEN = 'synthetic-child-precommit-token'
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
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) }); Object.freeze(value)
  }
  return value
}
function notifications(): Promise<void> { return new Promise((resolve) => { queueMicrotask(resolve) }) }
it('preserves the successfully registered owner through mounted root StrictMode lease reacquisition and saves', async () => {
  async function scenario(strict: boolean) {
    const defaults = freezeFixture(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, W))
    // Copied by value from child-precommit; independent literal reply, never a PUT echo.
    const saved: DashboardLayout = freezeFixture({ version: 1, workspaceId: W, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ] })
    const reply: CommittedDashboardLayout = freezeFixture({ workspaceId: W, layout: saved })
    const sibling = freezeFixture(toPersistedLayout(SIBLING,
      toggleItem(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, SIBLING).items,
        'hero-balance', false), DASHBOARD_WIDGETS))
    function userWith(layout: DashboardLayout): User {
      return freezeFixture({ id: ACTOR, email: 'child-precommit@example.invalid',
        is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
        preferences: { language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic child',
          dashboard_layouts: { [W]: layout, [SIBLING]: sibling } },
      })
    }
    const seedLayout = freezeFixture(toPersistedLayout(W,
      toggleItem(defaults.items, 'period-transactions', false), DASHBOARD_WIDGETS))
    const seed = userWith(seedLayout)
    const baselineUser = userWith(defaults)
    const postUser = userWith(saved)
    const fixtures = [seed, baselineUser, postUser, reply, sibling, defaults, seedLayout]
    const preimages = structuredClone(fixtures)
    const preferences = [seed.preferences, baselineUser.preferences, postUser.preferences]
    const restoration = deferred<User>()
    type Phase = 'restore' | 'mount' | 'recovery' | 'refresh' | 'save' | 'cleanup'
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
    function observe<T>(task: Promise<T>): Promise<T> { track(task.then(() => {})); return task }
    ports.me.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'GET', phase })
      const call: { phase: Phase; completed: boolean; user: User | null } = { phase, completed: false, user: null }
      const read = restoring ? restoration.promise : Promise.resolve(transportUser)
      readCalls.push(call); reads.push(read)
      track(read.then((user) => { call.completed = true; call.user = user }))
      return read
    })
    const client = new QueryClient({ defaultOptions: {
      queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
    } })
    ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
      portCalls.push({ port: 'PUT', phase })
      requests.push({ phase, id, layout: structuredClone(layout), current: binding.isCurrent(),
        generation: getDashboardAuthSnapshot(client).generation })
      transportUser = postUser
      return observe(Promise.resolve(reply))
    })
    ports.updateMe.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'PATCH', phase }); return observe(Promise.resolve(seed))
    })
    ports.login.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'login', phase }); return observe(Promise.resolve({ access_token: TOKEN }))
    })
    ports.verify2fa.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'verify2fa', phase }); return observe(Promise.resolve({ access_token: TOKEN }))
    })
    ports.register.mockReset().mockImplementation(() => {
      portCalls.push({ port: 'register', phase }); return observe(Promise.resolve())
    })
    const updateMeSpy = vi.spyOn(auth, 'updateMe')
    const warn = vi.spyOn(console, 'warn') // Passthrough, including actual teardown.
    const error = vi.spyOn(console, 'error')
    const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
    const contexts = new WeakSet<object>()
    const stores = new Set<SessionStore>()
    const owners = new Set<DashboardLayoutOwner>()
    const unsubscribes: Array<() => void> = []
    const publications: Array<{ store: SessionStore; snapshot: SessionSnapshot }> = []
    const bridgeRecords: DashboardAuthSnapshot[] = []
    const workspace: Workspace = freezeFixture({
      id: W, name: 'Child precommit', kind: 'personal', is_archived: false,
      timezone: null,
      default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
      icon: null, color: null, created_at: '2026-10-03T00:00:00Z',
      created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
      enabled_modules: ['accounts', 'transactions', 'goals'],
    })
    const workspaceBefore = structuredClone(workspace)
    type Witness = {
      owner: DashboardLayoutOwner | null; user: User | null; token: string | null
      loading: boolean; workspace: Workspace | null; snapshot: DashboardAuthSnapshot
      storedToken: string | null; storedWorkspace: string | null; reads: number; puts: number
    }
    type Lifecycle = { event: 'setup' | 'cleanup'; witness: Witness }
    const childLife: Lifecycle[] = []
    const tailLife: Lifecycle[] = []
    const captures: Array<{ stage: 'render' | 'layout' | 'passive'; witness: Witness }> = []
    const state: { auth: ReturnType<typeof useAuth> | null; owner: DashboardLayoutOwner | null;
      show: (() => void) | null } = { auth: null, owner: null, show: null }
    function witness(owner: DashboardLayoutOwner | null,
      context: ReturnType<typeof useAuth>, current: Workspace | null): Witness {
      // Track every nonnull exposure before any assertion, including unexpected stores.
      if (owner) {
        owners.add(owner)
        if (!stores.has(owner.store)) {
          stores.add(owner.store)
          unsubscribes.push(owner.store.subscribe(() => {
            publications.push({ store: owner.store, snapshot: owner.store.getSnapshot(W) })
          }))
        }
      }
      return { owner, user: context.user, token: context.token, loading: context.isLoading,
        workspace: current, snapshot: getDashboardAuthSnapshot(client),
        storedToken: localStorage.getItem('token'), storedWorkspace: localStorage.getItem(WORKSPACE_STORAGE_KEY),
        reads: reads.length, puts: requests.length }
    }
    function ObserveAuth({ children }: { children: ReactNode }) {
      const context = useAuth()
      if (!contexts.has(context)) {
        contexts.add(context)
        writerSpies.push(vi.spyOn(context, 'updateUser')) // Every real exposed context, passthrough.
      }
      useLayoutEffect(() => { state.auth = context })
      return children
    }
    function Child() {
      const owner = useDashboardLayoutOwner()
      const context = useAuth()
      const current = useWorkspace().current
      // A stable mount callback captures the initial render, not later exposure dependencies.
      const [recordMount] = useState(() => (event: Lifecycle['event']) => {
        childLife.push({ event, witness: witness(owner, context, current) })
      })
      captures.push({ stage: 'render', witness: witness(owner, context, current) })
      useLayoutEffect(() => {
        state.owner = owner
        captures.push({ stage: 'layout', witness: witness(owner, context, current) })
      })
      useLayoutEffect(() => {
        recordMount('setup')
        return () => { recordMount('cleanup') }
      }, [recordMount])
      useEffect(() => { captures.push({ stage: 'passive', witness: witness(owner, context, current) }) })
      return null
    }
    function Tail() {
      const context = useAuth()
      const current = useWorkspace().current
      const [recordMount] = useState(() => (event: Lifecycle['event']) => {
        tailLife.push({ event, witness: witness(null, context, current) })
      })
      useLayoutEffect(() => {
        recordMount('setup')
        return () => { recordMount('cleanup') }
      }, [recordMount])
      return null
    }
    // ONE placement subtree: StrictMode replays Provider/Child and following Tail together.
    function Placement() { return <><DashboardLayoutProvider><Child /></DashboardLayoutProvider><Tail /></> }
    function Shell() {
      const [show, setShow] = useState(false)
      useLayoutEffect(() => { state.show = () => { setShow(true) } }, [])
      return <QueryClientProvider client={client}><AuthProvider><ObserveAuth>
        <WorkspaceContext.Provider value={{ current: workspace, workspaces: [workspace],
          isLoading: false, switchWorkspace: async () => {}, refresh: async () => {},
          role: workspace.role, canManage: true, canWrite: true,
          enabledModules: ['accounts', 'transactions', 'goals'],
          hasModule: (id) => workspace.enabled_modules.includes(id) }}>
          {show && <Placement />}
        </WorkspaceContext.Provider>
      </ObserveAuth></AuthProvider></QueryClientProvider>
    }
    async function drain() {
      for (let turn = 0; turn < 8; turn += 1) {
        const before = [reads.length, completions.length, stores.size, publications.length, bridgeRecords.length]
        await Promise.allSettled(reads)
        await Promise.all(completions)
        await Promise.all([...stores].map((store) => store.whenIdle()))
        await notifications(); await notifications()
        const after = [reads.length, completions.length, stores.size, publications.length, bridgeRecords.length]
        if (after.every((value, index) => value === before[index])) return
      }
      throw new Error('Observed work failed to quiesce within the finite drain')
    }
    function eligible(generation: number) {
      expect(required(state.auth, 'Auth').user).toBe(seed)
      expect(state.auth).toMatchObject({ token: TOKEN, isLoading: false })
      expect(localStorage.getItem('token')).toBe(TOKEN)
      expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(W)
      expect(getDashboardAuthSnapshot(client).generation).toBe(generation)
    }
    function cleanEvidence() {
      expect(errors).toEqual([])
      expect(warn.mock.calls).toEqual([])
      expect(error.mock.calls).toEqual([])
      expect(updateMeSpy).not.toHaveBeenCalled()
      expect(ports.updateMe).not.toHaveBeenCalled()
      expect(writerSpies.length).toBeGreaterThan(0)
      writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
      expect(fixtures).toEqual(preimages)
      expect(workspace).toEqual(workspaceBefore)
      ;[seed, baselineUser, postUser].forEach((user, index) => {
        expect(user.preferences).toBe(preferences[index])
        expect(Object.isFrozen(user.preferences)).toBe(true)
      })
      fixtures.forEach((fixture) => { expect(Object.isFrozen(fixture)).toBe(true) })
      expect(portCalls.filter((call) => call.phase === 'cleanup')).toEqual([])
    }
    function sameOwner(owner: DashboardLayoutOwner, snapshot: DashboardAuthSnapshot) {
      expect(state.owner).toBe(owner)
      expect([...owners]).toEqual([owner])
      expect([...stores]).toEqual([owner.store])
      expect(getDashboardAuthSnapshot(client)).toBe(snapshot)
      expect(bridgeRecords).toHaveLength(1)
      expect(bridgeRecords[0]).toBe(snapshot)
      eligible(snapshot.generation)
      captures.forEach(({ witness: entry }) => {
        expect(entry.user).toBe(seed)
        expect(entry.workspace).toBe(workspace)
        expect(entry).toMatchObject({ token: TOKEN, loading: false, storedToken: TOKEN, storedWorkspace: W })
        expect(entry.snapshot.generation).toBe(snapshot.generation)
        if (entry.owner) expect(entry.owner).toBe(owner)
      })
      owners.forEach((entry) => { expect(entry.authGeneration).toBe(snapshot.generation) })
      publications.forEach((entry) => { expect(entry.snapshot.authGeneration).toBe(snapshot.generation) })
    }
    const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
    let unmount: (() => void) | null = null
    let unmountCompleted = false
    let unsubscribeBridge = () => {}
    const onAsyncError = (event: ErrorEvent) => { errors.push(event.error ?? event.message) }
    const onRejection = (event: PromiseRejectionEvent) => { errors.push(event.reason) }
    window.addEventListener('error', onAsyncError)
    window.addEventListener('unhandledrejection', onRejection)
    try {
      // 1. Restore while the layout subtree is absent; same controlled seed for all reads.
      localStorage.setItem('token', TOKEN)
      localStorage.setItem(WORKSPACE_STORAGE_KEY, W)
      unmount = render(<Shell />, { reactStrictMode: strict }).unmount
      await act(async () => { restoration.resolve(seed); await drain() })
      restoring = false
      const r0 = getDashboardAuthSnapshot(client)
      const generation = r0.generation
      const Rcount = strict ? 2 : 1
      const restoreCalls = Array.from({ length: Rcount }, () => ({ phase: 'restore', completed: true, user: seed }))
      eligible(generation)
      expect(readCalls).toEqual(restoreCalls)
      expect(captures).toEqual([])
      expect(childLife).toEqual([])
      expect(tailLife).toEqual([])
      expect(owners.size).toBe(0)
      expect(stores.size).toBe(0)
      expect(requests).toEqual([])
      unsubscribeBridge = subscribeDashboardAuth(client, () => { bridgeRecords.push(getDashboardAuthSnapshot(client)) })
      // 2–3. Synchronous placement/replay witnesses; no assertions in caught callbacks.
      phase = 'mount'
      act(() => { required(state.show, 'placement mount')() })
      const firstChild = required(childLife[0]?.witness ?? null, 'first Child')
      const r1 = required(tailLife[0]?.witness ?? null, 'first Tail').snapshot
      expect(firstChild.snapshot).toBe(r0)
      expect(r1).not.toBe(r0)
      expect(r1).toEqual({ generation, revision: r0.revision + 1 })
      const replayEvents = strict ? ['setup', 'cleanup', 'setup'] : ['setup']
      expect(childLife.map((entry) => entry.event)).toEqual(replayEvents)
      expect(tailLife.map((entry) => entry.event)).toEqual(replayEvents)
      childLife.forEach((entry, index) => { expect(entry.witness.snapshot).toBe(index === 0 ? r0 : r1) })
      tailLife.forEach((entry) => { expect(entry.witness.snapshot).toBe(r1) })
      ;[...childLife, ...tailLife].forEach(({ witness: entry }) => {
        expect(entry.user).toBe(seed)
        expect(entry.workspace).toBe(workspace)
        expect(entry).toMatchObject({ token: TOKEN, loading: false, storedToken: TOKEN,
          storedWorkspace: W, reads: Rcount, puts: 0 })
      })
      expect(readCalls).toEqual(restoreCalls)
      expect(requests).toEqual([])
      // 4. Cross the real old-ticket/publication queues; poll only actual owner recovery.
      phase = 'recovery'
      await act(async () => { await drain() })
      await waitFor(() => {
        expect(state.owner).not.toBeNull()
        expect(state.owner?.authGeneration).toBe(generation)
      }, { timeout: 1000 })
      await act(async () => { await drain() })
      const owner = required(state.owner, 'recovered owner')
      const store = owner.store
      sameOwner(owner, r1)
      expect(owner.actorId).toBe(ACTOR)
      expect(readCalls).toEqual(restoreCalls)
      expect(requests).toEqual([])
      expect(store.getSnapshot(W)).toMatchObject({ writable: false, layout: seedLayout })
      expect(store.getSnapshot(W).overlay).toStrictEqual({})
      expect(() => store.open(W)).toThrow(/^Dashboard layout /)
      // 5. An explicit authoritative read, not an owner rescue or automatic mount GET.
      phase = 'refresh'
      await act(async () => { await track(store.refresh(W)); await drain() })
      expect(readCalls).toEqual([...restoreCalls, { phase: 'refresh', completed: true, user: baselineUser }])
      expect(requests).toEqual([])
      expect(store.getSnapshot(W)).toMatchObject({ writable: true, layout: defaults })
      expect(store.getSnapshot(W).layout).not.toEqual(seedLayout)
      sameOwner(owner, r1)
      // 6. Positive save on those same ports defeats blanket-disable negative evidence.
      const edit = store.open(W)
      const changed = toPersistedLayout(W, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
      expect(changed).not.toEqual(defaults)
      expect(changed).toEqual(saved)
      store.setDraft(edit, changed.items)
      expect(store.getSnapshot(W)).toMatchObject({ state: 'editing', openedBaseline: defaults, draft: changed })
      phase = 'save'
      await act(async () => { await track(store.finalize(edit)); await drain() })
      expect(requests).toEqual([{ phase: 'save', id: W, layout: saved, current: true, generation }])
      const expectedPorts = [...Array.from({ length: Rcount }, () => ({ port: 'GET', phase: 'restore' })),
        { port: 'GET', phase: 'refresh' }, { port: 'PUT', phase: 'save' }, { port: 'GET', phase: 'save' }]
      expect(portCalls).toEqual(expectedPorts)
      expect(readCalls).toEqual([...restoreCalls, { phase: 'refresh', completed: true, user: baselineUser },
        { phase: 'save', completed: true, user: postUser }])
      expect(store.getSnapshot(W)).toMatchObject({ state: 'closed-success', provenance: 'saved',
        writable: true, pending: false, readPending: false, openedBaseline: null, draft: null,
        layout: saved, counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
      expect(store.getSnapshot(W).overlay).toStrictEqual({ [W]: saved })
      expect(store.getSnapshot(SIBLING).layout).toEqual(sibling)
      expect(new Set(captures.map((entry) => entry.stage))).toEqual(new Set(['render', 'layout', 'passive']))
      expect(childLife.map((entry) => entry.event)).toEqual(replayEvents)
      expect(tailLife.map((entry) => entry.event)).toEqual(replayEvents)
      sameOwner(owner, r1)
      cleanEvidence()
      // 7. Actual unmount: ONE terminal publication DELTA, then an extra native barrier.
      const preUnmountPublications = publications.length
      phase = 'cleanup'
      act(() => { required(unmount, 'unmount')(); unmountCompleted = true })
      await act(async () => { await drain() })
      const r2 = getDashboardAuthSnapshot(client)
      expect(r2).not.toBe(r1)
      expect(r2).toEqual({ generation, revision: r0.revision + 2 })
      expect(bridgeRecords).toHaveLength(2)
      expect(bridgeRecords[0]).toBe(r1)
      expect(bridgeRecords[1]).toBe(r2)
      expect(publications.length - preUnmountPublications).toBe(1)
      const terminal = required(publications[preUnmountPublications] ?? null, 'terminal store publication')
      expect(terminal.store).toBe(store)
      expect(terminal.snapshot).toBe(store.getSnapshot(W))
      expect(terminal.snapshot).toMatchObject({ state: 'closed', provenance: 'default', writable: false,
        pending: false, readPending: false, openedBaseline: null, draft: null, layout: defaults })
      expect(terminal.snapshot.overlay).toStrictEqual({})
      expect(childLife.map((entry) => entry.event)).toEqual([...replayEvents, 'cleanup'])
      expect(tailLife.map((entry) => entry.event)).toEqual([...replayEvents, 'cleanup'])
      const finalPublicationCount = publications.length
      await act(async () => { await notifications(); await drain() })
      expect(getDashboardAuthSnapshot(client)).toBe(r2)
      expect(bridgeRecords).toHaveLength(2)
      expect(bridgeRecords[0]).toBe(r1)
      expect(bridgeRecords[1]).toBe(r2)
      expect(publications).toHaveLength(finalPublicationCount)
      expect([...owners]).toEqual([owner])
      expect([...stores]).toEqual([store])
      stores.forEach((entry) => { expect(entry.getSnapshot(W).writable).toBe(false) })
      expect(reads).toHaveLength(Rcount + 2)
      expect(readCalls.every((call) => call.completed)).toBe(true)
      expect(portCalls).toEqual(expectedPorts)
      expect(requests).toHaveLength(1)
      eligible(generation)
      cleanEvidence()
    } finally {
      phase = 'cleanup'; restoration.resolve(seed)
      try {
        await act(async () => {
          try { await drain() } finally {
            if (!unmountCompleted && unmount !== null) { unmount(); unmountCompleted = true }
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
