import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
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
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 12, 12))
  vi.clearAllMocks()
  api.reports.netWorth.mockResolvedValue(emptyReport('net_worth'))
  api.reports.incomeExpenses.mockResolvedValue(emptyReport('income_expenses'))
  api.reports.cashFlow.mockResolvedValue(emptyReport('cash_flow'))
})

afterEach(() => vi.useRealTimers())

describe('Reports page — Custom range segment', () => {
  it('loads the Net Worth tab with the 1Y preset by default', async () => {
    renderWithProviders(<ReportsPage />)

    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())
    const [, , , , , startDate, endDate] = api.reports.netWorth.mock.calls[0]
    expect(startDate).toBeUndefined()
    expect(endDate).toBeUndefined()
  })

  it('opening Custom waits for Apply before querying January 1 through today', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))

    expect(api.reports.netWorth).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: t('reports.range1y') })).toHaveClass('bg-primary')
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    const year = 2026
    await waitFor(() => {
      const last = api.reports.netWorth.mock.calls.at(-1)!
      expect(last[5]).toBe(`${year}-01-01`)
      expect(last[6]).toBe('2026-09-12')
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
    ).toHaveTextContent(`${fmt(`${year}-01-01`)} — ${fmt('2026-09-12')}`)
  })

  it('is not offered on the Cash Flow tab', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))

    await waitFor(() => expect(api.reports.cashFlow).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: t('reports.customRange') })).not.toBeInTheDocument()
  })
})


it.each(['Cancel', 'Escape', 'outside'] as const)(
  '6M → Custom → %s leaves the preset and query unchanged', async (dismiss) => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))
    const preset = screen.getByRole('button', { name: t('reports.range6m') })
    await user.click(preset)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(2))
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    if (dismiss === 'Cancel') {
      await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.cancel') }))
    } else if (dismiss === 'Escape') {
      await user.keyboard('{Escape}')
    } else {
      await user.click(document.body)
    }
    expect(preset).toHaveClass('bg-primary')
    expect(api.reports.netWorth).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    expect(api.reports.netWorth).toHaveBeenCalledTimes(2)
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(3))
  },
)

it.each([
  ['reports.netWorth', 'reports.range1y', 12],
  ['reports.incomeExpenses', 'reports.range1y', 12],
  ['reports.moneyMap', 'reports.range3m', 3],
] as const)('Reset then Apply restores the fallback for %s', async (tab, fallback, months) => {
  const { user } = renderWithProviders(<ReportsPage />)
  if (tab !== 'reports.netWorth') {
    await user.click(screen.getByRole('button', { name: t(tab) }))
  }
  const custom = screen.getByRole('button', { name: t('reports.customRange') })
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  const reportApi = tab === 'reports.netWorth' ? api.reports.netWorth : api.reports.incomeExpenses
  await waitFor(() => expect(reportApi.mock.calls.at(-1)?.slice(-2)).toEqual([
    '2026-01-01', '2026-09-12',
  ]))
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  await waitFor(() => {
    expect(screen.getByRole('button', { name: t(fallback) })).toHaveClass('bg-primary')
    expect(reportApi.mock.calls.at(-1)?.[0]).toBe(months)
    expect(reportApi.mock.calls.at(-1)?.slice(-2)).toEqual([undefined, undefined])
  })
  expect(custom).toHaveTextContent(t('reports.customRange'))
})

it('preserves saved custom dates across supported tabs and preset dismissal', async () => {
  const { user } = renderWithProviders(<ReportsPage />)
  const custom = screen.getByRole('button', { name: t('reports.customRange') })
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  for (const tab of ['reports.incomeExpenses', 'reports.moneyMap']) {
    await user.click(screen.getByRole('button', { name: t(tab) }))
    await waitFor(() => expect(api.reports.incomeExpenses.mock.calls.at(-1)?.slice(-2))
      .toEqual(['2026-01-01', '2026-09-12']))
    expect(custom).toHaveClass('bg-primary')
  }
  await user.click(screen.getByRole('button', { name: t('reports.range6m') }))
  await waitFor(() => expect(api.reports.incomeExpenses.mock.calls.at(-1)?.slice(-2))
    .toEqual([undefined, undefined]))
  const count = api.reports.incomeExpenses.mock.calls.length
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.cancel') }))
  expect(api.reports.incomeExpenses).toHaveBeenCalledTimes(count)
  expect(screen.getByRole('button', { name: t('reports.range6m') })).toHaveClass('bg-primary')
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  await waitFor(() => expect(api.reports.incomeExpenses.mock.calls.at(-1)?.slice(-2))
    .toEqual(['2026-01-01', '2026-09-12']))
  await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
  await waitFor(() => expect(api.reports.cashFlow).toHaveBeenCalled())
  expect(screen.queryByRole('button', { name: t('reports.customRange') })).not.toBeInTheDocument()
  expect(api.reports.cashFlow.mock.calls.at(-1)?.[0]).toBe(6)
  await user.click(screen.getByRole('button', { name: t('reports.netWorth') }))
  await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  await waitFor(() => expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2))
    .toEqual(['2026-01-01', '2026-09-12']))
})
