import type {
  InvoiceSchedule,
  InvoiceScheduleEndType,
  InvoiceScheduleFrequency,
  InvoiceScheduleStatus,
} from '@/types'

/**
 * Pure helpers for recurring invoices. Everything here is a function of
 * the row the server sent: the server derives the money and the dates,
 * and the UI only decides how to say them.
 */

/** Periods in a year per frequency. Mirrors `PERIODS_PER_YEAR` on the
 *  server and exists here only so a form can preview the monthly figure
 *  before the schedule exists. */
export const PERIODS_PER_YEAR: Record<InvoiceScheduleFrequency, number> = {
  weekly: 52,
  biweekly: 26,
  monthly: 12,
  quarterly: 4,
  semiannual: 2,
  yearly: 1,
}

export const FREQUENCIES: InvoiceScheduleFrequency[] = [
  'weekly',
  'biweekly',
  'monthly',
  'quarterly',
  'semiannual',
  'yearly',
]

/** A per-period amount as a per-month one. A weekly retainer is 52
 *  periods over 12 months, not four a month. */
export function monthlyEquivalent(amount: number, frequency: InvoiceScheduleFrequency): number {
  return Math.round(((amount * PERIODS_PER_YEAR[frequency]) / 12) * 100) / 100
}

/** The tone of a schedule's status pill. Quiet unless something needs a
 *  person: paused after failures is the one loud state, because nothing
 *  will be billed until somebody looks. */
export function scheduleTone(schedule: Pick<InvoiceSchedule, 'status' | 'pause_reason' | 'past_due_count'>): string {
  if (schedule.status === 'paused' && schedule.pause_reason === 'failures') {
    return 'bg-rose-50 text-rose-600 border-rose-100 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/20'
  }
  if (schedule.status === 'active' && schedule.past_due_count > 0) {
    return 'bg-amber-50 text-amber-700 border-amber-100 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/20'
  }
  if (schedule.status === 'active') {
    return 'bg-emerald-50 text-emerald-600 border-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/20'
  }
  return 'bg-muted text-muted-foreground border-border'
}

/** What the pill says. `past_due` is not a stored status: it is an
 *  active agreement with an invoice past its date, and it outranks
 *  "active" on screen because it is the one thing to act on. */
export function scheduleLabel(
  schedule: Pick<InvoiceSchedule, 'status' | 'pause_reason' | 'past_due_count'>,
): InvoiceScheduleStatus | 'past_due' | 'paused_failures' {
  if (schedule.status === 'paused' && schedule.pause_reason === 'failures') return 'paused_failures'
  if (schedule.status === 'active' && schedule.past_due_count > 0) return 'past_due'
  return schedule.status
}

/** Which actions a schedule offers. Derived from the stored decisions
 *  alone, so the buttons and the server agree about what is allowed. */
export function scheduleActions(schedule: Pick<InvoiceSchedule, 'status' | 'invoice_count' | 'origin'>): {
  canPause: boolean
  canResume: boolean
  canEnd: boolean
  canEdit: boolean
  canGenerate: boolean
  canChangePrice: boolean
  canDelete: boolean
} {
  const live = schedule.status !== 'ended'
  const local = schedule.origin === 'local'
  return {
    canPause: schedule.status === 'active',
    canResume: schedule.status === 'paused',
    canEnd: live,
    canEdit: live,
    canGenerate: schedule.status === 'active' && local,
    canChangePrice: live,
    // Nothing was ever billed under it, so nothing is lost with it.
    canDelete: schedule.invoice_count === 0,
  }
}

/** Whether a term is still ahead of today, and so may still be edited
 *  or removed from the UI's point of view. The server has the last
 *  word: it also refuses when an invoice was issued under the term. */
export function isUpcomingTerm(effectiveFrom: string, today = new Date()): boolean {
  const start = new Date(`${effectiveFrom}T00:00:00`)
  const reference = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  return start.getTime() > reference.getTime()
}

/** The period label the invoice list and the picker share: "5 Sep to 4 Oct 2026". */
export function periodLabel(start: string, end: string, locale: string): string {
  const from = new Date(`${start}T00:00:00`)
  const to = new Date(`${end}T00:00:00`)
  const options: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'short', year: 'numeric' }
  const formatter = new Intl.DateTimeFormat(locale, options)
  // `formatRange` knows how each locale writes a span and drops the
  // repeated year on its own; the fallback is for a runtime without it.
  if (typeof formatter.formatRange === 'function') return formatter.formatRange(from, to)
  return `${formatter.format(from)} / ${formatter.format(to)}`
}

/** The end condition as the server takes it: only the field the chosen
 *  shape needs, the other explicitly null so a PATCH clears it. */
export function endPayload(endType: InvoiceScheduleEndType, endDate: string, endCount: string) {
  return {
    end_type: endType,
    end_date: endType === 'on_date' && endDate ? endDate : null,
    end_count: endType === 'after_count' && endCount ? Number(endCount) : null,
  }
}

/** Today as `YYYY-MM-DD` in the viewer's own calendar. Not
 *  `toISOString()`, which is the UTC date: in the evening west of UTC
 *  it is already tomorrow, and a billing date defaulted from it is off
 *  by one without anybody noticing. */
export function localToday(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}
