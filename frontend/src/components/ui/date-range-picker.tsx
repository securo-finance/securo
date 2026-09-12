import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Calendar as CalendarIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { localDateString } from '@/lib/date-utils'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'

export interface DateRangePickerProps {
  /** Inclusive start date as YYYY-MM-DD, or empty string when unset. */
  from: string
  /** Inclusive end date as YYYY-MM-DD, or empty string when unset. */
  to: string
  /** Fired when the user confirms a new range. Empty strings clear it. */
  onChange: (from: string, to: string) => void
  /** Optional label used in the trigger and popover header. */
  label?: string
  /** Extra classes applied to the trigger button. */
  className?: string
  /** Compact styling suitable for report toolbars. */
  size?: 'default' | 'sm'
  /** Trigger content when nothing is selected. */
  placeholder?: string
  /**
   * 'segment' renders the trigger as a plain segment cell (no border/icon of
   * its own) so it can sit inside a segmented control, e.g. a range-preset
   * button group, instead of appearing as a separate floating control.
   */
  variant?: 'button' | 'segment'
  /** Segment highlight state; ignored for variant 'button'. */
  active?: boolean
  /**
   * Fired as soon as the popover starts opening — before the calendar is
   * shown — so a segment trigger can mark itself selected immediately,
   * mirroring the other preset buttons it sits next to.
   */
  onOpen?: () => void
  /**
   * Applied via `onChange` the first time the picker opens with nothing
   * selected yet, so a fresh "Custom" segment starts from a sensible range
   * instead of an empty picker.
   */
  defaultFrom?: string
  defaultTo?: string
}

/**
 * A self-contained "from → to" date picker, mirroring the popover the
 * transactions filter bar uses. Isolated so multiple pages (Reports, Assets,
 * …) can share the same interaction without duplicating state juggling.
 *
 * Deliberately independent of the transactions filter bar's rich chip UI:
 * this variant is a single trigger + popover, which is what report toolbars
 * actually need.
 */
