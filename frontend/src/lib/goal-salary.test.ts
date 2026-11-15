import { describe, expect, it } from 'vitest'

import type { RecurringTransaction } from '@/types'
import { defaultSalary, salaryCandidates, salaryTarget } from './goal-salary'

function recurring(overrides: Partial<RecurringTransaction>): RecurringTransaction {
  return {
    id: 'r', description: 'Income', amount: 1000, currency: 'BRL', type: 'credit',
    frequency: 'monthly', is_active: true,
    ...overrides,
  } as RecurringTransaction
}

const meal = recurring({ id: 'meal', description: 'Vale-refeição', amount: 1500 })
const homeOffice = recurring({ id: 'ho', description: 'Auxílio home office', amount: 200 })
const salary = recurring({ id: 'sal', description: 'Salário', amount: 13967.38 })

describe('salaryTarget', () => {
  it('multiplies a monthly salary by the months', () => {
    expect(salaryTarget(salary, 6)).toBe(83804.28)
  })

  it('normalizes weekly pay over a year, not four weeks a month', () => {
    expect(salaryTarget(recurring({ amount: 1000, frequency: 'weekly' }), 6)).toBe(26000)
  })
})

describe('defaultSalary', () => {
  it('prefers the income named like a salary over a larger one', () => {
    const bonus = recurring({ id: 'bonus', description: 'PLR', amount: 50000 })
    expect(defaultSalary([meal, bonus, salary, homeOffice])?.id).toBe('sal')
  })

  it('matches the spelling without an accent', () => {
    expect(defaultSalary([meal, recurring({ id: 'x', description: 'SALARIO ACME' })])?.id).toBe('x')
  })

  it('falls back to the largest monthly income', () => {
    const yearly = recurring({ id: 'yearly', description: 'Bônus', amount: 12000, frequency: 'yearly' })
    expect(defaultSalary([homeOffice, meal, yearly])?.id).toBe('meal')
  })

  it('has nothing to pick from an empty list', () => {
    expect(defaultSalary([])).toBeNull()
  })
})

describe('salaryCandidates', () => {
  it('keeps active incomes and the goal’s own paused salary', () => {
    const rent = recurring({ id: 'rent', description: 'Aluguel', type: 'debit' })
    const paused = recurring({ id: 'paused', description: 'Salário antigo', is_active: false })
    const other = recurring({ id: 'other', description: 'Freela', is_active: false })
    expect(salaryCandidates([rent, salary, paused, other], 'paused').map((r) => r.id)).toEqual(['sal', 'paused'])
  })
})
