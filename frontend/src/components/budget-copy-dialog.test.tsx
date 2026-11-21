import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import { onlineManager } from '@tanstack/react-query'

import { BudgetCopyDialog } from '@/components/budget-copy-dialog'
import i18n from '@/lib/i18n'
import { renderWithProviders, t } from '@/test/utils'
import type { Budget } from '@/types'

const api = vi.hoisted(() => ({
  budgets: { list: vi.fn(), copyMonth: vi.fn() },
}))

vi.mock('@/lib/api', () => ({ budgets: api.budgets }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ current: { id: 'ws-1' } }) }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }) }))

function budget(id: string, categoryId: string, amount: number, month = '2026-09-01'): Budget {
  return { id, user_id: 'u', category_id: categoryId, amount, month, is_recurring: false }
}

const rent = budget('b-rent', 'rent', 900)
const food = budget('b-food', 'food', 300)

beforeEach(() => {
  vi.clearAllMocks()
  api.budgets.list.mockResolvedValue([rent, food])
  api.budgets.copyMonth.mockResolvedValue([food])
})

afterEach(async () => {
  onlineManager.setOnline(true)
  await i18n.changeLanguage('en')
})

describe('Budget copy dialog', () => {
  it('defaults to the previous month, previews only the missing categories and copies them', async () => {
    const onOpenChange = vi.fn()
    const { user } = renderWithProviders(
      <BudgetCopyDialog open onOpenChange={onOpenChange} targetMonth="2026-10" targetBudgets={[budget('b-rent-oct', 'rent', 900, '2026-10-01')]} targetRefreshing={false} />,
    )

    // Both the read and the copy are pinned to the workspace the preview is
    // built from, so a switch in another tab cannot move the copy.
    expect(api.budgets.list).toHaveBeenCalledWith('2026-09-01', 'ws-1')
    expect(await screen.findByText(t('budgets.copyPreviewCount', { count: 1 }))).toBeInTheDocument()
    expect(screen.getByText(t('budgets.copyPreviewTotal', { amount: '$300.00' }))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('budgets.copyConfirm') }))

    await waitFor(() => {
      expect(api.budgets.copyMonth).toHaveBeenCalledWith({
        source_month: '2026-09-01',
        target_month: '2026-10-01',
        adjustment_percentage: 0,
        overwrite_existing: false,
      }, 'ws-1')
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('replaces the existing budgets and applies the percentage adjustment', async () => {
    const { user } = renderWithProviders(
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[budget('b-rent-oct', 'rent', 900, '2026-10-01')]} targetRefreshing={false} />,
    )

    await screen.findByText(t('budgets.copyPreviewCount', { count: 1 }))
    await user.click(screen.getByRole('radio', { name: t('budgets.copyOverwrite') }))
    // Rent already is $900 in October, so replacing it would write nothing.
    expect(screen.getByText(t('budgets.copyPreviewCount', { count: 1 }))).toBeInTheDocument()

    await user.type(screen.getByLabelText(t('budgets.copyAdjustment')), '10')
    expect(screen.getByText(t('budgets.copyPreviewCount', { count: 2 }))).toBeInTheDocument()
    expect(screen.getByText(t('budgets.copyPreviewTotal', { amount: '$1,320.00' }))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('budgets.copyConfirm') }))

    await waitFor(() => {
      expect(api.budgets.copyMonth).toHaveBeenCalledWith({
        source_month: '2026-09-01',
        target_month: '2026-10-01',
        adjustment_percentage: 10,
        overwrite_existing: true,
      }, 'ws-1')
    })
  })

  it('copies with the options that were confirmed, not the ones the form holds when the request runs', async () => {
    const { user } = renderWithProviders(
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[]} targetRefreshing={false} />,
    )

    await screen.findByText(t('budgets.copyPreviewCount', { count: 2 }))
    onlineManager.setOnline(false)
    await user.click(screen.getByRole('button', { name: t('budgets.copyConfirm') }))
    // Queued while offline, the request has not gone out yet.
    expect(api.budgets.copyMonth).not.toHaveBeenCalled()

    await user.click(screen.getByRole('radio', { name: t('budgets.copyOverwrite') }))
    await act(async () => { onlineManager.setOnline(true) })

    await waitFor(() => {
      expect(api.budgets.copyMonth).toHaveBeenCalledWith({
        source_month: '2026-09-01',
        target_month: '2026-10-01',
        adjustment_percentage: 0,
        overwrite_existing: false,
      }, 'ws-1')
    })
  })

  it('leaves out a category the target month already resolves to at the copied amount', async () => {
    const { user } = renderWithProviders(
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[budget('b-rent-oct', 'rent', 990, '2026-10-01')]} targetRefreshing={false} />,
    )

    await screen.findByText(t('budgets.copyPreviewCount', { count: 1 }))
    await user.click(screen.getByRole('radio', { name: t('budgets.copyOverwrite') }))
    await user.type(screen.getByLabelText(t('budgets.copyAdjustment')), '10')

    // Rent already is $990 in October, so replacing it would write nothing.
    expect(screen.getByText(t('budgets.copyPreviewCount', { count: 1 }))).toBeInTheDocument()
    expect(screen.getByText(t('budgets.copyPreviewTotal', { amount: '$330.00' }))).toBeInTheDocument()
  })

  it('rounds each adjusted amount to cents before summing them', async () => {
    api.budgets.list.mockResolvedValue([
      budget('b-1', 'rent', 100.05),
      budget('b-2', 'food', 100.05),
    ])
    const { user } = renderWithProviders(
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[]} targetRefreshing={false} />,
    )

    await screen.findByText(t('budgets.copyPreviewCount', { count: 2 }))
    await user.type(screen.getByLabelText(t('budgets.copyAdjustment')), '10')

    // The API saves $110.06 for each category, so the preview adds cents, not
    // the unrounded $220.11 the raw product would give.
    expect(screen.getByText(t('budgets.copyPreviewTotal', { amount: '$220.12' }))).toBeInTheDocument()
  })

  it('does not round an amount just below half a cent up to it', async () => {
    api.budgets.list.mockResolvedValue([budget('b-rent', 'rent', 100)])
    const { user } = renderWithProviders(
      <BudgetCopyDialog
        open
        onOpenChange={vi.fn()}
        targetMonth="2026-10"
        targetBudgets={[budget('b-rent-oct', 'rent', 100.01, '2026-10-01')]}
        targetRefreshing={false}
      />,
    )

    // In keep mode the target's Rent means nothing is copied; replacing it is
    // what makes the two amounts comparable.
    await screen.findByText(t('budgets.copyPreviewAllExist', { month: 'October 2026' }))
    await user.click(screen.getByRole('radio', { name: t('budgets.copyOverwrite') }))
    await screen.findByText(t('budgets.copyPreviewCount', { count: 1 }))
    await user.type(screen.getByLabelText(t('budgets.copyAdjustment')), '0.004999999')

    // $100 at 0.004999999% is $100.004999999: the API saves $100.00, so this
    // is still a change for a target month that holds $100.01.
    expect(screen.getByText(t('budgets.copyPreviewCount', { count: 1 }))).toBeInTheDocument()
    expect(screen.getByText(t('budgets.copyPreviewTotal', { amount: '$100.00' }))).toBeInTheDocument()
  })

  it('counts one category when two people have an override for it', async () => {
    // The unique constraint is per user, so a workspace can hold one September
    // Rent row per editor; the service resolves one allocation per category.
    api.budgets.list.mockResolvedValue([
      budget('b-rent-1', 'rent', 900),
      budget('b-rent-2', 'rent', 1000),
    ])
    renderWithProviders(
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[]} targetRefreshing={false} />,
    )

    expect(await screen.findByText(t('budgets.copyPreviewCount', { count: 1 }))).toBeInTheDocument()
    expect(screen.getByText(t('budgets.copyPreviewTotal', { amount: '$1,000.00' }))).toBeInTheDocument()
  })

  it('refuses an adjusted amount over the budget limit, even for a category it would skip', async () => {
    api.budgets.list.mockResolvedValue([budget('b-rent', 'rent', 1_000_000_000_000)])
    const { user } = renderWithProviders(
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[budget('b-rent-oct', 'rent', 900, '2026-10-01')]} targetRefreshing={false} />,
    )

    // In keep mode the target's Rent means nothing is copied before adjusting.
    await screen.findByText(t('budgets.copyPreviewAllExist', { month: 'October 2026' }))
    await user.type(screen.getByLabelText(t('budgets.copyAdjustment')), '1000')

    // The target already has Rent, so in keep mode the copy would skip it —
    // but the service checks the limit before skipping and rejects it all.
    expect(screen.getByRole('alert')).toHaveTextContent(t('budgets.copyAmountTooLarge'))
    expect(screen.getByRole('button', { name: t('budgets.copyConfirm') })).toBeDisabled()
  })

  it('keeps confirmation disabled while the target month is refreshing', async () => {
    renderWithProviders(
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[]} targetRefreshing />,
    )

    await screen.findByText(t('budgets.copyPreviewCount', { count: 2 }))
    expect(screen.getByRole('button', { name: t('budgets.copyConfirm') })).toBeDisabled()
  })

  it('copies nothing, and cannot be confirmed, when the source month has no budgets', async () => {
    api.budgets.list.mockResolvedValue([])
    renderWithProviders(<BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[]} targetRefreshing={false} />)

    expect(await screen.findByText(t('budgets.copyPreviewEmpty'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('budgets.copyConfirm') })).toBeDisabled()
  })

  it('points to Replace when the target already holds every category of the source', async () => {
    // A recurring budget resolves in both months, so in keep mode the copy
    // would write nothing. The source is not empty, and saying it is would
    // send the person looking for the wrong problem.
    const { user } = renderWithProviders(
      <BudgetCopyDialog
        open
        onOpenChange={vi.fn()}
        targetMonth="2026-10"
        targetBudgets={[
          budget('b-rent-oct', 'rent', 900, '2026-10-01'),
          budget('b-food-oct', 'food', 300, '2026-10-01'),
        ]}
        targetRefreshing={false}
      />,
    )

    expect(await screen.findByText(t('budgets.copyPreviewAllExist', { month: 'October 2026' }))).toBeInTheDocument()
    expect(screen.queryByText(t('budgets.copyPreviewEmpty'))).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('budgets.copyConfirm') })).toBeDisabled()

    // Following the message: replacing with the adjustment copies them all.
    await user.click(screen.getByRole('radio', { name: t('budgets.copyOverwrite') }))
    await user.type(screen.getByLabelText(t('budgets.copyAdjustment')), '5')
    expect(screen.getByText(t('budgets.copyPreviewCount', { count: 2 }))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('budgets.copyConfirm') })).toBeEnabled()
  })

  it('says nothing would change when every source budget is already at the amount', async () => {
    const { user } = renderWithProviders(
      <BudgetCopyDialog
        open
        onOpenChange={vi.fn()}
        targetMonth="2026-10"
        targetBudgets={[
          budget('b-rent-oct', 'rent', 900, '2026-10-01'),
          budget('b-food-oct', 'food', 300, '2026-10-01'),
        ]}
        targetRefreshing={false}
      />,
    )

    await screen.findByText(t('budgets.copyPreviewAllExist', { month: 'October 2026' }))
    await user.click(screen.getByRole('radio', { name: t('budgets.copyOverwrite') }))

    // Replacing would write the same amounts, so nothing is copied and the
    // message says so instead of calling the source month empty.
    expect(await screen.findByText(t('budgets.copyPreviewNoChange', { month: 'October 2026' }))).toBeInTheDocument()
    expect(screen.queryByText(t('budgets.copyPreviewEmpty'))).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('budgets.copyConfirm') })).toBeDisabled()
  })
})
