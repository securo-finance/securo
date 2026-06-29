import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  addDays,
  addYears,
  format,
  type Locale,
} from 'date-fns'
import { ptBR, enUS, es, ja, ko, zhCN, he } from 'date-fns/locale'
import { ChevronLeft, ChevronRight, CalendarDays, ChevronDown } from 'lucide-react'

import { Calendar } from '@/components/ui/calendar'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
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
  // Match by full tag first, then base language, then fall back to enUS.
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
   * `{mode, anchor}` JSON under this key on mount and on every change.
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
 * Map a period mode to the calendar view the popover should open at, and
 * to the anchor derivation when the user picks at that granularity.
 *
 * - daily / weekly / custom → day picker (full calendar grid)
 * - monthly / quarterly / half_yearly → month picker (12 cells)
 * - yearly → year picker (12 years per page)
 */
function calendarViewFor(mode: PeriodMode): 'days' | 'months' | 'years' {
  if (mode === 'yearly') return 'years'
  if (mode === 'monthly' || mode === 'quarterly' || mode === 'half_yearly') {
    return 'months'
  }
  return 'days'
}

/**
 * When the user clicks at the calendar's granularity (e.g. a month cell in
 * monthly mode), derive the new anchor from the picked date. The anchor is
 * what `resolvePeriod` and `shiftAnchor` work with; for coarse modes we
 * snap to a representative day inside the chosen cell (1st for month/year).
 */
function anchorFromPick(mode: PeriodMode, picked: Date): string {
  if (mode === 'yearly') return format(new Date(picked.getFullYear(), 0, 1), 'yyyy-MM-dd')
  if (mode === 'monthly' || mode === 'quarterly' || mode === 'half_yearly') {
    return format(new Date(picked.getFullYear(), picked.getMonth(), 1), 'yyyy-MM-dd')
  }
  return format(picked, 'yyyy-MM-dd')
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

  // Persist to localStorage on every change (best-effort — silently ignores
  // quota errors or SSR where window is undefined).
  useEffect(() => {
    if (!storageKey) return
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(value))
    } catch {
      // ignore
    }
  }, [storageKey, value])

  // Current anchor as Date for calendar defaultMonth. Clamp day=1 if mode is
  // coarse so the calendar opens on the right month/year page.
  const anchorDate = useMemo(() => {
    const [y, m, d] = value.anchor.split('-').map(Number)
    return new Date(y, m - 1, d)
  }, [value.anchor])

  // Stepping anchor by ◀ / ▶. For daily/weekly/custom we use date-fns so the
  // week boundary (week_start) is respected for weekly; for the others we
  // delegate to period.shiftAnchor which clamps day overflow.
  const stepAnchor = (direction: -1 | 1) => {
    if (value.mode === 'custom') return // arrows disabled anyway
    const nextAnchor =
      value.mode === 'daily'
        ? format(addDays(anchorDate, direction), 'yyyy-MM-dd')
        : value.mode === 'weekly'
          ? format(addDays(anchorDate, 7 * direction), 'yyyy-MM-dd')
          : value.mode === 'yearly'
            ? format(addYears(anchorDate, direction), 'yyyy-MM-dd')
            : shiftAnchor(value.mode, value.anchor, direction)
    onChange({ mode: value.mode, anchor: nextAnchor })
  }

  const goToday = () => {
    onChange({ mode: value.mode, anchor: todayIso() })
  }

  const setMode = (mode: PeriodMode) => {
    onChange({ mode, anchor: todayIso() })
  }

  // Compute the label that appears in the value button. Different format per
  // mode so the user gets useful context without opening the picker.
  const label = useMemo(() => {
    const r = resolvePeriod(value.mode, value.anchor, weekStart)
    if (value.mode === 'daily') {
      return format(parseIsoLocal(r.start), 'PPP', { locale: dfLocale })
    }
    if (value.mode === 'weekly') {
      return `${format(parseIsoLocal(r.start), 'd MMM', { locale: dfLocale })} – ${format(parseIsoLocal(r.end), 'd MMM', { locale: dfLocale })}`
    }
    if (value.mode === 'monthly') {
      return format(parseIsoLocal(r.start), 'LLLL yyyy', { locale: dfLocale })
    }
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
    if (value.mode === 'yearly') {
      return format(parseIsoLocal(r.start), 'yyyy')
    }
    // custom
    return `${format(parseIsoLocal(r.start), 'd MMM', { locale: dfLocale })} – ${format(parseIsoLocal(r.end), 'd MMM', { locale: dfLocale })}`
  }, [value.mode, value.anchor, dfLocale, weekStart])

// Build the range string so callers can render "Saldo: ..." alongside. We
  // expose it via window for now — pages can compute it themselves using
  // resolvePeriod + their preferred formatter.
  // (intentionally not exposed as prop to keep the component minimal)

  const initialView = calendarViewFor(value.mode)
  const isCustom = value.mode === 'custom'

  return (
    <div className={cn('inline-flex items-center gap-1', className)}>
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="h-8 w-8"
        onClick={() => stepAnchor(-1)}
        disabled={isCustom}
        title={t('periodSelector.previous', 'Período anterior')}
      >
        <ChevronLeft className="h-4 w-4" />
      </Button>

      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="inline-flex items-center justify-center gap-2 min-w-[140px] border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-colors capitalize"
          >
            {label}
          </button>
        </PopoverTrigger>
        <PopoverContent align="center" className="w-auto p-0">
          <Calendar
            initialView={initialView}
            locale={dfLocale}
            selected={anchorDate}
            defaultMonth={anchorDate}
            // Day pick (daily/weekly/custom): set anchor to the picked day
            // and let resolvePeriod snap to the right week/range.
            onSelect={(date) => {
              if (!date) return
              onChange({ mode: value.mode, anchor: format(date, 'yyyy-MM-dd') })
            }}
            // Month pick (monthly/quarterly/half_yearly): anchor to day 1 of
            // the chosen month so resolvePeriod returns that whole month (or
            // quarter/semester containing it).
            onSelectMonth={(date) => {
              onChange({ mode: value.mode, anchor: anchorFromPick(value.mode, date) })
            }}
            // Year pick (yearly): anchor to Jan 1 of the chosen year.
            onSelectYear={(date) => {
              onChange({ mode: value.mode, anchor: anchorFromPick(value.mode, date) })
            }}
          />
        </PopoverContent>
      </Popover>

      <Button
        type="button"
        variant="outline"
        size="icon"
        className="h-8 w-8"
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
          className="h-8 w-8"
          onClick={goToday}
          title={t('periodSelector.today', 'Hoje')}
        >
          <CalendarDays className="h-4 w-4" />
        </Button>
      )}

      {!hideModeMenu && (
        <Popover>
          <PopoverTrigger asChild>
            <button
              type="button"
              className="inline-flex items-center justify-center gap-1.5 min-w-[110px] border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-colors"
            >
              {t(`periodSelector.${value.mode}`, value.mode)}
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
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
// Local helpers
// ---------------------------------------------------------------------------

function parseIsoLocal(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}