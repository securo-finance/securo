import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

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

afterEach(async () => { await i18n.changeLanguage('en') })

describe('Budget copy dialog', () => {
  it('defaults to the previous month, previews only the missing categories and copies them', async () => {
    const onOpenChange = vi.fn()
    const { user } = renderWithProviders(
      <BudgetCopyDialog open onOpenChange={onOpenChange} targetMonth="2026-10" targetBudgets={[budget('b-rent-oct', 'rent', 900, '2026-10-01')]} />,
    )

    expect(api.budgets.list).toHaveBeenCalledWith('2026-09-01')
    expect(await screen.findByText(t('budgets.copyPreviewCount', { count: 1 }))).toBeInTheDocument()
    expect(screen.getByText(t('budgets.copyPreviewTotal', { amount: '$300.00' }))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('budgets.copyConfirm') }))

    await waitFor(() => {
      expect(api.budgets.copyMonth).toHaveBeenCalledWith({
        source_month: '2026-09-01',
        target_month: '2026-10-01',
        adjustment_percentage: 0,
        overwrite_existing: false,
      })
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('replaces the existing budgets and applies the percentage adjustment', async () => {
    const { user } = renderWithProviders(
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[budget('b-rent-oct', 'rent', 900, '2026-10-01')]} />,
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
      })
    })
  })

  it('leaves out a category the target month already resolves to at the copied amount', async () => {
    const { user } = renderWithProviders(
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[budget('b-rent-oct', 'rent', 990, '2026-10-01')]} />,
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
      <BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[]} />,
    )

    await screen.findByText(t('budgets.copyPreviewCount', { count: 2 }))
    await user.type(screen.getByLabelText(t('budgets.copyAdjustment')), '10')

    // The API saves $110.06 for each category, so the preview adds cents, not
    // the unrounded $220.11 the raw product would give.
    expect(screen.getByText(t('budgets.copyPreviewTotal', { amount: '$220.12' }))).toBeInTheDocument()
  })

  it('copies nothing, and cannot be confirmed, when the source month has no budgets', async () => {
    api.budgets.list.mockResolvedValue([])
    renderWithProviders(<BudgetCopyDialog open onOpenChange={vi.fn()} targetMonth="2026-10" targetBudgets={[]} />)

    expect(await screen.findByText(t('budgets.copyPreviewEmpty'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('budgets.copyConfirm') })).toBeDisabled()
  })
})
