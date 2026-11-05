import { useLayoutEffect, useState, type ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { act, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'

import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, useWorkspace } from '@/contexts/workspace-context'
import {
  DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner,
} from '@/contexts/dashboard-layout-provider'
import {
  getDashboardAuthSnapshot, registerDashboardOwner,
  type DashboardRegistration, type RetirementReason,
} from '@/lib/dashboard-layout-auth-bridge'
import { auth, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout, toPersistedLayout, toggleItem,
  type DashboardLayout, type DashboardRequestBinding, type CommittedDashboardLayout,
} from '@/lib/dashboard-layout'
import type { SessionStore } from '@/lib/dashboard-layout-session'
import type { User, Workspace } from '@/types'

// Transport only. Mutation depth, registration, eligibility and retry stay real.
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
const TOKEN = 'synthetic-registration-retry-token'
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
function leaseCurrent(registration: DashboardRegistration | null): boolean {
  return registration?.isCurrent() ?? false
}

it('acquires and saves after genuine registration denial in plain and root StrictMode', async () => {
  async function scenario(strict: boolean) {
    const h = `HARNESS ${strict ? 'strict' : 'plain'}:`
    const b = strict ? 'BEHAVIOR qualified strict retry:' : 'HARNESS plain control:'
    const defaults = freezeFixture(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, W))
    // Independent literal committed reply; never derived from captured PUT/draft.
    const saved: DashboardLayout = freezeFixture({ version: 1, workspaceId: W, items: [
      { id: 'hero-balance', visible: true, order: 0 },
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
      return freezeFixture({ id: ACTOR, email: 'registration-retry@example.invalid',
        is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
        preferences: { language: 'pt-BR', timezone: 'UTC', display_name: 'Retry tracer',
          dashboard_layouts: { [W]: layout, [SIBLING]: sibling } },
      })
    }
    const seed = userWith(toPersistedLayout(W,
      toggleItem(defaults.items, 'period-transactions', false), DASHBOARD_WIDGETS))
    const baselineUser = userWith(defaults)
    const postUser = userWith(saved)
    const fixtures = [seed, baselineUser, postUser]
    const before = structuredClone(fixtures)
    const preferences = fixtures.map((user) => user.preferences)
    const siblingBefore = structuredClone(sibling)
    let resolveRestoration!: (user: User) => void
    const restoration = new Promise<User>((resolve) => { resolveRestoration = resolve })
    const reads: Promise<unknown>[] = []
    const readErrors: unknown[] = []
    const operations: Promise<unknown>[] = []
    const stores = new Set<SessionStore>()
    const owners = new Set<DashboardLayoutOwner>()
    let transportUser = baselineUser
    let restoring = true
    const requests: Array<{ id: string; layout: DashboardLayout; current: boolean }> = []
    ports.me.mockReset().mockImplementation(() => {
      const read = restoring ? restoration : Promise.resolve(transportUser)
      reads.push(read.then(() => undefined, (error: unknown) => { readErrors.push(error) }))
      return read
    })
    ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
      requests.push({ id, layout: structuredClone(layout), current: binding.isCurrent() })
      transportUser = postUser
      return Promise.resolve({ workspaceId: W, layout: saved })
    })
    ports.updateMe.mockReset().mockResolvedValue(seed)
    ports.login.mockReset()
    ports.verify2fa.mockReset()
    ports.register.mockReset()
    const updateMeSpy = vi.spyOn(auth, 'updateMe') // Passthrough.
    const warn = vi.spyOn(console, 'warn') // Passthrough, including lifecycle warnings.
    const error = vi.spyOn(console, 'error')
    const writerSpies: Array<ReturnType<typeof vi.spyOn>> = []
    const contexts = new WeakSet<object>()
    const client = new QueryClient({ defaultOptions: {
      queries: { retry: false, gcTime: 0 }, mutations: { retry: false },
    } })
    const workspace: Workspace = {
      id: W, name: 'Registration retry', kind: 'personal', is_archived: false,
      timezone: null,
      default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
      icon: null, color: null, created_at: '2026-10-03T00:00:00Z',
      created_by_user_id: ACTOR, managed_by_user_id: null, role: 'owner',
      enabled_modules: ['accounts', 'transactions', 'goals'],
    }
    let latestAuth: ReturnType<typeof useAuth> | undefined
    let exposed: DashboardLayoutOwner | null = null
    let showLayout: (() => void) | undefined
    let callbackActive = false
    let generation = -1
    let restorationReads = -1
    type Witness = {
      event: 'setup' | 'cleanup'; active: boolean; owner: DashboardLayoutOwner | null
      user: User | null; token: string | null; loading: boolean
      client: QueryClient; workspace: Workspace | null; generation: number
      reads: number; puts: number
    }
    const witness: Witness[] = []
    const callbackErrors: unknown[] = []
    const aReasons: RetirementReason[] = []
    const bReasons: RetirementReason[] = []
    const exits: Array<{ owner: DashboardLayoutOwner | null; generation: number }> = []
    function ObserveAuth({ children }: { children: ReactNode }) {
      const context = useAuth()
      if (!contexts.has(context)) {
        contexts.add(context)
        writerSpies.push(vi.spyOn(context, 'updateUser')) // Real context passthrough.
      }
      useLayoutEffect(() => { latestAuth = context })
      return children
    }
    function OwnerObserver() {
      const owner = useDashboardLayoutOwner()
      useLayoutEffect(() => {
        exposed = owner
        if (owner) { owners.add(owner); stores.add(owner.store) }
      }, [owner])
      return null
    }
    function ReplayWitness() {
      const owner = useDashboardLayoutOwner()
      const context = useAuth()
      const queryClient = useQueryClient()
      const current = useWorkspace().current
      useLayoutEffect(() => {
        function record(event: Witness['event']) {
          witness.push({ event, active: callbackActive, owner, user: context.user,
            token: context.token, loading: context.isLoading, client: queryClient,
            workspace: current, generation: getDashboardAuthSnapshot(queryClient).generation,
            reads: reads.length, puts: requests.length })
        }
        record('setup')
        return () => { record('cleanup') }
      }, []) // Mount/replay witness only; observes no private provider state.
      return null
    }
    function Shell() {
      const [show, setShow] = useState(false)
      useLayoutEffect(() => { showLayout = () => { setShow(true) } }, [])
      return <QueryClientProvider client={client}><AuthProvider><ObserveAuth>
        <WorkspaceContext.Provider value={{ current: workspace, workspaces: [workspace],
          isLoading: false, switchWorkspace: async () => {}, refresh: async () => {},
          role: workspace.role, canManage: true, canWrite: true,
          enabledModules: ['accounts', 'transactions', 'goals'],
          hasModule: (id) => workspace.enabled_modules.includes(id) }}>
          {show && <DashboardLayoutProvider><OwnerObserver /><ReplayWitness /></DashboardLayoutProvider>}
        </WorkspaceContext.Provider>
      </ObserveAuth></AuthProvider></QueryClientProvider>
    }
    function eligible() {
      expect(latestAuth?.user, `${h} same settled actor`).toBe(seed)
      expect(latestAuth?.token, `${h} same token`).toBe(TOKEN)
      expect(latestAuth?.isLoading, `${h} settled Auth`).toBe(false)
      expect(localStorage.getItem('token'), `${h} token storage`).toBe(TOKEN)
      expect(localStorage.getItem(WORKSPACE_STORAGE_KEY), `${h} workspace storage`).toBe(W)
      expect(getDashboardAuthSnapshot(client).generation, `${h} unchanged generation`).toBe(generation)
    }
    function cleanEvidence() {
      expect(callbackErrors, `${h} retirement exceptions`).toEqual([])
      expect(readErrors, `${h} transport read errors`).toEqual([])
      expect(warn.mock.calls, `${h} console warnings`).toEqual([])
      expect(error.mock.calls, `${h} console errors`).toEqual([])
      expect(updateMeSpy, `${h} external legacy writer`).not.toHaveBeenCalled()
      expect(ports.updateMe, `${h} external port`).not.toHaveBeenCalled()
      expect(writerSpies.length, `${h} real context observed`).toBeGreaterThan(0)
      writerSpies.forEach((spy) => { expect(spy, `${h} context legacy writer`).not.toHaveBeenCalled() })
      expect(fixtures, `${h} frozen fixtures`).toEqual(before)
      fixtures.forEach((user, index) => {
        expect(user.preferences, `${h} preference identity`).toBe(preferences[index])
      })
      expect(sibling, `${h} sibling fixture`).toEqual(siblingBefore)
    }
    function outcome(task: Promise<void>) {
      const handled = task.then(() => ({ ok: true as const }),
        (failure: unknown) => ({ ok: false as const, failure }))
      operations.push(handled)
      return handled
    }
    async function drain() {
      await Promise.allSettled(operations)
      await Promise.allSettled(reads)
      await Promise.all([...stores].map((store) => store.whenIdle()))
      await notifications()
    }
    const storageBefore = ['token', WORKSPACE_STORAGE_KEY]
      .map((key) => [key, localStorage.getItem(key)] as const)
    let unmount: (() => void) | undefined
    let a: DashboardRegistration | null = null
    let replacement: DashboardRegistration | null = null
    try {
      localStorage.setItem('token', TOKEN)
      localStorage.setItem(WORKSPACE_STORAGE_KEY, W)
      unmount = render(<Shell />, { reactStrictMode: strict }).unmount
      await act(async () => { resolveRestoration(seed); await restoration; await drain() })
      await waitFor(() => {
        expect(latestAuth, `${h} restoration`).toMatchObject({ user: seed, token: TOKEN, isLoading: false })
        expect(reads.length, `${h} real restoration count`).toBe(strict ? 2 : 1)
      }, { timeout: 1000 })
      generation = getDashboardAuthSnapshot(client).generation
      restorationReads = reads.length
      restoring = false
      eligible()
      expect(showLayout, `${h} external shell mount handle`).toBeTypeOf('function')
      expect(witness, `${h} initially omitted subtree`).toEqual([])
      act(() => {
        a = registerDashboardOwner(client, generation, Symbol('test A'), (reason) => {
          aReasons.push(reason)
          callbackActive = true
          try {
            // Test-body registration, NOT a lifecycle-triggered flushSync.
            flushSync(() => { showLayout!() })
            exits.push({ owner: exposed, generation: getDashboardAuthSnapshot(client).generation })
          } catch (failure: unknown) { callbackErrors.push(failure) }
          finally { callbackActive = false }
        })
      })
      expect(a, `${h} genuine A acquisition`).not.toBeNull()
      expect(a!.isCurrent(), `${h} replacing capability current`).toBe(true)
      act(() => {
        replacement = registerDashboardOwner(client, generation, Symbol('test B'),
          (reason) => { bReasons.push(reason) }, a!)
        replacement?.release() // Immediately remove the test-owned obstruction.
      })
      expect(replacement, `${h} genuine B acquisition`).not.toBeNull()
      expect(aReasons, `${h} A retirement`).toEqual(['identity'])
      expect(a!.isCurrent(), `${h} A detached`).toBe(false)
      expect(exits, `${h} synchronous denied exposure`).toEqual([{ owner: null, generation }])
      expect(witness.map((entry) => entry.event), `${h} committed replay`)
        .toEqual(strict ? ['setup', 'cleanup', 'setup'] : ['setup'])
      witness.forEach((entry) => {
        expect(entry, `${h} eligible commit inside retirement`).toMatchObject({
          active: true, owner: null, user: seed, token: TOKEN, loading: false,
          generation, reads: restorationReads, puts: 0,
        })
        expect(entry.user, `${h} witness same actor object`).toBe(seed)
        expect(entry.client, `${h} witness same client`).toBe(client)
        expect(entry.workspace, `${h} witness same workspace`).toBe(workspace)
      })
      eligible()
      cleanEvidence()
      expect(reads.length, `${h} no denial GET`).toBe(restorationReads)
      expect(requests, `${h} no denial PUT`).toEqual([])
      await act(async () => { await drain() })
      // Competitor removal is a stimulus barrier, not displaced-provider recovery.
      expect(replacement!.isCurrent(), `${h} B no longer current`).toBe(false)
      expect(bReasons.length, `${h} B retired exactly once`).toBe(1)
      expect(['teardown', 'displaced'], `${h} legitimate B retirement`).toContain(bReasons[0])
      eligible()
      cleanEvidence()
      try {
        await waitFor(() => {
          expect(exposed, `${b} first acquisition after unwind`).not.toBeNull()
          expect(owners.size, `${b} ONE distinct exposed owner`).toBe(1)
          expect(exposed?.authGeneration, `${b} same generation`).toBe(generation)
        }, { timeout: 1000 })
      } finally {
        eligible()
        cleanEvidence()
        expect(reads.length, `${h} no retry GET`).toBe(restorationReads)
        expect(requests, `${h} no retry PUT`).toEqual([])
      }
      const owner = exposed!
      const store = owner.store
      expect(owner.actorId, `${b} actor`).toBe(ACTOR)
      expect(store.getSnapshot(W), `${b} display-only seed`).toMatchObject({
        writable: false, layout: seed.preferences.dashboard_layouts?.[W],
      })
      expect(store.getSnapshot(W).overlay, `${b} empty overlay`).toStrictEqual({})
      expect(() => store.open(W), `${b} seed cannot open`).toThrow(/^Dashboard layout /)
      await act(async () => { expect(await outcome(store.refresh(W)), `${b} refresh`).toEqual({ ok: true }); await drain() })
      expect(store.getSnapshot(W), `${b} authoritative baseline`).toMatchObject({ writable: true, layout: defaults })
      const edit = store.open(W)
      const changed = toPersistedLayout(W, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
      expect(changed, `${h} literal changed draft`).toEqual(saved)
      expect(changed, `${h} genuine edit`).not.toEqual(defaults)
      store.setDraft(edit, changed.items)
      await act(async () => { expect(await outcome(store.finalize(edit)), `${b} finalize`).toEqual({ ok: true }); await drain() })
      expect(requests, `${b} ONE current-bound scoped PUT`).toEqual([{ id: W, layout: saved, current: true }])
      expect(reads.length, `${b} refresh and post-GET`).toBe(restorationReads + 2)
      expect(store.getSnapshot(W), `${b} committed success`).toMatchObject({
        state: 'closed-success', writable: true, pending: false, readPending: false,
        provenance: 'saved', layout: saved, openedBaseline: null, draft: null,
        counters: { success: 1, suppressed: 0 },
      })
      expect(store.getSnapshot(W).overlay, `${b} saved overlay`).toStrictEqual({ [W]: saved })
      expect(store.getSnapshot(SIBLING).layout, `${b} sibling intact`).toEqual(sibling)
      expect(owners.size, `${b} no rescue owner`).toBe(1)
      eligible()
      cleanEvidence()
    } finally {
      resolveRestoration(seed)
      try {
        await act(async () => {
          a?.release()
          replacement?.release()
          await drain()
          unmount?.()
          await drain()
          client.clear()
          await notifications()
        })
        expect(leaseCurrent(a), `${h} A cleanup`).toBe(false)
        expect(leaseCurrent(replacement), `${h} B cleanup`).toBe(false)
        cleanEvidence() // Warnings through unmount/teardown are HARNESS STOP.
      } finally {
        vi.restoreAllMocks()
        storageBefore.forEach(([key, value]) => {
          if (value === null) localStorage.removeItem(key)
          else localStorage.setItem(key, value)
        })
      }
    }
  }
  await scenario(false)
  await scenario(true)
})
