import {
  resetLayout,
  resolveDashboardLayout,
  toPersistedLayout,
  validateCommittedDashboardLayout,
  validateDashboardLayoutRequest,
  validateStoredDashboardLayout,
  type CommittedDashboardLayout,
  type DashboardLayout,
  type DashboardLayoutItem,
  type DashboardRequestBinding,
  type DashboardWidgetSpec,
} from '@/lib/dashboard-layout'
import type { User } from '@/types'
import { isLivePageLeaseWitness } from '@/lib/dashboard-layout-page-session-lease'

export interface StoreOptions {
  registry: readonly DashboardWidgetSpec[]
  actorId: string
  authGeneration: number
  authSeed: Readonly<User>
  getTokenIdentity(): string | null
  getWorkspaceId(): string
  api: {
    me(): Promise<User>
    saveLayout(
      workspaceId: string,
      layout: DashboardLayout,
      binding: DashboardRequestBinding,
    ): Promise<CommittedDashboardLayout>
  }
}

interface Counters {
  success: number
  suppressed: number
  conflict409: number
  other4xx: number
  server5xx: number
}

type SessionState = 'closed' | 'editing' | 'pending' | 'closed-success' | 'blocked' | 'unknown' | 'conflict'
type Provenance = 'default' | 'saved' | 'foreign'

export interface SnapshotLayout {
  readonly version: 1
  readonly workspaceId: string
  readonly items: readonly Readonly<DashboardLayoutItem>[]
}

export interface RecoveryRead {
  readonly readId: number
  readonly provenance: Provenance
  readonly target: SnapshotLayout
  readonly matchesSubmitted: boolean
}

export interface Recovery {
  readonly attemptId: string
  readonly actorId: string
  readonly authGeneration: number
  readonly workspaceId: string
  readonly epoch: number
  readonly baseline: SnapshotLayout
  readonly submitted: SnapshotLayout
  readonly category: 'conflict' | 'unknown' | 'blocked' | 'correction-required'
  readonly phase: 'needs-recheck' | 'rechecking' | 'review-ready' | 'protected' | 'recheck-failed' | 'matched'
  readonly recheck: RecoveryRead | null
}

export interface SessionSnapshot {
  readonly actorId: string
  readonly authGeneration: number
  readonly workspaceId: string
  readonly provenance: Provenance
  readonly writable: boolean
  readonly state: SessionState
  readonly pending: boolean
  readonly readPending: boolean
  readonly canUndo: boolean
  readonly layout: SnapshotLayout
  readonly openedBaseline: SnapshotLayout | null
  readonly draft: SnapshotLayout | null
  readonly recovery: Recovery | null
  readonly overlay: Readonly<Record<string, SnapshotLayout>>
  readonly counters: Readonly<Counters>
}

export type PersistenceStatus = Readonly<{
  pending: boolean
  readPending: boolean
  stale: boolean
  unsaved: boolean
  canCustomize: boolean
  failureKind: Recovery['category'] | null
}>

export type RecoveryActionTicket = Readonly<{
  attemptId: string
  actorId: string
  authGeneration: number
  workspaceId: string
  epoch: number
  readId: number | null
}>

export type RecoveryPermissions = Readonly<{
  recheck: boolean
  reapply: boolean
  retry: boolean
  discard: boolean
}>

export type RecoveryReview = Readonly<{
  ticket: RecoveryActionTicket
  recovery: Recovery
  permissions: RecoveryPermissions
}>

export const INERT_PERSISTENCE_STATUS: PersistenceStatus = Object.freeze({
  pending: false, readPending: false, stale: false,
  unsaved: false, canCustomize: false, failureKind: null,
})

export interface SessionStore {
  revoke(): void
  refresh(workspaceId: string): Promise<void>
  recheckRecovery(workspaceId: string, attemptId: string): Promise<void>
  reviewRecovery(workspaceId: string, attemptId: string, witness: LivePageLeaseWitness): RecoveryReview
  recheckReviewedRecovery(ticket: RecoveryActionTicket): Promise<void>
  replaceRecovery(ticket: RecoveryActionTicket, action: 'reapply' | 'retry'): Promise<void>
  discardRecovery(ticket: RecoveryActionTicket): void
  invalidateRecoveryReads(): void
  open(workspaceId: string): string
  retain(workspaceId: string): string | null
  discard(sessionId: string): void
  setDraft(sessionId: string, items: readonly DashboardLayoutItem[]): void
  reset(sessionId: string): void
  undoReset(sessionId: string): void
  finalize(sessionId: string, pageWitness?: LivePageLeaseWitness): Promise<void>
  whenIdle(): Promise<void>
  getSnapshot(workspaceId: string): SessionSnapshot
  getPersistenceStatus(workspaceId: string): PersistenceStatus
  subscribe(listener: () => void): () => void
}

export interface LivePageLeaseWitness {
  readonly store: SessionStore
  readonly workspaceId: string
  readonly continuity: object
  isCurrent(): boolean
}

interface WorkspaceRecord {
  workspaceId: string
  layout: DashboardLayout
  provenance: Provenance
  writable: boolean
  stale: boolean
  admission: boolean
  state: SessionState
  epoch: number
  read: Promise<void> | null
  issuedTransportLatch: TransportLatch | null
  recoveryDedup: TransportLatch | null
  readPermission: ReadPermission | null
  session: EditSession | null
  recovery: Recovery | null
}

interface EditSession {
  id: string
  record: WorkspaceRecord
  epoch: number
  baseline: DashboardLayout
  draft: DashboardLayout
  undo: DashboardLayout | null
  completion: Promise<void> | null
  captureAttempt: Recovery | null
  conflictRecheck: Promise<void> | null
}

