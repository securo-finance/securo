import { useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { GripVertical, LayoutDashboard, MoveHorizontal, Plus, X } from 'lucide-react'
import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { arrayMove, rectSortingStrategy, SortableContext, sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { resizeItem, snapSpan, SPANS, widgetSpan, type LayoutItem, type Span, type WidgetEntry } from '@/lib/library'
import { cn } from '@/lib/utils'

// Tailwind only ships classes it can see written out.
const SPAN_CLASS: Record<Span, string> = {
  4: 'lg:col-span-4',
  6: 'lg:col-span-6',
  8: 'lg:col-span-8',
  12: 'lg:col-span-12',
}
/** The grid's gap (gap-5), needed to turn a dragged width into columns. */
const GAP_PX = 20

/**
 * The dashboard's widgets on a 12-column grid, in the order and at the widths
 * someone chose. While editing, a widget is moved by dragging its handle (or
 * with the keyboard), resized by dragging its right edge or from its width
 * menu, and its content stops taking clicks. Small screens stack everything.
 */
export function WidgetGrid({
  items,
  entries,
  content,
  editing,
  onChange,
  onAdd,
}: {
  items: LayoutItem[]
  entries: WidgetEntry[]
  content: Record<string, ReactNode>
  editing: boolean
  onChange: (items: LayoutItem[]) => void
  onAdd: () => void
}) {
  const { t } = useTranslation()
  const gridRef = useRef<HTMLDivElement>(null)
  const sensors = useSensors(
    // A few pixels of travel before a drag starts, so a click on the handle stays a click.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  // While an edge is dragged, the layout it would give; committed on release.
  const [preview, setPreview] = useState<{ id: string; items: LayoutItem[] } | null>(null)
  const byId = new Map(entries.map((entry) => [entry.id, entry]))
  // Outside editing, a widget with nothing to show right now (no goals yet)
  // isn't there; while editing it still needs a handle.
  const shown = items.filter((item) => byId.has(item.id) && (editing || content[item.id]))

  if (shown.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
        <LayoutDashboard className="size-8 text-muted-foreground/60" />
        <div>
          <p className="text-sm font-medium">{t('library.emptyDashboard')}</p>
          <p className="mt-1 text-xs text-muted-foreground">{t('library.emptyDashboardHint')}</p>
        </div>
        <Button size="sm" onClick={onAdd}>
          <Plus size={14} className="mr-1" />
          {t('library.addWidget')}
        </Button>
      </div>
    )
  }

  const move = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return
    const from = items.findIndex((item) => item.id === active.id)
    const to = items.findIndex((item) => item.id === over.id)
    if (from >= 0 && to >= 0) onChange(arrayMove(items, from, to))
  }

  const resized = (id: string, span: Span) => resizeItem(items, entries, id, span)
  const laidOut = new Map((preview?.items ?? items).map((item) => [item.id, item]))

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={move}>
      <SortableContext items={shown.map((item) => item.id)} strategy={rectSortingStrategy}>
        <div ref={gridRef} className="grid grid-cols-1 gap-5 lg:grid-cols-12">
          {shown.map((item) => {
            const entry = byId.get(item.id)!
            return (
              <SortableWidget
                key={item.id}
                entry={entry}
                span={widgetSpan(item, entry)}
                shownSpan={widgetSpan(laidOut.get(item.id) ?? item, entry)}
                resizing={preview?.id === item.id}
                editing={editing}
                gridRef={gridRef}
                onPreview={(span) => setPreview(span === null ? null : { id: item.id, items: resized(item.id, span) })}
                onResize={(span) => onChange(resized(item.id, span))}
                onRemove={() => onChange(items.filter((other) => other.id !== item.id))}
              >
                {content[item.id]}
              </SortableWidget>
            )
          })}
        </div>
      </SortableContext>
    </DndContext>
  )
}

