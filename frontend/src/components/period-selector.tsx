import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  addDays,
  addYears,
  format,
  type Locale,
} from 'date-fns'
import {
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  X,
} from 'lucide-react'

import { Calendar } from '@/components/ui/calendar'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Button } from '@/components/ui/button'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { cn } from '@/lib/utils'
import {
  modePicksMonth,
  modePicksYear,
  resolvePeriod,
  shiftAnchor,
  weekStartFromLocale,
  type PeriodMode,
  type PeriodValue,
} from '@/lib/period'

function todayIso(): string {
  const d = new Date()
  return format(d, 'yyyy-MM-dd')
}

interface PeriodSelectorProps {
  value: PeriodValue
  onChange: (next: PeriodValue) => void
  className?: string
  /** When true, hides the mode selector (single-mode embedding). */
  hideModeMenu?: boolean
  /** When true, hides the "hoje" reset button. */
  hideToday?: boolean
}

const MODES: PeriodMode[] = [
  'daily',
  'weekly',
  'monthly',
  'quarterly',
  'half_yearly',
  'yearly',
  'custom',
]

/**
 * Map a period mode to the calendar view the popover should open at.
 *
 * - daily / weekly / custom → day picker (but custom renders a range picker
 *   instead, so this branch never fires the Calendar for custom)
 * - monthly / quarterly / half_yearly → month picker
 * - yearly → year picker
 */
function calendarViewFor(mode: PeriodMode): 'days' | 'months' | 'years' {
  if (mode === 'yearly') return 'years'
  if (mode === 'monthly' || mode === 'quarterly' || mode === 'half_yearly') {
    return 'months'
  }
  return 'days'
}

/**
 * When the user clicks at the calendar's granularity (a month cell in
 * monthly mode or a year cell in yearly mode), derive the new anchor from
 * the picked date. For coarse modes we snap to a representative day inside
 * the chosen cell (1st for month, Jan 1 for year).
 */
function anchorFromPick(mode: PeriodMode, picked: Date): string {
  if (mode === 'yearly') return format(new Date(picked.getFullYear(), 0, 1), 'yyyy-MM-dd')
  if (mode === 'monthly' || mode === 'quarterly' || mode === 'half_yearly') {
    return format(new Date(picked.getFullYear(), picked.getMonth(), 1), 'yyyy-MM-dd')
  }
  return format(picked, 'yyyy-MM-dd')
}

/**
 * The Date object used as the Calendar's `defaultMonth` and `selected`
 * anchor. For non-custom modes it's the anchor date. For custom we use the
 * range midpoint so the calendar opens inside the picked range.
 */
function anchorDateFor(value: PeriodValue): Date {
  if (value.mode === 'custom') {
    const from = parseIsoLocal(value.from)
    const to = parseIsoLocal(value.to)
    return new Date((from.getTime() + to.getTime()) / 2)
  }
  return parseIsoLocal(value.anchor)
}

/** Compute the value button's display label for the current value. */
function formatLabel(value: PeriodValue, dfLocale: Locale, weekStart: number): string {
  if (value.mode === 'custom') {
    return `${format(parseIsoLocal(value.from), 'dd/MM/yy', { locale: dfLocale })} – ${format(parseIsoLocal(value.to), 'dd/MM/yy', { locale: dfLocale })}`
  }
  const r = resolvePeriod(value.mode, value.anchor, weekStart)
  if (value.mode === 'daily') return format(parseIsoLocal(r.start), 'PPP', { locale: dfLocale })
  if (value.mode === 'weekly') {
    return `${format(parseIsoLocal(r.start), 'd MMM', { locale: dfLocale })} – ${format(parseIsoLocal(r.end), 'd MMM yyyy', { locale: dfLocale })}`
  }
  if (value.mode === 'monthly') return format(parseIsoLocal(r.start), 'LLLL yyyy', { locale: dfLocale })
  if (value.mode === 'quarterly') {
    const start = parseIsoLocal(r.start)
    const end = parseIsoLocal(r.end)
    return `${format(start, 'MMM', { locale: dfLocale })} – ${format(end, 'MMM yyyy', { locale: dfLocale })}`
  }
  if (value.mode === 'half_yearly') {
    const start = parseIsoLocal(r.start)
    const end = parseIsoLocal(r.end)
    return `${format(start, 'MMM', { locale: dfLocale })} – ${format(end, 'MMM yyyy', { locale: dfLocale })}`
  }
  // yearly
  return format(parseIsoLocal(r.start), 'yyyy')
}

