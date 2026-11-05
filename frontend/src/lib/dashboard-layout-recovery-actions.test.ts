import { expect, it } from 'vitest'

import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import type { CommittedDashboardLayout, DashboardLayout, DashboardRequestBinding } from '@/lib/dashboard-layout'
import { createDashboardLayoutSession, type SessionSnapshot, type SessionStore } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

// Bounded native core evidence: ONLY StoreOptions.api transport substitution.
// Not real AuthProvider, SDK/interceptors, hook, consumer, App or browser evidence.
const TEST_DATA = Object.freeze({
  actor: '33333333-3333-4333-8333-333333333333',
  workspace: '11111111-1111-4111-8111-111111111111',
  foreignWorkspace: '22222222-2222-4222-8222-222222222222',
  generation: 7,
})

// Prospective public behavior is structural, never a nonexistent export/import.
type ReadonlyStatus = Readonly<{
  pending: boolean
  readPending: boolean
  stale: boolean
  unsaved: boolean
  canCustomize: boolean
  failureKind: 'conflict' | 'unknown' | 'blocked' | 'correction-required' | null
}>
type StatusPort = SessionStore & {
  getPersistenceStatus?: (workspaceId: string) => ReadonlyStatus
}

function freezeFixture<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach((child: unknown) => { freezeFixture(child) })
    Object.freeze(value)
  }
  return value
}

function expectDeepFrozen(value: unknown): void {
  if (value !== null && typeof value === 'object') {
    expect(Object.isFrozen(value)).toBe(true)
    Object.values(value).forEach((child: unknown) => { expectDeepFrozen(child) })
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse })
  return { promise, resolve, reject }
}

