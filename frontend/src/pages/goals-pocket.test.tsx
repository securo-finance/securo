import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import GoalsPage from '@/pages/goals'
import { renderWithProviders, t } from '@/test/utils'
import type { Goal } from '@/types'

const api = vi.hoisted(() => ({
  goals: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    adjust: vi.fn(),
    activity: vi.fn(),
  },
  accounts: { list: vi.fn() },
  assets: { list: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-timezone', () => ({
  useEffectiveTimezone: () => 'Europe/Berlin',
}))

afterEach(() => vi.useRealTimers())

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'EUR' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

const account = {
  id: 'account',
  name: 'Savings',
  display_name: null,
  type: 'savings',
  currency: 'EUR',
  current_balance: 500,
  balance: 500,
  is_closed: false,
}

const pocket = {
  id: 'pocket',
  user_id: 'user',
  name: 'Monitor',
  target_amount: 900,
  current_amount: 600,
  currency: 'EUR',
  target_amount_primary: 900,
  current_amount_primary: 600,
  target_date: null,
  tracking_type: 'pocket',
  account_id: account.id,
  asset_id: null,
  asset_group_id: null,
  status: 'active',
  icon: null,
  color: null,
  position: 0,
  metadata_json: null,
  created_at: '2026-09-19T00:00:00Z',
  updated_at: '2026-09-19T00:00:00Z',
  percentage: 66.7,
  monthly_contribution: null,
  on_track: null,
  account_name: 'Savings',
  asset_name: null,
  asset_group_name: null,
  account_balance: 500,
  account_reserved_total: 600,
  account_available: -100,
  is_underfunded: true,
} satisfies Goal

describe('GoalsPage pockets', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.goals.list.mockResolvedValue([])
    api.goals.create.mockResolvedValue(pocket)
    api.accounts.list.mockResolvedValue([account])
    api.assets.list.mockResolvedValue([])
    api.assetGroups.list.mockResolvedValue([])
    api.currencies.list.mockResolvedValue([{ code: 'EUR', name: 'Euro', flag: '€' }])
  })

  it('shows the account underfunding state without changing the reserved amount', async () => {
    api.goals.list.mockResolvedValue([pocket])
    renderWithProviders(<GoalsPage />, { route: '/goals' })

    expect(await screen.findByText('Monitor')).toBeInTheDocument()
    expect(screen.getByText(t('goals.underfunded'))).toBeInTheDocument()
    expect(screen.getByText(/Free on account:.*-€100\.00/)).toBeInTheDocument()
  })

  it('counts remaining calendar days in the workspace timezone across a DST change', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-24T22:30:00Z'))
    api.goals.list.mockResolvedValue([{ ...pocket, target_date: '2026-10-26' }])
    renderWithProviders(<GoalsPage />, { route: '/goals' })

    expect(await screen.findByText(t('goals.daysRemaining', { count: 1 }))).toBeInTheDocument()
  })

  it('creates a pocket with its account currency and a custom opening allocation', async () => {
    const { user } = renderWithProviders(<GoalsPage />, { route: '/goals' })
    await user.click(await screen.findByRole('button', { name: t('goals.add') }))

    const trackingSelect = screen.getAllByRole('combobox').find(select =>
      within(select).queryByRole('option', { name: t('goals.trackingPocket') }),
    )
    expect(trackingSelect).toBeDefined()
    await user.selectOptions(trackingSelect!, 'pocket')

    const accountSelect = screen.getAllByRole('combobox').find(select =>
      within(select).queryByRole('option', { name: 'Savings (EUR)' }),
    )
    expect(accountSelect).toBeDefined()
    await user.selectOptions(accountSelect!, account.id)

    const nameInput = document.querySelector<HTMLInputElement>('input[name="name"]')
    const targetInput = document.querySelector<HTMLInputElement>('input[name="target_amount"]')
    expect(nameInput).not.toBeNull()
    expect(targetInput).not.toBeNull()
    await user.type(nameInput!, 'Monitor')
    await user.type(targetInput!, '900')
    await user.click(screen.getByRole('button', { name: t('goals.startingCustom') }))

    const openingInput = document.querySelector<HTMLInputElement>('input[type="number"]:not([name])')
    expect(openingInput).not.toBeNull()
    await user.type(openingInput!, '125')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.goals.create).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Monitor',
      target_amount: 900,
      currency: 'EUR',
      tracking_type: 'pocket',
      account_id: account.id,
      initial_allocation: 125,
    })))
  })
})
