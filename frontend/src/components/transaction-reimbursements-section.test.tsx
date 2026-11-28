import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { TransactionReimbursementsSection } from '@/components/transaction-reimbursements-section'
import { renderWithProviders, t } from '@/test/utils'
import type { Transaction } from '@/types'

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

const anchor = {
  id: 'dinner', account_id: 'acc', description: 'Dinner', date: '2026-10-10', amount: 100,
  currency: 'EUR', type: 'debit', reimbursement_role: 'anchor', reimbursement_net: 34,
} as unknown as Transaction

const counterpart = {
  ...anchor, id: 'alice', type: 'credit', amount: 50, reimbursement_role: 'counterpart',
  reimbursement_net: null, reimbursement_unallocated: 20,
} as unknown as Transaction

describe('TransactionReimbursementsSection privacy', () => {
  it('shows the net and what is left of a row', () => {
    const { unmount } = renderWithProviders(<TransactionReimbursementsSection transaction={anchor} accounts={[]} />)
    expect(screen.getByText(t('reimbursements.sectionNet', { amount: '−€34.00' }))).toBeInTheDocument()
    unmount()

    renderWithProviders(<TransactionReimbursementsSection transaction={counterpart} accounts={[]} />)
    expect(screen.getByText(t('reimbursements.sectionLeft', { amount: '€20.00' }))).toBeInTheDocument()
  })

  it('hides both amounts while the form hides its own', () => {
    const { unmount } = renderWithProviders(
      <TransactionReimbursementsSection transaction={anchor} accounts={[]} hideAmounts />,
    )
    expect(screen.queryByText(/34/)).not.toBeInTheDocument()
    expect(screen.getByText(t('reimbursements.sectionNet', { amount: '•••••' }))).toBeInTheDocument()
    unmount()

    renderWithProviders(<TransactionReimbursementsSection transaction={counterpart} accounts={[]} hideAmounts />)
    expect(screen.queryByText(/20/)).not.toBeInTheDocument()
  })
})
