import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { BudgetSpendingProgress } from '@/components/budget-spending-progress'
import i18n from '@/lib/i18n'
import { renderWithProviders } from '@/test/utils'

vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: {} } }) }))

afterEach(async () => { await i18n.changeLanguage('en') })

describe('Budget spending progress accessible name', () => {
  it.each([
    ['en', 'Rent', 'Spending by Category: Rent'],
    ['pt-BR', 'Aluguel', 'Gastos por Categoria: Aluguel'],
  ])('preserves localized spending context and the category in %s', async (language, categoryName, accessibleName) => {
    await i18n.changeLanguage(language)
    renderWithProviders(<BudgetSpendingProgress categoryName={categoryName} percentage={50} budgetAmount={900} currency="USD" />)

    expect(screen.getByRole('progressbar', { name: accessibleName })).toHaveAttribute('aria-valuenow', '50')
  })

  it('keeps the category identifiable while masking the value in privacy mode', () => {
    localStorage.setItem('privacyMode', 'true')
    renderWithProviders(<BudgetSpendingProgress categoryName="Rent" percentage={50} budgetAmount={900} currency="USD" showBudgetLabel={false} />)

    const bar = screen.getByRole('progressbar', { name: 'Spending by Category: Rent' })
    expect(bar).toHaveAttribute('aria-valuetext', '•••••')
  })
})
