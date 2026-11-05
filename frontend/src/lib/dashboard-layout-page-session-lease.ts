import type { LivePageLeaseWitness, RecoveryActionTicket, RecoveryReview, SessionStore } from '@/lib/dashboard-layout-session'

export type DialogTicket = Readonly<{ sessionId: string; workspaceId: string }>
export interface PageBinding {
  open(): DialogTicket
  close(ticket: DialogTicket, persist: boolean): Promise<void>
  recheckRecovery(attemptId: string): Promise<void>
  reviewRecovery(attemptId: string): RecoveryReview
  recheckReviewedRecovery(ticket: RecoveryActionTicket): Promise<void>
  replaceRecovery(ticket: RecoveryActionTicket, action: 'reapply' | 'retry'): Promise<void>
  discardRecovery(ticket: RecoveryActionTicket): void
  refreshDisplay(): Promise<void>
  release(): void
}
export interface PageSessionLease {
  bind(workspaceId: string): PageBinding
  select(workspaceId: string): void
  whenIdle(): Promise<void>
}

interface IssuedHandle { released: boolean }
interface QueuedRelease { cancel(): void }
interface WorkspaceEntry {
  workspaceId: string
  eligible: boolean
  count: number
  handles: Set<IssuedHandle>
  continuity: object | null
  active: TicketState | null
  queuedRelease: QueuedRelease | null
}
interface TicketState {
  ticket: DialogTicket
  entry: WorkspaceEntry
  closed: boolean
  completion: Promise<void> | null
}

const leases = new WeakMap<SessionStore, PageSessionLease>()
const liveWitnesses = new WeakSet<LivePageLeaseWitness>()

// Structural lookalikes are not native page capabilities. No registration port
// is exported; only actual selected entries can mint these witnesses.
export function isLivePageLeaseWitness(witness: LivePageLeaseWitness, store: SessionStore, workspaceId: string): boolean {
  return liveWitnesses.has(witness) && witness.store === store &&
    witness.workspaceId === workspaceId && witness.isCurrent()
}

