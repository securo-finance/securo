import { useQuery } from '@tanstack/react-query'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

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

function transferLegFixture(pairId: string | null, over: Partial<Transaction> = {}): Transaction {
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
    reporting_date_override: over.reporting_date_override ?? null,
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

it('warns in the unlink confirmation that both legs start counting in reports', async () => {
  const { user } = renderTransferDialog(vi.fn())

  await user.click(await screen.findByRole('button', { name: t('transactions.unlinkTransfer') }))
  const confirmText = await screen.findByText(t('transactions.unlinkTransferConfirm'))
  const confirmDialog = confirmText.closest('[role="dialog"]') as HTMLElement

  expect(within(confirmDialog).getByText(t('transactions.unlinkTransferReportWarning'))).toBeInTheDocument()
  // Screen readers announce the warning with the confirmation, not only the first sentence.
  expect(confirmDialog).toHaveAccessibleDescription(
    `${t('transactions.unlinkTransferConfirm')} ${t('transactions.unlinkTransferReportWarning')}`,
  )
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

it('reopens a transaction split among people with its members still selected', async () => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  const member = (id: string, name: string, is_self = false) => ({ id, name, is_self })
  const other = { id: 'g-other', name: 'Other', members: [member('a1', 'Me', true), member('a2', 'Ana')] }
  const trip = { id: 'g-trip', name: 'Trip', members: [member('b1', 'Me', true), member('b2', 'Bob'), member('b3', 'Carol')] }
  vi.spyOn(groups, 'list').mockResolvedValue([other, trip] as never)
  vi.spyOn(groups, 'get').mockImplementation(async (id: string) => (id === trip.id ? trip : other) as never)
  const split = (id: string, memberId: string) => ({
    id, transaction_id: 'tx-dinner', group_member_id: memberId, share_type: 'equal', share_amount: 10, share_pct: null,
  })
  renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={transferLegFixture('unused', {
        id: 'tx-dinner', amount: 30, transfer_pair_id: null,
        splits: [split('s1', 'b1'), split('s2', 'b2'), split('s3', 'b3')] as never,
      })}
      categories={[]}
      categoryGroups={[]}
      accounts={accountFixtures}
      onSave={vi.fn()}
      loading={false}
      error={null}
    />,
  )

  expect(screen.getByRole('checkbox', { name: t('splitGroups.splitTransaction') })).toBeChecked()
  expect(await screen.findByRole('checkbox', { name: 'Bob' })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: 'Carol' })).toBeChecked()
  expect(screen.getByDisplayValue('Trip')).toBeInTheDocument()
})

it('records an incoming transaction as a payback from the members picked', async () => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  const member = (id: string, name: string, is_self = false) => ({ id, name, is_self })
  const trip = { id: 'g1', name: 'Trip', members: [member('m-me', 'Me', true), member('m-jara', 'Jara'), member('m-alatz', 'Alatz')] }
  vi.spyOn(groups, 'list').mockResolvedValue([trip] as never)
  vi.spyOn(groups, 'get').mockResolvedValue(trip as never)
  const onSave = vi.fn()
  const { user } = renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={transferLegFixture('unused', {
        id: 'tx-in', type: 'credit', amount: 66.01, source: 'sync', transfer_pair_id: null,
      })}
      categories={[]}
      categoryGroups={[]}
      accounts={accountFixtures}
      onSave={onSave}
      loading={false}
      error={null}
    />,
  )

  await user.click(screen.getByRole('checkbox', { name: t('splitGroups.splitTransaction') }))
  await user.selectOptions(await screen.findByDisplayValue(t('splitGroups.shareEqual')), 'settlement')
  await user.click(await screen.findByRole('checkbox', { name: 'Jara' }))
  await user.click(screen.getByRole('checkbox', { name: 'Alatz' }))
  // You receive the payback, so you are not offered as a payer.
  expect(screen.queryByText('Me')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: t('common.save') }))

  await vi.waitFor(() => expect(onSave).toHaveBeenCalled())
  // The payback goes with the transaction's own save, like splits.
  expect(onSave.mock.calls[0][0].payback).toEqual({
    group_id: 'g1',
    to_member_id: 'm-me',
    payers: [{ member_id: 'm-jara', amount: 33.01 }, { member_id: 'm-alatz', amount: 33 }],
  })
  expect(onSave.mock.calls[0][0]).not.toHaveProperty('splits')
})

