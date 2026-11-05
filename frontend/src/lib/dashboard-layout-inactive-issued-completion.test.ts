import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease, type DialogTicket, type PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, INERT_PERSISTENCE_STATUS, type Recovery, type SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// TEST_AUTHOR_ONLY / NOT_RUN. Real constructor + lease, only native I/O/identity seams.
const ACTOR = '33333333-3333-4333-8333-333333333333'
const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const GENERATION = 7
const TOKEN = 'synthetic-inactive-issued-completion-token'
type Outcome = { rejected: false } | { rejected: true; error: unknown }

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
    Object.values(value).forEach((child: unknown) => { expectFrozen(child) })
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse })
  // Preserve, inspect and drain port rejections even if a body assertion fails
  // before dispatch. This is evidence, not a catch-and-ignore rejection sink.
  const outcome = promise.then<Outcome, Outcome>(
    () => ({ rejected: false }), (error: unknown) => ({ rejected: true, error }),
  )
  return { promise, outcome, resolve, reject }
}
function turn(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

// Every transport leaf is authored literally, independently of GOLDEN and PUT
// arguments. Both workspaces have strict RAW leaves; reviewed GET is not merged
// into Auth or normalized into a fabricated commit by this fixture.
const TEST_DATA = freeze({
  initial: {
    id: ACTOR, email: 'inactive-issued-completion@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Inactive issued completion tracer', onboarding_completed: true,
      dashboard_layouts: {
        [A]: { version: 1, workspaceId: A, items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
        [B]: { version: 1, workspaceId: B, items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: false, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
      },
    },
  } satisfies User & { preferences: { timezone: string } },
  positiveCommit: {
    workspaceId: A,
    layout: { version: 1, workspaceId: A, items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ] },
  } satisfies CommittedDashboardLayout,
  positiveRead: {
    id: ACTOR, email: 'inactive-issued-completion@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Inactive issued completion tracer', onboarding_completed: true,
      dashboard_layouts: {
        [A]: { version: 1, workspaceId: A, items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: false, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
        [B]: { version: 1, workspaceId: B, items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: false, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
      },
    },
  } satisfies User & { preferences: { timezone: string } },
  reviewedRead: {
    id: ACTOR, email: 'inactive-issued-completion@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Inactive issued completion tracer', onboarding_completed: true,
      dashboard_layouts: {
        [A]: { version: 1, workspaceId: A, items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: false, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
        [B]: { version: 1, workspaceId: B, items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: false, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ] },
      },
    },
  } satisfies User & { preferences: { timezone: string } },
})

// Separate, recursively frozen literal expected layouts; never transport echoes.
const GOLDEN = freeze({
  initial: { version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] } satisfies DashboardLayout,
  positive: { version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] } satisfies DashboardLayout,
  submitted: { version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: false, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] } satisfies DashboardLayout,
  reviewed: { version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] } satisfies DashboardLayout,
  newUnsent: { version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: false, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: false, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] } satisfies DashboardLayout,
  sibling: { version: 1, workspaceId: B, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: false, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] } satisfies DashboardLayout,
  siblingUnsent: { version: 1, workspaceId: B, items: [
    { id: 'hero-balance', visible: false, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: false, order: 3 },
    { id: 'goals-progress', visible: true, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] } satisfies DashboardLayout,
})

