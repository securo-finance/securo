import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease, type PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, type Recovery } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// AUTHOR_ONLY / NOT_RUN: four native characterizations. Constructor I/O and
// live identity getters are the only seams; every authority comes from the lease.
const A = 'a173ed00-143c-4f21-8cc7-f4e83cd14b91'
const B = 'b287fe10-254d-4022-9dd8-a5f94de25ca2'
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

// Independently authored raw leaves, not normalizer outputs or request echoes.
const INITIAL_A: DashboardLayout = frozen({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const POSITIVE_A: DashboardLayout = frozen({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const FAILED_A: DashboardLayout = frozen({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: false, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const QUALIFIED_A: DashboardLayout = frozen({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: false, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const FRESH_A: DashboardLayout = frozen({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: false, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const INITIAL_B: DashboardLayout = frozen({ version: 1, workspaceId: B, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: false, order: 5 },
] })
const CHANGED_B: DashboardLayout = frozen({ version: 1, workspaceId: B, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: false, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: false, order: 5 },
] })

// Envelopes are separate literal server fixtures, never assembled from captured PUTs.
const POSITIVE_COMMIT: CommittedDashboardLayout = frozen({ workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const RETRY_COMMIT: CommittedDashboardLayout = frozen({ workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: false, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })
const B_COMMIT: CommittedDashboardLayout = frozen({ workspaceId: B, layout: {
  version: 1, workspaceId: B, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: false, order: 5 },
  ],
} })
const FRESH_COMMIT: CommittedDashboardLayout = frozen({ workspaceId: A, layout: {
  version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: false, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ],
} })

// This only wraps preauthored leaves in a raw actor/root/map document. It does
// not inspect requests, invoke product validators, repair, or normalize anything.
function rawUser(a: DashboardLayout, b: DashboardLayout): User {
  return frozen({
    id: ACTOR, email: 'replacement-races@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Replacement race qualifier',
      onboarding_completed: true, dashboard_layouts: { [A]: a, [B]: b },
    },
  })
}
const INITIAL_USER = rawUser(INITIAL_A, INITIAL_B)
const POSITIVE_USER = rawUser(POSITIVE_A, INITIAL_B)
const REVIEWED_USER = rawUser(QUALIFIED_A, INITIAL_B)
const RETRY_USER = rawUser(FAILED_A, INITIAL_B)
const B_USER = rawUser(POSITIVE_A, CHANGED_B)
const FRESH_USER = rawUser(FRESH_A, INITIAL_B)
const FIXTURES = frozen({ INITIAL_USER, POSITIVE_USER, REVIEWED_USER, RETRY_USER,
  B_USER, FRESH_USER, FAILED_A, CHANGED_B, POSITIVE_COMMIT, RETRY_COMMIT, B_COMMIT, FRESH_COMMIT })
const FIXTURES_BEFORE = frozen(structuredClone(FIXTURES))
const REGISTRY_BEFORE = frozen(structuredClone(DASHBOARD_WIDGETS))
const E0 = Object.freeze(new Error('Independent original transport lost its reply E0'))
const E1 = frozen(Object.assign(new Error('Independent replacement service unavailable E1'), {
  response: { status: 503, data: { detail: 'independent held replacement failure' } },
}))
const ZERO_COUNTERS = frozen({ success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
const ONE_SUCCESS = frozen({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })

function port<T>(reply: T | Error, held = false) {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  let settled = false
  const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse })
  // Even an unissued failed input must be safely settled in cleanup.
  void promise.catch(() => {})
  function settle(): void {
    if (settled) return
    settled = true
    if (reply instanceof Error) reject(reply)
    else resolve(reply)
  }
  return { promise, held, settle, isSettled: () => settled }
}
type ReadPort = ReturnType<typeof port<User>>
type WritePort = ReturnType<typeof port<CommittedDashboardLayout>>

function rig(readPorts: ReadPort[], writePorts: WritePort[], identity?: { token: string }, generation = GENERATION) {
  const auth = identity ?? { token: 'synthetic-native-replacement-owner-31' }
  let selected = A
  let reads = 0
  const writes: Array<{ workspaceId: string; body: DashboardLayout; binding: DashboardRequestBinding; currentAtIssuance: boolean }> = []
  const observed: Array<Promise<Outcome>> = []
  const bindings: PageBinding[] = []
  const store = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: generation,
    authSeed: INITIAL_USER, getTokenIdentity: () => auth.token, getWorkspaceId: () => selected,
    api: {
      me() {
        const input = readPorts[reads++]
        if (!input) return Promise.reject(new Error('Unexpected native qualifier GET'))
        if (!input.held) input.settle()
        return input.promise
      },
      saveLayout(workspaceId, body, binding) {
        const input = writePorts[writes.length]
        writes.push({ workspaceId, body: structuredClone(body), binding, currentAtIssuance: binding.isCurrent() })
        if (!input) return Promise.reject(new Error('Unexpected native qualifier PUT'))
        if (!input.held) input.settle()
        return input.promise
      },
    },
  })
  const lease = getPageSessionLease(store)
  lease.select(A)
  function bind(workspace = selected): PageBinding {
    const binding = lease.bind(workspace)
    bindings.push(binding)
    return binding
  }
  function select(workspace: string): void {
    selected = workspace
    lease.select(workspace)
  }
  function observe(task: Promise<void>): Promise<Outcome> {
    const outcome = task.then<Outcome, Outcome>(() => ({ rejected: false }), (error: unknown) => ({ rejected: true, error }))
    observed.push(outcome)
    return outcome
  }
  return { store, lease, auth, bind, select, observe, readPorts, writePorts, writes, observed, bindings,
    reads: () => reads,
    async idle() { await lease.whenIdle(); await store.whenIdle(); await turn() },
  }
}
type Rig = ReturnType<typeof rig>

function turn(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}
function recovery(owner: Rig): Recovery {
  const capture = owner.store.getSnapshot(A).recovery
  expect(capture).not.toBeNull()
  if (!capture) throw new Error('Required public recovery was absent')
  return capture
}
async function rejectedExactly(owner: Rig, task: Promise<void>, error: unknown): Promise<void> {
  const result = await owner.observe(task)
  expect(result.rejected).toBe(true)
  if (!result.rejected) throw new Error('Required native rejection was fulfilled')
  expect(result.error).toBe(error)
}
async function denied(owner: Rig, task: Promise<void>, message: string): Promise<void> {
  const result = await owner.observe(task)
  expect(result.rejected).toBe(true)
  if (!result.rejected) throw new Error('Required native denial was fulfilled')
  expect(result.error).toBeInstanceOf(Error)
  expect(result.error).toHaveProperty('message', message)
}

async function changedSave(owner: Rig, binding: PageBinding, draft: DashboardLayout): Promise<void> {
  const ticket = binding.open()
  owner.store.setDraft(ticket.sessionId, draft.items)
  const task = binding.close(ticket, true)
  expect(await owner.observe(task)).toStrictEqual({ rejected: false })
  await owner.idle()
}

async function establishUnknown(owner: Rig) {
  const binding = owner.bind()
  expect(await owner.observe(binding.refreshDisplay())).toStrictEqual({ rejected: false })
  await owner.idle()
  expect(owner.store.getSnapshot(A).layout).toStrictEqual(INITIAL_A)
  expect(owner.store.getSnapshot(A).counters).toStrictEqual(ZERO_COUNTERS)
  // An actual changed native save succeeds before the failure stimulus.
  await changedSave(owner, binding, POSITIVE_A)
  expect(owner.store.getSnapshot(A).layout).toStrictEqual(POSITIVE_A)
  expect(owner.store.getSnapshot(A).overlay).toStrictEqual({ [A]: POSITIVE_A })
  expect(owner.store.getSnapshot(A).counters).toStrictEqual(ONE_SUCCESS)
  expect(owner.reads()).toBe(2)
  expect(owner.writes.map((write) => write.body)).toStrictEqual([POSITIVE_A])
  const dialog = binding.open()
  owner.store.setDraft(dialog.sessionId, FAILED_A.items)
  const close = binding.close(dialog, true)
  const core = owner.store.finalize(dialog.sessionId)
  expect(binding.close(dialog, true)).toBe(close)
  expect(owner.store.finalize(dialog.sessionId)).toBe(core)
  const closeOutcome = owner.observe(close)
  const coreOutcome = owner.observe(core)
  const results = await Promise.all([closeOutcome, coreOutcome])
  for (const result of results) {
    expect(result.rejected).toBe(true)
    if (!result.rejected) throw new Error('Required E0 was fulfilled')
    expect(result.error).toBe(E0)
  }
  await owner.idle()
  const capture = recovery(owner)
  expect(capture).toStrictEqual({
    attemptId: 'dashboard-attempt-2', actorId: ACTOR, authGeneration: GENERATION,
    workspaceId: A, epoch: 2, baseline: POSITIVE_A, submitted: FAILED_A,
    category: 'unknown', phase: 'needs-recheck', recheck: null,
  })
  expect(Object.isFrozen(capture)).toBe(true)
  expect(Object.isFrozen(capture.baseline)).toBe(true)
  expect(Object.isFrozen(capture.submitted.items)).toBe(true)
  expect(capture.submitted.items.every(Object.isFrozen)).toBe(true)
  expect(owner.store.getSnapshot(A).counters).toStrictEqual(ONE_SUCCESS)
  expect(owner.reads()).toBe(2) // Unknown never triggers an implicit GET.
  return { binding, dialog, close, core, capture, before: frozen(structuredClone(capture)) }
}
type Original = Awaited<ReturnType<typeof establishUnknown>>

// No lookup of a deleted/revoked session is used to recover its old completion.
async function originalUntouched(owner: Rig, old: Original): Promise<void> {
  expect(old.binding.close(old.dialog, true)).toBe(old.close)
  await rejectedExactly(owner, old.close, E0)
  await rejectedExactly(owner, old.core, E0)
  expect(old.capture).toStrictEqual(old.before)
  expect(old.capture.baseline).toStrictEqual(POSITIVE_A)
  expect(old.capture.submitted).toStrictEqual(FAILED_A)
}
async function reviewed(owner: Rig, binding: PageBinding) {
  const initial = binding.reviewRecovery(recovery(owner).attemptId)
  expect(initial.permissions).toStrictEqual({ recheck: true, discard: true, reapply: false, retry: false })
  expect(await owner.observe(binding.recheckReviewedRecovery(initial.ticket))).toStrictEqual({ rejected: false })
  await owner.idle()
  const qualified = binding.reviewRecovery(initial.ticket.attemptId)
  expect(qualified.permissions).toStrictEqual({ recheck: true, discard: true, reapply: false, retry: true })
  expect(qualified.recovery.recheck).toStrictEqual({
    readId: qualified.ticket.readId, provenance: 'saved', target: QUALIFIED_A, matchesSubmitted: false,
  })
  expect(qualified.ticket.readId).not.toBeNull()
  return { ...qualified, initiatingTicket: initial.ticket }
}

// Body failures survive cleanup; cleanup-only faults are raised outside finally.
// Every planned controlled input is settled, every issued task observed/drained,
// and terminal public revocation precedes release to prevent teardown auto-writes.
async function finite(body: (owners: Rig[]) => Promise<void>): Promise<void> {
  const owners: Rig[] = []
  const diagnostics: unknown[] = []
  let bodyFailed = false
  let bodyError: unknown
  try {
    await body(owners)
  } catch (error: unknown) {
    bodyFailed = true
    bodyError = error
  } finally {
    for (const owner of owners) {
      try { owner.store.revoke() } catch (error: unknown) { diagnostics.push(error) }
      for (const input of [...owner.readPorts, ...owner.writePorts]) {
        try { input.settle() } catch (error: unknown) { diagnostics.push(error) }
      }
      for (const binding of owner.bindings) {
        try { binding.release() } catch (error: unknown) { diagnostics.push(error) }
      }
    }
    for (const owner of owners) {
      try {
        await Promise.all(owner.observed)
        await owner.idle()
        expect([...owner.readPorts, ...owner.writePorts].every((input) => input.isSettled())).toBe(true)
        expect(owner.store.getSnapshot(A).readPending).toBe(false)
        expect(owner.store.getSnapshot(B).readPending).toBe(false)
      } catch (error: unknown) { diagnostics.push(error) }
    }
    try {
      expect(FIXTURES).toStrictEqual(FIXTURES_BEFORE)
      expect(DASHBOARD_WIDGETS).toStrictEqual(REGISTRY_BEFORE)
    } catch (error: unknown) { diagnostics.push(error) }
  }
  if (bodyFailed) {
    if (diagnostics.length) throw new AggregateError([bodyError, ...diagnostics], 'Primary qualifier failure followed by cleanup diagnostics', { cause: bodyError })
    throw bodyError
  }
  if (diagnostics.length) throw new AggregateError(diagnostics, 'Native qualifier cleanup failed')
}

it('native held Retry fails as distinct E1 with a new capture while original E0 completions and consumed tickets stay inert', async () => {
  await finite(async (owners) => {
    const held = port<CommittedDashboardLayout>(E1, true)
    const owner = rig([port(INITIAL_USER), port(POSITIVE_USER), port(REVIEWED_USER)],
      [port(POSITIVE_COMMIT), port<CommittedDashboardLayout>(E0), held])
    owners.push(owner)
    const old = await establishUnknown(owner)
    const qualified = await reviewed(owner, old.binding)
    const reviewedCapture = qualified.recovery
    const reviewedBefore = frozen(structuredClone(reviewedCapture))
    const task = old.binding.replaceRecovery(qualified.ticket, 'retry')
    const observed = owner.observe(task)
    const newSession = owner.store.retain(A)
    expect(newSession).toBe('dashboard-session-3')
    expect(newSession).not.toBe(old.dialog.sessionId)
    if (!newSession) throw new Error('Native held replacement session was not retained')
    expect(owner.store.finalize(newSession)).toBe(task)
    expect(owner.store.getSnapshot(A).openedBaseline).toStrictEqual(QUALIFIED_A)
    expect(owner.store.getSnapshot(A).draft).toStrictEqual(FAILED_A)
    expect(owner.store.getSnapshot(A).recovery).toBeNull()
    expect(owner.store.getSnapshot(A).pending).toBe(true)
    await turn()
    expect(owner.writes.map((write) => write.body)).toStrictEqual([POSITIVE_A, FAILED_A, FAILED_A])
    expect(owner.writes.map((write) => write.workspaceId)).toStrictEqual([A, A, A])
    expect(owner.writes.every((write) => write.currentAtIssuance)).toBe(true)
    expect(owner.writes[2].binding.isCurrent()).toBe(true)
    expect(owner.reads()).toBe(3)
    const pending = owner.store.getSnapshot(A)
    const pendingStatus = owner.store.getPersistenceStatus(A)
    // Both the consumed selected ticket and a pre-read ticket lack authority.
    await denied(owner, old.binding.replaceRecovery(qualified.ticket, 'retry'), 'Dashboard recovery replacement unavailable')
    await denied(owner, old.binding.recheckReviewedRecovery(qualified.ticket), 'Dashboard reviewed recovery read unavailable')
    await denied(owner, old.binding.replaceRecovery(qualified.initiatingTicket, 'retry'), 'Dashboard recovery replacement unavailable')
    old.binding.discardRecovery(qualified.initiatingTicket)
    old.binding.discardRecovery(qualified.ticket)
    expect(owner.store.getSnapshot(A)).toStrictEqual(pending)
    expect(owner.store.getPersistenceStatus(A)).toStrictEqual(pendingStatus)
    held.settle()
    const result = await observed
    expect(result.rejected).toBe(true)
    if (!result.rejected) throw new Error('Held E1 unexpectedly fulfilled')
    expect(result.error).toBe(E1)
    await rejectedExactly(owner, task, E1)
    await owner.idle()
    const next = recovery(owner)
    expect(next).not.toBe(old.capture)
    expect(next.attemptId).toBe('dashboard-attempt-3')
    expect(next.attemptId).not.toBe(old.capture.attemptId)
    expect(next.epoch).toBe(old.capture.epoch + 1)
    expect(next).toStrictEqual({
      attemptId: 'dashboard-attempt-3', actorId: ACTOR, authGeneration: GENERATION,
      workspaceId: A, epoch: 3, baseline: QUALIFIED_A, submitted: FAILED_A,
      category: 'unknown', phase: 'needs-recheck', recheck: null,
    })
    expect(owner.store.getSnapshot(A).counters).toStrictEqual({ ...ONE_SUCCESS, server5xx: 1 })
    expect(owner.store.getSnapshot(A).overlay).toStrictEqual({ [A]: POSITIVE_A })
    expect(owner.reads()).toBe(3)
    expect(owner.writes).toHaveLength(3)
    expect(reviewedCapture).toStrictEqual(reviewedBefore)
    await originalUntouched(owner, old)
    const failed = owner.store.getSnapshot(A)
    const failedStatus = owner.store.getPersistenceStatus(A)
    await denied(owner, old.binding.replaceRecovery(qualified.ticket, 'retry'), 'Dashboard recovery replacement unavailable')
    old.binding.discardRecovery(qualified.ticket)
    expect(owner.store.getSnapshot(A)).toStrictEqual(failed)
    expect(owner.store.getPersistenceStatus(A)).toStrictEqual(failedStatus)
    expect(owner.writes).toHaveLength(3)
    expect(owner.reads()).toBe(3)
  })
})

it('native reviewed GET retired across live A to B to A rejects stale and only a fresh binding can qualify a valid Retry', async () => {
  await finite(async (owners) => {
    const heldRead = port(REVIEWED_USER, true)
    const owner = rig([port(INITIAL_USER), port(POSITIVE_USER), heldRead, port(REVIEWED_USER), port(RETRY_USER)],
      [port(POSITIVE_COMMIT), port<CommittedDashboardLayout>(E0), port(RETRY_COMMIT)])
    owners.push(owner)
    const old = await establishUnknown(owner)
    const initial = old.binding.reviewRecovery(old.capture.attemptId)
    const task = old.binding.recheckReviewedRecovery(initial.ticket)
    const outcome = owner.observe(task)
    expect(old.binding.recheckReviewedRecovery(initial.ticket)).toBe(task)
    expect(owner.reads()).toBe(3)
    expect(owner.store.getSnapshot(A).readPending).toBe(true)
    owner.select(B)
    const bBinding = owner.bind(B)
    const bBefore = owner.store.getSnapshot(B)
    old.binding.release()
    owner.select(A)
    bBinding.release()
    const fresh = owner.bind(A)
    const retired = owner.store.getSnapshot(A)
    expect(retired.recovery?.phase).toBe('needs-recheck')
    expect(retired.recovery?.recheck).toBeNull()
    const awaiting = fresh.reviewRecovery(old.capture.attemptId)
    expect(awaiting.permissions).toStrictEqual({ recheck: false, discard: false, retry: false, reapply: false })
    await denied(owner, old.binding.replaceRecovery(initial.ticket, 'retry'), 'Dashboard reviewed page ticket is not current')
    await denied(owner, owner.store.replaceRecovery(initial.ticket, 'retry'), 'Dashboard recovery replacement unavailable')
    await denied(owner, old.binding.recheckReviewedRecovery(initial.ticket), 'Dashboard reviewed page ticket is not current')
    old.binding.discardRecovery(initial.ticket)
    expect(owner.store.getSnapshot(A)).toStrictEqual(retired)
    expect(owner.store.getSnapshot(B)).toStrictEqual(bBefore)
    expect(owner.writes).toHaveLength(2)
    expect(owner.reads()).toBe(3)
    heldRead.settle()
    const result = await outcome
    expect(result.rejected).toBe(true)
    if (!result.rejected) throw new Error('Retired reviewed read unexpectedly fulfilled')
    expect(result.error).toHaveProperty('message', 'Dashboard layout read is stale')
    await rejectedExactly(owner, task, result.error)
    await owner.idle()
    expect(owner.store.getSnapshot(A)).toStrictEqual({ ...retired, readPending: false })
    expect(owner.store.getSnapshot(B)).toStrictEqual(bBefore)
    expect(owner.reads()).toBe(3)
    const qualified = await reviewed(owner, fresh)
    expect(qualified.ticket.readId).not.toBe(initial.ticket.readId)
    expect(qualified.ticket.epoch).toBe(old.capture.epoch)
    const retry = fresh.replaceRecovery(qualified.ticket, 'retry')
    expect(await owner.observe(retry)).toStrictEqual({ rejected: false })
    await owner.idle()
    expect(owner.writes.map((write) => write.body)).toStrictEqual([POSITIVE_A, FAILED_A, FAILED_A])
    expect(owner.writes.every((write) => write.currentAtIssuance)).toBe(true)
    expect(owner.reads()).toBe(5) // Explicit fresh review + native successful-PUT GET.
    expect(owner.store.getSnapshot(A).layout).toStrictEqual(FAILED_A)
    expect(owner.store.getSnapshot(A).overlay).toStrictEqual({ [A]: FAILED_A })
    expect(owner.store.getSnapshot(A).recovery).toBeNull()
    expect(owner.store.getSnapshot(A).counters).toStrictEqual({ ...ONE_SUCCESS, success: 2 })
    expect(owner.store.getPersistenceStatus(A)).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: false, canCustomize: true, failureKind: null,
    })
    await originalUntouched(owner, old)
    const saved = owner.store.getSnapshot(A)
    await denied(owner, fresh.replaceRecovery(qualified.ticket, 'retry'), 'Dashboard recovery replacement unavailable')
    expect(owner.store.getSnapshot(A)).toStrictEqual(saved)
    expect(owner.writes).toHaveLength(3)
    expect(owner.reads()).toBe(5)
  })
})

