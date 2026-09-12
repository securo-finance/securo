import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import ReportsPage from '@/pages/reports'
import { renderWithProviders, t } from '@/test/utils'
import type { ReportResponse } from '@/types'

const api = vi.hoisted(() => ({
  reports: {
    netWorth: vi.fn(),
    incomeExpenses: vi.fn(),
    cashFlow: vi.fn(),
  },
}))

vi.mock('@/lib/api', () => ({ reports: api.reports }))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

function emptyReport(type: string): ReportResponse {
  return {
    summary: { primary_value: 1000, change_amount: 100, change_percent: 5, breakdowns: [] },
    trend: [{ date: '2026-01-01', value: 1000, breakdowns: {}, change: null }],
    meta: { type, series_keys: [], currency: 'USD', interval: 'monthly' },
    composition: [],
    category_trend: [],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.reports.netWorth.mockResolvedValue(emptyReport('net_worth'))
  api.reports.incomeExpenses.mockResolvedValue(emptyReport('income_expenses'))
  api.reports.cashFlow.mockResolvedValue(emptyReport('cash_flow'))
})

describe('Reports page — Custom range segment', () => {
  it('loads the Net Worth tab with the 1Y preset by default', async () => {
    renderWithProviders(<ReportsPage />)

    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())
    const [, , , , , startDate, endDate] = api.reports.netWorth.mock.calls[0]
    expect(startDate).toBeUndefined()
    expect(endDate).toBeUndefined()
  })

  it('selecting Custom pre-fills the current calendar year and queries that exact range', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))

    const year = new Date().getFullYear()
    await waitFor(() => {
      const last = api.reports.netWorth.mock.calls.at(-1)!
      expect(last[5]).toBe(`${year}-01-01`)
      expect(last[6]).toBe(`${year}-12-31`)
    })

    // The segment itself now displays the picked range instead of "Custom
    // range" — in the same compact, year-less form as the transactions
    // filter bar's applied-range chip.
    const fmt = (iso: string) =>
      new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', {
        day: '2-digit',
        month: 'short',
      })
    expect(
      screen.getByRole('button', { name: t('reports.customRange') }),
    ).toHaveTextContent(`${fmt(`${year}-01-01`)} — ${fmt(`${year}-12-31`)}`)
  })

  it('is not offered on the Cash Flow tab', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))

    await waitFor(() => expect(api.reports.cashFlow).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: t('reports.customRange') })).not.toBeInTheDocument()
  })
})
