import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/test/utils'
import { SpendingPlanSummary } from './spending-plan-summary'
import type { SpendingPlanTimeline } from '@/types'

const api = vi.hoisted(() => ({
  spendingPlan: { get: vi.fn(), timeline: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

function timeline(overrides: Partial<SpendingPlanTimeline>): SpendingPlanTimeline {
  return {
    currency: 'USD',
    daily_allowance: 50,
    balance_floor: 0,
    horizon_end: '2027-11-12',
    safe_daily_allowance: 61.2,
    average_daily_surplus: 80,
    covered_until: '2027-03-05',
    covered_through_horizon: false,
    today: { allowance: 50, spent: 12.5, remaining: 37.5 },
    opening_balance: 0,
    binding_date: null,
    points: [],
    events: [],
    ...overrides,
  }
}

const plan = { daily_allowance: '50.00', balance_floor: '0.00', account_ids: null, horizon_months: 12 }

describe('SpendingPlanSummary', () => {
  beforeEach(() => {
    api.spendingPlan.get.mockReset()
    api.spendingPlan.timeline.mockReset()
  })

  it('shows what is left today and until when the money lasts', async () => {
    api.spendingPlan.get.mockResolvedValue(plan)
    api.spendingPlan.timeline.mockResolvedValue(timeline({}))

    renderWithProviders(<SpendingPlanSummary />)

    expect(await screen.findByText('You can still spend $37.50 today')).toBeInTheDocument()
    expect(screen.getByText('Covered until Mar 5, 2027')).toBeInTheDocument()
    expect(screen.getByRole('link')).toHaveAttribute('href', '/plan')
  })

  it('is red when the money runs out before the horizon', async () => {
    api.spendingPlan.get.mockResolvedValue(plan)
    api.spendingPlan.timeline.mockResolvedValue(
      timeline({ covered_until: '2026-12-01', covered_through_horizon: false }),
    )

    renderWithProviders(<SpendingPlanSummary />)

    await screen.findByText('Covered until Dec 1, 2026')
    expect(screen.getByRole('link').className).toContain('bg-rose-50')
    expect(screen.getByRole('link').className).not.toContain('bg-emerald-50')
  })

  it('says how much today went over', async () => {
    api.spendingPlan.get.mockResolvedValue(plan)
    api.spendingPlan.timeline.mockResolvedValue(
      timeline({ today: { allowance: 50, spent: 80, remaining: -30 }, covered_until: null }),
    )

    renderWithProviders(<SpendingPlanSummary />)

    expect(await screen.findByText("You are $30.00 over today's amount")).toBeInTheDocument()
    expect(screen.getByText('Below the floor today')).toBeInTheDocument()
  })

  it('renders nothing without a plan', async () => {
    api.spendingPlan.get.mockResolvedValue(null)

    const { container } = renderWithProviders(<SpendingPlanSummary />)

    await vi.waitFor(() => expect(api.spendingPlan.get).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
    expect(api.spendingPlan.timeline).not.toHaveBeenCalled()
  })
})
