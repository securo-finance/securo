import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  addDays,
  addYears,
  format,
  type Locale,
} from 'date-fns'
import { ptBR, enUS, es, ja, ko, zhCN, he } from 'date-fns/locale'
import {
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  MoreVertical,
  X,
} from 'lucide-react'

import { Calendar } from '@/components/ui/calendar'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Button } from '@/components/ui/button'
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

const DATE_FNS_LOCALES: Record<string, Locale> = {
  'pt-BR': ptBR,
  en: enUS,
  es: es,
  ja: ja,
  ko: ko,
  zh: zhCN,
  he: he,
}

function resolveDateFnsLocale(lang: string): Locale {
  if (DATE_FNS_LOCALES[lang]) return DATE_FNS_LOCALES[lang]
  const base = lang.split('-')[0]
  if (DATE_FNS_LOCALES[base]) return DATE_FNS_LOCALES[base]
  return enUS
}

function todayIso(): string {
  const d = new Date()
  return format(d, 'yyyy-MM-dd')
}

interface PeriodSelectorProps {
  value: PeriodValue
  onChange: (next: PeriodValue) => void
  /**
   * Storage key for the localStorage persistence slot. Pass an account id so
   * each account remembers its own mode+anchor. The component reads/writes
   * `{mode, ...}` JSON under this key on mount and on every change.
   */
  storageKey?: string
  className?: string
  /** When true, hides the mode dropdown (single-mode embedding). */
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
    return `${format(parseIsoLocal(value.from), 'd MMM', { locale: dfLocale })} – ${format(parseIsoLocal(value.to), 'd MMM', { locale: dfLocale })}`
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
    const q = Math.floor(start.getMonth() / 3) + 1
    return `Q${q} ${start.getFullYear()} (${format(start, 'MMM', { locale: dfLocale })}–${format(end, 'MMM', { locale: dfLocale })})`
  }
  if (value.mode === 'half_yearly') {
    const start = parseIsoLocal(r.start)
    const s = start.getMonth() < 6 ? 1 : 2
    const end = parseIsoLocal(r.end)
    return `S${s} ${start.getFullYear()} (${format(start, 'MMM', { locale: dfLocale })}–${format(end, 'MMM', { locale: dfLocale })})`
  }
  // yearly
  return format(parseIsoLocal(r.start), 'yyyy')
}

/**
 * Ultra-short label for the narrowest breakpoint (< 375px). Even weekly
 * ranges drop the year, and quarterly/half-yearly collapse to "Q2 2026" /
 * "S1 2026". The whole component stacks vertically at this width so the
 * label has the full row to itself, but we still trim aggressively because
 * three 32×32 icon buttons eat most of the horizontal budget.
 */
function formatLabelUltraShort(value: PeriodValue, dfLocale: Locale, weekStart: number): string {
  if (value.mode === 'custom') {
    return `${format(parseIsoLocal(value.from), 'dd/MM', { locale: dfLocale })} – ${format(parseIsoLocal(value.to), 'dd/MM', { locale: dfLocale })}`
  }
  const r = resolvePeriod(value.mode, value.anchor, weekStart)
  if (value.mode === 'daily') return format(parseIsoLocal(r.start), 'dd/MM/yyyy', { locale: dfLocale })
  if (value.mode === 'weekly') {
    // No year — saving ~30px buys enough room for "28 Jun – 4 Jul" on a 320px viewport.
    return `${format(parseIsoLocal(r.start), 'd MMM', { locale: dfLocale })} – ${format(parseIsoLocal(r.end), 'd MMM', { locale: dfLocale })}`
  }
  if (value.mode === 'monthly') return format(parseIsoLocal(r.start), 'MMM yyyy', { locale: dfLocale })
  if (value.mode === 'quarterly') {
    const start = parseIsoLocal(r.start)
    const q = Math.floor(start.getMonth() / 3) + 1
    return `Q${q} ${start.getFullYear()}`
  }
  if (value.mode === 'half_yearly') {
    const start = parseIsoLocal(r.start)
    const s = start.getMonth() < 6 ? 1 : 2
    return `S${s} ${start.getFullYear()}`
  }
  // yearly
  return format(parseIsoLocal(r.start), 'yyyy')
}

/**
 * Medium label for the [375px, 640px) range — phone landscape / large
 * mobile. Room for the year on weekly, and quarterly/half-yearly can drop
 * the "Q2"/"S1" prefix in favor of explicit month ranges ("abr – jun 2026").
 */
