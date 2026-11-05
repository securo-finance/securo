import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease, type DialogTicket, type PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, type Recovery, type RecoveryActionTicket, type RecoveryReview, type SessionSnapshot, type SessionStore } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// TEST_AUTHOR_ONLY / NOT_RUN: constructor I/O seams, actual store/native lease.
// Optional prospective ports allow collection and an explicit assertion-level RED.
type DiscardBinding = PageBinding & { discardRecovery?: (ticket: RecoveryActionTicket) => void }
type DiscardStore = SessionStore & { discardRecovery?: (ticket: RecoveryActionTicket) => void }
type Outcome = { rejected: false } | { rejected: true; error: unknown }

function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
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
  return { promise, resolve, reject }
}

function notificationTurn(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('native explicit Discard consumes only qualified unknown recovery without transport, counter laundering, or reviving the original failed editor', async () => {
  const actor = '33333333-3333-4333-8333-333333333333'
  const workspace = '11111111-1111-4111-8111-111111111111'
  const generation = 7
  // Independent strict RAW responses: never synthesize a reply from a request
  // or use a production normalizer as the expected target/commit oracle.
  const initialUser: User = freezeFixture({
    id: actor, email: 'native-explicit-discard@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Explicit Discard tracer', onboarding_completed: true,
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
    id: actor, email: 'native-explicit-discard@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Explicit Discard tracer', onboarding_completed: true,
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
    id: actor, email: 'native-explicit-discard@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Explicit Discard tracer', onboarding_completed: true,
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
  const newUnsentDraft: DashboardLayout = freezeFixture({
    version: 1, workspaceId: workspace,
    items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  })
  const fixtures = freezeFixture({ initialUser, authSeed, positiveDraft, positiveCommit, positiveUser, failedDraft, reviewedUser, newUnsentDraft })
  const fixturesBefore = freezeFixture(structuredClone(fixtures))
  const registryBefore = freezeFixture(structuredClone(DASHBOARD_WIDGETS))
  const seedPreferences = authSeed.preferences
  const networkFailure = Object.freeze(new Error('Independent native explicit Discard original network failure'))
  const tokenIdentity = 'synthetic-native-explicit-discard-boundary-token'
  const initialReply = deferred<User>()
  const positiveReply = deferred<CommittedDashboardLayout>()
  const positiveReadReply = deferred<User>()
  const failureReply = deferred<CommittedDashboardLayout>()
  const reviewedReply = deferred<User>()
  void failureReply.promise.catch(() => {})
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
  const store: DiscardStore = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: actor, authGeneration: generation, authSeed,
    getTokenIdentity: () => tokenIdentity,
    getWorkspaceId: () => workspace,
    api: {
      me: () => {
        reads += 1
        if (reads === 1) return initialReply.promise
        if (reads === 2) return positiveReadReply.promise
        if (reads === 3) return reviewedReply.promise
        return Promise.reject(new Error('Unexpected native explicit Discard GET'))
      },
      saveLayout: (workspaceId, body, binding) => {
        // Callbacks record only; assertions run outside API classification catches.
        captured.push({ workspaceId, body: structuredClone(body), binding, currentAtIssuance: binding.isCurrent() })
        if (captured.length === 1) return positiveReply.promise
        if (captured.length === 2) return failureReply.promise
        return Promise.reject(new Error('Unexpected native explicit Discard PUT'))
      },
    },
  })
  const lease = getPageSessionLease(store)
  const handles: Array<{ binding: DiscardBinding; released: boolean }> = []
  function bindTracked(): DiscardBinding {
    const binding: DiscardBinding = lease.bind(workspace)
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
    throw new Error('Native explicit Discard task/publication fixed point did not settle')
  }
  let retainedCapture: Recovery | null = null
  let captureBefore: Recovery | null = null
  let freshEditor: { binding: DiscardBinding; ticket: DialogTicket } | null = null
  let premisesReady = false
  let discarded = false
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
    expect(store.getSnapshot(workspace).overlay).toStrictEqual({ [workspace]: positiveCommit.layout })
    expect(captured[0].binding.isCurrent()).toBe(false)
    expect(store.getPersistenceStatus(workspace).canCustomize).toBe(true)

    const failedTicket = binding.open()
    store.setDraft(failedTicket.sessionId, failedDraft.items)
    const failedClose = binding.close(failedTicket, true)
    const failedOutcome = observe(failedClose)
    await notificationTurn()
    expect(captured).toHaveLength(2)
    expect(captured[1].workspaceId).toBe(workspace)
    expect(captured[1].body).toStrictEqual(failedDraft)
    expect(captured[1].currentAtIssuance).toBe(true)
    expect(captured[1].binding.isCurrent()).toBe(true)
    // Capture the real store completion after actual PUT issuance, before failure.
    const originalCompletion = store.finalize(failedTicket.sessionId)
    const originalOutcome = observe(originalCompletion)
    expect(store.finalize(failedTicket.sessionId)).toBe(originalCompletion)
    expect(binding.close(failedTicket, false)).toBe(failedClose)
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
    expectFrozen(capture)
    expect(reads).toBe(2) // Original network failure is not 409: no automatic GET.
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

    const heldOutcome = observe(binding.recheckReviewedRecovery(review.ticket))
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
    const rawTarget = reviewedUser.preferences!.dashboard_layouts![workspace]
    const readyRecovery: Recovery = freezeFixture({
      ...expectedRecovery, phase: 'review-ready',
      recheck: { readId: 6, provenance: 'saved', target: rawTarget, matchesSubmitted: false },
    })
    const ready = store.getSnapshot(workspace)
    expect(ready).toStrictEqual({ ...failedSnapshot, recovery: readyRecovery })
    expect(ready.recovery?.baseline).toBe(capture.baseline)
    expect(ready.recovery?.submitted).toBe(capture.submitted)
    expect(rawTarget).not.toStrictEqual(capture.baseline)
    expect(rawTarget).not.toStrictEqual(capture.submitted)
    const freshReview = binding.reviewRecovery(capture.attemptId)
    const expectedReview: RecoveryReview = freezeFixture({
      ticket: { attemptId: 'dashboard-attempt-2', actorId: actor, authGeneration: generation, workspaceId: workspace, epoch: 2, readId: 6 },
      recovery: readyRecovery, permissions: { recheck: true, reapply: false, retry: true, discard: true },
    })
    expect(freshReview).toStrictEqual(expectedReview)
    expectFrozen(freshReview)
    expect(freshReview.recovery).toBe(ready.recovery)
    expect(store.retain(workspace)).toBeNull()
    expect(() => binding.open()).toThrow()
    expect(() => store.setDraft(failedTicket.sessionId, positiveDraft.items)).toThrow('Dashboard layout session is not editable')
    expect(binding.close(failedTicket, true)).toBe(failedClose)
    await drainFixedPoint()
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)
    premisesReady = true

    // Predicted RED is this explicit function assertion, not import/setup/TypeError.
    const discard = binding.discardRecovery
    expect(typeof discard, 'native PageBinding.discardRecovery must synchronously consume qualified retained recovery').toBe('function')
    if (typeof discard !== 'function') throw new Error('Missing native binding explicit Discard')
    const discardStore = store.discardRecovery
    expect(typeof discardStore, 'native SessionStore.discardRecovery must own local recovery consumption').toBe('function')
    if (typeof discardStore !== 'function') throw new Error('Missing native store explicit Discard')

    const readyStatus = store.getPersistenceStatus(workspace)
    const beforeGuards = observed.length
    const lookalike: RecoveryActionTicket = Object.freeze({ ...freshReview.ticket })
    expect(lookalike).toStrictEqual(freshReview.ticket)
    expect(lookalike).not.toBe(freshReview.ticket)
    expect(discard.call(binding, lookalike)).toBeUndefined()
    expect(discardStore.call(store, lookalike)).toBeUndefined()
    // Authentic, but stale: its original capture/read sequence preceded Recheck.
    expect(discard.call(binding, review.ticket)).toBeUndefined()
    expect(discardStore.call(store, review.ticket)).toBeUndefined()
    const foreign = bindTracked()
    const foreignDiscard = foreign.discardRecovery
    expect(typeof foreignDiscard).toBe('function')
    if (typeof foreignDiscard !== 'function') throw new Error('Missing foreign holder Discard port')
    expect(foreignDiscard.call(foreign, freshReview.ticket)).toBeUndefined()
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toBe(ready)
    expect(store.getPersistenceStatus(workspace)).toBe(readyStatus)
    expect(ready.recovery).toBe(freshReview.recovery)
    expect(freshReview).toStrictEqual(expectedReview)
    expect(observed).toHaveLength(beforeGuards)
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)

    // Use the SAME authentic ticket preserved by all refusals above.
    const beforeDiscard = observed.length
    expect(discard.call(binding, freshReview.ticket)).toBeUndefined()
    discarded = true
    const expectedDiscarded: SessionSnapshot = freezeFixture({
      actorId: actor, authGeneration: generation, workspaceId: workspace,
      provenance: 'saved', writable: true, state: 'closed', pending: false, readPending: false,
      canUndo: false, layout: rawTarget, openedBaseline: null, draft: null, recovery: null,
      overlay: {}, counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    const closed = store.getSnapshot(workspace)
    expect(closed).toStrictEqual(expectedDiscarded)
    // Empty subset matching is insufficient: remove the prior PUT overlay exactly.
    expect(closed.overlay).toStrictEqual({})
    expectFrozen(closed)
    const closedStatus = store.getPersistenceStatus(workspace)
    expect(closedStatus).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: false, canCustomize: true, failureKind: null,
    })
    expectFrozen(closedStatus)
    expect(JSON.stringify(closed)).not.toContain(tokenIdentity)
    expect(JSON.stringify(closed)).not.toContain(networkFailure.message)
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)
    expect(store.retain(workspace)).toBeNull()
    expect(() => store.setDraft(failedTicket.sessionId, positiveDraft.items)).toThrow('Dashboard layout session is not editable')
    expect(() => binding.reviewRecovery(capture.attemptId)).toThrow()
    await drainFixedPoint()
    expect(observed.slice(beforeDiscard)).toStrictEqual([closed])

    // Observe captured tasks, not store.finalize(oldSession) after consumption.
    expect(binding.close(failedTicket, false)).toBe(failedClose)
    const retainedClose = await observe(binding.close(failedTicket, true))
    const retainedCompletion = await observe(originalCompletion)
    expect(retainedClose).toStrictEqual({ rejected: true, error: networkFailure })
    expect(retainedCompletion).toStrictEqual({ rejected: true, error: networkFailure })
    if (retainedClose.rejected) expect(retainedClose.error).toBe(networkFailure)
    if (retainedCompletion.rejected) expect(retainedCompletion.error).toBe(networkFailure)
    const beforeReuse = observed.length
    expect(discard.call(binding, freshReview.ticket)).toBeUndefined()
    expect(discardStore.call(store, freshReview.ticket)).toBeUndefined()
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toBe(closed)
    expect(store.getPersistenceStatus(workspace)).toBe(closedStatus)
    expect(observed).toHaveLength(beforeReuse)
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)

    // Genuine fresh ordinary editing proves Discard did not disable all admission.
    const newTicket = binding.open()
    freshEditor = { binding, ticket: newTicket }
    expect(newTicket).not.toBe(failedTicket)
    expect(newTicket.sessionId).not.toBe(failedTicket.sessionId)
    expect(store.getSnapshot(workspace)).toStrictEqual({
      ...expectedDiscarded, state: 'editing', openedBaseline: rawTarget, draft: rawTarget,
    })
    expect(store.retain(workspace)).toBe(newTicket.sessionId)
    store.setDraft(newTicket.sessionId, newUnsentDraft.items)
    expect(store.getSnapshot(workspace).draft).toStrictEqual(newUnsentDraft)
    expect(store.getPersistenceStatus(workspace).unsaved).toBe(true)
    // Old consumed recovery authority cannot clear this genuinely changed editor.
    const editing = store.getSnapshot(workspace)
    expect(discard.call(binding, freshReview.ticket)).toBeUndefined()
    expect(discardStore.call(store, freshReview.ticket)).toBeUndefined()
    expect(store.getSnapshot(workspace)).toBe(editing)
    expect(await observe(binding.close(newTicket, false))).toStrictEqual({ rejected: false })
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toStrictEqual(expectedDiscarded)
    expect(store.getSnapshot(workspace).overlay).toStrictEqual({})
    expect(store.getPersistenceStatus(workspace)).toStrictEqual(closedStatus)
    expect(capture).toStrictEqual(captureBefore)
    expect(freshReview).toStrictEqual(expectedReview)
    expect(reads).toBe(3)
    expect(captured).toHaveLength(2)
  } catch (error: unknown) {
    primaryFailed = true
    primaryError = error
  } finally {
    // Settle all finite ports even on the explicit missing-method RED.
    initialReply.resolve(initialUser)
    positiveReply.resolve(positiveCommit)
    positiveReadReply.resolve(positiveUser)
    failureReply.reject(networkFailure)
    reviewedReply.resolve(reviewedUser)
    // A failing fresh-editor oracle must not let final release save an unsent edit.
    if (freshEditor) {
      try { await observe(freshEditor.binding.close(freshEditor.ticket, false)) }
      catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drainFixedPoint() } catch (error: unknown) { cleanupErrors.push(error) }
    const beforeRelease = { reads, writes: captured.length }
    for (const handle of handles) {
      if (handle.released) continue
      handle.released = true
      try { handle.binding.release() } catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drainFixedPoint() } catch (error: unknown) { cleanupErrors.push(error) }
    try {
      expect(reads).toBe(beforeRelease.reads)
      expect(captured).toHaveLength(beforeRelease.writes)
      if (premisesReady) {
        expect(reads).toBe(3)
        expect(captured).toHaveLength(2)
      }
      expect(handles.every((handle) => handle.released)).toBe(true)
      expect(store.getSnapshot(workspace).pending).toBe(false)
      expect(store.getSnapshot(workspace).readPending).toBe(false)
      // RED retains recovery; release can retire its qualified phase. Do not
      // replace the intended missing-method failure with a post-Discard oracle.
      if (discarded) expect(store.getSnapshot(workspace).recovery).toBeNull()
      if (retainedCapture) expect(retainedCapture).toStrictEqual(captureBefore)
      expect(fixtures).toStrictEqual(fixturesBefore)
      expect(authSeed.preferences).toBe(seedPreferences)
      expect(DASHBOARD_WIDGETS).toStrictEqual(registryBefore)
    } catch (error: unknown) { cleanupErrors.push(error) }
    unsubscribe()
  }
  // Separate primary attribution from cleanup; never throw inside finally.
  if (primaryFailed) {
    if (cleanupErrors.length > 0) throw new AggregateError([primaryError, ...cleanupErrors], 'Primary native explicit Discard failure with separate cleanup diagnostics')
    throw primaryError
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'Native explicit Discard cleanup failed')
})
