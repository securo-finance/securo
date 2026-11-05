import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { getPageSessionLease } from '@/lib/dashboard-layout-page-session-lease'
import { createDashboardLayoutSession, INERT_PERSISTENCE_STATUS } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'
function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freeze(child) })
    Object.freeze(value)
  }
  return value
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse })
  const outcome = promise.then(() => ({ ok: true as const }), (error: unknown) => ({ ok: false as const, error }))
  return { promise, outcome, resolve, reject }
}
function turn(): Promise<void> {
  return new Promise((resolve) => { queueMicrotask(resolve) })
}
// Independent literal server data, never generated from PUT arguments or GOLDEN.
const INITIAL: User = freeze({
  id: ACTOR, email: 'inactive-matrix@example.invalid', is_active: true,
  is_superuser: false, is_verified: true, is_2fa_enabled: false,
  preferences: { language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
    currency_display: 'symbol', display_name: 'Inactive matrix', onboarding_completed: true,
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
})
const COMMIT: CommittedDashboardLayout = freeze({ workspaceId: A,
  layout: { version: 1, workspaceId: A, items: [
    { id: 'hero-balance', visible: true, order: 0 },
    { id: 'uncategorized-banner', visible: true, order: 1 },
    { id: 'spending-charts', visible: true, order: 2 },
    { id: 'balance-evolution', visible: true, order: 3 },
    { id: 'goals-progress', visible: false, order: 4 },
    { id: 'period-transactions', visible: true, order: 5 },
  ] },
})
// Separate expected data, not server response aliases or tolerant normalization.
const BASELINE: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const SUBMITTED: DashboardLayout = freeze({ version: 1, workspaceId: A, items: [
  { id: 'hero-balance', visible: true, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: true, order: 3 },
  { id: 'goals-progress', visible: false, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const B_DRAFT: DashboardLayout = freeze({ version: 1, workspaceId: B, items: [
  { id: 'hero-balance', visible: false, order: 0 },
  { id: 'uncategorized-banner', visible: true, order: 1 },
  { id: 'spending-charts', visible: true, order: 2 },
  { id: 'balance-evolution', visible: false, order: 3 },
  { id: 'goals-progress', visible: true, order: 4 },
  { id: 'period-transactions', visible: true, order: 5 },
] })
const ZERO = freeze({ success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 })

for (const status of [null, 403, 409, 422] as const) {
  it(`records inactive issued A ${status ?? 'strict success'} without inactive GET, replay, or B editor mutation`, async () => {
    const sourceBefore = structuredClone({ INITIAL, COMMIT, BASELINE, SUBMITTED, B_DRAFT })
    let selected = A
    let reads = 0
    const port = deferred<CommittedDashboardLayout>()
    const failure = freeze(Object.assign(new Error(`Independent original inactive status ${status}`), { response: { status } }))
    const writes: Array<{ target: string; body: DashboardLayout; binding: DashboardRequestBinding }> = []
    const store = createDashboardLayoutSession({ registry: DASHBOARD_WIDGETS, actorId: ACTOR,
      authGeneration: 7, authSeed: INITIAL, getTokenIdentity: () => 'synthetic-inactive-matrix-token',
      getWorkspaceId: () => selected, api: {
        me: () => { reads += 1; return reads === 1 ? Promise.resolve(INITIAL) : Promise.reject(new Error('Forbidden inactive GET')) },
        saveLayout: (target, body, binding) => { writes.push({ target, body: structuredClone(body), binding }); return port.promise },
      },
    })
    const lease = getPageSessionLease(store)
    await store.refresh(A)
    await store.whenIdle()
    lease.select(A)
    const aBinding = lease.bind(A)
    const ticket = aBinding.open()
    store.setDraft(ticket.sessionId, SUBMITTED.items)
    expect(SUBMITTED).not.toStrictEqual(BASELINE)
    const close = aBinding.close(ticket, true)
    const closeOutcome = close.then(() => ({ ok: true as const }), (error: unknown) => ({ ok: false as const, error }))
    const completion = store.finalize(ticket.sessionId)
    const completionOutcome = completion.then(() => ({ ok: true as const }), (error: unknown) => ({ ok: false as const, error }))
    await turn()
    expect(writes).toHaveLength(1)
    expect(writes[0].target).toBe(A)
    expect(writes[0].body).toStrictEqual(SUBMITTED)
    expect(writes[0].binding.isCurrent()).toBe(true)
    selected = B
    lease.select(B)
    aBinding.release()
    const bBinding = lease.bind(B)
    const bTicket = bBinding.open()
    store.setDraft(bTicket.sessionId, B_DRAFT.items)
    const bBefore = store.getSnapshot(B)
    const bStatus = store.getPersistenceStatus(B)
    expect(writes[0].binding.isCurrent()).toBe(false)
    expect(store.getPersistenceStatus(A)).toBe(INERT_PERSISTENCE_STATUS)
    try {
      if (status === null) port.resolve(COMMIT)
      else port.reject(failure)
      const result = await completionOutcome
      const closeResult = await closeOutcome
      await lease.whenIdle()
      await store.whenIdle()
      if (status === null) {
        expect(result, 'strict inactive success must not be reclassified as stale selection').toStrictEqual({ ok: true })
        expect(closeResult).toStrictEqual({ ok: true })
      } else {
        expect(result).toStrictEqual({ ok: false, error: failure })
        expect(closeResult).toStrictEqual({ ok: false, error: failure })
        if (!result.ok) expect(result.error).toBe(failure)
        if (!closeResult.ok) expect(closeResult.error).toBe(failure)
      }
      expect(reads).toBe(1)
      expect(writes).toHaveLength(1)
      const counters = { ...ZERO, success: status === null ? 1 : 0,
        conflict409: status === 409 ? 1 : 0, other4xx: status === 403 || status === 422 ? 1 : 0 }
      const aAfter = store.getSnapshot(A)
      expect(aAfter).toStrictEqual({
        actorId: ACTOR, authGeneration: 7, workspaceId: A, provenance: 'saved',
        writable: status === null, state: status === null ? 'closed-success' : status === 403 ? 'blocked' : status === 409 ? 'conflict' : 'unknown',
        pending: false, readPending: false, canUndo: false,
        layout: status === null ? SUBMITTED : BASELINE,
        openedBaseline: status === null ? null : BASELINE, draft: status === null ? null : SUBMITTED,
        recovery: status === null ? null : {
          attemptId: 'dashboard-attempt-1', actorId: ACTOR, authGeneration: 7, workspaceId: A,
          epoch: 1, baseline: BASELINE, submitted: SUBMITTED,
          category: status === 403 ? 'blocked' : status === 409 ? 'conflict' : 'correction-required',
          phase: 'needs-recheck', recheck: null,
        },
        overlay: status === null ? { [A]: SUBMITTED } : {}, counters,
      })
      // Overlay/counters are intentional store-wide projections; B's target/editor is unchanged.
      expect(store.getSnapshot(B)).toStrictEqual({ ...bBefore, counters, overlay: aAfter.overlay })
      expect(store.getPersistenceStatus(B)).toBe(bStatus)
      expect(store.getSnapshot(B).draft).toStrictEqual(B_DRAFT)
      expect(store.finalize(ticket.sessionId)).toBe(completion)
      selected = A
      lease.select(A)
      bBinding.release()
      const returned = lease.bind(A)
      await turn()
      expect(reads).toBe(1)
      expect(writes).toHaveLength(1)
      expect(store.getSnapshot(A)).toStrictEqual(aAfter)
      if (status === null) expect(store.getPersistenceStatus(A).canCustomize).toBe(true)
      else {
        const review = returned.reviewRecovery('dashboard-attempt-1')
        expect(review.recovery).toBe(aAfter.recovery)
        expect(review.permissions).toStrictEqual({ recheck: true, reapply: false, retry: false, discard: true })
      }
      returned.release()
    } finally {
      port.resolve(COMMIT)
      await Promise.all([port.outcome, closeOutcome, completionOutcome])
      await lease.whenIdle()
      await store.whenIdle()
      bBinding.release()
      store.revoke()
      expect({ INITIAL, COMMIT, BASELINE, SUBMITTED, B_DRAFT }).toStrictEqual(sourceBefore)
    }
  })
}

for (const boundary of ['terminal-success', 'terminal-409', 'changed-token-success'] as const) {
  it(`rejects old issued ${boundary} record authority while a fresh owner actually saves`, async () => {
    let token = 'synthetic-old-owner-token'
    let oldReads = 0
    let freshReads = 0
    const oldPort = deferred<CommittedDashboardLayout>()
    const original = freeze(Object.assign(new Error('Original retired owner conflict'), { response: { status: 409 } }))
    const oldWrites: DashboardRequestBinding[] = []
    const old = createDashboardLayoutSession({ registry: DASHBOARD_WIDGETS, actorId: ACTOR,
      authGeneration: 7, authSeed: INITIAL, getTokenIdentity: () => token, getWorkspaceId: () => A,
      api: { me: () => { oldReads += 1; return Promise.resolve(INITIAL) },
        saveLayout: (_target, _body, binding) => { oldWrites.push(binding); return oldPort.promise } },
    })
    await old.refresh(A)
    const oldSession = old.open(A)
    old.setDraft(oldSession, SUBMITTED.items)
    const task = old.finalize(oldSession)
    const outcome = task.then(() => ({ ok: true as const }), (error: unknown) => ({ ok: false as const, error }))
    await turn()
    expect(oldWrites).toHaveLength(1)
    expect(oldWrites[0].isCurrent()).toBe(true)
    if (boundary === 'changed-token-success') token = 'synthetic-fresh-owner-token'
    else old.revoke()
    expect(oldWrites[0].isCurrent()).toBe(false)
    const retired = old.getSnapshot(A)
    let freshWrites = 0
    const fresh = createDashboardLayoutSession({ registry: DASHBOARD_WIDGETS, actorId: ACTOR,
      authGeneration: 8, authSeed: INITIAL, getTokenIdentity: () => token, getWorkspaceId: () => A,
      api: { me: () => { freshReads += 1; return Promise.resolve(INITIAL) },
        saveLayout: (target, body, binding) => {
          freshWrites += 1
          expect(target).toBe(A)
          expect(body).toStrictEqual(SUBMITTED)
          expect(binding.isCurrent()).toBe(true)
          return Promise.resolve(COMMIT)
        } },
    })
    try {
      await fresh.refresh(A)
      const freshSession = fresh.open(A)
      fresh.setDraft(freshSession, SUBMITTED.items)
      await fresh.finalize(freshSession)
      await fresh.whenIdle()
      const freshBefore = fresh.getSnapshot(A)
      expect(freshBefore.state).toBe('closed-success')
      expect(freshBefore.overlay).toStrictEqual({ [A]: SUBMITTED })
      expect(freshBefore.counters).toStrictEqual({ ...ZERO, success: 1 })
      expect(freshWrites).toBe(1)
      expect(freshReads).toBe(2)
      if (boundary === 'terminal-409') oldPort.reject(original)
      else oldPort.resolve(COMMIT)
      const result = await outcome
      expect(result.ok).toBe(false)
      if (!result.ok && boundary === 'terminal-409') expect(result.error).toBe(original)
      await old.whenIdle()
      expect(oldReads).toBe(1)
      expect(oldWrites).toHaveLength(1)
      expect(old.getSnapshot(A)).toStrictEqual({ ...retired,
        counters: boundary === 'terminal-409' ? { ...ZERO, conflict409: 1 } : { ...ZERO, success: 1 } })
      expect(old.getSnapshot(A).recovery).toBeNull()
      expect(fresh.getSnapshot(A)).toBe(freshBefore)
      expect(freshWrites).toBe(1)
      expect(freshReads).toBe(2)
    } finally {
      oldPort.resolve(COMMIT)
      await Promise.all([oldPort.outcome, outcome])
      await old.whenIdle()
      await fresh.whenIdle()
      old.revoke()
      fresh.revoke()
    }
  })
}