function formatLabelMedium(value: PeriodValue, dfLocale: Locale, weekStart: number): string {
  if (value.mode === 'custom') {
    return `${format(parseIsoLocal(value.from), 'dd/MM', { locale: dfLocale })} – ${format(parseIsoLocal(value.to), 'dd/MM', { locale: dfLocale })}`
  }
  const r = resolvePeriod(value.mode, value.anchor, weekStart)
  if (value.mode === 'daily') return format(parseIsoLocal(r.start), 'd MMM yyyy', { locale: dfLocale })
  if (value.mode === 'weekly') {
    return `${format(parseIsoLocal(r.start), 'd MMM', { locale: dfLocale })} – ${format(parseIsoLocal(r.end), 'd MMM yyyy', { locale: dfLocale })}`
  }
  if (value.mode === 'monthly') return format(parseIsoLocal(r.start), 'MMM yyyy', { locale: dfLocale })
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
  storageKey,
  className,
  hideModeMenu,
  hideToday,
}: PeriodSelectorProps) {
  const { t, i18n } = useTranslation()
  const dfLocale = resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language ?? 'en')
  const weekStart = weekStartFromLocale(i18n.resolvedLanguage ?? i18n.language ?? 'en')
  const [modeMenuOpen, setModeMenuOpen] = useState(false)

  // Persist to localStorage on every change.
  useEffect(() => {
    if (!storageKey) return
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(value))
    } catch {
      // ignore
    }
  }, [storageKey, value])

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
    setModeMenuOpen(false)
  }

  const label = useMemo(
    () => formatLabel(value, dfLocale, weekStart),
    [value, dfLocale, weekStart],
  )
  const mediumLabel = useMemo(
    () => formatLabelMedium(value, dfLocale, weekStart),
    [value, dfLocale, weekStart],
  )
  const shortLabel = useMemo(
    () => formatLabelUltraShort(value, dfLocale, weekStart),
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
    <div className={cn('flex w-full flex-wrap items-center gap-2 sm:w-auto', className)}>
      <div className="order-2 flex w-full min-w-0 items-center gap-1 min-[375px]:order-1 min-[375px]:w-auto min-[375px]:flex-1 sm:flex-none">
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
            mediumLabel={mediumLabel}
            shortLabel={shortLabel}
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
                <span className="min-[375px]:hidden">{shortLabel}</span>
                <span className="hidden min-[375px]:inline sm:hidden">{mediumLabel}</span>
                <span className="hidden sm:inline">{label}</span>
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
        <Popover open={modeMenuOpen} onOpenChange={setModeMenuOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              title={t(`periodSelector.${value.mode}`, value.mode)}
              aria-label={t(`periodSelector.${value.mode}`, value.mode)}
              className={cn(
                'inline-flex items-center justify-center gap-1.5 border border-border rounded-lg text-sm bg-card text-foreground hover:bg-muted/50 transition-colors',
                'order-1 w-full px-3',
                'min-[375px]:order-2 min-[375px]:h-8 min-[375px]:w-8 min-[375px]:p-0 min-[375px]:shrink-0',
                'sm:h-auto sm:w-auto sm:px-3 sm:py-1.5 sm:min-w-[110px]',
              )}
            >
              <span className="min-[375px]:hidden sm:inline whitespace-nowrap">{t(`periodSelector.${value.mode}`, value.mode)}</span>
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground min-[375px]:hidden sm:block" />
              <MoreVertical className="hidden h-4 w-4 text-muted-foreground min-[375px]:block sm:hidden" />
            </button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-44 p-1">
            {MODES.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={cn(
                  'w-full text-left px-3 py-1.5 text-sm rounded-md transition-colors',
                  value.mode === m
                    ? 'bg-primary text-primary-foreground font-medium'
                    : 'text-foreground hover:bg-muted/60',
                )}
              >
                {t(`periodSelector.${m}`, m)}
              </button>
            ))}
          </PopoverContent>
        </Popover>
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
  mediumLabel: string
  shortLabel: string
}

/**
 * The popover content for custom mode: two DatePickerInput fields (Início,
 * Fim) plus a Confirm button. The state stays local until the user clicks
 * Confirm, so they can fiddle with the inputs without committing.
 */
function CustomRangePopover({ value, onChange, label, mediumLabel, shortLabel }: CustomRangePopoverProps) {
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
          <span className="min-[375px]:hidden">{shortLabel}</span>
          <span className="hidden min-[375px]:inline sm:hidden">{mediumLabel}</span>
          <span className="hidden sm:inline">{label}</span>
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
              title={t('common.cancel', 'Cancelar')}
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
              title={t('common.confirm', 'Confirmar')}
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