it('retains an authentic issued A failure while B is selected, then explicitly reviews and discards without replay or cross-workspace editing', async () => {
  const dataBefore = freeze(structuredClone(TEST_DATA))
  const goldenBefore = freeze(structuredClone(GOLDEN))
  const registryBefore = freeze(structuredClone(DASHBOARD_WIDGETS))
  const authSeed: User = freeze(structuredClone(TEST_DATA.initial))
  const seedPreferences = authSeed.preferences
  const networkError = Object.freeze(new Error('Independent inactive A original network failure'))
  const retiredError = Object.freeze(new Error('Independent terminally revoked generation network failure'))
  const initial = deferred<User>()
  const positive = deferred<CommittedDashboardLayout>()
  const positiveRead = deferred<User>()
  const failed = deferred<CommittedDashboardLayout>()
  const reviewedRead = deferred<User>()
  const retiredWrite = deferred<CommittedDashboardLayout>()
  let selectedWorkspace = A
  let reads = 0
  const writes: Array<{ workspaceId: string; body: DashboardLayout; binding: DashboardRequestBinding; current: boolean }> = []
  const issued: Array<Promise<Outcome>> = []
  function observe(task: Promise<void>): Promise<Outcome> {
    const result = task.then<Outcome, Outcome>(
      () => ({ rejected: false }), (error: unknown) => ({ rejected: true, error }),
    )
    issued.push(result)
    return result
  }
  const store = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: GENERATION, authSeed,
    getTokenIdentity: () => TOKEN, getWorkspaceId: () => selectedWorkspace,
    api: {
      me: () => {
        reads += 1
        if (reads === 1) return initial.promise
        if (reads === 2) return positiveRead.promise
        if (reads === 3) return reviewedRead.promise
        return Promise.reject(new Error('Unexpected inactive-issued-completion GET'))
      },
      saveLayout: (workspaceId, body, binding) => {
        // Record outside assertions; never echo body into the strict response.
        writes.push({ workspaceId, body: structuredClone(body), binding, current: binding.isCurrent() })
        if (writes.length === 1) return positive.promise
        if (writes.length === 2) return failed.promise
        if (writes.length === 3) return retiredWrite.promise
        return Promise.reject(new Error('Unexpected inactive-issued-completion PUT'))
      },
    },
  })
  const lease = getPageSessionLease(store)
  const handles: Array<{ binding: PageBinding; released: boolean }> = []
  let openUnsent: { binding: PageBinding; ticket: DialogTicket } | null = null
  function bind(workspaceId: string): PageBinding {
    const binding = lease.bind(workspaceId)
    handles.push({ binding, released: false })
    return binding
  }
  function release(binding: PageBinding): void {
    binding.release()
    const handle = handles.find((candidate) => candidate.binding === binding)
    if (!handle) throw new Error('Untracked native page handle')
    handle.released = true
  }
  const publications: Array<{ a: SessionSnapshot; b: SessionSnapshot }> = []
  const unsubscribe = store.subscribe(() => {
    publications.push({ a: store.getSnapshot(A), b: store.getSnapshot(B) })
  }) // Read-only store subscription, never an extra page lease holder.
  async function drain(): Promise<void> {
    for (let step = 0; step < 8; step += 1) {
      const before = { reads, writes: writes.length, publications: publications.length }
      await lease.whenIdle()
      await store.whenIdle()
      await Promise.all(issued)
      await turn()
      await turn()
      if (reads === before.reads && writes.length === before.writes && publications.length === before.publications) return
    }
    throw new Error('Inactive-issued-completion native drain did not reach a fixed point')
  }
  const counters = freeze({ success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })
  function expectIO(expectedReads: number, expectedWrites = 2): void {
    expect(reads).toBe(expectedReads)
    expect(writes).toHaveLength(expectedWrites)
    expect(store.getSnapshot(A).counters).toStrictEqual(counters)
    expect(store.getSnapshot(B).counters).toStrictEqual(counters)
  }
  async function expectError(task: Promise<void>, error: Error): Promise<void> {
    const result = await observe(task)
    expect(result).toStrictEqual({ rejected: true, error })
    if (result.rejected) expect(result.error).toBe(error)
  }
  let primaryFailed = false
  let primaryError: unknown
  const cleanupErrors: unknown[] = []
  try {
    const initialTask = observe(store.refresh(A))
    initial.resolve(TEST_DATA.initial)
    expect(await initialTask).toStrictEqual({ rejected: false })
    await drain()
    expect(store.getSnapshot(A).layout).toStrictEqual(GOLDEN.initial)
    expect(store.getSnapshot(B).layout).toStrictEqual(GOLDEN.sibling)
    expect(store.getPersistenceStatus(A).canCustomize).toBe(true)
    lease.select(A)
    const originalBinding = bind(A)
    const positiveTicket = originalBinding.open()
    openUnsent = { binding: originalBinding, ticket: positiveTicket }
    store.setDraft(positiveTicket.sessionId, GOLDEN.positive.items)
    expect(GOLDEN.positive).not.toStrictEqual(GOLDEN.initial)
    const positiveClose = observe(originalBinding.close(positiveTicket, true))
    openUnsent = null
    await turn()
    expect(writes).toHaveLength(1)
    expect(writes[0]).toMatchObject({ workspaceId: A, body: GOLDEN.positive, current: true })
    expect(writes[0].binding.isCurrent()).toBe(true)
    positive.resolve(TEST_DATA.positiveCommit)
    await turn()
    await turn()
    expect(reads).toBe(2)
    positiveRead.resolve(TEST_DATA.positiveRead)
    expect(await positiveClose).toStrictEqual({ rejected: false })
    await drain()
    expect(store.getSnapshot(A)).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: A, provenance: 'saved',
      state: 'closed-success', writable: true, pending: false, readPending: false, canUndo: false,
      layout: GOLDEN.positive, openedBaseline: null, draft: null, recovery: null,
      overlay: { [A]: GOLDEN.positive }, counters,
    })
    expect(writes[0].binding.isCurrent()).toBe(false) // Consumed request, still eligible owner.
    expect(store.getPersistenceStatus(A).canCustomize).toBe(true)

    const oldTicket = originalBinding.open()
    openUnsent = { binding: originalBinding, ticket: oldTicket }
    store.setDraft(oldTicket.sessionId, GOLDEN.submitted.items)
    expect(GOLDEN.submitted).not.toStrictEqual(GOLDEN.positive)
    const oldClose = originalBinding.close(oldTicket, true)
    const oldCloseOutcome = observe(oldClose)
    openUnsent = null
    const oldCompletion = store.finalize(oldTicket.sessionId)
    const oldCompletionOutcome = observe(oldCompletion)
    expect(store.finalize(oldTicket.sessionId)).toBe(oldCompletion)
    expect(originalBinding.close(oldTicket, false)).toBe(oldClose)
    await turn()
    expectIO(2)
    expect(writes[1]).toMatchObject({ workspaceId: A, body: GOLDEN.submitted, current: true })
    expect(writes[1].binding.isCurrent()).toBe(true)
    const pendingA = store.getSnapshot(A)
    expect(pendingA.pending).toBe(true)
    expect(pendingA.openedBaseline).toStrictEqual(GOLDEN.positive)
    expect(pendingA.draft).toStrictEqual(GOLDEN.submitted)
    expect(pendingA.recovery).toBeNull()
    let oldSettled = false
    void oldCompletionOutcome.then(() => { oldSettled = true })

    // ACTUAL identity seam changes with the native page lifecycle. Pending A
    // survives native discard/select/release, but dispatch/review/edit do not.
    selectedWorkspace = B
    lease.select(B)
    release(originalBinding)
    const siblingBinding = bind(B)
    const siblingTicket = siblingBinding.open()
    openUnsent = { binding: siblingBinding, ticket: siblingTicket }
    store.setDraft(siblingTicket.sessionId, GOLDEN.siblingUnsent.items)
    await turn()
    await turn()
    expect(oldSettled).toBe(false)
    expect(writes[1].binding.isCurrent()).toBe(false)
    expect(store.getSnapshot(A)).toStrictEqual(pendingA)
    expect(store.getPersistenceStatus(A)).toBe(INERT_PERSISTENCE_STATUS)
    const siblingEditing = store.getSnapshot(B)
    expect(siblingEditing).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: B, provenance: 'saved',
      state: 'editing', writable: true, pending: false, readPending: false, canUndo: false,
      layout: GOLDEN.sibling, openedBaseline: GOLDEN.sibling, draft: GOLDEN.siblingUnsent,
      recovery: null, overlay: { [A]: GOLDEN.positive }, counters,
    })
    const siblingStatus = store.getPersistenceStatus(B)
    expect(siblingStatus).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: true, canCustomize: true, failureKind: null,
    })
    expect(() => originalBinding.open()).toThrow()
    expect(() => store.setDraft(oldTicket.sessionId, GOLDEN.positive.items)).toThrow('Dashboard layout session is not editable')
    expect(originalBinding.close(oldTicket, true)).toBe(oldClose)
    expectIO(2)
    failed.reject(networkError)
    const closeResult = await oldCloseOutcome
    const completionResult = await oldCompletionOutcome
    expect(closeResult).toStrictEqual({ rejected: true, error: networkError })
    expect(completionResult).toStrictEqual({ rejected: true, error: networkError })
    if (closeResult.rejected) expect(closeResult.error).toBe(networkError)
    if (completionResult.rejected) expect(completionResult.error).toBe(networkError)
    await drain()
    expectIO(2) // No inactive automatic GET, PUT replay, or global counter fabrication.
    expect(store.getSnapshot(B)).toStrictEqual(siblingEditing)
    expect(store.getPersistenceStatus(B)).toBe(siblingStatus)
    expect(store.getSnapshot(B).recovery).toBeNull()
    expect(store.getPersistenceStatus(A)).toBe(INERT_PERSISTENCE_STATUS)

    const expectedCapture: Recovery = freeze({
      attemptId: 'dashboard-attempt-2', actorId: ACTOR, authGeneration: GENERATION,
      workspaceId: A, epoch: 2, baseline: GOLDEN.positive, submitted: GOLDEN.submitted,
      category: 'unknown', phase: 'needs-recheck', recheck: null,
    })
    const inactive = store.getSnapshot(A)
    // FIRST SOURCE-PREDICTED FUNCTIONAL ORACLE: native issued failure must be
    // retained despite selection B. No missing-method or private-forcing oracle.
    expect(inactive.recovery, 'issued A network failure must retain its authentic capture while B is selected').toStrictEqual(expectedCapture)
    const capture = inactive.recovery
    if (!capture) throw new Error('Missing authentic inactive issued A recovery')
    const captureBefore = freeze(structuredClone(capture))
    expectFrozen(capture)
    expect(inactive).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: A, provenance: 'saved',
      state: 'unknown', writable: false, pending: false, readPending: false, canUndo: false,
      layout: GOLDEN.positive, openedBaseline: GOLDEN.positive, draft: GOLDEN.submitted,
      recovery: expectedCapture, overlay: { [A]: GOLDEN.positive }, counters,
    })
    expect(() => siblingBinding.reviewRecovery(capture.attemptId)).toThrow()
    expect(() => originalBinding.reviewRecovery(capture.attemptId)).toThrow()
    expect(store.retain(A)).toBeNull()
    await expectError(oldClose, networkError)
    await expectError(oldCompletion, networkError)
    expect(await observe(siblingBinding.close(siblingTicket, false))).toStrictEqual({ rejected: false })
    openUnsent = null
    await drain()
    selectedWorkspace = A
    lease.select(A)
    release(siblingBinding)
    const returnedBinding = bind(A)
    await drain()
    expectIO(2)
    expect(store.getSnapshot(A)).toStrictEqual(inactive)
    expect(store.getSnapshot(A).recovery).toBe(capture)
    const failureStatus = store.getPersistenceStatus(A)
    expect(failureStatus).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: true, canCustomize: false, failureKind: 'unknown',
    })
    expect(() => returnedBinding.open()).toThrow()
    expect(() => store.setDraft(oldTicket.sessionId, GOLDEN.positive.items)).toThrow('Dashboard layout session is not editable')
    // B's legitimate close changed the shared revision; readonly Review must
    // preserve the current A snapshot, not an object from before that close.
    const beforeReview = store.getSnapshot(A)
    const readonlyStart = publications.length
    const review = returnedBinding.reviewRecovery(capture.attemptId)
    expect(review.recovery).toBe(capture)
    expect(review.permissions).toStrictEqual({ recheck: true, reapply: false, retry: false, discard: true })
    expectFrozen(review)
    await drain()
    expect(publications).toHaveLength(readonlyStart)
    expect(store.getSnapshot(A)).toBe(beforeReview)
    expectIO(2)

    const siblingClosed = store.getSnapshot(B)
    const recheck = returnedBinding.recheckReviewedRecovery(review.ticket)
    const recheckOutcome = observe(recheck)
    let recheckSettled = false
    void recheckOutcome.then(() => { recheckSettled = true })
    await turn()
    await turn()
    expectIO(3)
    expect(recheckSettled).toBe(false)
    const held = store.getSnapshot(A)
    expect(held).toStrictEqual({ ...inactive, readPending: true, recovery: { ...expectedCapture, phase: 'rechecking' } })
    expect(held.recovery?.baseline).toBe(capture.baseline)
    expect(held.recovery?.submitted).toBe(capture.submitted)
    expect(returnedBinding.recheckReviewedRecovery(review.ticket)).toBe(recheck)
    const occupiedReview = returnedBinding.reviewRecovery(capture.attemptId)
    expect(occupiedReview.permissions).toStrictEqual({ recheck: false, reapply: false, retry: false, discard: false })
    expect(await observe(returnedBinding.recheckReviewedRecovery(occupiedReview.ticket))).toMatchObject({ rejected: true })
    expect(returnedBinding.discardRecovery(occupiedReview.ticket)).toBeUndefined()
    expect(store.getSnapshot(A)).toBe(held)
    expectIO(3)
    reviewedRead.resolve(TEST_DATA.reviewedRead)
    expect(await recheckOutcome).toStrictEqual({ rejected: false })
    await drain()
    const ready = store.getSnapshot(A)
    // Source-fit read serial: initial retire/read=2, select A=3, positive
    // retire/read=5, select B=6, release A=7, select A=8, release B=9, recheck=10.
    const readyCapture: Recovery = freeze({
      ...expectedCapture, phase: 'review-ready',
      recheck: { readId: 10, provenance: 'saved', target: GOLDEN.reviewed, matchesSubmitted: false },
    })
    expect(ready).toStrictEqual({ ...inactive, recovery: readyCapture })
    expect(ready.recovery?.baseline).toBe(capture.baseline)
    expect(ready.recovery?.submitted).toBe(capture.submitted)
    expect(GOLDEN.reviewed).not.toStrictEqual(GOLDEN.positive)
    expect(GOLDEN.reviewed).not.toStrictEqual(GOLDEN.submitted)
    expect(store.getPersistenceStatus(A)).toStrictEqual(failureStatus)
    expect(store.getSnapshot(B)).toStrictEqual(siblingClosed)
    expectIO(3)
    const readyReview = returnedBinding.reviewRecovery(capture.attemptId)
    expect(readyReview.ticket.readId).toBe(10)
    expect(readyReview.permissions).toStrictEqual({ recheck: true, reapply: false, retry: true, discard: true })
    expectFrozen(readyReview)
    expect(await observe(returnedBinding.recheckReviewedRecovery(review.ticket))).toMatchObject({ rejected: true })
    expect(returnedBinding.discardRecovery(review.ticket)).toBeUndefined()
    expect(store.getSnapshot(A)).toBe(ready)
    await expectError(oldCompletion, networkError)

    // Explicit discard is zero-write and promotes only this qualified GET target.
    expect(returnedBinding.discardRecovery(readyReview.ticket)).toBeUndefined()
    await drain()
    const discarded = store.getSnapshot(A)
    expect(discarded).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: A, provenance: 'saved',
      state: 'closed', writable: true, pending: false, readPending: false, canUndo: false,
      layout: GOLDEN.reviewed, openedBaseline: null, draft: null, recovery: null, overlay: {}, counters,
    })
    expect(discarded.overlay).toStrictEqual({})
    expect(store.getPersistenceStatus(A)).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: false, canCustomize: true, failureKind: null,
    })
    expectIO(3)
    expect(returnedBinding.discardRecovery(readyReview.ticket)).toBeUndefined()
    expect(await observe(returnedBinding.replaceRecovery(readyReview.ticket, 'retry'))).toMatchObject({ rejected: true })
    expect(await observe(returnedBinding.recheckReviewedRecovery(readyReview.ticket))).toMatchObject({ rejected: true })
    expect(originalBinding.close(oldTicket, false)).toBe(oldClose)
    await expectError(oldClose, networkError)
    await expectError(oldCompletion, networkError)
    expect(() => store.setDraft(oldTicket.sessionId, GOLDEN.positive.items)).toThrow('Dashboard layout session is not editable')
    await drain()
    expect(store.getSnapshot(A)).toBe(discarded)
    expectIO(3)

    const freshTicket = returnedBinding.open()
    openUnsent = { binding: returnedBinding, ticket: freshTicket }
    expect(freshTicket.sessionId).not.toBe(oldTicket.sessionId)
    expect(store.getSnapshot(A).openedBaseline).toStrictEqual(GOLDEN.reviewed)
    store.setDraft(freshTicket.sessionId, GOLDEN.newUnsent.items)
    await drain()
    const editing = store.getSnapshot(A)
    expect(editing.draft).toStrictEqual(GOLDEN.newUnsent)
    expect(editing.draft).not.toStrictEqual(editing.openedBaseline)
    expect(store.getPersistenceStatus(A).unsaved).toBe(true)
    expect(editing.recovery).toBeNull()
    expectIO(3) // Fresh changed editor is genuinely UNSENT.
    expect(returnedBinding.discardRecovery(readyReview.ticket)).toBeUndefined()
    expect(await observe(returnedBinding.replaceRecovery(readyReview.ticket, 'retry'))).toMatchObject({ rejected: true })
    await expectError(oldCompletion, networkError)
    await drain()
    expect(store.getSnapshot(A)).toBe(editing)
    expectIO(3)
    expect(await observe(returnedBinding.close(freshTicket, false))).toStrictEqual({ rejected: false })
    openUnsent = null
    await drain()
    expect(store.getSnapshot(A)).toStrictEqual(discarded)
    expect(capture).toStrictEqual(captureBefore)
    expectFrozen(capture)

    // Narrow genuine generation retirement control: SAME actor/token/selection,
    // native revoke (not a fake authGeneration mutation) while a new PUT is held.
    // Retiring issued work must not reacquire retained failure authority.
    const retiringTicket = returnedBinding.open()
    openUnsent = { binding: returnedBinding, ticket: retiringTicket }
    store.setDraft(retiringTicket.sessionId, GOLDEN.newUnsent.items)
    const retiringClose = returnedBinding.close(retiringTicket, true)
    const retiringCloseOutcome = observe(retiringClose)
    openUnsent = null
    const retiringCompletion = store.finalize(retiringTicket.sessionId)
    const retiringCompletionOutcome = observe(retiringCompletion)
    await turn()
    expectIO(3, 3)
    expect(writes[2]).toMatchObject({ workspaceId: A, body: GOLDEN.newUnsent, current: true })
    store.revoke()
    await turn()
    await turn()
    expect(writes[2].binding.isCurrent()).toBe(false)
    const revokedA = store.getSnapshot(A)
    const revokedB = store.getSnapshot(B)
    expect(revokedA.authGeneration).toBe(GENERATION)
    expect(revokedA.recovery).toBeNull()
    expect(revokedA.draft).toBeNull()
    expect(revokedA.overlay).toStrictEqual({})
    expect(store.getPersistenceStatus(A)).toBe(INERT_PERSISTENCE_STATUS)
    expect(() => returnedBinding.open()).toThrow()
    retiredWrite.reject(retiredError)
    expect(await retiringCloseOutcome).toStrictEqual({ rejected: true, error: retiredError })
    expect(await retiringCompletionOutcome).toStrictEqual({ rejected: true, error: retiredError })
    await expectError(retiringCompletion, retiredError)
    await drain()
    expect(store.getSnapshot(A)).toStrictEqual(revokedA)
    expect(store.getSnapshot(B)).toStrictEqual(revokedB)
    expect(store.retain(A)).toBeNull()
    expect(store.getSnapshot(A).recovery).toBeNull()
    expectIO(3, 3)
    await expectError(oldCompletion, networkError)
  } catch (error: unknown) {
    primaryFailed = true
    primaryError = error
  } finally {
    // Resolve/reject EVERY finite input, including not-yet-issued ports, then
    // drain lease, core, observed public tasks AND port outcomes. No finally throw.
    initial.resolve(TEST_DATA.initial)
    positive.resolve(TEST_DATA.positiveCommit)
    positiveRead.resolve(TEST_DATA.positiveRead)
    failed.reject(networkError)
    reviewedRead.resolve(TEST_DATA.reviewedRead)
    retiredWrite.reject(retiredError)
    if (openUnsent) {
      try {
        const result = await observe(openUnsent.binding.close(openUnsent.ticket, false))
        if (result.rejected) cleanupErrors.push(result.error)
      } catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drain() } catch (error: unknown) { cleanupErrors.push(error) }
    const beforeRelease = { reads, writes: writes.length }
    for (const handle of handles) {
      if (handle.released) continue
      try { release(handle.binding) } catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drain() } catch (error: unknown) { cleanupErrors.push(error) }
    try {
      const ports = await Promise.all([
        initial.outcome, positive.outcome, positiveRead.outcome,
        failed.outcome, reviewedRead.outcome, retiredWrite.outcome,
      ])
      expect(ports).toStrictEqual([
        { rejected: false }, { rejected: false }, { rejected: false },
        { rejected: true, error: networkError }, { rejected: false }, { rejected: true, error: retiredError },
      ])
      if (ports[3].rejected) expect(ports[3].error).toBe(networkError)
      if (ports[5].rejected) expect(ports[5].error).toBe(retiredError)
      expect(reads).toBe(beforeRelease.reads)
      expect(writes).toHaveLength(beforeRelease.writes)
      expect(handles.every((handle) => handle.released)).toBe(true)
      expect(store.getSnapshot(A).pending).toBe(false)
      expect(store.getSnapshot(A).readPending).toBe(false)
      expect(store.getSnapshot(B).pending).toBe(false)
      expect(store.getSnapshot(B).readPending).toBe(false)
      expect(TEST_DATA).toStrictEqual(dataBefore)
      expect(GOLDEN).toStrictEqual(goldenBefore)
      expect(authSeed.preferences).toBe(seedPreferences)
      expect(DASHBOARD_WIDGETS).toStrictEqual(registryBefore)
      expectFrozen(TEST_DATA)
      expectFrozen(GOLDEN)
    } catch (error: unknown) { cleanupErrors.push(error) }
    unsubscribe()
  }
  // Diagnostics are outside finally, with the primary body failure first.
  if (primaryFailed) {
    if (cleanupErrors.length > 0) throw new AggregateError([primaryError, ...cleanupErrors], 'Primary inactive-issued-completion failure with separate cleanup diagnostics')
    throw primaryError
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'Inactive-issued-completion cleanup failed')
})
