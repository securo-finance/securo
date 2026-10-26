import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MobileTransactionRow } from './mobile-transaction-row'
import type { Account, Category, Transaction } from '@/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))

function makeCategory(overrides: Partial<Category> = {}): Category {
  return {
    id: 'cat-1',
    user_id: 'u-1',
    group_id: null,
    name: 'Transfers',
    icon: 'arrow-left-right',
    color: '#3b82f6',
    is_system: true,
    is_hidden: false,
    treat_as_transfer: false,
    is_ignored: false,
    ...overrides,
  }
}

function makeTx(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: 'tx-1',
    user_id: 'u-1',
    account_id: 'acc-1',
    category_id: null,
    category: null,
    external_id: null,
    description: 'STORE PURCHASE',
    original_description: null,
    amount: -42,
    currency: 'USD',
    date: '2026-10-20',
    type: 'debit',
    source: 'manual',
    status: 'posted',
    payee: 'Store A',
    payee_id: null,
    payee_name: null,
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
    splits: [],
    is_ignored: false,
    ...overrides,
  }
}

function makeAccount(): Account {
  return {
    id: 'acc-1',
    user_id: 'u-1',
    connection_id: null,
    external_id: null,
    name: 'Checking A',
    display_name: null,
    masked_number: null,
    institution_name: null,
    institution_logo_url: null,
    type: 'checking',
    balance: 100,
    current_balance: 100,
    previous_balance: null,
    balance_primary: 100,
    currency: 'USD',
    credit_limit: null,
    available_credit: null,
    statement_close_day: null,
    payment_due_day: null,
    next_close_date: null,
    next_due_date: null,
    minimum_payment: null,
    card_brand: null,
    card_level: null,
    shared_balance_group: null,
    is_closed: false,
    closed_at: null,
  }
}

function renderRow(tx: Transaction) {
  return render(
    <MobileTransactionRow
      tx={tx}
      account={makeAccount()}
      groupName={undefined}
      selected={false}
      selectable={false}
      canWrite={true}
      highlighted={false}
      locale="en"
      userCurrency="USD"
      onSelect={() => {}}
      onClick={() => {}}
    />,
  )
}

describe('MobileTransactionRow transfer indicator', () => {
  it('shows the transfer icon for a treat_as_transfer category without a pair (#993)', () => {
    const category = makeCategory({ treat_as_transfer: true })
    renderRow(makeTx({ category_id: category.id, category }))
    expect(screen.getByLabelText('transactions.transfer')).toBeInTheDocument()
  })

  it('shows the transfer icon for a linked transfer pair', () => {
    renderRow(makeTx({ transfer_pair_id: 'pair-1' }))
    expect(screen.getByLabelText('transactions.transfer')).toBeInTheDocument()
  })

  it('hides the transfer icon for a normal row', () => {
    const category = makeCategory({ treat_as_transfer: false })
    renderRow(makeTx({ category_id: category.id, category }))
    expect(screen.queryByLabelText('transactions.transfer')).not.toBeInTheDocument()
  })
})
