/**
 * Customize-dashboard modal (Monarch-style; design packet §6.5 + §7).
 *
 * Presentation only: every reorder/toggle/reset flows through the props the
 * page hands down from `useDashboardLayout`, so the dashboard behind the
 * dialog renders the live draft. Pointer and touch drag use @dnd-kit exactly
 * per §8.2 (drag starts only from the handle); the keyboard path is
 * hand-rolled on that same handle so Arrow Up/Down move one position
 * immediately and announce it through the dialog's own polite live region
 * (§7.3). Closing by any path — Done, Escape, overlay — bubbles one
 * onOpenChange(false) to the page, which persists exactly once (§4.5).
 */
import { useEffect, useRef, useState, type ComponentProps, type KeyboardEvent } from 'react'

import {
  DndContext,
  PointerSensor,
  TouchSensor,
  closestCenter,
  defaultAnnouncements,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ChevronDown, ChevronUp, GripVertical, RotateCcw, Undo2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { getWidgetDefinition } from '@/components/dashboard/widgets'
import type {
  DashboardLayout,
  DashboardLayoutItem,
  DashboardWidgetId,
} from '@/lib/dashboard-layout'
import { cn } from '@/lib/utils'

/** Shared id: every handle's aria-describedby points at the instruction. */
const HANDLE_HINT_ID = 'customize-dashboard-handle-hint'

type FocusTarget = 'handle' | 'up' | 'down'
interface FocusRequest {
  id: DashboardWidgetId
  target: FocusTarget
}

export interface CustomizeDashboardDialogProps {
  open: boolean
  /** false = Done/Escape/overlay — the single close path (persist once). */
  onOpenChange: (open: boolean) => void
  draft: DashboardLayout
  canUndo: boolean
  disabled?: boolean
  busy?: boolean
  /** Native admission check also rejects callbacks captured before a freeze. */
  canEdit?: () => boolean
  onCloseAutoFocus?: ComponentProps<typeof DialogContent>['onCloseAutoFocus']
  onReorder: (fromIndex: number, toIndex: number) => void
  onMove: (id: DashboardWidgetId, direction: 'up' | 'down') => void
  /** Returns false when the hide was refused (last visible widget). */
  onToggle: (id: DashboardWidgetId) => boolean
  onReset: () => void
  onUndo: () => void
}

interface WidgetRowProps {
  item: DashboardLayoutItem
  index: number
  total: number
  disabled: boolean
  /** Drop-target gap indicator for a row that is not the dragged one. */
  dropTarget: boolean
  move: (
    id: DashboardWidgetId,
    index: number,
    direction: 'up' | 'down',
    focus: FocusTarget,
  ) => void
  toggle: (item: DashboardLayoutItem) => void
  registerHandle: (id: DashboardWidgetId, node: HTMLButtonElement | null) => void
  registerButton: (key: string, node: HTMLButtonElement | null) => void
}

function WidgetRow({
  item,
  index,
  total,
  disabled,
  dropTarget,
  move,
  toggle,
  registerHandle,
  registerButton,
}: WidgetRowProps) {
  const { t } = useTranslation()
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useSortable({ id: item.id, disabled })

  const definition = getWidgetDefinition(item.id)
  const name = t(definition.translationKey)
  const toggleLabelId = `customize-toggle-${item.id}`

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
    event.preventDefault()
    move(item.id, index, event.key === 'ArrowUp' ? 'up' : 'down', 'handle')
  }

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform) }}
      className={cn(
        'flex items-center gap-2 rounded-md border border-border px-2 py-1.5',
        'transition-transform motion-reduce:transition-none',
        isDragging && 'opacity-60 ring-2 ring-ring',
        dropTarget && !isDragging && 'border-t-2 border-dashed border-ring',
      )}
    >
      {/* Drag handle: the only drag affordance; also the keyboard mover. */}
      <button
        type="button"
        ref={(node) => registerHandle(item.id, node)}
        {...attributes}
        {...listeners}
        disabled={disabled}
        aria-label={t('dashboard.customize.dragHandle', { name })}
        aria-describedby={HANDLE_HINT_ID}
        onKeyDown={handleKeyDown}
        className={cn(
          'inline-flex h-7 w-7 shrink-0 cursor-grab items-center justify-center',
          'rounded text-muted-foreground hover:bg-accent hover:text-foreground',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          'active:cursor-grabbing',
        )}
      >
        <GripVertical className="h-4 w-4" aria-hidden="true" />
      </button>

      <span className="min-w-0 flex-1 truncate text-sm">{name}</span>

      {/* Accessible name for the switch: Show/Hide + widget name. */}
      <span id={toggleLabelId} className="sr-only">
        {item.visible
          ? t('dashboard.customize.hide', { name })
          : t('dashboard.customize.show', { name })}
      </span>
      <Switch
        disabled={disabled}
        checked={item.visible}
        onCheckedChange={() => toggle(item)}
        aria-labelledby={toggleLabelId}
      />

      <button
        type="button"
        ref={(node) => registerButton(`${item.id}:up`, node)}
        aria-label={t('dashboard.customize.moveUp', { name })}
        disabled={disabled || index === 0}
        onClick={() => move(item.id, index, 'up', 'up')}
        className={cn(
          'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded',
          'text-muted-foreground hover:bg-accent hover:text-foreground',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          'disabled:pointer-events-none disabled:opacity-40',
        )}
      >
        <ChevronUp className="h-4 w-4" aria-hidden="true" />
      </button>
      <button
        type="button"
        ref={(node) => registerButton(`${item.id}:down`, node)}
        aria-label={t('dashboard.customize.moveDown', { name })}
        disabled={disabled || index === total - 1}
        onClick={() => move(item.id, index, 'down', 'down')}
        className={cn(
          'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded',
          'text-muted-foreground hover:bg-accent hover:text-foreground',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          'disabled:pointer-events-none disabled:opacity-40',
        )}
      >
        <ChevronDown className="h-4 w-4" aria-hidden="true" />
      </button>
    </li>
  )
}

