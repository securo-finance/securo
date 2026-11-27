import { accounts, assets, reports } from '@/lib/api'
import type { Goal } from '@/types'
import {
  carryForward,
  cleanHistory,
  goalHistoryStart,
  historyMonths,
  type AmountPoint,
} from './goal-projection'

export type HistoryNote =
  'manual' | 'currency' | 'missing' | 'wallet' | 'reconstructed' | 'netWorth'

export interface GoalHistory {
  points: AmountPoint[]
  notes: HistoryNote[]
}

/**
 * Loads a goal's history through the existing account, asset, and report APIs.
 * Histories start one month before goal creation, capped at ten years. Asset history
 * requests the whole window and carries the last valuation from before it forward.
 * Wallet history uses weekly wallet-filtered net-worth reports; net worth uses weekly
 * unfiltered reports. Manual goals have no history; currency mismatches and missing
 * data return explanatory notes instead of invented balances.
 *
 * @param {Goal} goal - Goal identifying its tracking source and display currency.
 * @param {string} today - Current workspace date in YYYY-MM-DD format.
 * @returns {Promise<GoalHistory>} Historical amounts and notes describing unavailable or derived data.
 * @throws {Error} Propagates API failures so the chart can offer a retry.
 */
export async function loadGoalHistory(goal: Goal, today: string): Promise<GoalHistory> {
  const earliest = goalHistoryStart(goal, today)

  /**
   * Cleans history through today and adds a missing-data note when no points remain.
   *
   * @param {AmountPoint[]} points - Historical amounts to filter and deduplicate.
   * @param {HistoryNote[]} [notes=[]] - Existing notes to retain.
   * @returns {GoalHistory} Cleaned history with any missing-data note.
   */
  const result = (points: AmountPoint[], notes: HistoryNote[] = []): GoalHistory => {
    const clean = cleanHistory(points, earliest, today)
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

    const points = await accounts.balanceHistory(goal.account_id, earliest, today)
    // Balance history reports connected liabilities as debt-positive; goals use
    // signed balances. Manual liabilities already have the goal sign convention.
    const sign =
      account.connection_id && (account.type === 'credit_card' || account.type === 'loan')
        ? -1
        : 1
    return result(
      points.map(p => ({ date: p.date, amount: p.balance === 0 ? 0 : p.balance * sign })),
      ['reconstructed'],
    )
  }

  // for tying the goal to an asset, we simply use the assets valueTrend.
  // Request as many months as the window covers and carry the last valuation
  // before it forward, so sparse valuations still draw inside the window.
  if (goal.tracking_type === 'asset' && goal.asset_id) {
    const asset = await assets.get(goal.asset_id)
    if (asset.currency !== goal.currency) {
      return { points: [], notes: ['currency'] }
    }
    const trend = await assets.valueTrend(goal.asset_id, historyMonths(earliest, today))
    return result(carryForward(trend, earliest), ['reconstructed'])
  }

  // historical wallet totals are computed using the net worth report limited to that wallet.
  if (goal.tracking_type === 'asset_group' && goal.asset_group_id) {
    const members = (await assets.list()).filter(
      asset => asset.group_id === goal.asset_group_id && !asset.is_archived,
    )
    if (members.length === 0) {
      return { points: [], notes: ['missing'] }
    }

    const report = await reports.netWorth(
      undefined, 'weekly', undefined, [goal.asset_group_id], undefined, earliest, today,
    )
    if (report.meta.currency !== goal.currency) {
      return { points: [], notes: ['currency'] }
    }
    const points = report.trend.map(point => ({
      date: weeklySnapshotDate(point.date, earliest, today),
      amount: point.value,
    }))
    return result(points, ['wallet'])
  }

  // historical net worth values are computed from the net worth report.
  if (goal.tracking_type === 'net_worth') {
    const report = await reports.netWorth(
      undefined, 'weekly', undefined, undefined, undefined, earliest, today,
    )
    if (report.meta.currency !== goal.currency) {
      return { points: [], notes: ['currency'] }
    }
    const points = report.trend.map(point => ({
      date: weeklySnapshotDate(point.date, earliest, today),
      amount: point.value,
    }))
    return result(points, ['netWorth'])
  }
  return { points: [], notes: ['missing'] }
}

/**
 * Restores report snapshot dates from ISO week labels such as 2026-W47.
 * The backend samples every seven days from the request's start weekday and
 * replaces or appends the final snapshot with the exact end date.
 *
 * @param {string} label - ISO week label, or a full date to preserve unchanged.
 * @param {string} from - Inclusive request start in YYYY-MM-DD format.
 * @param {string} to - Inclusive request end in YYYY-MM-DD format.
 * @returns {string} Actual snapshot date in YYYY-MM-DD format.
 */
function weeklySnapshotDate(label: string, from: string, to: string): string {
  const match = /^(\d{4})-W(\d{2})$/.exec(label)
  if (!match) {
    return label
  }
  const dayMs = 86_400_000
  const january4 = new Date(Date.UTC(Number(match[1]), 0, 4))
  const monday =
    january4.getTime() -
    ((january4.getUTCDay() + 6) % 7) * dayMs +
    (Number(match[2]) - 1) * 7 * dayMs
  const end = Date.parse(to + 'T00:00:00Z')
  if (end >= monday && end < monday + 7 * dayMs) {
    return to
  }
  const weekday = (new Date(from + 'T00:00:00Z').getUTCDay() + 6) % 7
  return new Date(monday + weekday * dayMs).toISOString().slice(0, 10)
}
