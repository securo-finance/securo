import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease, type PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, type LivePageLeaseWitness, type Recovery, type SessionSnapshot, type SessionStore } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// AUTHORING_ONLY: real native store + native lease; ONLY StoreOptions I/O is substituted.
// NOT AuthProvider/SDK/interceptors/hooks/components/App/browser evidence.
const TEST_DATA = Object.freeze({
  actor: '33333333-3333-4333-8333-333333333333',
  workspace: '11111111-1111-4111-8111-111111111111',
  foreignWorkspace: '22222222-2222-4222-8222-222222222222',
  generation: 7,
})

// Inline optional prospective ports: collection imports no future API/export.
type ReadonlyReview = Readonly<{
  ticket: Readonly<{
    attemptId: string; actorId: string; authGeneration: number
    workspaceId: string; epoch: number; readId: number | null
  }>
  recovery: Recovery
  permissions: Readonly<{ recheck: boolean; reapply: boolean; retry: boolean; discard: boolean }>
}>
type ReviewBinding = PageBinding & { reviewRecovery?: (attemptId: string) => ReadonlyReview }
type ReviewStore = SessionStore & {
  reviewRecovery?: (workspaceId: string, attemptId: string, witness: LivePageLeaseWitness) => ReadonlyReview
}
type Outcome = { rejected: false } | { rejected: true; error: unknown }

function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}