export function CustomizeDashboardDialog({
  open,
  onOpenChange,
  draft,
  canUndo,
  disabled = false,
  busy = false,
  canEdit,
  onCloseAutoFocus,
  onReorder,
  onMove,
  onToggle,
  onReset,
  onUndo,
}: CustomizeDashboardDialogProps) {
  const { t } = useTranslation()
  const [announcement, setAnnouncement] = useState('')
  const [showHint, setShowHint] = useState(false)
  const [activeId, setActiveId] = useState<DashboardWidgetId | null>(null)
  const [overId, setOverId] = useState<DashboardWidgetId | null>(null)

  const pendingFocusRef = useRef<FocusRequest | null>(null)
  const handleNodes = useRef(new Map<DashboardWidgetId, HTMLButtonElement>())
  const buttonNodes = useRef(new Map<string, HTMLButtonElement>())
  const frozen = disabled || busy
  const canInteract = () => open && !frozen && (canEdit?.() ?? true)

  // Re-render after a move may relocate the focused node (keyed reorder =
  // DOM move = focus loss) — restore focus explicitly (fe-D4, §7.3).
  useEffect(() => {
    const pending = pendingFocusRef.current
    if (!pending) return
    pendingFocusRef.current = null
    if (!open || frozen || (canEdit && !canEdit())) return
    const node =
      pending.target === 'handle'
        ? handleNodes.current.get(pending.id)
        : buttonNodes.current.get(`${pending.id}:${pending.target}`)
    if (node?.isConnected && !node.disabled) node.focus()
  }, [draft.items, open, frozen, canEdit])

  // Pointer + touch only (§8.2). Keyboard is handled on the handle itself,
  // so no KeyboardSensor: arrows must move one position immediately rather
  // than drive a dnd-kit drag session.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 250, tolerance: 8 },
    }),
  )

  const ids = draft.items.map((item) => item.id)
  const total = draft.items.length

  const nameOf = (id: DashboardWidgetId) =>
    t(getWidgetDefinition(id).translationKey)
  const announceMoved = (id: DashboardWidgetId, position: number) =>
    setAnnouncement(
      t('dashboard.customize.announceMoved', {
        name: nameOf(id),
        position,
        total,
      }),
    )

  const move = (
    id: DashboardWidgetId,
    index: number,
    direction: 'up' | 'down',
    focus: FocusTarget,
  ) => {
    if (!canInteract()) return
    const to = direction === 'up' ? index - 1 : index + 1
    if (to < 0 || to >= total) return // clamped at the ends — no announce
    pendingFocusRef.current = { id, target: focus }
    onMove(id, direction)
    announceMoved(id, to + 1)
  }

  const toggle = (item: DashboardLayoutItem) => {
    if (!canInteract()) return
    const applied = onToggle(item.id)
    if (!applied) {
      setShowHint(true)
      setAnnouncement(t('dashboard.customize.lastVisible'))
      return
    }
    setShowHint(false)
    setAnnouncement(
      item.visible
        ? t('dashboard.customize.announceHidden', { name: nameOf(item.id) })
        : t('dashboard.customize.announceShown', { name: nameOf(item.id) }),
    )
  }

  const handleReset = () => {
    if (!canInteract()) return
    setShowHint(false)
    onReset()
    setAnnouncement(t('dashboard.customize.announceReset'))
  }

  const handleUndo = () => {
    if (!canInteract()) return
    // Undo slot lives only while the panel is open (AC-9); no announcement
    // specified for undo in §7.3 — the restored draft is self-evident.
    onUndo()
  }

  const handleDragStart = (event: DragStartEvent) => {
    if (!canInteract()) return
    setActiveId(event.active.id as DashboardWidgetId)
  }
  const handleDragOver = (event: DragOverEvent) => {
    if (!canInteract()) return
    setOverId((event.over?.id as DashboardWidgetId | undefined) ?? null)
  }
  const handleDragCancel = () => {
    // Escape mid-drag: draft untouched (§7.2).
    setActiveId(null)
    setOverId(null)
  }
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    setActiveId(null)
    setOverId(null)
    if (!canInteract()) return
    if (!over || active.id === over.id) return
    const from = ids.indexOf(active.id as DashboardWidgetId)
    const to = ids.indexOf(over.id as DashboardWidgetId)
    if (from < 0 || to < 0 || from === to) return
    onReorder(from, to)
    announceMoved(active.id as DashboardWidgetId, to + 1)
  }

  const registerHandle = (id: DashboardWidgetId, node: HTMLButtonElement | null) => {
    if (node) handleNodes.current.set(id, node)
    else handleNodes.current.delete(id)
  }
  const registerButton = (key: string, node: HTMLButtonElement | null) => {
    if (node) buttonNodes.current.set(key, node)
    else buttonNodes.current.delete(key)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md max-h-[calc(100dvh-2rem)] overflow-y-auto"
        aria-busy={busy} onCloseAutoFocus={onCloseAutoFocus}>
        <DialogHeader>
          <DialogTitle>{t('dashboard.customize.title')}</DialogTitle>
          <DialogDescription>
            {t('dashboard.customize.description')}
          </DialogDescription>
        </DialogHeader>

        <p id={HANDLE_HINT_ID} className="text-xs text-muted-foreground">
          {t('dashboard.customize.handleHint')}
        </p>

        {/* One polite live region announcing reorder/visibility/reset (§7.3). */}
        <div aria-live="polite" className="sr-only">
          {announcement}
        </div>

        <DndContext
          // Keep sensor descriptors stable: useSortable's native disabled flag
          // removes their row activators without changing dnd-kit's effect arity.
          sensors={sensors}
          accessibility={{ announcements: {
            onDragStart: (event) => canInteract() ? defaultAnnouncements.onDragStart(event) : undefined,
            onDragOver: (event) => canInteract() ? defaultAnnouncements.onDragOver?.(event) : undefined,
            onDragEnd: (event) => canInteract() ? defaultAnnouncements.onDragEnd(event) : undefined,
            onDragCancel: (event) => canInteract() ? defaultAnnouncements.onDragCancel(event) : undefined,
          } }}
          collisionDetection={closestCenter}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
          onDragCancel={handleDragCancel}
          onDragEnd={handleDragEnd}
        >
          <SortableContext items={ids} strategy={verticalListSortingStrategy}>
            <ul className="space-y-1.5">
              {draft.items.map((item, index) => (
                <WidgetRow
                  key={item.id}
                  item={item}
                  index={index}
                  total={total}
                  disabled={frozen}
                  dropTarget={overId === item.id && activeId !== null && activeId !== item.id}
                  move={move}
                  toggle={toggle}
                  registerHandle={registerHandle}
                  registerButton={registerButton}
                />
              ))}
            </ul>
          </SortableContext>
        </DndContext>

        {busy && (
          <p role="status" aria-live="polite" className="text-xs text-muted-foreground">
            {t('dashboard.customize.persistence.pending')}
          </p>
        )}

        {/* Last-visible refusal hint — visible, role=status (§7.5). */}
        <p role="status" className="text-xs text-muted-foreground">
          {showHint ? t('dashboard.customize.lastVisible') : ''}
        </p>

        <div className="flex items-center justify-between pt-2">
          {canUndo ? (
            <Button variant="ghost" size="sm" disabled={frozen} onClick={handleUndo}>
              <Undo2 className="mr-1.5 h-4 w-4" aria-hidden="true" />
              {t('dashboard.customize.undo')}
            </Button>
          ) : (
            <Button variant="ghost" size="sm" disabled={frozen} onClick={handleReset}>
              <RotateCcw className="mr-1.5 h-4 w-4" aria-hidden="true" />
              {t('dashboard.customize.reset')}
            </Button>
          )}
          <DialogClose asChild>
            <Button size="sm">{t('dashboard.customize.done')}</Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  )
}
