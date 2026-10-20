import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createTestQueryClient, renderWithProviders, t } from '@/test/utils'
import type { Goal } from '@/types'
import { PocketAllocator } from './pocket-allocator'
import { pocketAllocationsAreValid } from '@/lib/pocket-allocation-utils'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { language: 'en', currency_display: 'EUR' } } }),
}))

vi.mock('@/hooks/use-timezone', () => ({
  useEffectiveTimezone: () => 'Europe/Berlin',
}))

afterEach(() => vi.useRealTimers())

const pocket = {
  id: 'monitor', user_id: 'user', name: 'Monitor', target_amount: 500,
  current_amount: 40, currency: 'EUR', target_amount_primary: null,
  current_amount_primary: null, target_date: null, tracking_type: 'pocket',
  account_id: 'account', asset_id: null, asset_group_id: null, status: 'active',
  icon: null, color: null, position: 0, metadata_json: null, created_at: '',
  updated_at: '', percentage: 8, monthly_contribution: null, on_track: null,
  account_name: 'Savings', asset_name: null, asset_group_name: null,
} satisfies Goal

describe('pocketAllocationsAreValid', () => {
  it('accepts an empty assignment even when a converted amount is not known', () => {
    expect(pocketAllocationsAreValid([], Number.NaN)).toBe(true)
  })

  it('rejects assignments that exceed the transaction', () => {
    expect(pocketAllocationsAreValid([
      { goal_id: 'a', amount: 60 },
      { goal_id: 'b', amount: 50 },
    ], 100)).toBe(false)
  })
})

it('applies a split from the compact pocket dialog', async () => {
  const queryClient = createTestQueryClient()
  const base = {
    user_id: 'user', target_amount: 500, current_amount: 100, currency: 'EUR',
    target_amount_primary: null, current_amount_primary: null, target_date: null,
    tracking_type: 'pocket', account_id: 'account', asset_id: null,
    asset_group_id: null, status: 'active', icon: null, color: null, position: 0,
    metadata_json: null, created_at: '', updated_at: '', percentage: 20,
    monthly_contribution: null, on_track: null, account_name: 'Savings',
    asset_name: null, asset_group_name: null,
  } satisfies Partial<Goal>
  queryClient.setQueryData(['goals', 'pocket-allocator'], [
    { ...base, id: 'monitor', name: 'Monitor' },
    { ...base, id: 'desk', name: 'Desk' },
  ] as Goal[])
  const onChange = vi.fn()
  const { user } = renderWithProviders(
    <PocketAllocator
      accountId="account"
      transactionType="credit"
      transactionAmount={200}
      transactionStatus="posted"
      value={[]}
      onChange={onChange}
    />,
    { queryClient },
  )

  await user.click(screen.getByRole('button', { name: 'Choose pockets' }))
  fireEvent.change(screen.getByLabelText('Allocation for Monitor'), { target: { value: '125' } })
  expect(onChange).not.toHaveBeenCalled()

  await user.click(screen.getByRole('button', { name: 'Apply' }))
  expect(onChange).toHaveBeenLastCalledWith([{ goal_id: 'monitor', amount: 125 }])
})

