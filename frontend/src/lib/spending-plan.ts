import type { SpendingPlanEvent, SpendingPlanTimeline } from '@/types'

export function formatPlanDate(value: string, dateLocale: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString(dateLocale, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/** "Covered until 18 Dec 2027", "covered through the horizon" or "not covered". */
export function coverageLabel(
  timeline: SpendingPlanTimeline,
  dateLocale: string,
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  if (timeline.covered_until === null) return t('spendingPlan.notCovered')
  const date = formatPlanDate(timeline.covered_until, dateLocale)
  return timeline.covered_through_horizon
    ? t('spendingPlan.coveredThrough', { date })
    : t('spendingPlan.coveredUntil', { date })
}

type Point = SpendingPlanTimeline['points'][number]

/** The projected balance at the end of ``date``: the last point on or before it. */
export function balanceOn(points: Point[], date: string): number | null {
  let found: number | null = null
  for (const point of points) {
    if (point.date > date) break
    found = point.balance
  }
  return found
}

// ---- ISO dates (YYYY-MM-DD), computed in UTC so no timezone shifts a day ----

const DAY_MS = 86_400_000

function utc(iso: string) {
  return Date.parse(`${iso}T00:00:00Z`)
}

function isoOf(ms: number) {
  return new Date(ms).toISOString().slice(0, 10)
}

export function addDays(iso: string, days: number) {
  return isoOf(utc(iso) + days * DAY_MS)
}

/** Same day ``months`` later, clamped to the end of a shorter month. */
export function addMonths(iso: string, months: number) {
  const [year, month, day] = iso.split('-').map(Number)
  const lastDay = new Date(Date.UTC(year, month - 1 + months + 1, 0)).getUTCDate()
  return isoOf(Date.UTC(year, month - 1 + months, Math.min(day, lastDay)))
}

export function endOfMonth(iso: string) {
  const [year, month] = iso.split('-').map(Number)
  return isoOf(Date.UTC(year, month, 0))
}

/** Days from ``from`` through ``to``, both included. */
export function daysThrough(from: string, to: string) {
  return Math.round((utc(to) - utc(from)) / DAY_MS) + 1
}

export function weekday(iso: string) {
  return new Date(utc(iso)).getUTCDay()
}

/** The months from ``from``'s through ``to``'s, as YYYY-MM. */
export function monthsBetween(from: string, to: string) {
  const months: string[] = []
  for (let month = `${from.slice(0, 7)}-01`; month.slice(0, 7) <= to.slice(0, 7); month = addMonths(month, 1)) {
    months.push(month.slice(0, 7))
  }
  return months
}

/** A Sunday-first grid for ``month`` (YYYY-MM): null pads the first week. */
export function monthGrid(month: string): (string | null)[] {
  const first = `${month}-01`
  const days = daysThrough(first, endOfMonth(first))
  return [
    ...Array.from({ length: weekday(first) }, () => null),
    ...Array.from({ length: days }, (_, index) => addDays(first, index)),
  ]
}

// ---- Money in cents, so sums of many events never drift ----

const cents = (value: number) => Math.round(value * 100)
const sumCents = (values: number[]) => values.reduce((total, value) => total + cents(value), 0) / 100

// ---- The plan's model ----

/** How comfortable a balance is: well above the floor, near it, or below it. */
export type Tone = 'good' | 'warn' | 'bad'

/** A balance within this much of the floor is "near" it. */
export const COMFORT_MARGIN = 1500

export function balanceTone(balance: number, floor: number): Tone {
  if (balance < floor) return 'bad'
  return balance < floor + COMFORT_MARGIN ? 'warn' : 'good'
}

/** What the daily allowance takes on ``day``: today only what is left of it. */
export function allowanceOn(day: string, timeline: SpendingPlanTimeline) {
  const today = planToday(timeline)
  if (day < today) return 0
  if (day > today) return timeline.daily_allowance
  return Math.max(timeline.daily_allowance - timeline.today.spent, 0)
}

export function planToday(timeline: SpendingPlanTimeline) {
  return timeline.points[0]?.date ?? ''
}

/**
 * "Parcela 5/12" and "1ª parcela" mark one installment of a purchase. Without
 * them every installment of the same purchase reads as one item.
 */
export function cleanDescription(description: string) {
  return description
    .replace(/\s*\((?:parcela [^)]*|\d+ª parcela)\)/i, '')
    .replace(/\s+\d+\/\d+$/, '')
    .trim()
}

/** The receipt's and the waterfall's buckets: every event falls in exactly one. */
export type EventGroup = 'income' | 'fixed' | 'card' | 'scenario' | 'other'

export function eventGroup(event: SpendingPlanEvent): EventGroup {
  switch (event.source) {
    case 'card':
    case 'scenario':
    case 'other':
      return event.source
    case 'recurring':
    case 'planned':
      return event.amount > 0 ? 'income' : 'fixed'
  }
}

/** One line on a day: an event, or every card charge due that day on one card. */
export type DayEntry =
  | { kind: 'event'; event: SpendingPlanEvent; amount: number }
  | { kind: 'bill'; account: string | null; amount: number; children: SpendingPlanEvent[] }

export function dayEntries(events: SpendingPlanEvent[]): DayEntry[] {
  const entries: DayEntry[] = []
  const bills = new Map<string | null, SpendingPlanEvent[]>()
  for (const event of events) {
    if (event.source !== 'card') {
      entries.push({ kind: 'event', event, amount: event.amount })
      continue
    }
    const charges = bills.get(event.account_name) ?? []
    charges.push(event)
    bills.set(event.account_name, charges)
  }
  for (const [account, children] of bills) {
    entries.push({ kind: 'bill', account, amount: sumCents(children.map((c) => c.amount)), children })
  }
  return entries.sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount))
}

