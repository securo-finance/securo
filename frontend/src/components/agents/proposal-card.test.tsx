import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithProviders } from '@/test/utils'
import { isProposalData } from '@/lib/agent-proposals'

const { linkTransfer, unlinkTransfer, updateGoal, updateBudget, deleteBudget } = vi.hoisted(() => ({
  linkTransfer: vi.fn(),
  unlinkTransfer: vi.fn(),
  updateGoal: vi.fn(),
  updateBudget: vi.fn(),
  deleteBudget: vi.fn(),
}))
vi.mock('@/lib/api', () => ({
  budgets: { update: updateBudget, delete: deleteBudget },
  categories: {},
  goals: { update: updateGoal },
  recurring: {},
  rules: {},
  transactions: { linkTransfer, unlinkTransfer },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'en-GB' }))

import { ProposalCard } from './proposal-card'

const debitLeg = {
  id: 'tx-debit',
  description: 'Move to savings',
  date: '2026-10-01',
  amount: 250,
  currency: 'USD',
  type: 'debit',
  account_name: 'Checking',
}
const creditLeg = {
  id: 'tx-credit',
  description: 'From checking',
  date: '2026-10-02',
  amount: 250,
  currency: 'USD',
  type: 'credit',
  account_name: 'Savings',
}

describe('ProposalCard transfer kinds', () => {
  beforeEach(() => {
    localStorage.clear()
    linkTransfer.mockReset()
    unlinkTransfer.mockReset()
  })

  it('treats the transfer tool previews as proposals', () => {
    expect(isProposalData({ kind: 'link_transfer' })).toBe(true)
    expect(isProposalData({ kind: 'unlink_transfer' })).toBe(true)
  })

  it('shows both legs of a link preview and links them on Apply', async () => {
    linkTransfer.mockResolvedValue({ transfer_pair_id: 'pair-1' })
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-link"
        data={{
          kind: 'link_transfer',
          // Credit listed first: the card still reads from the debit side.
          transaction_ids: ['tx-credit', 'tx-debit'],
          legs: [creditLeg, debitLeg],
        }}
      />,
    )

    expect(screen.getByText('Link as transfer')).toBeInTheDocument()
    const summary = screen.getByText(/Checking .*→ Savings/)
    // Leg dates go through the shared date formatter in the viewer's date
    // locale, not as the raw ISO string from the tool payload.
    expect(summary.textContent).toContain('(01 Oct 2026)')
    expect(summary.textContent).not.toContain('2026-10-01')

    await user.click(screen.getByRole('button', { name: /Apply/ }))

    await waitFor(() => expect(linkTransfer).toHaveBeenCalledWith(['tx-credit', 'tx-debit']))
  })

  it('unlinks the pair by its id on Apply', async () => {
    unlinkTransfer.mockResolvedValue(undefined)
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-unlink"
        data={{
          kind: 'unlink_transfer',
          transfer_pair_id: 'pair-9',
          legs: [debitLeg, creditLeg],
        }}
      />,
    )

    expect(screen.getByText('Unlink transfer')).toBeInTheDocument()
    expect(screen.getByText(/go back to regular transactions/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Apply/ }))

    await waitFor(() => expect(unlinkTransfer).toHaveBeenCalledWith('pair-9'))
  })
})

describe('ProposalCard goal and budget kinds', () => {
  beforeEach(() => {
    localStorage.clear()
    updateGoal.mockReset()
    updateBudget.mockReset()
    deleteBudget.mockReset()
  })

  it('treats the goal and budget previews as proposals', () => {
    expect(isProposalData({ kind: 'update_goal' })).toBe(true)
    expect(isProposalData({ kind: 'update_budget' })).toBe(true)
    expect(isProposalData({ kind: 'delete_budget' })).toBe(true)
  })

  it('shows each goal change before and after, and sends only the changes on Apply', async () => {
    updateGoal.mockResolvedValue({ id: 'goal-1' })
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-goal"
        data={{
          kind: 'update_goal',
          target: { id: 'goal-1', name: 'Trip', current_amount: 2000, status: 'active', target_date: null },
          changes: { current_amount: 3500, status: 'paused', target_date: '2027-12-31' },
        }}
      />,
    )

    expect(screen.getByText('Update goal')).toBeInTheDocument()
    expect(
      screen.getByText('Trip → current_amount: 2000 → 3500, status: active → paused, target_date: ∅ → 2027-12-31'),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Apply/ }))

    await waitFor(() =>
      expect(updateGoal).toHaveBeenCalledWith('goal-1', {
        current_amount: 3500,
        status: 'paused',
        target_date: '2027-12-31',
      }),
    )
  })

  it('sends only the new amount when a budget is updated', async () => {
    updateBudget.mockResolvedValue({ id: 'budget-1' })
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-budget"
        data={{
          kind: 'update_budget',
          target: { id: 'budget-1', category_name: 'Groceries', month: '2026-10-01', amount: 800, currency: 'USD' },
          changes: { amount: 1200 },
        }}
      />,
    )

    expect(screen.getByText('Update budget')).toBeInTheDocument()
    expect(screen.getByText(/^Groceries \(2026-10-01\): .*800.* → .*1,200/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Apply/ }))

    await waitFor(() => expect(updateBudget).toHaveBeenCalledWith('budget-1', { amount: 1200 }))
  })

  it('deletes the budget on Apply and says when it covers every month', async () => {
    deleteBudget.mockResolvedValue(undefined)
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-delete-budget"
        data={{
          kind: 'delete_budget',
          target: { id: 'budget-2', category_name: 'Streaming', month: '2026-01-01', amount: 60, currency: 'USD', is_recurring: true },
        }}
      />,
    )

    expect(screen.getByText('Delete budget')).toBeInTheDocument()
    expect(screen.getByText(/^Streaming: .*60.* every month from 2026-01-01$/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Apply/ }))

    await waitFor(() => expect(deleteBudget).toHaveBeenCalledWith('budget-2'))
  })
})
