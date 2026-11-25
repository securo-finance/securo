import { describe, expect, it } from 'vitest'
import { goal } from '@/test/goal-fixtures'
import {
  calendarMonthAfter,
  cleanHistory,
  combineWalletHistories,
  projectGoal,
} from './goal-projection'

const today = '2026-11-21'
describe('goal projection', () => {
  it('pads deadlines by one calendar month, clamping to the destination month end', () => {
    expect(calendarMonthAfter('2027-06-15')).toBe('2027-07-15')
    expect(calendarMonthAfter('2027-01-31')).toBe('2027-02-28')
    expect(calendarMonthAfter('2028-01-31')).toBe('2028-02-29')
    expect(calendarMonthAfter('2027-12-31')).toBe('2028-01-31')
  })

  it.each([null, 0, -1, 33.33, 8000])(
    'connects current amount directly to the target regardless of monthly rate %s',
    monthly_contribution => {
      const testGoal = goal({ monthly_contribution, target_date: '2027-02-15' }, today)
      expect(projectGoal(testGoal, today).projection).toEqual([
        { date: today, amount: 2700 },
        { date: '2027-02-15', amount: 6000 },
      ])
    },
  )

  it.each(['2026-11-30', '2026-12-01', '2040-02-15'])(
    'uses the exact deadline %s with no intermediate payment points',
    target_date => {
      expect(projectGoal(goal({ target_date }, today), today).projection).toEqual([
        { date: today, amount: 2700 },
        { date: target_date, amount: 6000 },
      ])
    },
  )

  it('has no projection for an undated goal even when a monthly rate is available', () => {
    const testGoal = goal({ target_date: null, monthly_contribution: 100 }, today)
    expect(projectGoal(testGoal, today).projection).toEqual([])
  })

  it('keeps only the current point for reached, due-today, and overdue dated goals', () => {
    for (const overrides of [
      { current_amount: 6500 },
      { current_amount: 6000 },
      { target_date: today },
      { target_date: '2026-11-01' },
    ]) {
      const testGoal = goal(overrides, today)
      expect(projectGoal(testGoal, today).projection).toEqual([
        { date: today, amount: testGoal.current_amount },
      ])
    }
  })

  it('omits future/invalid history and deduplicates dates', () => {
    expect(
      cleanHistory(
        [
          { date: today, amount: 10 },
          { date: today, amount: 11 },
          { date: '2027-01-01', amount: 99 },
          { date: 'invalid', amount: 12 },
        ],
        '2026-01-01',
        today,
      ),
    ).toEqual([{ date: today, amount: 11 }])
  })

  it('does not silently count missing wallet valuations as zero', () => {
    const points = combineWalletHistories(
      [
        {
          purchase_date: null,
          sell_date: null,
          points: [
            { date: '2026-01-01', amount: 100 },
            { date: '2026-02-01', amount: 110 },
          ],
        },
        { purchase_date: null, sell_date: null, points: [{ date: '2026-02-01', amount: 200 }] },
      ],
      '2026-01-01',
      today,
    )
    expect(points).toEqual([{ date: '2026-02-01', amount: 310 }])
  })

  it('carries earlier valuations forward and drops sold assets after sale', () => {
    expect(
      combineWalletHistories(
        [
          {
            purchase_date: '2025-01-01',
            sell_date: '2026-02-01',
            points: [{ date: '2026-01-01', amount: 100 }],
          },
        ],
        '2026-01-01',
        today,
      ),
    ).toEqual([
      { date: '2026-01-01', amount: 100 },
      { date: '2026-02-02', amount: 0 },
    ])
  })
})
