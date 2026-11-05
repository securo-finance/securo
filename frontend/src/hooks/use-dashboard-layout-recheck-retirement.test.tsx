import { useLayoutEffect, type ReactElement, type ReactNode } from 'react'
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
import { validateCommittedDashboardLayout, type CommittedDashboardLayout, type DashboardLayout, type DashboardRequestBinding } from '@/lib/dashboard-layout'
import type { Recovery, RecoveryRead } from '@/lib/dashboard-layout-session'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { createTestQueryClient, i18n, t } from '@/test/utils'
import type { User, Workspace } from '@/types'

const ports = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout, binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  toastError: vi.fn(),
}))
// Substitute transport only; Auth, owner registration, lease and hook stay real.
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: ports.me, updateMe: ports.updateMe },
  dashboard: { saveLayout: ports.saveLayout },
}))
vi.mock('sonner', () => ({ toast: { error: (message: unknown) => ports.toastError(message) } }))

const ACTOR = '33333333-3333-4333-8333-333333333333'
const A = '11111111-1111-4111-8111-111111111111'
const TOKEN = 'synthetic-retired-read-token'
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
  return { id: ACTOR, email: 'retirement@example.invalid', is_active: true,
    is_superuser: false, is_verified: true, is_2fa_enabled: false, preferences }
}
// Independent strict literals: neither the accepted reply nor READ targets echo a PUT.
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
const retiredGet = freeze(user({ language: 'en', caller_extra: { retained: ['retired'] }, dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} } }))
const freshGet = freeze(user({ language: 'en', caller_extra: { retained: ['fresh'] }, dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} } }))
const workspace: Workspace = freeze({ id: A, name: 'Synthetic retirement', kind: 'personal',
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
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, issued: false, settled: false,
    settle() {
      if (!this.issued || this.settled) return
      this.settled = true
      if (failure === undefined) resolve(value); else reject(failure)
    },
  }
}
type Outcome = { status: 'pending' } | { status: 'fulfilled' } | { status: 'rejected'; failure: unknown }
type Expected = 'fulfilled' | 'offline' | 'retired' | 'unavailable'

