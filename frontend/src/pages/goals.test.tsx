import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import { renderWithProviders } from '@/test/utils'
import GoalsPage from './goals'
import type { Goal, RecurringTransaction } from '@/types'

const api = vi.hoisted(() => ({
  goals: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  accounts: { list: vi.fn() },
  assets: { list: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
  recurring: { list: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'pt-BR',
  useDateLocale: () => 'pt-BR',
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

function income(overrides: Partial<RecurringTransaction>): RecurringTransaction {
  return {
    id: 'r', description: 'Income', amount: 1000, currency: 'BRL', type: 'credit',
    frequency: 'monthly', is_active: true,
    ...overrides,
  } as RecurringTransaction
}

function text(el: Element | null): string {
  return (el?.textContent ?? '').replace(/\s/g, ' ')
}

describe('GoalsPage salary targets', () => {
  beforeEach(() => {
    Object.values(api).forEach((group) => Object.values(group).forEach((fn) => fn.mockReset()))
    api.accounts.list.mockResolvedValue([])
    api.assets.list.mockResolvedValue([])
    api.assetGroups.list.mockResolvedValue([])
    api.currencies.list.mockResolvedValue([{ code: 'BRL', name: 'Real', flag: '' }])
    api.recurring.list.mockResolvedValue([
      income({ id: 'meal', description: 'Vale-refeição', amount: 1500 }),
      income({ id: 'salary', description: 'Salário', amount: 13967.38 }),
      income({ id: 'ho', description: 'Auxílio home office', amount: 200 }),
      income({ id: 'rent', description: 'Aluguel', amount: 3000, type: 'debit' }),
    ])
  })

  it('creates a goal sized in months of the preselected salary', async () => {
    api.goals.list.mockResolvedValue([])
    api.goals.create.mockResolvedValue({})

    const { user } = renderWithProviders(<GoalsPage />)
    await user.click(await screen.findByRole('button', { name: 'Add Goal' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'Reserva')
    await user.click(within(dialog).getByRole('button', { name: 'Multiple of net salary' }))

    const source = await within(dialog).findByRole('combobox', { name: 'Net salary' })
    expect((source as HTMLSelectElement).value).toBe('salary')
    expect(within(source).queryByText(/Aluguel/)).toBeNull()

    await user.click(within(dialog).getByRole('button', { name: '12' }))
    expect(text(within(dialog).getByText(/^12 ×/))).toBe('12 × R$ 13.967,38 = R$ 167.608,56')
    await user.click(within(dialog).getByRole('button', { name: '6' }))
    expect(text(within(dialog).getByText(/^6 ×/))).toBe('6 × R$ 13.967,38 = R$ 83.804,28')

    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(api.goals.create).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Reserva',
      salary_recurring_id: 'salary',
      salary_months: 6,
      currency: 'BRL',
    }))
    expect(api.goals.create.mock.calls[0][0]).not.toHaveProperty('target_amount')
  })

  it('shows the target and coverage in salaries on the card', async () => {
    api.goals.list.mockResolvedValue([{
      id: 'g', name: 'Reserva', status: 'active', currency: 'BRL', percentage: 70,
      current_amount: '58662.99', target_amount: '83804.28', target_date: null,
      tracking_type: 'manual', on_track: null, monthly_contribution: null, icon: null, color: null,
      salary_recurring_id: 'salary', salary_months: 6, salary_name: 'Salário', monthly_salary: '13967.38',
    } as unknown as Goal])

    renderWithProviders(<GoalsPage />)

    expect(await screen.findByText('Target: 6 net salaries')).toBeInTheDocument()
    expect(screen.getByText('4,2 of 6 salaries')).toBeInTheDocument()
  })
})
