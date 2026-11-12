import { describe, expect, it } from 'vitest'

import {
  normalizeGoal,
  normalizeGoalAllocation,
  normalizeGoalSummary,
  type ApiGoal,
  type ApiGoalAllocation,
  type ApiGoalSummary,
} from './goal-api-normalization'

const goal = {
  id: 'goal-1',
  user_id: 'user-1',
  name: 'Emergency fund',
  target_amount: '500.00',
  current_amount: '100.00',
  currency: 'EUR',
  target_amount_primary: '500.00',
  current_amount_primary: '100.00',
  target_date: null,
  tracking_type: 'pocket',
  account_id: 'account-1',
  asset_id: null,
  asset_group_id: null,
  status: 'active',
  icon: null,
  color: null,
  position: 0,
  metadata_json: null,
  created_at: '2026-09-20T00:00:00Z',
  updated_at: '2026-09-20T00:00:00Z',
  percentage: 20,
  monthly_contribution: null,
  on_track: null,
  account_name: 'Checking',
  asset_name: null,
  asset_group_name: null,
  account_balance: '1000.00',
  account_reserved_total: '150.00',
  account_available: '850.00',
  is_underfunded: false,
} satisfies ApiGoal

describe('goal API normalization', () => {
  it('turns decimal strings into numbers before multiple pockets are summed', () => {
    const goals = [
      normalizeGoal(goal),
      normalizeGoal({ ...goal, id: 'goal-2', current_amount: '50.00' }),
    ]

    expect(goals.reduce((sum, item) => sum + item.current_amount, 0)).toBe(150)
    expect(goals[0]).toMatchObject({
      target_amount: 500,
      current_amount: 100,
      target_amount_primary: 500,
      current_amount_primary: 100,
      account_balance: 1000,
      account_reserved_total: 150,
      account_available: 850,
    })
  })

  it('normalizes summary and activity amounts', () => {
    const summary = normalizeGoalSummary({
      id: 'goal-1',
      name: 'Emergency fund',
      target_amount: '500.00',
      current_amount: '100.00',
      currency: 'EUR',
      target_date: null,
      status: 'active',
      icon: null,
      color: null,
      percentage: 20,
      monthly_contribution: null,
      on_track: null,
    } satisfies ApiGoalSummary)
    const allocation = normalizeGoalAllocation({
      id: 'allocation-1',
      goal_id: 'goal-1',
      goal_name: 'Emergency fund',
      transaction_id: null,
      amount: '25.00',
      source: 'adjustment',
      created_at: '2026-09-20T00:00:00Z',
      updated_at: '2026-09-20T00:00:00Z',
    } satisfies ApiGoalAllocation)

    expect(summary).toMatchObject({ target_amount: 500, current_amount: 100 })
    expect(allocation.amount).toBe(25)
  })

  it('rejects an invalid decimal instead of leaking NaN into the UI', () => {
    expect(() => normalizeGoal({ ...goal, current_amount: 'not-a-number' })).toThrow(
      'Invalid decimal value for goal.current_amount',
    )
  })
})
