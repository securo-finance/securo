import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowDown, ArrowUp, LayoutDashboard, Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { halfWidth, moveItem, type WidgetEntry } from '@/lib/library'
import { cn } from '@/lib/utils'

/**
 * The dashboard's widgets in the order someone chose. While editing, each
 * one can be moved or removed, and its content stops taking clicks.
 */
export function WidgetGrid({
  ids,
  entries,
  content,
  editing,
  onChange,
  onAdd,
}: {
  ids: string[]
  entries: WidgetEntry[]
  content: Record<string, ReactNode>
  editing: boolean
  onChange: (ids: string[]) => void
  onAdd: () => void
}) {
  const { t } = useTranslation()
  const byId = new Map(entries.map((entry) => [entry.id, entry]))
  const halves = halfWidth(ids, byId)

  if (ids.length === 0) {
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

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      {ids.map((id, index) => {
        const entry = byId.get(id)
        if (!entry) return null
        const half = halves.has(id)
        const body = content[id]
        // A widget can have nothing to show right now (no goals yet); outside
        // editing it simply isn't there, while editing it still needs a handle.
        if (!body && !editing) return null
        const title = t(entry.titleKey)
        return (
          <section
            key={id}
            aria-label={title}
            className={cn(
              'grid',
              half ? 'min-h-[380px]' : 'lg:col-span-2',
              // While editing, each widget sits in a frame with its own header, so
              // the controls never cover what the widget shows.
              editing && 'grid-rows-[auto_1fr] gap-2 rounded-2xl border border-dashed border-primary/40 bg-muted/30 p-2',
            )}
          >
            {editing && (
              <div className="flex items-center justify-between gap-2 pl-2">
                <span className="truncate text-xs font-medium text-muted-foreground">{title}</span>
                <div className="flex shrink-0 items-center gap-0.5">
                  <EditButton
                    label={t('library.moveUp', { name: title })}
                    disabled={index === 0}
                    onClick={() => onChange(moveItem(ids, index, -1))}
                  >
                    <ArrowUp />
                  </EditButton>
                  <EditButton
                    label={t('library.moveDown', { name: title })}
                    disabled={index === ids.length - 1}
                    onClick={() => onChange(moveItem(ids, index, 1))}
                  >
                    <ArrowDown />
                  </EditButton>
                  <EditButton
                    label={t('library.removeFrom.dashboard', { name: title })}
                    destructive
                    onClick={() => onChange(ids.filter((other) => other !== id))}
                  >
                    <X />
                  </EditButton>
                </div>
              </div>
            )}
            <div inert={editing} className={cn('grid', editing && 'select-none')}>
              {body || (
                <div className="flex items-center justify-center rounded-xl border border-dashed bg-card px-6 py-10 text-sm text-muted-foreground">
                  {t('library.nothingToShow')}
                </div>
              )}
            </div>
          </section>
        )
      })}
    </div>
  )
}

function EditButton({
  label,
  onClick,
  disabled,
  destructive,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  destructive?: boolean
  children: ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          disabled={disabled}
          onClick={onClick}
          className={cn(
            'flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors [&_svg]:size-3.5',
            'hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-35',
            destructive && 'hover:bg-destructive/10 hover:text-destructive',
          )}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}
