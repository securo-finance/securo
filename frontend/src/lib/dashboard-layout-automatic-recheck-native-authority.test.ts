import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease, isLivePageLeaseWitness, type PageBinding, type PageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, type LivePageLeaseWitness, type Recovery, type SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// AUTHOR_ONLY / NOT_RUN. Native store and native lease; only constructor I/O.
// No SDK/Auth/provider/hook/App/DOM/browser or HTTP-interceptor acceptance claim.
const A = 'a173ed00-143c-4f21-8cc7-f4e83cd14b91'
const ACTOR = 'c398af20-365e-4133-aee9-b6a05ef36db3'
const GENERATION = 31

type Outcome = { rejected: false } | { rejected: true; error: unknown }

function frozen<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { frozen(child) })
    Object.freeze(value)
  }
  return value
}

// Literal independent canonical leaves: no resolver-generated expected data.
const INITIAL: DashboardLayout = frozen({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const SUCCESSFUL: DashboardLayout = frozen({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const SUBMITTED: DashboardLayout = frozen({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: false, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const TARGET: DashboardLayout = frozen({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: false, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
// Independent strict reply, never built from a captured request.
const COMMITTED: CommittedDashboardLayout = frozen({ workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })

// Raw actor/root/map wrapper only; it does not validate, normalize or echo PUTs.
function rawUser(layout: DashboardLayout): User {
  return frozen({
    id: ACTOR, email: 'native-automatic-authority@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Native authority qualifier',
      onboarding_completed: true, dashboard_layouts: { [A]: layout },
    },
  })
}
const INITIAL_USER = rawUser(INITIAL)
const POST_USER = rawUser(SUCCESSFUL)
const TARGET_USER = rawUser(TARGET)
const FIXTURES = frozen({ INITIAL_USER, POST_USER, TARGET_USER, SUBMITTED, COMMITTED })
const FIXTURES_BEFORE = frozen(structuredClone(FIXTURES))
const REGISTRY_BEFORE = frozen(structuredClone(DASHBOARD_WIDGETS))
const ZERO = frozen({ success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })

function expectFrozenPublic(actual: unknown, expected: unknown): void {
  expect(actual).toStrictEqual(expected)
  if (actual !== null && typeof actual === 'object' && expected !== null && typeof expected === 'object') {
    expect(Object.isFrozen(actual)).toBe(true)
    expect(Object.getPrototypeOf(actual)).toBe(Array.isArray(expected) ? Array.prototype : Object.prototype)
    expect(new Set(Reflect.ownKeys(actual))).toStrictEqual(new Set(Reflect.ownKeys(expected)))
    const fields = actual as Record<string, unknown>
    const expectedFields = expected as Record<string, unknown>
    for (const key of Object.keys(expectedFields)) {
      expect(Object.getOwnPropertyDescriptor(actual, key)?.enumerable).toBe(true)
      expectFrozenPublic(fields[key], expectedFields[key])
    }
  }
}

function port<T>(reply: T | Error) {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  let settled = false
  const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse })
  void promise.catch(() => {}) // Also handle a port never issued after an early failure.
  return {
    promise,
    settle() {
      if (settled) return
      settled = true
      if (reply instanceof Error) reject(reply)
      else resolve(reply)
    },
    isSettled: () => settled,
  }
}

function turn(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

function rig(nativePositive: boolean) {
  const error409 = frozen(Object.assign(new Error('Independent original native HTTP409'), {
    response: { status: 409, data: { detail: 'Independent original layout conflict' } },
  }))
  const errorResponse = error409.response
  const errorData = errorResponse.data
  const initial = port(INITIAL_USER)
  const success = port(COMMITTED)
  const post = port(POST_USER)
  const conflict = port<CommittedDashboardLayout>(error409)
  const automatic = port(TARGET_USER)
  const ports = [initial, success, post, conflict, automatic]
  const events: Array<'GET' | 'PUT'> = []
  const writes: Array<{
    workspaceId: string; body: DashboardLayout; binding: DashboardRequestBinding; currentAtIssuance: boolean
  }> = []
  let reads = 0
  const observed: Array<Promise<Outcome>> = []
  const publications: SessionSnapshot[] = []
  const store = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: GENERATION,
    authSeed: INITIAL_USER,
    getTokenIdentity: () => 'synthetic-native-automatic-authority-token',
    getWorkspaceId: () => A,
    api: {
      me() {
        reads += 1
        events.push('GET')
        if (reads === 1) return initial.promise
        if (nativePositive && reads === 2) return post.promise
        if (nativePositive && reads === 3) return automatic.promise
        // An illicit negative GET settles strictly and immediately: the count/
        // phase oracle fails, rather than whenIdle hanging behind a hidden port.
        return Promise.resolve(TARGET_USER)
      },
      saveLayout(workspaceId, body, binding) {
        events.push('PUT')
        writes.push({ workspaceId, body: structuredClone(body), binding, currentAtIssuance: binding.isCurrent() })
        if (nativePositive && writes.length === 1) return success.promise
        if (writes.length === (nativePositive ? 2 : 1)) return conflict.promise
        return Promise.reject(new Error('Unexpected native authority PUT'))
      },
    },
  })
  const owner: {
    lease: PageSessionLease | null
    bindings: PageBinding[]
  } = { lease: null, bindings: [] }
  const unsubscribe = store.subscribe(() => { publications.push(store.getSnapshot(A)) })
  function observe(task: Promise<void>) {
    const observation: { rejectionSnapshot: SessionSnapshot | null; outcome: Promise<Outcome> } = {
      rejectionSnapshot: null, outcome: Promise.resolve({ rejected: false }),
    }
    observation.outcome = task.then<Outcome, Outcome>(() => ({ rejected: false }), (error: unknown) => {
      // Observation only, outside assertions. Registered before native kickoff,
      // this preserves the actual initial needs-recheck capture by reference.
      observation.rejectionSnapshot = store.getSnapshot(A)
      return { rejected: true, error }
    })
    observed.push(observation.outcome)
    return observation
  }
  async function idle(): Promise<void> {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const before = [reads, writes.length, publications.length]
      await store.whenIdle()
      if (owner.lease) await owner.lease.whenIdle()
      await Promise.all(observed)
      await turn()
      await turn()
      if (before[0] === reads && before[1] === writes.length && before[2] === publications.length) return
    }
    throw new Error('Native authority task/publication fixed point did not settle')
  }
  return { store, owner, ports, initial, success, post, conflict, automatic, events, writes,
    publications, observed, observe, idle, unsubscribe, error409, errorResponse, errorData, reads: () => reads }
}
type Rig = ReturnType<typeof rig>

async function finite(owner: Rig, body: () => Promise<void>): Promise<void> {
  let primaryFailed = false
  let primaryError: unknown
  const cleanupErrors: unknown[] = []
  try {
    await body()
  } catch (error: unknown) {
    primaryFailed = true
    primaryError = error
  } finally {
    // Settle every port, then drain issued native work before terminal teardown.
    for (const input of owner.ports) {
      try { input.settle() } catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await owner.idle() } catch (error: unknown) { cleanupErrors.push(error) }
    try { owner.store.revoke() } catch (error: unknown) { cleanupErrors.push(error) }
    for (const binding of owner.owner.bindings) {
      try { binding.release() } catch (error: unknown) { cleanupErrors.push(error) }
    }
    try {
      await owner.idle()
      expect(owner.ports.every((input) => input.isSettled())).toBe(true)
      expect(owner.store.getSnapshot(A).pending).toBe(false)
      expect(owner.store.getSnapshot(A).readPending).toBe(false)
      expect(FIXTURES).toStrictEqual(FIXTURES_BEFORE)
      expect(DASHBOARD_WIDGETS).toStrictEqual(REGISTRY_BEFORE)
      expect(owner.error409.response).toBe(owner.errorResponse)
      expect(owner.error409.response.data).toBe(owner.errorData)
      expect(owner.error409.response).toStrictEqual({ status: 409, data: { detail: 'Independent original layout conflict' } })
    } catch (error: unknown) { cleanupErrors.push(error) }
    owner.unsubscribe()
  }
  // Never throw inside finally or replace a primary body failure with teardown.
  if (primaryFailed) {
    if (cleanupErrors.length) throw new AggregateError([primaryError, ...cleanupErrors], 'Primary native authority failure with separate cleanup diagnostics', { cause: primaryError })
    throw primaryError
  }
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'Native authority cleanup failed')
}

async function qualifiedInitial(owner: Rig): Promise<void> {
  const read = owner.observe(owner.store.refresh(A))
  owner.initial.settle()
  expect(await read.outcome).toStrictEqual({ rejected: false })
  await owner.idle()
  expectFrozenPublic(owner.store.getSnapshot(A), {
    actorId: ACTOR, authGeneration: GENERATION, workspaceId: A,
    provenance: 'saved', writable: true, state: 'closed', pending: false, readPending: false,
    canUndo: false, layout: INITIAL, openedBaseline: null, draft: null, recovery: null,
    overlay: {}, counters: ZERO,
  })
  expect(owner.store.getSnapshot(A).overlay).toStrictEqual({})
  expect(owner.reads()).toBe(1)
  expect(owner.writes).toStrictEqual([])
}

function expectedConflict(positive: boolean): Recovery {
  return frozen({
    attemptId: positive ? 'dashboard-attempt-2' : 'dashboard-attempt-1',
    actorId: ACTOR, authGeneration: GENERATION, workspaceId: A, epoch: positive ? 2 : 1,
    baseline: positive ? SUCCESSFUL : INITIAL, submitted: SUBMITTED,
    category: 'conflict', phase: 'needs-recheck', recheck: null,
  })
}

function expectedFailedSnapshot(positive: boolean, recovery: Recovery, readPending = false): SessionSnapshot {
  return frozen<SessionSnapshot>({
    actorId: ACTOR, authGeneration: GENERATION, workspaceId: A,
    provenance: 'saved', writable: false, state: 'conflict', pending: false, readPending,
    canUndo: false, layout: positive ? SUCCESSFUL : INITIAL,
    openedBaseline: positive ? SUCCESSFUL : INITIAL, draft: SUBMITTED, recovery,
    overlay: positive ? { [A]: SUCCESSFUL } : {},
    counters: { success: positive ? 1 : 0, suppressed: 0, conflict409: 1, other4xx: 0, server5xx: 0 },
  })
}

async function originalRejection(owner: Rig, observation: ReturnType<Rig['observe']>): Promise<void> {
  const outcome = await observation.outcome
  expect(outcome.rejected).toBe(true)
  if (!outcome.rejected) throw new Error('Original native HTTP409 unexpectedly fulfilled')
  expect(outcome.error).toBe(owner.error409)
}

function issuedPut(owner: Rig, index: number, expected: DashboardLayout): void {
  expect(owner.writes).toHaveLength(index + 1)
  const write = owner.writes[index]
  expect(write.workspaceId).toBe(A)
  expect(write.body).toStrictEqual(expected)
  expect(write.currentAtIssuance).toBe(true)
  expect(write.binding.isCurrent()).toBe(true)
  expect(owner.store.getSnapshot(A).pending).toBe(true)
}

it('ordinary finalize with a fabricated page witness retains original CURRENT409 without electing an automatic GET', async () => {
  const owner = rig(false)
  await finite(owner, async () => {
    await qualifiedInitial(owner)
    // No getPageSessionLease/bind/select/open native holder exists in this case.
    expect(owner.owner.lease).toBeNull()
    expect(owner.owner.bindings).toStrictEqual([])
    const session = owner.store.open(A)
    owner.store.setDraft(session, SUBMITTED.items)
    const witness: LivePageLeaseWitness = Object.freeze({
      store: owner.store, workspaceId: A, continuity: Object.freeze({}), isCurrent: () => true,
    })
    expect(Object.isFrozen(witness)).toBe(true)
    expect(witness.isCurrent()).toBe(true)
    expect(isLivePageLeaseWitness(witness, owner.store, A)).toBe(false)
    const task = owner.store.finalize(session, witness)
    const original = owner.observe(task) // Attach before the controlled rejection.
    expect(owner.store.finalize(session, witness)).toBe(task)
    await turn()
    issuedPut(owner, 0, SUBMITTED)
    expect(owner.reads()).toBe(1)
    owner.conflict.settle()
    await originalRejection(owner, original)
    const originalSnapshot = original.rejectionSnapshot
    if (!originalSnapshot?.recovery) throw new Error('Missing actual original conflict capture')
    const capture = originalSnapshot.recovery
    const captureBefore = frozen(structuredClone(capture))
    expectFrozenPublic(capture, expectedConflict(false))
    await owner.idle() // Real writer/kickoff task drain; no manual recovery call.

    // Primary functional oracle: reject only invented auto-read authority, not PUT.
    expect(owner.reads(), 'fabricated ordinary-finalize authority must not elect automatic GET').toBe(1)
    const failed = owner.store.getSnapshot(A)
    expectFrozenPublic(failed, expectedFailedSnapshot(false, expectedConflict(false)))
    expect(failed.recovery).toBe(capture)
    expect(failed.overlay).toStrictEqual({})
    expect(owner.events).toStrictEqual(['GET', 'PUT'])
    expect(owner.writes).toHaveLength(1)
    expect(capture).toStrictEqual(captureBefore)
    expect(owner.store.getPersistenceStatus(A)).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: true, canCustomize: false, failureKind: 'conflict',
    })
    expect(owner.store.retain(A)).toBeNull()
    expect(() => owner.store.open(A)).toThrow('Dashboard layout session cannot open')
    expect(() => owner.store.setDraft(session, INITIAL.items)).toThrow('Dashboard layout session is not editable')
    expect(owner.store.finalize(session)).toBe(task)
    await originalRejection(owner, owner.observe(task))
    await owner.idle()
    expect(owner.store.getSnapshot(A)).toBe(failed)
    expect(owner.reads()).toBe(1)
    expect(owner.writes).toHaveLength(1)
    expect(isLivePageLeaseWitness(witness, owner.store, A)).toBe(false)
  })
})

