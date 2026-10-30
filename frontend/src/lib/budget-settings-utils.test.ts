import { describe, expect, it } from 'vitest'
import type { Budget } from '@/types'
import { normalizeBudgetComparisonRows, parseBudgetDraft, planBudgetChange, typicalActualForCategory } from '@/lib/budget-settings-utils'

function budget(overrides: Partial<Budget>): Budget {
  return { id: 'b1', user_id: 'u', category_id: 'food', amount: 100, month: '2026-08-01', is_recurring: true, ...overrides }
}

const month = '2026-10-01'
const plan = (amount: number, recurring: boolean, current?: Budget) =>
  planBudgetChange({ categoryId: 'food', amount, recurring, current, month })

describe('parseBudgetDraft', () => {
  it('reads locale-formatted values and distinguishes invalid input from an empty budget', () => {
    expect(parseBudgetDraft('12,5', 'de-DE')).toBe(12.5)
    expect(parseBudgetDraft('1.200,00', 'de-DE')).toBe(1200)
    expect(parseBudgetDraft('400')).toBe(400)
    expect(parseBudgetDraft('')).toBe(0)
    expect(parseBudgetDraft('abc')).toBeNull()
    expect(parseBudgetDraft('-5')).toBeNull()
  })
})

describe('budget settings helpers', () => {
  it('normalizes decimal strings returned by the API', () => {
    expect(normalizeBudgetComparisonRows([{
      category_id: 'food', category_name: 'Food', category_icon: 'food', category_color: 'blue', group_id: null, group_name: null,
      budget_amount: '1200.50', actual_amount: '2.25', projected_amount: '3',
      prev_month_amount: '4', projected_prev_month_amount: '5', percentage_used: '0.2', is_recurring: false,
    }])[0]).toMatchObject({ budget_amount: 1200.5, actual_amount: 2.25, percentage_used: 0.2 })
  })

  it('uses the median of monthly spending for a category', () => {
    expect(typicalActualForCategory([
      [{ category_id: 'food', actual_amount: 10 } as never],
      [{ category_id: 'food', actual_amount: 20 } as never],
      [{ category_id: 'food', actual_amount: 100 } as never],
    ], 'food')).toBe(20)
  })
})

describe('planBudgetChange', () => {
  it('does nothing when amount and repetition are unchanged', () => {
    expect(plan(100, true, budget({}))).toEqual([])
    expect(plan(0, true, undefined)).toEqual([])
  })

  it('starts a new recurring amount from this month on', () => {
    expect(plan(150, true, budget({}))).toEqual([{ type: 'update', id: 'b1', data: { amount: 150, effective_month: month } }])
    expect(plan(150, true, undefined)).toEqual([
      { type: 'create', data: { category_id: 'food', amount: 150, month, is_recurring: true } },
    ])
  })

  it('turns a month-only budget into a recurring one', () => {
    expect(plan(100, true, budget({ id: 'o1', month, is_recurring: false }))).toEqual([
      { type: 'delete', id: 'o1' },
      { type: 'create', data: { category_id: 'food', amount: 100, month, is_recurring: true } },
    ])
  })

  it('changes only this month when not repeating', () => {
    expect(plan(80, false, budget({}))).toEqual([
      { type: 'create', data: { category_id: 'food', amount: 80, month, is_recurring: false } },
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: '2026-11-01' } },
    ])
    expect(plan(80, false, budget({ id: 'o1', month, is_recurring: false }))).toEqual([
      { type: 'update', id: 'o1', data: { amount: 80 } },
    ])
  })

  it('stops a recurring budget after this month when repetition is switched off', () => {
    expect(plan(100, false, budget({}))).toEqual([
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: '2026-11-01' } },
    ])
  })

  it('keeps this month at the new amount and stops later months when repetition is switched off', () => {
    expect(plan(80, false, budget({}))).toEqual([
      { type: 'create', data: { category_id: 'food', amount: 80, month, is_recurring: false } },
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: '2026-11-01' } },
    ])
  })

  it('clears a budget that starts this month and keeps earlier months of an older one', () => {
    expect(plan(0, true, budget({ id: 'o1', month, is_recurring: false }))).toEqual([{ type: 'update', id: 'o1', data: { amount: 0 } }])
    expect(plan(0, true, budget({ month }))).toEqual([{ type: 'update', id: 'b1', data: { amount: 0 } }])
    expect(plan(0, true, budget({}))).toEqual([{ type: 'update', id: 'b1', data: { amount: 0, effective_month: month } }])
    expect(plan(0, false, budget({}))).toEqual([
      { type: 'create', data: { category_id: 'food', amount: 0, month, is_recurring: false } },
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: '2026-11-01' } },
    ])
  })
})
