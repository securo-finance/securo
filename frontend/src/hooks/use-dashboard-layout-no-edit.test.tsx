import { useLayoutEffect, type ReactElement } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { act, render, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'

import { useDashboardLayout, type DashboardLayoutController } from '@/hooks/use-dashboard-layout'
import { AuthContext, type useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, type useWorkspace } from '@/contexts/workspace-context'
import {
  DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner,
} from '@/contexts/dashboard-layout-provider'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { createTestQueryClient, i18n } from '@/test/utils'
import { WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { getPageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import {
  type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding,
  validateCommittedDashboardLayout,
} from '@/lib/dashboard-layout'
import type { SessionSnapshot, SessionStore } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

const ports = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout,
    binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
}))

// Only the three API boundaries are replaced; contexts/owner/hook/store are real.
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: ports.me, updateMe: ports.updateMe },
  dashboard: { saveLayout: ports.saveLayout },
}))

const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'
const TOKEN = 'synthetic-canonical-no-edit-token'

function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Uninitialized deferred') }
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}
function required<T>(value: T | null, label: string): T {
  if (value === null) throw new Error(`Missing real witness: ${label}`)
  return value
}
function tick(): Promise<void> {
  return new Promise((resolve) => { queueMicrotask(resolve) })
}

