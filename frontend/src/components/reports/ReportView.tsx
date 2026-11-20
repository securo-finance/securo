import { useTranslation } from 'react-i18next'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCompactCurrency, formatCurrency, ltrAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { ReportView as View, ReportViewBlock, ReportViewFormat } from '@/types'

// The theme's series colors, as Securo's own charts use them.
const PALETTE = Array.from({ length: 12 }, (_, i) => `var(--chart-${i + 1})`)

const tooltipStyle: React.CSSProperties = {
  backgroundColor: 'var(--popover)',
  color: 'var(--popover-foreground)',
  border: '1px solid var(--border)',
  borderRadius: 8,
  fontSize: 12,
}

const axis = { fontSize: 11 }

/** Numbers line up on the right; text and dates read from the start. */
const numeric = (format?: ReportViewFormat) => format === 'currency' || format === 'number' || format === 'percent'

type ChartBlock = Extract<ReportViewBlock, { type: 'chart' }>

function useFormatter(currency: string) {
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()

  const value = (v: string | number | null | undefined, format: ReportViewFormat = 'text'): string => {
    if (v === null || v === undefined || v === '') return '—'
    if (format === 'text') return String(v)
    if (format === 'date') {
      const text = String(v)
      const [y, m, d] = text.split('-').map(Number)
      if (!y || !m) return text
      return new Date(y, m - 1, d || 1).toLocaleDateString(dateLocale, d ? { dateStyle: 'medium' } : { month: 'short', year: 'numeric' })
    }
    const n = typeof v === 'number' ? v : Number(v)
    if (!Number.isFinite(n)) return String(v)
    if (format === 'currency') return mask(ltrAmount(formatCurrency(n, currency, locale)))
    if (format === 'percent') return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(n)}%`
    return mask(new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(n))
  }
  const compact = (n: number, format: ReportViewFormat = 'number'): string => {
    if (format === 'currency') return mask(formatCompactCurrency(n, currency, locale))
    if (format === 'percent') return `${n}%`
    return mask(new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }).format(n))
  }
  const xLabel = (v: string | number, format: ChartBlock['x_format']): string => {
    if (format === 'month' || format === 'date') return value(v, 'date')
    return String(v)
  }
  return { value, compact, xLabel }
}

function Card({ title, className, children }: { title?: string; className?: string; children: React.ReactNode }) {
  return (
    <section className={cn('bg-card rounded-xl border border-border shadow-sm p-5', className)}>
      {title && <h3 className="mb-3 text-sm font-semibold">{title}</h3>}
      {children}
    </section>
  )
}

function ChartView({ block, currency }: { block: ChartBlock; currency: string }) {
  const { t } = useTranslation()
  const { value, compact, xLabel } = useFormatter(currency)
  const format = block.format ?? 'currency'
  const rows = block.x.map((x, i) => {
    const row: Record<string, string | number | null> = { x: xLabel(x, block.x_format) }
    block.series.forEach((series, s) => { row[`s${s}`] = series.values[i] ?? null })
    return row
  })
  const color = (s: number) => block.series[s].color ?? PALETTE[s % PALETTE.length]
  const tooltip = <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'var(--muted)' }} formatter={(v) => value(Number(v), format)} />
  const yAxis = <YAxis width={64} tick={axis} stroke="var(--muted-foreground)" tickFormatter={(v) => compact(Number(v), format)} />
  const xAxis = <XAxis dataKey="x" tick={axis} stroke="var(--muted-foreground)" />
  const grid = <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
  const legend = block.series.length > 1 ? <Legend wrapperStyle={{ fontSize: 12 }} /> : null
  const margin = { top: 8, right: 8, bottom: 0, left: 0 }

  let chart: React.ReactElement
  switch (block.chart) {
    case 'pie':
    case 'donut': {
      const slices = block.x.map((x, i) => ({ name: xLabel(x, block.x_format), value: block.series[0].values[i] ?? 0 }))
      chart = (
        <PieChart>
          <Pie data={slices} dataKey="value" nameKey="name" innerRadius={block.chart === 'donut' ? '55%' : 0} outerRadius="85%" paddingAngle={1}>
            {slices.map((slice, i) => (
              <Cell key={`${slice.name}-${i}`} fill={block.colors?.[i] ?? PALETTE[i % PALETTE.length]} stroke="var(--card)" />
            ))}
          </Pie>
          <Tooltip contentStyle={tooltipStyle} formatter={(v) => value(Number(v), format)} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
        </PieChart>
      )
      break
    }
    case 'line':
      chart = (
        <LineChart data={rows} margin={margin}>
          {grid}{xAxis}{yAxis}{tooltip}{legend}
          {block.series.map((series, s) => (
            <Line key={s} type="monotone" dataKey={`s${s}`} name={series.name} stroke={color(s)} strokeWidth={2} dot={false} connectNulls />
          ))}
        </LineChart>
      )
      break
    case 'area':
    case 'stacked_area':
      chart = (
        <AreaChart data={rows} margin={margin}>
          {grid}{xAxis}{yAxis}{tooltip}{legend}
          {block.series.map((series, s) => (
            <Area key={s} type="monotone" dataKey={`s${s}`} name={series.name} stroke={color(s)} fill={color(s)} fillOpacity={0.15} strokeWidth={2}
              stackId={block.chart === 'stacked_area' ? 'stack' : undefined} />
          ))}
        </AreaChart>
      )
      break
    case 'horizontal_bar':
      chart = (
        <BarChart data={rows} layout="vertical" margin={{ ...margin, top: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
          <XAxis type="number" tick={axis} stroke="var(--muted-foreground)" tickFormatter={(v) => compact(Number(v), format)} />
          <YAxis type="category" dataKey="x" width={120} tick={axis} stroke="var(--muted-foreground)" />
          {tooltip}{legend}
          {block.series.map((series, s) => (
            <Bar key={s} dataKey={`s${s}`} name={series.name} fill={color(s)} radius={[0, 4, 4, 0]}>
              {block.series.length === 1 && block.colors && block.x.map((_, i) => (
                <Cell key={i} fill={block.colors?.[i] ?? color(0)} />
              ))}
            </Bar>
          ))}
        </BarChart>
      )
      break
    case 'scatter':
      chart = (
        <ScatterChart margin={margin}>
          {grid}
          <XAxis dataKey="x" type="number" tick={axis} stroke="var(--muted-foreground)" />
          {yAxis}
          <Tooltip contentStyle={tooltipStyle} formatter={(v) => value(Number(v), format)} />
          {legend}
          {block.series.map((series, s) => (
            <Scatter key={s} name={series.name} fill={color(s)}
              data={block.x.map((x, i) => ({ x: Number(x), [`s${s}`]: series.values[i] }))} dataKey={`s${s}`} />
          ))}
        </ScatterChart>
      )
      break
    default: {
      const stacked = block.chart === 'stacked_bar'
      chart = (
        <BarChart data={rows} margin={margin}>
          {grid}{xAxis}{yAxis}{tooltip}{legend}
          {block.series.map((series, s) => (
            <Bar key={s} dataKey={`s${s}`} name={series.name} fill={color(s)} stackId={stacked ? 'stack' : undefined}
              radius={stacked && s < block.series.length - 1 ? 0 : [4, 4, 0, 0]} />
          ))}
        </BarChart>
      )
    }
  }

  if (block.x.length === 0) {
    return <p className="py-16 text-center text-sm text-muted-foreground">{t('reports.noData')}</p>
  }
  return (
    <div className="h-72" role="img" aria-label={block.title ?? block.series.map((s) => s.name).join(', ')}>
      <ResponsiveContainer width="100%" height="100%">{chart}</ResponsiveContainer>
    </div>
  )
}

/**
 * A report's view, drawn with Securo's own components (ADR 0028): headline
 * figures, charts, tables and plain text. Values follow privacy mode and the
 * person's language; nothing in a view is run or fetched.
 */
export function ReportView({ view, currency }: { view: View; currency: string }) {
  const { value } = useFormatter(view.currency ?? currency)
  const kpis = view.blocks.filter((b) => b.type === 'kpi')
  const rest = view.blocks.filter((b) => b.type !== 'kpi')

  return (
    <div className="space-y-5">
      {kpis.length > 0 && (
        <div className={cn('grid gap-3', kpis.length > 1 && 'sm:grid-cols-2', kpis.length > 2 && 'lg:grid-cols-4')}>
          {kpis.map((block, i) => {
            if (block.type !== 'kpi') return null
            const deltaFormat = block.delta_format ?? block.format ?? 'currency'
            return (
              <div key={i} className="bg-card rounded-xl border border-border shadow-sm px-5 py-4">
                <p className="text-xs font-medium text-muted-foreground">{block.label}</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums">{value(block.value, block.format ?? 'currency')}</p>
                {(block.delta !== null && block.delta !== undefined) && (
                  <p className={cn('mt-0.5 text-xs tabular-nums', block.delta >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400')}>
                    {block.delta >= 0 ? '+' : ''}{value(block.delta, deltaFormat)}
                  </p>
                )}
                {block.hint && <p className="mt-0.5 text-xs text-muted-foreground">{block.hint}</p>}
              </div>
            )
          })}
        </div>
      )}
      {rest.map((block, i) => {
        if (block.type === 'chart') {
          return (
            <Card key={i} title={block.title}>
              <ChartView block={block} currency={view.currency ?? currency} />
            </Card>
          )
        }
        if (block.type === 'table') {
          return (
            <Card key={i} title={block.title} className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-xs text-muted-foreground">
                    {block.columns.map((column) => (
                      <th key={column.key} className={cn('py-2 pe-3 font-medium last:pe-0', numeric(column.format) ? 'text-end' : 'text-start')}>
                        {column.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, r) => (
                    <tr key={r} className="border-b last:border-0">
                      {block.columns.map((column) => (
                        <td key={column.key} className={cn('py-2 pe-3 last:pe-0', numeric(column.format) ? 'text-end tabular-nums' : 'text-start')}>
                          {value(row[column.key], column.format)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )
        }
        if (block.type === 'text') {
          return (
            <Card key={i} title={block.title}>
              <p className="whitespace-pre-line text-sm text-muted-foreground">{block.text}</p>
            </Card>
          )
        }
        return null
      })}
    </div>
  )
}