function drainNotifications(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('native readonly persistence status retains failed-save evidence without changing legacy snapshots or granting authority', async () => {
  const { actor, workspace, foreignWorkspace, generation } = TEST_DATA
  // Independent RAW GET/map/leaf literals; no display repair creates authority.
  const initialUser: User = freezeFixture({
    id: actor, email: 'native-status-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Native status tracer', onboarding_completed: true,
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
  // Independently authored committed reply, NEVER derived from the request.
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
    id: actor, email: 'native-status-tracer@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: {
      language: 'en', date_format: 'YYYY-MM-DD', timezone: 'UTC',
      currency_display: 'symbol', display_name: 'Native status tracer', onboarding_completed: true,
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
  const fixtures = freezeFixture({ authSeed, initialUser, firstDraft, committed, postUser, failedDraft })
  const fixturesBefore = freezeFixture(structuredClone(fixtures))
  const seedPreferences = authSeed.preferences
  const registryBefore = freezeFixture(structuredClone(DASHBOARD_WIDGETS))
  const networkFailure = Object.freeze(new Error('Independent native status transport failure'))
  const successReply = deferred<CommittedDashboardLayout>()
  const failureReply = deferred<CommittedDashboardLayout>()
  const issued: Promise<unknown>[] = []
  const captured: Array<{
    workspaceId: string
    body: DashboardLayout
    binding: DashboardRequestBinding
    currentAtIssuance: boolean
  }> = []
  let selectedWorkspace: string = workspace
  let reads = 0
  // This ordinary stable public token port is never exposed in snapshot/status.
  const tokenIdentity = 'synthetic-native-status-boundary-token'
  const store: StatusPort = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: actor, authGeneration: generation, authSeed,
    getTokenIdentity: () => tokenIdentity,
    getWorkspaceId: () => selectedWorkspace,
    api: {
      me: () => {
        reads += 1
        if (reads === 1) return Promise.resolve(initialUser)
        if (reads === 2) return Promise.resolve(postUser)
        return Promise.reject(new Error('Unexpected native status GET'))
      },
      saveLayout: (workspaceId, body, binding) => {
        // Record witnesses only. Assertions outside production's classification catch.
        captured.push({ workspaceId, body: structuredClone(body), binding, currentAtIssuance: binding.isCurrent() })
        if (captured.length === 1) return successReply.promise
        if (captured.length === 2) return failureReply.promise
        return Promise.reject(new Error('Unexpected native status PUT'))
      },
    },
  })
  const observed: SessionSnapshot[] = []
  const unsubscribe = store.subscribe(() => { observed.push(store.getSnapshot(workspace)) })
  let primaryError: unknown
  let primaryFailed = false
  const cleanupErrors: unknown[] = []
  try {
    const initialRead = store.refresh(workspace)
    issued.push(initialRead)
    await initialRead
    await store.whenIdle()
    await drainNotifications()
    expect(store.getSnapshot(workspace)).toMatchObject({
      writable: true, provenance: 'saved', state: 'closed', pending: false, readPending: false,
      counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })

    // Genuine current-owner changed save, not blanket-denial evidence.
    const successfulSession = store.open(workspace)
    store.setDraft(successfulSession, firstDraft.items)
    const successfulWrite = store.finalize(successfulSession)
    issued.push(successfulWrite)
    await drainNotifications()
    expect(captured).toHaveLength(1)
    expect(captured[0].workspaceId).toBe(workspace)
    expect(captured[0].body).toStrictEqual(firstDraft)
    expect(captured[0].currentAtIssuance).toBe(true)
    expect(captured[0].binding.isCurrent()).toBe(true)
    expect(store.getSnapshot(workspace)).toMatchObject({ state: 'pending', pending: true })
    successReply.resolve(committed)
    await successfulWrite
    await store.whenIdle()
    await drainNotifications()
    const successfulSnapshot = store.getSnapshot(workspace)
    const successfulBefore = freezeFixture(structuredClone(successfulSnapshot))
    expect(successfulSnapshot).toMatchObject({
      actorId: actor, authGeneration: generation, workspaceId: workspace,
      writable: true, provenance: 'saved', state: 'closed-success', pending: false,
      readPending: false, recovery: null, openedBaseline: null, draft: null,
      layout: committed.layout,
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expect(successfulSnapshot.overlay).toStrictEqual({ [workspace]: committed.layout })
    // The successful ticket's binding is consumed; owner remains eligible.
    expect(captured[0].binding.isCurrent()).toBe(false)
    expect(reads).toBe(2)

    const failedSession = store.open(workspace)
    store.setDraft(failedSession, failedDraft.items)
    const failedWrite = store.finalize(failedSession)
    // Attach both outcomes BEFORE controlled rejection; no unhandled rejection.
    const failureOutcome = failedWrite.then(
      () => ({ rejected: false as const }),
      (error: unknown) => ({ rejected: true as const, error }),
    )
    issued.push(failureOutcome)
    await drainNotifications()
    expect(captured).toHaveLength(2)
    expect(captured[1].workspaceId).toBe(workspace)
    expect(captured[1].body).toStrictEqual(failedDraft)
    expect(captured[1].currentAtIssuance).toBe(true)
    expect(captured[1].binding.isCurrent()).toBe(true)
    failureReply.reject(networkFailure)
    expect(await failureOutcome).toStrictEqual({ rejected: true, error: networkFailure })
    const outcome = await failureOutcome
    expect(outcome.rejected).toBe(true)
    if (outcome.rejected) expect(outcome.error).toBe(networkFailure)
    await store.whenIdle()
    await drainNotifications()
    const failedSnapshot = store.getSnapshot(workspace)
    const expectedRecovery = freezeFixture({
      attemptId: 'dashboard-attempt-2', actorId: actor, authGeneration: generation,
      workspaceId: workspace, epoch: 2,
      baseline: committed.layout, submitted: failedDraft,
      category: 'unknown', phase: 'needs-recheck', recheck: null,
    })
    // Exact legacy shape/content is established BEFORE the prospective oracle.
    expect(failedSnapshot).toStrictEqual({
      actorId: actor, authGeneration: generation, workspaceId: workspace,
      provenance: 'saved', writable: false, state: 'unknown', pending: false, readPending: false,
      canUndo: false, layout: committed.layout, openedBaseline: committed.layout,
      draft: failedDraft, recovery: expectedRecovery,
      overlay: { [workspace]: committed.layout },
      counters: { success: 1, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 },
    })
    expectDeepFrozen(failedSnapshot)
    expect(reads).toBe(2)
    expect(captured).toHaveLength(2)
    expect(observed.length).toBeGreaterThan(0)
    expect(observed[observed.length - 1]).toBe(failedSnapshot)
    const failedBefore = freezeFixture(structuredClone(failedSnapshot))
    const capture = failedSnapshot.recovery
    const publicationsBefore = observed.length

    // Intended runtime RED: missing public behavior, NOT collection or TypeError.
    const getPersistenceStatus = store.getPersistenceStatus
    expect(typeof getPersistenceStatus, 'native SessionStore.getPersistenceStatus must expose readonly persistence status').toBe('function')
    if (typeof getPersistenceStatus !== 'function') throw new Error('Missing native persistence status getter')
    const status = getPersistenceStatus.call(store, workspace)
    expect(status).toStrictEqual({
      pending: false, readPending: false, stale: false,
      unsaved: true, canCustomize: false, failureKind: 'unknown',
    })
    // Qualified committed display survives this failed save: NOT automatically stale.
    expect(Object.isFrozen(status)).toBe(true)
    expect(Reflect.ownKeys(status).sort()).toStrictEqual([
      'canCustomize', 'failureKind', 'pending', 'readPending', 'stale', 'unsaved',
    ])
    expect(getPersistenceStatus.call(store, workspace)).toBe(status)
    expect(store.getSnapshot(workspace)).toBe(failedSnapshot)
    expect(Object.getPrototypeOf(failedSnapshot)).toBe(Object.prototype)
    const legacyKeys = [
      'actorId', 'authGeneration', 'workspaceId', 'provenance', 'writable', 'state',
      'pending', 'readPending', 'canUndo', 'layout', 'openedBaseline', 'draft',
      'recovery', 'overlay', 'counters',
    ]
    expect(Reflect.ownKeys(failedSnapshot)).toStrictEqual(legacyKeys)
    expect(Object.keys(failedSnapshot)).toStrictEqual(legacyKeys)
    for (const key of legacyKeys) {
      expect(Object.getOwnPropertyDescriptor(failedSnapshot, key)?.enumerable).toBe(true)
    }

    // Readonly status and denied legacy actions must not grant recovery authority.
    expect(() => store.open(workspace)).toThrow('Dashboard layout session cannot open')
    expect(store.retain(workspace)).toBeNull()
    expect(() => store.setDraft(failedSession, firstDraft.items)).toThrow('Dashboard layout session is not editable')
    store.discard(failedSession)
    store.discard('never-issued-native-status-session')
    await store.whenIdle()
    await drainNotifications()
    expect(getPersistenceStatus.call(store, workspace)).toBe(status)
    expect(store.getSnapshot(workspace)).toBe(failedSnapshot)
    expect(store.getSnapshot(workspace)).toStrictEqual(failedBefore)
    expect(store.getSnapshot(workspace).recovery).toBe(capture)
    expect(observed).toHaveLength(publicationsBefore)
    expect(reads).toBe(2)
    expect(captured).toHaveLength(2)

    const inertExpected = {
      pending: false, readPending: false, stale: false,
      unsaved: false, canCustomize: false, failureKind: null,
    }
    // No foreign snapshot initialization: getter alone cannot materialize authority.
    const foreignStatus = getPersistenceStatus.call(store, foreignWorkspace)
    expect(foreignStatus).toStrictEqual(inertExpected)
    expect(Object.isFrozen(foreignStatus)).toBe(true)
    expect(getPersistenceStatus.call(store, foreignWorkspace)).toBe(foreignStatus)
    selectedWorkspace = foreignWorkspace
    const awayStatus = getPersistenceStatus.call(store, workspace)
    expect(awayStatus).toStrictEqual(inertExpected)
    expect(Object.isFrozen(awayStatus)).toBe(true)
    expect(getPersistenceStatus.call(store, workspace)).toBe(awayStatus)
    expect(() => store.open(workspace)).toThrow('Dashboard layout session cannot open')
    expect(store.getSnapshot(workspace)).toBe(failedSnapshot)
    selectedWorkspace = workspace
    const restoredStatus = getPersistenceStatus.call(store, workspace)
    expect(restoredStatus).toStrictEqual(status)
    expect(getPersistenceStatus.call(store, workspace)).toBe(restoredStatus)
    // Return selection need not reuse the pre-away status cache discriminator.
    await store.whenIdle()
    await drainNotifications()
    expect(store.getSnapshot(workspace)).toBe(failedSnapshot)
    expect(store.getSnapshot(workspace).recovery).toBe(capture)
    expect(store.getSnapshot(workspace)).toStrictEqual(failedBefore)
    expect(successfulSnapshot).toStrictEqual(successfulBefore)
    expect(successfulSnapshot.writable).toBe(true)
    expect(observed).toHaveLength(publicationsBefore)
    expect(reads).toBe(2)
    expect(captured).toHaveLength(2)
    expect(fixtures).toStrictEqual(fixturesBefore)
    expect(authSeed.preferences).toBe(seedPreferences)
    expect(DASHBOARD_WIDGETS).toStrictEqual(registryBefore)
  } catch (error: unknown) {
    primaryFailed = true
    primaryError = error
  } finally {
    // Settle every issued transport even when an earlier premise/oracle fails.
    selectedWorkspace = workspace
    successReply.resolve(committed)
    failureReply.resolve(committed)
    try { await Promise.allSettled(issued) } catch (error: unknown) { cleanupErrors.push(error) }
    try { await store.whenIdle(); await drainNotifications() } catch (error: unknown) { cleanupErrors.push(error) }
    try {
      expect(reads).toBe(2)
      expect(captured).toHaveLength(2)
      expect(fixtures).toStrictEqual(fixturesBefore)
      expect(DASHBOARD_WIDGETS).toStrictEqual(registryBefore)
    } catch (error: unknown) { cleanupErrors.push(error) }
    try { unsubscribe() } catch (error: unknown) { cleanupErrors.push(error) }
  }
  // Never throw in finally or substitute a teardown error for the body failure.
  if (primaryFailed) {
    if (cleanupErrors.length > 0) throw new AggregateError([primaryError, ...cleanupErrors], 'Native status primary failure with separate cleanup failures')
    throw primaryError
  }
  if (cleanupErrors.length > 0) throw new AggregateError(cleanupErrors, 'Native status cleanup failures')
})

it('retiring a qualified display read publishes stale status without replacing its legacy snapshot or allowing stale editing', async () => {
  const { actor, workspace, generation } = TEST_DATA
  const rawUser: User = freezeFixture({
    id: actor, email: 'retired-display@example.invalid',
    is_active: true, is_superuser: false, is_verified: true, is_2fa_enabled: false,
    preferences: { dashboard_layouts: { [workspace]: {
      version: 1, workspaceId: workspace, items: [
        { id: 'hero-balance', visible: true, order: 0 },
        { id: 'uncategorized-banner', visible: true, order: 1 },
        { id: 'spending-charts', visible: true, order: 2 },
        { id: 'balance-evolution', visible: true, order: 3 },
        { id: 'goals-progress', visible: true, order: 4 },
        { id: 'period-transactions', visible: true, order: 5 },
      ],
    } } },
  })
  const before = structuredClone(rawUser)
  const heldReply = deferred<User>()
  let reads = 0, writes = 0, publications = 0
  const store = createDashboardLayoutSession({
    registry: DASHBOARD_WIDGETS, actorId: actor, authGeneration: generation, authSeed: rawUser,
    getTokenIdentity: () => 'synthetic-retired-display-token', getWorkspaceId: () => workspace,
    api: {
      me: () => {
        reads += 1
        if (reads === 2) return heldReply.promise
        if (reads === 1 || reads === 3) return Promise.resolve(rawUser)
        return Promise.reject(new Error('Unexpected retired-display GET'))
      },
      saveLayout: () => { writes += 1; return Promise.reject(new Error('Forbidden retired-display write')) },
    },
  })
  const unsubscribe = store.subscribe(() => { publications += 1 })
  const heldTasks: Promise<unknown>[] = []
  let primary: unknown, primaryFailed = false
  const cleanup: unknown[] = []
  try {
    // The getter alone must not materialize an uninitialized record or publish.
    const inert = store.getPersistenceStatus(workspace)
    expect(inert).toStrictEqual({ pending: false, readPending: false, stale: false,
      unsaved: false, canCustomize: false, failureKind: null })
    expect(store.getPersistenceStatus(workspace)).toBe(inert)
    expect(reads).toBe(0); expect(publications).toBe(0)
    await store.refresh(workspace); await store.whenIdle(); await drainNotifications()
    expect(store.getPersistenceStatus(workspace)).toStrictEqual({ pending: false, readPending: false,
      stale: false, unsaved: false, canCustomize: true, failureKind: null })

    const held = store.refresh(workspace)
    const heldOutcome = held.then(() => ({ rejected: false, error: null }),
      (error: unknown) => ({ rejected: true, error }))
    heldTasks.push(heldOutcome)
    await drainNotifications()
    expect(reads).toBe(2)
    const heldSnapshot = store.getSnapshot(workspace)
    const heldValue = structuredClone(heldSnapshot)
    const heldStatus = store.getPersistenceStatus(workspace)
    expect(heldStatus).toStrictEqual({ pending: false, readPending: true, stale: false,
      unsaved: false, canCustomize: false, failureKind: null })
    const countBeforeRetirement = publications
    store.invalidateRecoveryReads()
    const retiredStatus = store.getPersistenceStatus(workspace)
    expect(retiredStatus).toStrictEqual({ ...heldStatus, stale: true })
    expect(retiredStatus).not.toBe(heldStatus); expect(Object.isFrozen(retiredStatus)).toBe(true)
    expect(store.getSnapshot(workspace)).toBe(heldSnapshot)
    expect(store.getSnapshot(workspace)).toStrictEqual(heldValue)
    await drainNotifications()
    expect(publications).toBe(countBeforeRetirement + 1)

    // The second private retirement is inert in both public projections.
    store.invalidateRecoveryReads()
    expect(store.getPersistenceStatus(workspace)).toBe(retiredStatus)
    expect(store.getSnapshot(workspace)).toBe(heldSnapshot)
    await drainNotifications()
    expect(publications).toBe(countBeforeRetirement + 1)
    expect(reads).toBe(2); expect(writes).toBe(0)
    heldReply.resolve(rawUser)
    const outcome = await heldOutcome
    expect(outcome.rejected).toBe(true)
    expect(outcome.error).toBeInstanceOf(Error)
    await store.whenIdle(); await drainNotifications()
    // A formerly qualified but retired read is not fresh editing authority.
    expect(store.getPersistenceStatus(workspace)).toStrictEqual({ pending: false, readPending: false,
      stale: true, unsaved: false, canCustomize: false, failureKind: null })
    expect(() => store.open(workspace)).toThrow('Dashboard layout session cannot open')
    expect(reads).toBe(2); expect(writes).toBe(0)

    // Explicit fresh qualification restores real admission, not blanket denial.
    await store.refresh(workspace); await store.whenIdle(); await drainNotifications()
    expect(store.getPersistenceStatus(workspace)).toStrictEqual({ pending: false, readPending: false,
      stale: false, unsaved: false, canCustomize: true, failureKind: null })
    const freshSession = store.open(workspace)
    expect(store.getSnapshot(workspace)).toMatchObject({ state: 'editing', writable: true })
    store.discard(freshSession)
    await store.whenIdle(); await drainNotifications()
    expect(store.getSnapshot(workspace)).toMatchObject({ state: 'closed', recovery: null,
      counters: { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 } })
    expect(reads).toBe(3); expect(writes).toBe(0); expect(rawUser).toStrictEqual(before)
  } catch (error: unknown) { primary = error; primaryFailed = true }
  finally {
    heldReply.resolve(rawUser)
    try { await Promise.allSettled(heldTasks); await store.whenIdle(); await drainNotifications() }
    catch (error: unknown) { cleanup.push(error) }
    try { expect(writes).toBe(0); expect(rawUser).toStrictEqual(before) }
    catch (error: unknown) { cleanup.push(error) }
    unsubscribe()
  }
  if (primaryFailed) {
    if (cleanup.length) throw new AggregateError([primary, ...cleanup], 'Retired display primary and cleanup failures')
    throw primary
  }
  if (cleanup.length) throw new AggregateError(cleanup, 'Retired display cleanup failed')
})
