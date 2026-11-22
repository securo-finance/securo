import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { ReimbursementDialog } from '@/components/reimbursement-dialog'
import { transactions } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { ReimbursementLinkedTransaction, Transaction, TransactionReimbursements } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/lib/api', () => ({
  transactions: {
    reimbursements: vi.fn(),
    replaceReimbursements: vi.fn(),
    reimbursementCandidates: vi.fn(),
  },
}))

const accounts = [{ id: 'acc', name: 'Wallet' }]

const dinner = {
  id: 'dinner',
  account_id: 'acc',
  description: 'Dinner for three',
  date: '2026-10-10',
  amount: 100,
  currency: 'EUR',
  type: 'debit',
} as unknown as Transaction

function linked(id: string, description: string, amount: number, unallocated = amount): ReimbursementLinkedTransaction {
  return { id, account_id: 'acc', description, date: '2026-10-11', amount, currency: 'EUR', type: 'credit', unallocated }
}

function noLinks(id: string): TransactionReimbursements {
  return { transaction_id: id, counterparts: [], anchors: [], unallocated: 100, over_allocated: false, net_amount: null }
}

describe('ReimbursementDialog', () => {
  afterEach(() => vi.resetAllMocks())

  it('links suggested paybacks to an anchor with the amounts left to cover', async () => {
    vi.mocked(transactions.reimbursements).mockResolvedValue(noLinks('dinner'))
    vi.mocked(transactions.reimbursementCandidates).mockResolvedValue([
      linked('alice', 'Alice pays back', 33),
      linked('bob', 'Bob pays back', 80),
    ])
    vi.mocked(transactions.replaceReimbursements).mockResolvedValue(noLinks('dinner'))
    const onClose = vi.fn()
    const { user } = renderWithProviders(
      <ReimbursementDialog open onClose={onClose} transaction={dinner} mode="counterparts" accounts={accounts} />,
    )

    await user.click(await screen.findByText('Alice pays back'))
    await user.click(screen.getByText('Bob pays back'))

    // Bob's 80 only needs to cover the 67 still unpaid.
    expect(screen.getByLabelText(t('reimbursements.amountFor', { description: 'Bob pays back' }))).toHaveValue('67.00')
    expect(screen.getByText(t('reimbursements.netPreview', { amount: '€0.00' }))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() =>
      expect(transactions.replaceReimbursements).toHaveBeenCalledWith('dinner', [
        { counterpart_id: 'alice', amount: '33.00' },
        { counterpart_id: 'bob', amount: '67.00' },
      ]),
    )
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('refuses to save more than a counterpart has left', async () => {
    vi.mocked(transactions.reimbursements).mockResolvedValue({
      ...noLinks('dinner'),
      counterparts: [
        {
          id: 'a1', anchor_id: 'dinner', counterpart_id: 'alice', amount: 20,
          created_at: '2026-10-11T00:00:00Z', transaction: linked('alice', 'Alice pays back', 50, 0),
        },
      ],
    })
    vi.mocked(transactions.reimbursementCandidates).mockResolvedValue([])
    const { user } = renderWithProviders(
      <ReimbursementDialog open onClose={vi.fn()} transaction={dinner} mode="counterparts" accounts={accounts} />,
    )

    const input = await screen.findByLabelText(t('reimbursements.amountFor', { description: 'Alice pays back' }))
    expect(input).toHaveValue('20.00')
    await user.clear(input)
    await user.type(input, '21')

    expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()
  })

  it('links a payback to an anchor from the payback side', async () => {
    const payback = { ...dinner, id: 'payback', description: 'Refund', amount: 40, type: 'credit' } as Transaction
    vi.mocked(transactions.reimbursements).mockImplementation(async (id) =>
      id === 'payback'
        ? { ...noLinks('payback'), unallocated: 40 }
        : {
            ...noLinks(id),
            counterparts: [
              {
                id: 'x', anchor_id: id, counterpart_id: 'other', amount: 10,
                created_at: '2026-10-11T00:00:00Z', transaction: linked('other', 'Other', 10, 0),
              },
            ],
          },
    )
    vi.mocked(transactions.reimbursementCandidates).mockResolvedValue([
      { ...linked('purchase', 'Jacket', 120), type: 'debit' },
    ])
    vi.mocked(transactions.replaceReimbursements).mockResolvedValue(noLinks('purchase'))
    const { user } = renderWithProviders(
      <ReimbursementDialog open onClose={vi.fn()} transaction={payback} mode="anchors" accounts={accounts} />,
    )

    await user.click(await screen.findByText('Jacket'))
    await user.click(screen.getByRole('button', { name: t('reimbursements.link') }))

    // The anchor keeps its other counterpart.
    await waitFor(() =>
      expect(transactions.replaceReimbursements).toHaveBeenCalledWith('purchase', [
        { counterpart_id: 'other', amount: '10' },
        { counterpart_id: 'payback', amount: '40.00' },
      ]),
    )
    expect(transactions.reimbursementCandidates).toHaveBeenCalledWith('payback', { looking_for: 'anchors', q: undefined })
  })
})
