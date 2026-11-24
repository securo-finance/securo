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

  it('uses the API monthly requirement and ignores old plan metadata', () => {
    const g = goal(
      {
        monthly_contribution: 471.43,
        metadata_json: { goal_tracker: { planned_monthly: '9999' } },
      },
      today,
    )
    const { projection } = projectGoal(g, today)
    expect(projection[1]).toEqual({ date: '2026-12-01', amount: 3171.43 })
    expect(projection.at(-1)).toEqual({ date: '2027-06-01', amount: 6000.01 })
  })

  it('does not count a payment today on the first', () => {
    expect(
      projectGoal(goal({ monthly_contribution: 100 }, today), '2026-12-01').projection[1].date,
    ).toBe('2027-01-01')
  })

  it('does not invent a rate for missing, zero or negative API requirements', () => {
    for (const monthly_contribution of [null, 0, -1]) {
      expect(projectGoal(goal({ monthly_contribution }, today), today).projection).toEqual([
        { date: today, amount: 2700 },
      ])
    }
  })

  it('does not project further for reached or overdue goals', () => {
    expect(
      projectGoal(goal({ current_amount: 6500, monthly_contribution: 100 }, today), today)
        .projection,
    ).toHaveLength(1)
    expect(
      projectGoal(goal({ target_date: today, monthly_contribution: 100 }, today), today).projection,
    ).toHaveLength(1)
  })

  it('caps long chart series at ten years', () => {
    expect(projectGoal(goal({ monthly_contribution: 0.01 }, today), today).projection).toHaveLength(
      121,
    )
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