describe('useDashboardLayout no-net-change persistence', () => {
  it.each(['default', 'saved', 'reset-then-undo'] as const)(
    '%s: closing an unchanged panel must not write stale preferences',
    async (scenario) => {
      const saved: DashboardLayout = {
        version: 1,
        workspaceId: A,
        items: DASHBOARD_WIDGETS.map((widget, order) => ({
          id: widget.id,
          visible: widget.id !== 'goals-progress',
          order,
        })),
      }
      const user: User = {
        id: ACTOR,
        email: 'tester@example.com',
        is_active: true,
        is_superuser: false,
        is_verified: true,
        is_2fa_enabled: false,
        preferences: {
          language: 'en',
          dashboard_layouts: scenario === 'default' ? {} : { [A]: saved },
        },
      }
      // A remains unchanged; another tab changed language and added sibling B.
      const remotePreferences = {
        ...user.preferences,
        language: 'pt-BR',
        dashboard_layouts: {
          ...user.preferences.dashboard_layouts,
          [B]: { ...saved, workspaceId: B },
        },
      }
      const remote: User = { ...user, preferences: remotePreferences }
      // Independent literal committed reply, never derived from a submitted PUT.
      const reply: CommittedDashboardLayout = { workspaceId: A, layout: {
        version: 1, workspaceId: A, items: [
          { id: 'hero-balance', visible: false, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: scenario === 'default', order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      } }
      // Independently authored postGET (not a PUT echo or reply projection).
      const post: User = {
        id: ACTOR, email: 'tester@example.com', is_active: true,
        is_superuser: false, is_verified: true, is_2fa_enabled: false,
        preferences: { language: 'pt-BR', dashboard_layouts: {
          [A]: { version: 1, workspaceId: A, items: [
            { id: 'hero-balance', visible: false, order: 0 },
            { id: 'uncategorized-banner', visible: true, order: 1 },
            { id: 'spending-charts', visible: true, order: 2 },
            { id: 'balance-evolution', visible: true, order: 3 },
            { id: 'goals-progress', visible: scenario === 'default', order: 4 },
            { id: 'period-transactions', visible: true, order: 5 },
          ] },
          [B]: { version: 1, workspaceId: B, items: [
            { id: 'hero-balance', visible: true, order: 0 },
            { id: 'uncategorized-banner', visible: true, order: 1 },
            { id: 'spending-charts', visible: true, order: 2 },
            { id: 'balance-evolution', visible: true, order: 3 },
            { id: 'goals-progress', visible: false, order: 4 },
            { id: 'period-transactions', visible: true, order: 5 },
          ] },
        } },
      }
      const fixtures = [user, remote, reply, post], fixturesBefore = structuredClone(fixtures)
      const remoteBefore = structuredClone(remotePreferences)
      const authPreferences = user.preferences, sibling = remotePreferences.dashboard_layouts[B]
      const postPreferences = post.preferences, postSibling = post.preferences.dashboard_layouts?.[B]
      const workspace: Workspace = {
        id: A, name: 'Canonical no-edit', kind: 'personal', is_archived: false,
        timezone: null,
        default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
        icon: null, color: null, created_at: '2026-10-05T00:00:00Z',
        created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
        enabled_modules: ['accounts', 'transactions', 'goals'],
      }
      const client = createTestQueryClient()
      const entryRead = deferred<User>(), laterRead = deferred<User>()
      const remountRead = deferred<User>(), postRead = deferred<User>()
      const put = deferred<CommittedDashboardLayout>()
      const errors: unknown[] = [], cleanupErrors: unknown[] = []
      const tasks: Promise<void>[] = [], publications: SessionSnapshot[] = []
      const stores = new Set<SessionStore>(), owners = new Set<DashboardLayoutOwner>()
      const unsubscribes: Array<() => void> = []
      const state: { owner: DashboardLayoutOwner | null; hook: DashboardLayoutController | null } = {
        owner: null, hook: null,
      }
      const gets: Array<{ user: User | null }> = []
      const requests: Array<{ id: string; layout: DashboardLayout;
        binding: DashboardRequestBinding; current: boolean; generation: number | null }> = []
      function track<T>(task: Promise<T>): Promise<T> {
        tasks.push(task.then(() => {}, (error: unknown) => { errors.push(error) }))
        return task
      }
      // Exactly: hook-entry READ, explicit remote READ, hook-remount READ,
      // finalize's immediate postGET (session.ts:292-335,487). No AuthProvider GET.
      const reads = [entryRead, laterRead, remountRead, postRead]
      ports.me.mockReset().mockImplementation(() => {
        const call: { user: User | null } = { user: null }
        gets.push(call)
        const read = reads[gets.length - 1]
        const task = read?.promise ?? Promise.reject(new Error('Unexpected extra GET'))
        track(task.then((value) => { call.user = value }))
        return task
      })
      ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
        const call = { id, layout: structuredClone(layout), binding, current: false,
          generation: state.owner?.authGeneration ?? null }
        requests.push(call)
        try { call.current = binding.isCurrent() } catch (error: unknown) { errors.push(error) }
        return track(requests.length === 1 ? put.promise : Promise.reject(new Error('Unexpected extra PUT')))
      })
      ports.updateMe.mockReset().mockImplementation(() =>
        track(Promise.reject(new Error('Forbidden legacy whole-preferences PATCH'))))
      const updateUser = vi.fn<(updated: User) => void>() // Explicit observable context port; no writer.
      const authContext: ReturnType<typeof useAuth> = {
        user, token: TOKEN, isLoading: false, updateUser,
        login: async () => { throw new Error('Unexpected login') },
        verify2fa: async () => { throw new Error('Unexpected verify2fa') },
        register: async () => { throw new Error('Unexpected register') },
        loginWithToken: () => { throw new Error('Unexpected loginWithToken') },
        logout: () => { throw new Error('Unexpected logout') },
      }
      const workspaceContext: ReturnType<typeof useWorkspace> = {
        current: workspace, workspaces: [workspace], isLoading: false,
        switchWorkspace: async () => { throw new Error('Unexpected workspace switch') },
        refresh: async () => { throw new Error('Unexpected workspace list refresh') },
        role: 'owner', canManage: true, canWrite: true,
        enabledModules: ['accounts', 'transactions', 'goals'],
        hasModule: (id) => workspace.enabled_modules.includes(id),
      }
      const warn = vi.spyOn(console, 'warn'), error = vi.spyOn(console, 'error') // Passthrough.
      function HookProbe() {
        const hook = useDashboardLayout()
        useLayoutEffect(() => { state.hook = hook })
        useLayoutEffect(() => () => { state.hook = null }, [])
        return null
      }
      function OwnerProbe({ show }: { show: boolean }) {
        const owner = useDashboardLayoutOwner()
        useLayoutEffect(() => {
          state.owner = owner
          if (!owner) return
          owners.add(owner)
          if (stores.has(owner.store)) return
          stores.add(owner.store)
          publications.push(owner.store.getSnapshot(A))
          unsubscribes.push(owner.store.subscribe(() => {
            try { publications.push(owner.store.getSnapshot(A)) }
            catch (failure: unknown) { errors.push(failure) }
          }))
        }, [owner])
        return owner && show ? <HookProbe /> : null
      }
      function Shell({ show }: { show: boolean }) {
        return <I18nextProvider i18n={i18n}><QueryClientProvider client={client}>
          <AuthContext.Provider value={authContext}>
            <WorkspaceContext.Provider value={workspaceContext}>
              <DashboardLayoutProvider><OwnerProbe show={show} /></DashboardLayoutProvider>
            </WorkspaceContext.Provider>
          </AuthContext.Provider>
        </QueryClientProvider></I18nextProvider>
      }
      // Includes issued transports, real subscriptions, store finalization and
      // native deferred page-lease release; mutation idle is only an extra witness.
      async function drain() {
        for (let turn = 0; turn < 8; turn += 1) {
          const before = [tasks.length, publications.length, gets.length, requests.length, stores.size]
          await tick(); await tick(); await Promise.all(tasks)
          for (const store of stores) {
            await store.whenIdle()
            await getPageSessionLease(store).whenIdle()
          }
          await tick(); await tick(); await Promise.all(tasks)
          await waitFor(() => expect(client.isMutating()).toBe(0))
          const after = [tasks.length, publications.length, gets.length, requests.length, stores.size]
          if (after.every((value, index) => value === before[index])) return
        }
        throw new Error('Canonical no-edit work failed to quiesce')
      }
      function evidence() {
        expect(errors).toEqual([])
        expect(warn.mock.calls).toEqual([]); expect(error.mock.calls).toEqual([])
        expect(ports.updateMe).not.toHaveBeenCalled(); expect(updateUser).not.toHaveBeenCalled()
        expect(fixtures).toStrictEqual(fixturesBefore)
        expect(remotePreferences).toStrictEqual(remoteBefore)
        expect(remote.preferences).toBe(remotePreferences)
        expect(remotePreferences.dashboard_layouts[B]).toBe(sibling)
        expect(authContext.user).toBe(user); expect(user.preferences).toBe(authPreferences)
        expect(user.preferences.language).toBe('en')
        expect(post.preferences).toBe(postPreferences)
        expect(post.preferences.dashboard_layouts?.[B]).toBe(postSibling)
        expect(postSibling).toStrictEqual(sibling)
        expect(authContext.token).toBe(TOKEN)
        expect(workspaceContext.current).toBe(workspace)
        expect(localStorage.getItem('token')).toBe(TOKEN)
        expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(A)
      }
      function readEvidence(expected: User[]) {
        expect(gets).toHaveLength(expected.length)
        gets.forEach((call, index) => { expect(call.user).toBe(expected[index]) })
        expect(ports.me.mock.calls).toEqual(expected.map(() => []))
      }
      const storageBefore = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({
        key, value: localStorage.getItem(key),
      }))
      let mounted: { rerender(ui: ReactElement): void; unmount(): void } | null = null
      let bodyFailed = false
      const onError = (event: ErrorEvent) => { errors.push(event.error ?? event.message) }
      const onRejection = (event: PromiseRejectionEvent) => { errors.push(event.reason) }
      window.addEventListener('error', onError)
      window.addEventListener('unhandledrejection', onRejection)
      try {
        localStorage.setItem('token', TOKEN); localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
        mounted = render(<Shell show />, { reactStrictMode: false })
        await act(async () => { entryRead.resolve(user); await drain() })
        const owner = required(state.owner, 'mounted real owner'), store = owner.store
        // Legacy hook has no entry GET: this is the first honest behavioral RED,
        // not a fallback/pre-opening of the store to make an unwired hook pass.
        readEvidence([user])
        const initial = store.getSnapshot(A)
        expect(initial).toMatchObject({ actorId: ACTOR, authGeneration: owner.authGeneration,
          workspaceId: A, writable: true, pending: false, readPending: false, state: 'closed',
          counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
        const hook = () => required(state.hook, 'real mounted hook')
        const stable = () => {
          expect(state.owner).toBe(owner); expect([...owners]).toEqual([owner])
          expect([...stores]).toEqual([store])
          expect(owner.actorId).toBe(ACTOR)
        }
        expect(hook().status).toBe(scenario === 'default' ? 'default' : 'saved')
        const initialDraft = structuredClone(hook().draft)
        expect(initialDraft).toStrictEqual(initial.layout)
        expect(initialDraft.items).toEqual(DASHBOARD_WIDGETS.map((widget, order) => ({
          id: widget.id, order, visible: scenario === 'default' || widget.id !== 'goals-progress',
        })))
        act(() => hook().openCustomize())
        expect(hook().customizeOpen).toBe(true)
        expect(hook().draft).toStrictEqual(initialDraft)
        expect(store.getSnapshot(A)).toMatchObject({ state: 'editing',
          openedBaseline: initialDraft, draft: initialDraft, canUndo: false })
        await act(async () => {
          const task = track(store.refresh(A)) // Public remote READ, not hook-entry substitution.
          laterRead.resolve(remote); await task; await drain()
        })
        readEvidence([user, remote])
        expect(hook().customizeOpen).toBe(true)
        expect(hook().draft).toStrictEqual(initialDraft)
        expect(store.getSnapshot(A)).toMatchObject({ openedBaseline: initialDraft, draft: initialDraft })
        if (scenario === 'reset-then-undo') {
          act(() => hook().reset())
          expect(hook().draft).not.toEqual(initialDraft)
          expect(hook().canUndo).toBe(true)
          expect(store.getSnapshot(A)).toMatchObject({ canUndo: true, draft: hook().draft })
          act(() => hook().undoReset())
          expect(hook().canUndo).toBe(false)
          expect(store.getSnapshot(A)).toMatchObject({ canUndo: false, draft: initialDraft })
        }
        expect(hook().draft).toStrictEqual(initialDraft)
        expect(ports.saveLayout).not.toHaveBeenCalled(); evidence(); stable()
        await act(async () => { hook().closeCustomize(true); await drain() })
        expect(hook().customizeOpen).toBe(false)
        expect(hook().draft).toStrictEqual(initialDraft)
        // finalize's exact-content branch increments suppressed once (:446-453).
        const afterClose = store.getSnapshot(A)
        expect(afterClose).toMatchObject({ state: 'closed', pending: false, readPending: false,
          openedBaseline: null, draft: null, canUndo: false, overlay: {},
          counters: { success: 0, suppressed: 1, conflict409: 0, other4xx: 0, server5xx: 0 } })
        expect(requests).toEqual([]); expect(ports.saveLayout).not.toHaveBeenCalled()
        readEvidence([user, remote]); evidence(); stable()
        // Reopen an identical draft and remove ONLY HookProbe. The real owner
        // stays mounted; its page-lease final release must suppress this too.
        act(() => hook().openCustomize())
        expect(hook().draft).toStrictEqual(initialDraft)
        await act(async () => { mounted?.rerender(<Shell show={false} />); await drain() })
        expect(state.hook).toBeNull(); stable()
        const afterUnmount = store.getSnapshot(A)
        expect(afterUnmount).toMatchObject({ state: 'closed', pending: false, readPending: false,
          openedBaseline: null, draft: null, canUndo: false, overlay: {},
          counters: { ...afterClose.counters, suppressed: afterClose.counters.suppressed + 1 } })
        expect(publications.at(-1)).toBe(afterUnmount)
        expect(requests).toEqual([]); expect(ports.saveLayout).not.toHaveBeenCalled()
        readEvidence([user, remote]); evidence()
        await act(async () => {
          mounted?.rerender(<Shell show />); remountRead.resolve(remote); await drain()
        })
        readEvidence([user, remote, remote]); stable()
        expect(hook().draft).toStrictEqual(initialDraft)
        act(() => hook().openCustomize())
        let toggled = false
        act(() => { toggled = hook().toggleVisible('hero-balance') })
        expect(toggled).toBe(true)
        const changed = structuredClone(hook().draft)
        expect(changed).not.toEqual(initialDraft)
        expect(changed.items).toEqual(initialDraft.items.map((item) =>
          item.id === 'hero-balance' ? { ...item, visible: false } : item))
        expect(changed).toStrictEqual(reply.layout)
        expect(validateCommittedDashboardLayout(A, reply, DASHBOARD_WIDGETS)).toStrictEqual(reply)
        expect(store.getSnapshot(A)).toMatchObject({ state: 'editing',
          openedBaseline: initialDraft, draft: changed })
        await act(async () => { hook().closeCustomize(true); await tick(); await tick() })
        expect(hook().customizeOpen).toBe(false)
        expect(requests).toHaveLength(1)
        const request = requests[0]!
        expect(request).toMatchObject({ id: A, layout: changed, current: true,
          generation: owner.authGeneration })
        expect(request.binding.isCurrent()).toBe(true)
        expect(Reflect.ownKeys(request.layout).sort()).toEqual(['items', 'version', 'workspaceId'])
        request.layout.items.forEach((item) => {
          expect(Reflect.ownKeys(item).sort()).toEqual(['id', 'order', 'visible'])
        })
        expect(store.getSnapshot(A)).toMatchObject({ state: 'pending', pending: true,
          counters: afterUnmount.counters })
        await act(async () => { put.resolve(reply); postRead.resolve(post); await drain() })
        const committed = store.getSnapshot(A)
        expect(committed).toMatchObject({ actorId: ACTOR, authGeneration: owner.authGeneration,
          workspaceId: A, provenance: 'saved', state: 'closed-success', writable: true,
          pending: false, readPending: false, canUndo: false, openedBaseline: null, draft: null,
          counters: { ...afterUnmount.counters, success: afterUnmount.counters.success + 1 } })
        expect(committed.layout).toStrictEqual(changed)
        expect(committed.overlay).toStrictEqual({ [A]: changed })
        expect(store.getSnapshot(B).layout).toStrictEqual(sibling)
        expect(publications.at(-1)).toBe(committed)
        expect(request.binding.isCurrent()).toBe(false)
        expect(hook().draft).toStrictEqual(changed)
        expect(ports.saveLayout).toHaveBeenCalledTimes(1)
        readEvidence([user, remote, remote, post]); evidence(); stable()
        await act(async () => { mounted?.rerender(<Shell show={false} />); await drain() })
        expect(store.getSnapshot(A)).toBe(committed)
        expect(ports.saveLayout).toHaveBeenCalledTimes(1)
        readEvidence([user, remote, remote, post]); evidence(); stable()
      } catch (failure: unknown) { bodyFailed = true; throw failure }
      finally {
        // Resolve all issued (and subsequently issued) controlled transports first.
        entryRead.resolve(user); laterRead.resolve(remote); remountRead.resolve(remote)
        put.resolve(reply); postRead.resolve(post)
        async function cleanup(action: () => void | Promise<void>) {
          try { await action() } catch (failure: unknown) { cleanupErrors.push(failure) }
        }
        await cleanup(async () => { await act(async () => { await drain() }) })
        await cleanup(async () => {
          await act(async () => { mounted?.rerender(<Shell show={false} />); await drain() })
        })
        await cleanup(async () => { await act(async () => { mounted?.unmount(); await drain() }) })
        await cleanup(() => {
          stores.forEach((store) => {
            expect(store.getSnapshot(A)).toMatchObject({ writable: false, pending: false,
              readPending: false, openedBaseline: null, draft: null, overlay: {} })
          })
        })
        await cleanup(() => { expect(errors).toEqual([]) })
        for (const unsubscribe of unsubscribes) await cleanup(unsubscribe)
        await cleanup(() => { client.clear() })
        await cleanup(async () => { await tick(); await tick() })
        await cleanup(() => { window.removeEventListener('error', onError) })
        await cleanup(() => { window.removeEventListener('unhandledrejection', onRejection) })
        await cleanup(() => { warn.mockRestore(); error.mockRestore() })
        ports.me.mockReset(); ports.saveLayout.mockReset(); ports.updateMe.mockReset()
        for (const { key, value } of storageBefore) {
          await cleanup(() => {
            if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value)
          })
        }
        if (bodyFailed && cleanupErrors.length) {
          console.error('Canonical no-edit cleanup diagnostics; primary failure preserved', cleanupErrors)
        }
      }
      if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'Canonical no-edit cleanup failed')
    },
  )
})
