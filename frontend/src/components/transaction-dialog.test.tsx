import { screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { TransactionDialog } from '@/components/transaction-dialog'
import { assets, currencies, groups, mortgagePayments, payees, transactions } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, RecurringTransaction, Transaction } from '@/types'

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
      business_day: null,
      count_saturdays: false,
      holiday_calendar: null,
      grace_days: 0,
      match_keywords: null,
      amount_tolerance_percent: 0,
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

const accountFixtures = [
  { id: 'acc-z', name: 'Zeta Bank', type: 'checking', currency: 'USD' },
  { id: 'acc-a', name: 'Alpha Card', type: 'checking', currency: 'USD' },
] as Account[]

function renderNewTransaction(defaultAccountId?: string) {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={null}
      categories={[]}
      categoryGroups={[]}
      accounts={accountFixtures}
      defaultAccountId={defaultAccountId}
      onSave={vi.fn()}
      loading={false}
      error={null}
    />,
  )
  return screen.getByRole('option', { name: 'Zeta Bank' }).closest('select') as HTMLSelectElement
}

it('starts a new transaction with no account selected, so one must be chosen', () => {
  const accountSelect = renderNewTransaction()

  expect(accountSelect).toHaveValue('')
  // The placeholder shows in the closed field but is hidden from the open list.
  const placeholder = accountSelect.selectedOptions[0]
  expect(placeholder).toHaveTextContent(t('transactions.createCounterpartSelectAccount'))
  expect(placeholder).toBeDisabled()
  expect(placeholder).toHaveAttribute('hidden')
  expect(accountSelect.validity.valueMissing).toBe(true)
})

it('keeps preselecting the account the form was opened from', () => {
  const accountSelect = renderNewTransaction('acc-z')

  expect(accountSelect).toHaveValue('acc-z')
  expect(screen.queryByText(t('transactions.createCounterpartSelectAccount'))).not.toBeInTheDocument()
})

it('forgets a save variant the browser blocked, so a later plain Save just saves', async () => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  const onSave = vi.fn()
  const { user } = renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={null}
      categories={[]}
      categoryGroups={[]}
      accounts={accountFixtures}
      onSave={onSave}
      loading={false}
      error={null}
    />,
  )

  // "Save and new" with no account: the browser blocks the submit.
  await user.click(screen.getByRole('button', { name: t('transactions.moreSaveOptions') }))
  await user.click(await screen.findByRole('menuitem', { name: t('transactions.saveAndNew') }))
  expect(onSave).not.toHaveBeenCalled()

  // Complete the form and use the plain Save button.
  const accountSelect = screen.getByRole('option', { name: 'Zeta Bank' }).closest('select') as HTMLSelectElement
  const form = accountSelect.closest('form') as HTMLFormElement
  const [description, amount] = Array.from(form.querySelectorAll<HTMLInputElement>('input[required]'))
  await user.type(description, 'Groceries')
  await user.type(amount, '12')
  await user.selectOptions(accountSelect, 'acc-z')
  await user.click(screen.getByRole('button', { name: t('common.save') }))

  expect(onSave).toHaveBeenCalledTimes(1)
  expect(onSave.mock.calls[0][4]).toBe('save')
})

function transferLegFixture(pairId: string, over: Partial<Transaction> = {}): Transaction {
  return {
    id: 'tx-1160-leg',
    user_id: 'synthetic-user',
    account_id: 'acc-a',
    category_id: null,
    category: null,
    external_id: null,
    description: 'R1160 transfer repro',
    original_description: null,
    amount: 250,
    currency: 'USD',
    date: '2026-10-15',
    type: 'debit',
    source: 'transfer',
    status: 'posted',
    payee: null,
    payee_id: null,
    payee_name: null,
    notes: null,
    transfer_pair_id: pairId,
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
    ...over,
  }
}

function renderTransferDialog(onUnlinkTransfer: (pairId: string) => void) {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  vi.spyOn(transactions, 'transferPair').mockResolvedValue(
    transferLegFixture('pair-1160', {
      id: 'tx-1160-counterpart',
      type: 'credit',
      account_id: 'acc-z',
    }),
  )
  return renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={transferLegFixture('pair-1160')}
      categories={[]}
      categoryGroups={[]}
      accounts={accountFixtures}
      onUnlinkTransfer={onUnlinkTransfer}
      onSave={vi.fn()}
      loading={false}
      error={null}
    />,
  )
}

it('asks for confirmation before unlinking a transfer pair', async () => {
  const onUnlinkTransfer = vi.fn()
  const { user } = renderTransferDialog(onUnlinkTransfer)

  await user.click(await screen.findByRole('button', { name: t('transactions.unlinkTransfer') }))

  expect(await screen.findByText(t('transactions.unlinkTransferConfirm'))).toBeInTheDocument()
  expect(onUnlinkTransfer).not.toHaveBeenCalled()
})

it('keeps the transfer linked when the unlink confirmation is cancelled', async () => {
  const onUnlinkTransfer = vi.fn()
  const { user } = renderTransferDialog(onUnlinkTransfer)

  await user.click(await screen.findByRole('button', { name: t('transactions.unlinkTransfer') }))
  const confirmText = await screen.findByText(t('transactions.unlinkTransferConfirm'))
  const confirmDialog = confirmText.closest('[role="dialog"]') as HTMLElement
  await user.click(within(confirmDialog).getByRole('button', { name: t('common.cancel') }))

  expect(screen.queryByText(t('transactions.unlinkTransferConfirm'))).not.toBeInTheDocument()
  expect(onUnlinkTransfer).not.toHaveBeenCalled()
})