export function eventsByDate(events: SpendingPlanEvent[]) {
  const byDate = new Map<string, SpendingPlanEvent[]>()
  for (const event of events) {
    const day = byDate.get(event.date) ?? []
    day.push(event)
    byDate.set(event.date, day)
  }
  return byDate
}

export interface LabeledAmount {
  /** Null for a line without a name: the "other" remainder or an unnamed what-if. */
  label: string | null
  amount: number
}

/** Sum events under their cleaned name, largest first. */
function byLabel(events: SpendingPlanEvent[], labelOf: (event: SpendingPlanEvent) => string | null) {
  const totals = new Map<string | null, number[]>()
  for (const event of events) {
    const label = labelOf(event)
    totals.set(label, [...(totals.get(label) ?? []), event.amount])
  }
  return [...totals]
    .map(([label, amounts]): LabeledAmount => ({ label, amount: sumCents(amounts) }))
    .sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount))
}

const cleanLabel = (event: SpendingPlanEvent) => (event.description ? cleanDescription(event.description) : null)

export interface Bucket {
  total: number
  items: LabeledAmount[]
}

function buckets(events: SpendingPlanEvent[]): Record<EventGroup, Bucket> {
  const grouped: Record<EventGroup, SpendingPlanEvent[]> = { income: [], fixed: [], card: [], scenario: [], other: [] }
  for (const event of events) grouped[eventGroup(event)].push(event)
  const bucket = (list: SpendingPlanEvent[], labelOf = cleanLabel): Bucket => ({
    total: sumCents(list.map((event) => event.amount)),
    items: byLabel(list, labelOf),
  })
  return {
    income: bucket(grouped.income),
    fixed: bucket(grouped.fixed),
    card: bucket(grouped.card),
    scenario: bucket(grouped.scenario),
    other: { total: sumCents(grouped.other.map((event) => event.amount)), items: [] },
  }
}

export interface Waterfall {
  date: string
  opening: number
  income: Bucket
  fixed: Bucket
  /** Items are the bills per card. */
  card: Bucket
  scenario: Bucket
  other: number
  days: number
  /** What the daily allowance took from today through ``date``, negative. */
  daily: number
  balance: number
}

