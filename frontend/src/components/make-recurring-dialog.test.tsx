import { act, fireEvent, screen, waitFor } from '@testing-library/react'
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

// A plain date field, so a test can move the bill's start date.
vi.mock('@/components/ui/date-picker-input', () => ({
  DatePickerInput: ({
    value,
    onChange,
  }: {
    value: string
    onChange: (value: string) => void
  }) => (
    <input
      type="date"
      value={value}
      aria-label="date"
      onChange={(event) => onChange(event.target.value)}
    />
  ),
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

/** The form writes its draft amounts in the workspace number format, so it
 * mounts only after that setting has settled. */
async function openDialog(
  transaction: Transaction,
  onClose = vi.fn(),
  categories: Category[] = [category],
) {
  const result = renderDialog(transaction, onClose, categories)
  await screen.findByLabelText(t('recurring.description'))
  return result
}

beforeEach(() => {
  vi.mocked(admin.numberFormat).mockReset()
  vi.mocked(admin.numberFormat).mockResolvedValue({ format: 'comma_dot' })
  vi.mocked(currencies.list).mockResolvedValue([])
  vi.mocked(recurringApi.create).mockReset()
  vi.mocked(recurringApi.create).mockResolvedValue({} as never)
})

describe('MakeRecurringDialog', () => {
  it('opens the bill form filled from the transaction', async () => {
    await openDialog(schoolFee())

    expect(screen.getByText(t('recurring.makeFromTransaction'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('recurring.description'))).toHaveValue('School fee')
    // The amount is shown unsigned; the direction rides in `type`. Money keeps
    // its two decimals, so the field reads "350.00", never "350".
    expect(screen.getByLabelText(t('recurring.amount'))).toHaveValue('350.00')
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
    const { user, onClose } = await openDialog(schoolFee())

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
        // The bill is made from this transaction, which the API links back so
        // the row stops offering "make recurring".
        source_transaction_id: 'tx-1',
        // The transaction already covers the first occurrence.
        skip_first: true,
      }),
    )
    expect(onClose).toHaveBeenCalled()
  })

  it('keeps the direction of an income transaction and its unsigned amount', async () => {
    const { user } = await openDialog(
      schoolFee({ id: 'tx-2', type: 'credit', amount: 1200, description: 'Salary' }),
    )

    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(vi.mocked(recurringApi.create).mock.calls[0][0]).toEqual(
      expect.objectContaining({ type: 'credit', amount: 1200, description: 'Salary' }),
    )
  })

  it('carries a split across categories into the bill', async () => {
    const { user } = await openDialog(
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

  it('waits for the workspace format before writing the draft amounts', async () => {
    // A workspace on the European format: the admin setting is still in flight
    // when the dialog opens. Writing the split rows under the interim format
    // would leave "200.5" in them, and the format landing under them would
    // read it as 2005 — the parts stop adding up and Save stays disabled with
    // nothing for the user to fix. The form waits for the format instead.
    let resolveFormat: (value: { format: 'dot_comma' }) => void = () => {}
    vi.mocked(admin.numberFormat).mockReturnValue(
      new Promise((resolve) => { resolveFormat = resolve }),
    )

    const { user } = renderDialog(
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

    // Nothing is written under the interim format.
    expect(screen.queryByLabelText(t('recurring.amount'))).not.toBeInTheDocument()
    expect(screen.getByText(t('common.loading'))).toBeInTheDocument()

    resolveFormat({ format: 'dot_comma' })

    const save = await screen.findByRole('button', { name: t('common.save') })
    // The rows were written with the settled format, so they keep their cents.
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

  it('opens the form on the fallback format when the setting cannot load', async () => {
    // The gate treats a failed setting as settled, so the form opens. But an
    // errored query with no data is fetched again whenever a new observer
    // mounts, and that fetch puts the query back to pending: the fields mount,
    // go back to loading, mount again, forever. The user never gets a form.
    vi.mocked(admin.numberFormat).mockRejectedValue(new Error('offline'))

    await openDialog(schoolFee({ id: 'tx-7' }))

    expect(screen.getByLabelText(t('recurring.amount'))).toHaveValue('350.00')
    // A setting that already failed is not asked for again on every mount.
    expect(vi.mocked(admin.numberFormat)).toHaveBeenCalledTimes(1)
  })

  it('skips the first occurrence only while the bill starts on the transaction date', async () => {
    const { user } = await openDialog(schoolFee({ id: 'tx-5' }))

    // Moving the start date makes the first occurrence one the source
    // transaction no longer covers, so generation has to create it.
    fireEvent.change(screen.getAllByLabelText('date')[0], { target: { value: '2026-04-15' } })
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(vi.mocked(recurringApi.create).mock.calls[0][0]).toEqual(
      expect.objectContaining({ start_date: '2026-04-15', skip_first: false }),
    )
  })

  it('leaves a reopened dialog alone when an earlier save lands', async () => {
    let resolveCreate: (value: unknown) => void = () => {}
    vi.mocked(recurringApi.create).mockReturnValue(
      new Promise((resolve) => { resolveCreate = resolve }) as never,
    )

    const onClose = vi.fn()
    const tx = schoolFee({ id: 'tx-6' })
    const { user, rerender } = renderDialog(tx, onClose)
    await screen.findByLabelText(t('recurring.description'))

    await user.click(screen.getByRole('button', { name: t('common.save') }))
    expect(recurringApi.create).toHaveBeenCalledTimes(1)

    // The user closed that form and opened the same transaction again; the
    // first save now belongs to a session that is no longer on screen.
    const dialog = (transaction: Transaction | null) => (
      <MakeRecurringDialog
        transaction={transaction}
        accounts={[account]}
        categories={[category]}
        categoryGroups={[]}
        onClose={onClose}
      />
    )
    rerender(dialog(null))
    rerender(dialog(tx))
    await screen.findByLabelText(t('recurring.description'))

    await act(async () => { resolveCreate({}) })

    expect(onClose).not.toHaveBeenCalled()
  })
})
