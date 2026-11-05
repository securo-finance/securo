import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease, type DialogTicket, type PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, type Recovery, type SessionSnapshot } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// AUTHOR_ONLY / NOT_RUN: ONE HTTP400 policy vertical, not a status matrix.
// Source fit: createDashboardLayoutSession owns strict PUT response validation,
// retained original completion/capture and counters. select + bind + open/close
// mint the real native handle/continuity witnesses; review/recheck/replace/discard
// are public lease ports, not fabricated permissions or a synthetic RecoveryView.
// Constructor transport + stable identity seams only: NOT SDK/hook/App/browser
// proof. No PATCH, Auth setter, storage/global mutation or private-state port.
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

// Independent literal RAW GET users and strict PUT envelopes. No transport
// response echoes submitted args or uses a production normalizer/golden layout.
const TEST_DATA = freeze({
  initial: {
    id: ACTOR, email: 'native-http400-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'HTTP400 policy tracer', onboarding_completed: true,
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
    id: ACTOR, email: 'native-http400-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'HTTP400 policy tracer', onboarding_completed: true,
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
  reviewedRead: {
    id: ACTOR, email: 'native-http400-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'HTTP400 policy tracer', onboarding_completed: true,
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
  ordinaryRead: {
    id: ACTOR, email: 'native-http400-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'HTTP400 policy tracer', onboarding_completed: true,
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
  correctedCommit: {
    workspaceId: WORKSPACE,
    layout: {
      version: 1, workspaceId: WORKSPACE,
      items: [
        { id: 'hero-balance', visible: false, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: false, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: true, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  } satisfies CommittedDashboardLayout,
  correctedRead: {
    id: ACTOR, email: 'native-http400-policy@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC', currency_display: 'symbol',
      display_name: 'HTTP400 policy tracer', onboarding_completed: true,
      dashboard_layouts: { [WORKSPACE]: {
        version: 1, workspaceId: WORKSPACE,
        items: [
          { id: 'hero-balance', visible: false, order: 0 },
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

// Separately fixed editor/expected layouts: deliberate duplication, never fed
// into a GET/PUT reply. The corrected edit is NOT the retained failed submission.
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
  reviewed: {
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
  corrected: {
    version: 1, workspaceId: WORKSPACE,
    items: [
      { id: 'hero-balance', visible: false, order: 0 },
      { id: 'uncategorized-banner', visible: true, order: 1 },
      { id: 'spending-charts', visible: false, order: 2 },
      { id: 'balance-evolution', visible: true, order: 3 },
      { id: 'goals-progress', visible: true, order: 4 },
      { id: 'period-transactions', visible: true, order: 5 },
    ],
  } satisfies DashboardLayout,
})

it('native HTTP400 requires correction, never replay authority after qualified comparison, but permits a fresh corrected save', async () => {
  const dataBefore = freeze(structuredClone(TEST_DATA))
  const goldenBefore = freeze(structuredClone(GOLDEN))
  const registryBefore = freeze(structuredClone(DASHBOARD_WIDGETS))
  const authSeed: User = freeze(structuredClone(TEST_DATA.initial))
  const seedBefore = freeze(structuredClone(authSeed))
  const seedPreferences = authSeed.preferences
  const tokenIdentity = 'synthetic-http400-constructor-identity'
  const originalError = Object.freeze(Object.assign(new Error('Independent native HTTP400 rejection'), {
    response: Object.freeze({ status: 400 }),
  }))
  const countersAfterFailure = freeze({ success: 1, suppressed: 0, conflict409: 0, other4xx: 1, server5xx: 0 })
  const initial = deferred<User>()
  const positive = deferred<CommittedDashboardLayout>()
  const positiveRead = deferred<User>()
  const failed = deferred<CommittedDashboardLayout>()
  const reviewedRead = deferred<User>()
  const ordinaryRead = deferred<User>()
  const corrected = deferred<CommittedDashboardLayout>()
  const correctedRead = deferred<User>()
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
    getTokenIdentity: () => tokenIdentity, getWorkspaceId: () => WORKSPACE,
    api: {
      me: () => {
        reads += 1
        if (reads === 1) return initial.promise
        if (reads === 2) return positiveRead.promise
        if (reads === 3) return reviewedRead.promise
        if (reads === 4) return ordinaryRead.promise
        if (reads === 5) return correctedRead.promise
        return Promise.reject(new Error('Unexpected HTTP400-policy GET'))
      },
      saveLayout: (workspaceId, body, binding) => {
        // Record only; assertions cannot be swallowed by transport classification.
        writes.push({ workspaceId, body: structuredClone(body), binding, current: binding.isCurrent() })
        if (writes.length === 1) return positive.promise
        if (writes.length === 2) return failed.promise
        if (writes.length === 3) return corrected.promise
        return Promise.reject(new Error('Unexpected HTTP400-policy PUT'))
      },
    },
  })
  const lease = getPageSessionLease(store)
  let binding: PageBinding | null = null
  let unsent: DialogTicket | null = null
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
    throw new Error('HTTP400 native task/publication fixed point did not settle')
  }
  async function expectOriginal(task: Promise<void>): Promise<void> {
    const result = await observe(task)
    expect(result).toStrictEqual({ rejected: true, error: originalError })
    if (result.rejected) expect(result.error).toBe(originalError)
  }
  function expectFailureIO(expectedReads: number): void {
    expect(reads).toBe(expectedReads)
    expect(writes).toHaveLength(2) // No replacement PUT; only the two ORIGINAL attempts.
    expect(store.getSnapshot(WORKSPACE).counters).toStrictEqual(countersAfterFailure)
  }
  let primaryFailed = false
  let primaryError: unknown
  const cleanupErrors: unknown[] = []
  try {
    const initialOutcome = observe(store.refresh(WORKSPACE))
    initial.resolve(TEST_DATA.initial)
    expect(await initialOutcome).toStrictEqual({ rejected: false })
    await drain()
    expect(reads).toBe(1)
    expect(writes).toHaveLength(0)
    expect(store.getSnapshot(WORKSPACE).layout).toStrictEqual(GOLDEN.initial)
    expect(store.getPersistenceStatus(WORKSPACE).canCustomize).toBe(true)
    lease.select(WORKSPACE)
    binding = lease.bind(WORKSPACE) // Sole genuine selected page holder.
    const positiveTicket = binding.open()
    unsent = positiveTicket
    store.setDraft(positiveTicket.sessionId, GOLDEN.positive.items)
    expect(store.getSnapshot(WORKSPACE).draft).not.toStrictEqual(GOLDEN.initial)
    const positiveOutcome = observe(binding.close(positiveTicket, true))
    unsent = null
    await turn()
    expect(writes).toHaveLength(1)
    expect(writes[0]).toMatchObject({ workspaceId: WORKSPACE, body: GOLDEN.positive, current: true })
    expect(writes[0].binding.isCurrent()).toBe(true)
    positive.resolve(TEST_DATA.positiveCommit)
    await turn()
    await turn()
    expect(reads).toBe(2)
    positiveRead.resolve(TEST_DATA.positiveRead)
    expect(await positiveOutcome).toStrictEqual({ rejected: false })
    await drain()
    expect(store.getSnapshot(WORKSPACE)).toMatchObject({
      state: 'closed-success', writable: true, pending: false, readPending: false,
      layout: GOLDEN.positive, recovery: null,
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(store.getSnapshot(WORKSPACE).overlay).toStrictEqual({ [WORKSPACE]: GOLDEN.positive })
    expect(writes[0].binding.isCurrent()).toBe(false) // Successful request consumed, owner still eligible.
    expect(store.getPersistenceStatus(WORKSPACE).canCustomize).toBe(true)

    const oldEditor = binding.open()
    unsent = oldEditor
    store.setDraft(oldEditor.sessionId, GOLDEN.submitted.items)
    expect(store.getSnapshot(WORKSPACE).draft).not.toStrictEqual(GOLDEN.positive)
    const oldClose = binding.close(oldEditor, true)
    const oldOutcome = observe(oldClose)
    unsent = null
    await turn()
    expect(writes).toHaveLength(2)
    expect(writes[1]).toMatchObject({ workspaceId: WORKSPACE, body: GOLDEN.submitted, current: true })
    expect(writes[1].binding.isCurrent()).toBe(true)
    const oldCompletion = store.finalize(oldEditor.sessionId)
    const completionOutcome = observe(oldCompletion)
    expect(store.finalize(oldEditor.sessionId)).toBe(oldCompletion)
    expect(binding.close(oldEditor, false)).toBe(oldClose)
    failed.reject(originalError) // Actual Error + response.status at the allowed PUT seam.
    expect(await oldOutcome).toStrictEqual({ rejected: true, error: originalError })
    expect(await completionOutcome).toStrictEqual({ rejected: true, error: originalError })
    await expectOriginal(oldClose)
    await expectOriginal(oldCompletion)
    await drain()
    const failureSnapshot = store.getSnapshot(WORKSPACE)
    const capture = failureSnapshot.recovery
    if (!capture) throw new Error('Missing authentic retained HTTP400 failure')
    expect(capture).toMatchObject({
      attemptId: 'dashboard-attempt-2', actorId: ACTOR, authGeneration: GENERATION,
      workspaceId: WORKSPACE, epoch: 2, baseline: GOLDEN.positive, submitted: GOLDEN.submitted,
      phase: 'needs-recheck', recheck: null,
    })
    expectFailureIO(2) // HTTP400 is not 409: zero automatic review GETs.
    expectFrozen(capture)
    expect(store.retain(WORKSPACE)).toBeNull()
    expect(() => binding!.open()).toThrow()
    expect(() => store.setDraft(oldEditor.sessionId, GOLDEN.positive.items)).toThrow('Dashboard layout session is not editable')
    expect(writes[1].binding.isCurrent()).toBe(true)

    // FIRST discriminating future oracle. Pinned source currently retains
    // category='unknown' for HTTP400. Expected RED, NOT executed author evidence.
    expect(capture.category, 'HTTP400 forbids unchanged replay and requires correction').toBe('correction-required')

    const expectedCapture: Recovery = freeze({
      attemptId: 'dashboard-attempt-2', actorId: ACTOR, authGeneration: GENERATION,
      workspaceId: WORKSPACE, epoch: 2, baseline: GOLDEN.positive, submitted: GOLDEN.submitted,
      category: 'correction-required', phase: 'needs-recheck', recheck: null,
    })
    const captureBefore = freeze(structuredClone(capture))
    expect(failureSnapshot).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: WORKSPACE, provenance: 'saved',
      state: 'unknown', writable: false, pending: false, readPending: false, canUndo: false,
      layout: GOLDEN.positive, openedBaseline: GOLDEN.positive, draft: GOLDEN.submitted,
      recovery: expectedCapture, overlay: { [WORKSPACE]: GOLDEN.positive }, counters: countersAfterFailure,
    })
    const failureStatus = store.getPersistenceStatus(WORKSPACE)
    expect(failureStatus).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: true,
      canCustomize: false, failureKind: 'correction-required',
    })
    const readonlyStart = published.length
    const review = binding.reviewRecovery(capture.attemptId)
    expectFrozen(review)
    expect(review.recovery).toBe(capture)
    expect(review.permissions).toStrictEqual({ recheck: true, reapply: false, retry: false, discard: true })
    await drain()
    expect(store.getSnapshot(WORKSPACE)).toBe(failureSnapshot)
    expect(published).toHaveLength(readonlyStart)
    expectFailureIO(2)

    const read = binding.recheckReviewedRecovery(review.ticket)
    const readOutcome = observe(read)
    let readSettled = false
    void readOutcome.then(() => { readSettled = true })
    await turn()
    await turn()
    const held = store.getSnapshot(WORKSPACE)
    expect(held).toStrictEqual({
      ...failureSnapshot, readPending: true,
      recovery: { ...expectedCapture, phase: 'rechecking', recheck: null },
    })
    expect(readSettled).toBe(false)
    expectFailureIO(3)
    expect(binding.recheckReviewedRecovery(review.ticket)).toBe(read)
    const occupied = binding.reviewRecovery(capture.attemptId)
    expect(occupied.permissions).toStrictEqual({ recheck: false, reapply: false, retry: false, discard: false })
    expect(binding.discardRecovery(occupied.ticket)).toBeUndefined()
    expect(store.getSnapshot(WORKSPACE)).toBe(held)
    reviewedRead.resolve(TEST_DATA.reviewedRead) // Strict different RAW target, not display-repaired data.
    expect(await readOutcome).toStrictEqual({ rejected: false })
    await drain()
    const ready = store.getSnapshot(WORKSPACE)
    expect(ready).toStrictEqual({
      ...failureSnapshot,
      recovery: { ...expectedCapture, phase: 'review-ready', recheck: {
        readId: 6, provenance: 'saved', target: GOLDEN.reviewed, matchesSubmitted: false,
      } },
    })
    expect(ready.recovery?.baseline).toBe(capture.baseline)
    expect(ready.recovery?.submitted).toBe(capture.submitted)
    expect(GOLDEN.reviewed).not.toStrictEqual(GOLDEN.positive)
    expect(GOLDEN.reviewed).not.toStrictEqual(GOLDEN.submitted)
    expect(store.getPersistenceStatus(WORKSPACE)).toStrictEqual(failureStatus)
    expectFailureIO(3)
    const readyReview = binding.reviewRecovery(capture.attemptId)
    expectFrozen(readyReview)
    expect(readyReview.recovery).toBe(ready.recovery)
    expect(readyReview.ticket).toStrictEqual({
      attemptId: 'dashboard-attempt-2', actorId: ACTOR, authGeneration: GENERATION,
      workspaceId: WORKSPACE, epoch: 2, readId: 6,
    })
    expect(readyReview.permissions).toStrictEqual({ recheck: true, reapply: false, retry: false, discard: true })
    const readyStatus = store.getPersistenceStatus(WORKSPACE)
    const deniedStart = published.length
    // BOTH direct real actions, SAME current native review ticket. A hidden
    // button is insufficient; each refusal must leave original authority intact.
    for (const action of ['retry', 'reapply'] as const) {
      expect(await observe(binding.replaceRecovery(readyReview.ticket, action))).toMatchObject({ rejected: true })
      await drain()
      expect(store.getSnapshot(WORKSPACE)).toBe(ready)
      expect(store.getPersistenceStatus(WORKSPACE)).toBe(readyStatus)
      expect(ready.recovery).toBe(readyReview.recovery)
      expect(ready.recovery?.epoch).toBe(2)
      expect(capture).toStrictEqual(captureBefore)
      expect(store.finalize(oldEditor.sessionId)).toBe(oldCompletion)
      expect(binding.close(oldEditor, true)).toBe(oldClose)
      await expectOriginal(oldClose)
      await expectOriginal(oldCompletion)
      expect(writes[1].binding.isCurrent()).toBe(true)
      expect(published).toHaveLength(deniedStart)
      expectFailureIO(3)
    }

    const discardStart = published.length
    expect(binding.discardRecovery(readyReview.ticket)).toBeUndefined()
    const discarded = store.getSnapshot(WORKSPACE)
    expect(discarded).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: WORKSPACE, provenance: 'saved',
      state: 'closed', writable: true, pending: false, readPending: false, canUndo: false,
      layout: GOLDEN.reviewed, openedBaseline: null, draft: null, recovery: null,
      overlay: {}, counters: countersAfterFailure,
    })
    expect(discarded.overlay).toStrictEqual({})
    expect(store.getPersistenceStatus(WORKSPACE)).toStrictEqual({
      pending: false, readPending: false, stale: false, unsaved: false, canCustomize: true, failureKind: null,
    })
    expect(writes[1].binding.isCurrent()).toBe(false)
    await drain()
    expect(published.slice(discardStart)).toStrictEqual([discarded])
    expectFailureIO(3) // Local Discard is not transport success or a replay.
    await expectOriginal(oldClose)
    await expectOriginal(oldCompletion) // Captured task, never relookup after session consumption.
    expect(() => store.setDraft(oldEditor.sessionId, GOLDEN.corrected.items)).toThrow('Dashboard layout session is not editable')
    expect(() => binding!.reviewRecovery(capture.attemptId)).toThrow()

    // Qualified Discard already permits editing. This explicit ordinary Refresh
    // is legitimate additional freshness, not required recovery or Retry authority.
    const refresh = binding.refreshDisplay()
    const refreshOutcome = observe(refresh)
    await turn()
    expect(reads).toBe(4)
    expect(binding.refreshDisplay()).toBe(refresh)
    expect(store.getSnapshot(WORKSPACE)).toStrictEqual({ ...discarded, readPending: true })
    expect(store.getPersistenceStatus(WORKSPACE).canCustomize).toBe(false)
    expect(() => binding!.open()).toThrow()
    ordinaryRead.resolve(TEST_DATA.ordinaryRead)
    expect(await refreshOutcome).toStrictEqual({ rejected: false })
    await drain()
    expect(store.getSnapshot(WORKSPACE)).toStrictEqual(discarded)
    expectFailureIO(4)

    const freshEditor = binding.open()
    unsent = freshEditor
    expect(freshEditor.sessionId).not.toBe(oldEditor.sessionId)
    expect(store.getSnapshot(WORKSPACE).openedBaseline).toStrictEqual(GOLDEN.reviewed)
    store.setDraft(freshEditor.sessionId, GOLDEN.corrected.items)
    expect(store.getSnapshot(WORKSPACE).draft).toStrictEqual(GOLDEN.corrected)
    expect(GOLDEN.corrected).not.toStrictEqual(GOLDEN.reviewed)
    expect(GOLDEN.corrected).not.toStrictEqual(GOLDEN.submitted)
    expect(store.getPersistenceStatus(WORKSPACE).unsaved).toBe(true)
    const editing = store.getSnapshot(WORKSPACE)
    expect(binding.discardRecovery(readyReview.ticket)).toBeUndefined()
    expect(await observe(binding.replaceRecovery(readyReview.ticket, 'retry'))).toMatchObject({ rejected: true })
    expect(store.getSnapshot(WORKSPACE)).toBe(editing)
    expectFailureIO(4)
    const freshOutcome = observe(binding.close(freshEditor, true))
    unsent = null
    await turn()
    expect(writes).toHaveLength(3) // Separate changed ordinary editor, NEVER original draft replay.
    expect(writes[2]).toMatchObject({ workspaceId: WORKSPACE, body: GOLDEN.corrected, current: true })
    expect(writes[2].body).not.toStrictEqual(writes[1].body)
    expect(writes[2].binding.isCurrent()).toBe(true)
    corrected.resolve(TEST_DATA.correctedCommit)
    await turn()
    await turn()
    expect(reads).toBe(5)
    correctedRead.resolve(TEST_DATA.correctedRead)
    expect(await freshOutcome).toStrictEqual({ rejected: false })
    await drain()
    expect(store.getSnapshot(WORKSPACE)).toStrictEqual({
      actorId: ACTOR, authGeneration: GENERATION, workspaceId: WORKSPACE, provenance: 'saved',
      state: 'closed-success', writable: true, pending: false, readPending: false, canUndo: false,
      layout: GOLDEN.corrected, openedBaseline: null, draft: null, recovery: null,
      overlay: { [WORKSPACE]: GOLDEN.corrected },
      counters: { success: 2, suppressed: 0, conflict409: 0, other4xx: 1, server5xx: 0 },
    })
    expect(store.getPersistenceStatus(WORKSPACE).canCustomize).toBe(true)
    expect(writes[2].binding.isCurrent()).toBe(false)
    expect(reads).toBe(5)
    expect(writes).toHaveLength(3)
    expect(capture).toStrictEqual(captureBefore)
    expectFrozen(readyReview)
    await expectOriginal(oldClose)
    await expectOriginal(oldCompletion)
  } catch (error: unknown) {
    primaryFailed = true
    primaryError = error
  } finally {
    // Settle every finite input on RED; never let release save an unsent edit.
    initial.resolve(TEST_DATA.initial)
    positive.resolve(TEST_DATA.positiveCommit)
    positiveRead.resolve(TEST_DATA.positiveRead)
    failed.reject(originalError)
    reviewedRead.resolve(TEST_DATA.reviewedRead)
    ordinaryRead.resolve(TEST_DATA.ordinaryRead)
    corrected.resolve(TEST_DATA.correctedCommit)
    correctedRead.resolve(TEST_DATA.correctedRead)
    if (binding && unsent) {
      try { await observe(binding.close(unsent, false)) }
      catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drain() } catch (error: unknown) { cleanupErrors.push(error) }
    const beforeRelease = { reads, writes: writes.length }
    if (binding) {
      try { binding.release() } catch (error: unknown) { cleanupErrors.push(error) }
    }
    try { await drain() } catch (error: unknown) { cleanupErrors.push(error) }
    try {
      expect(reads).toBe(beforeRelease.reads)
      expect(writes).toHaveLength(beforeRelease.writes)
      expect(store.getSnapshot(WORKSPACE).pending).toBe(false)
      expect(store.getSnapshot(WORKSPACE).readPending).toBe(false)
      expect(TEST_DATA).toStrictEqual(dataBefore)
      expect(GOLDEN).toStrictEqual(goldenBefore)
      expect(authSeed).toStrictEqual(seedBefore)
      expect(authSeed.preferences).toBe(seedPreferences)
      expect(DASHBOARD_WIDGETS).toStrictEqual(registryBefore)
      expectFrozen(TEST_DATA)
      expectFrozen(GOLDEN)
    } catch (error: unknown) { cleanupErrors.push(error) }
    unsubscribe()
  }
  // Keep primary failure distinct from cleanup diagnostics; no finally throw.
  if (primaryFailed) {
    if (cleanupErrors.length > 0) throw new AggregateError([primaryError, ...cleanupErrors], 'HTTP400 policy primary failure with separate cleanup diagnostics')
    throw primaryError
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'HTTP400 policy cleanup failed')
})