it('unlinks the pair only after the unlink confirmation is accepted', async () => {
  const onUnlinkTransfer = vi.fn()
  const { user } = renderTransferDialog(onUnlinkTransfer)

  await user.click(await screen.findByRole('button', { name: t('transactions.unlinkTransfer') }))
  const confirmText = await screen.findByText(t('transactions.unlinkTransferConfirm'))
  const confirmDialog = confirmText.closest('[role="dialog"]') as HTMLElement
  await user.click(within(confirmDialog).getByRole('button', { name: t('transactions.unlinkTransfer') }))

  expect(onUnlinkTransfer).toHaveBeenCalledTimes(1)
  expect(onUnlinkTransfer).toHaveBeenCalledWith('pair-1160')
})

it.each([
  ['ignored transaction', { is_ignored: true }],
  ['transfer', { transfer_pair_id: 'pair-1160' }],
  ['ignored category', { category: { id: 'ignored', is_ignored: true } as Transaction['category'] }],
])('hides mortgage allocation controls for an ineligible %s', async (_name, overrides) => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  const getAllocations = vi.spyOn(mortgagePayments, 'get').mockResolvedValue({
    payment_transaction_id: 'tx-1160-leg',
    payment_amount: 250,
    currency: 'USD',
    payment_account_name: 'Zeta Bank',
    allocations: [],
    principal_total: 0,
    interest_total: 0,
    principal_percentage: 0,
    interest_percentage: 0,
  })
  const securedLoan = {
    id: 'loan-1',
    name: 'Mortgage',
    type: 'loan',
    currency: 'USD',
    secured_asset_id: 'property-1',
  } as Account

  renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={transferLegFixture('pair-1160', {
        source: 'sync',
        transfer_pair_id: null,
        ...overrides,
      })}
      categories={[]}
      categoryGroups={[]}
      accounts={[...accountFixtures, securedLoan]}
      onSave={vi.fn()}
      loading={false}
      error={null}
    />,
  )

  expect(screen.queryByText(t('transactions.mortgageAllocation'))).not.toBeInTheDocument()
  expect(getAllocations).not.toHaveBeenCalled()
})

it('reads the split against the payment, not against the parts entered so far', async () => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  vi.spyOn(assets, 'list').mockResolvedValue([])
  vi.spyOn(mortgagePayments, 'get').mockResolvedValue({
    payment_transaction_id: 'tx-1160-leg',
    payment_amount: 250,
    currency: 'USD',
    payment_account_name: 'Zeta Bank',
    allocations: [],
    principal_total: 0,
    interest_total: 0,
    principal_percentage: 0,
    interest_percentage: 0,
  })
  const securedLoan = {
    id: 'loan-1',
    name: 'Mortgage',
    type: 'loan',
    currency: 'USD',
    secured_asset_id: 'property-1',
  } as Account
  const { user } = renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={transferLegFixture('pair-1160', { source: 'sync', transfer_pair_id: null })}
      categories={[]}
      categoryGroups={[]}
      accounts={[...accountFixtures, securedLoan]}
      onSave={vi.fn()}
      loading={false}
      error={null}
    />,
  )

  await user.click(await screen.findByRole('button', { name: t('transactions.mortgageAllocation') }))
  await user.type(await screen.findByLabelText(t('transactions.mortgagePrincipal')), '100')

  // 100 of a 250 payment is 40%, with the rest of the payment still
  // unallocated. Against the parts alone it would read as the whole payment.
  expect(
    await screen.findByText(t('transactions.mortgageSplitPercentages', { principal: '40.0', interest: '0.0' })),
  ).toBeInTheDocument()
})

it('does not submit the transaction when Enter is pressed in a mortgage split field', async () => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  vi.spyOn(assets, 'list').mockResolvedValue([])
  vi.spyOn(mortgagePayments, 'get').mockResolvedValue({
    payment_transaction_id: 'tx-1160-leg',
    payment_amount: 250,
    currency: 'USD',
    payment_account_name: 'Zeta Bank',
    allocations: [],
    principal_total: 0,
    interest_total: 0,
    principal_percentage: 0,
    interest_percentage: 0,
  })
  const onSave = vi.fn()
  const securedLoan = {
    id: 'loan-1',
    name: 'Mortgage',
    type: 'loan',
    currency: 'USD',
    secured_asset_id: 'property-1',
  } as Account
  const { user } = renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={transferLegFixture('pair-1160', { source: 'sync', transfer_pair_id: null })}
      categories={[]}
      categoryGroups={[]}
      accounts={[...accountFixtures, securedLoan]}
      onSave={onSave}
      loading={false}
      error={null}
    />,
  )

  await user.click(await screen.findByRole('button', { name: t('transactions.mortgageAllocation') }))
  const principalInput = await screen.findByLabelText(t('transactions.mortgagePrincipal'))
  await user.type(principalInput, '100')
  await user.keyboard('{Enter}')

  expect(onSave).not.toHaveBeenCalled()
})
