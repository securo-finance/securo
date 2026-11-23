import type { TFunction } from 'i18next'

import { monthRange, shiftMonth } from '@/lib/month-utils'
import type { PluginReportDefinition, PluginReportResponse, PluginWidgetDefinition, ReportView as View, ReportViewBlock } from '@/types'

// What a query report's chart becomes in a view.
const QUERY_CHARTS: Record<PluginReportDefinition['chart'], Extract<ReportViewBlock, { type: 'chart' }>['chart'] | null> = {
  bar: 'bar',
  line: 'line',
  area: 'area',
  pie: 'donut',
  table: null,
}

/**
 * A query report's rows as a view, named in the person's language. A `card`
 * (a dashboard widget) shows the total and the chart, and the table only when
 * there's no chart.
 */
export function queryReportView(
  report: PluginReportDefinition,
  data: PluginReportResponse,
  t: TFunction,
  { card = false }: { card?: boolean } = {},
): View {
  const byMonth = data.group_by === 'month'
  const label = (row: PluginReportResponse['rows'][number]) => {
    if (row.key === 'other') return t('reports.other')
    if (row.label === null) return t(`reports.pluginReport.none.${data.group_by ?? 'category'}`)
    return row.label
  }
  const rows = data.rows
  const blocks: ReportViewBlock[] = [
    { type: 'kpi', label: t(`reports.pluginReport.total.${report.measure}`), value: data.total, format: 'currency' },
  ]
  const chart = QUERY_CHARTS[report.chart]
  const hasValues = rows.some((row) => row.value !== 0)
  if (chart && hasValues) {
    blocks.push({
      type: 'chart',
      chart: chart === 'bar' && !byMonth ? 'horizontal_bar' : chart,
      x: rows.map((row) => (byMonth ? row.key : label(row))),
      x_format: byMonth ? 'month' : 'text',
      series: [{ name: t(`reports.pluginReport.total.${report.measure}`), values: rows.map((row) => row.value) }],
      colors: byMonth ? null : rows.map((row) => row.color),
      format: 'currency',
    })
  }
  if (hasValues && !(card && chart)) {
    const share = !byMonth && report.measure !== 'net' && data.total !== 0
    blocks.push({
      type: 'table',
      columns: [
        { key: 'label', label: t(`reports.pluginReport.groupBy.${data.group_by ?? 'category'}`), format: byMonth ? 'date' : 'text' },
        { key: 'value', label: t('reports.amount'), format: 'currency' },
        ...(share ? [{ key: 'share', label: t('reports.pluginReport.share'), format: 'percent' as const }] : []),
      ],
      rows: rows.map((row) => ({
        label: byMonth ? row.key : label(row),
        value: row.value,
        ...(share ? { share: Math.round((row.value / data.total) * 1000) / 10 } : {}),
      })),
    })
  }
  return { currency: data.currency, blocks }
}

/** The dates a plugin's widget reads: its period, counted back from the month on screen. */
export function pluginWidgetWindow(month: string, period: PluginWidgetDefinition['period']): { from: string; to: string } {
  const { from, to } = monthRange(month)
  if (period === 'year_to_date') return { from: `${month.slice(0, 4)}-01-01`, to }
  if (period === 'last_12_months') return { from: monthRange(shiftMonth(month, -11)).from, to }
  return { from, to }
}
