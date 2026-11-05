import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease, type PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, type LivePageLeaseWitness, type Recovery, type RecoveryActionTicket, type RecoveryReview, type SessionSnapshot, type SessionStore } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// TEST_AUTHOR_ONLY / NOT_RUN: real store and lease; only constructor transport seams.
// No AuthProvider, SDK, hook, UI, provider/App, or browser acceptance claim.
type ReviewedBinding = PageBinding & {
  recheckReviewedRecovery?: (ticket: RecoveryActionTicket) => Promise<void>
}
type ReviewedStore = SessionStore & {
  recheckReviewedRecovery?: (ticket: RecoveryActionTicket) => Promise<void>
}
type Outcome = { rejected: false } | { rejected: true; error: unknown }

function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}

function expectFrozenPublicData(actual: unknown, expected: unknown): void {
  expect(actual).toStrictEqual(expected)
  if (actual !== null && typeof actual === 'object' && expected !== null && typeof expected === 'object') {
    expect(Object.isFrozen(actual)).toBe(true)
    expect(Object.getPrototypeOf(actual)).toBe(Array.isArray(expected) ? Array.prototype : Object.prototype)
    expect(new Set(Reflect.ownKeys(actual))).toStrictEqual(new Set(Reflect.ownKeys(expected)))
    const fields = actual as Record<string, unknown>
    const expectedFields = expected as Record<string, unknown>
    for (const key of Object.keys(expectedFields)) {
      expect(Object.getOwnPropertyDescriptor(actual, key)?.enumerable).toBe(true)
      expectFrozenPublicData(fields[key], expectedFields[key])
    }
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse })
  return { promise, resolve, reject }
}