it('blocks over-allocation and discards an unconfirmed draft', async () => {
  const queryClient = createTestQueryClient()
  const goal = {
    id: 'monitor', user_id: 'user', name: 'Monitor', target_amount: 500,
    current_amount: 100, currency: 'EUR', target_amount_primary: null,
    current_amount_primary: null, target_date: null, tracking_type: 'pocket',
    account_id: 'account', asset_id: null, asset_group_id: null, status: 'active',
    icon: null, color: null, position: 0, metadata_json: null, created_at: '',
    updated_at: '', percentage: 20, monthly_contribution: null, on_track: null,
    account_name: 'Savings', asset_name: null, asset_group_name: null,
  } satisfies Goal
  queryClient.setQueryData(['goals', 'pocket-allocator'], [goal])
  const onChange = vi.fn()
  const { user } = renderWithProviders(
    <PocketAllocator
      accountId="account"
      transactionType="credit"
      transactionAmount={200}
      transactionStatus="posted"
      value={[]}
      onChange={onChange}
    />,
    { queryClient },
  )

  await user.click(screen.getByRole('button', { name: 'Choose pockets' }))
  fireEvent.change(screen.getByLabelText('Allocation for Monitor'), { target: { value: '225' } })

  expect(screen.getByText('Pocket allocations exceed the transaction amount.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()

  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(onChange).not.toHaveBeenCalled()
  expect(screen.queryByRole('dialog', { name: 'Pocket allocation' })).not.toBeInTheDocument()
})

it.each([
  { name: 'new debit', value: [], originalAmount: null, maximum: 40, invalid: true },
  { name: 'duplicated debit', value: [{ goal_id: 'monitor', amount: 50 }], originalAmount: null, maximum: 40, invalid: true },
  { name: 'existing debit', value: [{ goal_id: 'monitor', amount: 50 }], originalAmount: -50, maximum: 90, invalid: false },
  { name: 'credit changed to debit', value: [], originalAmount: 50, maximum: 0, invalid: true },
])('uses only the persisted effect when validating a $name', async ({ value, originalAmount, maximum, invalid }) => {
  const queryClient = createTestQueryClient()
  queryClient.setQueryData(['goals', 'pocket-allocator'], [pocket])
  const { user } = renderWithProviders(
    <PocketAllocator
      accountId="account"
      transactionType="debit"
      transactionAmount={100}
      transactionStatus="posted"
      value={value}
      originalAllocations={originalAmount == null ? [] : [{ goal_id: 'monitor', amount: originalAmount }]}
      onChange={vi.fn()}
    />,
    { queryClient },
  )

  await user.click(screen.getByRole('button', { name: value.length ? 'Edit pockets' : 'Choose pockets' }))
  fireEvent.change(screen.getByLabelText('Allocation for Monitor'), { target: { value: '50' } })

  expect(screen.getByLabelText('Allocation for Monitor')).toHaveAttribute('max', String(maximum))
  if (invalid) {
    expect(screen.getByText('An allocation exceeds the amount available in that pocket.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
  } else {
    expect(screen.queryByText('An allocation exceeds the amount available in that pocket.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled()
  }
})

it('keeps a fully spent pocket available after clearing an edit draft', async () => {
  const queryClient = createTestQueryClient()
  queryClient.setQueryData(['goals', 'pocket-allocator'], [{ ...pocket, current_amount: 0 }])
  const props = {
    accountId: 'account', transactionType: 'debit' as const,
    transactionAmount: 50, transactionStatus: 'posted' as const,
    originalAllocations: [{ goal_id: 'monitor', amount: -50 }], onChange: vi.fn(),
  }
  const { rerender, user } = renderWithProviders(
    <PocketAllocator {...props} value={[{ goal_id: 'monitor', amount: 50 }]} />,
    { queryClient },
  )

  await user.click(screen.getByRole('button', { name: 'Edit pockets' }))
  fireEvent.change(screen.getByLabelText('Allocation for Monitor'), { target: { value: '' } })
  await user.click(screen.getByRole('button', { name: 'Apply' }))
  expect(props.onChange).toHaveBeenCalledWith([])
  rerender(<PocketAllocator {...props} value={[]} />)

  await user.click(screen.getByRole('button', { name: 'Choose pockets' }))
  expect(screen.getByLabelText('Allocation for Monitor')).toHaveAttribute('max', '50')
})

it('counts remaining calendar days in the workspace timezone across a DST change', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-24T22:30:00Z'))
  const queryClient = createTestQueryClient()
  queryClient.setQueryData(['goals', 'pocket-allocator'], [{ ...pocket, target_date: '2026-10-26' }])
  const { user } = renderWithProviders(
    <PocketAllocator
      accountId="account" transactionType="credit" transactionAmount={100}
      transactionStatus="posted" value={[]} onChange={vi.fn()}
    />,
    { queryClient },
  )

  await user.click(screen.getByRole('button', { name: 'Choose pockets' }))
  expect(screen.getByText(t('goals.daysRemaining', { count: 1 }))).toBeInTheDocument()
})

it('applies cent splits whose floating-point sum would exceed the transaction', async () => {
  const queryClient = createTestQueryClient()
  queryClient.setQueryData(['goals', 'pocket-allocator'], [
    pocket, { ...pocket, id: 'desk', name: 'Desk' },
  ])
  const onChange = vi.fn()
  const { user } = renderWithProviders(
    <PocketAllocator
      accountId="account" transactionType="credit" transactionAmount={0.3}
      transactionStatus="posted" value={[]} onChange={onChange}
    />,
    { queryClient },
  )

  await user.click(screen.getByRole('button', { name: 'Choose pockets' }))
  fireEvent.change(screen.getByLabelText('Allocation for Monitor'), { target: { value: '0.1' } })
  fireEvent.change(screen.getByLabelText('Allocation for Desk'), { target: { value: '0.2' } })
  await user.click(screen.getByRole('button', { name: 'Apply' }))

  expect(onChange).toHaveBeenCalledWith([
    { goal_id: 'monitor', amount: 0.1 }, { goal_id: 'desk', amount: 0.2 },
  ])
})

it.each(['ignored', 'pending'] as const)('allows clearing allocations on an %s transaction', async state => {
  const queryClient = createTestQueryClient()
  queryClient.setQueryData(['goals', 'pocket-allocator'], [pocket])
  const onChange = vi.fn()
  const props = {
    accountId: 'account', transactionType: 'credit' as const, transactionAmount: 50,
    transactionStatus: state === 'pending' ? 'pending' as const : 'posted' as const,
    isIgnored: state === 'ignored', onChange,
  }
  const { rerender, user } = renderWithProviders(
    <PocketAllocator {...props} value={[{ goal_id: 'monitor', amount: 20 }]} />,
    { queryClient },
  )

  await user.click(screen.getByRole('button', { name: 'Edit pockets' }))
  expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Allocation for Monitor'), { target: { value: '' } })
  await user.click(screen.getByRole('button', { name: 'Apply' }))
  expect(onChange).toHaveBeenCalledWith([])

  rerender(<PocketAllocator {...props} value={[]} />)
  expect(screen.getByRole('button', { name: 'Choose pockets' })).toBeDisabled()
})

it('filters pockets without losing draft allocations', async () => {
  const queryClient = createTestQueryClient()
  const base = {
    user_id: 'user', target_amount: 500, current_amount: 100, currency: 'EUR',
    target_amount_primary: null, current_amount_primary: null, target_date: null,
    tracking_type: 'pocket', account_id: 'account', asset_id: null,
    asset_group_id: null, status: 'active', icon: null, color: null, position: 0,
    metadata_json: null, created_at: '', updated_at: '', percentage: 20,
    monthly_contribution: null, on_track: null, account_name: 'Savings',
    asset_name: null, asset_group_name: null,
  } satisfies Partial<Goal>
  queryClient.setQueryData(['goals', 'pocket-allocator'], [
    { ...base, id: 'monitor', name: 'Monitor' },
    { ...base, id: 'holiday', name: 'Holiday' },
  ] as Goal[])
  const onChange = vi.fn()
  const { user } = renderWithProviders(
    <PocketAllocator
      accountId="account"
      transactionType="credit"
      transactionAmount={200}
      transactionStatus="posted"
      value={[]}
      onChange={onChange}
    />,
    { queryClient },
  )

  await user.click(screen.getByRole('button', { name: 'Choose pockets' }))
  fireEvent.change(screen.getByLabelText('Allocation for Monitor'), { target: { value: '75' } })
  await user.type(screen.getByRole('searchbox', { name: 'Search pockets...' }), 'holiday')

  expect(screen.queryByLabelText('Allocation for Monitor')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Allocation for Holiday')).toBeInTheDocument()

  await user.clear(screen.getByRole('searchbox', { name: 'Search pockets...' }))
  expect(screen.getByLabelText('Allocation for Monitor')).toHaveValue(75)
})