it('native Retry retired before its dispatch microtask sends zero replacement transports and preserves a positive changed B save', async () => {
  await finite(async (owners) => {
    const owner = rig([port(INITIAL_USER), port(POSITIVE_USER), port(REVIEWED_USER), port(POSITIVE_USER), port(B_USER)],
      [port(POSITIVE_COMMIT), port<CommittedDashboardLayout>(E0), port(B_COMMIT)])
    owners.push(owner)
    const old = await establishUnknown(owner)
    const qualified = await reviewed(owner, old.binding)
    const bBefore = owner.store.getSnapshot(B)
    const retry = old.binding.replaceRecovery(qualified.ticket, 'retry')
    const retryOutcome = owner.observe(retry)
    const newSession = owner.store.retain(A)
    expect(newSession).toBe('dashboard-session-3')
    if (!newSession) throw new Error('Admitted replacement session was not retained')
    const core = owner.store.finalize(newSession)
    expect(core).toBe(retry)
    // No await/turn between admission and the genuine public selection retirement.
    owner.select(B)
    const bBinding = owner.bind(B)
    expect(owner.writes).toHaveLength(2)
    const result = await retryOutcome
    expect(result.rejected).toBe(true)
    if (!result.rejected) throw new Error('Retired dispatch unexpectedly fulfilled')
    expect(result.error).toHaveProperty('message', 'Dashboard layout write owner is not current')
    await rejectedExactly(owner, core, result.error)
    await owner.idle()
    expect(owner.writes.map((write) => write.body)).toStrictEqual([POSITIVE_A, FAILED_A])
    expect(owner.reads()).toBe(3)
    expect(owner.store.getSnapshot(A).counters).toStrictEqual(ONE_SUCCESS)
    expect(owner.store.getSnapshot(A).recovery).toBeNull()
    expect(owner.store.getSnapshot(B)).toStrictEqual(bBefore)
    await denied(owner, old.binding.replaceRecovery(qualified.ticket, 'retry'), 'Dashboard reviewed page ticket is not current')
    old.binding.discardRecovery(qualified.ticket)
    expect(owner.store.getSnapshot(B)).toStrictEqual(bBefore)
    // Explicit B qualification and a different real changed draft defeat blanket denial.
    expect(await owner.observe(bBinding.refreshDisplay())).toStrictEqual({ rejected: false })
    await owner.idle()
    expect(owner.store.getSnapshot(B).layout).toStrictEqual(INITIAL_B)
    await changedSave(owner, bBinding, CHANGED_B)
    expect(owner.writes.map((write) => write.workspaceId)).toStrictEqual([A, A, B])
    expect(owner.writes.map((write) => write.body)).toStrictEqual([POSITIVE_A, FAILED_A, CHANGED_B])
    expect(owner.writes.every((write) => write.currentAtIssuance)).toBe(true)
    expect(owner.reads()).toBe(5)
    expect(owner.store.getSnapshot(B).layout).toStrictEqual(CHANGED_B)
    expect(owner.store.getSnapshot(B).overlay).toStrictEqual({ [A]: POSITIVE_A, [B]: CHANGED_B })
    expect(owner.store.getSnapshot(B).counters).toStrictEqual({ ...ONE_SUCCESS, success: 2 })
    expect(owner.store.getPersistenceStatus(B)).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: false, canCustomize: true, failureKind: null,
    })
    await originalUntouched(owner, old)
    await rejectedExactly(owner, retry, result.error)
  })
})

