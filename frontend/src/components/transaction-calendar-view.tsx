import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftRight, CalendarDays, ChartNoAxesColumn, CircleDot, Clock, EyeClosed, Minus } from 'lucide-react'
import type { Account, TransactionCalendarDay, TransactionCalendarItem, TransactionCalendarResponse } from '@/types'
import { Skeleton } from '@/components/ui/skeleton'
import { AccountIcon } from '@/components/account-icon'
import { CategoryIcon } from '@/components/category-icon'
import { ProjectedTransactionBadge } from '@/components/projected-transaction-badge'
import { getAccountName } from '@/lib/account-utils'
import { activityChartData, dayActivity, isCalendarItemInteractive } from '@/lib/calendar-activity'
import { weekdayShortLabels } from '@/lib/date-utils'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { shouldShowPendingBadge } from '@/lib/transaction-status'

function parseLocalDate(value: string) {
  return new Date(`${value}T00:00:00`)
}

function compactCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

function signedAmount(item: TransactionCalendarItem) {
  return item.type === 'credit' ? Math.abs(item.amount) : -Math.abs(item.amount)
}

function displayDayNumber(date: string) {
  return parseLocalDate(date).getDate()
}

function todayIso() {
  return new Date().toISOString().split('T')[0]
}

// A busy day can hold dozens of rows, and only three fit. Showing the biggest movers
// makes those three worth reading; showing an arbitrary three did not.
function itemSize(item: TransactionCalendarItem) {
  return Math.abs(item.amount_primary ?? item.amount)
}

function topItemsBySize(items: TransactionCalendarItem[], limit: number) {
  return [...items].sort((a, b) => itemSize(b) - itemSize(a)).slice(0, limit)
}

type CalendarMarkerTone = 'income' | 'expense' | 'transfer' | 'projected'
type CalendarDensity = 'compact' | 'detailed'
type CalendarMetric = 'balance' | 'activity'
const CALENDAR_DENSITY_STORAGE_KEY = 'securo.transactionCalendar.density'
const CALENDAR_METRIC_STORAGE_KEY = 'securo.transactionCalendar.metric'

function readCalendarDensity(): CalendarDensity {
  if (typeof window === 'undefined') return 'compact'
  return window.localStorage.getItem(CALENDAR_DENSITY_STORAGE_KEY) === 'detailed' ? 'detailed' : 'compact'
}

function readCalendarMetric(): CalendarMetric {
  if (typeof window === 'undefined') return 'balance'
  return window.localStorage.getItem(CALENDAR_METRIC_STORAGE_KEY) === 'activity' ? 'activity' : 'balance'
}

