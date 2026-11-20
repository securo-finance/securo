import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Sankey,
  Scatter,
  ScatterChart,
  Tooltip,
  Treemap,
  XAxis,
  YAxis,
} from 'recharts'

import { Button } from '@/components/ui/button'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCompactCurrency, formatCurrency, ltrAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { ReportView as View, ReportViewBlock, ReportViewFormat, ReportViewWidth } from '@/types'

// The theme's series colors, as Securo's own charts use them.
const PALETTE = Array.from({ length: 12 }, (_, i) => `var(--chart-${i + 1})`)
const GAIN = 'var(--chart-income, #10B981)'
const LOSS = 'var(--chart-expense, #F43F5E)'

const tooltipStyle: React.CSSProperties = {
  backgroundColor: 'var(--popover)',
  color: 'var(--popover-foreground)',
  border: '1px solid var(--border)',
  borderRadius: 8,
  fontSize: 12,
}

const axis = { fontSize: 11 }

// Full class names, so the stylesheet keeps them: a phone stacks every block.
const WIDTHS: Record<ReportViewWidth, string> = {
  quarter: 'col-span-12 sm:col-span-6 lg:col-span-3',
  third: 'col-span-12 lg:col-span-4',
  half: 'col-span-12 lg:col-span-6',
  two_thirds: 'col-span-12 lg:col-span-8',
  full: 'col-span-12',
}

type Block<T extends ReportViewBlock['type']> = Extract<ReportViewBlock, { type: T }>
type Formatter = ReturnType<typeof useFormatter>

/** Numbers line up on the right; text and dates read from the start. */
const numeric = (format?: ReportViewFormat) => format === 'currency' || format === 'number' || format === 'percent'

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
  const xLabel = (v: string | number, format?: 'text' | 'date' | 'month' | 'number'): string =>
    format === 'month' || format === 'date' ? value(v, 'date') : String(v)
  return { value, compact, xLabel }
}

function Card({ title, children }: { title?: string | null; children: React.ReactNode }) {
  return (
    <section className="h-full bg-card rounded-xl border border-border shadow-sm p-5">
      {title && <h3 className="mb-3 text-sm font-semibold">{title}</h3>}
      {children}
    </section>
  )
}

function Empty() {
  const { t } = useTranslation()
  return <p className="py-12 text-center text-sm text-muted-foreground">{t('reports.noData')}</p>
}

