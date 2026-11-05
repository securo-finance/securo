import { useLayoutEffect, useState, type ReactNode } from 'react'
import { expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'

import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, useWorkspace } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { useDashboardLayout, type DashboardLayoutController } from '@/hooks/use-dashboard-layout'
import { getDashboardAuthSnapshot } from '@/lib/dashboard-layout-auth-bridge'
import { getPageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import { WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { validateCommittedDashboardLayout, validateDashboardLayoutRequest, type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding } from '@/lib/dashboard-layout'
import type { Recovery, SessionSnapshot } from '@/lib/dashboard-layout-session'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { createTestQueryClient, i18n, t } from '@/test/utils'
import type { User, Workspace } from '@/types'

const ports = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout, binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  toastError: vi.fn(),
}))
// Real Auth/provider/bridge/client/owner/lease/store/hook; transport and toast only.
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: ports.me, updateMe: ports.updateMe },
  dashboard: { saveLayout: ports.saveLayout },
}))
vi.mock('sonner', () => ({ toast: { error: (message: unknown) => ports.toastError(message) } }))

const ACTOR = '33333333-3333-4333-8333-333333333333'
const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const TOKEN = 'synthetic-selection-recheck-token'
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}
function expectFrozen(value: unknown): void {
  if (value !== null && typeof value === 'object') {
    expect(Object.isFrozen(value)).toBe(true)
    Object.values(value).forEach(expectFrozen)
  }
}
function identities(value: unknown): object[] {
  if (value === null || typeof value !== 'object') return []
  return [value, ...Object.values(value).flatMap(identities)]
}
function required<T>(value: T | null, label: string): T {
  if (value === null) throw new Error(`Missing real witness: ${label}`)
  return value
}
const P1: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'period-transactions', visible: true, order: 4 },
  { id: 'goals-progress', visible: true, order: 5 },
] })
const P2: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'uncategorized-banner', visible: true, order: 0 },
  { id: 'hero-balance', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'period-transactions', visible: true, order: 4 },
  { id: 'goals-progress', visible: true, order: 5 },
] })
const PB0: DashboardLayout = freeze({ version: 1, workspaceId: B, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const PB1: DashboardLayout = freeze({ version: 1, workspaceId: B, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
// Every accepted reply and GET leaf is an independent literal, never a request echo.
const acceptedA: CommittedDashboardLayout = freeze({ workspaceId: A, layout: { version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'period-transactions', visible: true, order: 4 },
  { id: 'goals-progress', visible: true, order: 5 },
] } })
const acceptedB: CommittedDashboardLayout = freeze({ workspaceId: B, layout: { version: 1, workspaceId: B, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] } })
const seed = freeze({ id: ACTOR, email: 'selection-seed@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', caller_extra: { retained: ['seed'] } } })
const postA = freeze({ id: ACTOR, email: 'selection-post-a@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', caller_extra: { retained: ['post-A'] }, dashboard_layouts: { [A]: {
    version: 1, workspaceId: A, leaf_extra: { retained: ['post-A-leaf'] }, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'period-transactions', visible: true, order: 4 },
      { id: 'goals-progress', visible: true, order: 5 },
    ],
  } } } })
const lateA = freeze({ id: ACTOR, email: 'selection-late-a@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false,
  root_extra: { retained: ['late-root'] }, preferences: { language: 'en',
    caller_extra: { retained: ['late-preferences'] }, dashboard_layouts: {
      map_extra: { retained: ['late-map'] }, [A]: {
        version: 1, workspaceId: A, leaf_extra: { retained: ['late-A'] }, items: [
          { id: 'uncategorized-banner', visible: true, order: 0, item_extra: { retained: ['late-A-item'] } },
          { id: 'hero-balance', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'period-transactions', visible: true, order: 4 },
          { id: 'goals-progress', visible: true, order: 5 },
        ],
      }, [B]: {
        version: 1, workspaceId: B, leaf_extra: { retained: ['late-B-poison'] }, items: [
          { id: 'uncategorized-banner', visible: true, order: 0, item_extra: { retained: ['late-B-item'] } },
          { id: 'hero-balance', visible: false, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      },
    },
  } })
const ordinaryB = freeze({ id: ACTOR, email: 'selection-current-b@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false,
  root_extra: { retained: ['current-B-root'] }, preferences: { language: 'en',
    caller_extra: { retained: ['current-B-preferences'] }, dashboard_layouts: {
      map_extra: { retained: ['current-B-map'] }, [A]: {
        version: 1, workspaceId: A, leaf_extra: { retained: ['current-B-A'] }, items: [
          { id: 'hero-balance', visible: true, order: 0, item_extra: { retained: ['current-B-A-item'] } },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'period-transactions', visible: true, order: 4 },
          { id: 'goals-progress', visible: true, order: 5 },
        ],
      }, [B]: {
        version: 1, workspaceId: B, leaf_extra: { retained: ['current-B-B'] },
        item_extra: { retained: ['current-B-B-item'] }, items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      },
    },
  } })
const postB = freeze({ id: ACTOR, email: 'selection-post-b@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false,
  root_extra: { retained: ['post-B-root'] }, preferences: { language: 'en',
    caller_extra: { retained: ['post-B-preferences'] }, dashboard_layouts: {
      map_extra: { retained: ['post-B-map'] }, [A]: {
        version: 1, workspaceId: A, leaf_extra: { retained: ['post-B-A'] }, items: [
          { id: 'hero-balance', visible: true, order: 0, item_extra: { retained: ['post-B-A-item'] } },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'period-transactions', visible: true, order: 4 },
          { id: 'goals-progress', visible: true, order: 5 },
        ],
      }, [B]: {
        version: 1, workspaceId: B, leaf_extra: { retained: ['post-B-B'] },
        item_extra: { retained: ['post-B-B-item'] }, items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: false, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      },
    },
  } })