function notificationTurn(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('native reviewed Recheck holds one authority-bound read and qualifies an independent target without consuming the retained unknown failure', async () => {
  const actor = '33333333-3333-4333-8333-333333333333'
  const workspace = '11111111-1111-4111-8111-111111111111'
  const generation = 7
  // Independent canonical RAW user/map/leaf; no normalizer-generated golden data.
  const initialUser: User = freezeFixture({
    id: actor, email: 'native-reviewed-recheck@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Reviewed Recheck tracer', onboarding_completed: true,
      dashboard_layouts: {
        [workspace]: {
          version: 1, workspaceId: workspace,
          items: [
            { id: 'hero-balance', visible: true, order: 0 },
            { id: 'uncategorized-banner', visible: true, order: 1 },
            { id: 'spending-charts', visible: true, order: 2 },
            { id: 'balance-evolution', visible: true, order: 3 },
            { id: 'goals-progress', visible: true, order: 4 },
            { id: 'period-transactions', visible: true, order: 5 },
          ],
        },
      },
    },
  })
  const authSeed: User = freezeFixture(structuredClone(initialUser))
  const positiveDraft: DashboardLayout = freezeFixture({
    version: 1, workspaceId: workspace,
    items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  })
  // Independent strict PUT reply and post-save GET, never request echoes.
  const committed: CommittedDashboardLayout = freezeFixture({
    workspaceId: workspace,
    layout: {
      version: 1, workspaceId: workspace,
      items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  })
  const postUser: User = freezeFixture({
    id: actor, email: 'native-reviewed-recheck@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Reviewed Recheck tracer', onboarding_completed: true,
      dashboard_layouts: {
        [workspace]: {
          version: 1, workspaceId: workspace,
          items: [
            { id: 'hero-balance', visible: true, order: 0 },
            { id: 'uncategorized-banner', visible: true, order: 1 },
            { id: 'spending-charts', visible: true, order: 2 },
            { id: 'balance-evolution', visible: true, order: 3 },
            { id: 'goals-progress', visible: false, order: 4 },
            { id: 'period-transactions', visible: true, order: 5 },
          ],
        },
      },
    },
  })
  const failedDraft: DashboardLayout = freezeFixture({
    version: 1, workspaceId: workspace,
    items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: false, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  })
  // Independent reviewed RAW GET: differs from both submitted and baseline.
  const reviewedUser: User = freezeFixture({
    id: actor, email: 'native-reviewed-recheck@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Reviewed Recheck tracer', onboarding_completed: true,
      dashboard_layouts: {
        [workspace]: {
          version: 1, workspaceId: workspace,
          items: [
            { id: 'hero-balance', visible: true, order: 0 },
            { id: 'uncategorized-banner', visible: true, order: 1 },
            { id: 'spending-charts', visible: false, order: 2 },
            { id: 'balance-evolution', visible: true, order: 3 },
            { id: 'goals-progress', visible: true, order: 4 },
            { id: 'period-transactions', visible: true, order: 5 },
          ],
        },
      },
    },
  })
  const fixtures = freezeFixture({ initialUser, authSeed, positiveDraft, committed, postUser, failedDraft, reviewedUser })
  const fixturesBefore = freezeFixture(structuredClone(fixtures))
  const seedPreferences = authSeed.preferences
  const registryBefore = freezeFixture(structuredClone(DASHBOARD_WIDGETS))
  const networkFailure = Object.freeze(new Error('Independent native reviewed Recheck save failure'))
  const initialReply = deferred<User>()
  const successReply = deferred<CommittedDashboardLayout>()
  const postReply = deferred<User>()
  const failureReply = deferred<CommittedDashboardLayout>()
  const reviewedReply = deferred<User>()
  // Also handle this finite failure port if an earlier assertion prevents issuance.
  void failureReply.promise.catch(() => {})
  const originalToken = 'synthetic-native-reviewed-recheck-boundary-token'
  let selectedWorkspace: string = workspace
  let tokenIdentity: string | null = originalToken
  let reads = 0
  const captured: Array<{
    workspaceId: string; body: DashboardLayout; binding: DashboardRequestBinding; currentAtIssuance: boolean
  }> = []
  const store: ReviewedStore = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: actor, authGeneration: generation, authSeed,
    getTokenIdentity: () => tokenIdentity,
    getWorkspaceId: () => selectedWorkspace,
    api: {
      me: () => {
        reads += 1
        if (reads === 1) return initialReply.promise
        if (reads === 2) return postReply.promise
        if (reads === 3) return reviewedReply.promise
        return Promise.reject(new Error('Unexpected native reviewed Recheck GET'))
      },
      saveLayout: (workspaceId, body, binding) => {
        // Assertions are outside the API's exception-isolated classification path.
        captured.push({ workspaceId, body: structuredClone(body), binding, currentAtIssuance: binding.isCurrent() })
        if (captured.length === 1) return successReply.promise
        if (captured.length === 2) return failureReply.promise
        return Promise.reject(new Error('Unexpected native reviewed Recheck PUT'))
      },
    },
  })
  const lease = getPageSessionLease(store)
  const handles: Array<{ binding: ReviewedBinding; released: boolean }> = []
  function bindTracked(): ReviewedBinding {
    const binding: ReviewedBinding = lease.bind(workspace)
    handles.push({ binding, released: false })
    return binding
  }
  function releaseTracked(binding: ReviewedBinding): void {
    const handle = handles.find((candidate) => candidate.binding === binding)
    if (!handle || handle.released) return
    handle.released = true
    binding.release()
  }
  const issued: Array<Promise<Outcome>> = []
  function observe(task: Promise<void>): Promise<Outcome> {
    const outcome = task.then<Outcome, Outcome>(
      () => ({ rejected: false }),
      (error: unknown) => ({ rejected: true, error }),
    )
    issued.push(outcome)
    return outcome
  }
  const observed: SessionSnapshot[] = []
  const unsubscribe = store.subscribe(() => { observed.push(store.getSnapshot(workspace)) })
  async function drainFixedPoint(): Promise<void> {
    for (let turn = 0; turn < 8; turn += 1) {
      const before = { publications: observed.length, reads, writes: captured.length }
      await lease.whenIdle()
      await store.whenIdle()
      await Promise.all(issued)
      await notificationTurn()
      await notificationTurn()
      if (before.publications === observed.length && before.reads === reads && before.writes === captured.length) return
    }
    throw new Error('Native reviewed Recheck task/publication fixed point did not settle')
  }
  let retainedCapture: Recovery | null = null
  let premisesReady = false
  let reviewedReadIssued = false
  let primaryFailed = false
  let primaryError: unknown
  const cleanupErrors: unknown[] = []
  try {
    const initialOutcome = observe(store.refresh(workspace))
    initialReply.resolve(initialUser)
    expect(await initialOutcome).toStrictEqual({ rejected: false })
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toMatchObject({
      writable: true, provenance: 'saved', state: 'closed', pending: false, readPending: false,
      layout: initialUser.preferences!.dashboard_layouts![workspace], recovery: null,
      counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    lease.select(workspace)
    const binding = bindTracked()
    const positiveTicket = binding.open()
    store.setDraft(positiveTicket.sessionId, positiveDraft.items)
    const positiveClose = binding.close(positiveTicket, true)
    const positiveOutcome = observe(positiveClose)
    await notificationTurn()
    expect(captured).toHaveLength(1)
    expect(captured[0].workspaceId).toBe(workspace)
    expect(captured[0].body).toStrictEqual(positiveDraft)
    expect(captured[0].currentAtIssuance).toBe(true)
    expect(captured[0].binding.isCurrent()).toBe(true)
    expect(store.getSnapshot(workspace).pending).toBe(true)
    successReply.resolve(committed)
    await notificationTurn()
    await notificationTurn()
    expect(reads).toBe(2)
    postReply.resolve(postUser)
    expect(await positiveOutcome).toStrictEqual({ rejected: false })
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toMatchObject({
      writable: true, provenance: 'saved', state: 'closed-success', pending: false, readPending: false,
      layout: committed.layout, openedBaseline: null, draft: null, recovery: null,
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(store.getSnapshot(workspace).overlay).toStrictEqual({ [workspace]: committed.layout })
    expect(captured[0].binding.isCurrent()).toBe(false) // Consumed request, not revoked owner.
    expect(store.getPersistenceStatus(workspace).canCustomize).toBe(true)

    const failedTicket = binding.open()
    expect(failedTicket).not.toBe(positiveTicket)
    store.setDraft(failedTicket.sessionId, failedDraft.items)
    const failedClose = binding.close(failedTicket, true)
    const failedOutcome = observe(failedClose)
    await notificationTurn()
    expect(captured).toHaveLength(2)
    expect(captured[1].workspaceId).toBe(workspace)
    expect(captured[1].body).toStrictEqual(failedDraft)
    expect(captured[1].currentAtIssuance).toBe(true)
    expect(captured[1].binding.isCurrent()).toBe(true)
    expect(binding.close(failedTicket, true)).toBe(failedClose)
    const storeCompletion = store.finalize(failedTicket.sessionId)
    const storeOutcome = observe(storeCompletion)
    expect(store.finalize(failedTicket.sessionId)).toBe(storeCompletion)
    failureReply.reject(networkFailure) // No HTTP409 and therefore no automatic GET.
    const failedResult = await failedOutcome
    const storeResult = await storeOutcome
    expect(failedResult.rejected).toBe(true)
    expect(storeResult.rejected).toBe(true)
    if (failedResult.rejected) expect(failedResult.error).toBe(networkFailure)
    if (storeResult.rejected) expect(storeResult.error).toBe(networkFailure)
    await drainFixedPoint()
    const expectedRecovery: Recovery = freezeFixture({
      attemptId: 'dashboard-attempt-2', actorId: actor, authGeneration: generation,
      workspaceId: workspace, epoch: 2, baseline: committed.layout, submitted: failedDraft,
      category: 'unknown', phase: 'needs-recheck', recheck: null,
    })
    const failedSnapshot = store.getSnapshot(workspace)
    expectFrozenPublicData(failedSnapshot, freezeFixture({
      actorId: actor, authGeneration: generation, workspaceId: workspace,
      provenance: 'saved', writable: false, state: 'unknown', pending: false, readPending: false,
      canUndo: false, layout: committed.layout, openedBaseline: committed.layout,
      draft: failedDraft, recovery: expectedRecovery, overlay: { [workspace]: committed.layout },
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    }))
    const capture = failedSnapshot.recovery
    expect(capture).not.toBeNull()
    if (!capture) throw new Error('Missing actual retained native failure')
    retainedCapture = capture
    expect(reads).toBe(2)
    expect(captured).toHaveLength(2)
    expect(store.retain(workspace)).toBeNull()
    expect(() => binding.open()).toThrow()
    expect(() => store.setDraft(failedTicket.sessionId, positiveDraft.items)).toThrow('Dashboard layout session is not editable')
    expect(binding.close(failedTicket, false)).toBe(failedClose)
    expect(store.finalize(failedTicket.sessionId)).toBe(storeCompletion)
    const statusBeforeReview = store.getPersistenceStatus(workspace)
    expect(statusBeforeReview).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: true, canCustomize: false, failureKind: 'unknown',
    })
    const publicationsBeforeReview = observed.length
    const review = binding.reviewRecovery(capture.attemptId)
    const expectedReview: RecoveryReview = freezeFixture({
      ticket: {
        attemptId: 'dashboard-attempt-2', actorId: actor, authGeneration: generation,
        workspaceId: workspace, epoch: 2, readId: null,
      },
      recovery: expectedRecovery,
      permissions: { recheck: true, reapply: false, retry: false, discard: true },
    })
    expectFrozenPublicData(review, expectedReview)
    expect(review.recovery).toBe(capture)
    const remaining = bindTracked()
    const remainingReview = remaining.reviewRecovery(capture.attemptId)
    expectFrozenPublicData(remainingReview, expectedReview)
    expect(remainingReview.ticket).not.toBe(review.ticket)
    // Structural continuity content resembles the first native entry; its opaque
    // identity is NOT inspected/copied. A true-returning function is no capability.
    const forgedWitness: LivePageLeaseWitness = Object.freeze({
      store, workspaceId: workspace, continuity: Object.freeze({ generation: 1 }), isCurrent: () => true,
    })
    expect(() => store.reviewRecovery(workspace, capture.attemptId, forgedWitness)).toThrow()
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toBe(failedSnapshot)
    expect(store.getSnapshot(workspace).recovery).toBe(capture)
    expect(store.getPersistenceStatus(workspace)).toBe(statusBeforeReview)
    expect(observed).toHaveLength(publicationsBeforeReview)
    expect(reads).toBe(2)
    expect(captured).toHaveLength(2)
    premisesReady = true

    // Prospective RED must be this explicit missing-method assertion, not a
    // collection/import error or TypeError before the genuine native premises.
    const recheck = binding.recheckReviewedRecovery
    expect(typeof recheck, 'native PageBinding.recheckReviewedRecovery must execute registered reviewed tickets').toBe('function')
    if (typeof recheck !== 'function') throw new Error('Missing native binding reviewed Recheck')
    const recheckStore = store.recheckReviewedRecovery
    expect(typeof recheckStore, 'native SessionStore.recheckReviewedRecovery must own reviewed ticket authority').toBe('function')
    if (typeof recheckStore !== 'function') throw new Error('Missing native store reviewed Recheck')
    const recheckRemaining = remaining.recheckReviewedRecovery
    expect(typeof recheckRemaining).toBe('function')
    if (typeof recheckRemaining !== 'function') throw new Error('Missing remaining holder reviewed Recheck')

    const heldRead = recheck.call(binding, review.ticket)
    const heldOutcome = observe(heldRead) // Observe before any controlled settlement.
    reviewedReadIssued = true
    let heldSettled = false
    void heldOutcome.then(() => { heldSettled = true })
    await notificationTurn()
    await notificationTurn()
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)
    expect(heldSettled).toBe(false)
    const heldSnapshot = store.getSnapshot(workspace)
    expectFrozenPublicData(heldSnapshot, freezeFixture({
      ...failedSnapshot, readPending: true,
      recovery: { ...expectedRecovery, phase: 'rechecking', recheck: null },
    }))
    expect(heldSnapshot.recovery?.baseline).toBe(capture.baseline)
    expect(heldSnapshot.recovery?.submitted).toBe(capture.submitted)
    const heldStatus = store.getPersistenceStatus(workspace)
    expect(heldStatus).toStrictEqual({ ...statusBeforeReview, readPending: true })
    const publicationsWhileHeld = observed.length
    // The initiating-ticket exception permits only its exact nonretired task.
    expect(recheck.call(binding, review.ticket)).toBe(heldRead)
    const whileHeldReview = binding.reviewRecovery(capture.attemptId)
    expectFrozenPublicData(whileHeldReview, freezeFixture({
      ticket: expectedReview.ticket, recovery: { ...expectedRecovery, phase: 'rechecking', recheck: null },
      permissions: { recheck: false, reapply: false, retry: false, discard: false },
    }))
    expect(whileHeldReview.recovery).toBe(heldSnapshot.recovery)
    expect(await observe(recheck.call(binding, whileHeldReview.ticket))).toMatchObject({ rejected: true })
    // Same entry, different genuine issuing handle: no cross-handle dedup grant.
    expect(await observe(recheckRemaining.call(remaining, review.ticket))).toMatchObject({ rejected: true })
    expect(await observe(recheck.call(binding, remainingReview.ticket))).toMatchObject({ rejected: true })
    const currentRemainingReview = remaining.reviewRecovery(capture.attemptId)
    expect(currentRemainingReview.permissions).toStrictEqual({ recheck: false, reapply: false, retry: false, discard: false })
    expect(await observe(recheckRemaining.call(remaining, currentRemainingReview.ticket))).toMatchObject({ rejected: true })
    const lookalike: RecoveryActionTicket = Object.freeze({ ...review.ticket })
    expect(lookalike).toStrictEqual(review.ticket)
    expect(lookalike).not.toBe(review.ticket)
    expect(await observe(recheck.call(binding, lookalike))).toMatchObject({ rejected: true })
    expect(await observe(recheckStore.call(store, lookalike))).toMatchObject({ rejected: true })
    await notificationTurn()
    await notificationTurn()
    expect(recheck.call(binding, review.ticket)).toBe(heldRead)
    expect(heldSettled).toBe(false)
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)
    expect(observed).toHaveLength(publicationsWhileHeld)
    expect(store.getSnapshot(workspace)).toBe(heldSnapshot)
    expect(store.getPersistenceStatus(workspace)).toBe(heldStatus)

    reviewedReply.resolve(reviewedUser)
    expect(await heldOutcome).toStrictEqual({ rejected: false })
    await drainFixedPoint()
    const ready = store.getSnapshot(workspace)
    // Native sequence: initial read 2, select retirement 3, post-save read 5,
    // reviewed read 6. Compare the actual qualified RAW leaf, not submitted data.
    const readyRecovery: Recovery = freezeFixture({
      ...expectedRecovery, phase: 'review-ready',
      recheck: {
        readId: 6, provenance: 'saved', target: reviewedUser.preferences!.dashboard_layouts![workspace],
        matchesSubmitted: false,
      },
    })
    expectFrozenPublicData(ready, freezeFixture({ ...failedSnapshot, recovery: readyRecovery }))
    expect(ready.recovery?.baseline).toBe(capture.baseline)
    expect(ready.recovery?.submitted).toBe(capture.submitted)
    expect(ready.recovery?.recheck?.target).not.toStrictEqual(capture.submitted)
    expect(ready.recovery?.recheck?.target).not.toStrictEqual(capture.baseline)
    expect(store.getPersistenceStatus(workspace)).toStrictEqual(statusBeforeReview)
    expect(store.retain(workspace)).toBeNull()
    expect(() => binding.open()).toThrow()
    expect(() => store.setDraft(failedTicket.sessionId, positiveDraft.items)).toThrow('Dashboard layout session is not editable')
    const freshReview = binding.reviewRecovery(capture.attemptId)
    expectFrozenPublicData(freshReview, freezeFixture({
      ticket: { ...expectedReview.ticket, readId: 6 }, recovery: readyRecovery,
      permissions: { recheck: true, reapply: false, retry: true, discard: true },
    }))
    expect(freshReview.recovery).toBe(ready.recovery)
    const publicationsReady = observed.length
    // Dedup is over; pre-read and held-phase tickets cannot authorize new reads.
    expect(await observe(recheck.call(binding, review.ticket))).toMatchObject({ rejected: true })
    expect(await observe(recheckStore.call(store, review.ticket))).toMatchObject({ rejected: true })
    expect(await observe(recheck.call(binding, whileHeldReview.ticket))).toMatchObject({ rejected: true })
    expect(await observe(binding.close(failedTicket, true))).toStrictEqual({ rejected: true, error: networkFailure })
    expect(binding.close(failedTicket, false)).toBe(failedClose)
    expect(store.finalize(failedTicket.sessionId)).toBe(storeCompletion)
    const retainedResult = await observe(storeCompletion)
    expect(retainedResult.rejected).toBe(true)
    if (retainedResult.rejected) expect(retainedResult.error).toBe(networkFailure)
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toBe(ready)
    expect(observed).toHaveLength(publicationsReady)
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)
    // Do not execute a fresh second read, replacement, retry, or discard here.
  } catch (error: unknown) {
    primaryFailed = true
    primaryError = error
  } finally {
    selectedWorkspace = workspace
    tokenIdentity = originalToken
    // Resolve/reject EVERY finite port before native task and publication drains.
    initialReply.resolve(initialUser)
    successReply.resolve(committed)
    postReply.resolve(postUser)
    failureReply.reject(networkFailure)
    reviewedReply.resolve(reviewedUser)
    try { await drainFixedPoint() } catch (error: unknown) { cleanupErrors.push(error) }
    for (const handle of handles) {
      try { releaseTracked(handle.binding) } catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drainFixedPoint() } catch (error: unknown) { cleanupErrors.push(error) }
    try {
      // Last-holder retirement may clear reviewed permission/phase; it must not
      // create transport, replay a write, or lose original retained provenance.
      if (premisesReady) {
        expect(reads).toBe(reviewedReadIssued ? 3 : 2)
        expect(captured).toHaveLength(2)
      }
      expect(handles.every((handle) => handle.released)).toBe(true)
      expect(store.getSnapshot(workspace).pending).toBe(false)
      expect(store.getSnapshot(workspace).readPending).toBe(false)
      if (retainedCapture) {
        const finalCapture = store.getSnapshot(workspace).recovery
        expect(finalCapture).toMatchObject({
          attemptId: 'dashboard-attempt-2', actorId: actor, authGeneration: generation,
          workspaceId: workspace, epoch: 2, category: 'unknown', phase: 'needs-recheck', recheck: null,
        })
        expect(finalCapture?.baseline).toBe(retainedCapture.baseline)
        expect(finalCapture?.submitted).toBe(retainedCapture.submitted)
        expect(store.getSnapshot(workspace).counters).toStrictEqual({
          success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0,
        })
      }
      expect(fixtures).toStrictEqual(fixturesBefore)
      expect(authSeed.preferences).toBe(seedPreferences)
      expect(DASHBOARD_WIDGETS).toStrictEqual(registryBefore)
    } catch (error: unknown) { cleanupErrors.push(error) }
    unsubscribe()
  }
  // Keep cleanup-only throws outside finally and preserve primary attribution.
  if (primaryFailed) {
    if (cleanupErrors.length > 0) throw new AggregateError([primaryError, ...cleanupErrors], 'Primary native reviewed Recheck failure with separate cleanup diagnostics')
    throw primaryError
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'Native reviewed Recheck cleanup failed')
})