/**
 * The balance at the end of ``date``, taken apart: the opening balance, every
 * event from today through ``date`` by bucket, and the daily allowance.
 * "other" is whatever the named buckets leave of the projected balance, so the
 * parts always add up to it to the cent.
 */
export function waterfallFor(timeline: SpendingPlanTimeline, date: string): Waterfall | null {
  const balance = balanceOn(timeline.points, date)
  const today = planToday(timeline)
  if (balance === null || date < today) return null
  const events = timeline.events.filter((event) => event.date <= date)
  const parts = buckets(events)
  const days = daysThrough(today, date)
  const daily = -(cents(allowanceOn(today, timeline)) + cents(timeline.daily_allowance) * (days - 1)) / 100
  const named = [timeline.opening_balance, parts.income.total, parts.fixed.total, parts.card.total, parts.scenario.total, daily]
  return {
    date,
    opening: timeline.opening_balance,
    ...parts,
    card: {
      total: parts.card.total,
      items: byLabel(events.filter((event) => event.source === 'card'), (event) => event.account_name),
    },
    other: (cents(balance) - named.reduce((total, value) => total + cents(value), 0)) / 100,
    days,
    daily,
    balance,
  }
}

export interface Receipt {
  /** The binding day: the safe allowance is what fits from today through it. */
  through: string
  days: number
  opening: number
  groups: { key: EventGroup; bucket: Bucket }[]
  /**
   * Today's variable spending. It is already in today's events, and today's
   * allowance only covers what is left of it, so it comes back here to count once.
   */
  spentToday: number
  floor: number
  /** What is left for day-to-day spending through the binding day. */
  free: number
  /** ``free`` per day, floored to cents like the safe allowance. */
  perDay: number
}

const RECEIPT_ORDER: EventGroup[] = ['income', 'fixed', 'card', 'scenario', 'other']

/** Everything that moves the balance from today through the binding day. */
export function planReceipt(timeline: SpendingPlanTimeline): Receipt | null {
  const through = timeline.binding_date
  if (!through) return null
  const parts = buckets(timeline.events.filter((event) => event.date <= through))
  const groups = RECEIPT_ORDER
    .map((key) => ({ key, bucket: parts[key] }))
    .filter(({ key, bucket }) => (key === 'other' ? cents(bucket.total) !== 0 : bucket.items.length > 0))
  const days = daysThrough(planToday(timeline), through)
  const free = sumCents([
    timeline.opening_balance,
    ...groups.map(({ bucket }) => bucket.total),
    timeline.today.spent,
    -timeline.balance_floor,
  ])
  return {
    through,
    days,
    opening: timeline.opening_balance,
    groups,
    spentToday: timeline.today.spent,
    floor: timeline.balance_floor,
    free,
    perDay: Math.floor(cents(free) / days) / 100,
  }
}

/** The first day the balance drops below the floor, or null when it never does. */
export function firstDayBelowFloor(timeline: SpendingPlanTimeline) {
  if (timeline.covered_through_horizon) return null
  return timeline.covered_until ? addDays(timeline.covered_until, 1) : planToday(timeline)
}

export type LookupShortcut = 'monthEnd' | 'yearEnd' | 'threeMonths' | 'sixMonths' | 'horizon'

/** Dates the balance lookup jumps to, each kept within the plan. */
export function lookupShortcuts(today: string, horizonEnd: string): { key: LookupShortcut; date: string }[] {
  const year = Number(today.slice(0, 4))
  const yearEnd = daysThrough(today, `${year}-12-31`) <= 30 ? `${year + 1}-12-31` : `${year}-12-31`
  const clamp = (date: string) => (date > horizonEnd ? horizonEnd : date)
  return [
    { key: 'monthEnd', date: clamp(endOfMonth(today)) },
    { key: 'yearEnd', date: clamp(yearEnd) },
    { key: 'threeMonths', date: clamp(addMonths(today, 3)) },
    { key: 'sixMonths', date: clamp(addMonths(today, 6)) },
    { key: 'horizon', date: horizonEnd },
  ]
}