function workspace(id: string): Workspace {
  return freeze({ id, name: `Synthetic selection ${id}`, kind: 'personal', is_archived: false,
    timezone: null,
    default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null, icon: null, color: null,
    created_at: '2026-10-05T00:00:00Z', created_by_user_id: ACTOR, managed_by_user_id: null,
    role: 'owner', enabled_modules: ['accounts', 'transactions', 'goals'] })
}
const workspaceA = workspace(A), workspaceB = workspace(B)
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })
function controlled<T>(value: T, failure?: unknown) {
  let resolve!: (value: T) => void, reject!: (failure: unknown) => void, started!: () => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  const issuedBarrier = new Promise<void>((yes) => { started = yes })
  return { promise, issuedBarrier, issued: false, settled: false,
    issue() { this.issued = true; started() },
    settle() {
      if (!this.issued || this.settled) return
      this.settled = true
      if (failure === undefined) resolve(value); else reject(failure)
    },
  }
}
type Outcome = { status: 'pending' } | { status: 'fulfilled' } | { status: 'rejected'; failure: unknown }
type Kind = 'fulfilled' | 'saveoffline' | 'staleread'

function harness() {
  const client = createTestQueryClient(), offlineError = freeze(new Error('original save offline'))
  const restoration = controlled<unknown>(seed), entry = controlled<unknown>(seed), post = controlled<unknown>(postA)
  const r1 = controlled<unknown>(lateA), currentB = controlled<unknown>(ordinaryB), postCurrentB = controlled<unknown>(postB)
  const firstPut = controlled(acceptedA), secondPut = controlled(acceptedA, offlineError), thirdPut = controlled(acceptedB)
  const controlledPorts = [restoration, entry, post, r1, currentB, postCurrentB, firstPut, secondPut, thirdPut]
  type Phase = 'auth' | 'entry' | 'ready' | 'save1' | 'post-A' | 'after-A' | 'save2' | 'failed' |
    'recheck-A' | 'recheck-live' | 'select-B' | 'B-live' | 'B-ready' | 'stale-settled' | 'save-B' | 'post-B' | 'done'
  let phase: Phase = 'auth', cleaning = false, mounts = 0, unmounts = 0
  const violations: unknown[] = [], unexpected: unknown[] = [], publications: SessionSnapshot[] = []
  const owners = new Set<DashboardLayoutOwner>(), tasks: Promise<void>[] = [], unsubscribes: Array<() => void> = []
  const observers: Array<{ kind: Kind; outcome: Outcome }> = []
  const writers: Array<ReturnType<typeof vi.spyOn>> = [], contexts = new WeakSet<object>()
  const state: { owner: DashboardLayoutOwner | null; hook: DashboardLayoutController | null;
    auth: ReturnType<typeof useAuth> | null; client: typeof client | null; workspace: Workspace | null;
    select: ((id: string) => Promise<void>) | null } = {
    owner: null, hook: null, auth: null, client: null, workspace: null, select: null,
  }
  const gets: Array<{ phase: Phase; generation: number; storage: string | null; target: string | null;
    owner: DashboardLayoutOwner | null; outcome: Outcome; user: unknown; start: SessionSnapshot | null;
    handled: Promise<void> | null }> = []
  const puts: Array<{ phase: Phase; id: string; layout: DashboardLayout; input: DashboardLayout;
    binding: DashboardRequestBinding; current: boolean; generation: number; storage: string | null;
    target: string | null; owner: DashboardLayoutOwner | null }> = []
  const inputs = [P1, P2, PB0, PB1, acceptedA, acceptedB, seed, postA, lateA, ordinaryB, postB,
    workspaceA, workspaceB, offlineError].map((input) => ({ input, before: structuredClone(input), identities: identities(input) }))
  const storage = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let mounted: { unmount(): void } | null = null
  function observe(task: Promise<unknown>, kind: Kind) {
    const result: { kind: Kind; outcome: Outcome } = { kind, outcome: { status: 'pending' } }
    observers.push(result)
    const handled = task.then(() => { result.outcome = { status: 'fulfilled' } }, (failure: unknown) => {
      result.outcome = { status: 'rejected', failure }
    })
    tasks.push(handled)
    return { result, handled }
  }
  function deny(kind: string): Promise<never> {
    const failure = new Error(`Forbidden ${kind} in ${phase}; GET=${gets.length}, PUT=${puts.length}`)
    violations.push(failure)
    const task = Promise.reject<never>(failure)
    tasks.push(task.then(() => {}, () => {}))
    return task
  }
  function admit(port: ReturnType<typeof controlled<unknown>>, call: (typeof gets)[number]): Promise<User> {
    port.issue()
    call.handled = port.promise.then((value) => { call.user = value; call.outcome = { status: 'fulfilled' } }, (failure: unknown) => {
      call.outcome = { status: 'rejected', failure }
    })
    tasks.push(call.handled)
    if (cleaning) port.settle()
    // Transport boundary only: raw independent fixtures retain every byte/reference/extra.
    return port.promise as Promise<User>
  }
  ports.me.mockReset().mockImplementation(() => {
    const target = state.workspace?.id ?? null
    const call: (typeof gets)[number] = { phase, generation: getDashboardAuthSnapshot(client).generation,
      storage: localStorage.getItem(WORKSPACE_STORAGE_KEY), target, owner: state.owner,
      outcome: { status: 'pending' }, user: null, handled: null,
      start: state.owner?.store.getSnapshot(target ?? A) ?? null }
    gets.push(call)
    // Guards reject immediately BEFORE unresolved admission; cleanup never disables them.
    const expected = gets.length <= 4 ? A : B
    if (call.storage !== expected || (gets.length > 1 && target !== expected) ||
        localStorage.getItem('token') !== TOKEN) return deny('GET target')
    let port: ReturnType<typeof controlled<unknown>>
    if (phase === 'auth' && gets.length === 1 && puts.length === 0) { port = restoration; phase = 'entry' }
    else if (phase === 'entry' && gets.length === 2 && puts.length === 0) { port = entry; phase = 'ready' }
    else if (phase === 'post-A' && gets.length === 3 && puts.length === 1) { port = post; phase = 'after-A' }
    else if (phase === 'recheck-A' && gets.length === 4 && puts.length === 2) { port = r1; phase = 'recheck-live' }
    else if (phase === 'select-B' && gets.length === 5 && puts.length === 2) { port = currentB; phase = 'B-live' }
    else if (phase === 'post-B' && gets.length === 6 && puts.length === 3) { port = postCurrentB; phase = 'done' }
    else return deny('GET')
    return admit(port, call)
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    const call = { phase, id, input: layout, layout: structuredClone(layout), binding,
      current: binding.isCurrent(), generation: getDashboardAuthSnapshot(client).generation,
      storage: localStorage.getItem(WORKSPACE_STORAGE_KEY), target: state.workspace?.id ?? null, owner: state.owner }
    puts.push(call)
    const expected = puts.length <= 2 ? A : B
    if (id !== expected || layout.workspaceId !== expected || call.target !== expected ||
        call.storage !== expected || !call.current || localStorage.getItem('token') !== TOKEN) return deny('PUT target/binding')
    let port: typeof firstPut
    if (phase === 'save1' && puts.length === 1 && gets.length === 2) { port = firstPut; phase = 'post-A' }
    else if (phase === 'save2' && puts.length === 2 && gets.length === 3) { port = secondPut; phase = 'failed' }
    else if (phase === 'save-B' && puts.length === 3 && gets.length === 5) { port = thirdPut; phase = 'post-B' }
    else return deny('PUT')
    port.issue(); observe(port.promise, port === secondPut ? 'saveoffline' : 'fulfilled')
    if (cleaning) port.settle()
    return port.promise
  })
  ports.updateMe.mockReset().mockImplementation(() => deny('legacy PATCH'))
  ports.toastError.mockReset()
  function ObserveAuth({ children }: { children: ReactNode }) {
    const context = useAuth()
    if (!contexts.has(context)) { contexts.add(context); writers.push(vi.spyOn(context, 'updateUser')) }
    useLayoutEffect(() => { state.auth = context })
    return children
  }
  function Probe() {
    const hook = useDashboardLayout(), owner = useDashboardLayoutOwner(), observedClient = useQueryClient()
    const current = useWorkspace().current
    useLayoutEffect(() => {
      state.hook = hook; state.owner = owner; state.client = observedClient; state.workspace = current
      if (owner && !owners.has(owner)) {
        owners.add(owner)
        unsubscribes.push(owner.store.subscribe(() => {
          try { publications.push(owner.store.getSnapshot(A), owner.store.getSnapshot(B)) }
          catch (failure: unknown) { unexpected.push(failure) }
        }))
      }
    })
    useLayoutEffect(() => {
      mounts += 1
      return () => { unmounts += 1; state.hook = null }
    }, [])
    return null
  }
  // Accepted context seam, not a claim that production WorkspaceProvider is tested.
  function WorkspaceHarness() {
    const [current, setCurrent] = useState(workspaceA)
    const switchWorkspace = async (id: string) => {
      if (id !== B) throw new Error('Unexpected workspace selection')
      localStorage.setItem(WORKSPACE_STORAGE_KEY, id)
      setCurrent(workspaceB)
    }
    useLayoutEffect(() => { state.select = switchWorkspace })
    return <WorkspaceContext.Provider value={{ current, workspaces: [workspaceA, workspaceB],
      isLoading: false, role: current.role, canWrite: true, canManage: true,
      enabledModules: ['accounts', 'transactions', 'goals'], hasModule: (id) => current.enabled_modules.includes(id),
      switchWorkspace, refresh: async () => { throw new Error('Unexpected workspace refresh') } }}>
      <DashboardLayoutProvider><Probe /></DashboardLayoutProvider>
    </WorkspaceContext.Provider>
  }
  function Shell() {
    return <I18nextProvider i18n={i18n}><QueryClientProvider client={client}><AuthProvider>
      <ObserveAuth><WorkspaceHarness /></ObserveAuth>
    </AuthProvider></QueryClientProvider></I18nextProvider>
  }
  const hook = () => required(state.hook, 'mounted hook')
  const owner = () => required(state.owner, 'provider owner')
  async function flush() { await act(async () => { await tick(); await tick() }) }
  async function drain() {
    for (let turn = 0; turn < 8; turn += 1) {
      const before = [tasks.length, publications.length, gets.length, puts.length, owners.size, mounts, unmounts]
      await tick(); await tick(); await Promise.all(tasks)
      for (const current of owners) {
        await current.store.whenIdle(); await getPageSessionLease(current.store).whenIdle()
      }
      await tick(); await tick(); await Promise.all(tasks)
      expect(client.isMutating()).toBe(0)
      const after = [tasks.length, publications.length, gets.length, puts.length, owners.size, mounts, unmounts]
      if (after.every((value, index) => value === before[index])) return
    }
    throw new Error('Selection characterization did not reach native fixed point')
  }
  function noGenericWriters() {
    expect(ports.updateMe).not.toHaveBeenCalled(); expect(writers.length).toBeGreaterThan(0)
    writers.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
  }
  function outcome(result: { kind: Kind; outcome: Outcome }) {
    if (result.kind === 'fulfilled') { expect(result.outcome).toStrictEqual({ status: 'fulfilled' }); return }
    expect(result.outcome.status).toBe('rejected')
    if (result.outcome.status !== 'rejected') throw new Error(`Missing ${result.kind} rejection`)
    if (result.kind === 'saveoffline') expect(result.outcome.failure).toBe(offlineError)
    else {
      expect(result.outcome.failure).toBeInstanceOf(Error)
      expect(result.outcome.failure).toEqual(new Error('Dashboard layout read is stale'))
      expect((result.outcome.failure as Error).message).toBe('Dashboard layout read is stale')
      expect(result.outcome.failure).not.toBe(offlineError)
    }
  }
  function preservedInputs() {
    inputs.forEach(({ input, before, identities: captured }) => {
      expect(input).toStrictEqual(before); expectFrozen(input)
      const now = identities(input)
      expect(now).toHaveLength(captured.length)
      now.forEach((identity, index) => { expect(identity).toBe(captured[index]) })
    })
  }
  function evidence() {
    expect(violations).toStrictEqual([]); expect(unexpected).toStrictEqual([])
    noGenericWriters(); expect(client.isMutating()).toBe(0)
    expect(ports.me.mock.calls).toStrictEqual(gets.map(() => []))
    expect(ports.saveLayout).toHaveBeenCalledTimes(puts.length)
    expect(gets).toHaveLength([restoration, entry, post, r1, currentB, postCurrentB].filter((port) => port.issued).length)
    expect(puts).toHaveLength([firstPut, secondPut, thirdPut].filter((port) => port.issued).length)
    observers.forEach(outcome)
    gets.forEach((call) => { expect(call.outcome).toStrictEqual({ status: 'fulfilled' }) })
    preservedInputs(); puts.forEach((call) => { expect(call.input).toStrictEqual(call.layout) })
  }
  function stable(expected: DashboardLayoutOwner, generation: number, current: Workspace) {
    expect(state.owner).toBe(expected); expect([...owners]).toStrictEqual([expected])
    expect(state.client).toBe(client); expect(state.workspace).toBe(current)
    expect(owner().store).toBe(expected.store); expect(expected.actorId).toBe(ACTOR)
    expect(expected.authGeneration).toBe(generation); expect(getDashboardAuthSnapshot(client).generation).toBe(generation)
    expect(state.auth?.user).toBe(seed); expect(state.auth?.token).toBe(TOKEN); expect(state.auth?.isLoading).toBe(false)
    expect(localStorage.getItem('token')).toBe(TOKEN); expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(current.id)
    expect(mounts).toBe(1); expect(unmounts).toBe(0)
    const snapshot = expected.store.getSnapshot(current.id)
    expect(snapshot.actorId).toBe(ACTOR); expect(snapshot.authGeneration).toBe(generation); expect(snapshot.workspaceId).toBe(current.id)
    gets.slice(1).forEach((call, index) => {
      const target = index < 3 ? A : B
      expect(call.generation).toBe(generation); expect(call.target).toBe(target); expect(call.storage).toBe(target)
      expect(call.owner).toBe(expected)
    })
  }
  function request(index: number, expected: DashboardLayout, generation: number) {
    const call = puts[index]!
    expect(call.id).toBe(expected.workspaceId); expect(call.layout).toStrictEqual(expected)
    expect(validateDashboardLayoutRequest(call.id, call.input, DASHBOARD_WIDGETS)).toStrictEqual(expected)
    expect(call.current).toBe(true); expect(call.binding.isCurrent()).toBe(true)
    expect(call.generation).toBe(generation); expect(call.storage).toBe(expected.workspaceId)
    expect(call.target).toBe(expected.workspaceId); expect(call.owner).toBe(owner())
    expect(Reflect.ownKeys(call.layout).sort()).toStrictEqual(['items', 'version', 'workspaceId'])
    call.layout.items.forEach((item) => { expect(Reflect.ownKeys(item).sort()).toStrictEqual(['id', 'order', 'visible']) })
  }
  function cardinality(getCount: number, putCount: number, toastCount: number) {
    expect(gets).toHaveLength(getCount); expect(ports.me).toHaveBeenCalledTimes(getCount)
    expect(puts).toHaveLength(putCount); expect(ports.saveLayout).toHaveBeenCalledTimes(putCount)
    expect(ports.toastError.mock.calls).toStrictEqual(toastCount ? [[t('dashboard.customize.saveError')]] : [])
    noGenericWriters(); expect(violations).toStrictEqual([]); expect(unexpected).toStrictEqual([])
  }
  const onError = (event: ErrorEvent) => { unexpected.push(event.error ?? event.message) }
  const onRejection = (event: PromiseRejectionEvent) => { unexpected.push(event.reason) }
  async function start() {
    window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
    localStorage.setItem('token', TOKEN); localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
    mounted = render(<Shell />, { reactStrictMode: false })
    expect(restoration.issued).toBe(true)
    await act(async () => { restoration.settle(); await tick(); await tick() })
    await flush(); expect(entry.issued).toBe(true)
    await act(async () => { entry.settle(); await drain() }); await flush()
    expect(gets).toHaveLength(2); expect(gets.map((call) => call.user)).toStrictEqual([seed, seed])
  }
  async function cleanup() {
    const failures: unknown[] = []
    async function step(action: () => void | Promise<void>) {
      try { await action() } catch (failure: unknown) { failures.push(failure) }
    }
    await step(async () => {
      cleaning = true; controlledPorts.forEach((port) => { if (port.issued) port.settle() })
      // Late LEGAL postGET settles at admission; forbidden calls still reject/diagnose.
      await act(async () => { await drain() }); await act(async () => { await drain() })
    })
    await step(async () => { await act(async () => { mounted?.unmount(); await drain() }) })
    await step(() => {
      owners.forEach(({ store }) => {
        for (const id of [A, B]) {
          const snapshot = store.getSnapshot(id)
          expect(snapshot.overlay).toStrictEqual({}); expect(snapshot.pending).toBe(false)
          expect(snapshot.readPending).toBe(false); expect(snapshot.openedBaseline).toBeNull()
          expect(snapshot.draft).toBeNull(); expect(snapshot.recovery).toBeNull(); expect(snapshot.writable).toBe(false)
        }
      })
      expect(unmounts).toBe(mounts)
      puts.forEach((call) => { expect(call.binding.isCurrent()).toBe(false) })
    })
    await step(evidence)
    controlledPorts.filter((port) => port.issued).forEach((port) => {
      if (!port.settled) failures.push(new Error('Issued transport remained unsettled'))
    })
    for (const unsubscribe of unsubscribes) await step(unsubscribe)
    await step(() => { client.clear() })
    await step(async () => { await act(async () => { await drain() }) })
    await step(() => { expect(violations).toStrictEqual([]); expect(unexpected).toStrictEqual([]) })
    await step(() => { window.removeEventListener('error', onError); window.removeEventListener('unhandledrejection', onRejection) })
    for (const spy of writers) await step(() => { spy.mockRestore() })
    for (const { key, value } of storage) await step(() => {
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value)
    })
    await step(() => { ports.me.mockReset(); ports.saveLayout.mockReset(); ports.updateMe.mockReset(); ports.toastError.mockReset() })
    return failures
  }
  return { offlineError, start, cleanup, hook, owner, stable, request, evidence, preservedInputs,
    cardinality, outcome, flush, drain, observe, firstPut, secondPut, thirdPut, post, r1, currentB, postCurrentB,
    gets, puts, select: () => required(state.select, 'public workspace switch'),
    phase: () => phase, setPhase: (next: Phase) => { phase = next } }
}