function harness() {
  const client = createTestQueryClient(), offlineError = new Error('offline')
  const restoration = controlled(seed), entry = controlled(seed), post = controlled(postGet)
  const firstPut = controlled(accepted), secondPut = controlled(accepted, offlineError)
  const retired = controlled(retiredGet), fresh = controlled(freshGet)
  const controlledPorts = [restoration, entry, firstPut, post, secondPut, retired, fresh]
  type Phase = 'auth' | 'entry' | 'ready' | 'save1' | 'post-get' | 'after-save1' | 'save2' | 'failed' |
    'retired-explicit' | 'retired-live' | 'page-absent' | 'remounted-occupied' | 'retired-drained' |
    'fresh-explicit' | 'fresh-live' | 'review-ready'
  let phase: Phase = 'auth', cleaning = false
  const violations: unknown[] = [], unexpected: unknown[] = [], publications: unknown[] = []
  const owners = new Set<DashboardLayoutOwner>()
  const tasks: Promise<void>[] = [], unsubscribes: Array<() => void> = []
  const observers: Array<{ expected: Expected; outcome: Outcome }> = []
  const writers: Array<ReturnType<typeof vi.spyOn>> = [], contexts = new WeakSet<object>()
  const state: { owner: DashboardLayoutOwner | null; hook: DashboardLayoutController | null;
    auth: ReturnType<typeof useAuth> | null; client: typeof client | null;
    workspace: Workspace | null; witnessVisits: number } = {
    owner: null, hook: null, auth: null, client: null, workspace: null, witnessVisits: 0,
  }
  const gets: Array<{ phase: Phase; generation: number; storage: string | null;
    completed: boolean; user: User | null; failure: unknown }> = []
  const puts: Array<{ phase: Phase; id: string; layout: DashboardLayout; input: DashboardLayout;
    binding: DashboardRequestBinding; current: boolean; generation: number; storage: string | null }> = []
  const inputs = [P1, P2, seed, postGet, retiredGet, freshGet, accepted, workspace].map((input) => ({
    input, before: structuredClone(input), identities: identities(input),
  }))
  const storage = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let mounted: { unmount(): void; rerender(ui: ReactElement): void } | null = null
  function observe(task: Promise<void>, expected: Expected) {
    const result: { expected: Expected; outcome: Outcome } = { expected, outcome: { status: 'pending' } }
    observers.push(result)
    // Typed handled observation only: assertions never execute inside isolated callbacks.
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
    const call = { phase, generation: getDashboardAuthSnapshot(client).generation,
      storage: localStorage.getItem(WORKSPACE_STORAGE_KEY), completed: false,
      user: null as User | null, failure: null as unknown }
    gets.push(call)
    let port: ReturnType<typeof controlled<User>>
    // Phase AND exact cardinality are checked before allocating unresolved transport.
    if (phase === 'auth' && gets.length === 1 && puts.length === 0) { port = restoration; phase = 'entry' }
    else if (phase === 'entry' && gets.length === 2 && puts.length === 0) { port = entry; phase = 'ready' }
    else if (phase === 'post-get' && gets.length === 3 && puts.length === 1) { port = post; phase = 'after-save1' }
    else if (phase === 'retired-explicit' && gets.length === 4 && puts.length === 2) { port = retired; phase = 'retired-live' }
    else if (phase === 'fresh-explicit' && gets.length === 5 && puts.length === 2) { port = fresh; phase = 'fresh-live' }
    else return deny('GET')
    port.issued = true
    tasks.push(port.promise.then((value) => { call.user = value; call.completed = true }, (failure: unknown) => {
      call.failure = failure; call.completed = true; unexpected.push(failure)
    }))
    if (cleaning) port.settle()
    return port.promise
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    puts.push({ phase, id, input: layout, layout: structuredClone(layout), binding,
      current: binding.isCurrent(), generation: getDashboardAuthSnapshot(client).generation,
      storage: localStorage.getItem(WORKSPACE_STORAGE_KEY) })
    let port: typeof firstPut
    if (phase === 'save1' && puts.length === 1 && gets.length === 2) {
      port = firstPut; phase = 'post-get'
    } else if (phase === 'save2' && puts.length === 2 && gets.length === 3) {
      port = secondPut; phase = 'failed'
    } else return deny('PUT')
    port.issued = true
    observe(port.promise.then(() => {}), port === secondPut ? 'offline' : 'fulfilled')
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
  // This witness survives page-only rerenders and never acquires a page binding.
  function OwnerWitness() {
    const owner = useDashboardLayoutOwner(), observedClient = useQueryClient(), current = useWorkspace().current
    useLayoutEffect(() => {
      state.owner = owner; state.client = observedClient; state.workspace = current
      state.witnessVisits += 1
      if (owner && !owners.has(owner)) {
        owners.add(owner)
        unsubscribes.push(owner.store.subscribe(() => {
          try { publications.push(owner.store.getSnapshot(A)) }
          catch (failure: unknown) { unexpected.push(failure) }
        }))
      }
    })
    return null
  }
  function PageProbe() {
    const controller = useDashboardLayout()
    useLayoutEffect(() => { state.hook = controller })
    useLayoutEffect(() => () => { state.hook = null }, [])
    return null
  }
  // Shell's component identity and every provider remain mounted on page toggles.
  function Shell({ page }: { page: boolean }) {
    return <I18nextProvider i18n={i18n}><QueryClientProvider client={client}><AuthProvider>
      <ObserveAuth><WorkspaceContext.Provider value={{ current: workspace, workspaces: [workspace],
        isLoading: false, role: 'owner', canWrite: true, canManage: true,
        enabledModules: ['accounts', 'transactions', 'goals'], hasModule: (id) => workspace.enabled_modules.includes(id),
        switchWorkspace: async () => { throw new Error('Unexpected workspace switch') },
        refresh: async () => { throw new Error('Unexpected workspace refresh') } }}>
        <DashboardLayoutProvider><OwnerWitness />{page ? <PageProbe /> : null}</DashboardLayoutProvider>
      </WorkspaceContext.Provider></ObserveAuth>
    </AuthProvider></QueryClientProvider></I18nextProvider>
  }
  const hook = () => required(state.hook, 'mounted page hook')
  const owner = () => required(state.owner, 'persistent provider owner')
  async function flush() { await act(async () => { await tick(); await tick() }) }
  function page(present: boolean) {
    act(() => { required(mounted, 'rendered Shell').rerender(<Shell page={present} />) })
  }
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
    throw new Error('Retirement characterization did not reach native fixed point')
  }
  function noGenericWriters() {
    expect(ports.updateMe).not.toHaveBeenCalled()
    expect(writers.length).toBeGreaterThan(0)
    writers.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
  }
  function cardinality(getCount: number) {
    expect(gets).toHaveLength(getCount); expect(ports.me).toHaveBeenCalledTimes(getCount)
    expect(puts).toHaveLength(2); expect(ports.saveLayout).toHaveBeenCalledTimes(2)
    expect(ports.toastError.mock.calls).toStrictEqual([[t('dashboard.customize.saveError')]])
    noGenericWriters(); expect(violations).toStrictEqual([]); expect(unexpected).toStrictEqual([])
  }
  function evidence() {
    expect(violations).toStrictEqual([]); expect(unexpected).toStrictEqual([])
    noGenericWriters(); expect(client.isMutating()).toBe(0)
    expect(ports.me.mock.calls).toStrictEqual(gets.map(() => []))
    expect(ports.saveLayout).toHaveBeenCalledTimes(puts.length)
    expect(gets).toHaveLength([restoration, entry, post, retired, fresh].filter((port) => port.issued).length)
    expect(puts).toHaveLength([firstPut, secondPut].filter((port) => port.issued).length)
    observers.forEach(({ expected, outcome }) => {
      expect(outcome.status).not.toBe('pending')
      if (expected === 'fulfilled') expect(outcome).toStrictEqual({ status: 'fulfilled' })
      else {
        expect(outcome.status).toBe('rejected')
        if (outcome.status !== 'rejected') throw new Error(`Expected observed ${expected} rejection`)
        if (expected === 'offline') expect(outcome.failure).toBe(offlineError)
        else {
          expect(outcome.failure).toBeInstanceOf(Error)
          expect(outcome.failure).toHaveProperty('message', expected === 'retired'
            ? 'Dashboard layout read is stale' : 'Dashboard recovery read unavailable')
        }
      }
    })
    gets.forEach((call) => { expect(call.completed).toBe(true); expect(call.failure).toBeNull() })
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
  const onError = (event: ErrorEvent) => { unexpected.push(event.error ?? event.message) }
  const onRejection = (event: PromiseRejectionEvent) => { unexpected.push(event.reason) }
  async function start() {
    window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
    localStorage.setItem('token', TOKEN); localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
    mounted = render(<Shell page />, { reactStrictMode: false })
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
      // Late legal postGET settles at admission; forbidden calls never gain admission.
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
        expect(snapshot.recovery).toBeNull(); expect(snapshot.writable).toBe(false)
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
  return { start, cleanup, hook, owner, stable, request, evidence, noGenericWriters, cardinality, flush, drain,
    observe, page, state, offlineError, firstPut, secondPut, post, retired, fresh, gets, puts,
    phase: () => phase, setPhase: (next: Phase) => { phase = next } }
}

it('retires explicit recovery READ on last page release, preserves its drain-only lifetime on same-owner remount, and admits fresh qualifying recheck after settlement', async () => {
  const h = harness()
  let primary: unknown, failed = false
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
    h.setPhase('save1')
    act(() => { h.hook().closeCustomize(true) })
    await h.flush(); expect(h.firstPut.issued).toBe(true)
    h.request(0, P1, generation); expect(store.getSnapshot(A).pending).toBe(true)
    await act(async () => { h.firstPut.settle(); await tick(); await tick() })
    await h.flush(); expect(h.post.issued).toBe(true)
    await act(async () => { h.post.settle(); await h.drain() })
    await h.flush()
    expect(h.gets).toHaveLength(3); expect(h.puts).toHaveLength(1)
    expect(h.gets[2]!.user).toBe(postGet)
    expect(store.getSnapshot(A).state).toBe('closed-success')
    expect(store.getSnapshot(A).layout).toStrictEqual(P1)
    expect(store.getSnapshot(A).overlay).toStrictEqual({ [A]: P1 })
    expect(store.getSnapshot(A).counters.success).toBe(1)
    expect(store.getSnapshot(A).writable).toBe(true)
    expect(store.getSnapshot(A).pending).toBe(false); expect(store.getSnapshot(A).readPending).toBe(false)
    expect(h.hook().customizeOpen).toBe(false); expect(ports.toastError).not.toHaveBeenCalled()
    h.stable(owner, generation); h.evidence()

    act(() => { h.hook().openCustomize() })
    act(() => { h.hook().move('hero-balance', 'down') })
    expect(h.hook().draft).toStrictEqual(P2)
    const sessionId = required(store.retain(A), 'second actual edit session')
    expect(sessionId).toBe('dashboard-session-2')
    h.setPhase('save2')
    act(() => { h.hook().closeCustomize(true) })
    await h.flush(); expect(h.secondPut.issued).toBe(true)
    h.request(1, P2, generation); expect(store.getSnapshot(A).pending).toBe(true)
    const issued = store.finalize(sessionId), originalFailure = h.observe(issued, 'offline')
    expect(store.finalize(sessionId)).toBe(issued)
    await act(async () => { h.secondPut.settle(); await originalFailure.handled; await h.drain() })
    await h.flush()
    expect(originalFailure.result.outcome).toStrictEqual({ status: 'rejected', failure: h.offlineError })
    if (originalFailure.result.outcome.status === 'rejected') expect(originalFailure.result.outcome.failure).toBe(h.offlineError)
    h.cardinality(3); h.stable(owner, generation); h.evidence()
    expect(h.hook().customizeOpen).toBe(false)

    function recovery(): Recovery {
      const value = required(store.getSnapshot(A).recovery, 'public recovery')
      if (h.state.hook) expect(h.hook().recovery).toBe(value)
      return value
    }
    const retained = recovery(), original = structuredClone(retained)
    expect(retained).toStrictEqual({ attemptId: 'dashboard-attempt-2', actorId: ACTOR,
      authGeneration: generation, workspaceId: A, epoch: 2, baseline: P1, submitted: P2,
      category: 'unknown', phase: 'needs-recheck', recheck: null })
    expectFrozen(retained)
    function unchanged(value: Recovery) {
      expect(value).toStrictEqual({ ...original, phase: value.phase, recheck: value.recheck })
      expect(value.baseline).toBe(retained.baseline); expect(value.submitted).toBe(retained.submitted)
      expectFrozen(value); expect(retained).toStrictEqual(original)
    }
    function recoveryState(phase: Recovery['phase'], readPending: boolean) {
      const value = recovery(), snapshot = store.getSnapshot(A)
      unchanged(value); expect(value.phase).toBe(phase); expect(value.recheck).toBeNull()
      expect(snapshot.pending).toBe(false); expect(snapshot.readPending).toBe(readPending)
      expect(snapshot.writable).toBe(false); expect(snapshot.state).toBe('unknown')
      expect(snapshot.counters.success).toBe(1); expect(snapshot.overlay).toStrictEqual({ [A]: P1 })
      expect(snapshot.layout).toStrictEqual(P1)
      return value
    }
    recoveryState('needs-recheck', false)
    const oldController = h.hook()
    h.setPhase('retired-explicit')
    let retiredTask!: Promise<void>, retiredObserver!: ReturnType<typeof h.observe>, duplicate!: Promise<void>
    act(() => {
      retiredTask = oldController.recheckRecovery(retained.attemptId)
      retiredObserver = h.observe(retiredTask, 'retired')
      duplicate = oldController.recheckRecovery(retained.attemptId)
    })
    expect(duplicate).toBe(retiredTask)
    expect(h.retired.issued).toBe(true); expect(h.phase()).toBe('retired-live')
    expect(store.getSnapshot(A).recovery?.phase).toBe('rechecking')
    expect(store.getSnapshot(A).recovery?.recheck).toBeNull()
    expect(store.getSnapshot(A).readPending).toBe(true)
    // Bounded publication flush only: never drain or await R while its transport is pending.
    await h.flush()
    recoveryState('rechecking', true); h.cardinality(4); h.stable(owner, generation)
    expect(retiredObserver.result.outcome).toStrictEqual({ status: 'pending' })
    expect(h.gets[3]!.completed).toBe(false)

    h.setPhase('page-absent')
    const beforeRelease = h.state.witnessVisits
    h.page(false)
    expect(h.state.hook).toBeNull()
    expect(h.state.witnessVisits).toBeGreaterThan(beforeRelease)
    // Native passive release resets public authority immediately, without transport settlement.
    recoveryState('needs-recheck', true); h.stable(owner, generation); h.cardinality(4)
    let idleCompleted = false
    const idle = store.whenIdle().then(() => { idleCompleted = true })
    const idleObserver = h.observe(idle, 'fulfilled')
    await h.flush()
    expect(idleCompleted).toBe(false); expect(h.retired.settled).toBe(false)
    expect(retiredObserver.result.outcome).toStrictEqual({ status: 'pending' })

    h.setPhase('remounted-occupied')
    const beforeRemount = h.state.witnessVisits
    h.page(true)
    expect(h.state.witnessVisits).toBeGreaterThan(beforeRemount)
    expect(h.hook()).not.toBe(oldController)
    await h.flush()
    h.stable(owner, generation); h.cardinality(4); recoveryState('needs-recheck', true)
    // Ordinary remount refresh shares R for drain lifetime ONLY, not qualification/dedup authority.
    expect(store.refresh(A)).toBe(retiredTask)
    expect(store.finalize(sessionId)).toBe(issued)
    expect(h.puts[1]!.binding.isCurrent()).toBe(true)
    let unavailable!: ReturnType<typeof h.observe>
    act(() => { unavailable = h.observe(h.hook().recheckRecovery(retained.attemptId), 'unavailable') })
    await h.flush(); await unavailable.handled
    expect(unavailable.result.outcome.status).toBe('rejected')
    if (unavailable.result.outcome.status === 'rejected') {
      expect(unavailable.result.outcome.failure).toBeInstanceOf(Error)
      expect(unavailable.result.outcome.failure).toHaveProperty('message', 'Dashboard recovery read unavailable')
    }
    recoveryState('needs-recheck', true); h.cardinality(4); h.stable(owner, generation)
    expect(idleCompleted).toBe(false); expect(h.fresh.issued).toBe(false)
    expect(retiredObserver.result.outcome).toStrictEqual({ status: 'pending' })
    // No retain/open/editable call is used to admit either recheck; the old ticket stays consumed.
    await act(async () => { h.retired.settle(); await retiredObserver.handled; await h.drain() })
    await h.flush()
    expect(retiredObserver.result.outcome.status).toBe('rejected')
    if (retiredObserver.result.outcome.status === 'rejected') {
      expect(retiredObserver.result.outcome.failure).toBeInstanceOf(Error)
      expect(retiredObserver.result.outcome.failure).toHaveProperty('message', 'Dashboard layout read is stale')
    }
    expect(h.gets[3]!.completed).toBe(true); expect(h.gets[3]!.failure).toBeNull()
    expect(h.gets[3]!.user).toBe(retiredGet) // HTTP resolved normally; the store task rejected stale.
    expect(idleCompleted).toBe(true); expect(idleObserver.result.outcome).toStrictEqual({ status: 'fulfilled' })
    h.setPhase('retired-drained')
    recoveryState('needs-recheck', false); h.cardinality(4); h.stable(owner, generation); h.evidence()
    expect(h.hook().customizeOpen).toBe(false)
    expect(h.puts[1]!.binding.isCurrent()).toBe(true)

    h.setPhase('fresh-explicit')
    let freshTask!: Promise<void>, freshObserver!: ReturnType<typeof h.observe>, freshDuplicate!: Promise<void>
    act(() => {
      freshTask = h.hook().recheckRecovery(retained.attemptId)
      freshObserver = h.observe(freshTask, 'fulfilled')
      freshDuplicate = h.hook().recheckRecovery(retained.attemptId)
    })
    expect(freshTask).not.toBe(retiredTask); expect(freshDuplicate).toBe(freshTask)
    expect(h.fresh.issued).toBe(true); expect(h.phase()).toBe('fresh-live')
    // GET-start state is synchronous; then assert the actual public hook publication too.
    expect(store.getSnapshot(A).recovery?.phase).toBe('rechecking')
    expect(store.getSnapshot(A).recovery?.recheck).toBeNull()
    expect(store.getSnapshot(A).readPending).toBe(true)
    await h.flush()
    recoveryState('rechecking', true); h.cardinality(5); h.stable(owner, generation)
    expect(freshObserver.result.outcome).toStrictEqual({ status: 'pending' })
    await act(async () => { h.fresh.settle(); await freshObserver.handled; await h.drain() })
    await h.flush()
    expect(freshObserver.result.outcome).toStrictEqual({ status: 'fulfilled' })
    const final = recovery(), read: RecoveryRead = required(final.recheck, 'fresh public evidence')
    expect(final.phase).toBe('review-ready'); unchanged(final)
    expect(Number.isInteger(read.readId)).toBe(true); expect(read.readId).toBeGreaterThan(0)
    expect(read).toStrictEqual({ readId: read.readId, provenance: 'saved', target: P1, matchesSubmitted: false })
    expectFrozen(read); expect(h.hook().recovery?.recheck).toBe(read)
    expect(store.getSnapshot(A).recovery?.recheck).toBe(read)
    expect(store.getSnapshot(A).pending).toBe(false); expect(store.getSnapshot(A).readPending).toBe(false)
    expect(store.getSnapshot(A).writable).toBe(false); expect(store.getSnapshot(A).counters.success).toBe(1)
    expect(store.getSnapshot(A).overlay).toStrictEqual({ [A]: P1 }); expect(h.hook().customizeOpen).toBe(false)
    expect(h.gets[4]!.user).toBe(freshGet)
    expect(h.gets.map((call) => call.phase)).toStrictEqual(['auth', 'entry', 'post-get', 'retired-explicit', 'fresh-explicit'])
    expect(h.puts.map((call) => call.phase)).toStrictEqual(['save1', 'save2'])
    h.setPhase('review-ready'); h.cardinality(5); h.stable(owner, generation); h.evidence()
  } catch (failure: unknown) { primary = failure; failed = true }
  const cleanupFailures = await h.cleanup()
  if (failed) {
    if (cleanupFailures.length) console.error('Retirement cleanup diagnostics; primary preserved', cleanupFailures)
    throw primary
  }
  if (cleanupFailures.length) throw new AggregateError(cleanupFailures, 'Retirement characterization cleanup failed')
})
