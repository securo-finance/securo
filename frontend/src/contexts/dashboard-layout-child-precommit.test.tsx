import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { act, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'

import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, useWorkspace } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { useDashboardLayoutOwner } from '@/contexts/dashboard-layout-context'
import {
  getDashboardAuthSnapshot, subscribeDashboardAuth, type DashboardAuthSnapshot,
} from '@/lib/dashboard-layout-auth-bridge'
import { auth, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout, toPersistedLayout, toggleItem,
  type DashboardLayout, type DashboardRequestBinding, type CommittedDashboardLayout,
} from '@/lib/dashboard-layout'
import type { SessionStore, SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

// External transport only: Auth methods, owner factory and bridge guards stay real.
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
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}
function notifications(): Promise<void> {
  return new Promise((resolve) => { queueMicrotask(resolve) })
}

it('refuses the stale first registration after a child identical-token Auth pulse and recovers a writable fresh owner', async () => {
  const defaults = freezeFixture(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, W))
  // Independent literal strict reply, not an echo or derivative of the submitted draft.
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
  const fixtures = [seed, baselineUser, postUser, reply, sibling]
  const preimages = structuredClone(fixtures)
  const preferences = [seed.preferences, baselineUser.preferences, postUser.preferences]
  const restoration = deferred<User>()
  const loginRead = deferred<User>()
  type Phase = 'restore' | 'precommit' | 'recovery' | 'refresh' | 'save' | 'cleanup'
  let phase: Phase = 'restore'
  let transportUser = baselineUser
  const errors: unknown[] = []
  const reads: Promise<User>[] = []
  const completions: Promise<void>[] = []
  const portCalls: Array<{ port: string; phase: Phase }> = []
  type ReadCall = { phase: Phase; completed: boolean; user: User | null }
  const readCalls: ReadCall[] = []
  const requests: Array<{ phase: Phase; id: string; layout: DashboardLayout;
    current: boolean; generation: number }> = []
  function track(task: Promise<void>): Promise<void> {
    const handled = task.then(() => {}, (failure: unknown) => { errors.push(failure) })
    completions.push(handled)
    return handled
  }
  ports.me.mockReset().mockImplementation(() => {
    portCalls.push({ port: 'GET', phase })
    const call: ReadCall = { phase, completed: false, user: null }
    // The only held reads are restoration and the child's real loginWithToken read.
    const read = reads.length === 0 ? restoration.promise
      : reads.length === 1 ? loginRead.promise : Promise.resolve(transportUser)
    readCalls.push(call)
    reads.push(read)
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
    const result = Promise.resolve(reply)
    track(result.then(() => {}))
    return result
  })
  ports.updateMe.mockReset().mockImplementation(() => {
    portCalls.push({ port: 'PATCH', phase }); return Promise.resolve(seed)
  })
  ports.login.mockReset().mockImplementation(() => {
    portCalls.push({ port: 'login', phase }); return Promise.resolve({ access_token: TOKEN })
  })
  ports.verify2fa.mockReset().mockImplementation(() => {
    portCalls.push({ port: 'verify2fa', phase }); return Promise.resolve({ access_token: TOKEN })
  })
  ports.register.mockReset().mockImplementation(() => {
    portCalls.push({ port: 'register', phase }); return Promise.resolve()
  })
  const updateMeSpy = vi.spyOn(auth, 'updateMe') // Passthrough, no implementation replacement.
  const warn = vi.spyOn(console, 'warn')
  const error = vi.spyOn(console, 'error')
  const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
  const contexts = new WeakSet<object>()
  const stores = new Set<SessionStore>()
  const owners = new Set<DashboardLayoutOwner>()
  const unsubscribes: Array<() => void> = []
  const publications: SessionSnapshot[] = []
  const bridgeRecords: DashboardAuthSnapshot[] = []
  const workspace: Workspace = freezeFixture({
    id: W, name: 'Child precommit', kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
    icon: null, color: null, created_at: '2026-10-03T00:00:00Z',
    created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
    enabled_modules: ['accounts', 'transactions', 'goals'],
  })
  type Witness = {
    owner: DashboardLayoutOwner | null; user: User | null; token: string | null
    loading: boolean; workspace: Workspace | null; snapshot: DashboardAuthSnapshot
    storedToken: string | null; storedWorkspace: string | null; queued: boolean
  }
  const state: {
    auth: ReturnType<typeof useAuth> | null; owner: DashboardLayoutOwner | null
    show: (() => void) | null; before: Witness | null; after: Witness | null; tail: Witness | null
  } = { auth: null, owner: null, show: null, before: null, after: null, tail: null }
  const captures: Array<{ stage: 'render' | 'layout' | 'passive'; witness: Witness }> = []
  const events: string[] = []
  let queued = false
  let pulses = 0
  function witness(owner: DashboardLayoutOwner | null,
    context: ReturnType<typeof useAuth>, current: Workspace | null): Witness {
    if (owner) {
      owners.add(owner)
      if (!stores.has(owner.store)) {
        stores.add(owner.store)
        unsubscribes.push(owner.store.subscribe(() => {
          publications.push(owner.store.getSnapshot(W))
        }))
      }
    }
    return { owner, user: context.user, token: context.token, loading: context.isLoading,
      workspace: current, snapshot: getDashboardAuthSnapshot(client),
      storedToken: localStorage.getItem('token'),
      storedWorkspace: localStorage.getItem(WORKSPACE_STORAGE_KEY), queued }
  }
  function ObserveAuth({ children }: { children: ReactNode }) {
    const context = useAuth()
    if (!contexts.has(context)) {
      contexts.add(context)
      writerSpies.push(vi.spyOn(context, 'updateUser')) // Every real exposed context.
    }
    useLayoutEffect(() => { state.auth = context })
    return children
  }
  function Child() {
    const owner = useDashboardLayoutOwner()
    const context = useAuth()
    const current = useWorkspace().current
    const pulsed = useRef(false)
    captures.push({ stage: 'render', witness: witness(owner, context, current) })
    useLayoutEffect(() => {
      state.owner = owner
      captures.push({ stage: 'layout', witness: witness(owner, context, current) })
      if (pulsed.current) return
      pulsed.current = true
      state.before = witness(owner, context, current)
      events.push('child-before')
      pulses += 1
      context.loginWithToken(TOKEN)
      state.after = witness(owner, context, current)
      events.push('pulse-after')
      queueMicrotask(() => { queued = true })
    })
    useEffect(() => { captures.push({ stage: 'passive', witness: witness(owner, context, current) }) })
    return null
  }
  function TailWitness() {
    const context = useAuth()
    const current = useWorkspace().current
    const recorded = useRef(false)
    useLayoutEffect(() => {
      if (recorded.current) return
      recorded.current = true
      state.tail = witness(null, context, current)
      events.push('tail')
    })
    return null
  }
  function Shell() {
    const [show, setShow] = useState(false)
    useLayoutEffect(() => { state.show = () => { setShow(true) } }, [])
    return <QueryClientProvider client={client}><AuthProvider><ObserveAuth>
      <WorkspaceContext.Provider value={{ current: workspace, workspaces: [workspace],
        isLoading: false, switchWorkspace: async () => {}, refresh: async () => {},
        role: workspace.role, canManage: true, canWrite: true,
        enabledModules: ['accounts', 'transactions', 'goals'],
        hasModule: (id) => workspace.enabled_modules.includes(id) }}>
        {show && <><DashboardLayoutProvider><Child /></DashboardLayoutProvider><TailWitness /></>}
      </WorkspaceContext.Provider>
    </ObserveAuth></AuthProvider></QueryClientProvider>
  }
  async function drain() {
    // Recollect after native barriers: completions may issue the post-PUT read.
    for (let turn = 0; turn < 8; turn += 1) {
      const readCount = reads.length
      const completionCount = completions.length
      const storeCount = stores.size
      await Promise.allSettled(reads)
      await Promise.all(completions)
      await Promise.all([...stores].map((store) => store.whenIdle()))
      await notifications()
      await notifications()
      if (reads.length === readCount && completions.length === completionCount && stores.size === storeCount) return
    }
    throw new Error('Observed work failed to quiesce within the finite drain')
  }
  function eligible(generation: number) {
    expect(state.auth).toMatchObject({ user: seed, token: TOKEN, isLoading: false })
    expect(required(state.auth, 'Auth').user).toBe(seed)
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
    ;[seed, baselineUser, postUser].forEach((user, index) => {
      expect(user.preferences).toBe(preferences[index])
    })
    if (state.after !== null) {
      const generation = state.after.snapshot.generation
      owners.forEach((owner) => { expect(owner.authGeneration).toBe(generation) })
      bridgeRecords.forEach((snapshot) => { expect(snapshot.generation).toBe(generation) })
      publications.forEach((snapshot) => { expect(snapshot.authGeneration).toBe(generation) })
    }
    expect(portCalls.filter((call) => call.phase === 'cleanup')).toEqual([])
  }
  const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let unmount: (() => void) | null = null
  let unsubscribeBridge = () => {}
  const onAsyncError = (event: ErrorEvent) => { errors.push(event.error ?? event.message) }
  const onRejection = (event: PromiseRejectionEvent) => { errors.push(event.reason) }
  window.addEventListener('error', onAsyncError)
  window.addEventListener('unhandledrejection', onRejection)
  try {
    localStorage.setItem('token', TOKEN)
    localStorage.setItem(WORKSPACE_STORAGE_KEY, W)
    unmount = render(<Shell />, { reactStrictMode: false }).unmount
    await act(async () => { restoration.resolve(seed); await drain() })
    const initial = getDashboardAuthSnapshot(client)
    eligible(initial.generation)
    expect(captures).toEqual([])
    expect(owners.size).toBe(0)
    expect(readCalls).toEqual([{ phase: 'restore', completed: true, user: seed }])
    unsubscribeBridge = subscribeDashboardAuth(client, () => {
      bridgeRecords.push(getDashboardAuthSnapshot(client)) // Record only; bridge catches observers.
    })
    phase = 'precommit'
    act(() => { required(state.show, 'subtree mount')() })
    const before = required(state.before, 'child-before')
    const after = required(state.after, 'pulse-after')
    const tail = required(state.tail, 'first tail layout')
    const generation = initial.generation + 1
    expect(events).toEqual(['child-before', 'pulse-after', 'tail'])
    expect(pulses).toBe(1)
    expect(before.owner).toBeNull()
    expect(before.snapshot).toBe(initial)
    expect(after.snapshot).toEqual({ generation, revision: initial.revision + 1 })
    // An illicit stale install increments revision even if render hides its owner.
    expect(tail.snapshot).toBe(after.snapshot)
    expect(tail.snapshot).toEqual({ generation, revision: initial.revision + 1 })
    ;[before, after, tail].forEach((entry) => {
      expect(entry.user).toBe(seed)
      expect(entry.workspace).toBe(workspace)
      expect(entry).toMatchObject({ token: TOKEN, loading: false,
        storedToken: TOKEN, storedWorkspace: W, queued: false })
    })
    expect(readCalls).toEqual([
      { phase: 'restore', completed: true, user: seed },
      { phase: 'precommit', completed: false, user: null },
    ])
    expect(requests).toEqual([])
    phase = 'recovery'
    await act(async () => { loginRead.resolve(seed); await drain() })
    // The only timing poll is bounded fresh-current-owner recovery, not precommit order.
    await waitFor(() => {
      expect(state.owner).not.toBeNull()
      expect(state.owner?.authGeneration).toBe(generation)
    }, { timeout: 1000 })
    await act(async () => { await drain() })
    eligible(generation)
    expect(reads.length).toBe(2)
    expect(readCalls.every((call) => call.completed)).toBe(true)
    expect(requests).toEqual([])
    const owner = required(state.owner, 'fresh owner')
    const store = owner.store
    expect(owner.actorId).toBe(ACTOR)
    expect(store.getSnapshot(W)).toMatchObject({ writable: false, layout: seedLayout })
    expect(store.getSnapshot(W).overlay).toStrictEqual({})
    expect(() => store.open(W)).toThrow(/^Dashboard layout /)
    phase = 'refresh'
    await act(async () => { await track(store.refresh(W)); await drain() })
    expect(store.getSnapshot(W)).toMatchObject({ writable: true, layout: defaults })
    expect(store.getSnapshot(W).layout).not.toEqual(seedLayout)
    const edit = store.open(W)
    const changed = toPersistedLayout(W, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
    expect(changed).not.toEqual(defaults)
    expect(changed).toEqual(saved)
    store.setDraft(edit, changed.items)
    expect(store.getSnapshot(W)).toMatchObject({ state: 'editing', openedBaseline: defaults, draft: changed })
    phase = 'save'
    await act(async () => { await track(store.finalize(edit)); await drain() })
    expect(requests).toEqual([{ phase: 'save', id: W, layout: saved, current: true, generation }])
    expect(portCalls).toEqual([
      { port: 'GET', phase: 'restore' }, { port: 'GET', phase: 'precommit' },
      { port: 'GET', phase: 'refresh' }, { port: 'PUT', phase: 'save' }, { port: 'GET', phase: 'save' },
    ])
    expect(readCalls).toEqual([
      { phase: 'restore', completed: true, user: seed },
      { phase: 'precommit', completed: true, user: seed },
      { phase: 'refresh', completed: true, user: baselineUser },
      { phase: 'save', completed: true, user: postUser },
    ])
    expect(store.getSnapshot(W)).toMatchObject({ state: 'closed-success', provenance: 'saved',
      writable: true, pending: false, readPending: false, openedBaseline: null, draft: null,
      layout: saved, counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
    expect(store.getSnapshot(W).overlay).toStrictEqual({ [W]: saved })
    expect(store.getSnapshot(SIBLING).layout).toEqual(sibling)
    expect(events).toEqual(['child-before', 'pulse-after', 'tail'])
    expect(pulses).toBe(1)
    expect(owners.size).toBeGreaterThan(0)
    captures.forEach(({ witness: entry }) => {
      expect(entry.user).toBe(seed)
      expect(entry.workspace).toBe(workspace)
      expect(entry).toMatchObject({ token: TOKEN, loading: false, storedToken: TOKEN, storedWorkspace: W })
      if (entry.owner) expect(entry.owner.authGeneration).toBe(generation)
    })
    expect(new Set(captures.map((entry) => entry.stage))).toEqual(new Set(['render', 'layout', 'passive']))
    expect(bridgeRecords.length).toBeGreaterThan(0)
    expect(publications.length).toBeGreaterThan(0)
    eligible(generation)
    cleanEvidence()
  } finally {
    phase = 'cleanup'
    restoration.resolve(seed)
    loginRead.resolve(seed)
    try {
      await act(async () => {
        await drain()
        unmount?.()
        await drain() // Includes guarded owner retirement and its native publications.
      })
      unsubscribeBridge()
      unsubscribes.forEach((unsubscribe) => { unsubscribe() })
      client.clear()
      await notifications()
      await drain()
      cleanEvidence() // Passthrough warnings/errors through actual teardown remain failures.
    } finally {
      unsubscribeBridge()
      unsubscribes.forEach((unsubscribe) => { unsubscribe() })
      client.clear()
      window.removeEventListener('error', onAsyncError)
      window.removeEventListener('unhandledrejection', onRejection)
      vi.restoreAllMocks()
      storageBefore.forEach(({ key, value }) => {
        if (value === null) localStorage.removeItem(key)
        else localStorage.setItem(key, value)
      })
    }
  }
})
