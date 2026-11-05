/** Subscriber/controller for the provider-owned dashboard layout session.
 * The public page lease owns close/release coordination; this hook holds
 * only its binding, dialog ticket and panel visibility, never persistence.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { useDashboardLayoutOwner, type DashboardLayoutOwner } from '@/contexts/dashboard-layout-provider'
import { useWorkspace } from '@/contexts/workspace-context'
import {
  DASHBOARD_WIDGETS,
  type DashboardWidgetDefinition,
} from '@/components/dashboard/widgets'
import {
  moveItem,
  resolveDashboardLayout,
  toggleItem,
  type DashboardLayout,
  type DashboardWidgetId,
} from '@/lib/dashboard-layout'
import { getPageSessionLease, type DialogTicket, type PageBinding } from '@/lib/dashboard-layout-page-session-lease'
import { INERT_PERSISTENCE_STATUS, type PersistenceStatus, type Recovery, type RecoveryActionTicket, type RecoveryReview, type SessionStore, type SnapshotLayout } from '@/lib/dashboard-layout-session'

/** Provenance of the stored layout (§4.3 rule 2 / §6.3). */
export type DashboardLayoutStatus = 'default' | 'saved' | 'foreign'

interface ControllerIdentity {
  owner: DashboardLayoutOwner | null
  store: SessionStore | null
  workspaceId: string
}

interface ControllerBinding {
  identity: ControllerIdentity
  binding: PageBinding
  ticket: DialogTicket | null
}

// Presentation receipts follow the owner store, not a remountable page or hook.
// Keep only workspace/attempt identifiers; never recovery data or authority.
const failureAnnouncements = new WeakMap<SessionStore, Map<string, Set<string>>>()

function copyLayout(layout: SnapshotLayout): DashboardLayout {
  return { version: 1, workspaceId: layout.workspaceId, items: layout.items.map((item) => ({ ...item })) }
}

export interface DashboardLayoutController {
  /** Resolved, visible-only widgets in display order — what the page renders. */
  orderedVisible: DashboardWidgetDefinition[]
  /** Full resolved layout (with hidden items) — the customize panel's draft. */
  draft: DashboardLayout
  status: DashboardLayoutStatus
  readonly recovery: Recovery | null
  readonly persistence: PersistenceStatus
  readonly recoveryReview: RecoveryReview | null
  readonly canReview: boolean
  reviewRecovery(attemptId: string): void
  closeRecoveryReview(): void
  /** Capture presentation-only owner/binding admission for deferred focus. */
  captureRecoveryFocusGuard(): () => boolean
  recheckReviewedRecovery(ticket: RecoveryActionTicket): Promise<void>
  replaceRecovery(ticket: RecoveryActionTicket, action: 'reapply' | 'retry'): Promise<void>
  discardRecovery(ticket: RecoveryActionTicket): void
  recheckRecovery(attemptId: string): Promise<void>
  refreshDisplay(): Promise<void>
  readonly canCustomize: boolean
  readonly showRefreshDisplay: boolean
  readonly canRefreshDisplay: boolean
  readonly displayReadPending: boolean
  customizeOpen: boolean
  readonly customizeDisabled: boolean
  /** Read live native admission again before a UI callback announces or focuses. */
  canEditCustomize(): boolean
  /** True while the panel holds a one-deep undo slot (AC-9). */
  canUndo: boolean
  openCustomize(): void
  /** Closing with persist=true is the single write point (§4.5). */
  closeCustomize(persist: boolean): void
  reorder(fromIndex: number, toIndex: number): void
  move(id: DashboardWidgetId, direction: 'up' | 'down'): void
  /** Returns false when the toggle was refused (last visible widget, §7.5). */
  toggleVisible(id: DashboardWidgetId): boolean
  reset(): void
  undoReset(): void
}