export function DateRangePicker({
  from,
  to,
  onChange,
  label,
  className,
  size = 'sm',
  placeholder,
  variant = 'button',
  active = false,
  onOpen,
  defaultFrom,
  defaultTo,
}: DateRangePickerProps) {
  const { t } = useTranslation()
  const dateLocale = useDisplayLocale()
  const dateFnsLocale = resolveDateFnsLocale(useDateLocale())
  const [open, setOpen] = useState(false)
  const [draftFrom, setDraftFrom] = useState(from)
  const [draftTo, setDraftTo] = useState(to)

  // Reset the drafts every time the popover opens so a preset switch above
  // the picker (which mutates the controlled `from`/`to` props) doesn't
  // surface stale values the next time the user opens it. Done via the
  // Popover callback rather than an effect on props to avoid the cascading
  // renders that react-hooks/set-state-in-effect flags.
  const handleOpenChange = (next: boolean) => {
    if (next) {
      onOpen?.()
      // First open with nothing picked yet: seed from the caller's default
      // (e.g. the current calendar year) instead of an empty calendar, and
      // push it straight into the controlled value so the segment reflects
      // it immediately, before the user touches a single day cell.
      const seedFrom = from || to ? from : defaultFrom ?? from
      const seedTo = from || to ? to : defaultTo ?? to
      setDraftFrom(seedFrom)
      setDraftTo(seedTo)
      if (!from && !to && (seedFrom || seedTo)) {
        onChange(seedFrom, seedTo)
      }
    }
    setOpen(next)
  }

  const headerLabel = label ?? t('transactions.filtersBar.customRange')
  const emptyLabel =
    placeholder ?? t('transactions.filtersBar.pickRange')
  const triggerLabel =
    from || to ? formatRange(from, to, dateLocale) : emptyLabel
  // Segment mode sits inline among short preset labels (6M, 1Y, …), so it
  // drops the year even across a year boundary — the same compact form the
  // transactions filter bar's applied-range chip uses. The year is still
  // visible while the calendar is open, which is the only place picking it
  // actually matters.
  const compactLabel =
    from || to ? formatCompactRange(from, to, dateLocale) : emptyLabel

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        {variant === 'segment' ? (
          <button
            type="button"
            className={cn(
              'px-3 py-1.5 text-xs font-semibold whitespace-nowrap transition-colors',
              active
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
              className,
            )}
            aria-label={headerLabel}
          >
            {active ? compactLabel : (label ?? emptyLabel)}
          </button>
        ) : (
          <Button
            type="button"
            variant="outline"
            size={size}
            className={cn(
              'gap-1.5 whitespace-nowrap font-normal',
              !(from || to) && 'text-muted-foreground',
              className,
            )}
            aria-label={headerLabel}
          >
            <CalendarIcon size={14} />
            {triggerLabel}
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-auto p-0"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <div className="border-b border-border/70 px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            {headerLabel}
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground/70">
            {draftFrom || draftTo
              ? formatRange(draftFrom, draftTo, dateLocale)
              : emptyLabel}
          </p>
        </div>
        <div className="flex flex-col gap-4 p-3 sm:flex-row sm:gap-0">
          <div className="sm:border-r sm:border-border/60 sm:pr-2">
            <p className="px-2 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
              {t('transactions.filtersBar.fromLabel')}
            </p>
            <Calendar
              selected={draftFrom ? new Date(draftFrom + 'T00:00:00') : undefined}
              defaultMonth={
                draftFrom ? new Date(draftFrom + 'T00:00:00') : new Date()
              }
              locale={dateFnsLocale}
              onSelect={(d) => setDraftFrom(d ? localDateString(d) : '')}
            />
          </div>
          <div className="sm:pl-2">
            <p className="px-2 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
              {t('transactions.filtersBar.toLabel')}
            </p>
            <Calendar
              selected={draftTo ? new Date(draftTo + 'T00:00:00') : undefined}
              defaultMonth={
                draftTo
                  ? new Date(draftTo + 'T00:00:00')
                  : draftFrom
                    ? new Date(draftFrom + 'T00:00:00')
                    : new Date()
              }
              locale={dateFnsLocale}
              onSelect={(d) => setDraftTo(d ? localDateString(d) : '')}
            />
          </div>
        </div>
        <div className="flex items-center justify-between gap-2 border-t border-border/70 px-3 py-2">
          <button
            type="button"
            onClick={() => {
              setDraftFrom('')
              setDraftTo('')
            }}
            className="text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            {t('transactions.filtersBar.reset')}
          </button>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setDraftFrom(from)
                setDraftTo(to)
                setOpen(false)
              }}
            >
              {t('transactions.filtersBar.cancel')}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!draftFrom && !draftTo}
              onClick={() => {
                // Normalize: if user only picked one bound, mirror it into
                // the other so downstream code always sees a full range.
                const nextFrom = draftFrom || draftTo
                const nextTo = draftTo || draftFrom
                if (nextFrom && nextTo && nextFrom > nextTo) {
                  onChange(nextTo, nextFrom)
                } else {
                  onChange(nextFrom, nextTo)
                }
                setOpen(false)
              }}
            >
              {t('transactions.filtersBar.apply')}
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}

function formatRange(from: string, to: string, locale: string): string {
  const fmt = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(locale, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    })
  if (from && to) return `${fmt(from)} — ${fmt(to)}`
  if (from) return `≥ ${fmt(from)}`
  return `≤ ${fmt(to)}`
}

// Mirrors the transactions filter bar's applied-range chip: day + short
// month, no year, even when the range crosses a year boundary.
function formatCompactRange(from: string, to: string, locale: string): string {
  const fmt = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(locale, {
      day: '2-digit',
      month: 'short',
    })
  if (from && to) return `${fmt(from)} — ${fmt(to)}`
  if (from) return `≥ ${fmt(from)}`
  return `≤ ${fmt(to)}`
}