it('reopens a payback with its payers, and replaces it only when it changes', async () => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  const trip = { id: 'g1', name: 'Trip', members: [{ id: 'm-me', name: 'Me', is_self: true }, { id: 'm-jara', name: 'Jara', is_self: false }] }
  vi.spyOn(groups, 'list').mockResolvedValue([trip] as never)
  vi.spyOn(groups, 'get').mockResolvedValue(trip as never)
  const onSave = vi.fn()
  const { user } = renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={transferLegFixture('unused', {
        id: 'tx-in', type: 'credit', amount: 20, source: 'sync', transfer_pair_id: null,
        settled_by: [{ id: 'set-1', group_id: 'g1', from_member_id: 'm-jara', amount: 20 }],
      })}
      categories={[]}
      categoryGroups={[]}
      accounts={accountFixtures}
      onSave={onSave}
      loading={false}
      error={null}
    />,
  )

  expect(screen.getByRole('checkbox', { name: t('splitGroups.splitTransaction') })).toBeChecked()
  expect(await screen.findByRole('checkbox', { name: 'Jara' })).toBeChecked()
  expect(screen.getByDisplayValue(t('splitGroups.shareSettlement'))).toBeInTheDocument()
  // Saving it unchanged leaves the settlement as it is.
  await user.click(screen.getByRole('button', { name: t('common.save') }))
  await vi.waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
  expect(onSave.mock.calls[0][0]).not.toHaveProperty('payback')

  // Turning it off removes the settlement; the transaction stays.
  await user.click(screen.getByRole('checkbox', { name: t('splitGroups.splitTransaction') }))
  await user.click(screen.getByRole('button', { name: t('common.save') }))
  await vi.waitFor(() => expect(onSave).toHaveBeenCalledTimes(2))
  expect(onSave.mock.calls[1][0].payback).toEqual({ payers: [] })
})

it('leaves the payback of a credit a settlement created alone when it is saved', async () => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  const onSave = vi.fn()
  const { user } = renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={transferLegFixture('unused', {
        id: 'tx-acerto', type: 'credit', amount: 20, source: 'settlement', transfer_pair_id: null,
        settled_by: [{ id: 'set-1', group_id: 'g1', from_member_id: 'm-jara', amount: 20 }],
      })}
      categories={[]}
      categoryGroups={[]}
      accounts={accountFixtures}
      onSave={onSave}
      loading={false}
      error={null}
    />,
  )

  // The split section doesn't show for it, so saving must not read that as a removal.
  await user.click(screen.getByRole('button', { name: t('common.save') }))
  await vi.waitFor(() => expect(onSave).toHaveBeenCalled())
  expect(onSave.mock.calls[0][0]).not.toHaveProperty('payback')
})

function splitFixture(): Transaction {
  const food = { id: 'cat-food', name: 'Food', icon: 'utensils', color: '#f59e0b' }
  const home = { id: 'cat-home', name: 'Home', icon: 'house', color: '#3b82f6' }
  return transferLegFixture('unused', {
    id: 'tx-425',
    description: 'Supermarket',
    amount: 150,
    source: 'manual',
    transfer_pair_id: null,
    has_category_lines: true,
    category_lines: [
      { id: 'l1', category_id: food.id, amount: 40, share_type: 'exact', share_pct: null, position: 0, notes: null, category: food as never },
      { id: 'l2', category_id: home.id, amount: 110, share_type: 'exact', share_pct: null, position: 1, notes: null, category: home as never },
    ],
  })
}

function renderSplitDialog(onSave = vi.fn()) {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  const rendered = renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={splitFixture()}
      categories={[]}
      categoryGroups={[]}
      accounts={accountFixtures}
      onSave={onSave}
      loading={false}
      error={null}
    />,
  )
  return { ...rendered, onSave }
}

it('round-trips the parts of a split transaction when it is saved', async () => {
  const { user, onSave } = renderSplitDialog()

  expect(screen.getByText(t('categoryLines.title'))).toBeInTheDocument()
  expect(screen.getByText(t('categoryLines.allAllocated'))).toBeInTheDocument()
  // A split row has no single category to pick, and no split among people.
  expect(screen.queryByText(t('splitGroups.splitTransaction'))).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: t('common.save') }))

  // Unchanged parts are not resent, so a scoped installment edit never
  // copies them over siblings with their own split.
  const payload = onSave.mock.calls[0][0]
  expect(payload.category_id).toBeNull()
  expect(payload).not.toHaveProperty('category_lines')
})

it('sends the parts once they change', async () => {
  const { user, onSave } = renderSplitDialog()
  const [first, second] = screen.getAllByRole('textbox', { name: t('categoryLines.partAmount') })
  await user.clear(first)
  await user.type(first, '50')
  await user.clear(second)
  await user.type(second, '100')
  await user.click(screen.getByRole('button', { name: t('common.save') }))
  expect(onSave.mock.calls[0][0].category_lines).toEqual({
    share_type: 'exact',
    lines: [
      { category_id: 'cat-food', amount: 50 },
      { category_id: 'cat-home', amount: 100 },
    ],
  })
})

