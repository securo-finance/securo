import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RecurringForm } from '@/pages/recurring'
import { currencies } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { RecurringTransaction } from '@/types'

// Space grouping, comma decimal: the format from issue #1072.
vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-GB',
  useDisplayLocale: () => 'fr-FR',
  useDisplayLocaleSettled: () => true,
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'EUR' } } }),
}))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  currencies: { list: vi.fn() },
}))

const account = { id: 'acct-1', name: 'Checking' }

function recurring(amount: number): RecurringTransaction {
  return {
    id: 'rec-1',
    user_id: 'user-1',
    account_id: account.id,
    category_id: null,
    description: 'Rent',
    amount,
    currency: 'EUR',
    type: 'debit',
    frequency: 'monthly',
    weekend_adjustment: 'none',
    day_of_month: 8,
    start_date: '2026-01-08',
    end_date: null,
    is_active: true,
    auto_generate: true,
    next_occurrence: '2026-12-08',
    amount_primary: null,
    fx_rate_used: null,
  }
}

function renderForm(seed: RecurringTransaction | null, onSave = vi.fn()) {
  const result = renderWithProviders(
    <RecurringForm
      recurring={seed}
      categories={[]}
      categoryGroups={[]}
      currentCategory={undefined}
      accounts={[account]}
      onSave={onSave}
      onCancel={vi.fn()}
      loading={false}
    />,
  )
  return { ...result, onSave }
}

function amountField() {
  return screen.getByLabelText(t('recurring.amount'))
}

function accountField() {
  return screen.getByRole('option', { name: account.name }).closest('select') as HTMLSelectElement
}

beforeEach(() => {
  vi.mocked(currencies.list).mockResolvedValue([])
})

describe('RecurringForm amount field', () => {
  it('seeds an existing amount in the workspace format', () => {
    renderForm(recurring(1234.5))

    expect(amountField()).toHaveValue('1234,5')
  })

  it('reads a comma decimal when creating', async () => {
    const { user, onSave } = renderForm(null)

    await user.type(screen.getByLabelText(t('recurring.description')), 'Rent')
    await user.type(amountField(), '1234,56')
    await user.selectOptions(accountField(), account.id)
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave.mock.calls[0][0].amount).toBe(1234.56)
  })

  it('keeps the cents when the amount has them', async () => {
    const { user, onSave } = renderForm(null)

    await user.type(screen.getByLabelText(t('recurring.description')), 'Rent')
    await user.type(amountField(), '50,25')
    await user.selectOptions(accountField(), account.id)
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave.mock.calls[0][0].amount).toBe(50.25)
  })

  it('blocks the save and explains what is wrong when the text is not an amount', async () => {
    const { user, onSave } = renderForm(null)

    await user.type(screen.getByLabelText(t('recurring.description')), 'Rent')
    await user.type(amountField(), '50.25')
    await user.selectOptions(accountField(), account.id)

    expect(screen.getByRole('alert')).toHaveTextContent(t('common.invalidAmount', { example: '1234,56' }))
    expect(amountField()).toHaveAttribute('aria-invalid', 'true')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave).not.toHaveBeenCalled()
  })
})
