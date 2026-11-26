import { useId } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ChevronDown, ChevronUp } from 'lucide-react'

import { loadGoalHistory } from '@/lib/goal-history'
import {
  axisTicks,
  calendarMonthAfter,
  firstOfMonthAfter,
  goalHistoryStart,
  projectGoal,
  targetLabelSide,
} from '@/lib/goal-projection'
import { todayInTimezone } from '@/lib/date-utils'
import { useGoalChartState } from '@/contexts/goal-chart-state'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import type { Goal } from '@/types'

/**
 * Converts a YYYY-MM-DD date to UTC midnight in milliseconds for the time axis.
 *
 * @param {string} day - Calendar date in YYYY-MM-DD format.
 * @returns {number} UTC timestamp in milliseconds, or NaN for an unparseable date.
 */
const timestamp = (day: string) => Date.parse(day + 'T00:00:00Z')

/**
 * Displays a collapsible goal history chart with the required-pace
 * projection, target, and deadline. Uses workspace dates, theme colors, and
 * privacy settings, and exposes loading, missing-data, and retry states.
 *
 * @param {{ goal: Goal }} props - Component props containing the goal to visualize.
 * @returns {import('react').ReactElement} The chart section intended for a nonmanual goal card.
 */
export function GoalVisualization({ goal }: { goal: Goal }) {
  // Read shared app settings: translated labels, workspace timezone, display locale,
  // and privacy mode. Changes to these settings cause React to render again.
  const { t } = useTranslation()
  const { current } = useWorkspace()
  const locale = useDisplayLocale()
  const { mask, privacyMode } = usePrivacyMode()
  const id = useId()
  const { expanded, toggleExpanded } = useGoalChartState(goal)
  const today = todayInTimezone(current?.timezone)
  const historyStart = goalHistoryStart(goal, today)

  // React Query loads and caches history for this goal and workspace. The key
  // identifies the cached result; rendering again does not by itself fetch again.
  // Collapsing disables automatic fetching. Results become stale after one minute
  // and can be refreshed on events such as remounting or returning to the window.
  const history = useQuery({
    queryKey: [
      'goals',
      'history',
      current?.id,
      goal.id,
      goal.tracking_type,
      goal.account_id,
      goal.asset_id,
      goal.asset_group_id,
      goal.currency,
      historyStart,
      today,
    ],
    queryFn: () => loadGoalHistory(goal, today),
    enabled: expanded,
    staleTime: 60_000,
    retry: false,
  })

  // Build a straight required-pace guide from today to the target and deadline.
  // Undated goals get history only. This calculation makes no API calls.
  const plan = projectGoal(goal, today)
  /**
   * Formats a value in the goal currency and applies the current privacy mask.
   *
   * @param {number} value - Amount in major currency units.
   * @returns {string} Localized currency text or its privacy placeholder.
   */
  const money = (value: number) => mask(formatCurrency(value, goal.currency, locale))
  /**
   * Formats a tooltip date in the display locale without a timezone day shift.
   *
   * @param {string} date - Calendar date in YYYY-MM-DD format.
   * @returns {string} Localized date label.
   */
  const dateLabel = (date: string) =>
    new Date(timestamp(date)).toLocaleDateString(locale, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      timeZone: 'UTC',
    })

  // Recharts expects one array of rows. Merge both series by date, keeping
  // separate actual/projected fields so each Line can select its own values.
  // Until history arrives, the historical series is empty.
  const rows = new Map<number, { time: number; actual?: number; projected?: number }>()
  for (const point of history.data?.points ?? []) {
    if (point.date < historyStart) {
      continue
    }
    rows.set(timestamp(point.date), { time: timestamp(point.date), actual: point.amount })
  }

  // The forecast starts from the current Goal API amount, never an old valuation.
  // Keep it separate from the historical line when their endpoints disagree.
  for (const point of plan.projection) {
    const time = timestamp(point.date)
    rows.set(time, { ...rows.get(time), time, projected: point.amount })
  }

  // Sort left to right and choose the visible date range. A future deadline gets
  // one month of space after it; the chart always extends at least to next month.
  const chart = [...rows.values()].sort((a, b) => a.time - b.time)
  // Keep the axis anchored even when the first available snapshot is later.
  const start = timestamp(historyStart)
  const deadlineEnd =
    goal.target_date && goal.target_date > today
      ? timestamp(calendarMonthAfter(goal.target_date))
      : 0
  const end = Math.max(
    chart.at(-1)?.time ?? start,
    timestamp(firstOfMonthAfter(today)),
    deadlineEnd,
  )
  // Anchor the ticks on month starts and show the day on short windows, so a
  // one- or two-month range never repeats the same month label.
  const shortAxis = end - start < 92 * 86_400_000
  const axisLabel = (value: number) =>
    new Date(value).toLocaleDateString(
      locale,
      shortAxis
        ? { day: 'numeric', month: 'short', timeZone: 'UTC' }
        : { month: 'short', year: '2-digit', timeZone: 'UTC' },
    )
  // A single actual point, such as the valuation carried into a sparse window,
  // has no segment to draw, so it needs its own dot to stay visible.
  const actualPoints = chart.filter(point => point.actual !== undefined).length
  // y-max = 10% above the target, current amount, or highest historical value,
  // whichever is largest
  // Use 100 only when the value is at most 90.
  const peak = chart.reduce(
    (max, point) => Math.max(max, point.actual ?? 0, point.projected ?? 0),
    Math.max(Number(goal.target_amount), Number(goal.current_amount)),
  )
  const paddedPeak = peak * 1.1
  const ceiling = paddedPeak > 90 ? paddedPeak : 100

  // y-min = 10% below the target, current amount, or lowest historical value
  // whichever is lowest
  // Use -100 for [-90, 0), and zero for [0, 90].
  const lowest = chart.reduce(
    (min, point) => Math.min(min, point.actual ?? min),
    Math.min(Number(goal.current_amount), Number(goal.target_amount)),
  )
  let floor = lowest - Math.abs(lowest) * 0.1
  if (lowest >= -90 && lowest < 0) {
    floor = -100
  } else if (lowest >= 0 && lowest <= 90) {
    floor = 0
  }

  return (
    <section
      className="mt-4 border-t border-border pt-4"
      aria-label={t('goalChart.region', { name: goal.name })}
    >
      {/* Clicking the header updates local state and shows or hides the chart body. */}
      <button
        type="button"
        onClick={toggleExpanded}
        aria-expanded={expanded}
        aria-controls={id}
        className="flex w-full items-center justify-between gap-3 text-sm font-medium"
      >
        <span>{t('goalChart.title')}</span>
        {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>
      {expanded && (
        <div id={id}>
          {/* Request failure: explain the problem and let the user fetch history again. */}
          {history.isError && (
            <p role="alert" className="mb-2 text-sm text-amber-700 dark:text-amber-400">
              {t('goalChart.historyFailed')}{' '}
              <button className="underline" onClick={() => history.refetch()}>
                {t('goalChart.retry')}
              </button>
            </p>
          )}
          {/* Initial loading state while there is no cached history to display. */}
          {history.isPending && (
            <p role="status" className="mb-2 text-sm text-muted-foreground">
              {t('goalChart.loading')}
            </p>
          )}
          {/* Show data-quality warnings returned by the history loader. */}
          {history.data?.notes
            .filter(note => ['missing', 'currency'].includes(note))
            .map(note => (
              <p key={note} className="mb-2 text-xs text-muted-foreground">
                {t(`goalChart.notes.${note}`)}
              </p>
            ))}
          {/* Privacy mode replaces the entire graph so plotted amounts are hidden too. */}
          {privacyMode ? (
            <div className="flex h-60 items-center justify-center rounded-lg bg-muted text-sm text-muted-foreground">
              {t('goalChart.hidden')}
            </div>
          ) : (
            <div
              className="h-64 w-full"
              role="img"
              aria-label={t('goalChart.chartLabel', { name: goal.name, currency: goal.currency })}
            >
              {/* Fit the graph to the available width and the fixed-height wrapper. */}
              <ResponsiveContainer width="100%" height="100%">
                {/* Share the merged data and coordinate system across all chart elements. */}
                <LineChart
                  data={chart}
                  margin={{ top: 25, right: 35, left: 0, bottom: 5 }}
                  accessibilityLayer
                >
                  {/* Horizontal guide lines make amounts easier to compare. */}
                  <CartesianGrid stroke="var(--border)" vertical={false} />
                  {/* Horizontal axis: read each row's time and format it as month/year. */}
                  <XAxis
                    type="number"
                    dataKey="time"
                    scale="time"
                    domain={[start, end]}
                    ticks={axisTicks(historyStart, new Date(end).toISOString().slice(0, 10))}
                    interval="preserveStartEnd"
                    tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                    tickFormatter={axisLabel}
                  />
                  {/* Vertical axis: use the amount bounds and compact labels such as 6.6K. */}
                  <YAxis
                    width={72}
                    domain={[floor, ceiling]}
                    allowDataOverflow
                    tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                    tickFormatter={value =>
                      new Intl.NumberFormat(locale, {
                        notation: 'compact',
                        maximumFractionDigits: 1,
                      }).format(value)
                    }
                  />
                  {/* On hover, show the full date and formatted amounts for each series. */}
                  <Tooltip
                    labelFormatter={value =>
                      dateLabel(new Date(Number(value)).toISOString().slice(0, 10))
                    }
                    formatter={(value, name) => [money(Number(value)), name]}
                    contentStyle={{
                      background: 'var(--card)',
                      borderColor: 'var(--border)',
                      borderRadius: 8,
                    }}
                  />
                  {/* Horizontal reference at the goal target amount. */}
                  <ReferenceLine
                    y={Number(goal.target_amount)}
                    ifOverflow="extendDomain"
                    stroke="var(--muted-foreground)"
                    strokeDasharray="4 4"
                    label={{
                      value: t('goalChart.target'),
                      position: targetLabelSide(Number(goal.target_amount), floor),
                      fill: 'var(--muted-foreground)',
                      fontSize: 11,
                    }}
                  />
                  {/* Vertical reference separating past dates from the projection period. */}
                  <ReferenceLine
                    x={timestamp(today)}
                    stroke="var(--muted-foreground)"
                    strokeDasharray="2 4"
                    label={{
                      value: t('goalChart.today'),
                      position: 'insideTopLeft',
                      fontSize: 11,
                      fill: 'var(--muted-foreground)',
                    }}
                  />
                  {/* Draw the deadline only when it falls inside the visible date range. */}
                  {goal.target_date &&
                    timestamp(goal.target_date) >= start &&
                    timestamp(goal.target_date) <= end && (
                      <ReferenceLine
                        x={timestamp(goal.target_date)}
                        stroke="var(--foreground)"
                        label={{
                          value: t('goalChart.deadline'),
                          position: 'insideTopRight',
                          fontSize: 11,
                          fill: 'var(--foreground)',
                        }}
                      />
                    )}
                  {/* Solid history line: join actual snapshots, skipping projection-only rows. */}
                  <Line
                    type="linear"
                    dataKey="actual"
                    name={t('goalChart.actual')}
                    stroke="var(--primary)"
                    strokeWidth={2.5}
                    dot={actualPoints === 1}
                    connectNulls
                    isAnimationActive={false}
                  />
                  {/* Straight dashed pace guide from today to the target on the deadline.
                      Reached/overdue dated goals show a current dot; undated goals omit it. */}
                  {plan.projection.length > 0 && (
                    <Line
                      type="linear"
                      dataKey="projected"
                      name={t(
                        plan.projection.length > 1 ? 'goalChart.projected' : 'goalChart.current',
                      )}
                      stroke="var(--primary)"
                      strokeWidth={2}
                      strokeOpacity={0.7}
                      strokeDasharray={plan.projection.length > 1 ? '6 5' : undefined}
                      dot={plan.projection.length === 1}
                      connectNulls
                      isAnimationActive={false}
                    />
                  )}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
          {/* Legend explains the line styles and identifies the goal currency. */}
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
            <span className="flex items-center gap-2">
              <i className="w-5 border-t-2 border-[var(--primary)]" />
              {t('goalChart.actual')}
            </span>
            {plan.projection.length > 0 && (
              <span className="flex items-center gap-2">
                <i
                  className={
                    plan.projection.length > 1
                      ? 'w-5 border-t-2 border-dashed border-[var(--primary)] opacity-70'
                      : 'h-2 w-2 rounded-full bg-[var(--primary)] opacity-70'
                  }
                />
                {t(plan.projection.length > 1 ? 'goalChart.projected' : 'goalChart.current')}
              </span>
            )}
            <span>{goal.currency}</span>
          </div>
        </div>
      )}
    </section>
  )
}
