import type { Goal } from '@/types'
import { firstOfMonthAfter } from '@/lib/goal-projection'

/**
 * Builds a complete Goal API fixture with dates relative to a supplied test day.
 *
 * @param {Partial<Goal>} overrides - Fields to replace after applying the default fixture values.
 * @param {string} today - Test reference date in YYYY-MM-DD format.
 * @returns {Goal} A deterministic goal fixture; overrides are applied without recalculation.
 */
export function goal(overrides: Partial<Goal>, today: string): Goal {
  return {
    id: 'goal',
    user_id: 'test-user',
    name: 'Goal',
    target_amount: 6000,
    current_amount: 2700,
    currency: 'USD',
    target_amount_primary: null,
    current_amount_primary: null,
    target_date: firstOfMonthAfter(today, 7),
    tracking_type: 'manual',
    account_id: null,
    asset_id: null,
    asset_group_id: null,
    status: 'active',
    icon: 'piggy-bank',
    color: '#6366F1',
    position: 0,
    metadata_json: null,
    created_at: firstOfMonthAfter(today, -8) + 'T00:00:00Z',
    updated_at: today + 'T12:00:00Z',
    percentage: 45,
    monthly_contribution: null,
    on_track: null,
    account_name: null,
    asset_name: null,
    asset_group_name: null,
    ...overrides,
  }
}