/** Build a PeriodValue of the given mode with a sensible default anchor/range. */
function defaultValueFor(mode: PeriodMode): PeriodValue {
  const today = todayIso()
  if (mode === 'custom') return { mode: 'custom', from: today, to: today }
  return { mode, anchor: today }
}

export function PeriodSelector({
  value,
  onChange,
  className,
  hideModeMenu,
  hideToday,
}: PeriodSelectorProps) {
  const { t, i18n } = useTranslation()
  const dfLocale = resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language ?? 'en')
  const weekStart = weekStartFromLocale(i18n.resolvedLanguage ?? i18n.language ?? 'en')

  // For non-custom modes, the anchor as a Date — used by the Calendar's
  // selected/defaultMonth props.
  const calendarAnchor = useMemo(() => anchorDateFor(value), [value])

  // ◀ / ▶ stepping. Disabled for custom (no anchor concept).
  const stepAnchor = (direction: -1 | 1) => {
    if (value.mode === 'custom') return
    const nextAnchor =
      value.mode === 'daily'
        ? format(addDays(calendarAnchor, direction), 'yyyy-MM-dd')
        : value.mode === 'weekly'
          ? format(addDays(calendarAnchor, 7 * direction), 'yyyy-MM-dd')
          : value.mode === 'yearly'
            ? format(addYears(calendarAnchor, direction), 'yyyy-MM-dd')
            : shiftAnchor(value.mode, value.anchor, direction)
    onChange({ mode: value.mode, anchor: nextAnchor })
  }

  // 📅 hoje reset. For custom, snap the range to today (single day).
  const goToday = () => {
    const today = todayIso()
    if (value.mode === 'custom') onChange({ mode: 'custom', from: today, to: today })
    else onChange({ mode: value.mode, anchor: today })
  }

  const setMode = (mode: PeriodMode) => {
    onChange(defaultValueFor(mode))
  }

  const label = useMemo(
    () => formatLabel(value, dfLocale, weekStart),
    [value, dfLocale, weekStart],
  )

  const isCustom = value.mode === 'custom'
  // Granular callbacks: only wire them up for modes that actually pick at
  // that granularity. Otherwise the Calendar uses its built-in climb-back
  // (e.g. clicking a year cell in daily mode returns to the month picker
  // instead of committing the year as the new anchor and closing).
  // The cast on the returned PeriodValue is safe: modePicksMonth/ModePicksYear
  // returned true here, which excludes 'custom'.
  const onSelectMonth = modePicksMonth(value.mode)
    ? (date: Date) => onChange({
        mode: value.mode as Exclude<PeriodMode, 'custom'>,
        anchor: anchorFromPick(value.mode, date),
      })
    : undefined
  const onSelectYear = modePicksYear(value.mode)
    ? (date: Date) => onChange({
        mode: value.mode as Exclude<PeriodMode, 'custom'>,
        anchor: anchorFromPick(value.mode, date),
      })
    : undefined

  return (
    <div className={cn('flex w-full flex-wrap items-center gap-x-2 gap-y-1 sm:w-auto md:gap-y-2', className)}>
      <div className="order-2 flex w-full min-w-0 items-center gap-1 md:order-1 md:w-auto md:flex-1 lg:flex-none">
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="h-8 w-8 shrink-0"
          onClick={() => stepAnchor(-1)}
          disabled={isCustom}
          title={t('periodSelector.previous', 'Período anterior')}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>

        {isCustom ? (
          <CustomRangePopover
            value={value}
            onChange={onChange}
            label={label}
          />
        ) : (
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                title={label}
                aria-label={label}
                className="inline-flex flex-1 items-center justify-center gap-2 border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-colors capitalize whitespace-nowrap sm:w-[164px] sm:flex-none"
              >
                {label}
              </button>
            </PopoverTrigger>
            <PopoverContent align="center" className="w-auto p-0">
              <Calendar
                initialView={calendarViewFor(value.mode)}
                locale={dfLocale}
                selected={calendarAnchor}
                defaultMonth={calendarAnchor}
                // Day pick (daily/weekly): set anchor to the picked day and let
                // resolvePeriod snap to the right week/range.
                onSelect={(date) => {
                  if (!date) return
                  onChange({ mode: value.mode, anchor: format(date, 'yyyy-MM-dd') })
                }}
                // Month pick (monthly/quarterly/half_yearly): fires when the mode
                // actually picks at the month granularity. Otherwise the Calendar
                // uses its default behavior (climb back to days view).
                onSelectMonth={onSelectMonth}
                // Year pick (yearly only): fires when mode is yearly. For other
                // modes the Calendar climbs back to months (the user picks year
                // → returns to month picker → confirms month → returns to days).
                onSelectYear={onSelectYear}
              />
            </PopoverContent>
          </Popover>
        )}

        <Button
          type="button"
          variant="outline"
          size="icon"
          className="h-8 w-8 shrink-0"
          onClick={() => stepAnchor(1)}
          disabled={isCustom}
          title={t('periodSelector.next', 'Próximo período')}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>

        {!hideToday && (
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="h-8 w-8 shrink-0"
            onClick={goToday}
            title={t('periodSelector.today', 'Hoje')}
          >
            <CalendarDays className="h-4 w-4" />
          </Button>
        )}
      </div>

      {!hideModeMenu && (
        <div className="order-1 flex h-[30px] w-full max-w-full items-center overflow-hidden rounded-lg border border-border bg-card md:order-2 md:h-[34px] md:w-auto">
          {MODES.map((m) => {
            const modeLabel = t(`periodSelector.${m}`, m)
            const modeShortLabel = t(`periodSelector.${m}Short`, m === 'custom' ? '...' : modeLabel)
            return (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                title={modeLabel}
                aria-label={modeLabel}
                className={cn(
                    'flex h-[30px] min-w-0 flex-1 items-center justify-center px-0 text-xs font-semibold transition-colors md:h-[34px] md:min-w-[34px] md:flex-none',
                  value.mode === m
                    ? 'bg-primary text-primary-foreground font-medium'
                    : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                )}
                aria-pressed={value.mode === m}
              >
                {modeShortLabel}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Custom-range popover
// ---------------------------------------------------------------------------

interface CustomRangePopoverProps {
  value: Extract<PeriodValue, { mode: 'custom' }>
  onChange: (next: PeriodValue) => void
  label: string
}

/**
 * The popover content for custom mode: two DatePickerInput fields (Início,
 * Fim) plus a Confirm button. The state stays local until the user clicks
 * Confirm, so they can fiddle with the inputs without committing.
 */
function CustomRangePopover({ value, onChange, label }: CustomRangePopoverProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [draftFrom, setDraftFrom] = useState(value.from)
  const [draftTo, setDraftTo] = useState(value.to)

  // Reset the draft whenever the popover opens so previous unsaved edits
  // don't leak in (and so external value changes propagate).
  const onOpenChange = (next: boolean) => {
    if (next) {
      setDraftFrom(value.from)
      setDraftTo(value.to)
    }
    setOpen(next)
  }

  const swap = () => {
    setDraftFrom(draftTo)
    setDraftTo(draftFrom)
  }

  const valid = draftFrom && draftTo && draftFrom <= draftTo

  const confirm = () => {
    if (!valid) return
    onChange({ mode: 'custom', from: draftFrom, to: draftTo })
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          title={label}
          aria-label={label}
          className="inline-flex flex-1 items-center justify-center gap-2 border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-colors capitalize whitespace-nowrap sm:w-[164px] sm:flex-none"
        >
          {label}
        </button>
      </PopoverTrigger>
      <PopoverContent align="center" className="w-auto p-3 space-y-3">
        <div className="space-y-1.5">
          <label className="text-xs text-muted-foreground">
            {t('transactions.from', 'Início')} <span className="text-rose-500">*</span>
          </label>
          <DatePickerInput
            value={draftFrom}
            onChange={setDraftFrom}
            placeholder={t('transactions.from', 'Início')}
            align="start"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs text-muted-foreground">
            {t('transactions.to', 'Fim')} <span className="text-rose-500">*</span>
          </label>
          <DatePickerInput
            value={draftTo}
            onChange={setDraftTo}
            placeholder={t('transactions.to', 'Fim')}
            align="start"
          />
        </div>
        <div className="flex items-center justify-between pt-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={swap}
            disabled={!draftFrom || !draftTo}
            title={t('periodSelector.swap', 'Inverter')}
          >
            <span className="text-xs">⇅</span>
          </Button>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              onClick={() => setOpen(false)}
              title={t('periodSelector.cancel', 'Cancel')}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
            <Button
              type="button"
              variant="default"
              size="icon"
              className="h-7 w-7"
              onClick={confirm}
              disabled={!valid}
              title={t('periodSelector.confirm', 'Confirm')}
            >
              <Check className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}

// ---------------------------------------------------------------------------
// Local helpers
// ---------------------------------------------------------------------------

function parseIsoLocal(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}