export function TransactionCalendarView({
  calendar,
  isLoading,
  locale,
  dateLocale,
  mask,
  selectedDate,
  onSelectedDateChange,
  onOpenTransaction,
  accounts,
  userCurrency,
}: {
  calendar?: TransactionCalendarResponse
  isLoading: boolean
  locale: string
  dateLocale: string
  mask: (value: string) => string
  selectedDate: string
  onSelectedDateChange: (date: string) => void
  onOpenTransaction: (id: string) => void
  accounts?: Account[]
  userCurrency: string
}) {
  const [density, setDensity] = useState<CalendarDensity>(readCalendarDensity)
  const [metric, setMetric] = useState<CalendarMetric>(readCalendarMetric)

  const accountById = useMemo(() => {
    const map = new Map<string, Account>()
    for (const account of accounts ?? []) map.set(account.id, account)
    return map
  }, [accounts])

  useEffect(() => {
    window.localStorage.setItem(CALENDAR_DENSITY_STORAGE_KEY, density)
  }, [density])

  useEffect(() => {
    window.localStorage.setItem(CALENDAR_METRIC_STORAGE_KEY, metric)
  }, [metric])

  useEffect(() => {
    if (!calendar?.days.length) return
    if (selectedDate && calendar.days.some((day) => day.date === selectedDate)) return
    const today = todayIso()
    const inCalendarToday = calendar.days.find((day) => day.date === today)
    const firstInMonth = calendar.days.find((day) => day.in_month)
    onSelectedDateChange((inCalendarToday ?? firstInMonth ?? calendar.days[0]).date)
  }, [calendar, onSelectedDateChange, selectedDate])

  const selectedDay = calendar?.days.find((day) => day.date === selectedDate)
  const weekDays = useMemo(() => weekdayShortLabels(dateLocale), [dateLocale])

  if (isLoading) {
    return (
      <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden mb-4">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <Skeleton className="h-6 w-44" />
          <Skeleton className="h-8 w-32" />
        </div>
        <div className="grid grid-cols-7">
          {Array.from({ length: 35 }).map((_, index) => (
            <div key={index} className="min-h-32 border-r border-b border-border p-3">
              <Skeleton className="h-4 w-8 mb-4" />
              <Skeleton className="h-4 w-24" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (!calendar) return null

  return (
    <div className="mb-4 flex flex-col gap-4 md:flex-row md:items-start">
      <section className="min-w-0 flex-1 bg-card rounded-xl border border-border shadow-sm overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-border sm:px-5">
          <CalendarLegend />
          <div className="flex flex-wrap items-center gap-2">
            <CalendarMetricToggle value={metric} onChange={setMetric} />
            <CalendarDensityToggle value={density} onChange={setDensity} />
          </div>
        </div>

        {metric === 'balance' ? (
          <BalanceTrend
            days={calendar.days.filter((day) => day.in_month)}
            currency={calendar.currency}
            locale={locale}
            mask={mask}
            selectedDate={selectedDate}
            onSelectDate={onSelectedDateChange}
          />
        ) : (
          <ActivityBars
            days={calendar.days.filter((day) => day.in_month)}
            currency={calendar.currency}
            locale={locale}
            mask={mask}
            selectedDate={selectedDate}
            onSelectDate={onSelectedDateChange}
          />
        )}

        <div className="hidden md:grid grid-cols-7 border-b border-border bg-muted/30">
          {weekDays.map((day) => (
            <div key={day} className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {day}
            </div>
          ))}
        </div>

        <div className="hidden md:grid grid-cols-7">
          {calendar.days.map((day, index) => (
            <DayCell
              key={day.date}
              day={day}
              selected={day.date === selectedDate}
              today={day.date === todayIso()}
              currency={calendar.currency}
              locale={locale}
              mask={mask}
              density={density}
              metric={metric}
              onSelect={() => onSelectedDateChange(day.date)}
              // The container clips the grid with rounded-xl + overflow-hidden, so the
              // two bottom corner cells carry a matching radius (12px outer − 1px
              // border) — otherwise their selection/focus ring loses its corner.
              className={cn(
                index === calendar.days.length - 7 && 'rounded-bl-[11px]',
                index === calendar.days.length - 1 && 'rounded-br-[11px]',
              )}
            />
          ))}
        </div>

        <div className="md:hidden divide-y divide-border">
          {calendar.days.filter((day) => day.in_month || day.date === selectedDate).map((day) => {
            const selected = day.date === selectedDate
            const panelId = `transaction-calendar-day-details-${day.date}`
            return (
              <div key={day.date}>
                <MobileDayRow
                  day={day}
                  selected={selected}
                  panelId={panelId}
                  currency={calendar.currency}
                  locale={locale}
                  dateLocale={dateLocale}
                  mask={mask}
                  density={density}
                  metric={metric}
                  onSelect={() => onSelectedDateChange(day.date)}
                />
                {selected && (
                  <SelectedDayPanel
                    id={panelId}
                    variant="mobile"
                    day={day}
                    currency={calendar.currency}
                    locale={locale}
                    dateLocale={dateLocale}
                    mask={mask}
                    metric={metric}
                    accountById={accountById}
                    userCurrency={userCurrency}
                    onOpenTransaction={onOpenTransaction}
                  />
                )}
              </div>
            )
          })}
        </div>
      </section>

      <SelectedDayPanel
        variant="desktop"
        day={selectedDay}
        currency={calendar.currency}
        locale={locale}
        dateLocale={dateLocale}
        mask={mask}
        metric={metric}
        accountById={accountById}
        userCurrency={userCurrency}
        onOpenTransaction={onOpenTransaction}
      />
    </div>
  )
}

// The grid tells you what happened on a day. It cannot show the shape of the month,
// which is the actual question: when does the balance fall, and does it cross zero.
// Reading 31 separate numbers to answer that is the thing this strip removes.
function BalanceTrend({
  days,
  currency,
  locale,
  mask,
  selectedDate,
  onSelectDate,
}: {
  days: TransactionCalendarDay[]
  currency: string
  locale: string
  mask: (value: string) => string
  selectedDate: string
  onSelectDate: (date: string) => void
}) {
  const { t } = useTranslation()
  const W = 1000
  const H = 120

  const geometry = useMemo(() => {
    if (days.length < 2) return null
    const values = days.map((day) => day.ending_balance)
    const rawMin = Math.min(...values, 0)
    const rawMax = Math.max(...values, 0)
    const pad = (rawMax - rawMin) * 0.12 || Math.abs(rawMax || 1) * 0.12
    const min = rawMin - pad
    const max = rawMax + pad
    const span = max - min || 1
    const x = (i: number) => (i / (days.length - 1)) * W
    const y = (v: number) => H - ((v - min) / span) * H
    const points = days.map((day, i) => ({ x: x(i), y: y(day.ending_balance), day }))
    const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ')
    const zeroY = y(0)
    // Close the area on the zero line rather than the bottom edge. Clipped above zero it
    // paints the surplus green; clipped below it paints only the actual shortfall red.
    const area = `${line} L${W},${zeroY.toFixed(2)} L0,${zeroY.toFixed(2)} Z`
    return { points, line, area, zeroY }
  }, [days])

  if (!geometry) return null
  const { points, line, area, zeroY } = geometry
  const selectedPoint = points.find((p) => p.day.date === selectedDate)

  return (
    <div className="hidden border-b border-border px-4 pb-3 pt-2 sm:px-5 md:block">
      <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t('transactions.calendarBalanceTrend')}
      </p>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="h-16 w-full overflow-visible"
        role="img"
        aria-label={t('transactions.calendarBalanceTrend')}
      >
        <defs>
          <linearGradient id="cal-trend-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(16 185 129)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="rgb(16 185 129)" stopOpacity="0.02" />
          </linearGradient>
          <clipPath id="cal-trend-positive">
            <rect x="0" y="0" width={W} height={Math.max(0, zeroY)} />
          </clipPath>
          {/* Only the stretch that actually dips under zero reads red. */}
          <clipPath id="cal-trend-negative">
            <rect x="0" y={zeroY} width={W} height={Math.max(0, H - zeroY)} />
          </clipPath>
        </defs>

        <path d={area} fill="url(#cal-trend-fill)" clipPath="url(#cal-trend-positive)" />
        <path d={area} fill="rgb(244 63 94)" fillOpacity="0.25" clipPath="url(#cal-trend-negative)" />

        <line
          x1="0"
          x2={W}
          y1={zeroY}
          y2={zeroY}
          stroke="currentColor"
          strokeWidth="1"
          strokeDasharray="4 4"
          vectorEffect="non-scaling-stroke"
          className="text-rose-400/70"
        />
        <path
          d={line}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
          className="text-emerald-600 dark:text-emerald-400"
        />

        {selectedPoint && (
          <>
            <line
              x1={selectedPoint.x}
              x2={selectedPoint.x}
              y1="0"
              y2={H}
              stroke="currentColor"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
              className="text-primary/40"
            />
            <circle
              cx={selectedPoint.x}
              cy={selectedPoint.y}
              r="4"
              vectorEffect="non-scaling-stroke"
              className="fill-background stroke-primary"
              strokeWidth="2"
            />
          </>
        )}

        {/* Invisible hit targets: the strip doubles as a way to pick a day. */}
        {points.map((p, i) => (
          <rect
            key={p.day.date}
            x={i === 0 ? 0 : p.x - W / (points.length - 1) / 2}
            y="0"
            width={W / (points.length - 1)}
            height={H}
            fill="transparent"
            className="cursor-pointer"
            onClick={() => onSelectDate(p.day.date)}
          >
            <title>{`${displayDayNumber(p.day.date)} · ${mask(formatCurrency(p.day.ending_balance, currency, locale))}`}</title>
          </rect>
        ))}
      </svg>
    </div>
  )
}

// The activity strip answers the other monthly question: how much came in and went
// out each day. Actual amounts are solid bars around a zero axis; projected amounts
// stack on top as dashed violet outlines so a forecasted bill never reads as settled.
function ActivityBars({
  days,
  currency,
  locale,
  mask,
  selectedDate,
  onSelectDate,
}: {
  days: TransactionCalendarDay[]
  currency: string
  locale: string
  mask: (value: string) => string
  selectedDate: string
  onSelectDate: (date: string) => void
}) {
  const { t } = useTranslation()
  const W = 1000
  const H = 120
  const data = useMemo(() => activityChartData(days), [days])

  if (days.length === 0) return null

  if (!data.hasActivity) {
    return (
      <div className="hidden border-b border-border px-4 pb-3 pt-2 sm:px-5 md:block">
        <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t('transactions.calendarActivityTrend')}
        </p>
        <p className="flex h-16 items-center justify-center text-sm text-muted-foreground">
          {t('transactions.calendarNoActivity')}
        </p>
      </div>
    )
  }

  const totalSpan = data.maxUp + data.maxDown || 1
  // Leave breathing room so the tallest bar never touches the strip edges.
  const scale = (H * 0.92) / totalSpan
  const axisY = data.maxUp * scale + H * 0.04
  const slot = W / data.days.length
  const barW = slot * 0.55

  const barHeight = (value: number) => (value > 0 ? Math.max(value * scale, 1.5) : 0)

  const tooltipFor = (activity: (typeof data.days)[number]) => {
    const amount = (value: number) => mask(compactCurrency(value, currency, locale))
    const actualParts: string[] = []
    if (activity.actualIncome > 0) actualParts.push(`${t('transactions.summaryIncome')} +${amount(activity.actualIncome)}`)
    if (activity.actualExpense > 0) actualParts.push(`${t('transactions.summaryExpenses')} −${amount(activity.actualExpense)}`)
    const projectedParts: string[] = []
    if (activity.projectedIncome > 0) projectedParts.push(`+${amount(activity.projectedIncome)}`)
    if (activity.projectedExpense > 0) projectedParts.push(`−${amount(activity.projectedExpense)}`)
    const segments = [
      String(displayDayNumber(activity.date)),
      actualParts.length > 0 ? actualParts.join(' · ') : t('transactions.calendarNoMovements'),
    ]
    if (projectedParts.length > 0) {
      segments.push(`${t('transactions.calendarProjected')} ${projectedParts.join(' · ')}`)
    }
    return segments.join(' · ')
  }

  return (
    <div className="hidden border-b border-border px-4 pb-3 pt-2 sm:px-5 md:block">
      <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t('transactions.calendarActivityTrend')}
      </p>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="h-16 w-full overflow-visible"
        role="img"
        aria-label={t('transactions.calendarActivityTrend')}
      >
        <line
          x1="0"
          x2={W}
          y1={axisY}
          y2={axisY}
          stroke="currentColor"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
          className="text-border"
        />

        {data.days.map((activity, i) => {
          const x = slot * i + (slot - barW) / 2
          const selected = activity.date === selectedDate
          const actualIncomeH = barHeight(activity.actualIncome)
          const actualExpenseH = barHeight(activity.actualExpense)
          const projectedIncomeH = barHeight(activity.projectedIncome)
          const projectedExpenseH = barHeight(activity.projectedExpense)
          return (
            <g key={activity.date}>
              {selected && (
                <line
                  x1={slot * i + slot / 2}
                  x2={slot * i + slot / 2}
                  y1="0"
                  y2={H}
                  stroke="currentColor"
                  strokeWidth="1"
                  vectorEffect="non-scaling-stroke"
                  className="text-primary/40"
                />
              )}
              {actualIncomeH > 0 && (
                <rect
                  x={x}
                  y={axisY - actualIncomeH}
                  width={barW}
                  height={actualIncomeH}
                  className="fill-emerald-500/80 dark:fill-emerald-400/80"
                />
              )}
              {projectedIncomeH > 0 && (
                <rect
                  x={x}
                  y={axisY - actualIncomeH - projectedIncomeH}
                  width={barW}
                  height={projectedIncomeH}
                  strokeWidth="1"
                  strokeDasharray="3 2"
                  vectorEffect="non-scaling-stroke"
                  className="fill-violet-500/10 stroke-violet-500 dark:stroke-violet-400"
                />
              )}
              {actualExpenseH > 0 && (
                <rect
                  x={x}
                  y={axisY}
                  width={barW}
                  height={actualExpenseH}
                  className="fill-rose-500/80 dark:fill-rose-400/80"
                />
              )}
              {projectedExpenseH > 0 && (
                <rect
                  x={x}
                  y={axisY + actualExpenseH}
                  width={barW}
                  height={projectedExpenseH}
                  strokeWidth="1"
                  strokeDasharray="3 2"
                  vectorEffect="non-scaling-stroke"
                  className="fill-violet-500/10 stroke-violet-500 dark:stroke-violet-400"
                />
              )}
              {/* Full-height hit target: bars can be a couple of pixels tall, so the
                  clickable/focusable area is the whole day column, keyboard included. */}
              <rect
                x={slot * i}
                y="0"
                width={slot}
                height={H}
                fill="transparent"
                role="button"
                tabIndex={0}
                aria-label={tooltipFor(activity)}
                aria-pressed={selected}
                className="cursor-pointer focus:outline-none focus-visible:fill-primary/10"
                onClick={() => onSelectDate(activity.date)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelectDate(activity.date)
                  }
                }}
              >
                <title>{tooltipFor(activity)}</title>
              </rect>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function CalendarLegend() {
  const { t } = useTranslation()
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
      <span className="font-semibold uppercase tracking-wide">{t('transactions.calendarLegend')}</span>
      <LegendItem tone="income" label={t('transactions.summaryIncome')} />
      <LegendItem tone="expense" label={t('transactions.summaryExpenses')} />
      <LegendItem tone="transfer" label={t('transactions.transfer')}>
        <ArrowLeftRight size={9} />
      </LegendItem>
      <LegendItem tone="projected" label={t('transactions.calendarProjected')}>
        <CalendarDays size={9} />
      </LegendItem>
    </div>
  )
}

function SegmentedToggle<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T
  onChange: (value: T) => void
  options: Array<{ value: T; label: string }>
  label: string
}) {
  return (
    <div className="inline-flex items-center gap-1 rounded-lg border border-border bg-muted/30 p-0.5 text-xs" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={cn(
            'rounded-md px-2.5 py-1 font-semibold text-muted-foreground transition-colors hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/40',
            value === option.value && 'bg-card text-foreground shadow-sm dark:bg-background',
          )}
          aria-pressed={value === option.value}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

function CalendarDensityToggle({ value, onChange }: { value: CalendarDensity; onChange: (value: CalendarDensity) => void }) {
  const { t } = useTranslation()
  return (
    <SegmentedToggle
      value={value}
      onChange={onChange}
      label={t('transactions.calendarDensity')}
      options={[
        { value: 'compact', label: t('transactions.calendarCompact') },
        { value: 'detailed', label: t('transactions.calendarDetailed') },
      ]}
    />
  )
}

function CalendarMetricToggle({ value, onChange }: { value: CalendarMetric; onChange: (value: CalendarMetric) => void }) {
  const { t } = useTranslation()
  return (
    <SegmentedToggle
      value={value}
      onChange={onChange}
      label={t('transactions.calendarMetric')}
      options={[
        { value: 'balance', label: t('transactions.calendarBalanceMode') },
        { value: 'activity', label: t('transactions.calendarActivityMode') },
      ]}
    />
  )
}

function LegendItem({ tone, label, children }: { tone: CalendarMarkerTone; label: string; children?: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      <BadgeDot tone={tone} label={label}>{children}</BadgeDot>
      <span>{label}</span>
    </span>
  )
}

function DayCell({
  day,
  selected,
  today,
  currency,
  locale,
  mask,
  density,
  metric,
  onSelect,
  className,
}: {
  day: TransactionCalendarDay
  selected: boolean
  today: boolean
  currency: string
  locale: string
  mask: (value: string) => string
  density: CalendarDensity
  metric: CalendarMetric
  onSelect: () => void
  className?: string
}) {
  const { t } = useTranslation()
  // The negative-balance warning belongs to the balance metric; activity mode has
  // no running balance to warn about.
  const isLowBalance = metric === 'balance' && day.ending_balance < 0
  const detailed = density === 'detailed'
  const previewItems = detailed ? topItemsBySize(day.items, 3) : []
  const moreCount = detailed ? Math.max(0, day.items.length - previewItems.length) : 0
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect()
        }
      }}
      className={cn(
        // Column flex so a quiet day's marker can claim the leftover height and
        // sit in the middle of the cell instead of hanging under the date.
        'flex flex-col border-r border-b border-border p-3 text-left transition-colors hover:bg-muted/30 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-primary/40',
        detailed ? 'min-h-44' : 'min-h-24',
        !day.in_month && 'bg-muted/20 text-muted-foreground',
        day.in_month && isLowBalance && 'bg-rose-50/60 dark:bg-rose-950/20',
        // Selection is a ring, not a fill, on low-balance days: tinting the cell
        // would hide the negative-balance warning exactly when the user opens it.
        // Inset so the container's overflow-hidden never clips it on edge rows.
        selected && 'z-10 ring-2 ring-inset ring-primary/70',
        selected && !isLowBalance && 'bg-primary/5 dark:bg-primary/10',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-1">
        <span className={cn(
          'inline-flex size-6 shrink-0 items-center justify-center rounded-full text-sm font-bold tabular-nums',
          today && 'bg-primary text-primary-foreground',
          !today && day.in_month && 'text-foreground',
          !today && !day.in_month && 'text-muted-foreground',
        )}>
          {displayDayNumber(day.date)}
        </span>
        {/* Shown in both densities. The rows below only list the top few items, so on a
            busy day the badges are the only thing reporting the income, transfer or
            projection buried in the "+N more". */}
        <CalendarBadges day={day} />
      </div>

      {metric === 'balance' ? (
        /* The balance is plain text on an ordinary day. Almost every day repeats
            yesterday's number, so making all of them loud buries the days that matter.
            Negative days keep the pill; the day it first turns negative is called out
            in words above the grid rather than with an icon nobody can decode. */
        <div className="mt-3 flex items-center gap-1">
          <p
            title={mask(formatCurrency(day.ending_balance, currency, locale))}
            className={cn(
              'whitespace-nowrap text-sm tabular-nums',
              isLowBalance
                ? 'rounded-full border border-rose-300 bg-rose-100 px-1.5 py-0.5 font-bold text-rose-700 dark:border-rose-500/50 dark:bg-rose-500/15 dark:text-rose-300'
                : 'font-semibold text-muted-foreground',
            )}
          >
            {mask(compactCurrency(day.ending_balance, currency, locale))}
          </p>
        </div>
      ) : (
        <DayCellActivity day={day} currency={currency} locale={locale} mask={mask} />
      )}

      {detailed && previewItems.length > 0 && (
        <div className="mt-3 space-y-1.5 pr-1">
          {previewItems.map((item) => (
            <DayPreviewRow
              key={`${item.kind}-${item.id ?? item.recurring_id}-${item.date}`}
              item={item}
              locale={locale}
              mask={mask}
            />
          ))}
          {moreCount > 0 && (
            <p className="truncate text-[11px] font-semibold text-muted-foreground">
              {t('transactions.calendarMoreItems', { count: moreCount })}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// The row is only ~70px wide, which truncated descriptions to two or three characters.
// That carried no meaning and starved the amount, so the row now shows the category icon
// and the amount only. The description stays available on hover and in the selected-day
// panel. Projected items get a dashed border to read as not yet settled.
// Hard cap for the mobile preview description: sliced, not just CSS-truncated,
// so the row keeps a single line even on the narrowest screens.
const PREVIEW_DESCRIPTION_MAX_CHARS = 20

function previewDescription(description: string) {
  if (description.length <= PREVIEW_DESCRIPTION_MAX_CHARS) return description
  return `${description.slice(0, PREVIEW_DESCRIPTION_MAX_CHARS).trimEnd()}…`
}

function DayPreviewRow({
  item,
  locale,
  mask,
  showDescription = false,
}: {
  item: TransactionCalendarItem
  locale: string
  mask: (value: string) => string
  // Mobile day rows have the horizontal room the desktop grid cells lack.
  showDescription?: boolean
}) {
  const amount = signedAmount(item)
  const label = [item.description, item.category_name].filter(Boolean).join(' · ')
  return (
    <div
      title={label}
      className={cn(
        'flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[11px] shadow-sm',
        item.kind === 'projected'
          ? 'border border-dashed border-violet-400/70 bg-violet-500/5 dark:border-violet-400/50'
          : 'bg-background/45 dark:bg-background/25',
      )}
    >
      <CategoryIcon icon={item.category_icon ?? undefined} color={item.category_color ?? undefined} size="xs" />
      {showDescription && (
        <span className="min-w-0 flex-1 truncate whitespace-nowrap text-muted-foreground">
          {previewDescription(item.description)}
        </span>
      )}
      <span
        className={cn(
          'font-bold tabular-nums whitespace-nowrap',
          showDescription ? 'shrink-0' : 'min-w-0 flex-1 truncate text-right',
          amount >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400',
        )}
      >
        {mask(`${amount >= 0 ? '+' : '−'}${compactCurrency(Math.abs(item.amount), item.currency, locale)}`)}
      </span>
    </div>
  )
}

// Activity cells report what actually moved: green income and red expense, real
// amounts only. Projected amounts get a secondary violet line so a forecast never
// blends into settled money. A day with nothing at all gets a centred dash rather
// than a sentence: in a quiet month the same string repeated 30 times is noise,
// and the eye should land on the days that moved. A day whose only entry is a
// projection is not empty, so it shows the violet figure and no dash.
function DayCellActivity({
  day,
  currency,
  locale,
  mask,
  align = 'left',
}: {
  day: TransactionCalendarDay
  currency: string
  locale: string
  mask: (value: string) => string
  align?: 'left' | 'right'
}) {
  const { t } = useTranslation()
  const activity = dayActivity(day)
  const projectedParts = [
    activity.projectedIncome > 0 ? mask(`+${compactCurrency(activity.projectedIncome, currency, locale)}`) : null,
    activity.projectedExpense > 0 ? mask(`−${compactCurrency(activity.projectedExpense, currency, locale)}`) : null,
  ].filter(Boolean)

  if (!activity.hasActual && !activity.hasProjected) {
    return (
      <div
        className={cn(
          'flex items-center text-muted-foreground/40',
          align === 'right' ? 'mt-0 justify-end' : 'flex-1 justify-center',
        )}
      >
        <Minus size={16} aria-hidden="true" />
        {/* The dash carries no meaning on its own, so the sentence stays for
            screen readers and keeps the string translated in every locale. */}
        <span className="sr-only">{t('transactions.calendarNoMovements')}</span>
      </div>
    )
  }

  return (
    <div className={cn('mt-3 space-y-0.5', align === 'right' && 'mt-0 text-right')}>
      {activity.hasActual && (
        <p className={cn('flex flex-wrap items-center gap-x-1.5 text-sm font-semibold tabular-nums', align === 'right' && 'justify-end')}>
          {activity.actualIncome > 0 && (
            <span className="text-emerald-600 dark:text-emerald-400">
              {mask(`+${compactCurrency(activity.actualIncome, currency, locale)}`)}
            </span>
          )}
          {activity.actualExpense > 0 && (
            <span className="text-rose-600 dark:text-rose-400">
              {mask(`−${compactCurrency(activity.actualExpense, currency, locale)}`)}
            </span>
          )}
        </p>
      )}
      {projectedParts.length > 0 && (
        <p
          title={t('transactions.calendarProjected')}
          className="text-[11px] font-semibold tabular-nums text-violet-600 dark:text-violet-300"
        >
          {projectedParts.join(' · ')}
        </p>
      )}
    </div>
  )
}

// Markers stay on one line beside the day number. A cell header is ~100px wide and the
// date takes 28px, so the four badges are sized to fit the remainder rather than wrap
// onto a second row, which used to push the balance chip down. They render the same
// in both metrics so a day always reads the same way regardless of the toggle.
function CalendarBadges({ day }: { day: TransactionCalendarDay }) {
  const { t } = useTranslation()
  return (
    <div className="flex shrink-0 items-center gap-0.5">
      {day.has_income && <BadgeDot tone="income" label={t('transactions.summaryIncome')} />}
      {day.has_expense && <BadgeDot tone="expense" label={t('transactions.summaryExpenses')} />}
      {day.has_transfer && (
        <BadgeDot tone="transfer" label={t('transactions.transfer')}>
          <ArrowLeftRight size={9} />
        </BadgeDot>
      )}
      {day.projected_count > 0 && (
        <BadgeDot tone="projected" label={t('transactions.calendarProjected')}>
          <CalendarDays size={9} />
        </BadgeDot>
      )}
    </div>
  )
}

function BadgeDot({
  tone,
  label,
  children,
}: {
  tone: CalendarMarkerTone
  label: string
  children?: ReactNode
}) {
  return (
    <span
      title={label}
      aria-label={label}
      className={cn(
        'inline-flex size-3.5 shrink-0 items-center justify-center rounded border bg-background/80 shadow-sm',
        tone === 'income' && 'border-emerald-500/40 text-emerald-600 dark:text-emerald-400',
        tone === 'expense' && 'border-rose-500/40 text-rose-600 dark:text-rose-400',
        tone === 'transfer' && 'border-sky-500/40 text-sky-600 dark:text-sky-400',
        tone === 'projected' && 'border-violet-500/40 text-violet-600 dark:text-violet-300',
      )}
    >
      {children ?? <CircleDot size={9} />}
    </span>
  )
}

function MobileDayRow({
  day,
  selected,
  panelId,
  currency,
  locale,
  dateLocale,
  mask,
  density,
  metric,
  onSelect,
}: {
  day: TransactionCalendarDay
  selected: boolean
  panelId: string
  currency: string
  locale: string
  dateLocale: string
  mask: (value: string) => string
  density: CalendarDensity
  metric: CalendarMetric
  onSelect: () => void
}) {
  const { t } = useTranslation()
  const isLowBalance = metric === 'balance' && day.ending_balance < 0
  const previewItems = density === 'detailed' ? day.items.slice(0, 3) : []
  const moreCount = density === 'detailed' ? Math.max(0, day.items.length - previewItems.length) : 0
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-expanded={selected}
      aria-controls={selected ? panelId : undefined}
      className={cn(
        'w-full border-l-4 border-transparent px-4 py-3 text-left transition-colors hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary',
        isLowBalance && !selected && 'bg-rose-50/75 dark:bg-rose-950/25',
        selected && 'border-primary bg-primary/10 dark:bg-primary/15',
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-foreground">
            {parseLocalDate(day.date).toLocaleDateString(dateLocale, { weekday: 'short', day: 'numeric', month: 'short' })}
          </p>
          <p className="text-xs text-muted-foreground">
            {t('transactions.calendarItemCount', { count: day.actual_count + day.projected_count })}
          </p>
        </div>
        <div className="flex flex-col items-end gap-0.5 text-right">
          {metric === 'balance' ? (
            <p className={cn('text-sm font-bold tabular-nums', day.ending_balance < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400')}>
              {mask(formatCurrency(day.ending_balance, currency, locale))}
            </p>
          ) : (
            <DayCellActivity day={day} currency={currency} locale={locale} mask={mask} align="right" />
          )}
          <CalendarBadges day={day} />
        </div>
      </div>
      {previewItems.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {previewItems.map((item) => (
            <DayPreviewRow
              key={`${item.kind}-${item.id ?? item.recurring_id}-${item.date}`}
              item={item}
              locale={locale}
              mask={mask}
              showDescription
            />
          ))}
          {moreCount > 0 && (
            <p className="truncate text-[11px] font-semibold text-muted-foreground">
              {t('transactions.calendarMoreItems', { count: moreCount })}
            </p>
          )}
        </div>
      )}
    </button>
  )
}

function SelectedDayPanel({
  id,
  variant,
  day,
  currency,
  locale,
  dateLocale,
  mask,
  metric,
  accountById,
  userCurrency,
  onOpenTransaction,
}: {
  id?: string
  variant: 'mobile' | 'desktop'
  day?: TransactionCalendarDay
  currency: string
  locale: string
  dateLocale: string
  mask: (value: string) => string
  metric: CalendarMetric
  accountById: Map<string, Account>
  userCurrency: string
  onOpenTransaction: (id: string) => void
}) {
  const { t } = useTranslation()
  if (!day) return null
  const activity = dayActivity(day)
  const headline = metric === 'balance' ? day.ending_balance : activity.actualNet
  const headlineLabel = metric === 'balance'
    ? mask(formatCurrency(day.ending_balance, currency, locale))
    : mask(`${activity.actualNet >= 0 ? '+' : '−'}${formatCurrency(Math.abs(activity.actualNet), currency, locale)}`)
  return (
    <aside
      id={id}
      className={cn(
        'bg-card overflow-hidden',
        variant === 'mobile' && 'mx-3 mt-2 mb-3 rounded-xl border border-border shadow-sm md:hidden',
        variant === 'desktop' && 'hidden rounded-xl border border-border shadow-sm md:sticky md:top-4 md:max-h-[calc(100vh-7rem)] md:w-[320px] md:shrink-0 md:self-start md:flex md:flex-col lg:w-[340px]',
      )}
    >
      <div className="px-4 py-4 border-b border-border">
        <div className="flex items-end justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {metric === 'balance' ? t('transactions.calendarSelectedDay') : t('transactions.calendarActualNet')}
            </p>
            <h3 className="truncate text-lg font-bold text-foreground">
              {parseLocalDate(day.date).toLocaleDateString(dateLocale, { weekday: 'long', day: 'numeric', month: 'long' })}
            </h3>
          </div>
          <p
            title={headlineLabel}
            className={cn(
              'shrink-0 text-right text-lg font-bold tabular-nums',
              headline < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400',
            )}
          >
            {headlineLabel}
          </p>
        </div>
      </div>

      {metric === 'activity' && (
        <>
          <div className="space-y-2 border-b border-border px-4 py-3">
            <DayPanelRow
              label={t('transactions.summaryIncome')}
              value={mask(`+${formatCurrency(activity.actualIncome, currency, locale)}`)}
              amount={activity.actualIncome}
            />
            <DayPanelRow
              label={t('transactions.summaryExpenses')}
              value={mask(`−${formatCurrency(activity.actualExpense, currency, locale)}`)}
              amount={-activity.actualExpense}
            />
            {day.has_transfer && (
              <p className="flex items-center gap-1.5 pt-0.5 text-xs text-muted-foreground">
                <ArrowLeftRight size={12} className="shrink-0 text-sky-600 dark:text-sky-400" />
                {t('transactions.calendarTransfersExcluded')}
              </p>
            )}
          </div>

          {/* A second group of the same rows rather than a callout box. The
              divider and the violet section label already say these figures are
              forecasts, and the amounts keep the colour of their direction so
              they read the same way as the projected rows listed below. */}
          {activity.hasProjected && (
            <div className="space-y-2 border-b border-border px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-violet-600 dark:text-violet-400">
                {t('transactions.calendarProjected')}
              </p>
              {activity.projectedIncome > 0 && (
                <DayPanelRow
                  label={t('transactions.summaryIncome')}
                  value={mask(`+${formatCurrency(activity.projectedIncome, currency, locale)}`)}
                  amount={activity.projectedIncome}
                />
              )}
              {activity.projectedExpense > 0 && (
                <DayPanelRow
                  label={t('transactions.summaryExpenses')}
                  value={mask(`−${formatCurrency(activity.projectedExpense, currency, locale)}`)}
                  amount={-activity.projectedExpense}
                />
              )}
              {activity.projectedIncome > 0 && activity.projectedExpense > 0 && (
                <DayPanelRow
                  label={t('transactions.summaryNet')}
                  value={mask(`${activity.projectedNet >= 0 ? '+' : '−'}${formatCurrency(Math.abs(activity.projectedNet), currency, locale)}`)}
                  amount={activity.projectedNet}
                />
              )}
            </div>
          )}
        </>
      )}

      <div className="min-h-0 divide-y divide-border overflow-y-auto md:flex-1">
        {day.items.length === 0 ? (
          <p className="px-4 py-8 text-sm text-muted-foreground text-center">{t('transactions.calendarNoItems')}</p>
        ) : (
          day.items.map((item) => (
            <CalendarItemRow
              key={`${item.kind}-${item.id ?? item.recurring_id}-${item.date}`}
              item={item}
              account={item.account_id ? accountById.get(item.account_id) : undefined}
              locale={locale}
              userCurrency={userCurrency}
              mask={mask}
              onOpenTransaction={onOpenTransaction}
            />
          ))
        )}
      </div>
    </aside>
  )
}

// One label/value line, the shape the rest of the panel and the page already use.
// `amount` only picks the colour, so the caller stays free to format the string.
function DayPanelRow({ label, value, amount }: { label: string; value: string; amount: number }) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span
        className={cn(
          'font-semibold tabular-nums',
          amount < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400',
        )}
      >
        {value}
      </span>
    </div>
  )
}

// Mirrors MobileTransactionRow so a transaction reads the same in the list and
// in the calendar: category icon, description with inline badges, account row,
// colour-coded signed amount with the primary-currency conversion underneath.
function CalendarItemRow({
  item,
  account,
  locale,
  userCurrency,
  mask,
  onOpenTransaction,
}: {
  item: TransactionCalendarItem
  account: Account | undefined
  locale: string
  userCurrency: string
  mask: (value: string) => string
  onOpenTransaction: (id: string) => void
}) {
  const { t } = useTranslation()
  const interactive = isCalendarItemInteractive(item)
  const amountColor = item.is_ignored
    ? 'text-gray-500'
    : item.type === 'credit'
      ? 'text-emerald-600'
      : 'text-rose-500'
  return (
    <button
      type="button"
      disabled={!interactive}
      onClick={() => { if (item.id) onOpenTransaction(item.id) }}
      className={cn(
        'w-full flex items-center gap-3 pl-3 pr-3 py-3 text-left',
        interactive
          ? 'hover:bg-muted/50 active:bg-muted/60 transition-colors'
          : 'cursor-default opacity-80',
      )}
    >
      <div className="shrink-0">
        <CategoryIcon icon={item.category_icon ?? undefined} color={item.category_color ?? undefined} size="md" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <p className="text-sm font-semibold text-foreground truncate leading-tight">{item.description}</p>
          {item.kind === 'projected' && item.source === 'recurring' && item.status !== 'pending' && (
            <ProjectedTransactionBadge />
          )}
          {item.is_transfer && (
            <ArrowLeftRight className="h-3 w-3 text-blue-600 shrink-0" />
          )}
          {item.is_ignored && (
            <EyeClosed className="h-3 w-3 text-gray-500 shrink-0" />
          )}
          {item.exclude_from_pnl && !item.is_ignored && (
            <ChartNoAxesColumn
              className="h-3 w-3 text-slate-500 shrink-0"
              aria-label={t('transactions.excludedFromReports')}
            />
          )}
          {shouldShowPendingBadge(item) && (
            <span
              title={t('transactions.pending')}
              className="shrink-0 inline-flex items-center justify-center rounded-full border border-amber-200 bg-amber-50 p-0.5 dark:border-amber-500/30 dark:bg-amber-500/10"
            >
              <Clock size={12} className="text-amber-500" role="img" aria-label={t('transactions.pending')} />
            </span>
          )}
        </div>
        {(account || item.account_name) && (
          <div className="flex items-center gap-1.5 mt-0.5">
            {account && <AccountIcon account={account} size="xs" />}
            <span className="text-xs text-muted-foreground truncate">
              {account ? getAccountName(account) : item.account_name}
            </span>
          </div>
        )}
      </div>
      <div className="shrink-0 text-right">
        <span className={cn('text-sm font-bold tabular-nums', amountColor)}>
          {mask(`${item.is_ignored ? ' ' : item.type === 'credit' ? '+' : '−'}${formatCurrency(Math.abs(item.amount), item.currency, locale)}`)}
        </span>
        {item.amount_primary != null && item.currency !== userCurrency && (
          <div className="text-[10px] text-muted-foreground tabular-nums mt-0.5">
            {mask(formatCurrency(Math.abs(item.amount_primary), userCurrency, locale))}
          </div>
        )}
      </div>
    </button>
  )
}
