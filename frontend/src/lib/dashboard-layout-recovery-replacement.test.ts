import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease, type PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, type PersistenceStatus, type Recovery, type RecoveryActionTicket, type RecoveryReview, type SessionSnapshot, type SessionStore } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// TEST_AUTHOR_ONLY / NOT_RUN. Actual store + native lease, constructor I/O seams only.
// Optional structural ports preserve collection until the explicit missing-method RED.
type ReplacementBinding = PageBinding & {
  replaceRecovery?: (ticket: RecoveryActionTicket, action: 'reapply' | 'retry') => Promise<void>
}
type ReplacementStore = SessionStore & {
  replaceRecovery?: (ticket: RecoveryActionTicket, action: 'reapply' | 'retry') => Promise<void>
}
type Outcome = { rejected: false } | { rejected: true; error: unknown }

function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
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

it('native atomic Retry consumes a qualified unknown recovery into one held replacement without exposing an editable interval or reviving the original failure', async () => {
  const actor = '33333333-3333-4333-8333-333333333333'
  const workspace = '11111111-1111-4111-8111-111111111111'
  const generation = 7
  // Independently authored canonical RAW users/leaves and strict PUT replies.
  // No request echo, response normalizer golden, fake provider, or private forcing.
  const initialUser: User = freezeFixture({
    id: actor, email: 'native-atomic-retry@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Atomic Retry tracer', onboarding_completed: true,
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
  const positiveCommit: CommittedDashboardLayout = freezeFixture({
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
  const positiveUser: User = freezeFixture({
    id: actor, email: 'native-atomic-retry@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Atomic Retry tracer', onboarding_completed: true,
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
  const reviewedUser: User = freezeFixture({
    id: actor, email: 'native-atomic-retry@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Atomic Retry tracer', onboarding_completed: true,
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
  const retryCommit: CommittedDashboardLayout = freezeFixture({
    workspaceId: workspace,
    layout: {
      version: 1, workspaceId: workspace,
      items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: false, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  })
  const retryUser: User = freezeFixture({
    id: actor, email: 'native-atomic-retry@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Atomic Retry tracer', onboarding_completed: true,
      dashboard_layouts: {
        [workspace]: {
          version: 1, workspaceId: workspace,
          items: [
            { id: 'hero-balance', visible: true, order: 0 },
            { id: 'uncategorized-banner', visible: false, order: 1 },
            { id: 'spending-charts', visible: true, order: 2 },
            { id: 'balance-evolution', visible: true, order: 3 },
            { id: 'goals-progress', visible: false, order: 4 },
            { id: 'period-transactions', visible: true, order: 5 },
          ],
        },
      },
    },
  })
  const fixtures = freezeFixture({ initialUser, authSeed, positiveDraft, positiveCommit, positiveUser, failedDraft, reviewedUser, retryCommit, retryUser })
  const fixturesBefore = freezeFixture(structuredClone(fixtures))
  const registryBefore = freezeFixture(structuredClone(DASHBOARD_WIDGETS))
  const seedPreferences = authSeed.preferences
  const networkFailure = Object.freeze(new Error('Independent native atomic Retry original network failure'))
  const initialReply = deferred<User>()
  const positiveReply = deferred<CommittedDashboardLayout>()
  const positiveReadReply = deferred<User>()
  const failureReply = deferred<CommittedDashboardLayout>()
  const reviewedReply = deferred<User>()
  const retryReply = deferred<CommittedDashboardLayout>()
  const retryReadReply = deferred<User>()
  void failureReply.promise.catch(() => {})
  const tokenIdentity = 'synthetic-native-atomic-retry-boundary-token'
  let reads = 0
  const captured: Array<{
    workspaceId: string; body: DashboardLayout; binding: DashboardRequestBinding; currentAtIssuance: boolean
  }> = []
  const issued: Array<Promise<Outcome>> = []
  function observe(task: Promise<void>): Promise<Outcome> {
    const outcome = task.then<Outcome, Outcome>(
      () => ({ rejected: false }),
      (error: unknown) => ({ rejected: true, error }),
    )
    issued.push(outcome)
    return outcome
  }
  let callbackBinding: ReplacementBinding | null = null
  let callbackTicket: RecoveryActionTicket | null = null
  let originalSessionId: string | null = null
  const callbackViews: Array<{ snapshot: SessionSnapshot; status: PersistenceStatus; current: boolean }> = []
  const callbackOpen: Outcome[] = []
  const callbackSetDraft: Outcome[] = []
  const callbackReuse: Array<Promise<Outcome>> = []
  const store: ReplacementStore = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: actor, authGeneration: generation, authSeed,
    getTokenIdentity: () => tokenIdentity,
    getWorkspaceId: () => workspace,
    api: {
      me: () => {
        reads += 1
        if (reads === 1) return initialReply.promise
        if (reads === 2) return positiveReadReply.promise
        if (reads === 3) return reviewedReply.promise
        if (reads === 4) return retryReadReply.promise
        return Promise.reject(new Error('Unexpected native atomic Retry GET'))
      },
      saveLayout: (workspaceId, body, binding) => {
        captured.push({ workspaceId, body: structuredClone(body), binding, currentAtIssuance: binding.isCurrent() })
        if (captured.length === 1) return positiveReply.promise
        if (captured.length === 2) return failureReply.promise
        if (captured.length === 3) {
          // Record deterministic synchronous reentry; ALL assertions are outside
          // the transport callback/catch that classifies actual API failures.
          callbackViews.push({ snapshot: store.getSnapshot(workspace), status: store.getPersistenceStatus(workspace), current: binding.isCurrent() })
          if (callbackBinding) {
            try { callbackBinding.open(); callbackOpen.push({ rejected: false }) }
            catch (error: unknown) { callbackOpen.push({ rejected: true, error }) }
          }
          if (originalSessionId) {
            try { store.setDraft(originalSessionId, positiveDraft.items); callbackSetDraft.push({ rejected: false }) }
            catch (error: unknown) { callbackSetDraft.push({ rejected: true, error }) }
          }
          if (callbackBinding?.replaceRecovery && callbackTicket) {
            callbackReuse.push(observe(callbackBinding.replaceRecovery(callbackTicket, 'retry')))
          }
          return retryReply.promise
        }
        return Promise.reject(new Error('Unexpected native atomic Retry PUT'))
      },
    },
  })
  const lease = getPageSessionLease(store)
  const handles: Array<{ binding: ReplacementBinding; released: boolean }> = []
  function bindTracked(): ReplacementBinding {
    const binding: ReplacementBinding = lease.bind(workspace)
    handles.push({ binding, released: false })
    return binding
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
    throw new Error('Native atomic Retry task/publication fixed point did not settle')
  }
  let retainedCapture: Recovery | null = null
  let captureBefore: Recovery | null = null
  let premisesReady = false
  let replacementIssued = false
  let primaryFailed = false
  let primaryError: unknown
  const cleanupErrors: unknown[] = []
  try {
    const initialOutcome = observe(store.refresh(workspace))
    initialReply.resolve(initialUser)
    expect(await initialOutcome).toStrictEqual({ rejected: false })
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toMatchObject({
      writable: true, state: 'closed', pending: false, readPending: false,
      layout: initialUser.preferences!.dashboard_layouts![workspace], recovery: null,
    })
    lease.select(workspace)
    const binding = bindTracked()
    const positiveTicket = binding.open()
    store.setDraft(positiveTicket.sessionId, positiveDraft.items)
    const positiveOutcome = observe(binding.close(positiveTicket, true))
    await notificationTurn()
    expect(captured).toHaveLength(1)
    expect(captured[0]).toMatchObject({ workspaceId: workspace, body: positiveDraft, currentAtIssuance: true })
    expect(captured[0].binding.isCurrent()).toBe(true)
    positiveReply.resolve(positiveCommit)
    await notificationTurn()
    await notificationTurn()
    expect(reads).toBe(2)
    positiveReadReply.resolve(positiveUser)
    expect(await positiveOutcome).toStrictEqual({ rejected: false })
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toMatchObject({
      writable: true, state: 'closed-success', pending: false, readPending: false,
      layout: positiveCommit.layout, recovery: null,
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(captured[0].binding.isCurrent()).toBe(false)
    expect(store.getPersistenceStatus(workspace).canCustomize).toBe(true)

    const failedTicket = binding.open()
    originalSessionId = failedTicket.sessionId
    store.setDraft(failedTicket.sessionId, failedDraft.items)
    const failedClose = binding.close(failedTicket, true)
    const failedOutcome = observe(failedClose)
    const originalCompletion = store.finalize(failedTicket.sessionId)
    const originalOutcome = observe(originalCompletion)
    expect(binding.close(failedTicket, false)).toBe(failedClose)
    expect(store.finalize(failedTicket.sessionId)).toBe(originalCompletion)
    await notificationTurn()
    expect(captured).toHaveLength(2)
    expect(captured[1].workspaceId).toBe(workspace)
    expect(captured[1].body).toStrictEqual(failedDraft)
    expect(captured[1].currentAtIssuance).toBe(true)
    expect(captured[1].binding.isCurrent()).toBe(true)
    failureReply.reject(networkFailure)
    const failedResult = await failedOutcome
    const originalResult = await originalOutcome
    expect(failedResult).toStrictEqual({ rejected: true, error: networkFailure })
    expect(originalResult).toStrictEqual({ rejected: true, error: networkFailure })
    if (failedResult.rejected) expect(failedResult.error).toBe(networkFailure)
    if (originalResult.rejected) expect(originalResult.error).toBe(networkFailure)
    await drainFixedPoint()
    const expectedRecovery: Recovery = freezeFixture({
      attemptId: 'dashboard-attempt-2', actorId: actor, authGeneration: generation,
      workspaceId: workspace, epoch: 2, baseline: positiveCommit.layout, submitted: failedDraft,
      category: 'unknown', phase: 'needs-recheck', recheck: null,
    })
    const failedSnapshot = store.getSnapshot(workspace)
    expect(failedSnapshot.recovery).toStrictEqual(expectedRecovery)
    const capture = failedSnapshot.recovery
    if (!capture) throw new Error('Missing genuine native retained unknown failure')
    retainedCapture = capture
    captureBefore = freezeFixture(structuredClone(capture))
    expect(Object.isFrozen(capture)).toBe(true)
    expect(reads).toBe(2) // Network unknown is not conflict: no automatic GET.
    expect(store.retain(workspace)).toBeNull()
    expect(() => binding.open()).toThrow()
    expect(() => store.setDraft(failedTicket.sessionId, positiveDraft.items)).toThrow('Dashboard layout session is not editable')
    const failureStatus = store.getPersistenceStatus(workspace)
    expect(failureStatus).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: true, canCustomize: false, failureKind: 'unknown',
    })
    const beforeReadonlyReview = observed.length
    const review = binding.reviewRecovery(capture.attemptId)
    expect(review.permissions).toStrictEqual({ recheck: true, reapply: false, retry: false, discard: true })
    expect(review.recovery).toBe(capture)
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toBe(failedSnapshot)
    expect(store.getPersistenceStatus(workspace)).toBe(failureStatus)
    expect(observed).toHaveLength(beforeReadonlyReview)
    expect(reads).toBe(2)
    expect(captured).toHaveLength(2)

    const heldRead = binding.recheckReviewedRecovery(review.ticket)
    const heldOutcome = observe(heldRead)
    let readSettled = false
    void heldOutcome.then(() => { readSettled = true })
    await notificationTurn()
    await notificationTurn()
    expect(reads).toBe(3)
    expect(readSettled).toBe(false)
    expect(captured).toHaveLength(2)
    expect(store.getSnapshot(workspace)).toMatchObject({
      state: 'unknown', pending: false, readPending: true,
      recovery: { category: 'unknown', phase: 'rechecking', recheck: null },
    })
    reviewedReply.resolve(reviewedUser)
    expect(await heldOutcome).toStrictEqual({ rejected: false })
    await drainFixedPoint()
    const ready = store.getSnapshot(workspace)
    const readyRecovery: Recovery = freezeFixture({
      ...expectedRecovery, phase: 'review-ready',
      recheck: { readId: 6, provenance: 'saved', target: reviewedUser.preferences!.dashboard_layouts![workspace], matchesSubmitted: false },
    })
    expect(ready).toStrictEqual({ ...failedSnapshot, recovery: readyRecovery })
    expect(ready.recovery?.baseline).toBe(capture.baseline)
    expect(ready.recovery?.submitted).toBe(capture.submitted)
    expect(ready.recovery?.recheck?.target).not.toStrictEqual(capture.submitted)
    expect(ready.recovery?.recheck?.target).not.toStrictEqual(capture.baseline)
    const freshReview = binding.reviewRecovery(capture.attemptId)
    const expectedReview: RecoveryReview = freezeFixture({
      ticket: { attemptId: 'dashboard-attempt-2', actorId: actor, authGeneration: generation, workspaceId: workspace, epoch: 2, readId: 6 },
      recovery: readyRecovery, permissions: { recheck: true, reapply: false, retry: true, discard: true },
    })
    expect(freshReview).toStrictEqual(expectedReview)
    expect(Object.isFrozen(freshReview)).toBe(true)
    expect(Object.isFrozen(freshReview.ticket)).toBe(true)
    expect(Object.isFrozen(freshReview.permissions)).toBe(true)
    expect(freshReview.recovery).toBe(ready.recovery)
    expect(store.retain(workspace)).toBeNull()
    expect(() => binding.open()).toThrow()
    expect(() => store.setDraft(failedTicket.sessionId, positiveDraft.items)).toThrow('Dashboard layout session is not editable')
    expect(binding.close(failedTicket, true)).toBe(failedClose)
    expect(store.finalize(failedTicket.sessionId)).toBe(originalCompletion)
    await drainFixedPoint()
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)
    premisesReady = true

    // Predicted genuine RED: this assertion, after actual positive save, original
    // unknown failure, readonly Review and held/strict-qualified reviewed Recheck.
    const replace = binding.replaceRecovery
    expect(typeof replace, 'native PageBinding.replaceRecovery must atomically replace a qualified unknown recovery').toBe('function')
    if (typeof replace !== 'function') throw new Error('Missing native binding atomic replacement')
    const replaceStore = store.replaceRecovery
    expect(typeof replaceStore, 'native SessionStore.replaceRecovery must own atomic replacement completion').toBe('function')
    if (typeof replaceStore !== 'function') throw new Error('Missing native store atomic replacement')

    const readyStatus = store.getPersistenceStatus(workspace)
    const beforeGuards = observed.length
    // Wrong category action must preserve this EXACT valid ticket/permission;
    // the positive Retry below uses it, not a freshly reissued substitute.
    expect(await observe(replace.call(binding, freshReview.ticket, 'reapply'))).toMatchObject({ rejected: true })
    const lookalike: RecoveryActionTicket = Object.freeze({ ...freshReview.ticket })
    expect(lookalike).toStrictEqual(freshReview.ticket)
    expect(lookalike).not.toBe(freshReview.ticket)
    expect(await observe(replace.call(binding, lookalike, 'retry'))).toMatchObject({ rejected: true })
    expect(await observe(replaceStore.call(store, lookalike, 'retry'))).toMatchObject({ rejected: true })
    const foreign = bindTracked()
    const foreignReplace = foreign.replaceRecovery
    expect(typeof foreignReplace).toBe('function')
    if (typeof foreignReplace !== 'function') throw new Error('Missing foreign holder replacement port')
    expect(await observe(foreignReplace.call(foreign, freshReview.ticket, 'retry'))).toMatchObject({ rejected: true })
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toBe(ready)
    expect(store.getPersistenceStatus(workspace)).toBe(readyStatus)
    expect(observed).toHaveLength(beforeGuards)
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)

    callbackBinding = binding
    callbackTicket = freshReview.ticket
    const beforeReplacement = observed.length
    const replacement = replace.call(binding, freshReview.ticket, 'retry')
    const replacementOutcome = observe(replacement) // Before any API microtask.
    replacementIssued = true
    let replacementSettled = false
    void replacementOutcome.then(() => { replacementSettled = true })
    const pending = store.getSnapshot(workspace)
    expect(pending).toStrictEqual({
      ...ready, writable: true, state: 'pending', pending: true, readPending: false,
      canUndo: false, openedBaseline: reviewedUser.preferences!.dashboard_layouts![workspace],
      draft: failedDraft, recovery: null,
    })
    expect(pending.openedBaseline).not.toBe(ready.recovery?.recheck?.target)
    expect(pending.draft).not.toBe(capture.submitted)
    expect(store.getPersistenceStatus(workspace)).toStrictEqual({
      pending: true, readPending: false, stale: false, unsaved: true, canCustomize: false, failureKind: null,
    })
    expect(captured).toHaveLength(2) // Admission/final latch precedes transport.
    expect(() => binding.open()).toThrow()
    expect(() => store.setDraft(failedTicket.sessionId, positiveDraft.items)).toThrow('Dashboard layout session is not editable')
    // Public pending-session lookup verifies the returned STORE completion, not
    // a new DialogTicket or adoption by binding.open; old session lookup is gone.
    const pendingSession = store.retain(workspace)
    expect(pendingSession).not.toBeNull()
    expect(pendingSession).not.toBe(failedTicket.sessionId)
    if (!pendingSession) throw new Error('Missing native replacement pending session')
    expect(store.finalize(pendingSession)).toBe(replacement)
    expect(binding.close(failedTicket, false)).toBe(failedClose)
    const retainedClose = observe(binding.close(failedTicket, true))
    const retainedCompletion = observe(originalCompletion)
    await notificationTurn()
    await notificationTurn()
    expect(captured).toHaveLength(3)
    expect(captured[2].workspaceId).toBe(workspace)
    expect(captured[2].body).toStrictEqual(failedDraft)
    expect(captured[2].currentAtIssuance).toBe(true)
    expect(captured[2].binding.isCurrent()).toBe(true)
    expect(reads).toBe(3)
    expect(replacementSettled).toBe(false)
    expect(callbackViews).toHaveLength(1)
    expect(callbackViews[0]).toStrictEqual({ snapshot: pending, status: store.getPersistenceStatus(workspace), current: true })
    expect(callbackOpen).toHaveLength(1)
    expect(callbackOpen[0]).toMatchObject({ rejected: true })
    expect(callbackSetDraft).toHaveLength(1)
    expect(callbackSetDraft[0]).toMatchObject({ rejected: true })
    expect(callbackReuse).toHaveLength(1)
    expect(await callbackReuse[0]).toMatchObject({ rejected: true })
    expect(await observe(replace.call(binding, freshReview.ticket, 'retry'))).toMatchObject({ rejected: true })
    const retainedCloseResult = await retainedClose
    const retainedCompletionResult = await retainedCompletion
    expect(retainedCloseResult).toStrictEqual({ rejected: true, error: networkFailure })
    expect(retainedCompletionResult).toStrictEqual({ rejected: true, error: networkFailure })
    if (retainedCloseResult.rejected) expect(retainedCloseResult.error).toBe(networkFailure)
    if (retainedCompletionResult.rejected) expect(retainedCompletionResult.error).toBe(networkFailure)
    await notificationTurn()
    await notificationTurn()
    expect(captured).toHaveLength(3)
    expect(reads).toBe(3)
    expect(observed.slice(beforeReplacement).length).toBeGreaterThan(0)
    for (const snapshot of observed.slice(beforeReplacement)) {
      expect(snapshot.state).toBe('pending')
      expect(snapshot.pending).toBe(true)
      expect(snapshot.recovery).toBeNull()
      expect(snapshot.openedBaseline).toStrictEqual(reviewedUser.preferences!.dashboard_layouts![workspace])
      expect(snapshot.draft).toStrictEqual(failedDraft)
    }
    expect(store.getSnapshot(workspace)).toBe(pending)
    expect(captured[2].binding.isCurrent()).toBe(true)

    retryReply.resolve(retryCommit)
    await notificationTurn()
    await notificationTurn()
    expect(reads).toBe(4)
    expect(captured[2].binding.isCurrent()).toBe(false) // Consumed request, eligible owner.
    retryReadReply.resolve(retryUser)
    expect(await replacementOutcome).toStrictEqual({ rejected: false })
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toStrictEqual({
      actorId: actor, authGeneration: generation, workspaceId: workspace,
      provenance: 'saved', writable: true, state: 'closed-success', pending: false, readPending: false,
      canUndo: false, layout: retryCommit.layout, openedBaseline: null, draft: null, recovery: null,
      overlay: { [workspace]: retryCommit.layout },
      counters: { success: 2, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(store.getPersistenceStatus(workspace)).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: false, canCustomize: true, failureKind: null,
    })
    expect(binding.close(failedTicket, true)).toBe(failedClose)
    const finalOriginal = await observe(originalCompletion)
    const finalClose = await observe(failedClose)
    if (finalOriginal.rejected) expect(finalOriginal.error).toBe(networkFailure)
    if (finalClose.rejected) expect(finalClose.error).toBe(networkFailure)
    expect(finalOriginal.rejected).toBe(true)
    expect(finalClose.rejected).toBe(true)
    expect(capture.baseline).toBe(retainedCapture.baseline)
    expect(capture.submitted).toBe(retainedCapture.submitted)
    expect(capture).toStrictEqual(captureBefore)
    expect(freshReview).toStrictEqual(expectedReview)
    expect(reads).toBe(4)
    expect(captured).toHaveLength(3)
  } catch (error: unknown) {
    primaryFailed = true
    primaryError = error
  } finally {
    // Settle EVERY finite port, including ones not issued on the missing-method
    // RED. Separate lease/store/observed-task/publication barriers follow.
    initialReply.resolve(initialUser)
    positiveReply.resolve(positiveCommit)
    positiveReadReply.resolve(positiveUser)
    failureReply.reject(networkFailure)
    reviewedReply.resolve(reviewedUser)
    retryReply.resolve(retryCommit)
    retryReadReply.resolve(retryUser)
    try { await drainFixedPoint() } catch (error: unknown) { cleanupErrors.push(error) }
    for (const handle of handles) {
      if (handle.released) continue
      handle.released = true
      try { handle.binding.release() } catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drainFixedPoint() } catch (error: unknown) { cleanupErrors.push(error) }
    try {
      if (premisesReady) {
        expect(reads).toBe(replacementIssued ? 4 : 3)
        expect(captured).toHaveLength(replacementIssued ? 3 : 2)
      }
      expect(handles.every((handle) => handle.released)).toBe(true)
      expect(store.getSnapshot(workspace).pending).toBe(false)
      expect(store.getSnapshot(workspace).readPending).toBe(false)
      if (retainedCapture) expect(retainedCapture).toStrictEqual(captureBefore)
      expect(fixtures).toStrictEqual(fixturesBefore)
      expect(authSeed.preferences).toBe(seedPreferences)
      expect(DASHBOARD_WIDGETS).toStrictEqual(registryBefore)
    } catch (error: unknown) { cleanupErrors.push(error) }
    unsubscribe()
  }
  // Do not let cleanup replace the primary RED or throw unsafely in finally.
  if (primaryFailed) {
    if (cleanupErrors.length > 0) throw new AggregateError([primaryError, ...cleanupErrors], 'Primary native atomic Retry failure with separate cleanup diagnostics')
    throw primaryError
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'Native atomic Retry cleanup failed')
})
