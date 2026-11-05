import { useLayoutEffect, type ReactNode } from 'react'
import { expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'

import { AuthProvider } from '@/contexts/auth-provider'
import { useAuth } from '@/contexts/auth-context'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { DashboardLayoutProvider, useDashboardLayoutOwner, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
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
// Only transport boundaries and the established Sonner observation stub.
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: ports.me, updateMe: ports.updateMe },
  dashboard: { saveLayout: ports.saveLayout },
}))
vi.mock('sonner', () => ({ toast: { error: (message: unknown) => ports.toastError(message) } }))

const ACTOR = '33333333-3333-4333-8333-333333333333'
const A = '11111111-1111-4111-8111-111111111111'
const TOKEN = 'synthetic-explicit-recheck-token'
type ProspectiveController = DashboardLayoutController & Partial<{
  recheckRecovery(attemptId: string): Promise<void>
}>
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
function user(preferences: NonNullable<User['preferences']> & Record<string, unknown>): User {
  return { id: ACTOR, email: 'recheck@example.invalid', is_active: true,
    is_superuser: false, is_verified: true, is_2fa_enabled: false, preferences }
}
// Every expected layout is independently literal; no submitted-request echo or resolver.
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
const explicitGet = freeze(user({ language: 'en', caller_extra: { retained: ['explicit'] }, dashboard_layouts: { [A]: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'period-transactions', visible: true, order: 4 },
    { id: 'goals-progress', visible: true, order: 5 },
  ],
} } }))
const workspace: Workspace = freeze({ id: A, name: 'Synthetic recheck', kind: 'personal',
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
  return { value, promise, issued: false, settled: false,
    settle() {
      if (!this.issued || this.settled) return
      this.settled = true
      if (failure === undefined) resolve(value); else reject(failure)
    },
  }
}

function harness() {
  const client = createTestQueryClient()
  const offlineError = new Error('offline')
  const restoration = controlled(seed), entry = controlled(seed), post = controlled(postGet)
  const firstPut = controlled(accepted), secondPut = controlled(accepted, offlineError)
  const explicit = controlled(explicitGet)
  const controlledPorts = [restoration, entry, firstPut, post, secondPut, explicit]
  type Phase = 'auth' | 'entry' | 'ready' | 'save1' | 'post-get' | 'after-save1' | 'save2' | 'failed' | 'explicit-read' | 'explicit-live' | 'review-ready'
  let phase: Phase = 'auth'
  let cleaning = false
  const violations: unknown[] = [], unexpected: unknown[] = []
  const outcomes: Array<{ rejected: boolean; failure?: unknown }> = []
  const tasks: Promise<void>[] = [], publications: unknown[] = []
  const owners = new Set<DashboardLayoutOwner>()
  const unsubscribes: Array<() => void> = []
  const writers: Array<ReturnType<typeof vi.spyOn>> = [], contexts = new WeakSet<object>()
  const state: { owner: DashboardLayoutOwner | null; hook: ProspectiveController | null;
    auth: ReturnType<typeof useAuth> | null; client: typeof client | null } = {
    owner: null, hook: null, auth: null, client: null,
  }
  const gets: Array<{ phase: Phase; generation: number; storage: string | null;
    completed: boolean; user: User | null; failure: unknown }> = []
  const puts: Array<{ phase: Phase; id: string; layout: DashboardLayout; input: DashboardLayout;
    binding: DashboardRequestBinding; current: boolean; generation: number; storage: string | null }> = []
  function identities(value: unknown): object[] {
    if (value === null || typeof value !== 'object') return []
    return [value, ...Object.values(value).flatMap(identities)]
  }
  const inputWitnesses = [seed, postGet, explicitGet, accepted].map((input) => ({
    input, before: structuredClone(input), identities: identities(input),
  }))
  const storage = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let mounted: { unmount(): void } | null = null
  let startExplicit!: () => void
  const explicitStarted = new Promise<void>((resolve) => { startExplicit = resolve })
  function track(task: Promise<unknown>, expectedFailure?: unknown) {
    tasks.push(task.then(() => { outcomes.push({ rejected: false }) }, (failure: unknown) => {
      outcomes.push({ rejected: true, failure })
      if (failure !== expectedFailure) unexpected.push(failure)
    }))
  }
  function deny(kind: string): Promise<never> {
    const failure = new Error(`Forbidden ${kind} in ${phase}; GET=${gets.length}, PUT=${puts.length}`)
    violations.push(failure)
    const task = Promise.reject(failure)
    track(task, failure)
    return task
  }
  ports.me.mockReset().mockImplementation(() => {
    const call = { phase, generation: getDashboardAuthSnapshot(client).generation,
      storage: localStorage.getItem(WORKSPACE_STORAGE_KEY), completed: false,
      user: null as User | null, failure: null as unknown }
    gets.push(call)
    // Check phase AND kind/cardinality before admitting any unresolved deferred.
    let port: ReturnType<typeof controlled<User>>
    if (phase === 'auth' && gets.length === 1 && puts.length === 0) { port = restoration; phase = 'entry' }
    else if (phase === 'entry' && gets.length === 2 && puts.length === 0) { port = entry; phase = 'ready' }
    else if (phase === 'post-get' && gets.length === 3 && puts.length === 1) { port = post; phase = 'after-save1' }
    else if (phase === 'explicit-read' && gets.length === 4 && puts.length === 2) { port = explicit; phase = 'explicit-live' }
    else return deny('GET')
    port.issued = true
    tasks.push(port.promise.then((value) => { call.user = value; call.completed = true },
      (failure: unknown) => { call.failure = failure; call.completed = true; unexpected.push(failure) }))
    if (port === explicit) startExplicit()
    // Cleanup never relaxes admission; only admitted/issued work is settled.
    if (cleaning) port.settle()
    return port.promise
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    puts.push({ phase, id, input: layout, layout: structuredClone(layout), binding,
      current: binding.isCurrent(), generation: getDashboardAuthSnapshot(client).generation,
      storage: localStorage.getItem(WORKSPACE_STORAGE_KEY) })
    let port: typeof firstPut
    if (phase === 'save1' && puts.length === 1 && gets.length === 2) {
      port = firstPut
      // Native successful finalize may issue postGET during settlement.
      phase = 'post-get'
    } else if (phase === 'save2' && puts.length === 2 && gets.length === 3) {
      port = secondPut; phase = 'failed'
    } else return deny('PUT')
    port.issued = true
    track(port.promise, port === secondPut ? offlineError : undefined)
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
    useLayoutEffect(() => {
      state.hook = hook; state.owner = owner; state.client = observedClient
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
    throw new Error('Explicit-recheck work did not reach native fixed point')
  }
  // Wait only for issued work. Late admitted postGET is settled on its boundary.
  async function settleIssued() {
    cleaning = true
    controlledPorts.forEach((port) => { if (port.issued) port.settle() })
    await act(async () => { await drain() })
    await act(async () => { await drain() })
  }
  function noGenericWriters() {
    expect(ports.updateMe).not.toHaveBeenCalled()
    expect(writers.length).toBeGreaterThan(0)
    writers.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
  }
  function evidence() {
    expect(violations).toStrictEqual([]); expect(unexpected).toStrictEqual([])
    noGenericWriters()
    expect(client.isMutating()).toBe(0)
    expect(ports.me.mock.calls).toStrictEqual(gets.map(() => []))
    expect(ports.saveLayout).toHaveBeenCalledTimes(puts.length)
    expect(gets).toHaveLength([restoration, entry, post, explicit].filter((port) => port.issued).length)
    expect(puts).toHaveLength([firstPut, secondPut].filter((port) => port.issued).length)
    expect(outcomes.filter((outcome) => outcome.rejected)).toStrictEqual(
      secondPut.settled ? [{ rejected: true, failure: offlineError }] : [],
    )
    gets.forEach((call) => { expect(call.completed).toBe(true); expect(call.failure).toBeNull() })
    inputWitnesses.forEach(({ input, before, identities: captured }) => {
      expect(input).toStrictEqual(before); expectFrozen(input)
      const now = identities(input)
      expect(now).toHaveLength(captured.length)
      now.forEach((identity, index) => { expect(identity).toBe(captured[index]) })
    })
    puts.forEach((call) => { expect(call.input).toStrictEqual(call.layout) })
  }
  function stable(expected: DashboardLayoutOwner, generation: number) {
    expect(state.owner).toBe(expected); expect([...owners]).toStrictEqual([expected])
    expect(state.client).toBe(client); expect(owner().store).toBe(expected.store)
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
    mounted = render(<Shell />, { reactStrictMode: false })
    expect(restoration.issued).toBe(true)
    await act(async () => { restoration.settle(); await tick(); await tick() })
    await flush()
    expect(entry.issued).toBe(true)
    await act(async () => { entry.settle(); await drain() })
    await flush()
    expect(gets).toHaveLength(2); expect(gets.map((call) => call.user)).toStrictEqual([seed, seed])
  }
  async function cleanup() {
    const failures: unknown[] = []
    async function step(action: () => void | Promise<void>) {
      try { await action() } catch (failure: unknown) { failures.push(failure) }
    }
    await step(settleIssued)
    await step(async () => { await act(async () => { state.hook?.closeCustomize(false); await drain() }) })
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
  return { client, offlineError, start, cleanup, hook, owner, stable, request, evidence, noGenericWriters, flush, drain,
    firstPut, secondPut, post, explicit, explicitStarted, gets, puts, track,
    phase: () => phase, setPhase: (next: Phase) => { phase = next } }
}

it('keeps a successful P1 save and original offline P2 recovery on the same mounted owner, then deduplicates explicit P1 recheck into frozen review-ready evidence without replay', async () => {
  const h = harness()
  let primary: unknown
  let failed = false
  try {
    await h.start()
    const owner = h.owner(), store = owner.store, generation = owner.authGeneration
    h.stable(owner, generation); h.evidence()
    expect(h.phase()).toBe('ready')
    expect(store.getSnapshot(A).writable).toBe(true)
    expect(store.getSnapshot(A).recovery).toBeNull()
    expect(store.getSnapshot(A).counters.success).toBe(0)
    expect(store.getSnapshot(A).pending).toBe(false)
    expect(store.getSnapshot(A).readPending).toBe(false)
    // Epoch-zero opening: actual first finalize must advance directly to epoch one.
    act(() => { h.hook().openCustomize() })
    expect(store.retain(A)).toBe('dashboard-session-1')
    act(() => { h.hook().move('period-transactions', 'up') })
    expect(h.hook().draft).toStrictEqual(P1)
    expect(validateCommittedDashboardLayout(A, accepted, DASHBOARD_WIDGETS)).toStrictEqual(accepted)
    h.setPhase('save1')
    act(() => { h.hook().closeCustomize(true) })
    await h.flush()
    expect(h.firstPut.issued).toBe(true); expect(h.puts).toHaveLength(1)
    h.request(0, P1, generation)
    expect(store.getSnapshot(A).pending).toBe(true)
    await act(async () => { h.firstPut.settle(); await tick(); await tick() })
    await h.flush()
    expect(h.post.issued).toBe(true)
    await act(async () => { h.post.settle(); await h.drain() })
    await h.flush()
    expect(h.gets).toHaveLength(3); expect(h.gets[2]!.user).toBe(postGet)
    expect(store.getSnapshot(A).layout).toStrictEqual(P1)
    expect(store.getSnapshot(A).overlay).toStrictEqual({ [A]: P1 })
    expect(store.getSnapshot(A).state).toBe('closed-success')
    expect(store.getSnapshot(A).writable).toBe(true)
    expect(store.getSnapshot(A).pending).toBe(false)
    expect(store.getSnapshot(A).readPending).toBe(false)
    expect(store.getSnapshot(A).counters.success).toBe(1)
    expect(h.hook().customizeOpen).toBe(false)
    expect(ports.toastError).not.toHaveBeenCalled()
    h.stable(owner, generation); h.evidence()

    act(() => { h.hook().openCustomize() })
    act(() => { h.hook().move('hero-balance', 'down') })
    expect(h.hook().draft).toStrictEqual(P2)
    const sessionId = required(store.retain(A), 'actual second retained session')
    expect(sessionId).toBe('dashboard-session-2')
    h.setPhase('save2')
    act(() => { h.hook().closeCustomize(true) })
    await h.flush()
    expect(h.secondPut.issued).toBe(true); expect(h.puts).toHaveLength(2)
    h.request(1, P2, generation)
    expect(store.getSnapshot(A).pending).toBe(true)
    const issued = store.finalize(sessionId)
    const outcome = { settled: false, rejected: false, failure: null as unknown }
    const observer = issued.then(() => { outcome.settled = true }, (failure: unknown) => {
      outcome.settled = true; outcome.rejected = true; outcome.failure = failure
    })
    h.track(observer)
    expect(store.finalize(sessionId)).toBe(issued)
    await act(async () => { h.secondPut.settle(); await observer; await h.drain() })
    await h.flush()
    expect(outcome).toStrictEqual({ settled: true, rejected: true, failure: h.offlineError })
    expect(outcome.failure).toBe(h.offlineError)
    expect(ports.toastError.mock.calls).toStrictEqual([[t('dashboard.customize.saveError')]])
    expect(h.gets).toHaveLength(3); expect(h.puts).toHaveLength(2)
    expect(ports.me).toHaveBeenCalledTimes(3); expect(ports.saveLayout).toHaveBeenCalledTimes(2)
    expect(h.hook().customizeOpen).toBe(false)
    expect(store.getSnapshot(A).pending).toBe(false)
    expect(store.getSnapshot(A).readPending).toBe(false)
    expect(store.getSnapshot(A).writable).toBe(false)
    expect(store.getSnapshot(A).counters.success).toBe(1)
    function recovery(): Recovery {
      const value = store.getSnapshot(A).recovery
      expect(value).not.toBeNull(); expect(value).toBeDefined()
      expect(h.hook().recovery).toBe(value)
      return required(value, 'store recovery')
    }
    const retained = recovery()
    expect(retained).toStrictEqual({ attemptId: 'dashboard-attempt-2', actorId: ACTOR,
      authGeneration: generation, workspaceId: A, epoch: 2, baseline: P1, submitted: P2,
      category: 'unknown', phase: 'needs-recheck', recheck: null })
    expect(retained.attemptId.trim().length).toBeGreaterThan(0)
    expectFrozen(retained)
    const original = structuredClone(retained)
    const immutable = { attemptId: retained.attemptId, actorId: retained.actorId,
      authGeneration: retained.authGeneration, workspaceId: retained.workspaceId,
      epoch: retained.epoch, baseline: retained.baseline, submitted: retained.submitted,
      category: retained.category }
    function unchanged(value: Recovery) {
      expect(value).toStrictEqual({ ...original, phase: value.phase, recheck: value.recheck })
      expect(value.attemptId).toBe(immutable.attemptId); expect(value.actorId).toBe(immutable.actorId)
      expect(value.authGeneration).toBe(immutable.authGeneration); expect(value.workspaceId).toBe(immutable.workspaceId)
      expect(value.epoch).toBe(immutable.epoch); expect(value.category).toBe(immutable.category)
      expect(value.baseline).toBe(immutable.baseline); expect(value.submitted).toBe(immutable.submitted)
      expectFrozen(value); expect(retained).toStrictEqual(original)
    }
    h.stable(owner, generation); h.evidence()

    // Intended assertion-level RED on unchanged product; never invoke an absent port.
    const controller: ProspectiveController = h.hook()
    expect(controller.recheckRecovery).toBeDefined()
    expect(typeof controller.recheckRecovery).toBe('function')
    if (typeof controller.recheckRecovery !== 'function') {
      throw new Error('Presence assertions must establish the real recheckRecovery port')
    }
    const recheck = controller.recheckRecovery
    // Everything below is authored NOT_REACHED until the product supplies the port.
    h.setPhase('explicit-read')
    let first!: Promise<void>
    let duplicate!: Promise<void>
    act(() => {
      first = recheck.call(controller, retained.attemptId)
      h.track(first)
      duplicate = recheck.call(controller, retained.attemptId)
      h.track(duplicate)
    })
    expect(duplicate).toBe(first)
    // Do not wait for a missing GET and turn a behavior failure into a timeout.
    expect(h.explicit.issued).toBe(true)
    await h.explicitStarted
    await h.flush()
    expect(h.phase()).toBe('explicit-live')
    expect(h.gets).toHaveLength(4); expect(ports.me).toHaveBeenCalledTimes(4)
    const live = recovery()
    expect(live.phase).toBe('rechecking'); expect(live.recheck).toBeNull()
    expect(store.getSnapshot(A).readPending).toBe(true)
    unchanged(live); h.stable(owner, generation)
    expect(h.puts).toHaveLength(2); expect(ports.saveLayout).toHaveBeenCalledTimes(2)
    h.noGenericWriters()
    expect(ports.toastError.mock.calls).toStrictEqual([[t('dashboard.customize.saveError')]])
    await act(async () => { h.explicit.settle(); await first; await h.drain() })
    await h.flush()
    const final = recovery()
    expect(final.phase).toBe('review-ready')
    expect(h.hook().customizeOpen).toBe(false)
    expect(final.recheck).not.toBeNull()
    const read: RecoveryRead = required(final.recheck, 'current public recheck evidence')
    expect(Number.isInteger(read.readId)).toBe(true); expect(read.readId).toBeGreaterThan(0)
    expect(h.hook().recovery?.recheck).toBe(read)
    expect(store.getSnapshot(A).recovery?.recheck).toBe(read)
    expect(read).toStrictEqual({ readId: read.readId, provenance: 'saved', target: P1, matchesSubmitted: false })
    expectFrozen(read); unchanged(final)
    expect(store.getSnapshot(A).pending).toBe(false); expect(store.getSnapshot(A).readPending).toBe(false)
    expect(store.getSnapshot(A).counters.success).toBe(1)
    expect(store.getSnapshot(A).overlay).toStrictEqual({ [A]: P1 })
    expect(h.gets[3]!.user).toBe(explicitGet)
    expect(h.gets).toHaveLength(4); expect(h.puts).toHaveLength(2)
    expect(ports.me).toHaveBeenCalledTimes(4); expect(ports.saveLayout).toHaveBeenCalledTimes(2)
    expect(ports.toastError.mock.calls).toStrictEqual([[t('dashboard.customize.saveError')]])
    h.setPhase('review-ready'); h.stable(owner, generation); h.evidence()
  } catch (failure: unknown) { primary = failure; failed = true }
  const cleanupFailures = await h.cleanup()
  if (failed) {
    if (cleanupFailures.length) console.error('Explicit-recheck cleanup diagnostics; primary preserved', cleanupFailures)
    throw primary
  }
  if (cleanupFailures.length) throw new AggregateError(cleanupFailures, 'Explicit-recheck cleanup failed')
})
