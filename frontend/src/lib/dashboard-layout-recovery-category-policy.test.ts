import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease, type DialogTicket, type PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, type Recovery, type RecoveryReview, type SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// TEST_AUTHOR_ONLY / NOT_RUN. One category-policy vertical, no replay/lifecycle matrix.
// Only constructor transport/identity seams are substituted; core and lease are real.
const ACTOR = '33333333-3333-4333-8333-333333333333'
const WORKSPACE = '11111111-1111-4111-8111-111111111111'
const GENERATION = 7

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
  return { promise, resolve, reject }
}
function turn(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

// Independently literal RAW GETs and strict PUT envelope. No request echo, no
// production normalizer, and no golden layout inserted into a transport response.
const TEST_DATA = freeze({
  initial: {
    id: ACTOR, email: 'native-category-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Category policy tracer', onboarding_completed: true,
      dashboard_layouts: { [WORKSPACE]: {
        version: 1, workspaceId: WORKSPACE,
        items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      } },
    },
  } satisfies User & { preferences: { timezone: string } },
  positiveCommit: {
    workspaceId: WORKSPACE,
    layout: {
      version: 1, workspaceId: WORKSPACE,
      items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  } satisfies CommittedDashboardLayout,
  positiveRead: {
    id: ACTOR, email: 'native-category-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Category policy tracer', onboarding_completed: true,
      dashboard_layouts: { [WORKSPACE]: {
        version: 1, workspaceId: WORKSPACE,
        items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: false, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      } },
    },
  } satisfies User & { preferences: { timezone: string } },
  differentRead: {
    id: ACTOR, email: 'native-category-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Category policy tracer', onboarding_completed: true,
      dashboard_layouts: { [WORKSPACE]: {
        version: 1, workspaceId: WORKSPACE,
        items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: false, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      } },
    },
  } satisfies User & { preferences: { timezone: string } },
  matchedRead: {
    id: ACTOR, email: 'native-category-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Category policy tracer', onboarding_completed: true,
      dashboard_layouts: { [WORKSPACE]: {
        version: 1, workspaceId: WORKSPACE,
        items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: false, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: false, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      } },
    },
  } satisfies User & { preferences: { timezone: string } },
  futureRead: {
    id: ACTOR, email: 'native-category-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Category policy tracer', onboarding_completed: true,
      dashboard_layouts: { [WORKSPACE]: {
        version: 2, workspaceId: WORKSPACE,
        items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: false, order: 1 },
          { id: 'spending-charts', visible: true, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: false, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      } },
    },
  },
  displayRead: {
    id: ACTOR, email: 'native-category-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'Category policy tracer', onboarding_completed: true,
      dashboard_layouts: { [WORKSPACE]: {
        version: 1, workspaceId: WORKSPACE,
        items: [
          { id: 'hero-balance', visible: true, order: 0 },
          { id: 'uncategorized-banner', visible: true, order: 1 },
          { id: 'spending-charts', visible: false, order: 2 },
          { id: 'balance-evolution', visible: true, order: 3 },
          { id: 'goals-progress', visible: true, order: 4 },
          { id: 'period-transactions', visible: true, order: 5 },
        ],
      } },
    },
  } satisfies User & { preferences: { timezone: string } },
})

