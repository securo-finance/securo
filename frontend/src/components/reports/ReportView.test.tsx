import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import { ReportView } from '@/components/reports/ReportView'
import { renderWithProviders, t } from '@/test/utils'
import type { ReportView as View } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))

const privacy = vi.hoisted(() => ({ on: false }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ privacyMode: privacy.on, mask: (v: string) => (privacy.on ? '••••' : v), MASK: '••••' }),
}))

const VIEW: View = {
  currency: 'EUR',
  blocks: [
    { type: 'kpi', label: 'Spent', value: 1234.5, delta: -100, hint: 'vs last month' },
    { type: 'kpi', label: 'Savings rate', value: 12.5, format: 'percent' },
    {
      type: 'chart', title: 'By month', chart: 'stacked_bar', x: ['2026-10', '2026-11'], x_format: 'month',
      series: [{ name: 'Food', values: [10, 20] }, { name: 'Rent', values: [30, null] }],
    },
    {
      type: 'table', title: 'Largest',
      columns: [{ key: 'name', label: 'Name' }, { key: 'when', label: 'When', format: 'date' }, { key: 'v', label: 'Amount', format: 'currency' }],
      rows: [{ name: 'Rent', when: '2026-11-02', v: 1500 }, { name: 'Unknown', when: null, v: null }],
    },
    { type: 'text', text: '<b>Not HTML</b>\nSecond line' },
  ],
}

// Charts only lay out with a measured width, which jsdom never has: these read
// the figures, tables and text, and that the chart is there.
describe('ReportView', () => {
  it('draws figures, a chart, a table and text, in the view currency and the app language', () => {
    renderWithProviders(<ReportView view={VIEW} currency="USD" />)
    expect(screen.getByText('€1,234.50')).toBeInTheDocument()
    expect(screen.getByText('-€100.00')).toBeInTheDocument()
    expect(screen.getByText('vs last month')).toBeInTheDocument()
    expect(screen.getByText('12.5%')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'By month' })).toBeInTheDocument()
    const rent = screen.getAllByText('Rent').map((el) => el.closest('tr')).find(Boolean)!
    expect(within(rent).getByText('Nov 2, 2026')).toBeInTheDocument()
    expect(within(rent).getByText('€1,500.00')).toBeInTheDocument()
    expect(within(screen.getByText('Unknown').closest('tr')!).getAllByText('—')).toHaveLength(2)
    // Text stays text: no markup is ever parsed.
    expect(screen.getByText(/<b>Not HTML<\/b>/)).toBeInTheDocument()
    expect(document.querySelector('b')).toBeNull()
  })

  it('hides money in privacy mode, but not shares', () => {
    privacy.on = true
    renderWithProviders(<ReportView view={VIEW} currency="USD" />)
    expect(screen.queryByText('€1,234.50')).not.toBeInTheDocument()
    expect(screen.getByText('12.5%')).toBeInTheDocument()
    privacy.on = false
  })
})