it('blocks saving while the parts do not add up to the amount', async () => {
  const { user, onSave } = renderSplitDialog()

  const [first] = screen.getAllByRole('textbox', { name: t('categoryLines.partAmount') })
  await user.clear(first)
  await user.type(first, '30')

  expect(screen.getByText(t('categoryLines.remaining', { value: '$10.00' }))).toBeInTheDocument()
  expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()

  await user.click(screen.getByRole('button', { name: t('categoryLines.fillRemaining') }))
  expect(screen.getByRole('button', { name: t('common.save') })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: t('common.save') }))
  expect(onSave.mock.calls[0][0].category_lines.lines.map((l: { amount: number }) => l.amount)).toEqual([30, 120])
})

it('undoing a split sends an empty list and keeps the largest part as the category', async () => {
  const { user, onSave } = renderSplitDialog()

  await user.click(screen.getByRole('button', { name: t('categoryLines.undo') }))
  expect(screen.queryByRole('button', { name: t('categoryLines.undo') })).not.toBeInTheDocument()
  // Back to a single category, the split can be started again.
  expect(screen.getByRole('button', { name: t('categoryLines.start') })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: t('common.save') }))

  const payload = onSave.mock.calls[0][0]
  expect(payload.category_lines).toEqual({ share_type: 'exact', lines: [] })
  expect(payload.category_id).toBe('cat-home')
})

it('does not offer a split across categories on a transfer', () => {
  renderTransferDialog(vi.fn())
  expect(screen.queryByRole('button', { name: t('categoryLines.start') })).not.toBeInTheDocument()
})

it('switching a fully allocated split to percentages keeps it allocated', async () => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  const tx = splitFixture()
  const cat = (id: string) => ({ id, name: id, icon: 'tag', color: '#000' })
  const onSave = vi.fn()
  const { user } = renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={{
        ...tx,
        amount: 0.03,
        category_lines: ['a', 'b', 'c'].map((id, position) => ({
          id, category_id: id, amount: 0.01, share_type: 'exact' as const, share_pct: null,
          position, notes: null, category: cat(id) as never,
        })),
      }}
      categories={[]}
      categoryGroups={[]}
      accounts={accountFixtures}
      onSave={onSave}
      loading={false}
      error={null}
    />,
  )

  await user.click(screen.getByRole('button', { name: t('categoryLines.byPercent') }))
  // 33.33 + 33.33 + 33.34: the hundredth left over goes to the last part.
  expect(screen.getByText(t('categoryLines.allAllocated'))).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: t('common.save') }))
  expect(onSave.mock.calls[0][0].category_lines.lines.map((l: { share_pct: number }) => l.share_pct))
    .toEqual([33.33, 33.33, 33.34])
})

it('Enter in a field does not save parts that do not add up', async () => {
  const { user, onSave } = renderSplitDialog()
  const [first] = screen.getAllByRole('textbox', { name: t('categoryLines.partAmount') })
  await user.clear(first)
  await user.type(first, '30{Enter}')
  expect(onSave).not.toHaveBeenCalled()
})
function syncedTransaction(overrides: Partial<Transaction> = {}): Transaction {
  return {
    ...transferLegFixture(null, {
      id: 'tx-synced',
      account_id: 'acc-a',
      external_id: 'provider-1',
      description: 'Synced transaction',
      date: '2026-10-01',
      type: 'credit',
      source: 'sync',
      transfer_pair_id: null,
    }),
    ...overrides,
    reporting_date_override: overrides.reporting_date_override ?? null,
  }
}

function renderSyncedDialog(
  transaction: Transaction,
  accounts: Account[] = accountFixtures,
  onSave = vi.fn(),
) {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
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
  it('submits reporting attribution without mutating the bank date', async () => {
    const { onSave, user } = renderSyncedDialog(syncedTransaction())

    expect(screen.getByDisplayValue('2026-10-01')).toBeDisabled()
    expect(screen.getByRole('combobox', { name: t('transactions.colStatus') })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: t('transactions.colStatus') })).toHaveValue('posted')
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
    const { onSave, user } = renderSyncedDialog(
      syncedTransaction({ reporting_date_override: '2026-09-30' }),
    )

    await user.click(
      screen.getByRole('button', { name: t('transactions.clearReportingDate') }),
    )
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave.mock.calls[0][0]).toMatchObject({ reporting_date_override: null })
    expect(onSave.mock.calls[0][0]).not.toHaveProperty('date')
  })

  it('hides reporting attribution until the synced account is resolved', () => {
    renderSyncedDialog(syncedTransaction(), [])

    expect(
      screen.queryByLabelText(t('transactions.reportingDatePlaceholder')),
    ).not.toBeInTheDocument()
  })
})