function Kpi({ block, f }: { block: Block<'kpi'>; f: Formatter }) {
  const format = block.format ?? 'currency'
  const delta = block.delta
  const good = delta !== null && delta !== undefined && (block.delta_good === 'down' ? delta <= 0 : delta >= 0)
  return (
    <div className="h-full bg-card rounded-xl border border-border shadow-sm px-5 py-4">
      <p className="text-xs font-medium text-muted-foreground">{block.label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{f.value(block.value, format)}</p>
      {delta !== null && delta !== undefined && (
        <p className={cn('mt-0.5 text-xs tabular-nums', good ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400')}>
          {delta >= 0 ? '+' : ''}{f.value(delta, block.delta_format ?? format)}
        </p>
      )}
      {block.hint && <p className="mt-0.5 text-xs text-muted-foreground">{block.hint}</p>}
    </div>
  )
}

/** Bars, lines and areas over one x axis, on one or two value axes. */
function CartesianView({ block, f }: { block: Block<'chart'>; f: Formatter }) {
  const format = block.format ?? 'currency'
  const rightFormat = block.y2_format ?? format
  const defaultKind = block.chart.includes('line') ? 'line' : block.chart.includes('area') ? 'area' : 'bar'
  const stacked = block.chart === 'stacked_bar' || block.chart === 'stacked_area'
  const hasRight = block.series.some((s) => s.axis === 'right')
  const color = (s: number) => block.series[s].color ?? PALETTE[s % PALETTE.length]
  const single = block.series.length === 1

  // A projected stretch is its own key, drawn dashed; the point it starts at is in both, so they join.
  const rows = block.x.map((x, i) => {
    const row: Record<string, string | number | null> = { x: f.xLabel(x, block.x_format) }
    block.series.forEach((series, s) => {
      const from = series.projected_from == null ? -1 : block.x.indexOf(series.projected_from)
      const v = series.values[i] ?? null
      row[`s${s}`] = from >= 0 && i > from ? null : v
      if (from >= 0) row[`p${s}`] = i >= from ? v : null
    })
    return row
  })

  return (
    <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
      <XAxis dataKey="x" tick={axis} stroke="var(--muted-foreground)" />
      <YAxis yAxisId="left" width={64} tick={axis} stroke="var(--muted-foreground)" tickFormatter={(v) => f.compact(Number(v), format)} />
      {hasRight && (
        <YAxis yAxisId="right" orientation="right" width={56} tick={axis} stroke="var(--muted-foreground)" tickFormatter={(v) => f.compact(Number(v), rightFormat)} />
      )}
      <Tooltip
        contentStyle={tooltipStyle}
        cursor={{ fill: 'var(--muted)' }}
        formatter={(v, _name, item) => {
          const s = Number(String(item.dataKey ?? '').slice(1))
          return f.value(Number(v), block.series[s]?.axis === 'right' ? rightFormat : format)
        }}
      />
      {block.series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
      {(block.references ?? []).map((ref, i) => (
        <ReferenceLine
          key={`ref-${i}`}
          yAxisId={ref.axis === 'right' && hasRight ? 'right' : 'left'}
          {...(ref.y != null ? { y: ref.y } : { x: f.xLabel(ref.x as string | number, block.x_format) })}
          stroke={ref.color ?? 'var(--muted-foreground)'}
          strokeDasharray="4 4"
          label={ref.label ? { value: ref.label, position: 'insideTopRight', fontSize: 11, fill: 'var(--muted-foreground)' } : undefined}
        />
      ))}
      {block.series.flatMap((series, s) => {
        const kind = series.kind ?? defaultKind
        const yAxisId = series.axis === 'right' ? 'right' : 'left'
        const stackId = series.stack ?? (stacked ? 'stack' : undefined)
        const keys: [string, boolean][] = [[`s${s}`, false]]
        if (series.projected_from != null) keys.push([`p${s}`, true])
        return keys.map(([dataKey, projected]) => {
          const common = { key: dataKey, dataKey, yAxisId, name: series.name, legendType: projected ? ('none' as const) : undefined }
          if (kind === 'line') {
            // A line with a single point has nothing to join: show that point.
            const points = series.values.filter((v) => v !== null).length
            return (
              <Line {...common} type="monotone" stroke={color(s)} strokeWidth={2} connectNulls
                strokeDasharray={projected ? '6 4' : undefined} dot={points === 1 ? { r: 3, fill: color(s) } : false} />
            )
          }
          if (kind === 'area') {
            return (
              <Area {...common} type="monotone" stroke={color(s)} fill={color(s)} fillOpacity={projected ? 0.06 : 0.15} strokeWidth={2}
                stackId={stackId} strokeDasharray={projected ? '6 4' : undefined} connectNulls />
            )
          }
          return (
            <Bar {...common} fill={color(s)} stackId={stackId} fillOpacity={projected ? 0.45 : 1} radius={stackId ? 0 : [4, 4, 0, 0]}>
              {(series.negative_color || (single && block.colors)) &&
                series.values.map((v, i) => (
                  <Cell key={i} fill={(v !== null && v < 0 && series.negative_color) || (single && block.colors?.[i]) || color(s)} />
                ))}
            </Bar>
          )
        })
      })}
    </ComposedChart>
  )
}

function ChartView({ block, f }: { block: Block<'chart'>; f: Formatter }) {
  const format = block.format ?? 'currency'
  if (block.x.length === 0) return <Empty />
  let chart: React.ReactElement
  if (block.chart === 'pie' || block.chart === 'donut') {
    const slices = block.x.map((x, i) => ({ name: f.xLabel(x, block.x_format), value: block.series[0].values[i] ?? 0 }))
    chart = (
      <PieChart>
        <Pie data={slices} dataKey="value" nameKey="name" innerRadius={block.chart === 'donut' ? '55%' : 0} outerRadius="85%" paddingAngle={1}>
          {slices.map((slice, i) => (
            <Cell key={`${slice.name}-${i}`} fill={block.colors?.[i] ?? PALETTE[i % PALETTE.length]} stroke="var(--card)" />
          ))}
        </Pie>
        <Tooltip contentStyle={tooltipStyle} formatter={(v) => f.value(Number(v), format)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    )
  } else if (block.chart === 'horizontal_bar') {
    const rows = block.x.map((x, i) => {
      const row: Record<string, string | number | null> = { x: f.xLabel(x, block.x_format) }
      block.series.forEach((series, s) => { row[`s${s}`] = series.values[i] ?? null })
      return row
    })
    const single = block.series.length === 1
    chart = (
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
        <XAxis type="number" tick={axis} stroke="var(--muted-foreground)" tickFormatter={(v) => f.compact(Number(v), format)} />
        <YAxis type="category" dataKey="x" width={120} tick={axis} stroke="var(--muted-foreground)" />
        <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'var(--muted)' }} formatter={(v) => f.value(Number(v), format)} />
        {!single && <Legend wrapperStyle={{ fontSize: 12 }} />}
        {block.series.map((series, s) => {
          const fill = series.color ?? PALETTE[s % PALETTE.length]
          return (
            <Bar key={s} dataKey={`s${s}`} name={series.name} fill={fill} radius={[0, 4, 4, 0]} stackId={series.stack ?? undefined}>
              {(series.negative_color || (single && block.colors)) &&
                series.values.map((v, i) => (
                  <Cell key={i} fill={(v !== null && v < 0 && series.negative_color) || (single && block.colors?.[i]) || fill} />
                ))}
            </Bar>
          )
        })}
      </BarChart>
    )
  } else if (block.chart === 'scatter') {
    chart = (
      <ScatterChart margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
        <XAxis dataKey="x" type="number" tick={axis} stroke="var(--muted-foreground)" />
        <YAxis dataKey="y" width={64} tick={axis} stroke="var(--muted-foreground)" tickFormatter={(v) => f.compact(Number(v), format)} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v) => f.value(Number(v), format)} />
        {block.series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
        {block.series.map((series, s) => (
          <Scatter key={s} name={series.name} fill={series.color ?? PALETTE[s % PALETTE.length]}
            data={block.x.map((x, i) => ({ x: Number(x), y: series.values[i] })).filter((p) => p.y !== null)} />
        ))}
      </ScatterChart>
    )
  } else {
    chart = <CartesianView block={block} f={f} />
  }
  return (
    <div className="h-72" role="img" aria-label={block.title ?? block.series.map((s) => s.name).join(', ')}>
      <ResponsiveContainer width="100%" height="100%">{chart}</ResponsiveContainer>
    </div>
  )
}

function Heatmap({ block, f }: { block: Block<'heatmap'>; f: Formatter }) {
  const format = block.format ?? 'currency'
  const max = Math.max(0, ...block.values.flat().map((v) => Math.abs(v ?? 0)))
  const base = block.color ?? 'var(--chart-1)'
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-separate border-spacing-1 text-[11px]" aria-label={block.title ?? undefined}>
        <thead>
          <tr>
            <th><span className="sr-only">{block.title ?? ''}</span></th>
            {block.x.map((x, i) => (
              <th key={i} scope="col" className="px-1 text-center font-medium text-muted-foreground whitespace-nowrap">{f.xLabel(x, block.x_format)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.y.map((y, r) => (
            <tr key={r}>
              <th scope="row" className="pe-2 text-end font-medium text-muted-foreground whitespace-nowrap">{String(y)}</th>
              {block.x.map((_, c) => {
                const v = block.values[r][c]
                const strength = v === null || max === 0 ? 0 : Math.abs(v) / max
                const label = `${String(y)} · ${f.xLabel(block.x[c], block.x_format)}: ${f.value(v, format)}`
                return (
                  <td key={c} title={label} aria-label={label}
                    className="h-7 min-w-7 rounded"
                    style={{ backgroundColor: v === null ? 'var(--muted)' : base, opacity: v === null ? 0.4 : 0.12 + 0.88 * strength }} />
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

interface TreemapCellProps {
  x?: number
  y?: number
  width?: number
  height?: number
  name?: string
  fill?: string
  depth?: number
  value?: number
  f: Formatter
  format: ReportViewFormat
}

function TreemapCell({ x = 0, y = 0, width = 0, height = 0, name, fill, depth, value, f, format }: TreemapCellProps) {
  if (depth === 0) return null
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} stroke="var(--card)" strokeWidth={2} rx={4} />
      {width > 60 && height > 28 && (
        <text x={x + 6} y={y + 16} fontSize={11} fill="white">
          <tspan>{name}</tspan>
          {height > 40 && <tspan x={x + 6} dy={14} opacity={0.85}>{f.value(value ?? 0, format)}</tspan>}
        </text>
      )}
    </g>
  )
}

function TreemapView({ block, f }: { block: Block<'treemap'>; f: Formatter }) {
  const format = block.format ?? 'currency'
  const data = block.items.map((item, i) => {
    const fill = item.color ?? PALETTE[i % PALETTE.length]
    return item.children?.length
      ? { name: item.name, fill, children: item.children.map((child) => ({ name: child.name, size: child.value, fill: child.color ?? fill })) }
      : { name: item.name, fill, size: item.value ?? 0 }
  })
  return (
    <div className="h-72" role="img" aria-label={block.title ?? undefined}>
      <ResponsiveContainer width="100%" height="100%">
        <Treemap data={data} dataKey="size" isAnimationActive={false} content={<TreemapCell f={f} format={format} />}>
          <Tooltip contentStyle={tooltipStyle} formatter={(v) => f.value(Number(v), format)} />
        </Treemap>
      </ResponsiveContainer>
    </div>
  )
}

interface SankeyNodeProps {
  x?: number
  y?: number
  width?: number
  height?: number
  index?: number
  payload?: { name?: string; value?: number }
  f: Formatter
  format: ReportViewFormat
}

function SankeyNode({ x = 0, y = 0, width = 0, height = 0, index = 0, payload, f, format }: SankeyNodeProps) {
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={PALETTE[index % PALETTE.length]} rx={2} />
      <text x={x + width + 6} y={y + height / 2} dy={4} fontSize={11} fill="var(--foreground)">
        {payload?.name} · {f.value(payload?.value ?? 0, format)}
      </text>
    </g>
  )
}

function SankeyView({ block, f }: { block: Block<'sankey'>; f: Formatter }) {
  const format = block.format ?? 'currency'
  return (
    <div className="h-80" role="img" aria-label={block.title ?? undefined}>
      <ResponsiveContainer width="100%" height="100%">
        <Sankey data={{ nodes: block.nodes.map((name) => ({ name })), links: block.links }} node={<SankeyNode f={f} format={format} />}
          nodePadding={18} margin={{ top: 8, right: 180, bottom: 8, left: 8 }} link={{ stroke: 'var(--chart-1)', strokeOpacity: 0.25 }}>
          <Tooltip contentStyle={tooltipStyle} formatter={(v) => f.value(Number(v), format)} />
        </Sankey>
      </ResponsiveContainer>
    </div>
  )
}

/** Each change floats from where the running total was; a total stands on zero. */
function waterfallRows(steps: Block<'waterfall'>['steps']) {
  const rows: { x: string; base: number; size: number; value: number; kind: 'total' | 'up' | 'down' }[] = []
  let running = 0
  for (const step of steps) {
    const start = step.total ? 0 : running
    running = step.total ? step.value : running + step.value
    rows.push({
      x: step.label,
      base: Math.min(start, running),
      size: Math.abs(step.value),
      value: step.value,
      kind: step.total ? 'total' : step.value >= 0 ? 'up' : 'down',
    })
  }
  return rows
}

function Waterfall({ block, f }: { block: Block<'waterfall'>; f: Formatter }) {
  const format = block.format ?? 'currency'
  const rows = waterfallRows(block.steps)
  return (
    <div className="h-72" role="img" aria-label={block.title ?? undefined}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="x" tick={axis} stroke="var(--muted-foreground)" />
          <YAxis width={64} tick={axis} stroke="var(--muted-foreground)" tickFormatter={(v) => f.compact(Number(v), format)} />
          <Tooltip
            contentStyle={tooltipStyle}
            cursor={{ fill: 'var(--muted)' }}
            formatter={(_v, name, item) => (name === 'base' ? [null, null] : [f.value(item.payload.value, format), item.payload.x])}
          />
          <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="size" stackId="w" radius={[3, 3, 0, 0]}>
            {rows.map((row, i) => (
              <Cell key={i} fill={row.kind === 'total' ? 'var(--chart-1)' : row.kind === 'up' ? GAIN : LOSS} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function Progress({ block, f }: { block: Block<'progress'>; f: Formatter }) {
  const format = block.format ?? 'currency'
  return (
    <ul className="space-y-3">
      {block.items.map((item, i) => {
        const share = item.value / item.target
        const over = share > 1
        const bad = over && (block.over_is ?? 'bad') === 'bad'
        return (
          <li key={i}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="truncate font-medium">{item.label}</span>
              <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
                {f.value(item.value, format)} / {f.value(item.target, format)} · {f.value(Math.round(share * 1000) / 10, 'percent')}
              </span>
            </div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label={item.label}
              aria-valuenow={Math.round(share * 100)} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full rounded-full"
                style={{ width: `${Math.min(share, 1) * 100}%`, backgroundColor: bad ? LOSS : item.color ?? (over ? GAIN : 'var(--chart-1)') }} />
            </div>
            {item.hint && <p className="mt-1 text-xs text-muted-foreground">{item.hint}</p>}
          </li>
        )
      })}
    </ul>
  )
}

function Table({ block, f }: { block: Block<'table'>; f: Formatter }) {
  const { t } = useTranslation()
  const [sort, setSort] = useState<{ key: string | null; ascending: boolean }>({ key: block.sort_by ?? null, ascending: !!block.sort_ascending })
  const [page, setPage] = useState(0)
  const size = block.page_size ?? 25
  const rows = useMemo(() => {
    if (!sort.key) return block.rows
    const key = sort.key
    return [...block.rows].sort((a, b) => {
      const x = a[key]
      const y = b[key]
      if (x === null || x === undefined) return 1
      if (y === null || y === undefined) return -1
      const order = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))
      return sort.ascending ? order : -order
    })
  }, [block.rows, sort])
  const pages = Math.max(1, Math.ceil(rows.length / size))
  const shown = rows.slice(page * size, page * size + size)
  const largest = (key: string) => Math.max(0, ...block.rows.map((row) => Math.abs(Number(row[key]) || 0)))
  const hasTotals = block.columns.some((c) => c.total)
  const toggle = (key: string) => {
    setPage(0)
    setSort((prev) => (prev.key === key ? { key, ascending: !prev.ascending } : { key, ascending: false }))
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-xs text-muted-foreground">
              {block.columns.map((column) => (
                <th key={column.key} className={cn('py-2 pe-3 font-medium last:pe-0', numeric(column.format) ? 'text-end' : 'text-start')}
                  aria-sort={sort.key === column.key ? (sort.ascending ? 'ascending' : 'descending') : undefined}>
                  <button type="button" onClick={() => toggle(column.key)} className="inline-flex items-center gap-1 hover:text-foreground">
                    {column.label}
                    {sort.key === column.key && (sort.ascending ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, r) => (
              <tr key={r} className="border-b last:border-0">
                {block.columns.map((column) => {
                  const v = row[column.key]
                  const n = typeof v === 'number' ? v : null
                  const max = column.bar ? largest(column.key) : 0
                  return (
                    <td key={column.key}
                      className={cn('relative py-2 pe-3 last:pe-0', numeric(column.format) ? 'text-end tabular-nums' : 'text-start',
                        column.signed && n !== null && n < 0 && 'text-rose-600 dark:text-rose-400',
                        column.signed && n !== null && n > 0 && 'text-emerald-600 dark:text-emerald-400')}>
                      {column.bar && n !== null && max > 0 && (
                        <span aria-hidden className="absolute inset-y-1.5 end-0 rounded bg-primary/10" style={{ width: `${(Math.abs(n) / max) * 100}%` }} />
                      )}
                      <span className="relative">{f.value(v, column.format)}</span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
          {hasTotals && (
            <tfoot>
              <tr className="border-t font-semibold">
                {block.columns.map((column, c) => (
                  <td key={column.key} className={cn('py-2 pe-3 last:pe-0', numeric(column.format) ? 'text-end tabular-nums' : 'text-start')}>
                    {column.total
                      ? f.value(block.rows.reduce((sum, row) => sum + (Number(row[column.key]) || 0), 0), column.format)
                      : c === 0 ? t('reports.pluginReport.totalRow') : ''}
                  </td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      {pages > 1 && (
        <div className="mt-3 flex items-center justify-end gap-2 text-xs text-muted-foreground">
          <span>{t('reports.pluginReport.page', { page: page + 1, pages })}</span>
          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" disabled={page === 0} onClick={() => setPage(page - 1)}
            aria-label={t('reports.pluginReport.previousPage')}>
            <ChevronLeft size={14} />
          </Button>
          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}
            aria-label={t('reports.pluginReport.nextPage')}>
            <ChevronRight size={14} />
          </Button>
        </div>
      )}
    </div>
  )
}

/**
 * A report's view, drawn with Securo's own components (ADR 0028): blocks on
 * a 12-column grid (stacked on a phone) of figures, charts, heatmaps,
 * treemaps, flows, waterfalls, progress, tables and plain text. Values follow
 * privacy mode and the person's language; nothing in a view is run or fetched.
 */
export function ReportView({ view, currency }: { view: View; currency: string }) {
  const f = useFormatter(view.currency ?? currency)
  return (
    <div className="grid grid-cols-12 gap-5">
      {view.blocks.map((block, i) => {
        const width = WIDTHS[block.width ?? (block.type === 'kpi' ? 'quarter' : 'full')]
        let body: React.ReactNode
        switch (block.type) {
          case 'kpi':
            return <div key={i} className={width}><Kpi block={block} f={f} /></div>
          case 'chart':
            body = <ChartView block={block} f={f} />
            break
          case 'heatmap':
            body = <Heatmap block={block} f={f} />
            break
          case 'treemap':
            body = <TreemapView block={block} f={f} />
            break
          case 'sankey':
            body = <SankeyView block={block} f={f} />
            break
          case 'waterfall':
            body = <Waterfall block={block} f={f} />
            break
          case 'progress':
            body = <Progress block={block} f={f} />
            break
          case 'table':
            body = <Table block={block} f={f} />
            break
          case 'text':
            body = <p className="whitespace-pre-line text-sm text-muted-foreground">{block.text}</p>
            break
          default:
            return null
        }
        return (
          <div key={i} className={width}>
            <Card title={block.title}>{body}</Card>
          </div>
        )
      })}
    </div>
  )
}
