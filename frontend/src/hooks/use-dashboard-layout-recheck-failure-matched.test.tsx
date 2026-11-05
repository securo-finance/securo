import { useLayoutEffect, type ReactNode } from 'react'
import { expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'

import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext, useWorkspace } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { useDashboardLayoutOwner } from '@/contexts/dashboard-layout-context'
import { useDashboardLayout, type DashboardLayoutController } from '@/hooks/use-dashboard-layout'
import { getDashboardAuthSnapshot } from '@/lib/dashboard-layout-auth-bridge'
import { getPageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import { WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { validateCommittedDashboardLayout, validateDashboardLayoutRequest, type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding } from '@/lib/dashboard-layout'
import type { Recovery, RecoveryRead, SessionSnapshot } from '@/lib/dashboard-layout-session'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { createTestQueryClient, i18n, t } from '@/test/utils'
import type { User, Workspace } from '@/types'

const ports = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout, binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  toastError: vi.fn(),
}))
// Real Auth, bridge, provider, lease, store and hook; substitution ends at transport.
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: ports.me, updateMe: ports.updateMe },
  dashboard: { saveLayout: ports.saveLayout },
}))
vi.mock('sonner', () => ({ toast: { error: (message: unknown) => ports.toastError(message) } }))

const ACTOR = '33333333-3333-4333-8333-333333333333'
const A = '11111111-1111-4111-8111-111111111111'
const TOKEN = 'synthetic-failure-matched-token'
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
function user(preferences: NonNullable<User['preferences']> & Record<string, unknown>): User {
  return { id: ACTOR, email: 'failure-matched@example.invalid', is_active: true,
    is_superuser: false, is_verified: true, is_2fa_enabled: false, preferences }
}
// Independent strict literals, never the request or a derived accepted-response echo.
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
const accepted: CommittedDashboardLayout = freeze({ workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} })
const seed = freeze(user({ language: 'en', caller_extra: { retained: ['seed'] } }))
const postGet = freeze(user({ language: 'en', caller_extra: { retained: ['post'] }, dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} } }))
const freshGet = freeze(user({ language: 'en', caller_extra: { retained: ['fresh-matched'] }, dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'uncategorized-banner', visible: true, order: 0 },
    { id: 'hero-balance', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} } }))
const workspace: Workspace = freeze({ id: A, name: 'Synthetic failure matched', kind: 'personal',
  timezone: null,
  is_archived: false, default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null,
  icon: null, color: null, created_at: '2026-10-05T00:00:00Z', created_by_user_id: ACTOR,
  managed_by_user_id: null, role: 'owner', enabled_modules: ['accounts', 'transactions', 'goals'] })
