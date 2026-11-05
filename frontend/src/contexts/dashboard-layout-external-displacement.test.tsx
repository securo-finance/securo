import type { ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'

import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, useWorkspace } from '@/contexts/workspace-context'
import { DashboardLayoutProvider } from '@/contexts/dashboard-layout-provider'
import { useDashboardLayoutOwner } from '@/contexts/dashboard-layout-context'
import {
  getDashboardAuthSnapshot, registerDashboardOwner, subscribeDashboardAuth,
  type DashboardAuthSnapshot, type DashboardRegistration, type RetirementReason,
} from '@/lib/dashboard-layout-auth-bridge'
import { auth, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout, toPersistedLayout, toggleItem,
  type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding,
} from '@/lib/dashboard-layout'
import type { SessionStore } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

// External transport ports only; Auth, bridge, provider, hooks and store are real.
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
const TOKEN = 'synthetic-external-displacement-token'
const FRESH_TOKEN = 'synthetic-external-displacement-fresh-token'
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
// Nullable argument avoids narrowing callback-written leases to never.
function requireLease(lease: DashboardRegistration | null): DashboardRegistration {
  expect(lease).not.toBeNull()
  if (lease === null) throw new Error('Foreign dashboard registration was rejected')
  return lease
}

it('keeps an externally displaced normal owner terminal at same G and permits a fresh-G save', async () => {
  const defaults = freezeFixture(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, W))
  // Independent strict literal replies, not copies of captured requests.
  const savedOld: DashboardLayout = freezeFixture({ version: 1, workspaceId: W, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const queuedLayout: DashboardLayout = freezeFixture({ version: 1, workspaceId: W, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const savedFresh: DashboardLayout = freezeFixture({ version: 1, workspaceId: W, items: [
    { id: 'hero-balance', visible: false, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const sibling = freezeFixture(toPersistedLayout(SIBLING,
    toggleItem(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, SIBLING).items,
      'hero-balance', false), DASHBOARD_WIDGETS))
  function userWith(layout: DashboardLayout): User {
    return freezeFixture({ id: ACTOR, email: 'external-displacement@example.invalid',
      is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
      preferences: { language: 'pt-BR', timezone: 'UTC', display_name: 'Synthetic actor',
        dashboard_layouts: { [W]: layout, [SIBLING]: sibling } },
    })
  }
  const seed = userWith(toPersistedLayout(W,
    toggleItem(defaults.items, 'period-transactions', false), DASHBOARD_WIDGETS))
  const freshSeed = userWith(toPersistedLayout(W,
    toggleItem(savedOld.items, 'period-transactions', false), DASHBOARD_WIDGETS))
  const baselineUser = userWith(defaults)
  const oldPostUser = userWith(savedOld)
  const freshBaselineUser = userWith(savedOld)
  const freshPostUser = userWith(savedFresh)
  const fixtures = [seed, freshSeed, baselineUser, oldPostUser, freshBaselineUser, freshPostUser]
  const fixturePreimage = structuredClone(fixtures)
  const preferencesPreimage = fixtures.map((user) => user.preferences)
  const siblingPreimage = structuredClone(sibling)
  const restored = deferred<User>()
  const relogin = deferred<User>()
  const reads: Promise<unknown>[] = []
  const readErrors: unknown[] = []
  const callbackErrors: unknown[] = []
  const completions: Promise<unknown>[] = []
  const stores = new Set<SessionStore>()
  type Phase = 'old' | 'displaced' | 'relogin' | 'fresh'
  let phase: Phase = 'old'
  let transportUser = baselineUser
  const requests: Array<{ phase: Phase; id: string; layout: DashboardLayout;
    currentAtDispatch: boolean }> = []
  ports.me.mockReset().mockImplementation(() => {
    const read = reads.length === 0 ? restored.promise
      : phase === 'relogin' ? relogin.promise : Promise.resolve(transportUser)
    reads.push(read.then(() => undefined, (error: unknown) => { readErrors.push(error) }))
    return read
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    requests.push({ phase, id, layout: structuredClone(layout), currentAtDispatch: binding.isCurrent() })
    const committed = phase === 'fresh' ? savedFresh : phase === 'displaced' ? queuedLayout : savedOld
    transportUser = phase === 'fresh' ? freshPostUser : oldPostUser
    // Unexpected PUTs settle with valid replies; exact counts expose them.
    return Promise.resolve(freezeFixture({ workspaceId: W, layout: committed }))
  })
  ports.updateMe.mockReset().mockResolvedValue(seed)
  ports.login.mockReset()
  ports.verify2fa.mockReset()
  ports.register.mockReset()
  const updateMeSpy = vi.spyOn(auth, 'updateMe')
  const warnSpy = vi.spyOn(console, 'warn') // Passthrough, never muted.
  const errorSpy = vi.spyOn(console, 'error')
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
  } })
  const workspace: Workspace = freezeFixture({
    id: W, name: 'External displacement tracer', kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
    icon: null, color: null, created_at: '2026-10-03T00:00:00Z',
    created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
    enabled_modules: ['accounts', 'transactions', 'goals'],
  })
  const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
  const observedContexts = new WeakSet<object>()
  function ObserveRealAuth({ children }: { children: ReactNode }) {
    const context = useAuth()
    if (!observedContexts.has(context)) {
      observedContexts.add(context)
      writerSpies.push(vi.spyOn(context, 'updateUser'))
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
  const storageBefore: Array<[string, string | null]> =
    ['token', WORKSPACE_STORAGE_KEY].map((key) => [key, localStorage.getItem(key)])
  const bridgeNotifications: DashboardAuthSnapshot[] = []
  const unsubscribe = subscribeDashboardAuth(client, () => {
    try { bridgeNotifications.push(getDashboardAuthSnapshot(client)) }
    catch (error: unknown) { callbackErrors.push(error) }
  })
  const foreignReasons: RetirementReason[] = []
  const foreignRetire = (reason: RetirementReason) => {
    try { foreignReasons.push(reason) }
    catch (error: unknown) { callbackErrors.push(error) }
  }
  let foreign: DashboardRegistration | null = null
  let unmount: (() => void) | undefined
  function outcome(task: Promise<void>): Promise<{ ok: boolean; error?: unknown }> {
    const handled = task.then(() => ({ ok: true }), (error: unknown) => ({ ok: false, error }))
    completions.push(handled)
    return handled
  }
  async function drain() {
    await Promise.allSettled(completions)
    await Promise.allSettled(reads)
    await Promise.all([...stores].map((store) => store.whenIdle()))
    await notifications()
    await notifications()
  }
  function observations() {
    expect(readErrors).toStrictEqual([])
    expect(callbackErrors).toStrictEqual([])
    expect(updateMeSpy).not.toHaveBeenCalled()
    expect(ports.updateMe).not.toHaveBeenCalled()
    expect(writerSpies.length).toBeGreaterThan(0)
    writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    expect(warnSpy).not.toHaveBeenCalled()
    expect(errorSpy).not.toHaveBeenCalled()
    expect(fixtures).toStrictEqual(fixturePreimage)
    fixtures.forEach((user, index) => {
      expect(user.preferences).toBe(preferencesPreimage[index])
      expect(user.preferences.dashboard_layouts?.[SIBLING]).toBe(sibling)
    })
    expect(sibling).toStrictEqual(siblingPreimage)
  }
  function terminal(store: SessionStore) {
    expect(store.getSnapshot(W)).toMatchObject({ state: 'closed', writable: false,
      pending: false, readPending: false, openedBaseline: null, draft: null,
      provenance: 'default', layout: defaults, counters: { success: 1, suppressed: 0 } })
    expect(store.getSnapshot(W).overlay).toStrictEqual({})
    expect(() => store.open(W)).toThrow(/^Dashboard layout /)
  }
  function seedOnly(store: SessionStore, user: User) {
    expect(store.getSnapshot(W)).toMatchObject({ state: 'closed', writable: false,
      pending: false, readPending: false, openedBaseline: null, draft: null,
      layout: user.preferences.dashboard_layouts?.[W], counters: { success: 0, suppressed: 0 } })
    expect(store.getSnapshot(W).overlay).toStrictEqual({})
    expect(store.getSnapshot(SIBLING).layout).toStrictEqual(sibling)
    expect(() => store.open(W)).toThrow(/^Dashboard layout /)
    observations()
  }
  try {
    localStorage.setItem('token', TOKEN)
    localStorage.setItem(WORKSPACE_STORAGE_KEY, W)
    const mounted = renderHook(() => {
      const context = useAuth()
      const owner = useDashboardLayoutOwner()
      if (owner) stores.add(owner.store)
      return { auth: context, owner, client: useQueryClient(), workspace: useWorkspace().current }
    }, { wrapper: Wrapper, reactStrictMode: false })
    unmount = mounted.unmount
    expect(reads.length).toBe(1)
    expect(mounted.result.current.owner).toBeNull()
    await act(async () => { restored.resolve(seed); await restored.promise; await drain() })
    await waitFor(() => {
      expect(mounted.result.current.auth.user).toBe(seed)
      expect(mounted.result.current.auth.isLoading).toBe(false)
      expect(mounted.result.current.owner).not.toBeNull()
    })
    const oldOwner = mounted.result.current.owner!
    const old = oldOwner.store
    const g = getDashboardAuthSnapshot(client).generation
    expect(oldOwner.actorId).toBe(ACTOR)
    expect(oldOwner.authGeneration).toBe(g)
    seedOnly(old, seed)
    await act(async () => { expect(await outcome(old.refresh(W))).toEqual({ ok: true }); await drain() })
    expect(reads.length).toBe(2)
    expect(old.getSnapshot(W)).toMatchObject({ writable: true, layout: defaults })
    expect(old.getSnapshot(W).layout).not.toEqual(seed.preferences.dashboard_layouts?.[W])
    const first = old.open(W)
    const changed = freezeFixture(toPersistedLayout(W,
      toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS))
    expect(changed).not.toEqual(defaults)
    expect(changed).toStrictEqual(savedOld)
    old.setDraft(first, changed.items)
    await act(async () => { expect(await outcome(old.finalize(first))).toEqual({ ok: true }); await drain() })
    expect(reads.length).toBe(3)
    expect(requests).toStrictEqual([{ phase: 'old', id: W, layout: savedOld, currentAtDispatch: true }])
    expect(old.getSnapshot(W)).toMatchObject({ state: 'closed-success', writable: true,
      pending: false, readPending: false, openedBaseline: null, draft: null,
      provenance: 'saved', layout: savedOld, counters: { success: 1, suppressed: 0 } })
    expect(old.getSnapshot(W).overlay).toStrictEqual({ [W]: savedOld })
    expect(old.getSnapshot(SIBLING).layout).toStrictEqual(sibling)
    observations()
    const second = old.open(W)
    const queued = freezeFixture(toPersistedLayout(W,
      toggleItem(savedOld.items, 'spending-charts', false), DASHBOARD_WIDGETS))
    expect(queued).not.toEqual(savedOld)
    expect(queued).toStrictEqual(queuedLayout)
    old.setDraft(second, queued.items)
    expect(old.getSnapshot(W)).toMatchObject({ state: 'editing', openedBaseline: savedOld, draft: queued })
    const countersBefore = old.getSnapshot(W).counters
    const notificationCount = bridgeNotifications.length
    phase = 'displaced'
    let queuedOutcome!: ReturnType<typeof outcome>
    function sameIdentity() {
      expect(getDashboardAuthSnapshot(client).generation).toBe(g)
      expect(mounted.result.current.client).toBe(client)
      expect(mounted.result.current.workspace).toBe(workspace)
      expect(mounted.result.current.auth.user).toBe(seed)
      expect(mounted.result.current.auth.user?.id).toBe(ACTOR)
      expect(mounted.result.current.auth.token).toBe(TOKEN)
      expect(mounted.result.current.auth.isLoading).toBe(false)
      expect(localStorage.getItem('token')).toBe(TOKEN)
      expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(W)
    }
    act(() => {
      queuedOutcome = outcome(old.finalize(second))
      // No await gap or fifth replacing capability: real branch is 'displaced'.
      foreign = registerDashboardOwner(client, g, Symbol('TEST foreign'), foreignRetire)
      expect(requireLease(foreign).isCurrent()).toBe(true)
      terminal(old)
      sameIdentity()
    })
    await act(async () => {
      const settled = await queuedOutcome
      expect(settled.ok).toBe(false)
      expect(settled.error).toBeInstanceOf(Error)
      await drain()
    })
    function sameGCheckpoint() {
      sameIdentity()
      expect(mounted.result.current.owner).toBeNull()
      terminal(old)
      expect(old.getSnapshot(W).counters).toStrictEqual(countersBefore)
      expect(reads.length).toBe(3)
      expect(requests).toStrictEqual([{ phase: 'old', id: W, layout: savedOld, currentAtDispatch: true }])
      observations()
    }
    expect(bridgeNotifications.length).toBeGreaterThan(notificationCount)
    expect(foreignReasons).toStrictEqual([])
    sameGCheckpoint()
    const releaseRevision = getDashboardAuthSnapshot(client).revision
    const releaseNotifications = bridgeNotifications.length
    await act(async () => { requireLease(foreign).release(); await drain() })
    expect(requireLease(foreign).isCurrent()).toBe(false)
    expect(foreignReasons).toStrictEqual(['teardown'])
    expect(getDashboardAuthSnapshot(client).revision).toBeGreaterThan(releaseRevision)
    expect(bridgeNotifications.length).toBeGreaterThan(releaseNotifications)
    expect(bridgeNotifications[bridgeNotifications.length - 1]).toEqual(getDashboardAuthSnapshot(client))
    sameGCheckpoint()
    // Two distinct finite mounted-hook checkpoints; no identity pulse/remount.
    await act(async () => { mounted.rerender(); await drain() })
    sameGCheckpoint()
    await act(async () => { mounted.rerender(); await drain() })
    sameGCheckpoint()
    phase = 'relogin'
    act(() => {
      mounted.result.current.auth.logout()
      mounted.result.current.auth.loginWithToken(FRESH_TOKEN)
      expect(getDashboardAuthSnapshot(client).generation).toBe(g + 2)
      terminal(old)
    })
    // Both explicit login GET and distinct-token effect GET remain unresolved.
    await waitFor(() => { expect(reads.length).toBe(5) })
    expect(requests.length).toBe(1)
    observations()
    phase = 'fresh'
    transportUser = freshBaselineUser
    await act(async () => { relogin.resolve(freshSeed); await relogin.promise; await drain() })
    await waitFor(() => {
      expect(mounted.result.current.auth.user).toBe(freshSeed)
      expect(mounted.result.current.owner).not.toBeNull()
    })
    const freshOwner = mounted.result.current.owner!
    const fresh = freshOwner.store
    expect(freshOwner).not.toBe(oldOwner)
    expect(fresh).not.toBe(old)
    expect(freshOwner.actorId).toBe(ACTOR)
    expect(freshOwner.authGeneration).toBe(g + 2)
    expect(mounted.result.current.auth.token).toBe(FRESH_TOKEN)
    expect(mounted.result.current.client).toBe(client)
    expect(mounted.result.current.workspace).toBe(workspace)
    expect(reads.length).toBe(5)
    seedOnly(fresh, freshSeed)
    await act(async () => { expect(await outcome(fresh.refresh(W))).toEqual({ ok: true }); await drain() })
    expect(reads.length).toBe(6)
    expect(fresh.getSnapshot(W)).toMatchObject({ writable: true, layout: savedOld })
    expect(fresh.getSnapshot(W).layout).not.toEqual(freshSeed.preferences.dashboard_layouts?.[W])
    const third = fresh.open(W)
    const freshChanged = freezeFixture(toPersistedLayout(W,
      toggleItem(savedOld.items, 'hero-balance', false), DASHBOARD_WIDGETS))
    expect(freshChanged).not.toEqual(savedOld)
    expect(freshChanged).toStrictEqual(savedFresh)
    fresh.setDraft(third, freshChanged.items)
    await act(async () => { expect(await outcome(fresh.finalize(third))).toEqual({ ok: true }); await drain() })
    expect(reads.length).toBe(7)
    expect(requests).toStrictEqual([
      { phase: 'old', id: W, layout: savedOld, currentAtDispatch: true },
      { phase: 'fresh', id: W, layout: savedFresh, currentAtDispatch: true },
    ])
    expect(fresh.getSnapshot(W)).toMatchObject({ state: 'closed-success', writable: true,
      pending: false, readPending: false, openedBaseline: null, draft: null,
      provenance: 'saved', layout: savedFresh, counters: { success: 1, suppressed: 0 } })
    expect(fresh.getSnapshot(W).overlay).toStrictEqual({ [W]: savedFresh })
    expect(fresh.getSnapshot(SIBLING).layout).toStrictEqual(sibling)
    expect(getDashboardAuthSnapshot(client).generation).toBe(g + 2)
    terminal(old)
    expect(old.getSnapshot(W).counters).toStrictEqual(countersBefore)
    observations()
  } finally {
    restored.resolve(seed)
    relogin.resolve(freshSeed)
    try {
      await act(async () => {
        foreign?.release()
        await drain()
        unmount?.()
        await drain()
        unsubscribe()
        client.clear()
        await notifications()
      })
      observations()
    } finally {
      unsubscribe()
      client.clear()
      vi.restoreAllMocks()
      storageBefore.forEach(([key, value]) => {
        if (value === null) localStorage.removeItem(key)
        else localStorage.setItem(key, value)
      })
    }
  }
})
