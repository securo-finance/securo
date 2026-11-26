import { goalHistoryStart } from './goal-projection'
import { describe, expect, it } from 'vitest'
import { goal } from '@/test/goal-fixtures'
import { calendarMonthAfter, cleanHistory, projectGoal } from './goal-projection'

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
      const projection = projectGoal(testGoal, today).projection
      expect(projection[0]).toEqual({ date: today, amount: 2700 })
      expect(projection.at(-1)).toEqual({ date: '2027-02-15', amount: 6000 })
      expect(projection[1].date).toBe('2026-11-28')
      expect(projection.length).toBeGreaterThan(2)
    },
  )

  it.each(['2026-11-30', '2026-12-01', '2040-02-15'])(
    'uses weekly samples and the exact deadline %s',
    target_date => {
      const projection = projectGoal(goal({ target_date }, today), today).projection
      expect(projection[0]).toEqual({ date: today, amount: 2700 })
      expect(projection.at(-1)).toEqual({ date: target_date, amount: 6000 })
      for (let index = 1; index < projection.length; index++) {
        const gap = Date.parse(projection[index].date) - Date.parse(projection[index - 1].date)
        expect(gap).toBeGreaterThan(0)
        expect(gap).toBeLessThanOrEqual(7 * 86_400_000)
        expect(projection[index].amount).toBeLessThanOrEqual(6000)
      }
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
})

it.each([
  { today: '2026-11-21', expected: '2016-11-21' },
  { today: '2024-02-29', expected: '2014-03-01' },
])('caps history at ten years on $today', ({ today, expected }) => {
  expect(goalHistoryStart(goal({ created_at: '1995-01-01T00:00:00Z' }, today), today)).toBe(expected)
})

it('interpolates weekly debt progress and keeps a short final interval', () => {
  const projection = projectGoal(
    goal({ current_amount: -100, target_amount: 0, target_date: '2026-12-07' }, today),
    today,
  ).projection
  expect(projection).toEqual([
    { date: today, amount: -100 },
    { date: '2026-11-28', amount: -56.25 },
    { date: '2026-12-05', amount: -12.5 },
    { date: '2026-12-07', amount: 0 },
  ])
})
