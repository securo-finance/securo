import { describe, expect, it } from 'vitest'

import { pluginWidgetWindow, queryReportView } from './plugin-report-view'
import type { PluginReportDefinition, PluginReportResponse } from '@/types'

const t = ((key: string) => key) as never

describe('pluginWidgetWindow', () => {
  it("counts a widget's period back from the month on screen", () => {
    expect(pluginWidgetWindow('2026-02', 'month')).toEqual({ from: '2026-02-01', to: '2026-02-28' })
    expect(pluginWidgetWindow('2026-05', 'year_to_date')).toEqual({ from: '2026-01-01', to: '2026-05-31' })
    expect(pluginWidgetWindow('2026-03', 'last_12_months')).toEqual({ from: '2025-04-01', to: '2026-03-31' })
  })
})

describe('queryReportView', () => {
  const report: PluginReportDefinition = { key: 'r', name: { en: 'R' }, measure: 'expenses', group_by: 'category', chart: 'bar' }
  const data: PluginReportResponse = {
    key: 'r', code: false, measure: 'expenses', group_by: 'category', chart: 'bar', currency: 'USD', total: 10,
    rows: [{ key: 'a', label: 'A', value: 10, color: null }],
  }

  it('shows the total, the chart and the table on the reports page', () => {
    expect(queryReportView(report, data, t).blocks.map((block) => block.type)).toEqual(['kpi', 'chart', 'table'])
  })

  it('leaves the table out of a widget that has a chart, and keeps it when the table is the chart', () => {
    expect(queryReportView(report, data, t, { card: true }).blocks.map((block) => block.type)).toEqual(['kpi', 'chart'])
    const table = { ...report, chart: 'table' as const }
    expect(queryReportView(table, data, t, { card: true }).blocks.map((block) => block.type)).toEqual(['kpi', 'table'])
  })
})
