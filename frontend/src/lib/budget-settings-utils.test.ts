import { describe, expect, it } from 'vitest'
import type { Budget } from '@/types'
import {
  budgetDisplayAmount,
  normalizeBudgetComparisonRows,
  normalizeBudgetForecast,
  normalizeBudgetRolloverSummary,
  parseBudgetDraft,
  planBudgetChange,
} from '@/lib/budget-settings-utils'

function budget(overrides: Partial<Budget>): Budget {
  return {
    id: 'b1',
    user_id: 'u',
    category_id: 'food',
    amount: 100,
    month: '2026-08-01',
    is_recurring: true,
    rollover: false,
    reset_rollover: false,
    ...overrides,
  }
}

const month = '2026-10-01'
const plan = (
  amount: number,
  recurring: boolean,
  current?: Budget,
  rollover = false,
  resetRollover = false,
) => planBudgetChange({ categoryId: 'food', amount, recurring, rollover, resetRollover, current, month })

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
      rollover: true, reset_rollover: false, carryover_amount: '150.25', effective_budget_amount: '1350.75',
    }])[0]).toMatchObject({
      budget_amount: 1200.5,
      actual_amount: 2.25,
      percentage_used: 0.2,
      rollover: true,
      reset_rollover: false,
      carryover_amount: 150.25,
      effective_budget_amount: 1350.75,
    })

    expect(normalizeBudgetRolloverSummary({
      month: '2026-10-01',
      total_budgeted: '1000.00',
      total_spent: '400.00',
      total_projected_spent: '500.00',
      net_surplus: '700.00',
      total_carryover: '200.00',
      total_effective_budget: '1200.00',
      categories: [{
        category_id: 'food',
        category_name: 'Food',
        budgeted: '1000.00',
        actual_spent: '400.00',
        projected_spent: '500.00',
        remaining: '700.00',
        carryover_eligible: true,
        rollover_enabled: true,
        reset_rollover: false,
        carryover_in: '200.00',
        effective_budget: '1200.00',
      }],
    })).toMatchObject({
      total_budgeted: 1000,
      total_carryover: 200,
      total_effective_budget: 1200,
      net_surplus: 700,
    })

    expect(normalizeBudgetForecast({
      forecast_months: 3,
      items: [{
        month: '2026-10-01',
        projected_budget: '1000.00',
        projected_spend: '600.00',
        projected_variance: '600.00',
        carryover_balance: '200.00',
        effective_budget: '1200.00',
      }],
    }).items[0]).toMatchObject({
      projected_budget: 1000,
      carryover_balance: 200,
      effective_budget: 1200,
      projected_spend: 600,
      projected_variance: 600,
    })
  })
})