describe('ReportView layout and the richer blocks', () => {
  it('lays blocks on the grid by their width, figures a quarter by default', () => {
    const { container } = renderWithProviders(
      <ReportView currency="USD" view={{ blocks: [
        { type: 'kpi', label: 'A', value: 1 },
        { type: 'text', text: 'half', width: 'half' },
        { type: 'text', text: 'full' },
      ] }} />,
    )
    const cells = container.querySelectorAll(':scope > div > div')
    expect(cells[0].className).toContain('lg:col-span-3')
    expect(cells[1].className).toContain('lg:col-span-6')
    expect(cells[2].className).toBe('col-span-12')
  })

  it('colors a change by whether a rise is good news', () => {
    renderWithProviders(
      <ReportView currency="USD" view={{ blocks: [{ type: 'kpi', label: 'Spending', value: 10, delta: 5, delta_good: 'down' }] }} />,
    )
    expect(screen.getByText('+$5.00').className).toContain('text-rose-600')
  })

  it('draws a heatmap cell per value, named for whoever reads it', () => {
    renderWithProviders(
      <ReportView currency="USD" view={{ blocks: [
        { type: 'heatmap', title: 'By weekday', x: ['Mon', 'Tue'], y: ['Oct'], values: [[10, null]] },
      ] }} />,
    )
    expect(screen.getByTitle('Oct · Mon: $10.00')).toBeInTheDocument()
    expect(screen.getByTitle('Oct · Tue: —')).toBeInTheDocument()
  })

  it('shows progress against a target, past it in red for a budget', () => {
    renderWithProviders(
      <ReportView currency="USD" view={{ blocks: [
        { type: 'progress', items: [{ label: 'Food', value: 120, target: 100 }, { label: 'Fun', value: 50, target: 100 }] },
      ] }} />,
    )
    const [food, fun] = screen.getAllByRole('progressbar')
    expect(food).toHaveAttribute('aria-valuenow', '120')
    expect(fun).toHaveAttribute('aria-valuenow', '50')
    expect((food.firstChild as HTMLElement).style.width).toBe('100%')
    expect(screen.getByText('$120.00 / $100.00 · 120%')).toBeInTheDocument()
  })

  it('draws combo, waterfall, treemap and sankey blocks as charts', () => {
    renderWithProviders(
      <ReportView currency="USD" view={{ blocks: [
        { type: 'chart', title: 'Combo', chart: 'combo', x: ['a', 'b'], y2_format: 'percent',
          series: [{ name: 'In', values: [1, 2] }, { name: 'Rate', values: [10, null], kind: 'line', axis: 'right', projected_from: 'a' }],
          references: [{ label: 'Goal', y: 5 }] },
        { type: 'waterfall', title: 'Waterfall', steps: [{ label: 'Start', value: 10, total: true }, { label: 'Out', value: -4 }] },
        { type: 'treemap', title: 'Treemap', items: [{ name: 'Home', children: [{ name: 'Rent', value: 5 }] }] },
        { type: 'sankey', title: 'Flow', nodes: ['In', 'Out'], links: [{ source: 0, target: 1, value: 5 }] },
      ] }} />,
    )
    for (const name of ['Combo', 'Waterfall', 'Treemap', 'Flow']) {
      expect(screen.getByRole('img', { name })).toBeInTheDocument()
    }
  })

  it('sorts a table by a column, adds up its totals, colors signs and pages through rows', async () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({ name: `Row ${i}`, v: i % 2 ? i : -i }))
    const { user } = renderWithProviders(
      <ReportView currency="USD" view={{ blocks: [{
        type: 'table', page_size: 5, sort_by: 'v',
        columns: [{ key: 'name', label: 'Name' }, { key: 'v', label: 'Value', format: 'currency', total: true, signed: true, bar: true }],
        rows,
      }] }} />,
    )
    const body = () => screen.getAllByRole('row').slice(1, -1).map((row) => within(row).getAllByRole('cell')[0].textContent)
    // Largest first, five a page.
    expect(body()).toEqual(['Row 11', 'Row 9', 'Row 7', 'Row 5', 'Row 3'])
    expect(screen.getByText(t('reports.pluginReport.page', { page: 1, pages: 3 }))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('reports.pluginReport.nextPage') }))
    expect(body()[0]).toBe('Row 1')
    await user.click(screen.getByRole('button', { name: 'Name' }))
    expect(body()).toEqual(['Row 9', 'Row 8', 'Row 7', 'Row 6', 'Row 5'])
    const total = screen.getAllByRole('row').at(-1)!
    expect(within(total).getByText(t('reports.pluginReport.totalRow'))).toBeInTheDocument()
    expect(within(total).getByText('$6.00')).toBeInTheDocument()
    expect(screen.getByText('-$8.00').closest('td')?.className).toContain('text-rose-600')
  })
})
