import { describe, expect, it } from 'vitest'
import { goal } from '@/test/goal-fixtures'
import {
  axisTicks,
  calendarMonthAfter,
  carryForward,
  cleanHistory,
  goalHistoryStart,
  historyMonths,
  projectGoal,
  targetLabelSide,
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

describe('goal history window', () => {
  it.each([
    { from: '2026-02-01', to: '2026-11-21', months: 10 },
    { from: '2016-11-21', to: '2026-11-21', months: 120 },
    { from: '2016-01-01', to: '2026-11-21', months: 120 },
    { from: '2026-11-01', to: '2026-11-21', months: 1 },
  ])('counts $months months between $from and $to', ({ from, to, months }) => {
    expect(historyMonths(from, to)).toBe(months)
  })

  it('carries the last valuation before the window forward to its start', () => {
    expect(
      carryForward(
        [
          { date: '2026-01-28', amount: 4800 },
          { date: '2025-11-28', amount: 4200 },
        ],
        '2026-02-01',
      ),
    ).toEqual([
      { date: '2026-01-28', amount: 4800 },
      { date: '2025-11-28', amount: 4200 },
      { date: '2026-02-01', amount: 4800 },
    ])
  })

  it('keeps the later amount when two valuations share the last date', () => {
    expect(
      carryForward(
        [
          { date: '2026-01-28', amount: 4200 },
          { date: '2026-01-28', amount: 4800 },
        ],
        '2026-02-01',
      ),
    ).toEqual([
      { date: '2026-01-28', amount: 4200 },
      { date: '2026-01-28', amount: 4800 },
      { date: '2026-02-01', amount: 4800 },
    ])
  })

  it('leaves points untouched when a valuation already starts the window', () => {
    const points = [{ date: '2026-02-01', amount: 5000 }]
    expect(carryForward(points, '2026-02-01')).toBe(points)
  })

  it('adds nothing without a valid valuation before the window', () => {
    expect(carryForward([{ date: 'invalid', amount: 10 }], '2026-02-01')).toEqual([
      { date: 'invalid', amount: 10 },
    ])
    expect(carryForward([], '2026-02-01')).toEqual([])
  })
})

describe('goal chart axis', () => {
  it('keeps the month labels of a short window distinct', () => {
    const ticks = axisTicks('2026-10-21', '2026-12-01')
    const labels = ticks.map(time => new Date(time).toISOString().slice(0, 10))
    expect(labels).toEqual(['2026-10-21', '2026-11-01', '2026-12-01'])
    expect(new Set(labels.map(label => label.slice(0, 7))).size).toBe(labels.length)
  })

  it('keeps one tick per month when the window ends mid-month', () => {
    const ticks = axisTicks('2026-10-21', '2026-12-15')
    const labels = ticks.map(time => new Date(time).toISOString().slice(0, 10))
    expect(labels).toEqual(['2026-10-21', '2026-11-01', '2026-12-15'])
  })

  it('thins a ten-year window to at most six ticks and keeps both ends', () => {
    const ticks = axisTicks('2016-11-21', '2026-11-21')
    expect(ticks.length).toBeLessThanOrEqual(6)
    expect(ticks[0]).toBe(Date.parse('2016-11-21T00:00:00Z'))
    expect(ticks.at(-1)).toBe(Date.parse('2026-11-21T00:00:00Z'))
  })

  it('falls back to the start for an empty or inverted range', () => {
    expect(axisTicks('2026-11-21', '2026-11-21')).toEqual([Date.parse('2026-11-21T00:00:00Z')])
  })

  it.each([
    // On the floor the label goes above the line, clear of the x-axis labels.
    { target: 0, floor: 0, side: 'insideBottomRight' },
    { target: -100, floor: -100, side: 'insideBottomRight' },
    { target: 0, floor: -100, side: 'insideTopLeft' },
    { target: 6000, floor: 0, side: 'insideTopLeft' },
  ])('places a target of $target above a floor of $floor at $side', ({ target, floor, side }) => {
    expect(targetLabelSide(target, floor)).toBe(side)
  })
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
