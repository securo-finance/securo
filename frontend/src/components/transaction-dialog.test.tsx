import { screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { TransactionDialog } from '@/components/transaction-dialog'
import { currencies, groups, payees } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { RecurringTransaction } from '@/types'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-US',
  useDisplayLocale: () => 'en-US',
}))

afterEach(() => vi.restoreAllMocks())

it.each(['America/Los_Angeles', 'Pacific/Kiritimati'])(
  'keeps a recurring calendar date unchanged in %s',
  async (timezone) => {
    const originalTimezone = process.env.TZ
    const recurringMatch: RecurringTransaction = {
      id: 'recurring-rent',
      user_id: 'synthetic-user',
      account_id: null,
      category_id: null,
      description: 'Rent',
      amount: 100,
      currency: 'USD',
      type: 'debit',
      frequency: 'monthly',
      weekend_adjustment: 'none',
      day_of_month: 8,
      start_date: '2026-01-08',
      end_date: null,
      is_active: true,
      auto_generate: false,
      next_occurrence: '2026-03-08',
      amount_primary: null,
      fx_rate_used: null,
    }
    vi.spyOn(currencies, 'list').mockResolvedValue([])
    vi.spyOn(payees, 'list').mockResolvedValue([])
    vi.spyOn(groups, 'list').mockResolvedValue([])

    try {
      process.env.TZ = timezone
      renderWithProviders(
        <TransactionDialog
          open
          onClose={vi.fn()}
          transaction={null}
          categories={[]}
          categoryGroups={[]}
          accounts={[]}
          recurringMatch={recurringMatch}
          onSave={vi.fn()}
          loading={false}
          error={null}
        />,
      )

      expect(await screen.findByText(t('transactions.recurringInfo', {
        frequency: t('recurring.monthly'),
        next: '3/8/2026',
      }))).toBeInTheDocument()
    } finally {
      if (originalTimezone === undefined) delete process.env.TZ
      else process.env.TZ = originalTimezone
    }
  },
)