it('genuine current native page close elects one automatic CURRENT409 GET while preserving original rejection and immutable capture', async () => {
  const owner = rig(true)
  await finite(owner, async () => {
    await qualifiedInitial(owner)
    const lease = getPageSessionLease(owner.store)
    owner.owner.lease = lease
    const binding = lease.bind(A)
    owner.owner.bindings.push(binding)

    // Real changed native success through the same constructors and finite ports.
    const successfulTicket = binding.open()
    owner.store.setDraft(successfulTicket.sessionId, SUCCESSFUL.items)
    const successTask = binding.close(successfulTicket, true)
    const successOutcome = owner.observe(successTask)
    await turn()
    issuedPut(owner, 0, SUCCESSFUL)
    owner.success.settle()
    owner.post.settle()
    expect(await successOutcome.outcome).toStrictEqual({ rejected: false })
    await owner.idle()
    expectFrozenPublic(owner.store.getSnapshot(A), {
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: A,
      provenance: 'saved', writable: true, state: 'closed-success', pending: false, readPending: false,
      canUndo: false, layout: SUCCESSFUL, openedBaseline: null, draft: null, recovery: null,
      overlay: { [A]: SUCCESSFUL }, counters: { ...ZERO, success: 1 },
    })
    expect(owner.writes[0].binding.isCurrent()).toBe(false) // Consumed request, live holder.
    expect(owner.store.getPersistenceStatus(A).canCustomize).toBe(true)
    expect(owner.events).toStrictEqual(['GET', 'PUT', 'GET'])

    const ticket = binding.open()
    expect(ticket).not.toBe(successfulTicket)
    expect(ticket).toStrictEqual({ sessionId: 'dashboard-session-2', workspaceId: A })
    owner.store.setDraft(ticket.sessionId, SUBMITTED.items)
    const closeTask = binding.close(ticket, true) // Native close mints its own WeakSet witness.
    const close = owner.observe(closeTask)
    const coreTask = owner.store.finalize(ticket.sessionId)
    const core = owner.observe(coreTask)
    expect(binding.close(ticket, true)).toBe(closeTask)
    expect(owner.store.finalize(ticket.sessionId)).toBe(coreTask)
    await turn()
    issuedPut(owner, 1, SUBMITTED)
    expect(owner.reads()).toBe(2)
    owner.conflict.settle()
    await originalRejection(owner, core)
    await originalRejection(owner, close)
    const originalSnapshot = core.rejectionSnapshot
    if (!originalSnapshot?.recovery) throw new Error('Missing native close original conflict capture')
    const capture = originalSnapshot.recovery
    const captureBefore = frozen(structuredClone(capture))
    expectFrozenPublic(capture, expectedConflict(true))
    // Bounded microtask barrier, never idle behind the held automatic response.
    for (let attempt = 0; attempt < 8; attempt += 1) await turn()
    expect(owner.reads(), 'genuine native CURRENT409 must issue exactly one autonomous GET').toBe(3)
    expect(owner.automatic.isSettled()).toBe(false)
    const held = owner.store.getSnapshot(A)
    expectFrozenPublic(held, expectedFailedSnapshot(true, {
      ...expectedConflict(true), phase: 'rechecking', recheck: null,
    }, true))
    expect(held.recovery?.baseline).toBe(capture.baseline)
    expect(held.recovery?.submitted).toBe(capture.submitted)
    expect(owner.events).toStrictEqual(['GET', 'PUT', 'GET', 'PUT', 'GET'])
    expect(owner.writes).toHaveLength(2)
    // No manual Recheck, reviewed call, replacement or authority injection.
    owner.automatic.settle()
    await owner.idle()
    const ready = owner.store.getSnapshot(A)
    const recheck = ready.recovery?.recheck
    if (!recheck) throw new Error('Missing actual autonomous strict recovery qualification')
    expect(Number.isSafeInteger(recheck.readId)).toBe(true)
    expect(recheck.readId).toBeGreaterThan(0)
    expectFrozenPublic(ready, expectedFailedSnapshot(true, {
      ...expectedConflict(true), phase: 'review-ready',
      recheck: { readId: recheck.readId, provenance: 'saved', target: TARGET, matchesSubmitted: false },
    }))
    expect(ready.recovery?.baseline).toBe(capture.baseline)
    expect(ready.recovery?.submitted).toBe(capture.submitted)
    expect(recheck.target).not.toStrictEqual(capture.submitted)
    expect(recheck.target).not.toStrictEqual(capture.baseline)
    expect(capture).toStrictEqual(captureBefore)
    expectFrozenPublic(capture, expectedConflict(true))
    expect(owner.store.getPersistenceStatus(A)).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: true, canCustomize: false, failureKind: 'conflict',
    })
    expect(owner.store.retain(A)).toBeNull()
    expect(() => binding.open()).toThrow('Dashboard layout session cannot open')
    expect(() => owner.store.setDraft(ticket.sessionId, INITIAL.items)).toThrow('Dashboard layout session is not editable')
    expect(binding.close(ticket, false)).toBe(closeTask)
    expect(owner.store.finalize(ticket.sessionId)).toBe(coreTask)
    await originalRejection(owner, owner.observe(closeTask))
    await originalRejection(owner, owner.observe(coreTask))
    await owner.idle()
    expect(owner.store.getSnapshot(A)).toBe(ready)
    expect(owner.reads()).toBe(3)
    expect(owner.writes.map(({ body }) => body)).toStrictEqual([SUCCESSFUL, SUBMITTED])
    expect(owner.events).toStrictEqual(['GET', 'PUT', 'GET', 'PUT', 'GET'])
  })
})
