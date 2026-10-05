import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { useState } from 'react'
import { IncomeExpenseStatement, StatementControls } from '@/components/reports/IncomeExpenseStatement'
import {
  STATEMENT_SETTINGS_KEY,
  loadStatementSettings,
  saveStatementSettings,
  sortStatementRows,
  statementChange,
  type StatementSettings,
} from '@/lib/category-statement'
import { renderWithProviders, t } from '@/test/utils'
import type { CategoryStatementResponse, CategoryStatementRow } from '@/types'

const api = vi.hoisted(() => ({
  reports: { categoryStatement: vi.fn() },
}))

vi.mock('@/lib/api', () => ({ reports: api.reports }))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

vi.mock('@/components/transaction-drill-down', () => ({
  TransactionDrillDown: ({ filter }: { filter: unknown }) =>
    filter ? <pre data-testid="drill-down">{JSON.stringify(filter)}</pre> : null,
}))

// The reports page owns the month and settings; mirror that wiring here.
function Statement({ accountIds }: { accountIds?: string[] }) {
  const [month, setMonth] = useState('2026-09')
  const [settings, setSettings] = useState(loadStatementSettings)
  const update = (next: StatementSettings) => {
    setSettings(next)
    saveStatementSettings(next)
  }
  return (
    <>
      <StatementControls month={month} onMonthChange={setMonth} settings={settings} onSettingsChange={update} />
      <IncomeExpenseStatement accountIds={accountIds} month={month} settings={settings} />
    </>
  )
}

function row(overrides: Partial<CategoryStatementRow>): CategoryStatementRow {
  return {
    key: 'k',
    kind: 'category',
    label: 'Label',
    icon: 'circle-help',
    color: '#000000',
    category_ids: [],
    txn_type: null,
    values: [],
    children: [],
    ...overrides,
  }
}

const food = row({ key: 'food', label: 'Groceries', category_ids: ['food'], values: [-300, -200, -100, -100] })
const dining = row({ key: 'dining', label: 'Dining', category_ids: ['dining'], values: [-50, -100, 0, 0] })

const statement: CategoryStatementResponse = {
  currency: 'USD',
  months: ['2026-09', '2026-08', '2026-07', '2026-06'],
  income: {
    totals: [1000, 2000, 2000, 2000],
    rows: [row({ key: 'salary', label: 'Salary', category_ids: ['salary'], values: [1000, 2000, 2000, 2000] })],
  },
  expenses: {
    totals: [-350, -300, -100, -100],
    rows: [
      row({
        key: 'living', kind: 'group', label: 'Living', category_ids: ['food', 'dining'],
        values: [-350, -300, -100, -100], children: [food, dining],
      }),
    ],
  },
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 12, 12))
  vi.clearAllMocks()
  localStorage.clear()
  api.reports.categoryStatement.mockResolvedValue(statement)
})

afterEach(() => vi.useRealTimers())

describe('statementChange', () => {
  it('treats less spending as good and less income as bad', () => {
    expect(statementChange(-8419.76, -12798.78, 'expenses')).toEqual({
      percent: expect.closeTo(-34.2, 1),
      good: true,
    })
    expect(statementChange(8742.96, 26063.49, 'income')).toEqual({
      percent: expect.closeTo(-66.5, 1),
      good: false,
    })
  })

  it('has nothing to compare against an empty or missing month', () => {
    expect(statementChange(-100, 0, 'expenses')).toBeNull()
    expect(statementChange(-100, undefined, 'expenses')).toBeNull()
  })
})

describe('sortStatementRows', () => {
  it('sorts rows and their children by the selected month magnitude', () => {
    const rows = [
      row({ key: 'a', values: [-10] }),
      row({ key: 'b', values: [-30], children: [row({ key: 'b1', values: [-5] }), row({ key: 'b2', values: [-25] })] }),
      row({ key: 'c', values: [20] }),
    ]
    expect(sortStatementRows(rows, 'default')).toBe(rows)
    expect(sortStatementRows(rows, 'asc').map((r) => r.key)).toEqual(['a', 'c', 'b'])
    const desc = sortStatementRows(rows, 'desc')
    expect(desc.map((r) => r.key)).toEqual(['b', 'c', 'a'])
    expect(desc[0].children.map((r) => r.key)).toEqual(['b2', 'b1'])
  })
})

