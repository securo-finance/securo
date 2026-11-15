import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithProviders } from '@/test/utils'
import SpendingPlanPage from './spending-plan'
import type { SpendingPlanTimeline } from '@/types'

const api = vi.hoisted(() => ({
  spendingPlan: {
    get: vi.fn(),
    save: vi.fn(),
    remove: vi.fn(),
    timeline: vi.fn(),
    preview: vi.fn(),
    simulate: vi.fn(),
  },
  accounts: { list: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

function timeline(overrides: Partial<SpendingPlanTimeline> = {}): SpendingPlanTimeline {
  return {
    currency: 'USD',
    daily_allowance: 30,
    balance_floor: 200,
    horizon_end: '2027-11-12',
    safe_daily_allowance: 28.11,
    average_daily_surplus: 275.5,
    covered_until: '2026-12-02',
    covered_through_horizon: false,
    today: { allowance: 30, spent: 10, remaining: 20 },
    points: [
      { date: '2026-11-12', balance: 1000 },
      { date: '2026-12-31', balance: 2500 },
    ],
    ...overrides,
  }
}

describe('SpendingPlanPage', () => {
  beforeEach(() => {
    Object.values(api.spendingPlan).forEach((fn) => fn.mockReset())
    api.accounts.list.mockResolvedValue([
      { id: 'checking', name: 'Checking', display_name: null, type: 'checking', is_closed: false },
      { id: 'car', name: 'Car loan', display_name: null, type: 'loan', is_closed: false },
      { id: 'card', name: 'Visa', display_name: null, type: 'credit_card', is_closed: false },
    ])
    api.spendingPlan.timeline.mockResolvedValue(timeline())
    api.spendingPlan.preview.mockResolvedValue(timeline())
  })

  it('shows today, the safe amount and the coverage of a saved plan', async () => {
    api.spendingPlan.get.mockResolvedValue({
      daily_allowance: '30.00',
      balance_floor: '200.00',
      account_ids: null,
      horizon_months: 12,
    })

    renderWithProviders(<SpendingPlanPage />)

    expect(await screen.findByText('$20.00')).toBeInTheDocument()
    expect(screen.getByText('$10.00 spent of $30.00')).toBeInTheDocument()
    expect(screen.getByText('$28.11')).toBeInTheDocument()
    expect(screen.getByText('Dec 2, 2026')).toBeInTheDocument()
    expect(screen.getByText('$275.50')).toBeInTheDocument()
  })

  it('saves every field typed in the dialog, not just the last one', async () => {
    api.spendingPlan.get.mockResolvedValue(null)
    api.spendingPlan.save.mockResolvedValue({
      daily_allowance: '45.00',
      balance_floor: '300.00',
      account_ids: null,
      horizon_months: 18,
    })

    const { user } = renderWithProviders(<SpendingPlanPage />)
    await user.click(await screen.findByRole('button', { name: 'Create plan' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Daily amount'), '45')
    await user.type(within(dialog).getByLabelText('Balance floor'), '300')
    await user.selectOptions(within(dialog).getByLabelText('Plan ahead for'), '18')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(api.spendingPlan.save).toHaveBeenCalledWith({
      daily_allowance: 45,
      balance_floor: 300,
      account_ids: null,
      horizon_months: 18,
    })
  })

  it('previews the floor and horizon being edited, not the saved ones', async () => {
    api.spendingPlan.get.mockResolvedValue(null)

    const { user } = renderWithProviders(<SpendingPlanPage />)
    await user.click(await screen.findByRole('button', { name: 'Create plan' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Balance floor'), '300')
    await user.selectOptions(within(dialog).getByLabelText('Plan ahead for'), '18')

    await waitFor(() =>
      expect(api.spendingPlan.preview).toHaveBeenLastCalledWith(
        expect.objectContaining({ balance_floor: 300, horizon_months: 18 }),
      ),
    )
  })

  it('leaves credit cards out of the account checklist because they always count', async () => {
    api.spendingPlan.get.mockResolvedValue(null)

    const { user } = renderWithProviders(<SpendingPlanPage />)
    await user.click(await screen.findByRole('button', { name: 'Create plan' }))
    const dialog = await screen.findByRole('dialog')

    expect(await within(dialog).findByText('Checking')).toBeInTheDocument()
    expect(within(dialog).queryByText('Visa')).not.toBeInTheDocument()
  })

  it('simulates a purchase and compares it with the plan', async () => {
    api.spendingPlan.get.mockResolvedValue({
      daily_allowance: '30.00',
      balance_floor: '200.00',
      account_ids: null,
      horizon_months: 12,
    })
    api.spendingPlan.simulate.mockResolvedValue(
      timeline({
        safe_daily_allowance: 5.39,
        covered_until: '2026-11-19',
        points: [
          { date: '2026-11-12', balance: 500 },
          { date: '2026-12-31', balance: 1500 },
        ],
      }),
    )

    const { user } = renderWithProviders(<SpendingPlanPage />)
    await user.type(await screen.findByLabelText('Total amount'), '6000')
    await user.clear(screen.getByLabelText('Installments'))
    await user.type(screen.getByLabelText('Installments'), '12')
    await user.click(screen.getByRole('button', { name: 'Simulate' }))

    expect(api.spendingPlan.simulate).toHaveBeenCalledWith([
      { description: undefined, amount: 6000, installments: 12, first_date: '2026-11-12' },
    ])
    const table = await screen.findByRole('table')
    expect(within(table).getByText('$5.39')).toBeInTheDocument()
    expect(within(table).getByText('Nov 19, 2026')).toBeInTheDocument()
  })
})
