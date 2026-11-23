import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { CashflowSankey } from '@/components/reports/CashflowSankey'
import { renderWithProviders } from '@/test/utils'
import type { ReportCompositionItem } from '@/types'

function item(group: string, key: string, label: string, value: number): ReportCompositionItem {
  return { group, key, label, value, color: '#000000' }
}

function render(composition: ReportCompositionItem[]) {
  renderWithProviders(<CashflowSankey composition={composition} currency="USD" locale="en-US" />)
}

// The chart only lays out with a measured width, which jsdom never has, so
// these tests read the accessible table the component renders from the same
// nodes.
describe('CashflowSankey investment withdrawals (#1418)', () => {
  it('draws a withdrawal as a source, so spending it is no deficit', () => {
    render([
      item('income', 'salary', 'Salary', 1000),
      item('withdrawals', 'invest', 'Investments', 500),
      item('expenses', 'rent', 'Rent', 1400),
    ])

    expect(screen.getByText('Investment withdrawals')).toBeInTheDocument()
    expect(screen.queryByText('Deficit')).not.toBeInTheDocument()
    expect(screen.getByText('Surplus')).toBeInTheDocument()
  })

  it('folds every withdrawal category into one node', () => {
    render([
      item('income', 'salary', 'Salary', 1000),
      item('withdrawals', 'stocks', 'Stocks', 200),
      item('withdrawals', 'bonds', 'Bonds', 300),
      item('expenses', 'rent', 'Rent', 900),
    ])

    expect(screen.getAllByText('Investment withdrawals')).toHaveLength(1)
    expect(screen.queryByText('Stocks')).not.toBeInTheDocument()
    expect(screen.queryByText('Bonds')).not.toBeInTheDocument()
  })

  it('still shows a deficit the withdrawals do not cover', () => {
    render([
      item('income', 'salary', 'Salary', 1000),
      item('withdrawals', 'invest', 'Investments', 100),
      item('expenses', 'rent', 'Rent', 1400),
    ])

    expect(screen.getByText('Deficit')).toBeInTheDocument()
  })

  it('charts a month with only a withdrawal', () => {
    render([item('withdrawals', 'invest', 'Investments', 100)])

    expect(screen.queryByText('No data available for this period')).not.toBeInTheDocument()
    expect(screen.getByText('Investment withdrawals')).toBeInTheDocument()
  })
})

describe('CashflowSankey accessible table (#252)', () => {
  it('clips the table with a wrapper, so it cannot widen the page on a phone', () => {
    render([
      item('income', 'salary', 'Salary', 1000),
      item('expenses', 'rent', 'Rent', 900),
    ])

    const table = screen.getByRole('table')
    // `overflow` does not clip a table box: with `sr-only` on the table itself
    // the cells kept their natural width and the Reports page measured 504px
    // on a 390px screen. The wrapper is what keeps it off the layout.
    expect(table).not.toHaveClass('sr-only')
    expect(table.parentElement).toHaveClass('sr-only')
  })
})