const tick = () => new Promise<void>((resolve) => { queueMicrotask(resolve) })
function required<T>(value: T | null, label: string): T {
  if (value === null) throw new Error(`Missing real witness: ${label}`)
  return value
}
function controlled<T>(value: T, failure?: unknown) {
  let resolve!: (value: T) => void
  let reject!: (failure: unknown) => void
  let started!: () => void
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
type Kind = 'fulfilled' | 'saveoffline' | 'failedrecheck'

function harness() {
  const client = createTestQueryClient()
  const offlineError = freeze(new Error('original save offline'))
  const readOfflineError = freeze(new Error('distinct recheck read offline'))
  const restoration = controlled(seed), entry = controlled(seed), post = controlled(postGet)
  const firstPut = controlled(accepted), secondPut = controlled(accepted, offlineError)
  const failedRead = controlled(seed, readOfflineError), fresh = controlled(freshGet)
  const controlledPorts = [restoration, entry, firstPut, post, secondPut, failedRead, fresh]
  type Phase = 'auth' | 'entry' | 'ready' | 'save1' | 'post-get' | 'after-save1' | 'save2' | 'failed' |
    'failed-recheck' | 'failed-read-live' | 'recheck-failed' | 'fresh-matched' | 'fresh-live' | 'matched'
  let phase: Phase = 'auth', cleaning = false
  const violations: unknown[] = [], unexpected: unknown[] = [], publications: SessionSnapshot[] = []
  const owners = new Set<DashboardLayoutOwner>()
  const tasks: Promise<void>[] = [], unsubscribes: Array<() => void> = []
  const observers: Array<{ kind: Kind; outcome: Outcome }> = []
  const writers: Array<ReturnType<typeof vi.spyOn>> = [], contexts = new WeakSet<object>()
  const state: { owner: DashboardLayoutOwner | null; hook: DashboardLayoutController | null;
    auth: ReturnType<typeof useAuth> | null; client: typeof client | null; workspace: Workspace | null } = {
    owner: null, hook: null, auth: null, client: null, workspace: null,
  }
  const gets: Array<{ phase: Phase; generation: number; storage: string | null; kind: Kind;
    outcome: Outcome; user: User | null; start: SessionSnapshot | null }> = []
  const puts: Array<{ phase: Phase; id: string; layout: DashboardLayout; input: DashboardLayout;
    binding: DashboardRequestBinding; current: boolean; generation: number; storage: string | null }> = []
  const inputs = [P1, P2, seed, postGet, freshGet, accepted, workspace, offlineError, readOfflineError].map((input) => ({
    input, before: structuredClone(input), identities: identities(input),
  }))
  const storage = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let mounted: { unmount(): void } | null = null
  function observe(task: Promise<unknown>, kind: Kind) {
    const result: { kind: Kind; outcome: Outcome } = { kind, outcome: { status: 'pending' } }
    observers.push(result)
    // Handlers capture only; every outcome assertion is outside production-isolated callbacks.
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
  ports.me.mockReset().mockImplementation(() => {
    const call: (typeof gets)[number] = { phase, generation: getDashboardAuthSnapshot(client).generation,
      storage: localStorage.getItem(WORKSPACE_STORAGE_KEY), kind: 'fulfilled',
      outcome: { status: 'pending' }, user: null, start: state.owner?.store.getSnapshot(A) ?? null }
    gets.push(call)
    let port: ReturnType<typeof controlled<User>>
    // Exact phase, method and cardinality admit a port before any unresolved return.
    if (phase === 'auth' && gets.length === 1 && puts.length === 0) { port = restoration; phase = 'entry' }
    else if (phase === 'entry' && gets.length === 2 && puts.length === 0) { port = entry; phase = 'ready' }
    else if (phase === 'post-get' && gets.length === 3 && puts.length === 1) { port = post; phase = 'after-save1' }
    else if (phase === 'failed-recheck' && gets.length === 4 && puts.length === 2) {
      port = failedRead; call.kind = 'failedrecheck'; phase = 'failed-read-live'
    } else if (phase === 'fresh-matched' && gets.length === 5 && puts.length === 2) { port = fresh; phase = 'fresh-live' }
    else return deny('GET')
    port.issue()
    tasks.push(port.promise.then((value) => { call.user = value; call.outcome = { status: 'fulfilled' } }, (failure: unknown) => {
      call.outcome = { status: 'rejected', failure }
    }))
    // Expected R1 transport rejection is NOT an unexpected global error or store outcome.
    if (cleaning) port.settle()
    return port.promise
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    puts.push({ phase, id, input: layout, layout: structuredClone(layout), binding,
      current: binding.isCurrent(), generation: getDashboardAuthSnapshot(client).generation,
      storage: localStorage.getItem(WORKSPACE_STORAGE_KEY) })
    let port: typeof firstPut
    if (phase === 'save1' && puts.length === 1 && gets.length === 2) { port = firstPut; phase = 'post-get' }
    else if (phase === 'save2' && puts.length === 2 && gets.length === 3) { port = secondPut; phase = 'failed' }
    else return deny('PUT')
    port.issue()
    observe(port.promise, port === secondPut ? 'saveoffline' : 'fulfilled')
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
          try { publications.push(owner.store.getSnapshot(A)) }
          catch (failure: unknown) { unexpected.push(failure) }
        }))
      }
    })
    useLayoutEffect(() => () => { state.hook = null }, [])
    return null
  }
  function Shell() {
    return <I18nextProvider i18n={i18n}><QueryClientProvider client={client}><AuthProvider>
      <ObserveAuth><WorkspaceContext.Provider value={{ current: workspace, workspaces: [workspace],
        isLoading: false, role: 'owner', canWrite: true, canManage: true,
        enabledModules: ['accounts', 'transactions', 'goals'], hasModule: (id) => workspace.enabled_modules.includes(id),
        switchWorkspace: async () => { throw new Error('Unexpected workspace switch') },
        refresh: async () => { throw new Error('Unexpected workspace refresh') } }}>
        <DashboardLayoutProvider><Probe /></DashboardLayoutProvider>
      </WorkspaceContext.Provider></ObserveAuth>
    </AuthProvider></QueryClientProvider></I18nextProvider>
  }
  const hook = () => required(state.hook, 'mounted hook')
  const owner = () => required(state.owner, 'provider owner')
  async function flush() { await act(async () => { await tick(); await tick() }) }
  async function drain() {
    for (let turn = 0; turn < 8; turn += 1) {
      const before = [tasks.length, publications.length, gets.length, puts.length, owners.size]
      await tick(); await tick(); await Promise.all(tasks)
      for (const current of owners) {
        await current.store.whenIdle(); await getPageSessionLease(current.store).whenIdle()
      }
      await tick(); await tick(); await Promise.all(tasks)
      expect(client.isMutating()).toBe(0)
      const after = [tasks.length, publications.length, gets.length, puts.length, owners.size]
      if (after.every((value, index) => value === before[index])) return
    }
    throw new Error('Failure-to-matched characterization did not reach native fixed point')
  }
  function noGenericWriters() {
    expect(ports.updateMe).not.toHaveBeenCalled()
    expect(writers.length).toBeGreaterThan(0)
    writers.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
  }
  function outcome(result: { kind: Kind; outcome: Outcome }) {
    if (result.kind === 'fulfilled') expect(result.outcome).toStrictEqual({ status: 'fulfilled' })
    else {
      expect(result.outcome.status).toBe('rejected')
      if (result.outcome.status !== 'rejected') throw new Error(`Missing ${result.kind} rejection`)
      if (result.kind === 'saveoffline') expect(result.outcome.failure).toBe(offlineError)
      else {
        expect(result.outcome.failure).toBeInstanceOf(Error)
        expect(result.outcome.failure).not.toBe(readOfflineError)
        expect(result.outcome.failure).not.toBe(offlineError)
        expect(result.outcome.failure).toHaveProperty('message', 'Dashboard layout refresh did not establish a current writable target')
      }
    }
  }
  function evidence() {
    expect(violations).toStrictEqual([]); expect(unexpected).toStrictEqual([])
    noGenericWriters(); expect(client.isMutating()).toBe(0)
    expect(ports.me.mock.calls).toStrictEqual(gets.map(() => []))
    expect(ports.saveLayout).toHaveBeenCalledTimes(puts.length)
    expect(gets).toHaveLength([restoration, entry, post, failedRead, fresh].filter((port) => port.issued).length)
    expect(puts).toHaveLength([firstPut, secondPut].filter((port) => port.issued).length)
    observers.forEach(outcome)
    gets.forEach((call) => {
      if (call.kind === 'failedrecheck') {
        expect(call.phase).toBe('failed-recheck'); expect(call.outcome.status).toBe('rejected')
        if (call.outcome.status !== 'rejected') throw new Error('Missing expected R1 HTTP failure')
        expect(call.outcome.failure).toBe(readOfflineError); expect(call.user).toBeNull()
      } else expect(call.outcome).toStrictEqual({ status: 'fulfilled' })
    })
    expect(gets.filter((call) => call.outcome.status === 'rejected')).toStrictEqual(
      failedRead.settled ? [gets[3]] : [],
    )
    inputs.forEach(({ input, before, identities: captured }) => {
      expect(input).toStrictEqual(before); expectFrozen(input)
      const now = identities(input)
      expect(now).toHaveLength(captured.length)
      now.forEach((identity, index) => { expect(identity).toBe(captured[index]) })
    })
    puts.forEach((call) => { expect(call.input).toStrictEqual(call.layout) })
  }
  function stable(expected: DashboardLayoutOwner, generation: number) {
    expect(state.owner).toBe(expected); expect([...owners]).toStrictEqual([expected])
    expect(state.client).toBe(client); expect(state.workspace).toBe(workspace)
    expect(owner().store).toBe(expected.store)
    expect(expected.actorId).toBe(ACTOR); expect(expected.authGeneration).toBe(generation)
    expect(getDashboardAuthSnapshot(client).generation).toBe(generation)
    expect(state.auth?.user).toBe(seed); expect(state.auth?.token).toBe(TOKEN)
    expect(localStorage.getItem('token')).toBe(TOKEN)
    expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(A)
    const snapshot = expected.store.getSnapshot(A)
    expect(snapshot.actorId).toBe(ACTOR); expect(snapshot.authGeneration).toBe(generation)
    expect(snapshot.workspaceId).toBe(A)
    gets.forEach((call) => { expect(call.generation).toBe(generation); expect(call.storage).toBe(A) })
  }
  function request(index: number, expected: DashboardLayout, generation: number) {
    const call = puts[index]!
    expect(call.id).toBe(A); expect(call.layout).toStrictEqual(expected)
    expect(call.current).toBe(true); expect(call.binding.isCurrent()).toBe(true)
    expect(call.generation).toBe(generation); expect(call.storage).toBe(A)
    expect(Reflect.ownKeys(call.layout).sort()).toStrictEqual(['items', 'version', 'workspaceId'])
    call.layout.items.forEach((item) => {
      expect(Reflect.ownKeys(item).sort()).toStrictEqual(['id', 'order', 'visible'])
    })
  }
  function cardinality(getCount: number) {
    expect(gets).toHaveLength(getCount); expect(ports.me).toHaveBeenCalledTimes(getCount)
    expect(puts).toHaveLength(2); expect(ports.saveLayout).toHaveBeenCalledTimes(2)
    expect(ports.toastError.mock.calls).toStrictEqual([[t('dashboard.customize.saveError')]])
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
    await act(async () => { entry.settle(); await drain() })
    await flush()
    expect(gets).toHaveLength(2); expect(gets.map((call) => call.user)).toStrictEqual([seed, seed])
  }
  async function cleanup() {
    const failures: unknown[] = []
    async function step(action: () => void | Promise<void>) {
      try { await action() } catch (failure: unknown) { failures.push(failure) }
    }
    await step(async () => {
      cleaning = true
      controlledPorts.forEach((port) => { if (port.issued) port.settle() })
      // A late legal postGET settles at admission, never by relaxed deny policy.
      await act(async () => { await drain() })
      await act(async () => { await drain() })
    })
    await step(async () => { await act(async () => { mounted?.unmount(); await drain() }) })
    await step(() => {
      owners.forEach(({ store }) => {
        const snapshot = store.getSnapshot(A)
        expect(snapshot.overlay).toStrictEqual({})
        expect(snapshot.pending).toBe(false); expect(snapshot.readPending).toBe(false)
        expect(snapshot.openedBaseline).toBeNull(); expect(snapshot.draft).toBeNull()
        expect(snapshot.writable).toBe(false); expect(snapshot.recovery).toBeNull()
      })
    })
    await step(evidence)
    controlledPorts.filter((port) => port.issued).forEach((port) => {
      if (!port.settled) failures.push(new Error('Issued transport remained unsettled'))
    })
    for (const unsubscribe of unsubscribes) await step(unsubscribe)
    await step(() => { client.clear() })
    await step(async () => { await act(async () => { await drain() }) })
    await step(() => { window.removeEventListener('error', onError); window.removeEventListener('unhandledrejection', onRejection) })
    for (const spy of writers) await step(() => { spy.mockRestore() })
    for (const { key, value } of storage) await step(() => {
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value)
    })
    await step(() => { ports.me.mockReset(); ports.saveLayout.mockReset(); ports.updateMe.mockReset(); ports.toastError.mockReset() })
    return failures
  }
  return { offlineError, readOfflineError, start, cleanup, hook, owner, stable, request, evidence,
    cardinality, outcome, flush, drain, observe, firstPut, secondPut, post, failedRead, fresh, gets, puts,
    phase: () => phase, setPhase: (next: Phase) => { phase = next } }
}

