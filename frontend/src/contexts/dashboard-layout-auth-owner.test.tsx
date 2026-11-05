import type { ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'

import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { DashboardLayoutProvider } from '@/contexts/dashboard-layout-provider'
import { useDashboardLayoutOwner } from '@/contexts/dashboard-layout-context'
import { getDashboardAuthSnapshot } from '@/lib/dashboard-layout-auth-bridge'
import { WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout, toPersistedLayout, toggleItem,
  type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding,
} from '@/lib/dashboard-layout'
import type { SessionSnapshot, SessionStore } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

// Only external transport ports are replaced; missing product imports are AVAILABILITY.
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

const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'
const TOKEN = 'synthetic-mounted-auth-cycle-token'
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

it('synchronously retires a queued old mounted Auth-cycle write and lets the fresh owner save', async () => {
  const defaults = resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, A)
  // Independent strict transport fixtures, never an echo of captured requests.
  const oldLayout: DashboardLayout = freezeFixture({ version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const queuedLayout: DashboardLayout = freezeFixture({ version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const freshLayout: DashboardLayout = freezeFixture({ version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: false, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] })
  const sibling = freezeFixture(toPersistedLayout(B,
    toggleItem(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, B).items,
      'hero-balance', false), DASHBOARD_WIDGETS))
  function userWith(layout: DashboardLayout): User {
    return freezeFixture({ id: ACTOR, email: 'mounted-auth-owner@example.invalid',
      is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
      preferences: { language: 'pt-BR', date_format: 'YYYY-MM-DD', timezone: 'UTC',
        currency_display: 'symbol', display_name: 'Synthetic mounted owner',
        onboarding_completed: true, dashboard_layouts: { [A]: layout, [B]: sibling } },
    })
  }
  const seed = userWith(toPersistedLayout(A,
    toggleItem(defaults.items, 'period-transactions', false), DASHBOARD_WIDGETS))
  const baselineUser = userWith(defaults)
  const oldPostUser = userWith(oldLayout)
  const freshPostUser = userWith(freshLayout)
  const seedBefore = structuredClone(seed)
  const preferencesBefore = seed.preferences
  const restored = deferred<User>()
  const relogin = deferred<User>()
  const reads: Promise<User>[] = []
  const completions: Promise<unknown>[] = []
  const stores = new Set<SessionStore>()
  type Phase = 'old' | 'cycle' | 'fresh'
  let phase: Phase = 'old'
  let transportUser = baselineUser
  const requests: Array<{ phase: Phase; id: string; layout: DashboardLayout;
    currentAtDispatch: boolean }> = []
  ports.me.mockReset().mockImplementation(() => {
    const read = reads.length === 0 ? restored.promise
      : phase === 'cycle' ? relogin.promise : Promise.resolve(transportUser)
    reads.push(read)
    void read.catch(() => {})
    return read
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    requests.push({ phase, id, layout: structuredClone(layout),
      currentAtDispatch: binding.isCurrent() })
    const committed = phase === 'old' ? oldLayout
      : phase === 'cycle' ? queuedLayout : freshLayout
    if (phase === 'old') transportUser = oldPostUser
    if (phase === 'fresh') transportUser = freshPostUser
    // Even an illicit old PUT gets a valid settled reply; no counterfeit guard.
    return Promise.resolve({ workspaceId: A, layout: committed })
  })
  ports.updateMe.mockReset().mockResolvedValue(seed)
  ports.login.mockReset().mockResolvedValue({ access_token: TOKEN })
  ports.verify2fa.mockReset().mockResolvedValue({ access_token: TOKEN, token_type: 'bearer' })
  ports.register.mockReset().mockResolvedValue(undefined)
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
  } })
  const workspace: Workspace = {
    id: A, name: 'Mounted tracer', kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
    icon: null, color: null, created_at: '2026-10-02T00:00:00Z',
    created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
    enabled_modules: ['accounts', 'transactions', 'goals'],
  }
  const workspaceValue = {
    current: workspace, workspaces: [workspace], isLoading: false,
    switchWorkspace: async () => {}, refresh: async () => {},
    role: workspace.role, canManage: true, canWrite: true,
    enabledModules: ['accounts', 'transactions', 'goals'] as const,
    hasModule: (id: string) => workspace.enabled_modules.includes(id),
  }
  const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
  const observedContexts = new WeakSet<object>()
  function ObserveRealAuth({ children }: { children: ReactNode }) {
    const context = useAuth()
    if (!observedContexts.has(context)) {
      observedContexts.add(context)
      // Default spy implementation calls the REAL exposed updater unchanged.
      writerSpies.push(vi.spyOn(context, 'updateUser'))
    }
    return children
  }
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}><AuthProvider>
      <ObserveRealAuth><WorkspaceContext.Provider value={{ ...workspaceValue,
        enabledModules: [...workspaceValue.enabledModules] }}>
        <DashboardLayoutProvider>{children}</DashboardLayoutProvider>
      </WorkspaceContext.Provider></ObserveRealAuth>
    </AuthProvider></QueryClientProvider>
  }
  const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => [key, localStorage.getItem(key)] as const)
  let unmount: (() => void) | undefined
  const outcome = (task: Promise<void>) => {
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
    expect(ports.updateMe).not.toHaveBeenCalled()
    expect(writerSpies.length).toBeGreaterThan(0)
    writerSpies.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
  }
  try {
    localStorage.setItem('token', TOKEN)
    localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
    const mounted = renderHook(() => ({ auth: useAuth(), owner: useDashboardLayoutOwner() }),
      { wrapper: Wrapper })
    unmount = mounted.unmount
    await act(async () => { restored.resolve(seed); await restored.promise; await notifications() })
    await waitFor(() => {
      expect(mounted.result.current.auth.isLoading).toBe(false)
      expect(mounted.result.current.auth.user).toBe(seed)
      expect(mounted.result.current.owner).not.toBeNull()
    })
    const oldOwner = mounted.result.current.owner!
    const old = oldOwner.store
    stores.add(old)
    expect(oldOwner.actorId).toBe(ACTOR)
    expect(old.getSnapshot(A).writable).toBe(false)
    await act(async () => { await old.refresh(A); await drain() })
    expect(old.getSnapshot(A)).toMatchObject({ writable: true, layout: defaults })
    expect(old.getSnapshot(A).layout).not.toEqual(seed.preferences.dashboard_layouts?.[A])
    const first = old.open(A)
    const changed = toPersistedLayout(A, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
    expect(changed).not.toEqual(defaults)
    expect(changed).toEqual(oldLayout)
    old.setDraft(first, changed.items)
    let firstOutcome!: Awaited<ReturnType<typeof outcome>>
    await act(async () => { firstOutcome = await outcome(old.finalize(first)); await drain() })
    expect(firstOutcome).toEqual({ ok: true })
    expect(requests).toEqual([{ phase: 'old', id: A, layout: oldLayout, currentAtDispatch: true }])
    expect(old.getSnapshot(A)).toMatchObject({ state: 'closed-success', provenance: 'saved',
      writable: true, layout: oldLayout, counters: { success: 1, suppressed: 0 } })
    expect(old.getSnapshot(A).overlay).toEqual({ [A]: oldLayout })
    expect(old.getSnapshot(B).layout).toEqual(sibling)
    expect(mounted.result.current.auth.user).toBe(seed)
    expect(seed).toEqual(seedBefore)
    expect(seed.preferences).toBe(preferencesBefore)
    noLegacyWrites()

    const second = old.open(A)
    const queued = toPersistedLayout(A, toggleItem(oldLayout.items, 'spending-charts', false), DASHBOARD_WIDGETS)
    expect(queued).not.toEqual(oldLayout)
    expect(queued).toEqual(queuedLayout)
    old.setDraft(second, queued.items)
    expect(old.getSnapshot(A)).toMatchObject({ openedBaseline: oldLayout, draft: queued, writable: true })
    const before = old.getSnapshot(A)
    const generation = getDashboardAuthSnapshot(client).generation
    const putCount = requests.length
    phase = 'cycle'
    let queuedOutcome!: ReturnType<typeof outcome>
    let immediate!: SessionSnapshot
    let immediateGeneration!: number
    act(() => {
      queuedOutcome = outcome(old.finalize(second))
      // No await/microtask gap; these are the REAL Auth context operations.
      mounted.result.current.auth.logout()
      mounted.result.current.auth.loginWithToken(TOKEN)
      immediate = old.getSnapshot(A)
      immediateGeneration = getDashboardAuthSnapshot(client).generation
    })
    let settled!: Awaited<ReturnType<typeof outcome>>
    await act(async () => {
      relogin.resolve(oldPostUser)
      settled = await queuedOutcome
      await drain()
    })
    // Intended B1 behavior RED AFTER positive reachability and all old work drains.
    expect(immediateGeneration, 'REAL Auth cycle must synchronously advance dashboard generation').not.toBe(generation)
    expect(immediate).toMatchObject({ state: 'closed', writable: false,
      pending: false, readPending: false, openedBaseline: null, draft: null })
    expect(immediate.overlay).toStrictEqual({})
    expect(settled.ok).toBe(false)
    if (!settled.ok) expect(settled.error).toBeInstanceOf(Error)
    expect(requests.length).toBe(putCount)
    expect(old.getSnapshot(A).counters).toEqual(before.counters)
    expect(old.getSnapshot(A).overlay).toStrictEqual({})
    expect(old.getSnapshot(A)).toMatchObject({ writable: false, openedBaseline: null, draft: null })
    expect(() => old.open(A)).toThrow(/^Dashboard layout /)
    noLegacyWrites()

    await waitFor(() => { expect(mounted.result.current.owner).not.toBeNull() })
    const freshOwner = mounted.result.current.owner!
    expect(freshOwner).not.toBe(oldOwner)
    expect(freshOwner.authGeneration).not.toBe(oldOwner.authGeneration)
    expect(freshOwner.authGeneration).toBe(getDashboardAuthSnapshot(client).generation)
    expect(freshOwner.actorId).toBe(ACTOR)
    expect(mounted.result.current.auth.token).toBe(TOKEN)
    const fresh = freshOwner.store
    expect(fresh).not.toBe(old)
    stores.add(fresh)
    phase = 'fresh'
    transportUser = oldPostUser
    await act(async () => { await fresh.refresh(A); await drain() })
    expect(fresh.getSnapshot(A)).toMatchObject({ writable: true, layout: oldLayout })
    const third = fresh.open(A)
    const freshChanged = toPersistedLayout(A, toggleItem(oldLayout.items, 'hero-balance', false), DASHBOARD_WIDGETS)
    expect(freshChanged).not.toEqual(oldLayout)
    expect(freshChanged).toEqual(freshLayout)
    fresh.setDraft(third, freshChanged.items)
    let freshOutcome!: Awaited<ReturnType<typeof outcome>>
    await act(async () => { freshOutcome = await outcome(fresh.finalize(third)); await drain() })
    expect(freshOutcome).toEqual({ ok: true })
    expect(requests).toEqual([
      { phase: 'old', id: A, layout: oldLayout, currentAtDispatch: true },
      { phase: 'fresh', id: A, layout: freshLayout, currentAtDispatch: true },
    ])
    expect(fresh.getSnapshot(A)).toMatchObject({ state: 'closed-success', provenance: 'saved',
      writable: true, pending: false, readPending: false, layout: freshLayout,
      openedBaseline: null, draft: null, counters: { success: 1, suppressed: 0 } })
    expect(fresh.getSnapshot(A).overlay).toEqual({ [A]: freshLayout })
    expect(fresh.getSnapshot(B).layout).toEqual(sibling)
    expect(old.getSnapshot(A).overlay).toStrictEqual({})
    expect(seed).toEqual(seedBefore)
    expect(seed.preferences).toBe(preferencesBefore)
    noLegacyWrites()
  } finally {
    restored.resolve(seed)
    relogin.resolve(oldPostUser)
    try {
      await act(async () => { unmount?.(); await drain() })
    } finally {
      vi.restoreAllMocks()
      storageBefore.forEach(([key, value]) => {
        if (value === null) localStorage.removeItem(key)
        else localStorage.setItem(key, value)
      })
    }
  }
})