describe('IncomeExpenseStatement', () => {
  it('requests one month beyond the visible columns and renders the totals', async () => {
    renderWithProviders(<Statement accountIds={['acc-1']} />)

    await waitFor(() =>
      expect(api.reports.categoryStatement).toHaveBeenCalledWith('2026-09', 4, ['acc-1']),
    )
    expect(await screen.findByText(t('reports.statementExpenseTotal'))).toBeInTheDocument()
    // One header per section; June is only fetched for July's change.
    expect(screen.getAllByRole('columnheader', { name: 'September 2026' })).toHaveLength(2)
    expect(screen.queryByRole('columnheader', { name: 'June 2026' })).not.toBeInTheDocument()
    // Oldest month on the left, the selected one on the right.
    const incomeHeader = screen.getAllByRole('row')[0]
    expect(within(incomeHeader).getAllByRole('columnheader').map((th) => th.textContent)).toEqual([
      t('reports.income'), 'July 2026', 'August 2026', 'September 2026',
    ])
    expect(screen.getByText('Living')).toBeInTheDocument()
    // Net result: 1000 - 350.
    expect(screen.getByText('$650.00')).toBeInTheDocument()
  })

  it('expands a group into its categories', async () => {
    const { user } = renderWithProviders(<Statement />)
    await screen.findByText('Living')
    expect(screen.queryByText('Groceries')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('reports.statementToggleGroup', { name: 'Living' }) }))

    expect(screen.getByText('Groceries')).toBeInTheDocument()
    expect(screen.getByText('Dining')).toBeInTheDocument()
  })

  it('opens the realized transactions of a group for the clicked month', async () => {
    const { user } = renderWithProviders(<Statement />)
    const living = (await screen.findByText('Living')).closest('tr')!

    // The selected month is the rightmost column.
    await user.click(within(living).getAllByRole('button', { name: t('reports.statementViewTransactions') }).at(-1)!)

    const filter = JSON.parse(screen.getByTestId('drill-down').textContent!)
    expect(filter).toMatchObject({
      category_ids: ['food', 'dining'],
      from: '2026-09-01',
      to: '2026-09-30',
      realizedOnly: true,
    })
  })

  it('remembers the chosen number of columns', async () => {
    const { user } = renderWithProviders(<Statement />)
    await screen.findByText('Living')

    await user.click(screen.getByRole('button', { name: t('reports.statementSettings') }))
    await user.click(screen.getByRole('button', { name: '5' }))

    await waitFor(() =>
      expect(api.reports.categoryStatement).toHaveBeenLastCalledWith('2026-09', 6, undefined),
    )
    expect(JSON.parse(localStorage.getItem(STATEMENT_SETTINGS_KEY)!)).toMatchObject({ columns: 5 })
  })

  it('can show the selected month alone', async () => {
    const { user } = renderWithProviders(<Statement />)
    await screen.findByText('Living')

    await user.click(screen.getByRole('button', { name: t('reports.statementSettings') }))
    await user.click(screen.getByRole('button', { name: '1' }))

    await waitFor(() =>
      expect(api.reports.categoryStatement).toHaveBeenLastCalledWith('2026-09', 2, undefined),
    )
    await waitFor(() =>
      expect(screen.queryByRole('columnheader', { name: 'August 2026' })).not.toBeInTheDocument(),
    )
  })

  it('hides the percentage change when turned off', async () => {
    const { user } = renderWithProviders(<Statement />)
    await screen.findByText('Living')
    expect(screen.getAllByText('-50%').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: t('reports.statementSettings') }))
    await user.click(screen.getByRole('switch'))

    expect(screen.queryByText('-50%')).not.toBeInTheDocument()
  })
})
