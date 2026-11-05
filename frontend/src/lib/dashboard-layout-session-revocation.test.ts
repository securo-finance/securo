import { expect, it, vi } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import {
  resolveDashboardLayout,
  toPersistedLayout,
  toggleItem,
  validateCommittedDashboardLayout,
  type CommittedDashboardLayout,
  type DashboardLayout,
  type DashboardRequestBinding,
} from '@/lib/dashboard-layout'
import type { SessionStore, StoreOptions } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// PROPOSED INTERNAL SPI, absent from the current real SessionStore:
// revoke(): void synchronously and terminally invalidates this generation.
// The cast below does not implement it; availability fails explicitly in the case.
type ProposedRevocableStore = SessionStore & { revoke(): void }
const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const ACTOR = '33333333-3333-4333-8333-333333333333'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => { resolve = settle })
  return { promise, resolve }
}

// Fixture immutability, not store/writer behavior.
function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}

it('terminally revokes an unsent old-generation edit with unchanged identity while a fresh generation still saves', async () => {
  const defaults = resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, A)
  const sibling = freezeFixture(toPersistedLayout(
    B,
    toggleItem(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, B).items, 'goals-progress', false),
    DASHBOARD_WIDGETS,
  ))
  const authSeed: Readonly<User> = freezeFixture({
    id: ACTOR,
    email: 'revocation-tracer@example.invalid',
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Synthetic revocation tracer',
      onboarding_completed: true, dashboard_layouts: { [B]: sibling },
    },
  })
  const seedBefore = structuredClone(authSeed)
  const preferencesBefore = authSeed.preferences
  const siblingBefore = structuredClone(sibling)
  const initialUser: User = freezeFixture(structuredClone(seedBefore))

  // Literal strict replies and literal GET users, never derived from captured PUT.
  const oldCommitted = freezeFixture(validateCommittedDashboardLayout(A, {
    workspaceId: A,
    layout: {
      version: 1, workspaceId: A,
      items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  }, DASHBOARD_WIDGETS))
  const oldPostUser: User = freezeFixture({
    id: ACTOR, email: 'revocation-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Synthetic revocation tracer',
      onboarding_completed: true,
      dashboard_layouts: {
        [B]: sibling,
        [A]: {
          version: 1, workspaceId: A,
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
  const freshCommitted = freezeFixture(validateCommittedDashboardLayout(A, {
    workspaceId: A,
    layout: {
      version: 1, workspaceId: A,
      items: [
        { id: 'hero-balance', visible: false, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: false, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    },
  }, DASHBOARD_WIDGETS))
  const freshPostUser: User = freezeFixture({
    id: ACTOR, email: 'revocation-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'pt-BR', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Synthetic revocation tracer',
      onboarding_completed: true,
      dashboard_layouts: {
        [B]: sibling,
        [A]: {
          version: 1, workspaceId: A,
          items: [
            { id: 'hero-balance', visible: false, order: 0 },
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
  const usersBefore = [initialUser, oldPostUser, freshPostUser].map((user) => structuredClone(user))
  const oldEntry = deferred<User>()
  const oldReply = deferred<CommittedDashboardLayout>()
  const oldStarted = deferred<void>()
  const oldPostRead = deferred<User>()
  const freshEntry = deferred<User>()
  const freshReply = deferred<CommittedDashboardLayout>()
  const freshStarted = deferred<void>()
  const freshPostRead = deferred<User>()
  type Phase = 'old-positive' | 'revoked' | 'fresh-positive'
  let phase: Phase = 'old-positive'
  const captured: Array<{
    phase: Phase
    workspaceId: string
    layout: DashboardLayout
    binding: DashboardRequestBinding
  }> = []
  const me = vi.fn<StoreOptions['api']['me']>()
    .mockImplementationOnce(() => oldEntry.promise)
    .mockImplementationOnce(() => oldPostRead.promise)
    .mockImplementationOnce(() => freshEntry.promise)
    .mockImplementationOnce(() => freshPostRead.promise)
    .mockImplementation(() => Promise.reject(new Error('Unexpected tracer GET')))
  const saveLayout = vi.fn<StoreOptions['api']['saveLayout']>((workspaceId, layout, binding) => {
    captured.push({ phase, workspaceId, layout: structuredClone(layout), binding })
    // No fake isCurrent guard: every boundary invocation is counted and recorded.
    if (phase === 'old-positive') {
      oldStarted.resolve()
      return oldReply.promise
    }
    if (phase === 'fresh-positive') {
      freshStarted.resolve()
      return freshReply.promise
    }
    // An illicit revoked-phase dispatch must fail promptly AND fail count assertions.
    return Promise.reject(new Error('Unexpected scoped PUT during revoked phase'))
  })
  const updateMe = vi.fn<(...args: unknown[]) => Promise<User>>(() => {
    return Promise.reject(new Error('Forbidden generic auth.updateMe'))
  })

  vi.resetModules()
  vi.doMock('@/lib/api', () => ({ auth: { me, updateMe }, dashboard: { saveLayout } }))
  try {
    const { createDashboardLayoutSession } = await import('@/lib/dashboard-layout-session')
    // Identity never changes or becomes null, even during cleanup. Token only here.
    const getTokenIdentity = (() => {
      const identity = 'synthetic-revocation-boundary-token'
      return () => identity
    })()
    const options: StoreOptions = {
      registry: DASHBOARD_WIDGETS, actorId: ACTOR, authGeneration: 1, authSeed,
      getTokenIdentity, getWorkspaceId: () => A, api: { me, saveLayout },
    }
    const old = createDashboardLayoutSession(options)
    const entry = old.refresh(A)
    expect(me).toHaveBeenCalledTimes(1)
    oldEntry.resolve(initialUser)
    await entry
    await old.whenIdle()
    expect(old.getSnapshot(A)).toMatchObject({
      authGeneration: 1, writable: true, provenance: 'default', layout: defaults,
    })
    const positiveSession = old.open(A)
    const oldExpected = toPersistedLayout(A, toggleItem(defaults.items, 'goals-progress', false), DASHBOARD_WIDGETS)
    expect(oldExpected).not.toEqual(defaults)
    expect(oldExpected).toEqual(oldCommitted.layout)
    old.setDraft(positiveSession, oldExpected.items)
    const positive = old.finalize(positiveSession)
    // Attach both settlement handlers immediately, including for positive controls.
    const positiveOutcome = positive.then(() => ({ ok: true }), (error: unknown) => ({ ok: false, error }))
    expect(old.getSnapshot(A).pending).toBe(true)
    await oldStarted.promise
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(captured[0].binding.isCurrent()).toBe(true)
    oldReply.resolve(oldCommitted)
    oldPostRead.resolve(oldPostUser)
    expect(await positiveOutcome).toEqual({ ok: true })
    await old.whenIdle()
    expect(me).toHaveBeenCalledTimes(2)
    expect(old.getSnapshot(A)).toMatchObject({
      state: 'closed-success', writable: true, pending: false, readPending: false,
      overlay: { [A]: oldExpected }, layout: oldExpected, counters: { success: 1 },
    })
    expect(old.getSnapshot(B).layout).toEqual(siblingBefore)
    expect(authSeed).toEqual(seedBefore)
    expect(authSeed.preferences).toBe(preferencesBefore)
    expect(updateMe).not.toHaveBeenCalled()
    expect(captured.filter((request) => request.phase === 'old-positive')).toHaveLength(1)

    // Expected initial RED here, AFTER a real same-owner positive control.
    const revocableOld = old as ProposedRevocableStore
    expect(typeof revocableOld.revoke, 'proposed real store.revoke(): void must exist for terminal generation invalidation').toBe('function')
    phase = 'revoked'
    const putsBeforeRevocation = saveLayout.mock.calls.length
    const revokedSession = old.open(A)
    const queued = toPersistedLayout(A, toggleItem(oldExpected.items, 'spending-charts', false), DASHBOARD_WIDGETS)
    expect(queued).not.toEqual(oldExpected)
    old.setDraft(revokedSession, queued.items)
    expect(old.getSnapshot(A)).toMatchObject({ writable: true, openedBaseline: oldExpected, draft: queued })
    const queuedCompletion = old.finalize(revokedSession)
    const queuedOutcome = queuedCompletion.then(
      () => ({ ok: true as const }),
      (error: unknown) => ({ ok: false as const, error }),
    )
    expect(old.getSnapshot(A)).toMatchObject({ state: 'pending', pending: true })
    expect(saveLayout).toHaveBeenCalledTimes(1)
    // NO await between finalize's synchronous latch and synchronous revocation.
    revocableOld.revoke()
    expect(old.getSnapshot(A)).toMatchObject({
      writable: false, pending: false, readPending: false,
      overlay: {}, draft: null, openedBaseline: null,
    })
    expect(old.getSnapshot(A).overlay).toEqual({})
    expect(old.getSnapshot(A).layout).toEqual(defaults)
    expect(old.getSnapshot(A).provenance).toBe('default')
    expect(old.getSnapshot(B).layout).toEqual(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, B))
    expect(old.getSnapshot(B).layout).not.toEqual(siblingBefore)
    const revokedOutcome = await queuedOutcome
    expect(revokedOutcome.ok).toBe(false)
    if (revokedOutcome.ok) throw new Error('Old queued finalize unexpectedly succeeded')
    expect(revokedOutcome.error).toBeInstanceOf(Error)
    expect(revokedOutcome.error).not.toBeInstanceOf(TypeError)
    expect((revokedOutcome.error as Error).message).toMatch(
      /^Dashboard layout (?:write did not establish a current committed leaf|write owner is not current|session is revoked|generation is revoked)$/,
    )
    await old.whenIdle()
    expect(old.getSnapshot(A).overlay).toEqual({})
    expect(old.getSnapshot(A).layout).toEqual(defaults)
    expect(old.getSnapshot(A).provenance).toBe('default')
    expect(old.getSnapshot(B).layout).toEqual(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, B))
    expect(saveLayout.mock.calls.length - putsBeforeRevocation).toBe(0)
    expect(saveLayout).toHaveBeenCalledTimes(1)
    expect(captured.filter((request) => request.phase === 'revoked')).toHaveLength(0)
    expect(me).toHaveBeenCalledTimes(2)
    expect(updateMe).not.toHaveBeenCalled()
    expect(() => old.open(A)).toThrow(/^Dashboard layout /)
    const lateOld = old.finalize(revokedSession).then(
      () => ({ ok: true as const }),
      (error: unknown) => ({ ok: false as const, error }),
    )
    const lateOutcome = await lateOld
    expect(lateOutcome.ok).toBe(false)
    if (lateOutcome.ok) throw new Error('Revoked old session unexpectedly finalized')
    expect(lateOutcome.error).toBeInstanceOf(Error)
    expect(lateOutcome.error).not.toBeInstanceOf(TypeError)
    expect((lateOutcome.error as Error).message).toMatch(
      /^Dashboard layout (?:session cannot finalize|session is revoked|generation is revoked)$/,
    )
    await old.whenIdle()
    expect(old.getSnapshot(A)).toMatchObject({ writable: false, pending: false, overlay: {}, draft: null, openedBaseline: null })
    expect(old.getSnapshot(A).overlay).toEqual({})
    expect(old.getSnapshot(A).layout).toEqual(defaults)
    expect(old.getSnapshot(A).provenance).toBe('default')
    expect(old.getSnapshot(B).layout).toEqual(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, B))

    phase = 'fresh-positive'
    const putsBeforeFresh = saveLayout.mock.calls.length
    const fresh = createDashboardLayoutSession({ ...options, authGeneration: 2 })
    expect(fresh).not.toBe(old)
    const freshRead = fresh.refresh(A)
    expect(me).toHaveBeenCalledTimes(3)
    freshEntry.resolve(oldPostUser)
    await freshRead
    await fresh.whenIdle()
    expect(fresh.getSnapshot(A)).toMatchObject({ authGeneration: 2, writable: true, layout: oldExpected, overlay: {} })
    const freshSession = fresh.open(A)
    const freshExpected = toPersistedLayout(A, toggleItem(oldExpected.items, 'hero-balance', false), DASHBOARD_WIDGETS)
    expect(freshExpected).not.toEqual(oldExpected)
    expect(freshExpected).toEqual(freshCommitted.layout)
    fresh.setDraft(freshSession, freshExpected.items)
    const freshCompletion = fresh.finalize(freshSession)
    const freshOutcome = freshCompletion.then(() => ({ ok: true }), (error: unknown) => ({ ok: false, error }))
    expect(fresh.getSnapshot(A).pending).toBe(true)
    await freshStarted.promise
    expect(saveLayout).toHaveBeenCalledTimes(2)
    expect(captured[1].binding.isCurrent()).toBe(true)
    // Old-owner operations must not disturb a pending replacement owner.
    const freshPending = fresh.getSnapshot(A)
    expect(() => old.open(A)).toThrow(/^Dashboard layout /)
    const oldAgain = old.finalize(revokedSession).then(() => true, () => false)
    expect(await oldAgain).toBe(false)
    await old.whenIdle()
    expect(fresh.getSnapshot(A)).toEqual(freshPending)
    expect(saveLayout).toHaveBeenCalledTimes(2)
    freshReply.resolve(freshCommitted)
    freshPostRead.resolve(freshPostUser)
    expect(await freshOutcome).toEqual({ ok: true })
    await fresh.whenIdle()
    await old.whenIdle()
    expect(fresh.getSnapshot(A)).toMatchObject({
      authGeneration: 2, state: 'closed-success', writable: true,
      pending: false, readPending: false, layout: freshExpected,
      openedBaseline: null, draft: null, overlay: { [A]: freshExpected },
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(fresh.getSnapshot(B).layout).toEqual(siblingBefore)
    expect(old.getSnapshot(A)).toMatchObject({ writable: false, overlay: {}, draft: null, openedBaseline: null })
    expect(old.getSnapshot(A).overlay).toEqual({})
    expect(old.getSnapshot(A).layout).toEqual(defaults)
    expect(old.getSnapshot(A).provenance).toBe('default')
    expect(old.getSnapshot(B).layout).toEqual(resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, B))
    expect(saveLayout.mock.calls.length - putsBeforeFresh).toBe(1)
    expect(saveLayout).toHaveBeenCalledTimes(2)
    expect(me).toHaveBeenCalledTimes(4)
    expect(updateMe).not.toHaveBeenCalled()
    expect(captured).toHaveLength(2)
    expect(captured.map((request) => request.phase)).toEqual(['old-positive', 'fresh-positive'])
    expect(captured.filter((request) => request.phase === 'revoked')).toHaveLength(0)
    for (const [index, expected] of [oldExpected, freshExpected].entries()) {
      const request = captured[index]
      expect(request.workspaceId).toBe(A)
      expect(request.layout).toEqual(expected)
      expect(Object.keys(request.layout).sort()).toEqual(['items', 'version', 'workspaceId'])
      request.layout.items.forEach((item, order) => {
        expect(Object.keys(item).sort()).toEqual(['id', 'order', 'visible'])
        expect(item.order).toBe(order)
      })
    }
    expect(authSeed).toEqual(seedBefore)
    expect(authSeed.preferences).toBe(preferencesBefore)
    expect(authSeed.preferences.dashboard_layouts?.[B]).toBe(sibling)
    expect(authSeed.preferences.dashboard_layouts).not.toHaveProperty(A)
    expect([initialUser, oldPostUser, freshPostUser]).toEqual(usersBefore)
  } finally {
    // Mock registration restoration only; no counterfeit product revocation/cleanup.
    vi.doUnmock('@/lib/api')
    vi.restoreAllMocks()
    vi.resetModules()
  }
  // Vitest bounds all declared promise barriers; no sleeps or query-client drain.
}, 10_000)
