import { useMemo } from 'react'
import type { TFunction } from 'i18next'
import { formatCurrency } from '@/lib/format'
import { cleanDescription, type DayEntry, type Tone } from '@/lib/spending-plan'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import type { SpendingPlanEvent } from '@/types'

const MINUS = '−'

function asDate(iso: string) {
  return new Date(`${iso}T00:00:00`)
}

/** Money and date formatting for the plan page, masked in privacy mode. */
export function usePlanFormat(currency: string) {
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  return useMemo(() => {
    const compactFormatter = new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 })
    const money = (value: number) => mask(formatCurrency(value, currency, locale))
    const sign = (value: number) => (Math.round(value * 100) === 0 ? '' : value > 0 ? '+' : MINUS)
    return {
      money,
      /** "+R$ 10,00" or "−R$ 10,00". */
      signed: (value: number) => `${sign(value)}${money(Math.abs(value))}`,
      /** "2,4 mil", for a calendar cell. */
      compact: (value: number) => mask(`${value < 0 ? MINUS : ''}${compactFormatter.format(Math.abs(value))}`),
      signedCompact: (value: number) => mask(`${sign(value)}${compactFormatter.format(Math.abs(value))}`),
      /** "20/01/27". */
      shortDate: (iso: string) =>
        asDate(iso).toLocaleDateString(dateLocale, { day: '2-digit', month: '2-digit', year: '2-digit' }),
      /** "20/01". */
      dayMonth: (iso: string) => asDate(iso).toLocaleDateString(dateLocale, { day: '2-digit', month: '2-digit' }),
      /** "qua., 20 de janeiro de 2027". */
      longDate: (iso: string) =>
        asDate(iso).toLocaleDateString(dateLocale, { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' }),
      /** "Novembro de 2026". */
      monthTitle: (month: string) => {
        const title = asDate(`${month}-01`).toLocaleDateString(dateLocale, { month: 'long', year: 'numeric' })
        return title.charAt(0).toUpperCase() + title.slice(1)
      },
      /** "dom." for a 0 (Sunday) through 6 weekday. */
      weekdayShort: (weekday: number) =>
        new Date(Date.UTC(2023, 0, 1 + weekday)).toLocaleDateString(dateLocale, { weekday: 'short', timeZone: 'UTC' }),
    }
  }, [mask, currency, locale, dateLocale])
}

export type PlanFormat = ReturnType<typeof usePlanFormat>

export function eventLabel(event: SpendingPlanEvent, t: TFunction) {
  if (event.description) return cleanDescription(event.description)
  return event.source === 'scenario' ? t('spendingPlan.whatIf') : t('spendingPlan.otherMoves')
}

export function entryLabel(entry: DayEntry, t: TFunction) {
  if (entry.kind === 'event') return eventLabel(entry.event, t)
  return entry.account ? t('spendingPlan.billFrom', { account: entry.account }) : t('spendingPlan.billGeneric')
}

export const TONE_BAND: Record<Tone, string> = {
  good: 'bg-emerald-500/70 dark:bg-emerald-400/60',
  warn: 'bg-amber-400 dark:bg-amber-400/70',
  bad: 'bg-rose-500/80 dark:bg-rose-400/70',
}

export const TONE_TEXT: Record<Tone, string> = {
  good: 'text-foreground',
  warn: 'text-foreground',
  bad: 'text-rose-600 dark:text-rose-400',
}

export const INCOME_TEXT = 'text-emerald-600 dark:text-emerald-400'
export const SCENARIO_TEXT = 'text-amber-600 dark:text-amber-400'

export function entryTone(entry: DayEntry) {
  if (entry.kind === 'event' && entry.event.source === 'scenario') return SCENARIO_TEXT
  return entry.amount > 0 ? INCOME_TEXT : 'text-muted-foreground'
}