it('keeps original offline P2 recovery through failed recheck then fresh matched READ on the same owner without replay or write unlock', async () => {
  const h = harness()
  let primary: unknown, failed = false
  let cleanupFailures: unknown[] = []
  try {
    await h.start()
    const owner = h.owner(), store = owner.store, generation = owner.authGeneration
    h.stable(owner, generation); h.evidence()
    expect(h.phase()).toBe('ready')
    expect(store.getSnapshot(A).writable).toBe(true)
    expect(store.getSnapshot(A).counters.success).toBe(0)
    expect(store.getSnapshot(A).recovery).toBeNull()
    expect(store.getSnapshot(A).pending).toBe(false); expect(store.getSnapshot(A).readPending).toBe(false)
    act(() => { h.hook().openCustomize() })
    expect(store.retain(A)).toBe('dashboard-session-1')
    act(() => { h.hook().move('period-transactions', 'up') })
    expect(h.hook().draft).toStrictEqual(P1)
    expect(validateCommittedDashboardLayout(A, accepted, DASHBOARD_WIDGETS)).toStrictEqual(accepted)
    expect(accepted.layout).not.toBe(P1)
    h.setPhase('save1')
    act(() => { h.hook().closeCustomize(true) })
    await h.flush()
    expect(h.firstPut.issued).toBe(true); expect(h.puts).toHaveLength(1)
    h.request(0, P1, generation); expect(store.getSnapshot(A).pending).toBe(true)
    await act(async () => { h.firstPut.settle(); await tick(); await tick() })
    await h.flush(); expect(h.post.issued).toBe(true)
    await act(async () => { h.post.settle(); await h.drain() })
    await h.flush()
    const successful = store.getSnapshot(A)
    expect(h.gets).toHaveLength(3); expect(h.gets[2]!.user).toBe(postGet)
    expect(successful.layout).toStrictEqual(P1); expect(successful.overlay).toStrictEqual({ [A]: P1 })
    expect(successful.state).toBe('closed-success'); expect(successful.writable).toBe(true)
    expect(successful.pending).toBe(false); expect(successful.readPending).toBe(false)
    expect(successful.counters.success).toBe(1); expect(h.hook().customizeOpen).toBe(false)
    expect(ports.toastError).not.toHaveBeenCalled(); h.stable(owner, generation); h.evidence()

    act(() => { h.hook().openCustomize() })
    act(() => { h.hook().move('hero-balance', 'down') })
    expect(h.hook().draft).toStrictEqual(P2)
    const sessionId = required(store.retain(A), 'second actual retained session')
    expect(sessionId).toBe('dashboard-session-2')
    h.setPhase('save2')
    act(() => { h.hook().closeCustomize(true) })
    await h.flush()
    expect(h.secondPut.issued).toBe(true); expect(h.puts).toHaveLength(2)
    h.request(1, P2, generation); expect(store.getSnapshot(A).pending).toBe(true)
    const originalTask = store.finalize(sessionId)
    const saveObservation = h.observe(originalTask, 'saveoffline')
    expect(store.finalize(sessionId)).toBe(originalTask)
    await act(async () => { h.secondPut.settle(); await saveObservation.handled; await h.drain() })
    await h.flush(); h.outcome(saveObservation.result)
    expect(saveObservation.result.outcome).toStrictEqual({ status: 'rejected', failure: h.offlineError })
    expectFrozen(h.offlineError); expect(h.readOfflineError).not.toBe(h.offlineError)
    h.cardinality(3)
    function recovery(): Recovery {
      const value = required(store.getSnapshot(A).recovery, 'public store recovery')
      expect(h.hook().recovery).toBe(value)
      return value
    }
    const retained = recovery()
    expect(retained).toStrictEqual({ attemptId: 'dashboard-attempt-2', actorId: ACTOR,
      authGeneration: generation, workspaceId: A, epoch: 2, baseline: P1, submitted: P2,
      category: 'unknown', phase: 'needs-recheck', recheck: null })
    expectFrozen(retained)
    const original = structuredClone(retained)
    const immutable = { attemptId: retained.attemptId, actorId: retained.actorId,
      authGeneration: retained.authGeneration, workspaceId: retained.workspaceId,
      epoch: retained.epoch, baseline: retained.baseline, submitted: retained.submitted, category: retained.category }
    function unchanged(value: Recovery) {
      expect(value).toStrictEqual({ ...original, phase: value.phase, recheck: value.recheck })
      expect(value.attemptId).toBe(immutable.attemptId); expect(value.actorId).toBe(immutable.actorId)
      expect(value.authGeneration).toBe(immutable.authGeneration); expect(value.workspaceId).toBe(immutable.workspaceId)
      expect(value.epoch).toBe(immutable.epoch); expect(value.category).toBe(immutable.category)
      expect(value.baseline).toBe(immutable.baseline); expect(value.submitted).toBe(immutable.submitted)
      expectFrozen(value); expect(retained).toStrictEqual(original); expectFrozen(retained)
    }
    function readonly(readPending: boolean) {
      const snapshot = store.getSnapshot(A)
      expect(snapshot.state).toBe('unknown'); expect(snapshot.writable).toBe(false)
      expect(snapshot.pending).toBe(false); expect(snapshot.readPending).toBe(readPending)
      expect(snapshot.counters).toStrictEqual(successful.counters)
      expect(snapshot.counters.success).toBe(1)
      expect(snapshot.overlay).toStrictEqual({ [A]: P1 }); expect(snapshot.layout).toStrictEqual(P1)
      expect(snapshot.openedBaseline).toStrictEqual(P1); expect(snapshot.draft).toStrictEqual(P2)
      expect(h.hook().draft).toStrictEqual(P1); expect(h.hook().customizeOpen).toBe(false)
      h.stable(owner, generation)
    }
    readonly(false); h.evidence()

    // No open/retain/new ticket: completed failed session remains the sole capture.
    h.setPhase('failed-recheck')
    let r1!: Promise<void>, r1Duplicate!: Promise<void>
    let r1Observation!: ReturnType<typeof h.observe>
    act(() => {
      r1 = h.hook().recheckRecovery(retained.attemptId)
      r1Observation = h.observe(r1, 'failedrecheck')
      r1Duplicate = h.hook().recheckRecovery(retained.attemptId)
      h.observe(r1Duplicate, 'failedrecheck')
    })
    expect(r1Duplicate).toBe(r1)
    expect(h.failedRead.issued).toBe(true); h.cardinality(4)
    const r1Start = required(h.gets[3]!.start, 'R1 synchronous GET-start snapshot')
    expect(r1Start.recovery?.phase).toBe('rechecking'); expect(r1Start.recovery?.recheck).toBeNull()
    expect(r1Start.readPending).toBe(true); expect(r1Start.writable).toBe(false)
    const r1Synchronous = required(store.getSnapshot(A).recovery, 'synchronous R1 recovery')
    expect(r1Synchronous.phase).toBe('rechecking'); expect(r1Synchronous.recheck).toBeNull()
    unchanged(r1Synchronous)
    await h.failedRead.issuedBarrier
    await h.flush() // Only publication microtasks; NEVER drain unresolved R1.
    expect(h.phase()).toBe('failed-read-live')
    const r1Live = recovery()
    expect(r1Live).toBe(r1Synchronous); expect(r1Live.phase).toBe('rechecking')
    expect(r1Live.recheck).toBeNull(); unchanged(r1Live); readonly(true); h.cardinality(4)
    expect(h.gets[3]!.outcome).toStrictEqual({ status: 'pending' })
    expect(r1Observation.result.outcome).toStrictEqual({ status: 'pending' })
    await act(async () => { h.failedRead.settle(); await r1Observation.handled; await h.drain() })
    await h.flush()
    h.outcome(r1Observation.result)
    const failedTransport = h.gets[3]!.outcome
    expect(failedTransport.status).toBe('rejected')
    if (failedTransport.status !== 'rejected') throw new Error('R1 HTTP must reject distinctly')
    expect(failedTransport.failure).toBe(h.readOfflineError)
    const failedCapture = recovery()
    expect(failedCapture.phase).toBe('recheck-failed'); expect(failedCapture.recheck).toBeNull()
    unchanged(failedCapture); readonly(false); h.cardinality(4); h.evidence()
    expect(store.finalize(sessionId)).toBe(originalTask)
    h.outcome(saveObservation.result)
    h.setPhase('recheck-failed')

    // Strict raw independent P2, qualified before display repair can mask anything.
    const preferences = required(freshGet.preferences ?? null, 'fresh own preferences')
    const map = required(preferences.dashboard_layouts ?? null, 'fresh own map')
    const rawP2 = map[A]!
    expect(Object.getPrototypeOf(freshGet)).toBe(Object.prototype)
    expect(Object.getPrototypeOf(preferences)).toBe(Object.prototype)
    expect(Object.getPrototypeOf(map)).toBe(Object.prototype)
    expect(Object.prototype.hasOwnProperty.call(freshGet, 'id')).toBe(true)
    expect(Object.prototype.hasOwnProperty.call(freshGet, 'preferences')).toBe(true)
    expect(freshGet.id).toBe(ACTOR)
    expect(Object.prototype.hasOwnProperty.call(preferences, 'dashboard_layouts')).toBe(true)
    expect(Reflect.ownKeys(map)).toStrictEqual([A])
    expect(validateDashboardLayoutRequest(A, rawP2, DASHBOARD_WIDGETS)).toStrictEqual(P2)
    expect(rawP2).toStrictEqual(P2); expect(rawP2).not.toBe(P2)
    expect(rawP2.items).not.toBe(P2.items)
    expect(rawP2.items.map((item) => item.id)).toStrictEqual([
      'uncategorized-banner', 'hero-balance', 'spending-charts', 'balance-evolution', 'period-transactions', 'goals-progress',
    ])
    expect(freshGet).not.toBe(postGet); expect(freshGet.preferences).not.toBe(postGet.preferences)
    expect(preferences).toHaveProperty('caller_extra', { retained: ['fresh-matched'] })
    expect(postGet.preferences).toHaveProperty('caller_extra', { retained: ['post'] })
    expect(seed.preferences).toHaveProperty('caller_extra', { retained: ['seed'] })
    expect(accepted.layout).not.toBe(h.puts[0]!.input)
    expect(rawP2).not.toBe(h.puts[1]!.input)
    h.setPhase('fresh-matched')
    let freshTask!: Promise<void>, freshDuplicate!: Promise<void>
    let freshObservation!: ReturnType<typeof h.observe>
    act(() => {
      freshTask = h.hook().recheckRecovery(retained.attemptId)
      freshObservation = h.observe(freshTask, 'fulfilled')
      freshDuplicate = h.hook().recheckRecovery(retained.attemptId)
      h.observe(freshDuplicate, 'fulfilled')
    })
    expect(freshTask).not.toBe(r1); expect(freshDuplicate).toBe(freshTask)
    expect(h.fresh.issued).toBe(true); h.cardinality(5)
    const freshStart = required(h.gets[4]!.start, 'fresh synchronous GET-start snapshot')
    expect(freshStart.recovery?.phase).toBe('rechecking'); expect(freshStart.recovery?.recheck).toBeNull()
    expect(freshStart.readPending).toBe(true); expect(freshStart.writable).toBe(false)
    const freshSynchronous = required(store.getSnapshot(A).recovery, 'synchronous fresh recovery')
    expect(freshSynchronous.phase).toBe('rechecking'); expect(freshSynchronous.recheck).toBeNull()
    unchanged(freshSynchronous)
    await h.fresh.issuedBarrier
    await h.flush() // No unresolved fresh-task drain.
    expect(h.phase()).toBe('fresh-live')
    const freshLive = recovery()
    expect(freshLive).toBe(freshSynchronous); unchanged(freshLive); readonly(true); h.cardinality(5)
    expect(h.gets[4]!.outcome).toStrictEqual({ status: 'pending' })
    expect(freshObservation.result.outcome).toStrictEqual({ status: 'pending' })
    await act(async () => { h.fresh.settle(); await freshObservation.handled; await h.drain() })
    await h.flush(); h.outcome(freshObservation.result)
    const matched = recovery()
    expect(matched.phase).toBe('matched')
    const read: RecoveryRead = required(matched.recheck, 'matched readonly evidence')
    expect(Number.isInteger(read.readId)).toBe(true); expect(read.readId).toBeGreaterThan(0)
    expect(read).toStrictEqual({ readId: read.readId, provenance: 'saved', target: P2, matchesSubmitted: true })
    expect(read.target).not.toBe(rawP2); expect(read.target).not.toBe(retained.submitted)
    expect(h.hook().recovery?.recheck).toBe(read); expectFrozen(read); unchanged(matched)
    expect(failedCapture.phase).toBe('recheck-failed'); expect(failedCapture.recheck).toBeNull(); expectFrozen(failedCapture)
    expect(h.gets[4]!.user).toBe(freshGet)
    expect(store.finalize(sessionId)).toBe(originalTask); h.outcome(saveObservation.result)
    readonly(false); h.cardinality(5); h.evidence()
    h.setPhase('matched')
  } catch (failure: unknown) { primary = failure; failed = true }
  finally { cleanupFailures = await h.cleanup() }
  if (failed) {
    if (cleanupFailures.length) console.error('Failure-to-matched cleanup diagnostics; primary preserved', cleanupFailures)
    throw primary
  }
  if (cleanupFailures.length) throw new AggregateError(cleanupFailures, 'Failure-to-matched cleanup failed')
})
