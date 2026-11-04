import { describe, expect, it } from 'vitest'

import type { TransactionCalendarDay, TransactionCalendarItem } from '../types'
import { activityChartData, dayActivity, isCalendarItemInteractive } from './calendar-activity'

function day(overrides: Partial<TransactionCalendarDay>): TransactionCalendarDay {
  return {
    date: '2026-07-01',
    in_month: true,
    ending_balance: 0,
    income: 0,
    expense: 0,
    transfer_net: 0,
    actual_income: 0,
    actual_expense: 0,
    actual_transfer_net: 0,
    projected_income: 0,
    projected_expense: 0,
    projected_transfer_net: 0,
    actual_count: 0,
    projected_count: 0,
    has_income: false,
    has_expense: false,
    has_transfer: false,
    items: [],
    ...overrides,
  }
}

describe('dayActivity', () => {
  it('derives nets for a day with only actual activity', () => {
    const activity = dayActivity(day({ actual_income: 500, actual_expense: 120 }))
    expect(activity.actualNet).toBe(380)
    expect(activity.projectedNet).toBe(0)
    expect(activity.hasActual).toBe(true)
    expect(activity.hasProjected).toBe(false)
  })

  it('derives nets for a projected-only day', () => {
    const activity = dayActivity(day({ projected_income: 80, projected_expense: 50 }))
    expect(activity.actualNet).toBe(0)
    expect(activity.projectedNet).toBe(30)
    expect(activity.hasActual).toBe(false)
    expect(activity.hasProjected).toBe(true)
  })

  it('keeps actual and projected separate on a mixed day', () => {
    const activity = dayActivity(day({
      actual_income: 400, actual_expense: 100,
      projected_income: 200, projected_expense: 60,
    }))
    expect(activity.actualNet).toBe(300)
    expect(activity.projectedNet).toBe(140)
    expect(activity.hasActual).toBe(true)
    expect(activity.hasProjected).toBe(true)
  })

  it('treats a transfer-only day as no activity', () => {
    const activity = dayActivity(day({ actual_transfer_net: -250, has_transfer: true }))
    expect(activity.hasActual).toBe(false)
    expect(activity.hasProjected).toBe(false)
    expect(activity.actualNet).toBe(0)
  })

  it('handles zero amounts without flagging activity', () => {
    const activity = dayActivity(day({}))
    expect(activity.hasActual).toBe(false)
    expect(activity.actualNet).toBe(0)
    expect(activity.projectedNet).toBe(0)
  })

  it('tolerates responses without the split fields', () => {
    const legacy = day({ income: 100, expense: 40 })
    // Simulate an older payload where the split fields are absent.
    const bare = { ...legacy } as Record<string, unknown>
    delete bare.actual_income
    delete bare.actual_expense
    delete bare.projected_income
    delete bare.projected_expense
    const activity = dayActivity(bare as unknown as TransactionCalendarDay)
    expect(activity.actualNet).toBe(0)
    expect(activity.hasActual).toBe(false)
  })
})

describe('activityChartData', () => {
  it('scales to the combined actual+projected height per direction', () => {
    const data = activityChartData([
      day({ date: '2026-07-01', actual_income: 300, projected_income: 200 }),
      day({ date: '2026-07-02', actual_expense: 150, projected_expense: 100 }),
      day({ date: '2026-07-03', actual_income: 50 }),
    ])
    expect(data.maxUp).toBe(500)
    expect(data.maxDown).toBe(250)
    expect(data.hasActivity).toBe(true)
    expect(data.days).toHaveLength(3)
  })

  it('reports an empty month so the chart can show an empty state', () => {
    const data = activityChartData([
      day({ date: '2026-07-01' }),
      day({ date: '2026-07-02', actual_transfer_net: 90, has_transfer: true }),
    ])
    expect(data.maxUp).toBe(0)
    expect(data.maxDown).toBe(0)
    expect(data.hasActivity).toBe(false)
  })

  it('handles an empty day list', () => {
    const data = activityChartData([])
    expect(data.days).toEqual([])
    expect(data.hasActivity).toBe(false)
  })
})

describe('isCalendarItemInteractive', () => {
  it('allows persisted pending and future forecast rows to open', () => {
    const item = {
      kind: 'projected',
      id: 'transaction-id',
    } as TransactionCalendarItem

    expect(isCalendarItemInteractive(item)).toBe(true)
  })

  it('keeps virtual recurring projections read-only', () => {
    const item = {
      kind: 'projected',
      id: null,
      recurring_id: 'recurring-id',
    } as TransactionCalendarItem

    expect(isCalendarItemInteractive(item)).toBe(false)
  })
})
