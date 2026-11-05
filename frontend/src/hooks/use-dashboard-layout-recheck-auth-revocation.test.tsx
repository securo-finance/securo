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
import type { SessionSnapshot } from '@/lib/dashboard-layout-session'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { createTestQueryClient, i18n, t } from '@/test/utils'
import type { User, Workspace } from '@/types'

const ports = vi.hoisted(() => ({
  me: vi.fn<() => Promise<User>>(),
  saveLayout: vi.fn<(id: string, layout: DashboardLayout, binding: DashboardRequestBinding) => Promise<CommittedDashboardLayout>>(),
  updateMe: vi.fn<(updates: Partial<User>) => Promise<User>>(),
  login: vi.fn(), verify2fa: vi.fn(), register: vi.fn(), toastError: vi.fn(),
}))
// Auth methods, bridge, provider, owner, hook, lease and store are production code.
vi.mock('@/lib/api', () => ({
  WORKSPACE_STORAGE_KEY: 'workspace_id',
  auth: { me: ports.me, updateMe: ports.updateMe, login: ports.login, verify2fa: ports.verify2fa, register: ports.register },
  dashboard: { saveLayout: ports.saveLayout },
}))
vi.mock('sonner', () => ({ toast: { error: (message: unknown) => ports.toastError(message) } }))
const ACTOR = '33333333-3333-4333-8333-333333333333'
const A = '11111111-1111-4111-8111-111111111111'
const TOKEN1 = 'synthetic-auth-revocation-token-one'
const TOKEN2 = 'synthetic-auth-revocation-token-two'
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}
function identities(value: unknown): object[] {
  if (value === null || typeof value !== 'object') return []
  return [value, ...Object.values(value).flatMap(identities)]
}
function frozen(value: unknown): void {
  if (value !== null && typeof value === 'object') {
    expect(Object.isFrozen(value)).toBe(true); Object.values(value).forEach(frozen)
  }
}
function required<T>(value: T | null, label: string): T {
  if (value === null) throw new Error(`Missing actual witness: ${label}`)
  return value
}
// Expected canonical layouts are independent literals, not normalized transport echoes.
const P0: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
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
const PF0: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const PF1: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const acceptedOld: CommittedDashboardLayout = freeze({ workspaceId: A, layout: { version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'period-transactions', visible: true, order: 4 },
  { id: 'goals-progress', visible: true, order: 5 },
] } })
const acceptedFresh: CommittedDashboardLayout = freeze({ workspaceId: A, layout: { version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] } })
const oldSeed = freeze({ id: ACTOR, email: 'old-auth@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', caller_extra: { retained: ['old-seed'] } } })
const oldPost = freeze({ id: ACTOR, email: 'old-post@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { caller_extra: { retained: ['old-post'] }, dashboard_layouts: { [A]: {
    version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'period-transactions', visible: true, order: 4 },
      { id: 'goals-progress', visible: true, order: 5 },
    ],
  } } } })
const lateOldUser = freeze({ id: ACTOR, email: 'late-old@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false, root_extra: { retained: ['late-root'] },
  preferences: { caller_extra: { retained: ['late-prefs'] }, dashboard_layouts: {
    map_extra: { retained: ['late-map'] }, [A]: {
      version: 1, workspaceId: A, leaf_extra: { retained: ['late-leaf'] }, items: [
        { id: 'uncategorized-banner', visible: true, order: 0, item_extra: { retained: ['late-item'] } },
        { id: 'hero-balance', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'period-transactions', visible: true, order: 4 },
        { id: 'goals-progress', visible: true, order: 5 },
      ],
    },
  } } })
