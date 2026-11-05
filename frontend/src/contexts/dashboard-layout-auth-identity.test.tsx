import type { ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'

import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext } from '@/contexts/workspace-context'
import {
  DashboardLayoutProvider, useDashboardLayoutOwner,
} from '@/contexts/dashboard-layout-provider'
import { getDashboardAuthSnapshot } from '@/lib/dashboard-layout-auth-bridge'
import { auth, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout, toPersistedLayout, toggleItem,
  type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding,
} from '@/lib/dashboard-layout'
import type { SessionStore } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

// External transport only: providers, bridge, registry and store remain real.
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
const ACTOR_A = '33333333-3333-4333-8333-333333333333'
const ACTOR_B = '44444444-4444-4444-8444-444444444444'
const TOKEN_A = 'synthetic-identity-handoff-token-a'
const TOKEN_B = 'synthetic-identity-handoff-token-b'
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
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

it('replaces temporary actor A with actor B at the same auth generation and lets B save', async () => {
  const defaults = resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, W)
  // Literal strict replies are independent of requests and draft transformations.
  const savedA: DashboardLayout = freezeFixture({ version: 1, workspaceId: W, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const baselineB: DashboardLayout = freezeFixture({ version: 1, workspaceId: W, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: false, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const savedB: DashboardLayout = freezeFixture({ version: 1, workspaceId: W, items: [
    { id: 'hero-balance', visible: false, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: false, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const sibling = freezeFixture(toPersistedLayout(SIBLING,
    toggleItem(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, SIBLING).items,
      'hero-balance', false), DASHBOARD_WIDGETS))
  function userWith(id: string, layout: DashboardLayout): User {
    return freezeFixture({ id, email: `${id}@example.invalid`,
      is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
      preferences: { language: 'pt-BR', timezone: 'UTC', display_name: id,
        dashboard_layouts: { [W]: layout, [SIBLING]: sibling } },
    })
  }
  const seedA = userWith(ACTOR_A, toPersistedLayout(W,
    toggleItem(defaults.items, 'period-transactions', false), DASHBOARD_WIDGETS))
  const seedB = userWith(ACTOR_B, toPersistedLayout(W,
    toggleItem(baselineB.items, 'period-transactions', false), DASHBOARD_WIDGETS))
  const baselineUserA = userWith(ACTOR_A, defaults)
  const postUserA = userWith(ACTOR_A, savedA)
  const baselineUserB = userWith(ACTOR_B, baselineB)
  const postUserB = userWith(ACTOR_B, savedB)
  const frozenUsers = [seedA, seedB, baselineUserA, postUserA, baselineUserB, postUserB]
  const beforeUsers = structuredClone(frozenUsers)
  const beforePreferences = frozenUsers.map((user) => user.preferences)
  const siblingBefore = structuredClone(sibling)
  const restored = deferred<User>()
  const actorB = deferred<User>()
  const reads: Promise<unknown>[] = []
  const readErrors: unknown[] = []
  const completions: Promise<unknown>[] = []
  const stores = new Set<SessionStore>()
  type Phase = 'actorA' | 'pendingActorB' | 'actorB'
  let phase: Phase = 'actorA'
  let transportUser = baselineUserA
  const requests: Array<{ phase: Phase; id: string; layout: DashboardLayout;
    currentAtDispatch: boolean }> = []
  ports.me.mockReset().mockImplementation(() => {
    const read = reads.length === 0 ? restored.promise
      : phase === 'pendingActorB' ? actorB.promise : Promise.resolve(transportUser)
    reads.push(read.then(() => undefined, (error: unknown) => { readErrors.push(error) }))
    return read
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    requests.push({ phase, id, layout: structuredClone(layout),
      currentAtDispatch: binding.isCurrent() })
    const committed = phase === 'actorB' ? savedB : savedA
    transportUser = phase === 'actorB' ? postUserB : postUserA
    // Unexpected PUTs also settle validly; exact request assertions catch them.
    return Promise.resolve({ workspaceId: W, layout: committed })
  })
  ports.updateMe.mockReset().mockResolvedValue(seedA)
  ports.login.mockReset()
  ports.verify2fa.mockReset()
  ports.register.mockReset()
  const updateMeSpy = vi.spyOn(auth, 'updateMe')
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
  } })
  const workspace: Workspace = {
    id: W, name: 'Same-generation tracer', kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
    icon: null, color: null, created_at: '2026-10-02T00:00:00Z',
    created_by_user_id: ACTOR_A, managed_by_user_id: null, role: 'owner',
    enabled_modules: ['accounts', 'transactions', 'goals'],
  }
  const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
  const observedContexts = new WeakSet<object>()
  function ObserveRealAuth({ children }: { children: ReactNode }) {
    const context = useAuth()
    if (!observedContexts.has(context)) {
      observedContexts.add(context)
      writerSpies.push(vi.spyOn(context, 'updateUser')) // Real passthrough.
    }
    return children
  }
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}><AuthProvider><ObserveRealAuth>
      <WorkspaceContext.Provider value={{ current: workspace, workspaces: [workspace],
        isLoading: false, switchWorkspace: async () => {}, refresh: async () => {},
        role: workspace.role, canManage: true, canWrite: true,
        enabledModules: ['accounts', 'transactions', 'goals'],
        hasModule: (id) => workspace.enabled_modules.includes(id) }}>
        <DashboardLayoutProvider>{children}</DashboardLayoutProvider>
      </WorkspaceContext.Provider>
    </ObserveRealAuth></AuthProvider></QueryClientProvider>
  }
  const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => [key, localStorage.getItem(key)] as const)
  let unmount: (() => void) | undefined
  function outcome(task: Promise<void>) {
    const handled = task.then(() => ({ ok: true as const }),
      (error: unknown) => ({ ok: false as const, error }))
    completions.push(handled)
    return handled
  }
  async function drain() {
    await Promise.allSettled(completions)
    await Promise.allSettled(reads)
    await Promise.all([...stores].map((store) => store.whenIdle()))
    await notifications()
  }
  function noLegacyWrites() {
    expect(updateMeSpy).not.toHaveBeenCalled()
    expect(ports.updateMe).not.toHaveBeenCalled()
    expect(writerSpies.length).toBeGreaterThan(0)
    writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
  }
  function immutableFixtures() {
    expect(frozenUsers).toEqual(beforeUsers)
    frozenUsers.forEach((user, index) => { expect(user.preferences).toBe(beforePreferences[index]) })
    expect(sibling).toEqual(siblingBefore)
  }
  function terminal(store: SessionStore) {
    expect(store.getSnapshot(W)).toMatchObject({ state: 'closed', writable: false,
      pending: false, readPending: false, openedBaseline: null, draft: null })
    expect(store.getSnapshot(W).overlay).toStrictEqual({})
    expect(() => store.open(W)).toThrow(/^Dashboard layout /)
  }
  try {
    localStorage.setItem('token', TOKEN_A)
    localStorage.setItem(WORKSPACE_STORAGE_KEY, W)
    const mounted = renderHook(() => {
      const context = useAuth()
      const owner = useDashboardLayoutOwner()
      if (owner) stores.add(owner.store)
      return { auth: context, owner }
    }, { wrapper: Wrapper })
    unmount = mounted.unmount
    await act(async () => { restored.resolve(seedA); await restored.promise; await notifications() })
    await waitFor(() => {
      expect(mounted.result.current.auth).toMatchObject({ user: seedA, token: TOKEN_A, isLoading: false })
      expect(mounted.result.current.owner).not.toBeNull()
    })
    const a0 = mounted.result.current.owner!
    const g0 = getDashboardAuthSnapshot(client).generation
    expect(a0.actorId).toBe(ACTOR_A)
    expect(a0.authGeneration).toBe(g0)
    expect(a0.store.getSnapshot(W)).toMatchObject({ writable: false, layout: seedA.preferences.dashboard_layouts?.[W] })
    expect(() => a0.store.open(W)).toThrow(/^Dashboard layout /)
    await act(async () => { expect(await outcome(a0.store.refresh(W))).toEqual({ ok: true }); await drain() })
    expect(a0.store.getSnapshot(W)).toMatchObject({ writable: true, layout: defaults })
    const editA = a0.store.open(W)
    const changedA = toPersistedLayout(W, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
    expect(changedA).not.toEqual(defaults)
    expect(changedA).toEqual(savedA)
    a0.store.setDraft(editA, changedA.items)
    await act(async () => { expect(await outcome(a0.store.finalize(editA))).toEqual({ ok: true }); await drain() })
    expect(a0.store.getSnapshot(W)).toMatchObject({ state: 'closed-success', writable: true,
      provenance: 'saved', layout: savedA, counters: { success: 1, suppressed: 0 } })
    expect(a0.store.getSnapshot(W).overlay).toStrictEqual({ [W]: savedA })
    expect(a0.store.getSnapshot(SIBLING).layout).toEqual(sibling)
    expect(requests).toEqual([{ phase: 'actorA', id: W, layout: savedA, currentAtDispatch: true }])
    noLegacyWrites()
    immutableFixtures()

    phase = 'pendingActorB'
    const readsBeforeHandoff = reads.length
    let g1!: number
    act(() => {
      mounted.result.current.auth.loginWithToken(TOKEN_B) // Exactly ONE real pulse.
      g1 = getDashboardAuthSnapshot(client).generation
      terminal(a0.store)
    })
    expect(g1).toBeGreaterThan(g0)
    // Do not drain here: BOTH real Auth reads are held on actorB.promise.
    await waitFor(() => {
      expect(mounted.result.current.auth).toMatchObject({ user: seedA, token: TOKEN_B, isLoading: false })
      expect(mounted.result.current.owner).not.toBeNull()
      expect(mounted.result.current.owner).not.toBe(a0)
      expect(reads.length).toBe(readsBeforeHandoff + 2)
    })
    const a1 = mounted.result.current.owner!
    expect(a1.actorId).toBe(ACTOR_A)
    expect(a1.authGeneration).toBe(g1)
    expect(a1.store).not.toBe(a0.store)
    expect(a1.store.getSnapshot(W)).toMatchObject({ writable: false, layout: seedA.preferences.dashboard_layouts?.[W] })
    expect(a1.store.getSnapshot(W).overlay).toStrictEqual({})
    expect(() => a1.store.open(W)).toThrow(/^Dashboard layout /)
    terminal(a0.store)
    noLegacyWrites()

    phase = 'actorB'
    transportUser = baselineUserB
    await act(async () => { actorB.resolve(seedB); await actorB.promise; await drain() })
    await waitFor(() => {
      expect(mounted.result.current.auth).toMatchObject({ user: seedB, token: TOKEN_B, isLoading: false })
      expect(mounted.result.current.owner).not.toBeNull()
      expect(mounted.result.current.owner?.actorId).toBe(ACTOR_B)
    })
    const b1 = mounted.result.current.owner!
    expect(b1).not.toBe(a1)
    expect(b1.store).not.toBe(a1.store)
    expect(b1.store).not.toBe(a0.store)
    expect(b1.authGeneration).toBe(g1)
    expect(b1.authGeneration).toBe(a1.authGeneration)
    expect(getDashboardAuthSnapshot(client).generation).toBe(g1)
    expect(localStorage.getItem('token')).toBe(TOKEN_B)
    terminal(a1.store)
    expect(b1.store.getSnapshot(W)).toMatchObject({ writable: false, layout: seedB.preferences.dashboard_layouts?.[W] })
    await act(async () => { expect(await outcome(b1.store.refresh(W))).toEqual({ ok: true }); await drain() })
    expect(b1.store.getSnapshot(W)).toMatchObject({ writable: true, layout: baselineB })
    expect(b1.store.getSnapshot(W).layout).not.toEqual(savedA)
    expect(b1.store.getSnapshot(W).layout).not.toEqual(seedB.preferences.dashboard_layouts?.[W])
    const editB = b1.store.open(W)
    const changedB = toPersistedLayout(W, toggleItem(baselineB.items, 'hero-balance', false), DASHBOARD_WIDGETS)
    expect(changedB).not.toEqual(baselineB)
    expect(changedB).toEqual(savedB)
    b1.store.setDraft(editB, changedB.items)
    await act(async () => { expect(await outcome(b1.store.finalize(editB))).toEqual({ ok: true }); await drain() })
    expect(requests).toEqual([
      { phase: 'actorA', id: W, layout: savedA, currentAtDispatch: true },
      { phase: 'actorB', id: W, layout: savedB, currentAtDispatch: true },
    ])
    expect(b1.store.getSnapshot(W)).toMatchObject({ state: 'closed-success', provenance: 'saved',
      writable: true, pending: false, readPending: false, layout: savedB,
      openedBaseline: null, draft: null,
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
    expect(b1.store.getSnapshot(W).overlay).toStrictEqual({ [W]: savedB })
    expect(b1.store.getSnapshot(SIBLING).layout).toEqual(sibling)
    expect(mounted.result.current.auth.user).toBe(seedB)
    expect(getDashboardAuthSnapshot(client).generation).toBe(g1)
    terminal(a0.store)
    terminal(a1.store)
    expect(readErrors).toEqual([])
    noLegacyWrites()
    immutableFixtures()
  } finally {
    restored.resolve(seedA)
    actorB.resolve(seedB)
    try {
      await act(async () => { await drain(); unmount?.(); await drain(); client.clear(); await notifications() })
    } finally {
      vi.restoreAllMocks()
      storageBefore.forEach(([key, value]) => {
        if (value === null) localStorage.removeItem(key)
        else localStorage.setItem(key, value)
      })
    }
  }
})