// Also excludes nonenumerable/symbol fields, functions and prototype-carried internals.
function expectFrozenPublicData(actual: unknown, expected: unknown): void {
  expect(actual).toStrictEqual(expected)
  if (actual !== null && typeof actual === 'object' && expected !== null && typeof expected === 'object') {
    expect(Object.isFrozen(actual)).toBe(true)
    expect(Object.getPrototypeOf(actual)).toBe(Array.isArray(expected) ? Array.prototype : Object.prototype)
    expect(new Set(Reflect.ownKeys(actual))).toStrictEqual(new Set(Reflect.ownKeys(expected)))
    const actualFields = actual as Record<string, unknown>
    const expectedFields = expected as Record<string, unknown>
    for (const key of Object.keys(expectedFields)) {
      expect(Object.getOwnPropertyDescriptor(actual, key)?.enumerable).toBe(true)
      expectFrozenPublicData(actualFields[key], expectedFields[key])
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

it('native binding reviews an authentic retained unknown save readonly without reviving its consumed editor or granting released handles access', async () => {
  const { actor, workspace, foreignWorkspace, generation } = TEST_DATA
  // Independent RAW user/map/leaf fixtures, never a normalizer/request echo.
  const initialUser: User = freezeFixture({
    id: actor, email: 'native-review-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Native review tracer', onboarding_completed: true,
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
  const firstDraft: DashboardLayout = freezeFixture({
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
  // Independent committed reply, not synthesized from the captured PUT.
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
    id: actor, email: 'native-review-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Native review tracer', onboarding_completed: true,
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
  const fixtures = freezeFixture({ initialUser, authSeed, firstDraft, committed, postUser, failedDraft })
  const fixturesBefore = freezeFixture(structuredClone(fixtures))
  const seedPreferences = authSeed.preferences
  const registryBefore = freezeFixture(structuredClone(DASHBOARD_WIDGETS))
  const networkFailure = Object.freeze(new Error('Independent native readonly Review transport failure'))
  const initialReply = deferred<User>()
  const successReply = deferred<CommittedDashboardLayout>()
  const postReply = deferred<User>()
  const failureReply = deferred<CommittedDashboardLayout>()
  let selectedWorkspace: string = workspace
  const originalToken = 'synthetic-native-review-boundary-token'
  let tokenIdentity: string | null = originalToken
  let reads = 0
  const captured: Array<{
    workspaceId: string; body: DashboardLayout; binding: DashboardRequestBinding; currentAtIssuance: boolean
  }> = []
  const store: ReviewStore = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: actor, authGeneration: generation, authSeed,
    getTokenIdentity: () => tokenIdentity,
    getWorkspaceId: () => selectedWorkspace,
    api: {
      me: () => {
        reads += 1
        if (reads === 1) return initialReply.promise
        if (reads === 2) return postReply.promise
        return Promise.reject(new Error('Unexpected native Review GET'))
      },
      saveLayout: (workspaceId, body, binding) => {
        // Record native guard witnesses; assertions stay outside isolated callbacks.
        captured.push({ workspaceId, body: structuredClone(body), binding, currentAtIssuance: binding.isCurrent() })
        if (captured.length === 1) return successReply.promise
        if (captured.length === 2) return failureReply.promise
        return Promise.reject(new Error('Unexpected native Review PUT'))
      },
    },
  })
  const lease = getPageSessionLease(store)
  const handles: Array<{ binding: ReviewBinding; released: boolean }> = []
  function bindTracked(workspaceId: string): ReviewBinding {
    const binding: ReviewBinding = lease.bind(workspaceId)
    handles.push({ binding, released: false })
    return binding
  }
  function releaseTracked(binding: ReviewBinding): void {
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
    throw new Error('Native Review task/publication fixed point did not settle')
  }
  let retainedSnapshot: SessionSnapshot | null = null
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
    expect(store.getPersistenceStatus(workspace).canCustomize).toBe(true)
    lease.select(workspace)
    const binding = bindTracked(workspace)
    expect(reads).toBe(1)
    expect(captured).toHaveLength(0)

    // Real positive save through this genuine live holder before any future oracle.
    const successfulTicket = binding.open()
    expect(successfulTicket.workspaceId).toBe(workspace)
    store.setDraft(successfulTicket.sessionId, firstDraft.items)
    const successfulClose = binding.close(successfulTicket, true)
    const successOutcome = observe(successfulClose)
    await notificationTurn()
    expect(captured).toHaveLength(1)
    expect(captured[0].workspaceId).toBe(workspace)
    expect(captured[0].body).toStrictEqual(firstDraft)
    expect(captured[0].currentAtIssuance).toBe(true)
    expect(captured[0].binding.isCurrent()).toBe(true)
    expect(store.getSnapshot(workspace).pending).toBe(true)
    successReply.resolve(committed)
    await notificationTurn()
    await notificationTurn()
    expect(reads).toBe(2)
    postReply.resolve(postUser)
    expect(await successOutcome).toStrictEqual({ rejected: false })
    await drainFixedPoint()
    const successfulSnapshot = store.getSnapshot(workspace)
    expect(successfulSnapshot).toMatchObject({
      actorId: actor, authGeneration: generation, workspaceId: workspace,
      writable: true, provenance: 'saved', state: 'closed-success', pending: false, readPending: false,
      layout: committed.layout, openedBaseline: null, draft: null, recovery: null,
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(successfulSnapshot.overlay).toStrictEqual({ [workspace]: committed.layout })
    expect(captured[0].binding.isCurrent()).toBe(false) // Consumed request, not revoked owner.
    expect(store.getPersistenceStatus(workspace)).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: false, canCustomize: true, failureKind: null,
    })

    const failedTicket = binding.open()
    expect(failedTicket).not.toBe(successfulTicket)
    store.setDraft(failedTicket.sessionId, failedDraft.items)
    const failedClose = binding.close(failedTicket, true)
    const failureOutcome = observe(failedClose) // Handle original rejection before rejecting transport.
    await notificationTurn()
    expect(captured).toHaveLength(2)
    expect(captured[1].workspaceId).toBe(workspace)
    expect(captured[1].body).toStrictEqual(failedDraft)
    expect(captured[1].currentAtIssuance).toBe(true)
    expect(captured[1].binding.isCurrent()).toBe(true)
    expect(binding.close(failedTicket, true)).toBe(failedClose)
    // Observe the original store task ONLY after the real native close issued it.
    const storeCompletion = store.finalize(failedTicket.sessionId)
    const storeOutcome = observe(storeCompletion)
    expect(store.finalize(failedTicket.sessionId)).toBe(storeCompletion)
    failureReply.reject(networkFailure) // No HTTP409; no automatic recovery GET.
    const closeResult = await failureOutcome
    const storeResult = await storeOutcome
    expect(closeResult.rejected).toBe(true)
    expect(storeResult.rejected).toBe(true)
    if (closeResult.rejected) expect(closeResult.error).toBe(networkFailure)
    if (storeResult.rejected) expect(storeResult.error).toBe(networkFailure)
    await drainFixedPoint()
    expect(binding.close(failedTicket, false)).toBe(failedClose)
    expect(store.finalize(failedTicket.sessionId)).toBe(storeCompletion)
    expect(await observe(binding.close(failedTicket, true))).toStrictEqual({ rejected: true, error: networkFailure })

    const expectedRecovery: Recovery = freezeFixture({
      attemptId: 'dashboard-attempt-2', actorId: actor, authGeneration: generation,
      workspaceId: workspace, epoch: 2, baseline: committed.layout, submitted: failedDraft,
      category: 'unknown', phase: 'needs-recheck', recheck: null,
    })
    const failedSnapshot = store.getSnapshot(workspace)
    retainedSnapshot = failedSnapshot
    const expectedSnapshot = freezeFixture({
      actorId: actor, authGeneration: generation, workspaceId: workspace,
      provenance: 'saved', writable: false, state: 'unknown', pending: false, readPending: false,
      canUndo: false, layout: committed.layout, openedBaseline: committed.layout,
      draft: failedDraft, recovery: expectedRecovery, overlay: { [workspace]: committed.layout },
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expectFrozenPublicData(failedSnapshot, expectedSnapshot)
    expect(reads).toBe(2)
    expect(captured).toHaveLength(2)
    expect(observed.length).toBeGreaterThan(0)
    expect(observed[observed.length - 1]).toBe(failedSnapshot)
    expect(store.retain(workspace)).toBeNull()
    const capture = failedSnapshot.recovery
    const status = store.getPersistenceStatus(workspace)
    expect(status).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: true, canCustomize: false, failureKind: 'unknown',
    })
    const publicationsBefore = observed.length

    // Canonical predicted RED: assertion on missing native public readonly Review.
    const reviewRecovery = binding.reviewRecovery
    expect(typeof reviewRecovery, 'native PageBinding.reviewRecovery must expose readonly recovery Review').toBe('function')
    if (typeof reviewRecovery !== 'function') throw new Error('Missing native binding readonly Review')
    expect(typeof store.reviewRecovery, 'native SessionStore.reviewRecovery must own readonly recovery Review').toBe('function')
    const expectedReview: ReadonlyReview = freezeFixture({
      ticket: {
        attemptId: expectedRecovery.attemptId, actorId: actor, authGeneration: generation,
        workspaceId: workspace, epoch: 2, readId: null,
      },
      recovery: expectedRecovery,
      permissions: { recheck: true, reapply: false, retry: false, discard: true },
    })
    expectFrozenPublicData(reviewRecovery.call(binding, expectedRecovery.attemptId), expectedReview)
    // Multiple reviews may mint distinct registered tickets; no cache/reference claim.
    expectFrozenPublicData(reviewRecovery.call(binding, expectedRecovery.attemptId), expectedReview)
    expect(() => binding.open()).toThrow()
    expect(() => store.setDraft(failedTicket.sessionId, firstDraft.items)).toThrow('Dashboard layout session is not editable')
    expect(binding.close(failedTicket, true)).toBe(failedClose)
    expect(store.finalize(failedTicket.sessionId)).toBe(storeCompletion)
    expect(() => reviewRecovery.call(binding, 'never-issued-review-attempt')).toThrow()
    selectedWorkspace = foreignWorkspace
    expect(() => reviewRecovery.call(binding, expectedRecovery.attemptId)).toThrow()
    selectedWorkspace = workspace
    tokenIdentity = null
    expect(() => reviewRecovery.call(binding, expectedRecovery.attemptId)).toThrow()
    tokenIdentity = originalToken

    // Exact issuing handle matters even when a genuine other holder survives.
    const remaining = bindTracked(workspace)
    const remainingReview = remaining.reviewRecovery
    expect(typeof remainingReview).toBe('function')
    if (typeof remainingReview !== 'function') throw new Error('Missing surviving binding readonly Review')
    releaseTracked(binding)
    expect(() => reviewRecovery.call(binding, expectedRecovery.attemptId)).toThrow()
    expectFrozenPublicData(remainingReview.call(remaining, expectedRecovery.attemptId), expectedReview)
    // Genuine zero-holder gap: no phantom observer holder; readonly reacquisition is allowed.
    releaseTracked(remaining)
    const rebound = bindTracked(workspace)
    const reboundReview = rebound.reviewRecovery
    expect(typeof reboundReview).toBe('function')
    if (typeof reboundReview !== 'function') throw new Error('Missing reacquired binding readonly Review')
    expect(() => remainingReview.call(remaining, expectedRecovery.attemptId)).toThrow()
    expectFrozenPublicData(reboundReview.call(rebound, expectedRecovery.attemptId), expectedReview)
    await drainFixedPoint()
    expect(store.getSnapshot(workspace)).toBe(failedSnapshot)
    expectFrozenPublicData(store.getSnapshot(workspace), expectedSnapshot)
    expect(store.getSnapshot(workspace).recovery).toBe(capture)
    expect(store.getPersistenceStatus(workspace)).toBe(status)
    expect(observed).toHaveLength(publicationsBefore)
    expect(reads).toBe(2)
    expect(captured).toHaveLength(2)
  } catch (error: unknown) {
    primaryFailed = true
    primaryError = error
  } finally {
    const beforeCleanup = { reads, writes: captured.length, publications: observed.length }
    selectedWorkspace = workspace
    tokenIdentity = originalToken
    // Settle EVERY controlled port, including ports whose issuance was cut short by an assertion.
    initialReply.resolve(initialUser)
    successReply.resolve(committed)
    postReply.resolve(postUser)
    failureReply.reject(networkFailure)
    // A never-issued failure transport still gets a rejection handler.
    void failureReply.promise.catch(() => {})
    for (const handle of handles) {
      try { releaseTracked(handle.binding) } catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drainFixedPoint() } catch (error: unknown) { cleanupErrors.push(error) }
    try {
      expect({ reads, writes: captured.length, publications: observed.length }).toStrictEqual(beforeCleanup)
      expect(reads).toBe(2)
      expect(captured).toHaveLength(2)
      if (retainedSnapshot !== null) {
        expect(store.getSnapshot(workspace)).toBe(retainedSnapshot)
        expect(store.getSnapshot(workspace).recovery).toBe(retainedSnapshot.recovery)
      }
      expect(fixtures).toStrictEqual(fixturesBefore)
      expect(authSeed.preferences).toBe(seedPreferences)
      expect(DASHBOARD_WIDGETS).toStrictEqual(registryBefore)
    } catch (error: unknown) { cleanupErrors.push(error) }
    unsubscribe()
  }
  // No throw inside finally; preserve the primary failure and separate cleanup diagnostics.
  if (primaryFailed) {
    if (cleanupErrors.length > 0) throw new AggregateError([primaryError, ...cleanupErrors], 'Primary native Review failure with separate cleanup diagnostics')
    throw primaryError
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'Native Review cleanup failed')
})
