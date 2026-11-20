import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import { ReportView } from '@/components/reports/ReportView'
import { renderWithProviders } from '@/test/utils'
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