const freshSeed = freeze({ id: ACTOR, email: 'fresh-auth@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false, root_extra: { retained: ['fresh-seed-root'] },
  preferences: { caller_extra: { retained: ['fresh-seed-prefs'] }, dashboard_layouts: {
    map_extra: { retained: ['fresh-seed-map'] }, [A]: {
      version: 1, workspaceId: A, leaf_extra: { retained: ['fresh-seed-leaf'] },
      item_extra: { retained: ['fresh-seed-item'] }, items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: true, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  } } })
const freshEntryUser = freeze({ id: ACTOR, email: 'fresh-entry@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false, root_extra: { retained: ['fresh-entry-root'] },
  preferences: { caller_extra: { retained: ['fresh-entry-prefs'] }, dashboard_layouts: {
    map_extra: { retained: ['fresh-entry-map'] }, [A]: {
      version: 1, workspaceId: A, leaf_extra: { retained: ['fresh-entry-leaf'] },
      item_extra: { retained: ['fresh-entry-item'] }, items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: true, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  } } })
const freshPost = freeze({ id: ACTOR, email: 'fresh-post@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false, root_extra: { retained: ['fresh-post-root'] },
  preferences: { caller_extra: { retained: ['fresh-post-prefs'] }, dashboard_layouts: {
    map_extra: { retained: ['fresh-post-map'] }, [A]: {
      version: 1, workspaceId: A, leaf_extra: { retained: ['fresh-post-leaf'] },
      item_extra: { retained: ['fresh-post-item'] }, items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  } } })
const workspaceA: Workspace = freeze({ id: A, name: 'Synthetic Auth revocation A', kind: 'personal',
  timezone: null,
  is_archived: false, default_currency: 'USD', locale: 'en-US', tax_jurisdiction: null, icon: null, color: null,
  created_at: '2026-10-05T00:00:00Z', created_by_user_id: ACTOR, managed_by_user_id: null,
  role: 'owner', enabled_modules: ['accounts', 'transactions', 'goals'] })
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
type ExpectedOutcome = 'fulfilled' | 'saveoffline' | 'staleread'
type Cohort = 'old' | 'fresh' | 'diagnostic'
type Phase = 'auth' | 'entry' | 'ready' | 'save1' | 'post-old' | 'after-old' | 'save2' | 'failed' |
  'recheck' | 'recheck-live' | 'logged-out' | 'login-explicit' | 'login-effect' | 'login-held' |
  'fresh-entry' | 'fresh-live' | 'fresh-ready' | 'save-fresh' | 'post-fresh' | 'done'
function harness() {
  const client = createTestQueryClient(), offlineError = freeze(new Error('original save offline'))
  const auth1 = controlled<unknown>(oldSeed), entry2 = controlled<unknown>(oldSeed), post3 = controlled<unknown>(oldPost)
  const r1 = controlled<unknown>(lateOldUser), auth5 = controlled<unknown>(freshSeed), auth6 = controlled<unknown>(freshSeed)
  const entry7 = controlled<unknown>(freshEntryUser), post8 = controlled<unknown>(freshPost)
  const put1 = controlled(acceptedOld), put2 = controlled(acceptedOld, offlineError), put3 = controlled(acceptedFresh)
  const getPorts = [auth1, entry2, post3, r1, auth5, auth6, entry7, post8]
  const allPorts = [...getPorts, put1, put2, put3]
  let phase: Phase = 'auth', cleaning = false, mounts = 0, unmounts = 0
  let oldOwner: DashboardLayoutOwner | null = null, freshOwner: DashboardLayoutOwner | null = null
  let generation: number | null = null
  const owners = new Set<DashboardLayoutOwner>(), violations: unknown[] = [], unexpected: unknown[] = []
  const publications: Array<{ owner: DashboardLayoutOwner; snapshot: SessionSnapshot }> = []
  const unsubscribes: Array<() => void> = [], writers: Array<ReturnType<typeof vi.spyOn>> = []
  const contexts = new WeakSet<object>()
  const state: { owner: DashboardLayoutOwner | null; hook: DashboardLayoutController | null;
    auth: ReturnType<typeof useAuth> | null; client: typeof client | null; workspace: Workspace | null } = {
    owner: null, hook: null, auth: null, client: null, workspace: null,
  }
  const tasks: Array<{ cohort: Cohort; expectedOutcome: ExpectedOutcome; outcome: Outcome; handled: Promise<void> }> = []
  const gets: Array<{ phase: Phase; generation: number; token: string | null; storage: string | null;
    target: string | null; owner: DashboardLayoutOwner | null; start: SessionSnapshot | null;
    outcome: Outcome; user: unknown; handled: Promise<void> | null }> = []
  const puts: Array<{ phase: Phase; generation: number; token: string | null; storage: string | null;
    target: string | null; owner: DashboardLayoutOwner | null; id: string; input: DashboardLayout;
    layout: DashboardLayout; binding: DashboardRequestBinding; current: boolean }> = []
  const inputs = [P0, P1, P2, PF0, PF1, acceptedOld, acceptedFresh, oldSeed, oldPost, lateOldUser,
    freshSeed, freshEntryUser, freshPost, workspaceA, offlineError].map((input) => ({
    input, before: structuredClone(input), refs: identities(input),
  }))
  const storage = ['token', WORKSPACE_STORAGE_KEY].map((key) => ({ key, value: localStorage.getItem(key) }))
  let mounted: { unmount(): void } | null = null
  function observe(task: Promise<unknown>, cohort: Cohort, expectedOutcome: ExpectedOutcome) {
    const result: (typeof tasks)[number] = { cohort, expectedOutcome, outcome: { status: 'pending' }, handled: Promise.resolve() }
    result.handled = task.then(() => { result.outcome = { status: 'fulfilled' } }, (failure: unknown) => {
      result.outcome = { status: 'rejected', failure }
    })
    tasks.push(result)
    return result
  }
  function deny(kind: string): Promise<never> {
    const failure = new Error(`Forbidden ${kind} in ${phase}; GET=${gets.length}, PUT=${puts.length}`)
    violations.push(failure)
    const task = Promise.reject<never>(failure)
    observe(task, 'diagnostic', 'fulfilled')
    return task
  }
  function callbackFailure(failure: unknown) {
    unexpected.push(failure)
    observe(Promise.reject(failure), 'diagnostic', 'fulfilled')
  }
  function admit(port: ReturnType<typeof controlled<unknown>>, call: (typeof gets)[number], cohort: Cohort): Promise<User> {
    port.issue()
    call.handled = port.promise.then((value) => { call.user = value; call.outcome = { status: 'fulfilled' } }, (failure: unknown) => {
      call.outcome = { status: 'rejected', failure }
    })
    observe(call.handled, cohort, 'fulfilled')
    // During cleanup both REAL Auth GETs must issue while both are still held.
    if (cleaning && port !== auth5 && port !== auth6) port.settle()
    return port.promise as Promise<User>
  }
  ports.me.mockReset().mockImplementation(() => {
    const call: (typeof gets)[number] = { phase, generation: getDashboardAuthSnapshot(client).generation,
      token: localStorage.getItem('token'), storage: localStorage.getItem(WORKSPACE_STORAGE_KEY),
      target: state.workspace?.id ?? null, owner: state.owner, start: state.owner?.store.getSnapshot(A) ?? null,
      outcome: { status: 'pending' }, user: null, handled: null }
    gets.push(call)
    const n = gets.length, fresh = n >= 5
    if (call.storage !== A || call.target !== A || call.token !== (fresh ? TOKEN2 : TOKEN1)) return deny('GET storage/target/token')
    if (n === 1) {
      if (call.owner !== null || phase !== 'auth' || puts.length !== 0) return deny('initial Auth GET')
      generation = call.generation; phase = 'entry'
    } else {
      if (call.generation !== required(generation, 'initial generation') + (fresh ? 2 : 0)) return deny('GET generation')
      if (n === 5 || n === 6) {
        if (call.owner !== null || state.auth?.user !== null || puts.length !== 2) return deny('held Auth owner')
      } else {
        const expected = fresh ? freshOwner : oldOwner
        if (!expected || call.owner !== expected || expected.actorId !== ACTOR || expected.authGeneration !== call.generation) return deny('GET owner')
      }
      if (n === 2 && phase === 'entry' && puts.length === 0) phase = 'ready'
      else if (n === 3 && phase === 'post-old' && puts.length === 1) phase = 'after-old'
      else if (n === 4 && phase === 'recheck' && puts.length === 2) phase = 'recheck-live'
      else if (n === 5 && phase === 'login-explicit') phase = 'login-effect'
      else if (n === 6 && phase === 'login-effect' && auth5.issued && !auth5.settled) phase = 'login-held'
      else if (n === 7 && phase === 'fresh-entry' && puts.length === 2 && auth5.settled && auth6.settled) phase = 'fresh-live'
      else if (n === 8 && phase === 'post-fresh' && puts.length === 3) phase = 'done'
      else return deny('GET phase/cardinality')
    }
    const port = getPorts[n - 1]
    if (!port) return deny('extra GET')
    return admit(port, call, fresh ? 'fresh' : 'old')
  })
  ports.saveLayout.mockReset().mockImplementation((id, layout, binding) => {
    const call = { phase, id, input: layout, layout: structuredClone(layout), binding, current: binding.isCurrent(),
      generation: getDashboardAuthSnapshot(client).generation, token: localStorage.getItem('token'),
      storage: localStorage.getItem(WORKSPACE_STORAGE_KEY), target: state.workspace?.id ?? null, owner: state.owner }
    puts.push(call)
    const n = puts.length, fresh = n === 3, expected = fresh ? freshOwner : oldOwner
    if (id !== A || layout.workspaceId !== A || call.storage !== A || call.target !== A || !call.current ||
        call.token !== (fresh ? TOKEN2 : TOKEN1) || !expected || call.owner !== expected || expected.actorId !== ACTOR ||
        call.generation !== required(generation, 'PUT generation') + (fresh ? 2 : 0) || expected.authGeneration !== call.generation) return deny('PUT binding/identity')
    let port: typeof put1
    if (n === 1 && phase === 'save1' && gets.length === 2) { port = put1; phase = 'post-old' }
    else if (n === 2 && phase === 'save2' && gets.length === 3) { port = put2; phase = 'failed' }
    else if (n === 3 && phase === 'save-fresh' && gets.length === 7 && !r1.settled) { port = put3; phase = 'post-fresh' }
    else return deny('PUT phase/cardinality')
    port.issue(); observe(port.promise, fresh ? 'fresh' : 'old', n === 2 ? 'saveoffline' : 'fulfilled')
    if (cleaning) port.settle()
    return port.promise
  })
  ports.updateMe.mockReset().mockImplementation(() => deny('legacy PATCH'))
  for (const port of [ports.login, ports.verify2fa, ports.register]) port.mockReset().mockImplementation(() => deny('Auth credential API'))
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
        if (oldOwner === null) oldOwner = owner
        else if (freshOwner === null) freshOwner = owner
        unsubscribes.push(owner.store.subscribe(() => {
          try { publications.push({ owner, snapshot: owner.store.getSnapshot(A) }) }
          catch (failure: unknown) { callbackFailure(failure) }
        }))
      }
    })
    useLayoutEffect(() => { mounts += 1; return () => { unmounts += 1; state.hook = null } }, [])
    return null
  }
  // Fixed workspace test seam; production WorkspaceProvider is not claimed.
  function Shell() {
    return <I18nextProvider i18n={i18n}><QueryClientProvider client={client}><AuthProvider><ObserveAuth>
      <WorkspaceContext.Provider value={{ current: workspaceA, workspaces: [workspaceA], isLoading: false,
        role: workspaceA.role, canWrite: true, canManage: true, enabledModules: ['accounts', 'transactions', 'goals'],
        hasModule: (id) => workspaceA.enabled_modules.includes(id),
        switchWorkspace: async () => { throw new Error('Forbidden workspace switch') },
        refresh: async () => { throw new Error('Forbidden workspace refresh') } }}>
        <DashboardLayoutProvider><Probe /></DashboardLayoutProvider>
      </WorkspaceContext.Provider>
    </ObserveAuth></AuthProvider></QueryClientProvider></I18nextProvider>
  }
  const hook = () => required(state.hook, 'mounted hook')
  const owner = () => required(state.owner, 'current owner')
  const auth = () => required(state.auth, 'current Auth')
  async function flush() { await act(async () => { await tick(); await tick() }) }
  async function drain(cohort: 'old' | 'fresh' | 'all') {
    for (let turn = 0; turn < 8; turn += 1) {
      const selected = [...owners].filter((current) => cohort === 'all' || current === (cohort === 'old' ? oldOwner : freshOwner))
      const count = () => [tasks.filter((task) => cohort === 'all' || task.cohort === cohort).length,
        publications.filter((publication) => cohort === 'all' || selected.includes(publication.owner)).length,
        gets.length, puts.length, owners.size, mounts, unmounts]
      const before = count()
      await tick(); await tick()
      await Promise.all(tasks.filter((task) => cohort === 'all' || task.cohort === cohort).map((task) => task.handled))
      for (const current of selected) { await current.store.whenIdle(); await getPageSessionLease(current.store).whenIdle() }
      await tick(); await tick()
      const after = count()
      expect(client.isMutating()).toBe(0)
      if (after.every((value, index) => value === before[index])) return
    }
    throw new Error(`Auth revocation ${cohort} cohort did not reach native publication fixed point`)
  }
  function outcome(task: (typeof tasks)[number]) {
    if (task.expectedOutcome === 'fulfilled') { expect(task.outcome).toStrictEqual({ status: 'fulfilled' }); return }
    expect(task.outcome.status).toBe('rejected')
    if (task.outcome.status !== 'rejected') throw new Error(`Missing ${task.expectedOutcome} outcome`)
    if (task.expectedOutcome === 'saveoffline') expect(task.outcome.failure).toBe(offlineError)
    else {
      expect(task.outcome.failure).toBeInstanceOf(Error)
      expect(task.outcome.failure).toEqual(new Error('Dashboard layout read is stale'))
      expect((task.outcome.failure as Error).message).toBe('Dashboard layout read is stale')
      expect(task.outcome.failure).not.toBe(offlineError)
    }
  }
  function preservedInputs() {
    inputs.forEach(({ input, before, refs }) => {
      expect(input).toStrictEqual(before); frozen(input)
      const now = identities(input); expect(now).toHaveLength(refs.length)
      now.forEach((ref, index) => { expect(ref).toBe(refs[index]) })
    })
    puts.forEach((call) => { expect(call.input).toStrictEqual(call.layout) })
  }
  function cardinality(getCount: number, putCount: number, toastCount: number) {
    expect(gets).toHaveLength(getCount); expect(ports.me.mock.calls).toStrictEqual(gets.map(() => []))
    expect(puts).toHaveLength(putCount); expect(ports.saveLayout).toHaveBeenCalledTimes(putCount)
    expect(ports.toastError.mock.calls).toStrictEqual(toastCount ? [[t('dashboard.customize.saveError')]] : [])
    expect(violations).toStrictEqual([]); expect(unexpected).toStrictEqual([])
    for (const port of [ports.updateMe, ports.login, ports.verify2fa, ports.register]) expect(port).not.toHaveBeenCalled()
    expect(writers.length).toBeGreaterThan(0); writers.forEach((spy) => { expect(spy).not.toHaveBeenCalled() })
    expect(client.isMutating()).toBe(0); preservedInputs()
  }
  function stable(current: DashboardLayoutOwner, g: number, user: unknown, token: string) {
    expect(state.owner).toBe(current); expect(owner().store).toBe(current.store)
    expect(current.actorId).toBe(ACTOR); expect(current.authGeneration).toBe(g)
    expect(getDashboardAuthSnapshot(client).generation).toBe(g)
    expect(auth().user).toBe(user); expect(auth().token).toBe(token); expect(auth().isLoading).toBe(false)
    expect(localStorage.getItem('token')).toBe(token); expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(A)
    expect(state.client).toBe(client); expect(state.workspace).toBe(workspaceA)
    expect(mounts).toBe(1); expect(unmounts).toBe(0)
  }
  function request(index: number, expected: DashboardLayout) {
    const call = puts[index]!
    expect(call.id).toBe(A); expect(call.layout).toStrictEqual(expected)
    expect(validateDashboardLayoutRequest(A, call.input, DASHBOARD_WIDGETS)).toStrictEqual(expected)
    expect(call.current).toBe(true); expect(call.binding.isCurrent()).toBe(true)
    expect(Reflect.ownKeys(call.input).sort()).toStrictEqual(['items', 'version', 'workspaceId'])
    call.input.items.forEach((item) => { expect(Reflect.ownKeys(item).sort()).toStrictEqual(['id', 'order', 'visible']) })
  }
  function revoked(current: DashboardLayoutOwner, readPending: boolean) {
    const snapshot = current.store.getSnapshot(A)
    expect(snapshot.layout).toStrictEqual(P0); expect(snapshot.provenance).toBe('default')
    expect(snapshot.overlay).toStrictEqual({}); expect(snapshot.state).toBe('closed')
    expect(snapshot.writable).toBe(false); expect(snapshot.pending).toBe(false); expect(snapshot.readPending).toBe(readPending)
    expect(snapshot.openedBaseline).toBeNull(); expect(snapshot.draft).toBeNull(); expect(snapshot.recovery).toBeNull()
    expect(snapshot.canUndo).toBe(false)
  }
  function pendingOld() {
    expect(r1.issued).toBe(true); expect(r1.settled).toBe(false)
    expect(gets[3]!.outcome).toStrictEqual({ status: 'pending' })
    const pending = tasks.filter((task) => task.outcome.status === 'pending')
    expect(pending).toHaveLength(3)
    pending.forEach((task) => {
      expect(task.cohort).toBe('old')
      expect(['fulfilled', 'staleread']).toContain(task.expectedOutcome)
    })
    tasks.filter((task) => task.outcome.status !== 'pending').forEach(outcome)
    revoked(required(oldOwner, 'old owner'), true)
  }
  const onError = (event: ErrorEvent) => { callbackFailure(event.error ?? event.message) }
  const onRejection = (event: PromiseRejectionEvent) => { callbackFailure(event.reason) }
  async function start() {
    window.addEventListener('error', onError); window.addEventListener('unhandledrejection', onRejection)
    localStorage.setItem('token', TOKEN1); localStorage.setItem(WORKSPACE_STORAGE_KEY, A)
    mounted = render(<Shell />, { reactStrictMode: false })
    expect(auth1.issued).toBe(true)
    await act(async () => { auth1.settle(); await tick(); await tick() }); await flush()
    expect(entry2.issued).toBe(true)
    await act(async () => { entry2.settle(); await drain('old') }); await flush()
  }
  async function settleAuthPair() {
    // Finite native React effect checkpoints, never wait for an unissued port.
    if (auth5.issued && !auth6.issued) { await flush(); await flush() }
    if (auth5.issued && !auth6.issued) {
      throw new Error('Cleanup: distinct-token effect GET6 did not issue while GET5 held')
    }
    if (auth5.issued && auth6.issued && (!auth5.settled || !auth6.settled)) {
      phase = 'fresh-entry'; auth5.settle(); auth6.settle()
    }
  }
  async function cleanup() {
    const failures: unknown[] = []
    async function step(action: () => void | Promise<void>) {
      try { await action() } catch (failure: unknown) { failures.push(failure) }
    }
    cleaning = true
    await step(async () => { await act(async () => { await settleAuthPair() }) })
    // Let already-elected writes issue with their original guards before retiring
    // the held old HTTP. This is a publication checkpoint, not an old-cohort drain.
    await step(flush)
    await step(async () => {
      // If mount/effect failed, unmount first instead of fabricating a GET6.
      if (auth5.issued && !auth6.issued) mounted?.unmount()
      allPorts.forEach((port) => { if (port.issued) port.settle() })
      await act(async () => { await drain('all') }); await act(async () => { await drain('all') })
    })
    await step(async () => { await act(async () => { mounted?.unmount(); await drain('all') }) })
    await step(() => {
      owners.forEach((current) => { revoked(current, false) })
      expect(unmounts).toBe(mounts)
      puts.forEach((call) => { expect(call.binding.isCurrent()).toBe(false) })
    })
    await step(() => {
      tasks.forEach(outcome); gets.forEach((call) => { expect(call.outcome).toStrictEqual({ status: 'fulfilled' }) })
      allPorts.filter((port) => port.issued).forEach((port) => { expect(port.settled).toBe(true) })
      cardinality(gets.length, puts.length, put2.settled ? 1 : 0)
    })
    for (const unsubscribe of unsubscribes) await step(unsubscribe)
    await step(() => { client.clear() })
    await step(async () => { await act(async () => { await drain('all') }) })
    await step(() => { expect(violations).toStrictEqual([]); expect(unexpected).toStrictEqual([]) })
    await step(() => { window.removeEventListener('error', onError) })
    await step(() => { window.removeEventListener('unhandledrejection', onRejection) })
    for (const spy of writers) await step(() => { spy.mockRestore() })
    for (const { key, value } of storage) await step(() => {
      if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value)
    })
    for (const port of [ports.me, ports.saveLayout, ports.updateMe, ports.login, ports.verify2fa, ports.register, ports.toastError]) {
      await step(() => { port.mockReset() })
    }
    return failures
  }
  return { client, state, owners, offlineError, start, cleanup, flush, drain, observe, outcome, cardinality,
    stable, request, revoked, pendingOld, preservedInputs, hook, owner, auth, gets, puts,
    auth5, auth6, entry7, post3, r1, post8, put1, put2, put3,
    setPhase: (next: Phase) => { phase = next }, phase: () => phase }
}