it('an actually issued held native Retry cannot revive a publicly revoked owner or mutate a successfully saved fresh auth generation', async () => {
  await finite(async (owners) => {
    const held = port<CommittedDashboardLayout>(E1, true)
    const identity = { token: 'synthetic-native-replacement-owner-31' }
    const owner = rig([port(INITIAL_USER), port(POSITIVE_USER), port(REVIEWED_USER)],
      [port(POSITIVE_COMMIT), port<CommittedDashboardLayout>(E0), held], identity)
    owners.push(owner)
    const old = await establishUnknown(owner)
    const qualified = await reviewed(owner, old.binding)
    const retry = old.binding.replaceRecovery(qualified.ticket, 'retry')
    const outcome = owner.observe(retry)
    const newSession = owner.store.retain(A)
    expect(newSession).toBe('dashboard-session-3')
    if (!newSession) throw new Error('Issued replacement session was not retained')
    const core = owner.store.finalize(newSession)
    expect(core).toBe(retry)
    await turn()
    expect(owner.writes).toHaveLength(3)
    expect(owner.writes[2].body).toStrictEqual(FAILED_A)
    expect(owner.writes[2].currentAtIssuance).toBe(true)
    expect(owner.writes[2].binding.isCurrent()).toBe(true)
    expect(held.isSettled()).toBe(false)
    expect(owner.store.getSnapshot(A).pending).toBe(true)
    owner.store.revoke()
    identity.token = 'synthetic-native-replacement-owner-32'
    expect(owner.writes[2].binding.isCurrent()).toBe(false)
    await turn()
    const revoked = owner.store.getSnapshot(A)
    expect(revoked).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: A, provenance: 'default',
      writable: false, state: 'closed', pending: false, readPending: false, canUndo: false,
      layout: INITIAL_A, openedBaseline: null, draft: null, recovery: null, overlay: {}, counters: ONE_SUCCESS,
    })
    const revokedStatus = owner.store.getPersistenceStatus(A)
    expect(revokedStatus).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: false, canCustomize: false, failureKind: null,
    })
    const fresh = rig([port(INITIAL_USER), port(FRESH_USER)], [port(FRESH_COMMIT)], identity, GENERATION + 1)
    owners.push(fresh)
    const freshBinding = fresh.bind()
    expect(await fresh.observe(freshBinding.refreshDisplay())).toStrictEqual({ rejected: false })
    await fresh.idle() // Never await the revoked owner's held transport here.
    await changedSave(fresh, freshBinding, FRESH_A)
    const freshA = fresh.store.getSnapshot(A)
    const freshB = fresh.store.getSnapshot(B)
    const freshStatusA = fresh.store.getPersistenceStatus(A)
    const freshStatusB = fresh.store.getPersistenceStatus(B)
    expect(freshA).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION + 1, workspaceId: A, provenance: 'saved',
      writable: true, state: 'closed-success', pending: false, readPending: false, canUndo: false,
      layout: FRESH_A, openedBaseline: null, draft: null, recovery: null,
      overlay: { [A]: FRESH_A }, counters: ONE_SUCCESS,
    })
    expect(freshStatusA).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: false, canCustomize: true, failureKind: null,
    })
    expect(fresh.writes.map((write) => write.body)).toStrictEqual([FRESH_A])
    expect(fresh.writes[0].currentAtIssuance).toBe(true)
    expect(fresh.reads()).toBe(2)
    expect(held.isSettled()).toBe(false) // Fresh-owner positive completed before old reply.
    await denied(owner, old.binding.replaceRecovery(qualified.ticket, 'retry'), 'Dashboard recovery replacement unavailable')
    await denied(owner, owner.store.recheckReviewedRecovery(qualified.ticket), 'Dashboard reviewed recovery read unavailable')
    old.binding.discardRecovery(qualified.ticket)
    expect(owner.store.getSnapshot(A)).toStrictEqual(revoked)
    held.settle()
    const result = await outcome
    expect(result.rejected).toBe(true)
    if (!result.rejected) throw new Error('Revoked held E1 unexpectedly fulfilled')
    expect(result.error).toBe(E1)
    await rejectedExactly(owner, retry, E1)
    await rejectedExactly(owner, core, E1)
    await owner.idle()
    expect(owner.store.getSnapshot(A)).toStrictEqual({ ...revoked, counters: { ...ONE_SUCCESS, server5xx: 1 } })
    expect(owner.store.getPersistenceStatus(A)).toStrictEqual(revokedStatus)
    expect(owner.reads()).toBe(3)
    expect(owner.writes).toHaveLength(3)
    expect(owner.writes.map((write) => write.body)).toStrictEqual([POSITIVE_A, FAILED_A, FAILED_A])
    expect(fresh.store.getSnapshot(A)).toStrictEqual(freshA)
    expect(fresh.store.getSnapshot(B)).toStrictEqual(freshB)
    expect(fresh.store.getPersistenceStatus(A)).toStrictEqual(freshStatusA)
    expect(fresh.store.getPersistenceStatus(B)).toStrictEqual(freshStatusB)
    expect(fresh.reads()).toBe(2)
    expect(fresh.writes).toHaveLength(1)
    await originalUntouched(owner, old)
    expect(qualified.recovery.baseline).toStrictEqual(POSITIVE_A)
    expect(qualified.recovery.submitted).toStrictEqual(FAILED_A)
  })
})