// Experimental, unactivated candidate: native deferred final release with
// reacquisition cancellation is repaired here, but NOT yet accepted/validated.
export function getPageSessionLease(store: SessionStore): PageSessionLease {
  const existing = leases.get(store)
  if (existing) return existing
  const entries = new Map<string, WorkspaceEntry>()
  const tickets = new WeakMap<DialogTicket, TicketState>()
  const tasks = new Set<Promise<void>>()
  let selected: string | null = null
  let nextContinuity = 0

  function eligible(entry: WorkspaceEntry): boolean {
    return entry.eligible && selected === entry.workspaceId &&
      entries.get(entry.workspaceId) === entry
  }

  function issue(entry: WorkspaceEntry, sessionId: string): TicketState {
    const current = entry.active
    if (current && !current.closed && current.ticket.sessionId === sessionId) return current
    const ticket = Object.freeze({ sessionId, workspaceId: entry.workspaceId })
    const state: TicketState = { ticket, entry, closed: false, completion: null }
    tickets.set(ticket, state)
    entry.active = state
    return state
  }

  function captureLiveWitness(entry: WorkspaceEntry, handle?: IssuedHandle): LivePageLeaseWitness | null {
    const continuity = entry.continuity
    if (!eligible(entry) || entry.count <= 0 || entry.handles.size !== entry.count ||
        continuity === null) return null
    const witness = Object.freeze({
      store, workspaceId: entry.workspaceId, continuity,
      isCurrent: () => eligible(entry) && entry.count > 0 &&
        entry.handles.size === entry.count && entry.continuity === continuity &&
        (handle === undefined || (!handle.released && entry.handles.has(handle))),
    })
    liveWitnesses.add(witness)
    return witness
  }

  function adopt(entry: WorkspaceEntry): TicketState | null {
    const sessionId = store.retain(entry.workspaceId)
    return sessionId === null ? null : issue(entry, sessionId)
  }

  function cancelQueuedRelease(entry: WorkspaceEntry): void {
    const queued = entry.queuedRelease
    entry.queuedRelease = null
    if (queued) queued.cancel()
  }

  function deferRelease(entry: WorkspaceEntry, handle: IssuedHandle, active: TicketState): void {
    cancelQueuedRelease(entry)
    const ticket = active.ticket
    let resolveRelease: () => void = () => { throw new Error('Release latch not initialized') }
    let rejectRelease: (reason: unknown) => void = () => { throw new Error('Release latch not initialized') }
    const task = new Promise<void>((resolve, reject) => {
      resolveRelease = resolve
      rejectRelease = reject
    })
    const queued: QueuedRelease = { cancel: resolveRelease }
    // Publish completion and a unique turn guard before invoking the native queue.
    entry.queuedRelease = queued
    tasks.add(task)
    void task.then(() => { tasks.delete(task) }, () => { tasks.delete(task) })
    queueMicrotask(() => {
      if (entry.queuedRelease !== queued) {
        resolveRelease()
        return
      }
      entry.queuedRelease = null
      if (!handle.released || entry.handles.has(handle) || entry.count !== 0 ||
          !eligible(entry) || entry.active !== active || active.closed ||
          active.ticket !== ticket || tickets.get(ticket) !== active) {
        resolveRelease()
        return
      }
      try {
        // Existing close/read/finalize latches remain the only store authority.
        void closeCaptured(active, true).then(resolveRelease, rejectRelease)
      } catch (error: unknown) {
        rejectRelease(error)
      }
    })
  }

  function closeCaptured(state: TicketState, persist: boolean): Promise<void> {
    if (state.completion) return state.completion
    if (state.closed) return Promise.reject(new Error('Dashboard page ticket is closed'))
    const { entry, ticket } = state
    // Install the private latch before invoking any real-store operation.
    let resolveClose: () => void = () => { throw new Error('Close latch not initialized') }
    let rejectClose: (reason: unknown) => void = () => { throw new Error('Close latch not initialized') }
    const task = new Promise<void>((resolve, reject) => {
      resolveClose = resolve
      rejectClose = reject
    })
    state.completion = task
    tasks.add(task)
    function settled(): void {
      state.closed = true
      if (entry.active === state) entry.active = null
      tasks.delete(task)
    }
    // Explicit callers keep the original rejection. Bookkeeping also handles
    // implicit release rejection; error/recovery paths remain runtime-unproved.
    void task.then(settled, settled)

    function finish(): void {
      if (!eligible(entry) || entry.active !== state ||
          store.retain(ticket.workspaceId) !== ticket.sessionId) {
        throw new Error('Dashboard page close target is not current')
      }
      const snapshot = store.getSnapshot(ticket.workspaceId)
      if (!persist && !snapshot.pending) {
        store.discard(ticket.sessionId)
        resolveClose()
      } else {
        // A pending session returns the real store's already-issued latch.
        void store.finalize(ticket.sessionId, captureLiveWitness(entry) ?? undefined).then(resolveClose, rejectClose)
      }
    }

    try {
      if (!eligible(entry) || entry.active !== state ||
          store.retain(ticket.workspaceId) !== ticket.sessionId) {
        throw new Error('Dashboard page close target is not current')
      }
      const snapshot = store.getSnapshot(ticket.workspaceId)
      if (persist && snapshot.readPending && !snapshot.pending) {
        // Capture happened above; never adopt another session after this read.
        // whenIdle settles reads; retain/writability reject failed/stale reads.
        void store.whenIdle().then(() => {
          try { finish() } catch (error: unknown) { rejectClose(error) }
        }, rejectClose)
      } else {
        // Once close is elected, the real store synchronously marks pending
        // and queues its actual PUT; explicit close remains immediate.
        finish()
      }
    } catch (error: unknown) {
      rejectClose(error)
    }
    return task
  }

  function bind(workspaceId: string): PageBinding {
    if (selected === null) selected = workspaceId
    let entry = entries.get(workspaceId)
    if (!entry) {
      entry = { workspaceId, eligible: selected === workspaceId, count: 0,
        handles: new Set<IssuedHandle>(), continuity: null, active: null, queuedRelease: null }
      entries.set(workspaceId, entry)
    }
    const capturedEntry = entry
    cancelQueuedRelease(capturedEntry)
    const handle: IssuedHandle = { released: false }
    const reviewTickets = new WeakMap<RecoveryActionTicket, object>()
    capturedEntry.handles.add(handle)
    capturedEntry.count += 1
    if (capturedEntry.count === 1 && eligible(capturedEntry)) {
      capturedEntry.continuity = Object.freeze({ generation: ++nextContinuity })
    }
    // Bind only retains editing/pending state; idle bind does not open/close.
    if (eligible(capturedEntry)) adopt(capturedEntry)

    function live(): boolean {
      return !handle.released && capturedEntry.handles.has(handle) && eligible(capturedEntry)
    }
    return {
      open() {
        if (!live()) throw new Error('Dashboard page binding is not current')
        const active = capturedEntry.active
        if (active && !active.closed && active.completion) {
          throw new Error('Dashboard page close is pending')
        }
        if (store.getSnapshot(workspaceId).pending) {
          throw new Error('Dashboard page close is pending')
        }
        const retained = adopt(capturedEntry)
        if (retained) return retained.ticket
        // Actual open enforces owner, committed workspace, reads and eligibility.
        return issue(capturedEntry, store.open(workspaceId)).ticket
      },
      close(ticket, persist) {
        const state = tickets.get(ticket)
        if (!state || state.entry !== capturedEntry) {
          return Promise.reject(new Error('Dashboard page ticket is foreign'))
        }
        // Duplicate close shares its exact completion even after a fresh open.
        if (state.completion) return state.completion
        if (!live() || state.closed || capturedEntry.active !== state) {
          return Promise.reject(new Error('Dashboard page ticket is stale'))
        }
        return closeCaptured(state, persist)
      },
      recheckRecovery(attemptId) {
        if (!live()) return Promise.reject(new Error('Dashboard page binding is not current'))
        return store.recheckRecovery(workspaceId, attemptId)
      },
      reviewRecovery(attemptId) {
        const witness = live() ? captureLiveWitness(capturedEntry, handle) : null
        if (!witness) throw new Error('Dashboard page binding is not current')
        const review = store.reviewRecovery(workspaceId, attemptId, witness)
        reviewTickets.set(review.ticket, witness.continuity)
        return review
      },
      recheckReviewedRecovery(ticket) {
        const continuity = reviewTickets.get(ticket)
        if (!live() || continuity === undefined || capturedEntry.continuity !== continuity) {
          return Promise.reject(new Error('Dashboard reviewed page ticket is not current'))
        }
        return store.recheckReviewedRecovery(ticket)
      },
      replaceRecovery(ticket, action) {
        const continuity = reviewTickets.get(ticket)
        if (!live() || continuity === undefined || capturedEntry.continuity !== continuity) {
          return Promise.reject(new Error('Dashboard reviewed page ticket is not current'))
        }
        return store.replaceRecovery(ticket, action)
      },
      discardRecovery(ticket) {
        const continuity = reviewTickets.get(ticket)
        if (!live() || continuity === undefined || capturedEntry.continuity !== continuity) return
        store.discardRecovery(ticket)
      },
      refreshDisplay() {
        if (!live() || capturedEntry.continuity === null) {
          return Promise.reject(new Error('Dashboard page binding is not current'))
        }
        const snapshot = store.getSnapshot(workspaceId)
        const active = capturedEntry.active
        if (snapshot.pending || snapshot.recovery || (active && !active.closed && active.completion)) {
          return Promise.reject(new Error('Dashboard display refresh is unavailable'))
        }
        // Preserve the native issued task and its drain ownership; never recheck a failure.
        return store.refresh(workspaceId)
      },
      release() {
        if (handle.released) return
        handle.released = true
        capturedEntry.handles.delete(handle)
        capturedEntry.count -= 1
        if (capturedEntry.count === 0) {
          capturedEntry.continuity = null
          store.invalidateRecoveryReads()
        }
        const active = capturedEntry.active
        if (capturedEntry.count === 0 && eligible(capturedEntry) && active && !active.closed) {
          deferRelease(capturedEntry, handle, active)
        }
      },
    }
  }

  function select(workspaceId: string): void {
    if (selected === workspaceId) return
    const previous = selected === null ? undefined : entries.get(selected)
    if (previous) previous.continuity = null
    store.invalidateRecoveryReads()
    selected = workspaceId
    if (previous) {
      cancelQueuedRelease(previous)
      previous.eligible = false
      entries.delete(previous.workspaceId)
      const captured = previous.active
      // Never re-query the previous workspace to adopt/discard a new session.
      // Real discard removes only unsent state, including a close awaiting reads;
      // issued pending work/latches survive. No revoke or owner disposal.
      if (captured && !captured.closed) {
        store.discard(captured.ticket.sessionId)
        captured.closed = true
        previous.active = null
      }
    }
    const next = entries.get(workspaceId)
    if (next) {
      next.eligible = true
      if (eligible(next) && next.count > 0 && next.handles.size === next.count) {
        next.continuity = Object.freeze({ generation: ++nextContinuity })
      }
    }
  }

  async function whenIdle(): Promise<void> {
    do {
      await Promise.allSettled([...tasks])
      await store.whenIdle()
    } while (tasks.size > 0)
  }

  const lease: PageSessionLease = { bind, select, whenIdle }
  leases.set(store, lease)
  return lease
}