describe('planBudgetChange', () => {
  it('does nothing when amount, repetition, and rollover are unchanged', () => {
    expect(plan(100, true, budget({}))).toEqual([])
    expect(plan(0, true, undefined)).toEqual([])
  })

  it('compares with the limit in the display currency, not the typed one', () => {
    const inBrl = budget({ amount: 1800, currency: 'BRL', amount_primary: '360.00' })
    expect(budgetDisplayAmount(inBrl)).toBe(360)
    expect(plan(360, true, inBrl)).toEqual([])
    expect(plan(1800, true, inBrl)).toEqual([
      { type: 'update', id: 'b1', data: { amount: 1800, effective_month: month, rollover: false } },
    ])
  })

  it('emits an update when toggling rollover or resetting carryover with unchanged amount', () => {
    expect(plan(100, true, budget({}), true, false)).toEqual([
      { type: 'update', id: 'b1', data: { amount: 100, effective_month: month, rollover: true } },
    ])
    expect(plan(100, true, budget({ rollover: true }), true, true)).toEqual([
      { type: 'update', id: 'b1', data: { amount: 100, effective_month: month, rollover: true, reset_rollover: true } },
    ])
  })

  it('starts a new recurring amount from this month on', () => {
    expect(plan(150, true, budget({}))).toEqual([
      { type: 'update', id: 'b1', data: { amount: 150, effective_month: month, rollover: false } },
    ])
    expect(plan(150, true, undefined)).toEqual([
      { type: 'create', data: { category_id: 'food', amount: 150, month, is_recurring: true, rollover: false } },
    ])
  })

  it('turns a month-only budget into a recurring one', () => {
    expect(plan(100, true, budget({ id: 'o1', month, is_recurring: false }))).toEqual([
      { type: 'delete', id: 'o1' },
      { type: 'create', data: { category_id: 'food', amount: 100, month, is_recurring: true, rollover: false } },
    ])
  })

  it('changes only this month when not repeating', () => {
    expect(plan(80, false, budget({}))).toEqual([
      { type: 'create', data: { category_id: 'food', amount: 80, month, is_recurring: false, rollover: false } },
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: '2026-11-01', rollover: false } },
    ])
    expect(plan(80, false, budget({ id: 'o1', month, is_recurring: false }))).toEqual([
      { type: 'update', id: 'o1', data: { amount: 80, rollover: false } },
    ])
  })

  it('stops a recurring budget after this month when repetition is switched off', () => {
    expect(plan(100, false, budget({}))).toEqual([
      { type: 'create', data: { category_id: 'food', amount: 100, month, is_recurring: false, rollover: false } },
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: '2026-11-01', rollover: false } },
    ])
  })

  it('keeps this month at the new amount and stops later months when repetition is switched off', () => {
    expect(plan(80, false, budget({}))).toEqual([
      { type: 'create', data: { category_id: 'food', amount: 80, month, is_recurring: false, rollover: false } },
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: '2026-11-01', rollover: false } },
    ])
  })

  it('keeps zero tombstones and forces rollover: false when clearing one-off and recurring budgets', () => {
    expect(plan(0, true, budget({ id: 'o1', month, is_recurring: false, rollover: true }), true)).toEqual([
      { type: 'update', id: 'o1', data: { amount: 0, rollover: false } },
    ])
    expect(plan(0, true, budget({ id: 'z1', amount: 0, is_recurring: false }))).toEqual([
      { type: 'update', id: 'z1', data: { amount: 0, rollover: false } },
    ])
    expect(plan(0, true, budget({ month, rollover: true }), true)).toEqual([
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: month, rollover: false } },
    ])
    expect(plan(0, true, budget({ id: 'zr', amount: 0, month }))).toEqual([
      { type: 'update', id: 'zr', data: { amount: 0, effective_month: month, rollover: false } },
    ])
  })

  it('keeps a cleared month-only override as a zero tombstone and removes future segments', () => {
    expect(planBudgetChange({
      categoryId: 'food', amount: 0, recurring: false,
      current: budget({ id: 'oct-oneoff', month, is_recurring: false }),
      series: [
        budget({ id: 'aug-series', month: '2026-08-01' }),
        budget({ id: 'oct-oneoff', month, is_recurring: false }),
        budget({ id: 'dec-edit', month: '2026-12-01', amount: 250 }),
      ],
      month,
    })).toEqual([
      { type: 'delete', id: 'dec-edit' },
      { type: 'update', id: 'oct-oneoff', data: { amount: 0, rollover: false } },
      { type: 'update', id: 'aug-series', data: { amount: 0, effective_month: '2026-11-01', rollover: false } },
    ])
  })

  it('clearing the start of a series removes later recurring edits', () => {
    expect(planBudgetChange({
      categoryId: 'food', amount: 0, recurring: true,
      current: budget({ id: 'aug-series', month: '2026-08-01' }),
      series: [
        budget({ id: 'aug-series', month: '2026-08-01' }),
        budget({ id: 'dec-edit', month: '2026-12-01', amount: 250 }),
      ],
      month: '2026-08-01',
    })).toEqual([
      { type: 'delete', id: 'dec-edit' },
      { type: 'update', id: 'aug-series', data: { amount: 0, effective_month: '2026-08-01', rollover: false } },
    ])
  })

  it('keeps earlier months of a recurring budget when clearing this month', () => {
    expect(plan(0, true, budget({}))).toEqual([
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: month, rollover: false } },
    ])
    expect(plan(0, false, budget({}))).toEqual([
      { type: 'update', id: 'b1', data: { amount: 0, effective_month: month, rollover: false } },
    ])
  })
})
