import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LoanBalanceDialog } from '@/components/loan-balance-dialog'
import { todayInTimezone } from '@/lib/date-utils'
import { formatCurrency } from '@/lib/format'
import { renderWithProviders, t } from '@/test/utils'

const adjustLoanBalance = vi.fn()

vi.mock('@/lib/api', () => ({
  accounts: { adjustLoanBalance: (id: string, body: unknown) => adjustLoanBalance(id, body) },
  categories: { list: async () => [] },
  categoryGroups: { list: async () => [] },
}))

// The date picker reads the display locale from the signed-in user.
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

let workspaceTimezone: string | undefined
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => workspaceTimezone }))

const money = (value: number) => formatCurrency(value, 'BRL', 'en-US')

function renderDialog(onClose = vi.fn()) {
  renderWithProviders(
    <LoanBalanceDialog open onClose={onClose} accountId="loan" currentDebt={10000} currency="BRL" locale="en-US" />,
  )
  return onClose
}

function typeBalance(value: string) {
  fireEvent.change(screen.getByLabelText(t('loans.adjustBalance')), { target: { value } })
}

describe('LoanBalanceDialog', () => {
  beforeEach(() => {
    adjustLoanBalance.mockReset()
    workspaceTimezone = undefined
  })

  it('previews a higher statement as a cost of the loan', () => {
    renderDialog()

    typeBalance('10250.4')

    expect(screen.getByRole('status')).toHaveTextContent(t('loans.adjustPreviewGrew', { amount: money(250.4) }))
  })

  it('previews a lower statement as a correction that is not income', () => {
    renderDialog()

    typeBalance('9800')

    expect(screen.getByRole('status')).toHaveTextContent(t('loans.adjustPreviewFell', { amount: money(200) }))
  })

  it('says when the statement already matches the ledger', () => {
    renderDialog()

    expect(screen.getByRole('status')).toHaveTextContent(t('loans.adjustPreviewMatches'))
  })

  it('sends the statement and closes', async () => {
    adjustLoanBalance.mockResolvedValue({ adjustment: 250.4, current_debt: 10250.4, transaction_id: 'tx' })
    const onClose = renderDialog()

    typeBalance('10250.4')
    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(adjustLoanBalance).toHaveBeenCalledWith(
      'loan',
      expect.objectContaining({
        balance: 10250.4,
        description: t('loans.adjustDefaultDescription'),
        category_id: null,
      }),
    )
  })

  it('does not send a negative balance', () => {
    renderDialog()

    typeBalance('-5')

    expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()
  })

  it('dates the statement today in the workspace timezone', async () => {
    workspaceTimezone = 'Pacific/Kiritimati'
    adjustLoanBalance.mockResolvedValue({ adjustment: 0, current_debt: 10000, transaction_id: null })
    const onClose = renderDialog()

    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(adjustLoanBalance).toHaveBeenCalledWith(
      'loan',
      expect.objectContaining({ date: todayInTimezone('Pacific/Kiritimati') }),
    )
  })
})
