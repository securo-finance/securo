import { fireEvent, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TransactionDialog } from '@/components/transaction-dialog'
import { renderWithProviders, t } from '@/test/utils'
import type { Transaction } from '@/types'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ privacyMode: false, MASK: '••••' }),
}))

vi.mock('@/components/transaction-attachments', () => ({
  TransactionAttachments: () => null,
}))

vi.mock('@/components/transaction-splits-section', () => ({
  TransactionSplitsSection: () => null,
}))

vi.mock('@/components/category-select', () => ({
  CategorySelect: () => <div data-testid="category-select" />,
}))

vi.mock('@/components/ui/date-picker-input', () => ({
  DatePickerInput: ({
    value,
    onChange,
    placeholder,
    disabled,
  }: {
    value: string
    onChange: (value: string) => void
    placeholder?: string
    disabled?: boolean
  }) => (
    <input
      type="date"
      value={value}
      aria-label={placeholder ?? 'date'}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

vi.mock('@/lib/api', () => ({
  currencies: {
    list: vi.fn().mockResolvedValue([
      { code: 'BRL', symbol: 'R$', name: 'Brazilian real', flag: '' },
    ]),
  },
  transactions: {},
  settings: {},
  payees: { list: vi.fn().mockResolvedValue([]) },
  rules: { list: vi.fn().mockResolvedValue([]) },
  categories: {},
  categoryGroups: {},
}))

function syncedTransaction(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: 'tx-1',
    user_id: 'user-1',
    account_id: 'checking-1',
    category_id: null,
    category: null,
    external_id: 'provider-1',
    description: 'Resgate - Global Account',
    original_description: null,
    amount: 1000,
    currency: 'BRL',
    date: '2026-10-01',
    type: 'credit',
    source: 'sync',
    status: 'posted',
    payee: null,
    payee_id: null,
    payee_name: null,
    notes: null,
    transfer_pair_id: null,
    amount_primary: 1000,
    fx_rate_used: 1,
    fx_fallback: false,
    attachment_count: 0,
    installment_number: null,
    total_installments: null,
    installment_total_amount: null,
    installment_purchase_date: null,
    installment_series_id: null,
    bill_id: null,
    effective_bill_date: null,
    reporting_date_override: null,
    recurring_transaction_id: null,
    splits: [],
    is_ignored: false,
    exclude_from_pnl: false,
    ...overrides,
  }
}

const accounts = [
  { id: 'checking-1', name: 'Checking', type: 'checking' },
]

function renderDialog(transaction: Transaction, onSave = vi.fn()) {
  return {
    onSave,
    ...renderWithProviders(
      <TransactionDialog
        open
        onClose={vi.fn()}
        transaction={transaction}
        categories={[]}
        categoryGroups={[]}
        accounts={accounts}
        onSave={onSave}
        loading={false}
        error={null}
        isSynced
      />,
    ),
  }
}

describe('TransactionDialog reporting date override', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submits reporting attribution without mutating the bank date', async () => {
    const { onSave, user } = renderDialog(syncedTransaction())

    const bankDate = screen.getByDisplayValue('2026-10-01')
    expect(bankDate).toBeDisabled()

    fireEvent.change(
      screen.getByLabelText(t('transactions.reportingDatePlaceholder')),
      { target: { value: '2026-09-30' } },
    )
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave).toHaveBeenCalledOnce()
    const payload = onSave.mock.calls[0][0]
    expect(payload).toMatchObject({ reporting_date_override: '2026-09-30' })
    expect(payload).not.toHaveProperty('date')
  })

  it('resets reporting attribution to automatic without changing the bank date', async () => {
    const { onSave, user } = renderDialog(
      syncedTransaction({ reporting_date_override: '2026-09-30' }),
    )

    await user.click(
      screen.getByRole('button', { name: t('transactions.clearReportingDate') }),
    )
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave.mock.calls[0][0]).toMatchObject({ reporting_date_override: null })
    expect(onSave.mock.calls[0][0]).not.toHaveProperty('date')
  })
})
