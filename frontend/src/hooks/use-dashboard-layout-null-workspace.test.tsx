import { useLayoutEffect, type ReactElement } from 'react'
import { expect, it, vi } from 'vitest'
import { act, render, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'
import { useDashboardLayout, type DashboardLayoutController } from '@/hooks/use-dashboard-layout'
import { AuthContext, type useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, type useWorkspace } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { createTestQueryClient, i18n } from '@/test/utils'
import { WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { getPageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import type { SessionStore } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

const ports = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout, binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
}))
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: ports.me, updateMe: ports.updateMe },
  dashboard: { saveLayout: ports.saveLayout },
}))
const A = '11111111-1111-4111-8111-111111111111'
const ACTOR = '33333333-3333-4333-8333-333333333333'
const TOKEN = 'synthetic-null-workspace-token'
const client = createTestQueryClient()
function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Uninitialized transport') }
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}
function required<T>(value: T | null): T {
  if (value === null) throw new Error('Missing real mounted witness')
  return value
}
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })

it('discards unsent A session on null selection and permits a fresh same-owner A save', async () => {
  const user: User = {
    id: ACTOR, email: 'tester@example.com', is_active: true, is_superuser: false,
    is_verified: true, is_2fa_enabled: false,
    preferences: { language: 'en', dashboard_layouts: {} },
  }
  // Independent literal transport reply; never produced from the submitted PUT.
  const reply: CommittedDashboardLayout = { workspaceId: A, layout: {
    version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: false, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } }
  // Separately authored postGET, not a reply projection or a PUT echo.
  const post: User = {
    id: ACTOR, email: 'tester@example.com', is_active: true, is_superuser: false,
    is_verified: true, is_2fa_enabled: false, preferences: { language: 'en', dashboard_layouts: {
      [A]: { version: 1, workspaceId: A, items: [
        { id: 'hero-balance', visible: false, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: true, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ] },
    } },
  }
  const workspace: Workspace = {
    id: A, name: 'Null selection regression', kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null, icon: null, color: null,
    created_at: '2026-10-05T00:00:00Z', created_by_user_id: ACTOR,
    managed_by_user_id: null, role: 'owner', enabled_modules: ['accounts', 'transactions', 'goals'],
  }
  const updateUser = vi.fn<(updated: User) => void>()
  const unexpected = async (): Promise<never> => { throw new Error('Unexpected context action') }
  const authContext: ReturnType<typeof useAuth> = {
    user, token: TOKEN, isLoading: false, updateUser, login: unexpected, verify2fa: unexpected,
    register: unexpected, loginWithToken: () => { throw new Error('Unexpected login') },
    logout: () => { throw new Error('Unexpected logout') },
  }
  const workspaceContext: ReturnType<typeof useWorkspace> = {
    current: workspace, workspaces: [workspace], isLoading: false,
    switchWorkspace: unexpected, refresh: unexpected, role: 'owner', canManage: true, canWrite: true,
    enabledModules: ['accounts', 'transactions', 'goals'], hasModule: (id) => workspace.enabled_modules.includes(id),
  }
  const entry = deferred<User>(), restored = deferred<User>(), postRead = deferred<User>()
  const put = deferred<CommittedDashboardLayout>(), reads = [entry, restored, postRead]
  const errors: unknown[] = [], cleanupErrors: unknown[] = [], tasks: Promise<void>[] = []
  const gets: Array<User | null> = [], stores = new Set<SessionStore>(), owners = new Set<DashboardLayoutOwner>()
  const unsubscribes: Array<() => void> = []
  let publications = 0
  const state: { owner: DashboardLayoutOwner | null; hook: DashboardLayoutController | null } = { owner: null, hook: null }
  const requests: Array<{ id: string; layout: DashboardLayout; binding: DashboardRequestBinding; current: boolean }> = []
  function track<T>(task: Promise<T>): Promise<T> {
    tasks.push(task.then(() => {}, (failure: unknown) => { errors.push(failure) }))
    return task
  }
  ports.me.mockReset().mockImplementation(() => {
    const index = gets.length
    gets.push(null)
    return track((reads[index]?.promise ?? Promise.reject(new Error('Unexpected extra GET')))
      .then((value) => { gets[index] = value; return value }))
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    requests.push({ id, layout: structuredClone(layout), binding, current: binding.isCurrent() })
    return track(requests.length === 1 ? put.promise : Promise.reject(new Error('Unexpected extra PUT')))
  })
  ports.updateMe.mockReset().mockImplementation(() => track(Promise.reject(new Error('Forbidden PATCH'))))
  const warn = vi.spyOn(console, 'warn'), error = vi.spyOn(console, 'error') // Passthrough.
  function Probe() {
    const owner = useDashboardLayoutOwner(), hook = useDashboardLayout()
    useLayoutEffect(() => { state.hook = hook; state.owner = owner })
    useLayoutEffect(() => {
      if (!owner) return
      owners.add(owner)
      if (stores.has(owner.store)) return
      stores.add(owner.store)
      unsubscribes.push(owner.store.subscribe(() => { publications += 1 }))
    }, [owner])
    return null // Both hooks stay mounted through A -> null -> A.
  }
  function Shell({ current }: { current: Workspace | null }) {
    return <I18nextProvider i18n={i18n}><QueryClientProvider client={client}>
      <AuthContext.Provider value={authContext}>
        <WorkspaceContext.Provider value={{ ...workspaceContext, current }}>
          <DashboardLayoutProvider><Probe /></DashboardLayoutProvider>
        </WorkspaceContext.Provider>
      </AuthContext.Provider>
    </QueryClientProvider></I18nextProvider>
  }
  async function drain() {
    for (let turn = 0; turn < 8; turn += 1) {
      const before = [tasks.length, publications, gets.length, requests.length, stores.size]
      await tick(); await tick(); await Promise.all(tasks)
      for (const store of stores) { await store.whenIdle(); await getPageSessionLease(store).whenIdle() }
      await tick(); await tick(); await Promise.all(tasks)
      await waitFor(() => expect(client.isMutating()).toBe(0))
      const after = [tasks.length, publications, gets.length, requests.length, stores.size]
      if (after.every((value, index) => value === before[index])) return
    }
    throw new Error('Null-workspace work failed to quiesce')
  }
  function evidence() {
    expect(errors).toEqual([]); expect(warn.mock.calls).toEqual([]); expect(error.mock.calls).toEqual([])
    expect(ports.updateMe).not.toHaveBeenCalled(); expect(updateUser).not.toHaveBeenCalled()
    expect(authContext.user).toBe(user); expect(user.preferences.dashboard_layouts).toStrictEqual({})
    expect(authContext.token).toBe(TOKEN); expect(localStorage.getItem('token')).toBe(TOKEN)
  }
  const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let mounted: { rerender(ui: ReactElement): void; unmount(): void } | null = null
  let bodyFailed = false
  const onError = (event: ErrorEvent) => { errors.push(event.error ?? event.message) }
  const onRejection = (event: PromiseRejectionEvent) => { errors.push(event.reason) }
  window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
  try {
    localStorage.setItem('token', TOKEN); localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
    mounted = render(<Shell current={workspace} />, { reactStrictMode: false })
    await act(async () => { entry.resolve(user); await drain() })
    const owner = required(state.owner), store = owner.store, hook = () => required(state.hook)
    const stable = () => { expect(state.owner).toBe(owner); expect([...owners]).toEqual([owner]); expect([...stores]).toEqual([store]) }
    expect(gets).toEqual([user]); expect(ports.me).toHaveBeenCalledExactlyOnceWith()
    expect(store.getSnapshot(A)).toMatchObject({ state: 'closed', writable: true, pending: false, readPending: false })
    expect(hook().customizeOpen).toBe(false)
    const defaults = structuredClone(hook().draft)
    expect(defaults.items).toEqual(DASHBOARD_WIDGETS.map((widget, order) => ({ id: widget.id, order, visible: true })))
    act(() => hook().openCustomize())
    let toggled = false
    act(() => { toggled = hook().toggleVisible('hero-balance') })
    expect(toggled).toBe(true); expect(hook().customizeOpen).toBe(true)
    const edited = structuredClone(hook().draft)
    expect(edited).toStrictEqual(reply.layout)
    act(() => hook().reset())
    expect(hook().draft).toStrictEqual(defaults); expect(hook().canUndo).toBe(true)
    act(() => hook().undoReset())
    expect(hook().draft).toStrictEqual(edited); expect(hook().canUndo).toBe(false)
    act(() => hook().reset()) // Leave this meaningful undo slot unconsumed.
    expect(store.getSnapshot(A)).toMatchObject({ state: 'editing', draft: defaults, openedBaseline: defaults, canUndo: true })
    expect(hook().canUndo).toBe(true); evidence(); stable()
    await act(async () => { localStorage.removeItem(WORKSPACE_STORAGE_KEY); mounted?.rerender(<Shell current={null} />); await drain() })
    expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBeNull()
    expect(hook().customizeOpen).toBe(false); stable()
    expect(requests).toEqual([]); expect(ports.saveLayout).not.toHaveBeenCalled(); evidence()
    // Desired disposal RED: the original real store must drop its unsent session.
    expect(store.getSnapshot(A)).toMatchObject({ state: 'closed', draft: null, openedBaseline: null, canUndo: false, pending: false, readPending: false })
    expect(store.getSnapshot(A).overlay).toStrictEqual({})
    await act(async () => { localStorage.setItem(WORKSPACE_STORAGE_KEY, A); mounted?.rerender(<Shell current={workspace} />); restored.resolve(user); await drain() })
    expect(gets).toEqual([user, user]); stable(); evidence()
    expect(hook().customizeOpen).toBe(false); expect(hook().canUndo).toBe(false)
    expect(hook().status).toBe('default'); expect(hook().draft).toStrictEqual(defaults)
    expect(store.getSnapshot(A)).toMatchObject({ state: 'closed', draft: null, openedBaseline: null, canUndo: false, writable: true })
    expect(store.getSnapshot(A).overlay).toStrictEqual({}); expect(requests).toEqual([])
    act(() => hook().openCustomize())
    act(() => { toggled = hook().toggleVisible('hero-balance') })
    expect(toggled).toBe(true); expect(hook().draft).toStrictEqual(reply.layout)
    await act(async () => { hook().closeCustomize(true); await tick(); await tick() })
    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({ id: A, layout: reply.layout, current: true })
    expect(requests[0]!.binding.isCurrent()).toBe(true)
    await act(async () => { put.resolve(reply); postRead.resolve(post); await drain() })
    expect(gets).toEqual([user, user, post]); expect(ports.saveLayout).toHaveBeenCalledTimes(1)
    expect(store.getSnapshot(A)).toMatchObject({ state: 'closed-success', pending: false, readPending: false, layout: reply.layout, draft: null, openedBaseline: null, canUndo: false })
    expect(store.getSnapshot(A).overlay).toStrictEqual({ [A]: reply.layout })
    expect(hook().customizeOpen).toBe(false); expect(hook().draft).toStrictEqual(reply.layout)
    expect(requests[0]!.binding.isCurrent()).toBe(false); stable(); evidence()
  } catch (failure: unknown) { bodyFailed = true; throw failure }
  finally {
    entry.resolve(user); restored.resolve(user); put.resolve(reply); postRead.resolve(post)
    async function cleanup(action: () => void | Promise<void>) {
      try { await action() } catch (failure: unknown) { cleanupErrors.push(failure) }
    }
    await cleanup(async () => { await act(async () => { await drain() }) })
    await cleanup(async () => { await act(async () => { mounted?.unmount(); await drain() }) })
    await cleanup(() => { expect(errors).toEqual([]); expect(warn.mock.calls).toEqual([]); expect(error.mock.calls).toEqual([]) })
    for (const unsubscribe of unsubscribes) await cleanup(unsubscribe)
    await cleanup(() => { client.clear() })
    await cleanup(() => { window.removeEventListener('error', onError); window.removeEventListener('unhandledrejection', onRejection) })
    for (const { key, value } of storageBefore) await cleanup(() => {
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value)
    })
    await cleanup(() => { warn.mockRestore(); error.mockRestore() })
    ports.me.mockReset(); ports.saveLayout.mockReset(); ports.updateMe.mockReset()
    if (bodyFailed && cleanupErrors.length) console.error('Null-workspace cleanup diagnostics; primary failure preserved', cleanupErrors)
  }
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'Null-workspace cleanup failed')
})