it('rejects old recovery READ across real logout and distinct-token relogin after a fresh owner saves', async () => {
  const h = harness()
  let primary: unknown, failed = false, cleanupFailures: unknown[] = []
  try {
    expect(TOKEN2).not.toBe(TOKEN1); expect(PF0).toStrictEqual(P0)
    await h.start()
    const old = h.owner(), store = old.store, g = old.authGeneration
    h.stable(old, g, oldSeed, TOKEN1); expect([...h.owners]).toStrictEqual([old]); h.cardinality(2, 0, 0)
    expect(store.getSnapshot(A).layout).toStrictEqual(P0); expect(store.getSnapshot(A).writable).toBe(true)
    act(() => { h.hook().openCustomize() }); expect(store.retain(A)).toBe('dashboard-session-1')
    act(() => { h.hook().move('period-transactions', 'up') }); expect(h.hook().draft).toStrictEqual(P1)
    expect(validateCommittedDashboardLayout(A, acceptedOld, DASHBOARD_WIDGETS)).toStrictEqual(acceptedOld)
    h.setPhase('save1'); act(() => { h.hook().closeCustomize(true) })
    const task1 = store.finalize('dashboard-session-1'), observed1 = h.observe(task1, 'old', 'fulfilled')
    expect(store.finalize('dashboard-session-1')).toBe(task1)
    await h.flush(); expect(h.put1.issued).toBe(true); h.request(0, P1)
    await act(async () => { h.put1.settle(); await tick(); await tick() }); await h.flush()
    expect(h.post3.issued).toBe(true); expect(h.puts[0]!.binding.isCurrent()).toBe(false)
    await act(async () => { h.post3.settle(); await observed1.handled; await h.drain('old') }); await h.flush()
    const successful = store.getSnapshot(A)
    expect(successful.layout).toStrictEqual(P1); expect(successful.overlay).toStrictEqual({ [A]: P1 })
    expect(successful.state).toBe('closed-success'); expect(successful.writable).toBe(true)
    expect(successful.pending).toBe(false); expect(successful.readPending).toBe(false)
    expect(successful.counters.success).toBe(1); h.outcome(observed1); h.cardinality(3, 1, 0)
    act(() => { h.hook().openCustomize() }); act(() => { h.hook().move('hero-balance', 'down') })
    expect(h.hook().draft).toStrictEqual(P2)
    const session = required(store.retain(A), 'old second edit'); expect(session).toBe('dashboard-session-2')
    h.setPhase('save2'); act(() => { h.hook().closeCustomize(true) })
    const originalTask = store.finalize(session), originalObservation = h.observe(originalTask, 'old', 'saveoffline')
    const duplicateTask = store.finalize(session), duplicateObservation = h.observe(duplicateTask, 'old', 'saveoffline')
    expect(duplicateTask).toBe(originalTask)
    await h.flush(); expect(h.put2.issued).toBe(true); h.request(1, P2)
    await act(async () => { h.put2.settle(); await originalObservation.handled; await h.drain('old') }); await h.flush()
    h.outcome(originalObservation); h.outcome(duplicateObservation); h.cardinality(3, 2, 1)
    const capture = required(store.getSnapshot(A).recovery, 'old failed capture')
    expect(capture).toStrictEqual({ attemptId: 'dashboard-attempt-2', actorId: ACTOR, authGeneration: g,
      workspaceId: A, epoch: 2, baseline: P1, submitted: P2, category: 'unknown', phase: 'needs-recheck', recheck: null })
    const capturedValue = structuredClone(capture), captureRefs = identities(capture)
    const baseline = capture.baseline, submitted = capture.submitted
    const failedSnapshot = store.getSnapshot(A)
    expect(failedSnapshot.layout).toStrictEqual(P1); expect(failedSnapshot.overlay).toStrictEqual({ [A]: P1 })
    expect(failedSnapshot.openedBaseline).toStrictEqual(P1); expect(failedSnapshot.draft).toStrictEqual(P2)
    expect(failedSnapshot.state).toBe('unknown'); expect(failedSnapshot.writable).toBe(false)
    expect(failedSnapshot.pending).toBe(false); expect(failedSnapshot.readPending).toBe(false)
    expect(failedSnapshot.counters).toStrictEqual(successful.counters); expect(h.hook().customizeOpen).toBe(false)
    frozen(capture)
    for (const body of [lateOldUser, freshSeed, freshEntryUser, freshPost]) {
      const prefs = body.preferences, map = prefs.dashboard_layouts, leaf = map[A]
      for (const value of [body, prefs, map, leaf, ...leaf.items]) expect(Object.getPrototypeOf(value)).toBe(Object.prototype)
      for (const [value, key] of [[body, 'id'], [body, 'preferences'], [prefs, 'dashboard_layouts'], [map, A]] as const) {
        expect(Object.prototype.hasOwnProperty.call(value, key)).toBe(true)
      }
      expect(body.id).toBe(ACTOR); expect(leaf.workspaceId).toBe(A); expect(leaf.version).toBe(1)
    }
    const rawOld = lateOldUser.preferences.dashboard_layouts[A]
    expect(validateDashboardLayoutRequest(A, rawOld, DASHBOARD_WIDGETS)).toStrictEqual(P2)
    expect(validateDashboardLayoutRequest(A, rawOld, DASHBOARD_WIDGETS)).toStrictEqual(submitted)
    expect(rawOld).not.toBe(h.puts[1]!.input); expect(rawOld.items).not.toBe(h.puts[1]!.input.items)
    for (const [body, layout] of [[freshSeed, PF0], [freshEntryUser, PF0], [freshPost, PF1]] as const) {
      expect(validateDashboardLayoutRequest(A, body.preferences.dashboard_layouts[A], DASHBOARD_WIDGETS)).toStrictEqual(layout)
      expect(layout).not.toStrictEqual(P2)
    }
    const rawBodies = [lateOldUser, freshSeed, freshEntryUser, freshPost]
    rawBodies.forEach((body, index) => {
      const refs = identities(body)
      rawBodies.slice(index + 1).forEach((other) => {
        const otherRefs = identities(other); refs.forEach((ref) => { expect(otherRefs).not.toContain(ref) })
      })
    })
    h.setPhase('recheck')
    let r1Task!: Promise<void>, r1Duplicate!: Promise<void>
    let r1Observation!: ReturnType<typeof h.observe>, r1DuplicateObservation!: ReturnType<typeof h.observe>
    act(() => {
      r1Task = h.hook().recheckRecovery(capture.attemptId); r1Observation = h.observe(r1Task, 'old', 'staleread')
      r1Duplicate = h.hook().recheckRecovery(capture.attemptId); r1DuplicateObservation = h.observe(r1Duplicate, 'old', 'staleread')
    })
    expect(r1Duplicate).toBe(r1Task); expect(h.r1.issued).toBe(true)
    const start = required(h.gets[3]!.start, 'synchronous R1 transport witness')
    expect(start.recovery?.phase).toBe('rechecking'); expect(start.recovery?.recheck).toBeNull()
    expect(start.readPending).toBe(true); expect(start.writable).toBe(false)
    const rechecking = required(store.getSnapshot(A).recovery, 'external rechecking capture')
    const recheckingValue = structuredClone(rechecking), recheckingRefs = identities(rechecking)
    expect(rechecking).toStrictEqual({ ...capturedValue, phase: 'rechecking' })
    expect(rechecking.baseline).toBe(baseline); expect(rechecking.submitted).toBe(submitted); frozen(rechecking)
    function externalUnchanged() {
      for (const [value, before, refs] of [[capture, capturedValue, captureRefs], [rechecking, recheckingValue, recheckingRefs]] as const) {
        expect(value).toStrictEqual(before); frozen(value)
        const now = identities(value); expect(now).toHaveLength(refs.length)
        now.forEach((ref, index) => { expect(ref).toBe(refs[index]) })
        expect(value.baseline).toBe(baseline); expect(value.submitted).toBe(submitted)
      }
      expect(duplicateTask).toBe(originalTask); h.outcome(originalObservation); h.outcome(duplicateObservation)
      h.preservedInputs()
    }
    await h.r1.issuedBarrier; await h.flush()
    expect(h.hook().recovery).toBe(rechecking); h.cardinality(4, 2, 1)
    expect(r1Observation.outcome).toStrictEqual({ status: 'pending' })
    expect(r1DuplicateObservation.outcome).toStrictEqual({ status: 'pending' })
    let immediate!: SessionSnapshot, immediateGeneration!: number
    h.setPhase('logged-out')
    act(() => {
      h.auth().logout()
      immediate = store.getSnapshot(A); immediateGeneration = getDashboardAuthSnapshot(h.client).generation
    })
    expect(immediateGeneration).toBe(g + 1)
    expect(immediate).toStrictEqual({ ...successful, layout: P0, provenance: 'default', overlay: {}, state: 'closed',
      writable: false, pending: false, readPending: true, openedBaseline: null, draft: null, recovery: null, canUndo: false })
    h.puts.forEach((call) => { expect(call.binding.isCurrent()).toBe(false) })
    externalUnchanged(); h.pendingOld()
    await h.flush(); await h.flush()
    expect(h.auth().user).toBeNull(); expect(h.auth().token).toBeNull(); expect(h.auth().isLoading).toBe(false)
    expect(localStorage.getItem('token')).toBeNull(); expect(h.state.owner).toBeNull()
    expect(h.state.client).toBe(h.client); expect(h.state.workspace).toBe(workspaceA)
    expect(localStorage.getItem(WORKSPACE_STORAGE_KEY)).toBe(A)
    expect(h.hook().recovery).toBeNull(); expect(h.hook().customizeOpen).toBe(false); expect(h.hook().draft).toStrictEqual(P0)
    h.cardinality(4, 2, 1); h.pendingOld()
    h.setPhase('login-explicit')
    act(() => { h.auth().loginWithToken(TOKEN2) })
    expect(h.auth5.issued).toBe(true)
    expect(getDashboardAuthSnapshot(h.client).generation).toBe(g + 2); expect(localStorage.getItem('token')).toBe(TOKEN2)
    await h.flush(); await h.flush()
    expect(h.auth6.issued).toBe(true)
    await h.auth5.issuedBarrier; await h.auth6.issuedBarrier
    expect(h.auth5.settled).toBe(false); expect(h.auth6.settled).toBe(false)
    expect(h.state.owner).toBeNull(); expect(h.auth().user).toBeNull(); expect(h.auth().token).toBe(TOKEN2)
    expect(h.entry7.issued).toBe(false); h.cardinality(6, 2, 1)
    h.setPhase('fresh-entry')
    await act(async () => {
      h.auth5.settle(); h.auth6.settle()
      await required(h.gets[4]!.handled, 'explicit Auth HTTP observer')
      await required(h.gets[5]!.handled, 'token-effect Auth HTTP observer'); await tick(); await tick()
    }); await h.flush(); await h.flush()
    const fresh = h.owner(), freshStore = fresh.store
    expect(fresh).not.toBe(old); expect(freshStore).not.toBe(store); expect([...h.owners]).toStrictEqual([old, fresh])
    h.stable(fresh, g + 2, freshSeed, TOKEN2)
    expect(h.entry7.issued).toBe(true); expect(h.entry7.settled).toBe(false)
    expect(h.gets[6]!.start?.readPending).toBe(true); expect(h.gets[6]!.start?.writable).toBe(false)
    expect(h.gets[6]!.start?.recovery).toBeNull(); expect(h.gets[6]!.start?.overlay).toStrictEqual({})
    expect(h.gets[6]!.start?.counters.success).toBe(0)
    expect(h.gets[6]!.start?.layout).toStrictEqual(PF0)
    await act(async () => { h.entry7.settle(); await h.drain('fresh') }); await h.flush()
    const freshReady = freshStore.getSnapshot(A)
    expect(freshReady.layout).toStrictEqual(PF0); expect(freshReady.provenance).toBe('saved')
    expect(freshReady.state).toBe('closed'); expect(freshReady.writable).toBe(true)
    expect(freshReady.pending).toBe(false); expect(freshReady.readPending).toBe(false)
    expect(freshReady.recovery).toBeNull(); expect(freshReady.overlay).toStrictEqual({})
    expect(freshReady.openedBaseline).toBeNull(); expect(freshReady.draft).toBeNull(); expect(freshReady.counters.success).toBe(0)
    expect(h.hook().draft).toStrictEqual(PF0); expect(h.hook().status).toBe('saved'); expect(h.hook().customizeOpen).toBe(false)
    h.pendingOld(); externalUnchanged(); h.cardinality(7, 2, 1); h.setPhase('fresh-ready')
    act(() => { h.hook().openCustomize() })
    expect(h.hook().customizeOpen).toBe(true)
    const freshSession = required(freshStore.retain(A), 'fresh actual session'); expect(freshSession).toBe('dashboard-session-1')
    let toggled = false
    act(() => { toggled = h.hook().toggleVisible('goals-progress') })
    expect(toggled).toBe(true); expect(h.hook().draft).toStrictEqual(PF1)
    expect(validateCommittedDashboardLayout(A, acceptedFresh, DASHBOARD_WIDGETS)).toStrictEqual(acceptedFresh)
    h.setPhase('save-fresh'); act(() => { h.hook().closeCustomize(true) })
    const freshTask = freshStore.finalize(freshSession), freshObservation = h.observe(freshTask, 'fresh', 'fulfilled')
    expect(freshStore.finalize(freshSession)).toBe(freshTask)
    await h.flush(); expect(h.put3.issued).toBe(true); h.request(2, PF1)
    expect(h.put3.settled).toBe(false); expect(freshStore.getSnapshot(A).pending).toBe(true)
    expect(acceptedFresh.layout).not.toBe(h.puts[2]!.input)
    await h.flush(); expect(h.puts[2]!.binding.isCurrent()).toBe(true)
    h.stable(fresh, g + 2, freshSeed, TOKEN2); h.cardinality(7, 3, 1)
    await act(async () => { h.put3.settle(); await tick(); await tick() }); await h.flush()
    expect(h.post8.issued).toBe(true); expect(h.post8.settled).toBe(false)
    expect(h.puts[2]!.binding.isCurrent()).toBe(false)
    expect(freshStore.getSnapshot(A).state).toBe('closed-success'); expect(freshStore.getSnapshot(A).writable).toBe(true)
    expect(freshStore.getSnapshot(A).readPending).toBe(true); expect(freshObservation.outcome).toStrictEqual({ status: 'pending' })
    await act(async () => { h.post8.settle(); await freshObservation.handled; await h.drain('fresh') }); await h.flush()
    h.outcome(freshObservation); h.pendingOld(); externalUnchanged(); h.cardinality(8, 3, 1)
    const freshPositive = freshStore.getSnapshot(A)
    expect(freshPositive.layout).toStrictEqual(PF1); expect(freshPositive.overlay).toStrictEqual({ [A]: PF1 })
    expect(freshPositive.state).toBe('closed-success'); expect(freshPositive.provenance).toBe('saved')
    expect(freshPositive.writable).toBe(true); expect(freshPositive.pending).toBe(false); expect(freshPositive.readPending).toBe(false)
    expect(freshPositive.recovery).toBeNull(); expect(freshPositive.openedBaseline).toBeNull(); expect(freshPositive.draft).toBeNull()
    expect(freshPositive.counters).toStrictEqual({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
    expect(store.getSnapshot(A).counters.success).toBe(1)
    expect(h.hook().customizeOpen).toBe(false); expect(h.hook().draft).toStrictEqual(PF1); expect(h.hook().recovery).toBeNull()
    const oldBefore = structuredClone(store.getSnapshot(A)), freshBefore = structuredClone(freshPositive)
    const bridgeBefore = getDashboardAuthSnapshot(h.client), bridgeValue = structuredClone(bridgeBefore)
    frozen(bridgeBefore); h.stable(fresh, g + 2, freshSeed, TOKEN2)
    // Raw HTTP success is separate from the old STORE task's stale rejection.
    await act(async () => { h.r1.settle(); await r1Observation.handled; await h.drain('all') }); await h.flush()
    h.outcome(r1Observation); h.outcome(r1DuplicateObservation)
    expect(r1Duplicate).toBe(r1Task); expect(r1Observation.outcome).toStrictEqual(r1DuplicateObservation.outcome)
    if (r1Observation.outcome.status === 'rejected' && r1DuplicateObservation.outcome.status === 'rejected') {
      expect(r1DuplicateObservation.outcome.failure).toBe(r1Observation.outcome.failure)
    }
    expect(h.gets[3]!.outcome).toStrictEqual({ status: 'fulfilled' }); expect(h.gets[3]!.user).toBe(lateOldUser)
    expect(store.getSnapshot(A)).toStrictEqual({ ...oldBefore, readPending: false })
    expect(freshStore.getSnapshot(A)).toStrictEqual(freshBefore)
    expect(getDashboardAuthSnapshot(h.client)).toBe(bridgeBefore); expect(bridgeBefore).toStrictEqual(bridgeValue)
    h.revoked(old, false); h.stable(fresh, g + 2, freshSeed, TOKEN2); externalUnchanged(); h.cardinality(8, 3, 1)
    expect([...h.owners]).toStrictEqual([old, fresh])
    h.gets.forEach((call) => { expect(call.outcome).toStrictEqual({ status: 'fulfilled' }); expect(call.storage).toBe(A); expect(call.target).toBe(A) })
    expect(h.gets.map((call) => call.phase)).toStrictEqual(['auth', 'entry', 'post-old', 'recheck', 'login-explicit', 'login-effect', 'fresh-entry', 'post-fresh'])
    expect(h.gets.map((call) => call.user)).toStrictEqual([oldSeed, oldSeed, oldPost, lateOldUser, freshSeed, freshSeed, freshEntryUser, freshPost])
    const expectedUsers = [oldSeed, oldSeed, oldPost, lateOldUser, freshSeed, freshSeed, freshEntryUser, freshPost]
    h.gets.forEach((call, index) => { expect(call.user).toBe(expectedUsers[index]) })
    expect(h.gets.map((call) => call.owner)).toStrictEqual([null, old, old, old, null, null, fresh, fresh])
    expect(h.gets.map((call) => call.generation)).toStrictEqual([g, g, g, g, g + 2, g + 2, g + 2, g + 2])
    expect(h.puts.map((call) => call.phase)).toStrictEqual(['save1', 'save2', 'save-fresh'])
    expect(h.puts.map((call) => call.owner)).toStrictEqual([old, old, fresh])
    expect(h.puts.map((call) => call.token)).toStrictEqual([TOKEN1, TOKEN1, TOKEN2])
    h.puts.forEach((call) => { expect(call.binding.isCurrent()).toBe(false) }); expect(h.phase()).toBe('done')
  } catch (failure: unknown) { primary = failure; failed = true }
  finally { cleanupFailures = await h.cleanup() }
  if (failed) {
    if (cleanupFailures.length) console.error('Auth revocation cleanup diagnostics; primary preserved', cleanupFailures)
    throw primary
  }
  if (cleanupFailures.length) throw new AggregateError(cleanupFailures, 'Auth revocation cleanup failed')
})