it('rejects stale A recovery READ across committed A-to-B selection while admitting a changed current B save', async () => {
  const h = harness()
  let primary: unknown, failed = false
  let cleanupFailures: unknown[] = []
  try {
    await h.start()
    const owner = h.owner(), store = owner.store, generation = owner.authGeneration
    h.stable(owner, generation, workspaceA); h.evidence(); h.cardinality(2, 0, 0)
    expect(h.phase()).toBe('ready'); expect(store.getSnapshot(A).writable).toBe(true)
    expect(store.getSnapshot(A).counters.success).toBe(0)
    act(() => { h.hook().openCustomize() }); expect(store.retain(A)).toBe('dashboard-session-1')
    act(() => { h.hook().move('period-transactions', 'up') }); expect(h.hook().draft).toStrictEqual(P1)
    expect(validateCommittedDashboardLayout(A, acceptedA, DASHBOARD_WIDGETS)).toStrictEqual(acceptedA)
    h.setPhase('save1'); act(() => { h.hook().closeCustomize(true) })
    const firstTask = store.finalize('dashboard-session-1'), firstObservation = h.observe(firstTask, 'fulfilled')
    expect(store.finalize('dashboard-session-1')).toBe(firstTask)
    await h.flush()
    expect(h.firstPut.issued).toBe(true); h.request(0, P1, generation)
    expect(store.getSnapshot(A).pending).toBe(true)
    await act(async () => { h.firstPut.settle(); await tick(); await tick() }); await h.flush()
    expect(h.post.issued).toBe(true)
    await act(async () => { h.post.settle(); await firstObservation.handled; await h.drain() }); await h.flush()
    h.outcome(firstObservation.result)
    const successful = store.getSnapshot(A)
    expect(successful.layout).toStrictEqual(P1); expect(successful.overlay).toStrictEqual({ [A]: P1 })
    expect(successful.state).toBe('closed-success'); expect(successful.writable).toBe(true)
    expect(successful.pending).toBe(false); expect(successful.readPending).toBe(false)
    expect(successful.counters.success).toBe(1); expect(h.hook().customizeOpen).toBe(false)
    h.stable(owner, generation, workspaceA); h.evidence(); h.cardinality(3, 1, 0)

    act(() => { h.hook().openCustomize() }); act(() => { h.hook().move('hero-balance', 'down') })
    expect(h.hook().draft).toStrictEqual(P2)
    const sessionId = required(store.retain(A), 'second actual retained session')
    expect(sessionId).toBe('dashboard-session-2')
    h.setPhase('save2'); act(() => { h.hook().closeCustomize(true) })
    const originalTask = store.finalize(sessionId), saveObservation = h.observe(originalTask, 'saveoffline')
    const originalDuplicate = store.finalize(sessionId), duplicateSaveObservation = h.observe(originalDuplicate, 'saveoffline')
    expect(originalDuplicate).toBe(originalTask)
    await h.flush(); expect(h.secondPut.issued).toBe(true); h.request(1, P2, generation)
    await act(async () => { h.secondPut.settle(); await saveObservation.handled; await h.drain() }); await h.flush()
    h.outcome(saveObservation.result); h.outcome(duplicateSaveObservation.result); h.cardinality(3, 2, 1)
    const retained = required(store.getSnapshot(A).recovery, 'failed A recovery')
    expect(h.hook().recovery).toBe(retained)
    expect(retained).toStrictEqual({ attemptId: 'dashboard-attempt-2', actorId: ACTOR,
      authGeneration: generation, workspaceId: A, epoch: 2, baseline: P1, submitted: P2,
      category: 'unknown', phase: 'needs-recheck', recheck: null })
    const original = structuredClone(retained), baseline = retained.baseline, submitted = retained.submitted
    const captureIdentities = identities(retained)
    expectFrozen(retained)
    function unchanged(value: Recovery) {
      expect(value).toStrictEqual({ ...original, phase: value.phase, recheck: value.recheck })
      expect(value.baseline).toBe(baseline); expect(value.submitted).toBe(submitted); expectFrozen(value)
      expect(retained).toStrictEqual(original); expectFrozen(retained)
      const currentIdentities = identities(retained)
      expect(currentIdentities).toHaveLength(captureIdentities.length)
      currentIdentities.forEach((identity, index) => { expect(identity).toBe(captureIdentities[index]) })
      expect(store.finalize(sessionId)).toBe(originalTask); h.outcome(saveObservation.result)
      h.outcome(duplicateSaveObservation.result)
    }
    const failedA = store.getSnapshot(A)
    expect(failedA.state).toBe('unknown'); expect(failedA.writable).toBe(false)
    expect(failedA.pending).toBe(false); expect(failedA.readPending).toBe(false)
    expect(failedA.layout).toStrictEqual(P1); expect(failedA.overlay).toStrictEqual({ [A]: P1 })
    expect(failedA.openedBaseline).toStrictEqual(P1); expect(failedA.draft).toStrictEqual(P2)
    expect(failedA.counters).toStrictEqual(successful.counters); expect(h.hook().customizeOpen).toBe(false)
    expect(h.hook().draft).toStrictEqual(P1); unchanged(retained); h.evidence()

    for (const body of [lateA, ordinaryB, postB]) {
      const preferences = body.preferences, map = preferences.dashboard_layouts
      for (const value of [body, preferences, map, map[A], map[B], ...map[A].items, ...map[B].items]) {
        expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
      }
      expect(Object.prototype.hasOwnProperty.call(body, 'id')).toBe(true); expect(body.id).toBe(ACTOR)
      expect(Object.prototype.hasOwnProperty.call(body, 'preferences')).toBe(true)
      expect(Object.prototype.hasOwnProperty.call(preferences, 'dashboard_layouts')).toBe(true)
      expect(Object.prototype.hasOwnProperty.call(map, A)).toBe(true)
      expect(Object.prototype.hasOwnProperty.call(map, B)).toBe(true)
      expect(map[A].version).toBe(1); expect(map[A].workspaceId).toBe(A)
      expect(map[B].version).toBe(1); expect(map[B].workspaceId).toBe(B)
    }
    const rawA = lateA.preferences.dashboard_layouts[A], poisonB = lateA.preferences.dashboard_layouts[B]
    expect(validateDashboardLayoutRequest(A, rawA, DASHBOARD_WIDGETS)).toStrictEqual(P2)
    expect(validateDashboardLayoutRequest(A, rawA, DASHBOARD_WIDGETS)).toStrictEqual(submitted)
    const validPoison = validateDashboardLayoutRequest(B, poisonB, DASHBOARD_WIDGETS)
    expect(validPoison.workspaceId).toBe(B); expect(validPoison).not.toStrictEqual(PB0)
    expect(validPoison).not.toStrictEqual(PB1)
    expect(validateDashboardLayoutRequest(A, ordinaryB.preferences.dashboard_layouts[A], DASHBOARD_WIDGETS)).toStrictEqual(P1)
    expect(validateDashboardLayoutRequest(B, ordinaryB.preferences.dashboard_layouts[B], DASHBOARD_WIDGETS)).toStrictEqual(PB0)
    expect(validateDashboardLayoutRequest(A, postB.preferences.dashboard_layouts[A], DASHBOARD_WIDGETS)).toStrictEqual(P1)
    expect(validateDashboardLayoutRequest(B, postB.preferences.dashboard_layouts[B], DASHBOARD_WIDGETS)).toStrictEqual(PB1)
    expect(rawA).not.toBe(h.puts[1]!.input); expect(rawA.items).not.toBe(h.puts[1]!.input.items)
    expect(ordinaryB.preferences).not.toBe(lateA.preferences); expect(postB.preferences).not.toBe(ordinaryB.preferences)
    h.preservedInputs(); h.setPhase('recheck-A')
    let r1!: Promise<void>, r1Duplicate!: Promise<void>
    let r1Observation!: ReturnType<typeof h.observe>, r1DuplicateObservation!: ReturnType<typeof h.observe>
    act(() => {
      r1 = h.hook().recheckRecovery(retained.attemptId); r1Observation = h.observe(r1, 'staleread')
      r1Duplicate = h.hook().recheckRecovery(retained.attemptId); r1DuplicateObservation = h.observe(r1Duplicate, 'staleread')
    })
    expect(r1Duplicate).toBe(r1); expect(h.r1.issued).toBe(true); h.cardinality(4, 2, 1)
    const r1Start = required(h.gets[3]!.start, 'R1 synchronous GET-start')
    expect(r1Start.recovery?.phase).toBe('rechecking'); expect(r1Start.recovery?.recheck).toBeNull()
    expect(r1Start.readPending).toBe(true); expect(r1Start.writable).toBe(false)
    const rechecking = required(store.getSnapshot(A).recovery, 'R1 synchronous capture')
    unchanged(rechecking); expect(rechecking.phase).toBe('rechecking'); expect(rechecking.recheck).toBeNull()
    await h.r1.issuedBarrier; await h.flush()
    expect(h.hook().recovery).toBe(rechecking); expect(h.phase()).toBe('recheck-live')
    expect(store.getSnapshot(A).readPending).toBe(true); expect(store.getSnapshot(A).writable).toBe(false)
    expect(h.gets[3]!.outcome).toStrictEqual({ status: 'pending' })
    expect(r1Observation.result.outcome).toStrictEqual({ status: 'pending' })
    h.stable(owner, generation, workspaceA)

    h.setPhase('select-B'); await act(async () => { await h.select()(B); await tick(); await tick() })
    await h.flush(); expect(h.currentB.issued).toBe(true); h.cardinality(5, 2, 1)
    expect(h.phase()).toBe('B-live'); h.stable(owner, generation, workspaceB)
    expect(h.r1.settled).toBe(false); expect(h.gets[3]!.outcome).toStrictEqual({ status: 'pending' })
    expect(r1Observation.result.outcome).toStrictEqual({ status: 'pending' })
    expect(r1DuplicateObservation.result.outcome).toStrictEqual({ status: 'pending' })
    expect(store.getSnapshot(A).readPending).toBe(true)
    const retired = required(store.getSnapshot(A).recovery, 'native selection-retired capture')
    unchanged(retired); expect(retired.phase).toBe('needs-recheck'); expect(retired.recheck).toBeNull()
    expect(h.hook().recovery).toBeNull(); expect(h.hook().customizeOpen).toBe(false)
    expect(h.hook().draft).toStrictEqual(PB0); expect(h.hook().draft).not.toStrictEqual(P2)
    expect(h.gets[4]!.target).toBe(B); expect(h.gets[4]!.storage).toBe(B)
    expect(h.gets[4]!.start?.readPending).toBe(true); expect(h.gets[4]!.start?.recovery).toBeNull()
    expect(h.gets[4]!.outcome).toStrictEqual({ status: 'pending' })
    h.puts.slice(0, 2).forEach((call) => { expect(call.binding.isCurrent()).toBe(false) })
    // R1 remains unresolved: never whenIdle/drain here. Await only observed GET5 HTTP,
    // then bounded native microtask/act publications and actual B completion snapshot.
    await act(async () => { h.currentB.settle(); await required(h.gets[4]!.handled, 'GET5 HTTP observer'); await tick(); await tick() })
    await h.flush(); await h.flush()
    expect(h.gets[4]!.outcome).toStrictEqual({ status: 'fulfilled' }); expect(h.gets[4]!.user).toBe(ordinaryB)
    const bBefore = store.getSnapshot(B), aBefore = store.getSnapshot(A)
    expect(bBefore.state).toBe('closed'); expect(bBefore.writable).toBe(true)
    expect(bBefore.pending).toBe(false); expect(bBefore.readPending).toBe(false); expect(bBefore.recovery).toBeNull()
    expect(bBefore.openedBaseline).toBeNull(); expect(bBefore.draft).toBeNull()
    expect(bBefore.layout).toStrictEqual(PB0); expect(bBefore.provenance).toBe('saved')
    expect(bBefore.counters.success).toBe(1); expect(bBefore.overlay).toStrictEqual({ [A]: P1 })
    expect(h.hook().draft).toStrictEqual(PB0); expect(h.hook().status).toBe('saved'); expect(h.hook().customizeOpen).toBe(false)
    expect(aBefore.state).toBe('unknown'); expect(aBefore.readPending).toBe(true); expect(aBefore.pending).toBe(false)
    expect(aBefore.layout).toStrictEqual(P1); expect(aBefore.overlay).toStrictEqual({ [A]: P1 })
    expect(aBefore.recovery).toBe(retired); unchanged(retired)
    // Ordinary GET5 legitimately updates all record leaves/writability; no pre-switch comparison.
    const bContent = structuredClone(bBefore), aContent = structuredClone(aBefore)
    h.stable(owner, generation, workspaceB); h.preservedInputs(); h.cardinality(5, 2, 1)
    h.setPhase('B-ready')
    await act(async () => { h.r1.settle(); await r1Observation.handled; await h.drain() }); await h.flush()
    h.outcome(r1Observation.result); h.outcome(r1DuplicateObservation.result)
    expect(r1Observation.result.outcome).toStrictEqual(r1DuplicateObservation.result.outcome)
    expect(h.gets[3]!.outcome).toStrictEqual({ status: 'fulfilled' }); expect(h.gets[3]!.user).toBe(lateA)
    // STRICT deep public content, never reference identity/private cache revision.
    expect(store.getSnapshot(B)).toStrictEqual(bContent)
    expect(store.getSnapshot(A)).toStrictEqual({ ...aContent, readPending: false })
    expect(store.getSnapshot(A).recovery).toBe(retired); unchanged(retired)
    expect(h.hook().draft).toStrictEqual(PB0); expect(h.hook().recovery).toBeNull()
    expect(h.hook().customizeOpen).toBe(false); h.stable(owner, generation, workspaceB)
    h.evidence(); h.cardinality(5, 2, 1); h.setPhase('stale-settled')

    act(() => { h.hook().openCustomize() }); expect(h.hook().customizeOpen).toBe(true)
    const bSessionId = required(store.retain(B), 'actual new B edit session')
    expect(bSessionId).toBe('dashboard-session-3'); expect(bSessionId).not.toBe(sessionId)
    let toggled = false
    act(() => { toggled = h.hook().toggleVisible('goals-progress') })
    expect(toggled).toBe(true); expect(h.hook().draft).toStrictEqual(PB1)
    expect(validateCommittedDashboardLayout(B, acceptedB, DASHBOARD_WIDGETS)).toStrictEqual(acceptedB)
    h.setPhase('save-B'); act(() => { h.hook().closeCustomize(true) })
    const bTask = store.finalize(bSessionId), bObservation = h.observe(bTask, 'fulfilled')
    expect(store.finalize(bSessionId)).toBe(bTask)
    await h.flush(); expect(h.thirdPut.issued).toBe(true); h.request(2, PB1, generation)
    h.cardinality(5, 3, 1); h.stable(owner, generation, workspaceB)
    expect(h.thirdPut.settled).toBe(false); expect(h.puts[2]!.binding.isCurrent()).toBe(true)
    expect(store.getSnapshot(B).pending).toBe(true); expect(store.getSnapshot(B).recovery).toBeNull()
    expect(acceptedB.layout).not.toBe(h.puts[2]!.input)
    await h.flush(); expect(h.puts[2]!.binding.isCurrent()).toBe(true)
    await act(async () => { h.thirdPut.settle(); await tick(); await tick() }); await h.flush()
    expect(h.postCurrentB.issued).toBe(true); expect(h.postCurrentB.settled).toBe(false)
    // Successful native session consumption ends the ephemeral write binding BEFORE GET6.
    expect(h.puts[2]!.binding.isCurrent()).toBe(false)
    expect(store.getSnapshot(B).state).toBe('closed-success'); expect(store.getSnapshot(B).writable).toBe(true)
    expect(store.getSnapshot(B).readPending).toBe(true); h.stable(owner, generation, workspaceB)
    expect(bObservation.result.outcome).toStrictEqual({ status: 'pending' }); h.cardinality(6, 3, 1)
    await act(async () => { h.postCurrentB.settle(); await bObservation.handled; await h.drain() }); await h.flush()
    const finishedB = store.getSnapshot(B)
    expect(finishedB.state).toBe('closed-success'); expect(finishedB.writable).toBe(true)
    expect(finishedB.layout).toStrictEqual(PB1); expect(finishedB.overlay).toStrictEqual({ [A]: P1, [B]: PB1 })
    expect(finishedB.counters).toStrictEqual({ ...successful.counters, success: 2 })
    expect(finishedB.pending).toBe(false); expect(finishedB.readPending).toBe(false); expect(finishedB.recovery).toBeNull()
    expect(finishedB.openedBaseline).toBeNull(); expect(finishedB.draft).toBeNull(); expect(finishedB.provenance).toBe('saved')
    expect(h.hook().customizeOpen).toBe(false); expect(h.hook().draft).toStrictEqual(PB1)
    expect(store.getSnapshot(A).recovery).toBe(retired); unchanged(retired)
    expect(store.getSnapshot(A).state).toBe('unknown'); expect(store.getSnapshot(A).layout).toStrictEqual(P1)
    expect(store.getSnapshot(A).openedBaseline).toStrictEqual(P1); expect(store.getSnapshot(A).draft).toStrictEqual(P2)
    expect(store.getSnapshot(A).readPending).toBe(false); expect(store.getSnapshot(A).pending).toBe(false)
    h.puts.forEach((call) => { expect(call.binding.isCurrent()).toBe(false) })
    h.stable(owner, generation, workspaceB); h.outcome(bObservation.result); h.evidence(); h.cardinality(6, 3, 1)
    expect(h.gets.map((call) => call.phase)).toStrictEqual(['auth', 'entry', 'post-A', 'recheck-A', 'select-B', 'post-B'])
    expect(h.gets.map((call) => call.storage)).toStrictEqual([A, A, A, A, B, B])
    expect(h.gets.map((call) => call.user)).toStrictEqual([seed, seed, postA, lateA, ordinaryB, postB])
    expect(h.puts.map((call) => call.phase)).toStrictEqual(['save1', 'save2', 'save-B'])
    expect(h.puts.map((call) => call.id)).toStrictEqual([A, A, B]); expect(h.phase()).toBe('done')
  } catch (failure: unknown) { primary = failure; failed = true }
  finally { cleanupFailures = await h.cleanup() }
  if (failed) {
    if (cleanupFailures.length) console.error('Selection cleanup diagnostics; primary preserved', cleanupFailures)
    throw primary
  }
  if (cleanupFailures.length) throw new AggregateError(cleanupFailures, 'Selection cleanup failed')
})
