import { accounts, assets, reports, transactions } from '@/lib/api'
import type { Goal } from '@/types'
import {
  cleanHistory,
  combineWalletHistories,
  firstOfMonthAfter,
  type AmountPoint,
} from './goal-projection'

export type HistoryNote =
  'manual' | 'currency' | 'missing' | 'wallet' | 'reconstructed' | 'netWorth' | 'partial'

export interface GoalHistory {
  points: AmountPoint[]
  notes: HistoryNote[]
}

/**
 * Loads a goal's history through the existing account, asset, and report APIs.
 * Account and asset histories retain all available dates through today. Wallet
 * history combines member valuations. Net-worth history starts three calendar
 * months back, or at its first positive snapshot if that is more recent.
 * Manual goals have no history; currency mismatches and missing data return
 * explanatory notes instead of invented balances.
 *
 * @param {Goal} goal - Goal identifying its tracking source and display currency.
 * @param {string} today - Current workspace date in YYYY-MM-DD format.
 * @returns {Promise<GoalHistory>} Historical amounts and notes describing unavailable or derived data.
 * @throws {Error} Propagates API failures so the chart can offer a retry.
 */
export async function loadGoalHistory(goal: Goal, today: string): Promise<GoalHistory> {
  /**
   * Cleans history through today and adds a missing-data note when no points remain.
   *
   * @param {AmountPoint[]} points - Historical amounts to filter and deduplicate.
   * @param {HistoryNote[]} [notes=[]] - Existing notes to retain.
   * @returns {GoalHistory} Cleaned history with any missing-data note.
   */
  const result = (points: AmountPoint[], notes: HistoryNote[] = []): GoalHistory => {
    const clean = cleanHistory(points, '0001-01-01', today)
    return { points: clean, notes: [...notes, ...(clean.length === 0 ? ['missing' as const] : [])] }
  }

  // manual goals don't have history.
  if (goal.tracking_type === 'manual') {
    return { points: [], notes: ['manual'] }
  }


  if (goal.tracking_type === 'account' && goal.account_id) {
    const account = await accounts.get(goal.account_id)
    if (account.currency !== goal.currency) {
      return { points: [], notes: ['currency'] }
    }
    const first = await transactions.list({
      account_id: goal.account_id,
      sort_by: 'transaction_date',
      sort_dir: 'asc',
      limit: 1,
      include_opening_balance: true,
    })
    const from = first.items[0]?.date ?? today
    const points = await accounts.balanceHistory(
      goal.account_id,
      from > today ? today : from,
      today,
    )
    return result(
      points.map(p => ({ date: p.date, amount: p.balance })),
      ['reconstructed'],
    )
  }

  // for tying the goal to an asset, we simply use the assets valueTrend.
  if (goal.tracking_type === 'asset' && goal.asset_id) {
    const asset = await assets.get(goal.asset_id)
    if (asset.currency !== goal.currency) {
      return { points: [], notes: ['currency'] }
    }
    return result(await assets.valueTrend(goal.asset_id), ['reconstructed'])
  }

  // for a wallet, we have to combine the entire wallets history for all assets under it
  // and use that total sum for the history.
  if (goal.tracking_type === 'asset_group' && goal.asset_group_id) {
    const members = (await assets.list()).filter(
      a => a.group_id === goal.asset_group_id && !a.is_archived,
    )
    if (members.some(a => a.currency !== goal.currency)) {
      return { points: [], notes: ['currency'] }
    }
    if (members.length === 0) {
      return { points: [], notes: ['missing'] }
    }
    const series = await Promise.all(
      members.map(async asset => ({
        purchase_date: asset.purchase_date,
        sell_date: asset.sell_date,
        points: await assets.valueTrend(asset.id),
      })),
    )
    const points = combineWalletHistories(series, '0001-01-01', today)
    return result(points, [
      'wallet',
      ...(series.some(s => !s.points.length) ? ['partial' as const] : []),
    ])
  }

  // for net worth we use the net worth report to establish the previous values.
  if (goal.tracking_type === 'net_worth') {
    const [first, range, members] = await Promise.all([
      transactions.list({
        sort_by: 'transaction_date',
        sort_dir: 'asc',
        limit: 1,
        include_opening_balance: true,
      }),
      reports.dataRange(),
      assets.list(true),
    ])
    const histories = await Promise.all(members.map(asset => assets.valueTrend(asset.id)))
    // find the earliest date where net worth would appear
    const dates = [
      today,
      first.items[0]?.date,
      range.min_date,
      ...members.map(a => a.purchase_date),
      ...histories.flatMap(points => points.map(p => p.date)),
    ]
      .filter((date): date is string => !!date && date <= today)
      .sort()
    const points: AmountPoint[] = []

    // Fetch adjoining custom ranges of up to ten years, using monthly snapshots.
    // Explicit start/end dates override the default 12-month window; the backend
    // caps each custom range at ten years.
    for (const [from, to] of reportHistoryWindows(dates[0], today)) {
      const report = await reports.netWorth(
        12,
        'monthly',
        undefined,
        undefined,
        undefined,
        from,
        to,
      )
      if (report.meta.currency !== goal.currency) {
        return { points: [], notes: ['currency'] }
      }
      points.push(
        ...report.trend.map(p => ({ date: monthlySnapshotDate(p.date, to), amount: p.value })),
      )
    }

    const history = cleanHistory(points, '0001-01-01', today)
    const firstPositive = history.find(point => point.amount > 0)?.date
    const threeMonthsAgo = firstOfMonthAfter(today, -3)

    // special: use either the first positive value in the date, or use three months ago as the start of the
    // graph.
    const start = firstPositive && firstPositive > threeMonthsAgo ? firstPositive : threeMonthsAgo
    // Keep later dips below zero; only trim the beginning of the series.
    return result(
      history.filter(point => point.date >= start),
      ['netWorth'],
    )
  }
  return { points: [], notes: ['missing'] }
}

/**
 * Restores the date represented by a monthly report's YYYY-MM label.
 * Snapshots fall on month end, capped at the requested range end, matching the
 * backend's monthly snapshot convention. Other label formats pass through.
 *
 * @param {string} label - Report date label, normally YYYY-MM.
 * @param {string} end - Inclusive report range end in YYYY-MM-DD format.
 * @returns {string} The snapshot date in YYYY-MM-DD format, or the unchanged label.
 */
function monthlySnapshotDate(label: string, end: string): string {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(label)) {
    return label
  }
  const [year, month] = label.split('-').map(Number)
  // The report labels month-end snapshots YYYY-MM; the final month is
  // capped at the requested end date, matching the backend's _date_points.
  const monthEnd = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10)
  return monthEnd > end ? end : monthEnd
}

/**
 * Splits an inclusive date range into adjoining report requests of at most ten
 * calendar years. Windows after the first begin on January 1 to avoid splitting
 * monthly snapshots across requests; the final window ends on today.
 *
 * @param {string} from - Earliest history date in YYYY-MM-DD format.
 * @param {string} today - Inclusive upper date bound in YYYY-MM-DD format.
 * @returns {Array<[string, string]>} Inclusive start/end pairs, or an empty array when from is after today.
 */
export function reportHistoryWindows(from: string, today: string): [string, string][] {
  const windows: [string, string][] = []
  let start = from
  while (start <= today) {
    const next = `${String(Number(start.slice(0, 4)) + 10).padStart(4, '0')}-01-01`
    const end = next > today ? today : `${Number(next.slice(0, 4)) - 1}-12-31`
    windows.push([start, end])
    start = next
  }
  return windows
}