interface TransportLatch {
  kind: 'ordinary' | 'recovery'
  readId: number
  attemptId: string | null
  task: Promise<void>
  retired: boolean
  owner: Readonly<{ actorId: string; authGeneration: number }>
  record: WorkspaceRecord
  session: EditSession | null
  epoch: number
  capture: Recovery | null
}

type ReadPermission = Readonly<{
  readId: number
  attemptId: string
  actorId: string
  authGeneration: number
  workspaceId: string
  epoch: number
  target: SnapshotLayout
}>

interface ReviewAuthority {
  record: WorkspaceRecord
  session: EditSession
  capture: Recovery
  permission: ReadPermission | null
  readSerial: number
  witness: LivePageLeaseWitness
  continuity: object
  initiatingRead: TransportLatch | null
}

function isObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null) return false
  const prototype: unknown = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function owns(value: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key)
}

function httpErrorStatus(error: unknown): number | undefined {
  try {
    if (typeof error !== 'object' || error === null) return undefined
    const failure = error as Record<string, unknown>
    const response = failure.response
    const status = (typeof response === 'object' && response !== null ?
      (response as Record<string, unknown>).status : undefined) ?? failure.status
    return typeof status === 'number' && Number.isInteger(status) ? status : undefined
  } catch {
    // Unreadable error fields must not replace the original rejection.
    return undefined
  }
}

function copyLayout(layout: DashboardLayout): DashboardLayout {
  return { version: 1, workspaceId: layout.workspaceId, items: layout.items.map((item) => ({ ...item })) }
}

function freezeLayout(layout: DashboardLayout): SnapshotLayout {
  return Object.freeze({
    version: 1, workspaceId: layout.workspaceId,
    items: Object.freeze(layout.items.map((item) => Object.freeze({ ...item }))),
  })
}

function layoutContent(layout: DashboardLayout | null) {
  return layout && [layout.version, layout.workspaceId,
    layout.items.map((item) => [item.id, item.visible, item.order])]
}