/** Shows the flag state a mounted list query holds, as a page would. */
function FlagConsumer({ queryKey, read }: { queryKey: string[]; read: () => Promise<Transaction> }) {
  const { data } = useQuery({ queryKey, queryFn: read })
  if (!data) return null
  return <p>{`${queryKey[0]}: ${data.is_flagged ? 'flagged' : 'not flagged'}`}</p>
}

describe('TransactionDialog flag (#56)', () => {
  it('refreshes the mounted transaction lists itself when no onIgnoreChanged is passed', async () => {
    const tx = syncedTransaction({ is_flagged: false })
    let stored = tx
    const read = async () => stored
    vi.spyOn(transactions, 'update').mockImplementation(async (_id, body) => {
      stored = { ...stored, is_flagged: body.is_flagged ?? stored.is_flagged }
      return stored
    })
    vi.spyOn(currencies, 'list').mockResolvedValue([])
    vi.spyOn(payees, 'list').mockResolvedValue([])
    vi.spyOn(groups, 'list').mockResolvedValue([])
    const { user } = renderWithProviders(
      <>
        <FlagConsumer queryKey={['transactions', 'list']} read={read} />
        <FlagConsumer queryKey={['drill-down', 'expenses']} read={read} />
        <TransactionDialog
          open
          onClose={vi.fn()}
          transaction={tx}
          categories={[]}
          categoryGroups={[]}
          accounts={accountFixtures}
          onSave={vi.fn()}
          loading={false}
          error={null}
          isSynced
        />
      </>,
    )
    expect(await screen.findByText('transactions: not flagged')).toBeInTheDocument()
    expect(await screen.findByText('drill-down: not flagged')).toBeInTheDocument()

    const button = screen.getByRole('button', { name: t('transactions.flagAction') })
    expect(button).not.toHaveAttribute('aria-pressed')
    await user.click(button)

    expect(transactions.update).toHaveBeenCalledWith(tx.id, { is_flagged: true })
    expect(await screen.findByText('transactions: flagged')).toBeInTheDocument()
    expect(await screen.findByText('drill-down: flagged')).toBeInTheDocument()
    expect(
      await screen.findByRole('button', { name: t('transactions.unflagAction') }),
    ).toBeInTheDocument()
  })

  it('sets a reminder date and note on the flag, each saved on its own', async () => {
    vi.setSystemTime(new Date('2026-10-20T12:00:00'))
    const tx = syncedTransaction({ is_flagged: true })
    let stored = tx
    vi.spyOn(transactions, 'update').mockImplementation(async (_id, body) => {
      stored = { ...stored, ...body } as Transaction
      return stored
    })
    vi.spyOn(currencies, 'list').mockResolvedValue([])
    vi.spyOn(payees, 'list').mockResolvedValue([])
    vi.spyOn(groups, 'list').mockResolvedValue([])
    const { user } = renderWithProviders(
      <TransactionDialog
        open
        onClose={vi.fn()}
        transaction={tx}
        categories={[]}
        categoryGroups={[]}
        accounts={accountFixtures}
        onSave={vi.fn()}
        loading={false}
        error={null}
        isSynced
      />,
    )

    expect(await screen.findByTestId('flag-reminder')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('transactions.flagPreset.nextWeek') }))
    expect(transactions.update).toHaveBeenCalledWith(tx.id, { flag_due_date: '2026-10-27' })

    const note = screen.getByLabelText(t('transactions.flagNote'))
    await user.type(note, 'ask for the refund{Enter}')
    expect(transactions.update).toHaveBeenLastCalledWith(tx.id, { flag_note: 'ask for the refund' })
    vi.useRealTimers()
  })

  it('hides the reminder once the flag is removed', async () => {
    const tx = syncedTransaction({ is_flagged: true, flag_due_date: '2026-10-27', flag_note: 'call the shop' })
    vi.spyOn(transactions, 'update').mockResolvedValue({
      ...tx, is_flagged: false, flag_due_date: null, flag_note: null,
    })
    vi.spyOn(currencies, 'list').mockResolvedValue([])
    vi.spyOn(payees, 'list').mockResolvedValue([])
    vi.spyOn(groups, 'list').mockResolvedValue([])
    const { user } = renderWithProviders(
      <TransactionDialog
        open
        onClose={vi.fn()}
        transaction={tx}
        categories={[]}
        categoryGroups={[]}
        accounts={accountFixtures}
        onSave={vi.fn()}
        loading={false}
        error={null}
        isSynced
      />,
    )

    expect(await screen.findByDisplayValue('call the shop')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('transactions.unflagAction') }))

    await waitFor(() => expect(screen.queryByTestId('flag-reminder')).not.toBeInTheDocument())
  })
})
