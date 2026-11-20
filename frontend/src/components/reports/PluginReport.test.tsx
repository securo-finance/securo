import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import { PluginReport } from '@/components/reports/PluginReport'
import { pluginReportEntries } from '@/lib/library'
import { renderWithProviders, t } from '@/test/utils'
import type { InstalledPlugin, PluginReportDefinition, PluginReportResponse } from '@/types'

const api = vi.hoisted(() => ({ reports: { plugin: vi.fn() } }))
vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))

const privacy = vi.hoisted(() => ({ on: false }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ privacyMode: privacy.on, mask: (v: string) => (privacy.on ? '••••' : v), MASK: '••••' }),
}))

function entry(report: Partial<PluginReportDefinition> = {}) {
  const plugin = {
    plugin_id: 'maria/casa',
    name: { en: 'Home' },
    reports: [{ key: 'r', name: { en: 'Pet spending' }, measure: 'expenses', group_by: 'category', chart: 'table', ...report }],
  } as unknown as InstalledPlugin
  return pluginReportEntries([plugin])[0]
}

function respond(rows: PluginReportResponse['rows'], report: Partial<PluginReportResponse> = {}) {
  api.reports.plugin.mockResolvedValue({
    key: 'r', measure: 'expenses', group_by: 'category', chart: 'table', currency: 'USD',
    rows, total: rows.reduce((sum, row) => sum + row.value, 0), ...report,
  })
}

beforeEach(() => {
  api.reports.plugin.mockReset()
  privacy.on = false
})

// Charts only lay out with a measured width, which jsdom never has, so these
// read the table every report shows next to (or as) its chart.
describe('PluginReport', () => {
  it("asks for the page's period and accounts, and lists what came back with its share", async () => {
    respond([
      { key: 'c1', label: 'Rent', value: 750, color: '#112233' },
      { key: 'none', label: null, value: 150, color: null },
      { key: 'other', label: null, value: 100, color: null },
    ])
    renderWithProviders(<PluginReport entry={entry()} startDate="2026-01-01" endDate="2026-11-20" accountIds={['a1']} />)

    const rent = (await screen.findByText('Rent')).closest('tr')!
    expect(within(rent).getByText('$750.00')).toBeInTheDocument()
    expect(within(rent).getByText('75%')).toBeInTheDocument()
    expect(screen.getByText(t('reports.pluginReport.none.category'))).toBeInTheDocument()
    expect(screen.getByText(t('reports.other'))).toBeInTheDocument()
    expect(screen.getByText('$1,000.00')).toBeInTheDocument()
    expect(screen.getByText(t('reports.pluginReport.total.expenses'))).toBeInTheDocument()
    expect(screen.getByText(t('library.fromPlugin', { name: 'Home' }))).toBeInTheDocument()
    expect(api.reports.plugin).toHaveBeenCalledWith('maria/casa', 'r', '2026-01-01', '2026-11-20', ['a1'])
  })

  it('names months in the app language and leaves out the share', async () => {
    respond([{ key: '2026-10', label: '2026-10', value: 40, color: null }, { key: '2026-11', label: '2026-11', value: 60, color: null }],
      { group_by: 'month' })
    renderWithProviders(<PluginReport entry={entry({ group_by: 'month', chart: 'table' })} startDate="2026-10-01" endDate="2026-11-20" accountIds={undefined} />)

    expect(await screen.findByText('Oct 2026')).toBeInTheDocument()
    expect(screen.getByText('Nov 2026')).toBeInTheDocument()
    expect(screen.queryByText(t('reports.pluginReport.share'))).not.toBeInTheDocument()
  })

  it('says when the period has nothing to show', async () => {
    respond([])
    renderWithProviders(<PluginReport entry={entry()} startDate="2026-01-01" endDate="2026-01-31" accountIds={undefined} />)
    expect(await screen.findByText(t('reports.noData'))).toBeInTheDocument()
  })

  it('hides the amounts in privacy mode', async () => {
    privacy.on = true
    respond([{ key: 'c1', label: 'Rent', value: 750, color: null }])
    renderWithProviders(<PluginReport entry={entry()} startDate="2026-01-01" endDate="2026-11-20" accountIds={undefined} />)
    expect(await screen.findByText('Rent')).toBeInTheDocument()
    expect(screen.queryByText('$750.00')).not.toBeInTheDocument()
    expect(screen.getAllByText('••••').length).toBeGreaterThan(0)
  })

  it('offers a retry when the report fails to load', async () => {
    api.reports.plugin.mockRejectedValue(new Error('boom'))
    renderWithProviders(<PluginReport entry={entry()} startDate="2026-01-01" endDate="2026-11-20" accountIds={undefined} />)
    expect(await screen.findByRole('button', { name: t('common.retry') })).toBeInTheDocument()
  })
})