// Separate literal expected/editor layouts. Deliberate duplication keeps the
// response oracle independent of submitted args and of the production resolver.
const GOLDEN = freeze({
  initial: {
    version: 1, workspaceId: WORKSPACE,
    items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } satisfies DashboardLayout,
  positive: {
    version: 1, workspaceId: WORKSPACE,
    items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } satisfies DashboardLayout,
  submitted: {
    version: 1, workspaceId: WORKSPACE,
    items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: false, order: 1 },
      { id: 'spending-charts', visible: true, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } satisfies DashboardLayout,
  different: {
    version: 1, workspaceId: WORKSPACE,
    items: [
      { id: 'hero-balance', visible: true, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } satisfies DashboardLayout,
  newUnsent: {
    version: 1, workspaceId: WORKSPACE,
    items: [
      { id: 'hero-balance', visible: false, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: false, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } satisfies DashboardLayout,
})

type PolicyCase = {
  name: string
  status: number | null
  category: Recovery['category']
  target: 'different' | 'matched' | 'future' | 'unqualified'
}
const CASES: readonly PolicyCase[] = freeze([
  { name: '403 blocked: qualified compare/discard, never replacement', status: 403, category: 'blocked', target: 'different' },
  { name: '404 blocked: qualified compare/discard, never replacement', status: 404, category: 'blocked', target: 'different' },
  { name: '422 correction-required: qualified compare/discard, never replacement', status: 422, category: 'correction-required', target: 'different' },
  { name: 'unknown matched: comparison is not success or retry permission', status: null, category: 'unknown', target: 'matched' },
  { name: 'unknown future RAW: protected target, local discard then explicit display refresh', status: null, category: 'unknown', target: 'future' },
  { name: '403 unqualified: local discard then explicit display refresh', status: 403, category: 'blocked', target: 'unqualified' },
])

it.each(CASES)('native non-replay category policy / $name', async (policy) => {
  const dataBefore = freeze(structuredClone(TEST_DATA))
  const goldenBefore = freeze(structuredClone(GOLDEN))
  const registryBefore = freeze(structuredClone(DASHBOARD_WIDGETS))
  const authSeed: User = freeze(structuredClone(TEST_DATA.initial))
  const seedPreferences = authSeed.preferences
  const originalError = policy.status === null
    ? Object.freeze(new Error('Independent category-policy original unknown save failure'))
    : Object.freeze(Object.assign(new Error('Independent category-policy original HTTP failure'), {
      response: Object.freeze({ status: policy.status }),
    }))
  const expectedCounters = freeze({
    success: 1, suppressed: 0, conflict409: 0, other4xx: policy.status === null ? 0 : 1, server5xx: 0,
  })
  const initial = deferred<User>()
  const positive = deferred<CommittedDashboardLayout>()
  const positiveRead = deferred<User>()
  const failed = deferred<CommittedDashboardLayout>()
  const firstReview = deferred<User>()
  const secondReview = deferred<User>()
  const displayRead = deferred<User>()
  // RAW future data is intentionally outside User's version-1 static type;
  // this single cast crosses the constructor GET seam, not the strict validator.
  const reviewedRaw: User = policy.target === 'future' ? TEST_DATA.futureRead as unknown as User
    : policy.target === 'matched' ? TEST_DATA.matchedRead : TEST_DATA.differentRead
  void failed.promise.catch(() => {})
  let reads = 0
  const writes: Array<{ workspaceId: string; body: DashboardLayout; binding: DashboardRequestBinding; current: boolean }> = []
  const issued: Array<Promise<Outcome>> = []
  function observe(task: Promise<void>): Promise<Outcome> {
    const outcome = task.then<Outcome, Outcome>(
      () => ({ rejected: false }), (error: unknown) => ({ rejected: true, error }),
    )
    issued.push(outcome)
    return outcome
  }
  const store = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: GENERATION, authSeed,
    getTokenIdentity: () => 'synthetic-category-policy-token', getWorkspaceId: () => WORKSPACE,
    api: {
      me: () => {
        reads += 1
        if (reads === 1) return initial.promise
        if (reads === 2) return positiveRead.promise
        if (policy.target === 'unqualified' && reads === 3) return displayRead.promise
        if (policy.target !== 'unqualified' && reads === 3) return firstReview.promise
        if (policy.target !== 'unqualified' && reads === 4) return secondReview.promise
        if (policy.target === 'future' && reads === 5) return displayRead.promise
        return Promise.reject(new Error('Unexpected category-policy GET'))
      },
      saveLayout: (workspaceId, body, binding) => {
        // Record only: assertions stay outside transport classification catches.
        writes.push({ workspaceId, body: structuredClone(body), binding, current: binding.isCurrent() })
        if (writes.length === 1) return positive.promise
        if (writes.length === 2) return failed.promise
        return Promise.reject(new Error('Unexpected category-policy PUT'))
      },
    },
  })
  const lease = getPageSessionLease(store)
  let binding: PageBinding | null = null
  let openUnsent: DialogTicket | null = null
  let released = false
  const published: SessionSnapshot[] = []
  const unsubscribe = store.subscribe(() => { published.push(store.getSnapshot(WORKSPACE)) })
  async function drain(): Promise<void> {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const before = { reads, writes: writes.length, publications: published.length }
      await lease.whenIdle()
      await store.whenIdle()
      await Promise.all(issued)
      await turn()
      await turn()
      if (before.reads === reads && before.writes === writes.length && before.publications === published.length) return
    }
    throw new Error('Category-policy native task/publication fixed point did not settle')
  }
  function expectIO(expectedReads: number): void {
    expect(reads).toBe(expectedReads)
    expect(writes).toHaveLength(2)
    expect(store.getSnapshot(WORKSPACE).counters).toStrictEqual(expectedCounters)
  }
  async function expectOriginal(task: Promise<void>): Promise<void> {
    const result = await observe(task)
    expect(result).toStrictEqual({ rejected: true, error: originalError })
    if (result.rejected) expect(result.error).toBe(originalError)
  }
  let primaryFailed = false
  let primaryError: unknown
  const cleanupErrors: unknown[] = []
  try {
    const initialTask = observe(store.refresh(WORKSPACE))
    initial.resolve(TEST_DATA.initial)
    expect(await initialTask).toStrictEqual({ rejected: false })
    await drain()
    expect(reads).toBe(1)
    expect(writes).toHaveLength(0)
    expect(store.getSnapshot(WORKSPACE).layout).toStrictEqual(GOLDEN.initial)
    expect(store.getPersistenceStatus(WORKSPACE).canCustomize).toBe(true)
    lease.select(WORKSPACE)
    binding = lease.bind(WORKSPACE) // The only page holder; subscribe adds no lease.
    const positiveTicket = binding.open()
    openUnsent = positiveTicket
    store.setDraft(positiveTicket.sessionId, GOLDEN.positive.items)
    const positiveClose = observe(binding.close(positiveTicket, true))
    openUnsent = null
    await turn()
    expect(writes).toHaveLength(1)
    expect(writes[0]).toMatchObject({ workspaceId: WORKSPACE, body: GOLDEN.positive, current: true })
    expect(writes[0].binding.isCurrent()).toBe(true)
    positive.resolve(TEST_DATA.positiveCommit)
    await turn()
    await turn()
    expect(reads).toBe(2)
    positiveRead.resolve(TEST_DATA.positiveRead)
    expect(await positiveClose).toStrictEqual({ rejected: false })
    await drain()
    expect(store.getSnapshot(WORKSPACE)).toMatchObject({
      state: 'closed-success', writable: true, pending: false, readPending: false,
      layout: GOLDEN.positive, recovery: null,
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(store.getSnapshot(WORKSPACE).overlay).toStrictEqual({ [WORKSPACE]: GOLDEN.positive })
    expect(store.getPersistenceStatus(WORKSPACE).canCustomize).toBe(true)
    expect(writes[0].binding.isCurrent()).toBe(false) // Native success consumed the request, not the owner.

    const oldEditor = binding.open()
    openUnsent = oldEditor
    store.setDraft(oldEditor.sessionId, GOLDEN.submitted.items)
    const oldClose = binding.close(oldEditor, true)
    const oldCloseOutcome = observe(oldClose)
    openUnsent = null
    await turn()
    expect(writes).toHaveLength(2)
    expect(writes[1]).toMatchObject({ workspaceId: WORKSPACE, body: GOLDEN.submitted, current: true })
    expect(writes[1].binding.isCurrent()).toBe(true)
    const oldCompletion = store.finalize(oldEditor.sessionId)
    const oldCompletionOutcome = observe(oldCompletion)
    expect(store.finalize(oldEditor.sessionId)).toBe(oldCompletion)
    expect(binding.close(oldEditor, false)).toBe(oldClose)
    failed.reject(originalError)
    expect(await oldCloseOutcome).toStrictEqual({ rejected: true, error: originalError })
    expect(await oldCompletionOutcome).toStrictEqual({ rejected: true, error: originalError })
    await expectOriginal(oldClose)
    await expectOriginal(oldCompletion)
    await drain()
    const expectedCapture: Recovery = freeze({
      attemptId: 'dashboard-attempt-2', actorId: ACTOR, authGeneration: GENERATION,
      workspaceId: WORKSPACE, epoch: 2, baseline: GOLDEN.positive, submitted: GOLDEN.submitted,
      category: policy.category, phase: 'needs-recheck', recheck: null,
    })
    const failureSnapshot = store.getSnapshot(WORKSPACE)
    expect(failureSnapshot).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: WORKSPACE, provenance: 'saved',
      state: policy.category === 'blocked' ? 'blocked' : 'unknown', writable: false,
      pending: false, readPending: false, canUndo: false, layout: GOLDEN.positive,
      openedBaseline: GOLDEN.positive, draft: GOLDEN.submitted, recovery: expectedCapture,
      overlay: { [WORKSPACE]: GOLDEN.positive }, counters: expectedCounters,
    })
    const capture = failureSnapshot.recovery
    if (!capture) throw new Error('Missing authentic completed retained category-policy failure')
    const captureBefore = freeze(structuredClone(capture))
    const failureStatus = store.getPersistenceStatus(WORKSPACE)
    expect(failureStatus).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: true, canCustomize: false, failureKind: policy.category,
    })
    expectIO(2) // None of these categories is 409: no automatic GET.
    function expectOldUneditable(): void {
      expect(store.retain(WORKSPACE)).toBeNull()
      expect(() => binding!.open()).toThrow()
      expect(() => store.setDraft(oldEditor.sessionId, GOLDEN.positive.items)).toThrow('Dashboard layout session is not editable')
      expect(binding!.close(oldEditor, true)).toBe(oldClose)
    }
    expectOldUneditable()
    const readonlyStart = published.length
    let review: RecoveryReview = binding.reviewRecovery(capture.attemptId)
    const originalReview = review
    expectFrozen(review)
    expect(review.recovery).toBe(capture)
    expect(review.permissions).toStrictEqual({ recheck: true, reapply: false, retry: false, discard: true })
    await drain()
    expect(store.getSnapshot(WORKSPACE)).toBe(failureSnapshot)
    expect(store.getPersistenceStatus(WORKSPACE)).toBe(failureStatus)
    expect(published).toHaveLength(readonlyStart)
    expectIO(2)

    if (policy.target !== 'unqualified') {
      // A fresh post-read ticket can issue another read for EVERY retained
      // category. Two finite controlled reads also distinguish exact initiator
      // dedup from a ticket minted during occupancy; no extra observer holder.
      for (const step of [
        { reply: firstReview, readId: 6, reads: 3 },
        { reply: secondReview, readId: 7, reads: 4 },
      ]) {
        const initiating = review
        const readStart = published.length
        const task = binding.recheckReviewedRecovery(initiating.ticket)
        const outcome = observe(task)
        let settled = false
        void outcome.then(() => { settled = true })
        await turn()
        await turn()
        const held = store.getSnapshot(WORKSPACE)
        expect(held).toStrictEqual({
          ...failureSnapshot, readPending: true,
          recovery: { ...expectedCapture, phase: 'rechecking', recheck: null },
        })
        expect(held.recovery?.baseline).toBe(capture.baseline)
        expect(held.recovery?.submitted).toBe(capture.submitted)
        expect(store.getPersistenceStatus(WORKSPACE)).toStrictEqual({ ...failureStatus, readPending: true })
        expect(settled).toBe(false)
        expectIO(step.reads)
        expect(published.slice(readStart)).toStrictEqual([held])
        const heldStatus = store.getPersistenceStatus(WORKSPACE)
        const occupiedStart = published.length
        expect(binding.recheckReviewedRecovery(initiating.ticket)).toBe(task)
        const occupiedReview = binding.reviewRecovery(capture.attemptId)
        expectFrozen(occupiedReview)
        expect(occupiedReview.permissions).toStrictEqual({ recheck: false, reapply: false, retry: false, discard: false })
        expect(await observe(binding.recheckReviewedRecovery(occupiedReview.ticket))).toMatchObject({ rejected: true })
        expect(await observe(binding.replaceRecovery(occupiedReview.ticket, 'retry'))).toMatchObject({ rejected: true })
        expect(binding.discardRecovery(occupiedReview.ticket)).toBeUndefined()
        expectOldUneditable()
        await turn()
        await turn()
        expect(binding.recheckReviewedRecovery(initiating.ticket)).toBe(task)
        expect(store.getSnapshot(WORKSPACE)).toBe(held)
        expect(store.getPersistenceStatus(WORKSPACE)).toBe(heldStatus)
        expect(published).toHaveLength(occupiedStart)
        expectIO(step.reads)
        const settleStart = published.length
        step.reply.resolve(reviewedRaw)
        expect(await outcome).toStrictEqual({ rejected: false })
        await drain()
        const ready = store.getSnapshot(WORKSPACE)
        const target = policy.target === 'matched' ? GOLDEN.submitted : GOLDEN.different
        expect(ready).toStrictEqual({
          ...failureSnapshot,
          recovery: {
            ...expectedCapture,
            phase: policy.target === 'future' ? 'protected' : policy.target === 'matched' ? 'matched' : 'review-ready',
            recheck: policy.target === 'future' ? null : {
              readId: step.readId, provenance: 'saved', target, matchesSubmitted: policy.target === 'matched',
            },
          },
        })
        expect(ready.recovery?.baseline).toBe(capture.baseline)
        expect(ready.recovery?.submitted).toBe(capture.submitted)
        expect(ready.layout).toStrictEqual(GOLDEN.positive) // Comparison does not alter display or commit outcome.
        expect(published.slice(settleStart)).toStrictEqual([ready])
        expect(store.getPersistenceStatus(WORKSPACE)).toStrictEqual(failureStatus)
        expectIO(step.reads)
        const readyStatus = store.getPersistenceStatus(WORKSPACE)
        const readyStart = published.length
        review = binding.reviewRecovery(capture.attemptId)
        expectFrozen(review)
        expect(review.recovery).toBe(ready.recovery)
        expect(review.permissions).toStrictEqual({ recheck: true, reapply: false, retry: false, discard: true })
        expect(review.ticket.readId).toBe(policy.target === 'future' ? null : step.readId)
        expect(await observe(binding.recheckReviewedRecovery(initiating.ticket))).toMatchObject({ rejected: true })
        expect(await observe(binding.recheckReviewedRecovery(occupiedReview.ticket))).toMatchObject({ rejected: true })
        expect(binding.discardRecovery(initiating.ticket)).toBeUndefined()
        expectOldUneditable()
        await expectOriginal(oldCompletion)
        await drain()
        expect(store.getSnapshot(WORKSPACE)).toBe(ready)
        expect(store.getPersistenceStatus(WORKSPACE)).toBe(readyStatus)
        expect(published).toHaveLength(readyStart)
        expectIO(step.reads)
      }
    }

    const beforeDiscard = store.getSnapshot(WORKSPACE)
    const beforeDiscardStatus = store.getPersistenceStatus(WORKSPACE)
    const guardsStart = published.length
    const readsBeforeDiscard = policy.target === 'unqualified' ? 2 : 4
    // All bounded categories deny BOTH real replacement actions. Denial must
    // preserve this exact valid discard ticket, not require ticket reissuance.
    expect(await observe(binding.replaceRecovery(review.ticket, 'retry'))).toMatchObject({ rejected: true })
    expect(await observe(binding.replaceRecovery(review.ticket, 'reapply'))).toMatchObject({ rejected: true })
    await drain()
    expect(store.getSnapshot(WORKSPACE)).toBe(beforeDiscard)
    expect(store.getPersistenceStatus(WORKSPACE)).toBe(beforeDiscardStatus)
    expect(published).toHaveLength(guardsStart)
    expectIO(readsBeforeDiscard)
    expect(review.permissions.discard).toBe(true)
    const discardStart = published.length
    expect(binding.discardRecovery(review.ticket)).toBeUndefined()
    const requiresRefresh = policy.target === 'future' || policy.target === 'unqualified'
    const promoted = policy.target === 'matched' ? GOLDEN.submitted : GOLDEN.different
    const discarded = store.getSnapshot(WORKSPACE)
    expect(discarded).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: WORKSPACE, provenance: 'saved',
      state: 'closed', writable: !requiresRefresh, pending: false, readPending: false, canUndo: false,
      layout: requiresRefresh ? GOLDEN.positive : promoted,
      openedBaseline: null, draft: null, recovery: null,
      overlay: requiresRefresh ? { [WORKSPACE]: GOLDEN.positive } : {}, counters: expectedCounters,
    })
    expect(discarded.overlay).toStrictEqual(requiresRefresh ? { [WORKSPACE]: GOLDEN.positive } : {})
    expect(store.getPersistenceStatus(WORKSPACE)).toStrictEqual({
      pending: false, readPending: false, stale: requiresRefresh, unsaved: false,
      canCustomize: !requiresRefresh, failureKind: null,
    })
    expectFrozen(discarded)
    await drain()
    expect(published.slice(discardStart)).toStrictEqual([discarded])
    expectIO(readsBeforeDiscard) // LOCAL consumption: zero GET/PUT or counter transitions.
    const discardedStatus = store.getPersistenceStatus(WORKSPACE)
    const inertStart = published.length
    expect(binding.close(oldEditor, false)).toBe(oldClose)
    await expectOriginal(oldClose)
    await expectOriginal(oldCompletion) // Captured completion, never relookup the removed session.
    expect(() => store.setDraft(oldEditor.sessionId, GOLDEN.positive.items)).toThrow('Dashboard layout session is not editable')
    expect(() => binding!.reviewRecovery(capture.attemptId)).toThrow()
    for (const stale of [originalReview, review]) {
      expect(binding.discardRecovery(stale.ticket)).toBeUndefined()
      expect(await observe(binding.replaceRecovery(stale.ticket, 'retry'))).toMatchObject({ rejected: true })
      expect(await observe(binding.recheckReviewedRecovery(stale.ticket))).toMatchObject({ rejected: true })
    }
    if (requiresRefresh) expect(() => binding!.open()).toThrow()
    await drain()
    expect(store.getSnapshot(WORKSPACE)).toBe(discarded)
    expect(store.getPersistenceStatus(WORKSPACE)).toBe(discardedStatus)
    expect(published).toHaveLength(inertStart)
    expectIO(readsBeforeDiscard)

    let editorBaseline = promoted
    let finalReads = readsBeforeDiscard
    if (requiresRefresh) {
      const refreshStart = published.length
      const refresh = binding.refreshDisplay() // Explicit native display port; never reviewed Recheck.
      const refreshOutcome = observe(refresh)
      finalReads = policy.target === 'future' ? 5 : 3
      await turn()
      await turn()
      const refreshing = store.getSnapshot(WORKSPACE)
      expect(refreshing).toStrictEqual({ ...discarded, readPending: true })
      expect(store.getPersistenceStatus(WORKSPACE)).toStrictEqual({ ...discardedStatus, readPending: true })
      expect(published.slice(refreshStart)).toStrictEqual([refreshing])
      expectIO(finalReads)
      expect(binding.refreshDisplay()).toBe(refresh)
      expect(() => binding!.open()).toThrow()
      const settleStart = published.length
      displayRead.resolve(TEST_DATA.displayRead)
      expect(await refreshOutcome).toStrictEqual({ rejected: false })
      await drain()
      const refreshed = store.getSnapshot(WORKSPACE)
      expect(refreshed).toStrictEqual({ ...discarded, writable: true, layout: GOLDEN.different })
      // Ordinary GET establishes display/edit authority, not a fabricated PUT.
      expect(refreshed.overlay).toStrictEqual({ [WORKSPACE]: GOLDEN.positive })
      expect(store.getPersistenceStatus(WORKSPACE)).toStrictEqual({
        pending: false, readPending: false, stale: false, unsaved: false, canCustomize: true, failureKind: null,
      })
      expect(published.slice(settleStart)).toStrictEqual([refreshed])
      expectIO(finalReads)
      editorBaseline = GOLDEN.different
    }

    // Genuine fresh changed UNSENT ordinary editor proves discard/refresh did
    // not blanket-disable admission. Close(false), not finalize: zero new PUT.
    const beforeEditor = store.getSnapshot(WORKSPACE)
    const freshTicket = binding.open()
    openUnsent = freshTicket
    expect(freshTicket.sessionId).not.toBe(oldEditor.sessionId)
    expect(store.getSnapshot(WORKSPACE).openedBaseline).toStrictEqual(editorBaseline)
    store.setDraft(freshTicket.sessionId, GOLDEN.newUnsent.items)
    await drain()
    const editing = store.getSnapshot(WORKSPACE)
    expect(editing.draft).toStrictEqual(GOLDEN.newUnsent)
    expect(editing.draft).not.toStrictEqual(editorBaseline)
    expect(store.getPersistenceStatus(WORKSPACE).unsaved).toBe(true)
    const editingStatus = store.getPersistenceStatus(WORKSPACE)
    const editingStart = published.length
    expect(binding.discardRecovery(review.ticket)).toBeUndefined()
    expect(await observe(binding.replaceRecovery(review.ticket, 'reapply'))).toMatchObject({ rejected: true })
    expect(await observe(binding.recheckReviewedRecovery(review.ticket))).toMatchObject({ rejected: true })
    expect(() => store.setDraft(oldEditor.sessionId, GOLDEN.positive.items)).toThrow('Dashboard layout session is not editable')
    await expectOriginal(oldClose)
    await expectOriginal(oldCompletion)
    await drain()
    expect(store.getSnapshot(WORKSPACE)).toBe(editing)
    expect(store.getPersistenceStatus(WORKSPACE)).toBe(editingStatus)
    expect(published).toHaveLength(editingStart)
    expectIO(finalReads)
    const closeStart = published.length
    expect(await observe(binding.close(freshTicket, false))).toStrictEqual({ rejected: false })
    openUnsent = null
    await drain()
    const closed = store.getSnapshot(WORKSPACE)
    expect(closed).toStrictEqual(beforeEditor)
    expect(published.slice(closeStart)).toStrictEqual([closed])
    expectIO(finalReads)
    expect(capture).toStrictEqual(captureBefore)
    expect(capture.baseline).toBe(originalReview.recovery.baseline)
    expect(capture.submitted).toBe(originalReview.recovery.submitted)
    expectFrozen(originalReview)
    expectFrozen(review)
    await expectOriginal(oldClose)
    await expectOriginal(oldCompletion)
  } catch (error: unknown) {
    primaryFailed = true
    primaryError = error
  } finally {
    // Settle EVERY finite input before draining; do not hide the body failure
    // with a finally throw, and never let release save an unsent failed oracle.
    initial.resolve(TEST_DATA.initial)
    positive.resolve(TEST_DATA.positiveCommit)
    positiveRead.resolve(TEST_DATA.positiveRead)
    failed.reject(originalError)
    firstReview.resolve(reviewedRaw)
    secondReview.resolve(reviewedRaw)
    displayRead.resolve(TEST_DATA.displayRead)
    if (binding && openUnsent) {
      try { await observe(binding.close(openUnsent, false)) }
      catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drain() } catch (error: unknown) { cleanupErrors.push(error) }
    const beforeRelease = { reads, writes: writes.length }
    if (binding) {
      try { binding.release(); released = true }
      catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drain() } catch (error: unknown) { cleanupErrors.push(error) }
    try {
      expect(reads).toBe(beforeRelease.reads)
      expect(writes).toHaveLength(beforeRelease.writes)
      if (binding) expect(released).toBe(true)
      expect(store.getSnapshot(WORKSPACE).pending).toBe(false)
      expect(store.getSnapshot(WORKSPACE).readPending).toBe(false)
      expect(TEST_DATA).toStrictEqual(dataBefore)
      expect(GOLDEN).toStrictEqual(goldenBefore)
      expect(authSeed.preferences).toBe(seedPreferences)
      expect(DASHBOARD_WIDGETS).toStrictEqual(registryBefore)
    } catch (error: unknown) { cleanupErrors.push(error) }
    unsubscribe()
  }
  if (primaryFailed) {
    if (cleanupErrors.length > 0) throw new AggregateError([primaryError, ...cleanupErrors], 'Primary category-policy failure with separate cleanup diagnostics')
    throw primaryError
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'Category-policy cleanup failed')
})