function SortableWidget({
  entry,
  span,
  shownSpan,
  resizing,
  editing,
  gridRef,
  onPreview,
  onResize,
  onRemove,
  children,
}: {
  entry: WidgetEntry
  span: Span
  /** The width to draw: `span`, or what a resize in progress would make it. */
  shownSpan: Span
  resizing: boolean
  editing: boolean
  gridRef: RefObject<HTMLDivElement | null>
  onPreview: (span: Span | null) => void
  onResize: (span: Span) => void
  onRemove: () => void
  children: ReactNode
}) {
  const { t } = useTranslation()
  const title = t(entry.titleKey)
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id: entry.id,
    disabled: !editing,
  })
  const sectionRef = useRef<HTMLElement | null>(null)

  const startResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const section = sectionRef.current
    const grid = gridRef.current
    if (!section || !grid) return
    event.preventDefault()
    const handle = event.currentTarget
    handle.setPointerCapture(event.pointerId)
    const startX = event.clientX
    const startWidth = section.getBoundingClientRect().width
    const column = (grid.getBoundingClientRect().width + GAP_PX) / 12
    let next = span
    const onMove = (move: PointerEvent) => {
      const width = startWidth + (move.clientX - startX)
      next = snapSpan((width + GAP_PX) / column, entry.minSpan)
      onPreview(next)
    }
    const onUp = () => {
      handle.removeEventListener('pointermove', onMove)
      handle.removeEventListener('pointerup', onUp)
      handle.removeEventListener('pointercancel', onUp)
      onPreview(null)
      if (next !== span) onResize(next)
    }
    handle.addEventListener('pointermove', onMove)
    handle.addEventListener('pointerup', onUp)
    handle.addEventListener('pointercancel', onUp)
  }

  const style: CSSProperties = {
    // Translate only: scaling would squash a widget into its neighbour's size.
    transform: CSS.Translate.toString(transform),
    transition,
  }

  return (
    <section
      ref={(node) => {
        setNodeRef(node)
        sectionRef.current = node
      }}
      style={style}
      aria-label={title}
      className={cn(
        'relative grid',
        SPAN_CLASS[shownSpan],
        entry.tall && 'min-h-[380px]',
        // While editing, each widget sits in a frame with its own header, so
        // the controls never cover what the widget shows.
        editing && 'grid-rows-[auto_1fr] gap-2 rounded-2xl border border-dashed border-primary/40 bg-muted/30 p-2',
        resizing && 'border-primary bg-primary/5',
        isDragging && 'z-20 opacity-80 shadow-xl',
      )}
    >
      {editing && (
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1">
            <button
              ref={setActivatorNodeRef}
              type="button"
              title={t('library.dragToMove', { name: title })}
              className="flex size-7 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:cursor-grabbing"
              {...attributes}
              {...listeners}
              aria-label={t('library.dragToMove', { name: title })}
            >
              <GripVertical size={14} />
            </button>
            <span className="truncate text-xs font-medium text-muted-foreground">{title}</span>
            {resizing && (
              <span className="shrink-0 rounded-md bg-primary px-1.5 py-0.5 text-[10px] font-semibold text-primary-foreground">
                {t(`library.width.${shownSpan}`)}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-0.5">
            <DropdownMenu>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <button type="button" aria-label={t('library.widthOf', { name: title })} className={iconButton('hidden lg:flex')}>
                      <MoveHorizontal />
                    </button>
                  </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent>{t('library.widthOf', { name: title })}</TooltipContent>
              </Tooltip>
              <DropdownMenuContent align="end" className="w-40">
                <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">{t('library.widthLabel')}</DropdownMenuLabel>
                <DropdownMenuRadioGroup value={String(span)} onValueChange={(value) => onResize(Number(value) as Span)}>
                  {SPANS.filter((option) => option >= entry.minSpan).map((option) => (
                    <DropdownMenuRadioItem key={option} value={String(option)}>
                      {t(`library.width.${option}`)}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={t('library.removeFrom.dashboard', { name: title })}
                  onClick={onRemove}
                  className={iconButton('hover:bg-destructive/10 hover:text-destructive')}
                >
                  <X />
                </button>
              </TooltipTrigger>
              <TooltipContent>{t('library.removeFrom.dashboard', { name: title })}</TooltipContent>
            </Tooltip>
          </div>
        </div>
      )}
      <div inert={editing} className={cn('grid', editing && 'select-none')}>
        {children || (
          <div className="flex items-center justify-center rounded-xl border border-dashed bg-card px-6 py-10 text-sm text-muted-foreground">
            {t('library.nothingToShow')}
          </div>
        )}
      </div>
      {editing && (
        // The right edge: drag it to make the widget wider or narrower. Wide
        // screens only, where there are columns; the width menu does the same
        // from the keyboard.
        <button
          type="button"
          tabIndex={-1}
          aria-hidden
          onPointerDown={startResize}
          className="group absolute -right-3 top-1/2 z-10 hidden h-16 w-4 -translate-y-1/2 cursor-ew-resize touch-none items-center justify-center lg:flex"
        >
          <span className="h-10 w-1.5 rounded-full bg-primary/40 transition-colors group-hover:bg-primary group-active:bg-primary" />
        </button>
      )}
    </section>
  )
}

function iconButton(extra?: string) {
  return cn(
    'flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors [&_svg]:size-3.5',
    'hover:bg-muted hover:text-foreground',
    extra,
  )
}