export function useDashboardLayout(): DashboardLayoutController {
  const { t } = useTranslation()
  const owner = useDashboardLayoutOwner()
  const { current } = useWorkspace()
  const workspaceId = current?.id ?? ''
  const store = owner && workspaceId ? owner.store : null
  const identity = useMemo<ControllerIdentity>(
    () => ({ owner, store, workspaceId }), [owner, store, workspaceId],
  )
  const committed = useRef<ControllerIdentity | null>(null)
  const bindingRef = useRef<ControllerBinding | null>(null)
  const [bindingState, setBindingState] = useState<ControllerBinding | null>(null)
  const [panel, setPanel] = useState<{ identity: ControllerIdentity; ticket: DialogTicket } | null>(null)
  const [review, setReview] = useState<{ identity: ControllerIdentity; binding: PageBinding; model: RecoveryReview; status: PersistenceStatus } | null>(null)
  const subscribe = useCallback((listener: () => void) => store ? store.subscribe(listener) : () => {}, [store])
  const getSnapshot = useCallback(() => store ? store.getSnapshot(workspaceId) : null, [store, workspaceId])
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  const getStatus = useCallback(() => store ? store.getPersistenceStatus(workspaceId) : INERT_PERSISTENCE_STATUS, [store, workspaceId])
  const persistence = useSyncExternalStore(subscribe, getStatus, getStatus)
  const defaults = useMemo(() => resolveDashboardLayout(DASHBOARD_WIDGETS, undefined, workspaceId), [workspaceId])
  const view = snapshot?.state === 'editing' && snapshot.draft ? snapshot.draft : snapshot?.layout
  const draft = useMemo(() => copyLayout(view ?? defaults), [view, defaults])
  const customizeOpen = panel?.identity === identity && snapshot?.state === 'editing'

  // Selection wins over the previous passive binding's deferred release.
  useLayoutEffect(() => {
    committed.current = identity
    if (owner) getPageSessionLease(owner.store).select(workspaceId)
    return () => { if (committed.current === identity) committed.current = null }
  }, [identity, owner, workspaceId])

  useEffect(() => {
    if (!store) return
    const lease = getPageSessionLease(store)
    const binding = lease.bind(workspaceId)
    const captured: ControllerBinding = { identity, binding, ticket: null }
    bindingRef.current = captured
    queueMicrotask(() => {
      if (bindingRef.current === captured && committed.current === identity) setBindingState(captured)
    })
    // Reacquisition uses the owner's existing session, including its undo slot.
    const initial = store.getSnapshot(workspaceId)
    if (initial.state === 'editing' && initial.writable) {
      try {
        captured.ticket = binding.open()
        setPanel({ identity, ticket: captured.ticket })
      } catch {
        // The public binding/store remain authoritative; never fake an open.
      }
    }
    const refresh = () => {
      if (committed.current !== identity || bindingRef.current !== captured) return
      // Rejection remains represented by the owner's snapshot; no retry.
      void captured.binding.refreshDisplay().catch(() => {})
    }
    refresh()
    window.addEventListener('focus', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      if (bindingRef.current === captured) bindingRef.current = null
      binding.release()
    }
  }, [identity, store, workspaceId])

  const currentBinding = () => {
    const captured = bindingRef.current
    return committed.current === identity && captured?.identity === identity ? captured : null
  }

  const announceRetainedFailure = useCallback((captured: ControllerBinding, recovery: Recovery | null) => {
    if (!owner || !store || !recovery || committed.current !== identity ||
        bindingRef.current !== captured || captured.identity !== identity ||
        recovery.actorId !== owner.actorId || recovery.authGeneration !== owner.authGeneration ||
        recovery.workspaceId !== workspaceId) return
    // Native status rejects revoked/foreign selection; identity rejects stale
    // rendered captures. These readonly checks grant no recovery capability.
    if (store.getPersistenceStatus(workspaceId).failureKind !== recovery.category ||
        store.getSnapshot(workspaceId).recovery !== recovery) return
    let workspaces = failureAnnouncements.get(store)
    if (!workspaces) {
      workspaces = new Map<string, Set<string>>()
      failureAnnouncements.set(store, workspaces)
    }
    let attempts = workspaces.get(workspaceId)
    if (!attempts) {
      attempts = new Set<string>()
      workspaces.set(workspaceId, attempts)
    }
    if (attempts.has(recovery.attemptId)) return
    attempts.add(recovery.attemptId)
    toast.error(t('dashboard.customize.saveError'))
  }, [identity, owner, store, t, workspaceId])

  useEffect(() => {
    const captured = bindingRef.current
    if (captured) announceRetainedFailure(captured, snapshot?.recovery ?? null)
  }, [announceRetainedFailure, bindingState, persistence, snapshot])

  // Reconcile only an already-open readonly view. Ticket allocation never
  // reads or writes; a stale rendered model cannot become action authority.
  useEffect(() => {
    if (!review || review.identity !== identity || !store || !snapshot?.recovery ||
        (review.model.recovery === snapshot.recovery && review.status === persistence)) return
    const captured = bindingRef.current
    if (!captured || captured.identity !== identity || captured.binding !== review.binding) return
    const recovery = snapshot.recovery
    let live = true
    queueMicrotask(() => {
      if (!live || committed.current !== identity || bindingRef.current !== captured ||
          store.getSnapshot(workspaceId).recovery !== recovery) return
      try {
        const model = captured.binding.reviewRecovery(recovery.attemptId)
        setReview((previous) => previous === review ? { identity, binding: captured.binding, model, status: persistence } : previous)
      } catch {
        // Retired captures stay readonly with denied actions.
      }
    })
    return () => { live = false }
  }, [identity, persistence, review, snapshot, store, workspaceId])

  const reviewRecovery = (attemptId: string) => {
    const captured = currentBinding()
    if (!captured || !store) return
    try {
      const model = captured.binding.reviewRecovery(attemptId)
      setReview({ identity, binding: captured.binding, model, status: store.getPersistenceStatus(workspaceId) })
    } catch {
      // Selection/auth/capture changes cannot open a foreign review.
    }
  }
  const closeRecoveryReview = () => { setReview(null) }
  const captureRecoveryFocusGuard = () => {
    const captured = currentBinding()
    return () => captured !== null && currentBinding() === captured && store !== null &&
      store.getPersistenceStatus(workspaceId) !== INERT_PERSISTENCE_STATUS
  }
  const reviewedBinding = (ticket: RecoveryActionTicket) => {
    const captured = currentBinding()
    return captured && review?.identity === identity && review.binding === captured.binding &&
      review.model.ticket === ticket && review.model.recovery === store?.getSnapshot(workspaceId).recovery &&
      review.status === store?.getPersistenceStatus(workspaceId) ? captured.binding : null
  }
  const recheckReviewedRecovery = (ticket: RecoveryActionTicket): Promise<void> => {
    const binding = reviewedBinding(ticket)
    return binding ? binding.recheckReviewedRecovery(ticket) : Promise.reject(new Error('Dashboard review is not current'))
  }
  const replaceRecovery = (ticket: RecoveryActionTicket, action: 'reapply' | 'retry'): Promise<void> => {
    const binding = reviewedBinding(ticket)
    return binding ? binding.replaceRecovery(ticket, action) : Promise.reject(new Error('Dashboard review is not current'))
  }
  const discardRecovery = (ticket: RecoveryActionTicket) => { reviewedBinding(ticket)?.discardRecovery(ticket) }
  const recoveryReview = review?.identity === identity && review.binding === bindingState?.binding && snapshot?.recovery
    ? review.model.recovery === snapshot.recovery && review.status === persistence && !persistence.pending && !persistence.readPending
      ? review.model
      : { ...review.model, recovery: snapshot.recovery, permissions: { recheck: false, reapply: false, retry: false, discard: false } }
    : null

  const editable = () => {
    const captured = currentBinding()
    if (!captured?.ticket || !store) return null
    const latest = store.getSnapshot(workspaceId)
    if (latest.state !== 'editing' || latest.pending || latest.readPending || !latest.writable ||
        latest.recovery || !store.getPersistenceStatus(workspaceId).canCustomize ||
        !latest.draft || store.retain(workspaceId) !== captured.ticket.sessionId) return null
    return { ticket: captured.ticket, draft: copyLayout(latest.draft) }
  }

  const canEditCustomize = () => editable() !== null

  const recheckRecovery = (attemptId: string): Promise<void> => {
    const captured = currentBinding()
    if (!captured || !store || captured.identity.store !== store) {
      return Promise.reject(new Error('Dashboard layout recovery binding is not current'))
    }
    return captured.binding.recheckRecovery(attemptId)
  }

  const refreshDisplay = (): Promise<void> => {
    const captured = currentBinding()
    if (!captured || !store || captured.identity.store !== store) {
      return Promise.reject(new Error('Dashboard display refresh binding is not current'))
    }
    return captured.binding.refreshDisplay()
  }

  const openCustomize = () => {
    const captured = currentBinding()
    if (!captured || !store) return
    const latest = store.getSnapshot(workspaceId)
    if (latest.pending || latest.readPending || !latest.writable) return
    try {
      const ticket = captured.binding.open()
      captured.ticket = ticket
      setPanel({ identity, ticket })
    } catch {
      // Protected/stale owners cannot manufacture an editable fallback.
    }
  }

  const closeCustomize = (persist: boolean) => {
    const captured = currentBinding()
    const ticket = captured?.ticket
    if (!captured || !ticket) return
    captured.ticket = null
    setPanel(null)
    // Capture the original target before any read, selection or owner change.
    void captured.binding.close(ticket, persist).catch(() => {
      announceRetainedFailure(captured, store?.getSnapshot(workspaceId).recovery ?? null)
    })
  }

  const reorder = (fromIndex: number, toIndex: number) => {
    const edit = editable()
    if (!edit || !store) return
    const n = edit.draft.items.length
    const clamp = (value: number) =>
      Math.min(Math.max(Number.isFinite(value) ? value : 0, 0), n - 1)
    const from = clamp(fromIndex)
    const to = clamp(toIndex)
    if (from === to) return
    store.setDraft(edit.ticket.sessionId, moveItem(edit.draft.items, from, to))
    // Update only the UI handle; render re-reads the owner's synchronous draft
    // before its deferred subscription publication, preserving immediate preview.
    setPanel({ identity, ticket: edit.ticket })
  }

  const move = (id: DashboardWidgetId, direction: 'up' | 'down') => {
    const edit = editable()
    if (!edit || !store) return
    const from = edit.draft.items.findIndex((item) => item.id === id)
    if (from === -1) return
    const to = direction === 'up' ? from - 1 : from + 1
    if (to < 0 || to >= edit.draft.items.length) return // clamped, no change
    store.setDraft(edit.ticket.sessionId, moveItem(edit.draft.items, from, to))
    setPanel({ identity, ticket: edit.ticket })
  }

  const toggleVisible = (id: DashboardWidgetId): boolean => {
    const edit = editable()
    if (!edit || !store) return false
    const target = edit.draft.items.find((item) => item.id === id)
    if (!target) return false
    const items = toggleItem(edit.draft.items, id, !target.visible)
    if (items === edit.draft.items) return false // last-visible refusal (§7.5)
    store.setDraft(edit.ticket.sessionId, items)
    setPanel({ identity, ticket: edit.ticket })
    return true
  }

  const reset = () => {
    const edit = editable()
    if (!edit || !store) return
    store.reset(edit.ticket.sessionId)
    setPanel({ identity, ticket: edit.ticket })
  }

  const undoReset = () => {
    const edit = editable()
    if (!edit || !store) return
    store.undoReset(edit.ticket.sessionId)
    setPanel({ identity, ticket: edit.ticket })
  }

  const orderedVisible = useMemo(() => {
    const byId = new Map<DashboardWidgetId, DashboardWidgetDefinition>(
      DASHBOARD_WIDGETS.map((definition) => [definition.id, definition]),
    )
    return draft.items.flatMap((item) => {
      const definition = item.visible ? byId.get(item.id) : undefined
      return definition ? [definition] : []
    })
  }, [draft])

  return {
    orderedVisible,
    draft,
    status: snapshot?.provenance ?? 'default',
    recovery: snapshot?.recovery ?? null,
    persistence,
    recoveryReview,
    canReview: bindingState?.identity === identity && snapshot?.recovery !== null && snapshot?.recovery !== undefined,
    reviewRecovery,
    closeRecoveryReview,
    captureRecoveryFocusGuard,
    recheckReviewedRecovery,
    replaceRecovery,
    discardRecovery,
    recheckRecovery,
    refreshDisplay,
    canCustomize: bindingState?.identity === identity && persistence.canCustomize && snapshot?.writable === true &&
      !snapshot.pending && !snapshot.readPending && !snapshot.recovery &&
      (snapshot.state === 'closed' || snapshot.state === 'closed-success' || snapshot.state === 'editing'),
    showRefreshDisplay: snapshot !== null && (persistence.stale || !snapshot.writable) && !snapshot.recovery,
    canRefreshDisplay: bindingState?.identity === identity && snapshot !== null &&
      !snapshot.pending && !snapshot.readPending && !snapshot.recovery,
    displayReadPending: snapshot?.readPending ?? false,
    customizeOpen,
    customizeDisabled: !customizeOpen || bindingState?.identity !== identity ||
      !bindingState.ticket || bindingState.ticket !== panel?.ticket || !persistence.canCustomize ||
      snapshot?.state !== 'editing' || !snapshot.writable || snapshot.pending || snapshot.readPending ||
      snapshot.recovery !== null || !snapshot.draft,
    canEditCustomize,
    canUndo: customizeOpen && snapshot?.canUndo === true,
    openCustomize,
    closeCustomize,
    reorder,
    move,
    toggleVisible,
    reset,
    undoReset,
  }
}