/** Minimum, unmounted provider-lifetime foundation; no auth/cache replacement. */
export function createDashboardLayoutSession(options: StoreOptions): SessionStore {
  const { actorId, authGeneration } = options
  const registry = options.registry.map((spec) => ({ ...spec }))
  // The token exists only in this private closure, never a session or snapshot.
  const tokenIdentity = options.getTokenIdentity()
  const seedMatches = options.authSeed.id === actorId
  let preferences: User['preferences'] | undefined
  try {
    preferences = seedMatches ? options.authSeed.preferences : undefined
  } catch {
    // An unreadable Auth seed displays defaults without establishing writability.
    preferences = undefined
  }
  let authoritative = false
  let revoked = false
  let nextSession = 0
  let nextAttempt = 0
  let nextReadId = 0
  let latestReadId = 0
  const readOwner = Object.freeze({ actorId, authGeneration })
  const records = new Map<string, WorkspaceRecord>()
  const sessions = new Map<string, EditSession>()
  const reviewTickets = new WeakMap<RecoveryActionTicket, ReviewAuthority>()
  const overlay = new Map<string, DashboardLayout>()
  const tasks = new Set<Promise<void>>()
  const counters: Counters = { success: 0, suppressed: 0, conflict409: 0, other4xx: 0, server5xx: 0 }
  const registrations = new Set<{ listener: () => void }>()
  const snapshotCache = new Map<string, { revision: number; owner: boolean; snapshot: SessionSnapshot }>()
  const observedRecords = new Map<string, string>()
  const observedPersistence = new Map<string, string>()
  const persistenceCache = new Map<string, { revision: number; content: string; status: PersistenceStatus }>()
  let persistenceRevision = 0
  let revision = 0
  let scheduled = false
  let observedOwner = ownerCurrent()
  let observedGlobals = globalContent()

  function ownerCurrent(): boolean {
    return !revoked && seedMatches && tokenIdentity !== null && options.getTokenIdentity() === tokenIdentity
  }

  function recordContent(record: WorkspaceRecord, owner: boolean): string {
    return JSON.stringify([
      record.provenance, record.writable && owner, record.state, record.read !== null,
      layoutContent(record.layout), layoutContent(record.session?.baseline ?? null),
      layoutContent(record.session?.draft ?? null),
      owner && record.recovery?.epoch === record.epoch ? record.recovery : null,
      canUndo(record),
    ])
  }

  function globalContent(): string {
    return JSON.stringify([
      [...overlay.keys()].sort().map((key) => [key, layoutContent(overlay.get(key)!)]),
      counters.success, counters.suppressed, counters.conflict409, counters.other4xx, counters.server5xx,
    ])
  }

  function persistenceFor(record: WorkspaceRecord): PersistenceStatus {
    if (!ownerCurrent() || options.getWorkspaceId() !== record.workspaceId) return INERT_PERSISTENCE_STATUS
    const recovery = record.recovery?.epoch === record.epoch ? record.recovery : null
    const session = record.session
    const editing = session !== null && sessionEditable(session)
    return {
      pending: record.state === 'pending', readPending: record.read !== null,
      stale: record.stale,
      unsaved: recovery !== null || record.state === 'pending' ||
        (session !== null && JSON.stringify(layoutContent(session.draft)) !== JSON.stringify(layoutContent(session.baseline))),
      canCustomize: record.writable && !record.stale && !record.admission && record.read === null && recovery === null &&
        (record.state === 'closed' || record.state === 'closed-success' || editing),
      failureKind: recovery?.category ?? null,
    }
  }

  // Compare only public content and the live owner discriminator, never latch identity.
  // Called outside API classification catches; getters do not commit or publish.
  function invalidate(): boolean {
    const owner = ownerCurrent()
    const globals = globalContent()
    let changed = owner !== observedOwner || globals !== observedGlobals
    let statusChanged = false
    observedOwner = owner
    observedGlobals = globals
    for (const [key, record] of records) {
      const content = recordContent(record, owner)
      if (content !== observedRecords.get(key)) changed = true
      observedRecords.set(key, content)
      const statusContent = JSON.stringify(persistenceFor(record))
      if (statusContent !== observedPersistence.get(key)) statusChanged = true
      observedPersistence.set(key, statusContent)
    }
    if (changed) revision += 1
    if (statusChanged) persistenceRevision += 1
    return changed || statusChanged
  }

  function schedulePublication(changed: boolean): void {
    if (!changed || scheduled) return
    scheduled = true
    queueMicrotask(() => {
      scheduled = false
      const captured = [...registrations]
      for (const registration of captured) {
        if (!registrations.has(registration)) continue
        try {
          registration.listener()
        } catch {
          // Observer failures never affect API outcomes or other observers.
        }
      }
    })
  }

  function subscribe(listener: () => void): () => void {
    const registration = { listener }
    registrations.add(registration)
    return () => { registrations.delete(registration) }
  }

  function revoke(): void {
    if (revoked) return
    revoked = true
    retireRecoveryReads()
    preferences = undefined
    authoritative = false
    overlay.clear()
    snapshotCache.clear()
    persistenceCache.clear()
    for (const session of sessions.values()) {
      session.undo = null
      session.captureAttempt = null
    }
    sessions.clear()
    for (const record of records.values()) {
      record.epoch += 1
      record.layout = resolveDashboardLayout(registry, undefined, record.workspaceId)
      record.provenance = 'default'
      record.writable = false
      record.stale = true
      record.state = 'closed'
      record.session = null
      record.recovery = null
    }
    // Issued/queued work still settles through track(); revocation is not HTTP abort.
    schedulePublication(invalidate())
  }

  function track(task: Promise<void>): Promise<void> {
    tasks.add(task)
    // Both handlers settle bookkeeping without introducing an unhandled rejection.
    void task.then(() => { tasks.delete(task) }, () => { tasks.delete(task) })
    return task
  }

  function readLeaf(workspaceId: string, source: User['preferences'] | undefined): {
    layout: DashboardLayout
    provenance: Provenance
    valid: boolean
  } {
    let raw: unknown
    let valid = seedMatches
    let present = false
    // A null root is ambiguous on GET; the server retains the final write guard.
    if (source !== null && source !== undefined) {
      if (!isObject(source)) valid = false
      else if (owns(source, 'dashboard_layouts')) {
        const map = source.dashboard_layouts
        if (!isObject(map)) valid = false
        else if (owns(map, workspaceId)) {
          present = true
          raw = map[workspaceId]
        }
      }
    }
    const layout = resolveDashboardLayout(registry, raw, workspaceId)
    try {
      // Strict reserved fields establish writability; display repair never does.
      validateStoredDashboardLayout(workspaceId, present ? raw : layout, registry)
    } catch {
      valid = false
    }
    return { layout, valid, provenance: valid ? (present ? 'saved' : 'default') : 'foreign' }
  }

  function recordFor(workspaceId: string): WorkspaceRecord {
    let record = records.get(workspaceId)
    if (!record) {
      const leaf = readLeaf(workspaceId, preferences)
      record = {
        workspaceId, layout: leaf.layout, provenance: leaf.provenance,
        writable: authoritative && leaf.valid, state: 'closed', epoch: 0,
        stale: !authoritative || !leaf.valid,
        admission: false,
        read: null, issuedTransportLatch: null, recoveryDedup: null, readPermission: null,
        session: null, recovery: null,
      }
      records.set(workspaceId, record)
      // Materializing a logical default is not an observable store transition.
      observedRecords.set(workspaceId, recordContent(record, ownerCurrent()))
      observedPersistence.set(workspaceId, JSON.stringify(persistenceFor(record)))
    }
    return record
  }

  function retireRecoveryReads(): void {
    latestReadId = ++nextReadId
    for (const record of records.values()) {
      if (record.issuedTransportLatch) {
        if (record.issuedTransportLatch.kind === 'ordinary' && !record.issuedTransportLatch.retired) record.stale = true
        record.issuedTransportLatch.retired = true
      }
      record.recoveryDedup = null
      record.readPermission = null
      const capture = record.recovery
      if (capture && (capture.recheck !== null || capture.phase === 'rechecking' ||
          capture.phase === 'review-ready' || capture.phase === 'matched')) {
        record.recovery = Object.freeze({ ...capture, phase: 'needs-recheck', recheck: null })
      }
    }
  }

  function invalidateRecoveryReads(): void {
    retireRecoveryReads()
    schedulePublication(invalidate())
  }

  function qualifyRecoveryRead(user: unknown, workspaceId: string): {
    provenance: Provenance
    target: SnapshotLayout | null
  } {
    try {
      if (!isObject(user) || !owns(user, 'id') || user.id !== actorId ||
          !owns(user, 'preferences')) {
        return { provenance: 'foreign', target: null }
      }
      const source = user.preferences
      if (!isObject(source)) return { provenance: 'foreign', target: null }
      let raw: unknown = resetLayout(registry, workspaceId)
      let present = false
      if (owns(source, 'dashboard_layouts')) {
        const map = source.dashboard_layouts
        if (!isObject(map)) return { provenance: 'foreign', target: null }
        if (owns(map, workspaceId)) {
          raw = map[workspaceId]
          present = true
        }
      }
      // Qualify the raw reserved fields before any display normalization.
      const validated = validateStoredDashboardLayout(workspaceId, raw, registry)
      const target = freezeLayout(resolveDashboardLayout(registry, validated, workspaceId))
      return { provenance: present ? 'saved' : 'default', target }
    } catch {
      return { provenance: 'foreign', target: null }
    }
  }

  function recoveryEligible(record: WorkspaceRecord, attemptId: string): boolean {
    const capture = record.recovery
    const session = record.session
    return ownerCurrent() && options.getWorkspaceId() === record.workspaceId &&
      records.get(record.workspaceId) === record && capture !== null &&
      capture.attemptId === attemptId && capture.actorId === actorId &&
      capture.authGeneration === authGeneration && capture.workspaceId === record.workspaceId &&
      capture.epoch === record.epoch && session !== null && session.record === record &&
      sessions.get(session.id) === session && session.completion !== null &&
      session.captureAttempt === null &&
      ((record.state === 'unknown' && capture.category === 'unknown') ||
        (record.state === 'conflict' && capture.category === 'conflict'))
  }

  function readCurrent(latch: TransportLatch): boolean {
    const { record, session, capture } = latch
    return ownerCurrent() && latch.owner === readOwner &&
      options.getWorkspaceId() === record.workspaceId && records.get(record.workspaceId) === record &&
      record.issuedTransportLatch === latch && record.read === latch.task && !latch.retired &&
      latestReadId === latch.readId && record.epoch === latch.epoch &&
      record.session === session && record.recovery === capture &&
      (session === null || (sessions.get(session.id) === session && session.record === record))
  }

  function issueRead(record: WorkspaceRecord, kind: TransportLatch['kind'], reviewAuthority?: ReviewAuthority): Promise<void> {
    if (kind === 'ordinary') retireRecoveryReads()
    else {
      record.readPermission = null
      record.recovery = Object.freeze({ ...record.recovery!, phase: 'rechecking', recheck: null })
    }
    // Capture after our own retirement/phase transition, including nullable ordinary identities.
    const capture = record.recovery
    let resolveRead!: () => void
    let rejectRead!: (reason: unknown) => void
    const task = new Promise<void>((resolve, reject) => {
      resolveRead = resolve
      rejectRead = reject
    })
    const latch: TransportLatch = {
      kind, readId: ++nextReadId, attemptId: capture?.attemptId ?? null,
      task, retired: false, owner: readOwner, record, session: record.session,
      epoch: record.epoch, capture,
    }
    latestReadId = latch.readId
    record.issuedTransportLatch = latch
    record.recoveryDedup = kind === 'recovery' ? latch : null
    record.read = task
    if (reviewAuthority) reviewAuthority.initiatingRead = latch
    track(task)
    const changed = invalidate()
    // Install both authority and drain latches before synchronous transport reentry.
    void (async () => {
      try {
        const user = await options.api.me()
        if (!readCurrent(latch)) throw new Error('Dashboard layout read is stale')
        if (kind === 'recovery') {
          const qualified = qualifyRecoveryRead(user, record.workspaceId)
          if (!readCurrent(latch)) throw new Error('Dashboard layout read is stale')
          record.writable = false
          if (qualified.target === null) {
            record.readPermission = null
            record.recovery = Object.freeze({ ...capture!, phase: 'protected', recheck: null })
          } else {
            const target = qualified.target
            const matchesSubmitted = JSON.stringify(target) === JSON.stringify(capture!.submitted)
            record.readPermission = Object.freeze({
              readId: latch.readId, attemptId: capture!.attemptId, actorId, authGeneration,
              workspaceId: record.workspaceId, epoch: latch.epoch, target,
            })
            const recheck: RecoveryRead = Object.freeze({
              readId: latch.readId, provenance: qualified.provenance, target, matchesSubmitted,
            })
            record.recovery = Object.freeze({
              ...capture!, phase: matchesSubmitted ? 'matched' : 'review-ready', recheck,
            })
          }
        } else {
          if (user.id !== actorId) throw new Error('Dashboard layout read owner is not current')
          const source = user.preferences
          const leaves = [...records.values()].map((entry) => ({ entry, leaf: readLeaf(entry.workspaceId, source) }))
          if (!readCurrent(latch)) throw new Error('Dashboard layout read is stale')
          preferences = source
          authoritative = true
          for (const { entry, leaf } of leaves) {
            entry.layout = leaf.layout
            entry.provenance = leaf.provenance
            entry.writable = leaf.valid
            entry.stale = !leaf.valid
            // Ordinary display refresh never grants recovery read permission.
          }
        }
      } catch {
        if (!readCurrent(latch)) throw new Error('Dashboard layout read is stale')
        record.writable = false
        record.readPermission = null
        if (kind === 'ordinary') record.stale = true
        if (capture && capture.epoch === latch.epoch) {
          record.recovery = Object.freeze({ ...capture, phase: 'recheck-failed', recheck: null })
        }
        throw new Error('Dashboard layout refresh did not establish a current writable target')
      } finally {
        // Retirement never drops issued work; only its own settlement clears the slot.
        if (record.issuedTransportLatch === latch) record.issuedTransportLatch = null
        if (record.recoveryDedup === latch) record.recoveryDedup = null
        if (record.read === task) record.read = null
        schedulePublication(invalidate())
      }
    })().then(resolveRead, rejectRead)
    schedulePublication(changed)
    return task
  }

  function refresh(workspaceId: string): Promise<void> {
    if (!ownerCurrent() || options.getWorkspaceId() !== workspaceId) {
      return Promise.reject(new Error('Dashboard layout read owner is not current'))
    }
    const record = recordFor(workspaceId)
    if (record.admission) return Promise.reject(new Error('Dashboard replacement admission is pending'))
    if (records.get(workspaceId) !== record) {
      return Promise.reject(new Error('Dashboard layout read owner is not current'))
    }
    if (record.read) {
      return record.issuedTransportLatch?.task === record.read ? record.read :
        Promise.reject(new Error('Dashboard layout read owner is not current'))
    }
    return issueRead(record, 'ordinary')
  }

  function recheckRecovery(workspaceId: string, attemptId: string): Promise<void> {
    const record = records.get(workspaceId)
    if (!record || record.admission || !recoveryEligible(record, attemptId)) {
      return Promise.reject(new Error('Dashboard recovery read unavailable'))
    }
    const latch = record.recoveryDedup
    if (latch && latch.kind === 'recovery' && latch.attemptId === attemptId && readCurrent(latch)) {
      return latch.task
    }
    if (record.read || record.issuedTransportLatch) {
      return Promise.reject(new Error('Dashboard recovery read unavailable'))
    }
    return issueRead(record, 'recovery')
  }

  function reviewRecovery(workspaceId: string, attemptId: string, witness: LivePageLeaseWitness): RecoveryReview {
    const record = records.get(workspaceId)
    const session = record?.session
    const capture = record?.recovery
    if (!record || !session || !capture || record.admission || !ownerCurrent() || options.getWorkspaceId() !== workspaceId ||
        !isLivePageLeaseWitness(witness, store, workspaceId) ||
        records.get(workspaceId) !== record || sessions.get(session.id) !== session || session.record !== record ||
        !session.completion || session.captureAttempt !== null || record.state === 'pending' ||
        capture.attemptId !== attemptId || capture.actorId !== actorId || capture.authGeneration !== authGeneration ||
        capture.workspaceId !== workspaceId || capture.epoch !== record.epoch) {
      throw new Error('Dashboard recovery review unavailable')
    }
    const permission = record.readPermission
    const idle = record.read === null && record.issuedTransportLatch === null
    const qualified = idle && capture.phase === 'review-ready' && capture.recheck !== null &&
      !capture.recheck.matchesSubmitted && permission !== null &&
      permission.readId === latestReadId && permission.readId === capture.recheck.readId &&
      permission.attemptId === attemptId && permission.actorId === actorId &&
      permission.authGeneration === authGeneration && permission.workspaceId === workspaceId &&
      permission.epoch === record.epoch && permission.target === capture.recheck.target
    const ticket: RecoveryActionTicket = Object.freeze({
      attemptId, actorId, authGeneration, workspaceId, epoch: record.epoch,
      readId: permission?.readId ?? null,
    })
    reviewTickets.set(ticket, {
      record, session, capture, permission, readSerial: latestReadId,
      witness, continuity: witness.continuity,
      initiatingRead: null,
    })
    return Object.freeze({
      ticket, recovery: capture,
      permissions: Object.freeze({
        recheck: idle, discard: idle,
        reapply: qualified && capture.category === 'conflict',
        retry: qualified && capture.category === 'unknown',
      }),
    })
  }

  function reviewOwnerCurrent(ticket: RecoveryActionTicket, authority: ReviewAuthority): boolean {
    const { record, session, capture, witness, continuity } = authority
    return ownerCurrent() && options.getWorkspaceId() === record.workspaceId &&
      records.get(record.workspaceId) === record && record.session === session &&
      sessions.get(session.id) === session && session.record === record &&
      session.completion !== null && session.captureAttempt === null && !record.admission && record.state !== 'pending' &&
      record.epoch === capture.epoch && ticket.epoch === capture.epoch &&
      ticket.actorId === actorId && ticket.authGeneration === authGeneration &&
      ticket.workspaceId === record.workspaceId && ticket.attemptId === capture.attemptId &&
      witness.continuity === continuity && isLivePageLeaseWitness(witness, store, record.workspaceId)
  }

  function recheckReviewedRecovery(ticket: RecoveryActionTicket): Promise<void> {
    const authority = reviewTickets.get(ticket)
    if (!authority || !reviewOwnerCurrent(ticket, authority)) {
      return Promise.reject(new Error('Dashboard reviewed recovery read unavailable'))
    }
    const { record } = authority
    const initiating = authority.initiatingRead
    // Only this initiating ticket may deduplicate its own phase transition. A
    // ticket minted while occupied cannot adopt somebody else's issued work.
    if (initiating) {
      return record.recoveryDedup === initiating && readCurrent(initiating) ? initiating.task :
        Promise.reject(new Error('Dashboard reviewed recovery read unavailable'))
    }
    if (record.recovery !== authority.capture || record.readPermission !== authority.permission ||
        latestReadId !== authority.readSerial || record.read || record.issuedTransportLatch) {
      return Promise.reject(new Error('Dashboard reviewed recovery read unavailable'))
    }
    return issueRead(record, 'recovery', authority)
  }

  function replaceRecovery(ticket: RecoveryActionTicket, action: 'reapply' | 'retry'): Promise<void> {
    const authority = reviewTickets.get(ticket)
    if (!authority || !reviewOwnerCurrent(ticket, authority)) {
      return Promise.reject(new Error('Dashboard recovery replacement unavailable'))
    }
    const { record, session: oldSession, capture, permission } = authority
    if ((action !== 'reapply' && action !== 'retry') ||
        (action === 'reapply' ? capture.category !== 'conflict' : capture.category !== 'unknown') ||
        record.recovery !== capture || record.readPermission !== permission || latestReadId !== authority.readSerial ||
        record.read || record.issuedTransportLatch || !permission || permission.readId !== latestReadId ||
        capture.phase !== 'review-ready' || !capture.recheck || capture.recheck.matchesSubmitted ||
        capture.recheck.readId !== permission.readId || capture.recheck.target !== permission.target) {
      return Promise.reject(new Error('Dashboard recovery replacement unavailable'))
    }
    let baseline: DashboardLayout
    let draft: DashboardLayout
    try {
      baseline = validateDashboardLayoutRequest(record.workspaceId, copyLayout({
        version: 1, workspaceId: record.workspaceId, items: permission.target.items.map((item) => ({ ...item })),
      }), registry)
      draft = validateDashboardLayoutRequest(record.workspaceId, copyLayout({
        version: 1, workspaceId: record.workspaceId, items: capture.submitted.items.map((item) => ({ ...item })),
      }), registry)
    } catch (error: unknown) {
      return Promise.reject(error)
    }
    if (JSON.stringify(baseline) === JSON.stringify(draft) || !reviewOwnerCurrent(ticket, authority)) {
      return Promise.reject(new Error('Dashboard recovery replacement unavailable'))
    }
    // No callback, await, publication or transport occurs between this final
    // authority check and installation of the shared finalization latch.
    const preimage = { session: record.session, recovery: record.recovery, permission: record.readPermission,
      dedup: record.recoveryDedup, writable: record.writable, stale: record.stale, state: record.state,
      epoch: record.epoch, undo: oldSession.undo, nextSession, nextAttempt }
    const session: EditSession = {
      id: `dashboard-session-${nextSession + 1}`, record, epoch: record.epoch,
      baseline, draft, undo: null, completion: null, captureAttempt: null, conflictRecheck: null,
    }
    let task: Promise<void>
    record.admission = true
    try {
      record.readPermission = null
      record.recoveryDedup = null
      record.recovery = null
      oldSession.undo = null
      sessions.delete(oldSession.id)
      nextSession += 1
      sessions.set(session.id, session)
      record.session = session
      record.state = 'editing'
      record.writable = true
      record.stale = false
      task = finalizeValidated(session, authority.witness, false)
      reviewTickets.delete(ticket)
    } catch (error: unknown) {
      sessions.delete(session.id)
      sessions.set(oldSession.id, oldSession)
      record.session = preimage.session
      record.recovery = preimage.recovery
      record.readPermission = preimage.permission
      record.recoveryDedup = preimage.dedup
      record.writable = preimage.writable
      record.stale = preimage.stale
      record.state = preimage.state
      record.epoch = preimage.epoch
      oldSession.undo = preimage.undo
      nextSession = preimage.nextSession
      nextAttempt = preimage.nextAttempt
      return Promise.reject(error)
    } finally {
      record.admission = false
    }
    schedulePublication(invalidate())
    return task
  }

  function discardRecovery(ticket: RecoveryActionTicket): void {
    const authority = reviewTickets.get(ticket)
    if (!authority || !reviewOwnerCurrent(ticket, authority)) return
    const { record, session, capture, permission } = authority
    if (record.recovery !== capture || record.readPermission !== permission ||
        latestReadId !== authority.readSerial || record.read || record.issuedTransportLatch) return
    const qualified = permission !== null && permission.readId === latestReadId &&
      capture.recheck !== null && capture.recheck.readId === permission.readId &&
      capture.recheck.target === permission.target
    // Prepare before consuming anything. A comparison read is not a PUT overlay
    // and is promoted only while its exact private qualification is current.
    const target = qualified ? copyLayout({
      version: 1, workspaceId: record.workspaceId, items: permission.target.items.map((item) => ({ ...item })),
    }) : null
    session.undo = null
    record.readPermission = null
    record.recoveryDedup = null
    record.epoch += 1
    record.session = null
    record.recovery = null
    record.state = 'closed'
    sessions.delete(session.id)
    reviewTickets.delete(ticket)
    if (target) {
      record.layout = target
      record.provenance = capture.recheck!.provenance
      record.writable = true
      record.stale = false
      overlay.delete(record.workspaceId)
    } else {
      record.writable = false
      record.stale = true
    }
    schedulePublication(invalidate())
  }

  function open(workspaceId: string): string {
    const record = recordFor(workspaceId)
    if (record.admission || !ownerCurrent() || options.getWorkspaceId() !== workspaceId || !record.writable || record.stale || record.read || record.recovery ||
        (record.state !== 'closed' && record.state !== 'closed-success')) {
      throw new Error('Dashboard layout session cannot open')
    }
    const baseline = toPersistedLayout(workspaceId, record.layout.items, registry)
    const session: EditSession = {
      id: `dashboard-session-${++nextSession}`, record, epoch: record.epoch,
      baseline, draft: copyLayout(baseline), undo: null, completion: null, captureAttempt: null,
      conflictRecheck: null,
    }
    record.session = session
    record.state = 'editing'
    sessions.set(session.id, session)
    schedulePublication(invalidate())
    return session.id
  }

  function retain(workspaceId: string): string | null {
    const record = records.get(workspaceId)
    const session = record?.session
    if (!record || record.admission || !session || sessions.get(session.id) !== session || session.record !== record ||
        !ownerCurrent() || options.getWorkspaceId() !== workspaceId || !record.writable) return null
    // Reads do not replace the captured edit; finalize advances the record epoch.
    if (record.state === 'editing' && !record.stale && !session.completion && record.epoch === session.epoch) return session.id
    if (record.state === 'pending' && session.completion) return session.id
    return null
  }

  function discard(sessionId: string): void {
    const session = sessions.get(sessionId)
    if (!session || session.record.admission || session.completion || session.record.session !== session ||
        session.record.state !== 'editing' || session.record.epoch !== session.epoch) return
    const record = session.record
    // Invalidate captured reads without aborting transport or dropping its drain latch.
    session.undo = null
    record.epoch += 1
    record.session = null
    record.state = 'closed'
    sessions.delete(sessionId)
    schedulePublication(invalidate())
  }

  function sessionEditable(session: EditSession): boolean {
    return !session.record.admission && sessions.get(session.id) === session && !session.completion && session.record.session === session &&
      session.record.state === 'editing' && ownerCurrent() && options.getWorkspaceId() === session.record.workspaceId &&
      session.record.writable && !session.record.stale && !session.record.read && session.record.epoch === session.epoch
  }

  function canUndo(record: WorkspaceRecord): boolean {
    const session = record.session
    return session !== null && session.undo !== null && sessionEditable(session)
  }

  function editableSession(sessionId: string): EditSession {
    const session = sessions.get(sessionId)
    if (!session || !sessionEditable(session)) {
      throw new Error('Dashboard layout session is not editable')
    }
    return session
  }

  function setDraft(sessionId: string, items: readonly DashboardLayoutItem[]): void {
    const session = editableSession(sessionId)
    const draft = toPersistedLayout(session.record.workspaceId, items, registry)
    session.draft = validateDashboardLayoutRequest(session.record.workspaceId, draft, registry)
    schedulePublication(invalidate())
  }

  function reset(sessionId: string): void {
    const session = editableSession(sessionId)
    const workspaceId = session.record.workspaceId
    const draft = validateDashboardLayoutRequest(workspaceId, resetLayout(registry, workspaceId), registry)
    const undo = toPersistedLayout(workspaceId, session.draft.items, registry)
    session.undo = undo
    session.draft = draft
    schedulePublication(invalidate())
  }

  function undoReset(sessionId: string): void {
    const session = editableSession(sessionId)
    if (session.undo === null) return
    session.draft = copyLayout(session.undo)
    session.undo = null
    schedulePublication(invalidate())
  }

  function countFailure(status: number | undefined): void {
    if (status === undefined) return
    if (status === 409) counters.conflict409 += 1
    else if (status >= 400 && status < 500) counters.other4xx += 1
    else if (status >= 500 && status < 600) counters.server5xx += 1
  }

  function queueConflictRecheck(
    record: WorkspaceRecord,
    session: EditSession,
    capture: Recovery,
    readSerial: number,
    pageWitness: LivePageLeaseWitness,
    continuity: object,
    writeTask: Promise<void>,
  ): void {
    if (session.conflictRecheck) return
    function current(): boolean {
      try {
        return pageWitness.continuity === continuity &&
          isLivePageLeaseWitness(pageWitness, store, record.workspaceId) &&
          latestReadId === readSerial && record.session === session &&
          session.completion === writeTask && record.recovery === capture &&
          recoveryEligible(record, capture.attemptId)
      } catch {
        // Unreadable page authority denies recovery, never the original rejection.
        return false
      }
    }
    // This drain observes the original writer; the writer never awaits the drain.
    const kickoff = writeTask.then(() => {}, async () => {
      if (!current()) return
      const recoveryLatch = record.recoveryDedup
      if (recoveryLatch && recoveryLatch.kind === 'recovery' &&
          recoveryLatch.attemptId === capture.attemptId && readCurrent(recoveryLatch)) {
        await store.recheckRecovery(record.workspaceId, capture.attemptId)
        return
      }
      const occupiedRead = record.read
      const occupiedLatch = record.issuedTransportLatch
      if (occupiedRead || occupiedLatch) {
        if (!occupiedRead || !occupiedLatch || occupiedLatch.task !== occupiedRead) return
        // Observe one exact occupied transport, including retired reads, without retrying.
        await occupiedRead.then(() => {}, () => {})
        if (!current() || record.read !== null || record.issuedTransportLatch !== null) return
      }
      if (!current()) return
      await store.recheckRecovery(record.workspaceId, capture.attemptId)
    })
    session.conflictRecheck = track(kickoff)
  }

  function finalize(sessionId: string, pageWitness?: LivePageLeaseWitness): Promise<void> {
    const existing = sessions.get(sessionId)
    if (existing?.record.admission) return Promise.reject(new Error('Dashboard replacement admission is pending'))
    if (existing?.completion) return existing.completion
    let session: EditSession
    try {
      session = editableSession(sessionId)
    } catch {
      return Promise.reject(new Error('Dashboard layout session cannot finalize'))
    }
    return finalizeValidated(session, pageWitness)
  }

  // Public finalize has checked current owner/selection and editable authority.
  // Replacement checks them immediately before its callback-free transaction;
  // this core bypasses only that private admission lock, never session identity.
  function finalizeValidated(session: EditSession, pageWitness?: LivePageLeaseWitness, publish = true): Promise<void> {
    const record = session.record
    if (sessions.get(session.id) !== session || record.session !== session || session.completion ||
        record.state !== 'editing' || !record.writable || record.stale || record.read ||
        record.recovery || record.epoch !== session.epoch) {
      throw new Error('Dashboard finalization transition invariant failed')
    }
    const submitted = copyLayout(session.draft)
    session.undo = null
    if (JSON.stringify(submitted) === JSON.stringify(session.baseline)) {
      counters.suppressed += 1
      record.state = 'closed'
      record.session = null
      const task = Promise.resolve()
      session.completion = task
      schedulePublication(invalidate())
      return task
    }
    const epoch = ++record.epoch
    const readSerial = latestReadId
    let liveWitness: LivePageLeaseWitness | null = null
    let continuity: object | null = null
    function electPageWitness(): void {
      try {
        if (pageWitness && isLivePageLeaseWitness(pageWitness, store, record.workspaceId)) {
          liveWitness = pageWitness
          continuity = pageWitness.continuity
        }
      } catch {
        // Missing/unreadable page authority denies only automatic recovery, not the PUT.
      }
    }
    if (publish) electPageWitness()
    // Copy/freeze before dispatch; this private candidate is not yet a failure.
    session.captureAttempt = Object.freeze({
      attemptId: `dashboard-attempt-${++nextAttempt}`, actorId, authGeneration,
      workspaceId: record.workspaceId, epoch,
      baseline: freezeLayout(session.baseline), submitted: freezeLayout(submitted),
      category: 'unknown', phase: 'needs-recheck', recheck: null,
    })
    record.state = 'pending'
    const binding: DashboardRequestBinding = {
      isCurrent: () => ownerCurrent() && options.getWorkspaceId() === record.workspaceId &&
        record.epoch === epoch && record.session === session,
    }
    // Selection gates dispatch, but an issued outcome belongs to its exact live
    // record/session/epoch and task. Revocation or replacement retires that owner.
    function completionCurrent(): boolean {
      return ownerCurrent() && records.get(record.workspaceId) === record &&
        sessions.get(session.id) === session && session.record === record &&
        record.session === session && record.epoch === epoch && session.completion === task
    }
    // Latch before invoking even a synchronously reentrant API boundary.
    const task: Promise<void> = Promise.resolve().then(async () => {
      // Replacement admission stays callback-free; native authority is still
      // elected after the transaction, never bypassed by its publication flag.
      if (!publish) electPageWitness()
      let changed = false
      let issued = false
      let validated = false
      let conflictCapture: Recovery | null = null
      try {
        try {
          if (!binding.isCurrent()) throw new Error('Dashboard layout write owner is not current')
          issued = true
          const response = await options.api.saveLayout(record.workspaceId, copyLayout(submitted), binding)
          const committed = validateCommittedDashboardLayout(record.workspaceId, response, registry)
          validated = true
          counters.success += 1
          if (!completionCurrent()) throw new Error('Dashboard layout completion owner is not current')
          overlay.set(record.workspaceId, copyLayout(committed.layout))
          record.layout = resolveDashboardLayout(registry, committed.layout, record.workspaceId)
          record.provenance = 'saved'
          record.state = 'closed-success'
          record.stale = false
          record.session = null
        } catch (error: unknown) {
          const status = httpErrorStatus(error)
          countFailure(status)
          const capture = session.captureAttempt
          // Qualify ownership before mutating any record or retaining its capture.
          if (completionCurrent()) {
            record.writable = false
            record.state = 'unknown'
          }
          if (issued && !validated && completionCurrent() && capture?.epoch === epoch) {
            if (status === 403 || status === 404) {
              record.state = 'blocked'
              record.recovery = Object.freeze({ ...capture, category: 'blocked', phase: 'needs-recheck', recheck: null })
            } else if (status === 409) {
              record.state = 'conflict'
              conflictCapture = Object.freeze({ ...capture, category: 'conflict', phase: 'needs-recheck', recheck: null })
              record.recovery = conflictCapture
            } else if (status !== undefined && status >= 400 && status < 500 && status !== 401) {
              record.recovery = Object.freeze({ ...capture, category: 'correction-required', phase: 'needs-recheck', recheck: null })
            } else {
              record.recovery = capture
            }
          }
          // The already-issued latch rejects with the original caught object.
          throw error
        } finally {
          session.captureAttempt = null
          changed = invalidate()
        }
        // Invalidate the reply before immediate GET; queue delivery after invocation.
        // A failed GET cannot reclassify or undo a validated PUT success.
        // An inactive success records only its captured target; it never starts a GET.
        if (!ownerCurrent() || options.getWorkspaceId() !== record.workspaceId) return
        const read = refresh(record.workspaceId)
        schedulePublication(changed)
        changed = false
        await read
      } finally {
        schedulePublication(changed)
        if (conflictCapture && liveWitness && continuity) {
          queueConflictRecheck(record, session, conflictCapture, readSerial, liveWitness, continuity, task)
        }
      }
    })
    session.completion = track(task)
    if (publish) schedulePublication(invalidate())
    return task
  }

  async function whenIdle(): Promise<void> {
    while (tasks.size > 0) await Promise.allSettled([...tasks])
  }

  function getSnapshot(workspaceId: string): SessionSnapshot {
    const record = recordFor(workspaceId)
    const owner = ownerCurrent()
    const recovery = owner && record.recovery?.epoch === record.epoch ? record.recovery : null
    const undoAvailable = canUndo(record)
    const cached = snapshotCache.get(workspaceId)
    if (cached?.revision === revision && cached.owner === owner && cached.snapshot.canUndo === undoAvailable &&
        cached.snapshot.recovery === recovery) return cached.snapshot
    const overlayCopy = Object.fromEntries(
      [...overlay].map(([key, layout]) => [key, freezeLayout(layout)]),
    )
    const snapshot: SessionSnapshot = Object.freeze({
      actorId, authGeneration, workspaceId, provenance: record.provenance,
      writable: record.writable && owner, state: record.state,
      pending: record.state === 'pending', readPending: record.read !== null,
      canUndo: undoAvailable,
      layout: freezeLayout(record.layout),
      openedBaseline: record.session ? freezeLayout(record.session.baseline) : null,
      draft: record.session ? freezeLayout(record.session.draft) : null,
      recovery,
      overlay: Object.freeze(overlayCopy), counters: Object.freeze({ ...counters }),
    })
    snapshotCache.set(workspaceId, { revision, owner, snapshot })
    return snapshot
  }

  function getPersistenceStatus(workspaceId: string): PersistenceStatus {
    const record = records.get(workspaceId)
    if (!record || !ownerCurrent() || options.getWorkspaceId() !== workspaceId) return INERT_PERSISTENCE_STATUS
    const status = persistenceFor(record)
    const content = JSON.stringify(status)
    const cached = persistenceCache.get(workspaceId)
    if (cached?.content === content) return cached.status
    const frozen = Object.freeze(status)
    persistenceCache.set(workspaceId, { revision: persistenceRevision, content, status: frozen })
    return frozen
  }

  const store: SessionStore = { revoke, refresh, recheckRecovery, reviewRecovery, recheckReviewedRecovery, replaceRecovery, discardRecovery, invalidateRecoveryReads, open, retain, discard, setDraft, reset, undoReset, finalize, whenIdle, getSnapshot, getPersistenceStatus, subscribe }
  return store
}
