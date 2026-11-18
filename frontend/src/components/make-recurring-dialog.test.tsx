import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MakeRecurringDialog } from '@/components/make-recurring-dialog'
import { admin, currencies, recurring as recurringApi } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Category, Transaction } from '@/types'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  admin: { numberFormat: vi.fn() },
  currencies: { list: vi.fn() },
  recurring: { create: vi.fn() },
}))

const account = { id: 'acct-1', name: 'Checking' }

const category: Category = {
  id: 'cat-1',
  user_id: 'user-1',
  group_id: null,
  name: 'School',
  icon: 'graduation-cap',
  color: '#00aa00',
  is_system: false,
  is_hidden: false,
  treat_as_transfer: false,
  is_ignored: false,
}

const books: Category = { ...category, id: 'cat-2', name: 'Books' }

/** The boleto the user already paid and now bills every month. */
function schoolFee(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: 'tx-1',
    user_id: 'user-1',
    account_id: account.id,
    category_id: category.id,
    category,
    external_id: null,
    description: 'School fee',
    original_description: null,
    amount: -350,
    currency: 'BRL',
    date: '2026-03-15',
    type: 'debit',
    source: 'manual',
    status: 'posted',
    payee: 'Colégio Aurora',
    payee_id: null,
    payee_name: 'Colégio Aurora',
    notes: null,
    transfer_pair_id: null,
    amount_primary: null,
    fx_rate_used: null,
    fx_fallback: false,
    installment_number: null,
    total_installments: null,
    installment_total_amount: null,
    installment_purchase_date: null,
    installment_series_id: null,
    bill_id: null,
    effective_bill_date: null,
    reporting_date_override: null,
    splits: [],
    is_ignored: false,
    ...overrides,
  }
}

function renderDialog(
  transaction: Transaction,
  onClose = vi.fn(),
  categories: Category[] = [category],
) {
  const result = renderWithProviders(
    <MakeRecurringDialog
      transaction={transaction}
      accounts={[account]}
      categories={categories}
      categoryGroups={[]}
      onClose={onClose}
    />,
  )
  return { ...result, onClose }
}

beforeEach(() => {
  vi.mocked(admin.numberFormat).mockReset()
  // Dot decimal, as the USD preference alone already implies: the format the
  // dialog mounts with before the admin setting lands.
  vi.mocked(admin.numberFormat).mockResolvedValue({ format: 'comma_dot' })
  vi.mocked(currencies.list).mockResolvedValue([])
  vi.mocked(recurringApi.create).mockReset()
  vi.mocked(recurringApi.create).mockResolvedValue({} as never)
})

describe('MakeRecurringDialog', () => {
  it('opens the bill form filled from the transaction', () => {
    renderDialog(schoolFee())

    expect(screen.getByText(t('recurring.makeFromTransaction'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('recurring.description'))).toHaveValue('School fee')
    // The amount is shown unsigned; the direction rides in `type`.
    expect(screen.getByLabelText(t('recurring.amount'))).toHaveValue('350')
    // The day of the transaction is the bill's day of the month.
    expect(screen.getByDisplayValue('15')).toBeInTheDocument()
    expect(
      screen.getByRole('option', { name: 'Checking' }).closest('select'),
    ).toHaveValue(account.id)
    expect(screen.getByText(category.name)).toBeInTheDocument()
    // The selector shows the currency Save submits, even when the list is
    // empty and the user's own currency is USD.
    expect(screen.getByRole('option', { name: 'BRL' }).closest('select')).toHaveValue('BRL')
  })

  it('saves the bill with the transaction data and the monthly default', async () => {
    const { user, onClose } = renderDialog(schoolFee())

    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(recurringApi.create).toHaveBeenCalledTimes(1)
    expect(vi.mocked(recurringApi.create).mock.calls[0][0]).toEqual(
      expect.objectContaining({
        description: 'School fee',
        amount: 350,
        currency: 'BRL',
        type: 'debit',
        frequency: 'monthly',
        day_of_month: 15,
        start_date: '2026-03-15',
        category_id: category.id,
        account_id: account.id,
        // The transaction already covers the first occurrence.
        skip_first: true,
      }),
    )
    expect(onClose).toHaveBeenCalled()
  })

  it('keeps the direction of an income transaction and its unsigned amount', async () => {
    const { user } = renderDialog(
      schoolFee({ id: 'tx-2', type: 'credit', amount: 1200, description: 'Salary' }),
    )

    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(vi.mocked(recurringApi.create).mock.calls[0][0]).toEqual(
      expect.objectContaining({ type: 'credit', amount: 1200, description: 'Salary' }),
    )
  })

  it('carries a split across categories into the bill', async () => {
    const { user } = renderDialog(
      schoolFee({
        id: 'tx-3',
        category_id: null,
        has_category_lines: true,
        category_lines: [
          {
            id: 'line-1', category_id: category.id, amount: 200,
            share_type: 'exact', share_pct: null, position: 0, notes: null,
          },
          {
            id: 'line-2', category_id: books.id, amount: 150,
            share_type: 'exact', share_pct: null, position: 1, notes: null,
          },
        ],
      }),
      vi.fn(),
      [category, books],
    )

    expect(screen.getByText(t('categoryLines.badge', { count: 2 }))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(vi.mocked(recurringApi.create).mock.calls[0][0]).toEqual(
      expect.objectContaining({
        category_id: null,
        category_lines: {
          share_type: 'exact',
          lines: [
            { category_id: category.id, amount: 200 },
            { category_id: books.id, amount: 150 },
          ],
        },
      }),
    )
  })

  it('keeps a split saveable when the workspace format lands after the form opened', async () => {
    // A workspace on the European format: the dialog opens with the dot-decimal
    // preference format already written into the split rows, and the admin
    // setting arrives while it is open. Reading those rows under the new
    // separators turns "200.5" into 2005, the parts stop adding up to the
    // total, and Save stays disabled with nothing for the user to fix.
    let resolveFormat: (value: { format: 'dot_comma' }) => void = () => {}
    vi.mocked(admin.numberFormat).mockReturnValue(
      new Promise((resolve) => { resolveFormat = resolve }),
    )

    const { user, queryClient } = renderDialog(
      schoolFee({
        id: 'tx-4',
        category_id: null,
        has_category_lines: true,
        category_lines: [
          {
            id: 'line-1', category_id: category.id, amount: 200.5,
            share_type: 'exact', share_pct: null, position: 0, notes: null,
          },
          {
            id: 'line-2', category_id: books.id, amount: 149.5,
            share_type: 'exact', share_pct: null, position: 1, notes: null,
          },
        ],
      }),
      vi.fn(),
      [category, books],
    )

    resolveFormat({ format: 'dot_comma' })
    await waitFor(() => {
      expect(queryClient.getQueryState(['admin', 'number-format'])?.status).toBe('success')
    })

    const save = screen.getByRole('button', { name: t('common.save') })
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)

    expect(vi.mocked(recurringApi.create).mock.calls[0][0]).toEqual(
      expect.objectContaining({
        amount: 350,
        category_lines: {
          share_type: 'exact',
          lines: [
            { category_id: category.id, amount: 200.5 },
            { category_id: books.id, amount: 149.5 },
          ],
        },
      }),
    )
  })
})
