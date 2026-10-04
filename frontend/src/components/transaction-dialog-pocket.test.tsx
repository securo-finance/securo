import { fireEvent, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { TransactionDialog } from './transaction-dialog'
import { createTestQueryClient, renderWithProviders, t } from '@/test/utils'
import type { Category, Transaction, TransactionEditPayload } from '@/types'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { language: 'en', currency_display: 'EUR' } } }),
}))

const draft = {
  description: 'Salary', amount: 100, date: '2026-09-28', type: 'credit',
  status: 'posted', currency: 'EUR', account_id: 'account',
  goal_allocations: [{ goal_id: 'monitor', amount: 50 }],
} satisfies TransactionEditPayload

function renderDraft(transaction: Transaction | null = null) {
  const queryClient = createTestQueryClient()
  queryClient.setDefaultOptions({ queries: { enabled: false, retry: false, staleTime: Infinity } })
  queryClient.setQueryData(['settings', 'attachments'], { max_size_mb: 10 })
  queryClient.setQueryData(['goals', 'pocket-allocator'], [{
    id: 'monitor', name: 'Monitor', tracking_type: 'pocket', account_id: 'account',
    status: 'active', currency: 'EUR', target_amount: 500, current_amount: 50,
    percentage: 10, target_date: null, monthly_contribution: null, on_track: null,
  }])
  const onSave = vi.fn()
  const result = renderWithProviders(
    <TransactionDialog
      open onClose={vi.fn()} transaction={transaction} duplicateDraft={transaction ? undefined : draft}
      categories={[{
        id: 'ignored', name: 'Ignored category', user_id: 'user', group_id: null,
        icon: 'circle-help', color: '#000000', is_system: false, is_hidden: false,
        treat_as_transfer: false, is_ignored: true,
      } satisfies Category]} categoryGroups={[]}
      accounts={[{ id: 'account', name: 'Savings', currency: 'EUR' }]}
      onSave={onSave} loading={false} error={null}
    />,
    { queryClient },
  )
  return { ...result, onSave }
}

it('submits the visible pocket assignments of a duplicated create draft without editing them', async () => {
  const { user, onSave } = renderDraft()
  expect(screen.getByRole('button', { name: 'Edit pockets' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save' }))

  expect(onSave).toHaveBeenCalledWith(
    expect.objectContaining({ goal_allocations: draft.goal_allocations }),
    undefined, undefined, undefined, 'save',
  )
})

it('retains allocations after selecting an ignored category and lets the user explicitly clear them', async () => {
  const { user, onSave } = renderDraft()
  await user.click(screen.getByRole('button', { name: t('transactions.noCategory') }))
  await user.click(screen.getByRole('option', { name: 'Ignored category' }))

  await user.click(screen.getByRole('button', { name: 'Edit pockets' }))
  expect(screen.getByLabelText('Allocation for Monitor')).toHaveValue(50)
  expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Allocation for Monitor'), { target: { value: '' } })
  await user.click(screen.getByRole('button', { name: 'Apply' }))
  await user.click(screen.getByRole('button', { name: 'Save' }))

  expect(onSave).toHaveBeenCalledWith(
    expect.objectContaining({ category_id: 'ignored', goal_allocations: [] }),
    undefined, undefined, undefined, 'save',
  )
})

it('keeps saved pocket assignments when a status change is reverted', async () => {
  const saved = {
    ...draft, id: 'tx', goal_allocations: [{ id: 'allocation', goal_id: 'monitor', goal_name: 'Monitor', amount: 50 }],
  } as unknown as Transaction
  const { user, onSave } = renderDraft(saved)
  const statusSelect = screen.getByDisplayValue(t('transactions.statusPosted'))
  await user.selectOptions(statusSelect, 'pending')
  await user.selectOptions(statusSelect, 'posted')
  await user.click(screen.getByRole('button', { name: 'Save' }))

  expect(onSave).toHaveBeenCalledTimes(1)
  expect(onSave.mock.calls[0][0]).not.toHaveProperty('goal_allocations')
})

it('clears saved pocket assignments when the status change is kept', async () => {
  const saved = {
    ...draft, id: 'tx', goal_allocations: [{ id: 'allocation', goal_id: 'monitor', goal_name: 'Monitor', amount: 50 }],
  } as unknown as Transaction
  const { user, onSave } = renderDraft(saved)
  await user.selectOptions(screen.getByDisplayValue(t('transactions.statusPosted')), 'pending')
  await user.click(screen.getByRole('button', { name: 'Save' }))

  expect(onSave.mock.calls[0][0]).toEqual(expect.objectContaining({ status: 'pending', goal_allocations: [] }))
})

it('keeps edited pocket assignments when a status change is reverted', async () => {
  const saved = {
    ...draft, id: 'tx', goal_allocations: [{ id: 'allocation', goal_id: 'monitor', goal_name: 'Monitor', amount: 50 }],
  } as unknown as Transaction
  const { user, onSave } = renderDraft(saved)
  await user.click(screen.getByRole('button', { name: 'Edit pockets' }))
  fireEvent.change(screen.getByLabelText('Allocation for Monitor'), { target: { value: '30' } })
  await user.click(screen.getByRole('button', { name: 'Apply' }))
  const statusSelect = screen.getByDisplayValue(t('transactions.statusPosted'))
  await user.selectOptions(statusSelect, 'pending')
  await user.selectOptions(statusSelect, 'posted')
  await user.click(screen.getByRole('button', { name: 'Save' }))

  expect(onSave.mock.calls[0][0]).toEqual(expect.objectContaining({
    goal_allocations: [{ goal_id: 'monitor', amount: 30 }],
  }))
})

it('restores the pocket assignments of a draft when installments are switched off again', async () => {
  const { user, onSave } = renderDraft()
  const installments = screen.getByRole('checkbox', { name: t('transactions.makeInstallment') })
  await user.click(installments)
  await user.click(installments)
  await user.click(screen.getByRole('button', { name: 'Save' }))

  expect(onSave).toHaveBeenCalledWith(
    expect.objectContaining({ goal_allocations: draft.goal_allocations }),
    undefined, undefined, undefined, 'save',
  )
})
