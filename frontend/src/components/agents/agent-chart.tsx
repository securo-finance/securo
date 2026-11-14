import { memo } from 'react'
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
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatCurrency } from '@/lib/format'

/**
 * In-chat chart, rendered when an assistant message contains a fenced
 * code block tagged `securo-chart`. The body is JSON in this shape:
 *
 *   { type: "line"|"bar"|"area"|"pie", title?, currency?, data, series? }
 *
 * Multi-series example:
 *   {
 *     "type": "line",
 *     "title": "Income vs expenses",
 *     "currency": "BRL",
 *     "data": [{"x":"Jan","income":3000,"expense":1800}, ...],
 *     "series": [
 *       {"key":"income","name":"Income","color":"#10B981"},
 *       {"key":"expense","name":"Expense","color":"#F43F5E"}
 *     ]
 *   }
 *
 * Single-series shorthand (no `series` array, default key is `y`):
 *   { "type":"bar", "data":[{"x":"Food","y":500},{"x":"Rent","y":1200}] }
 *
 * Pie:
 *   { "type":"pie", "data":[{"name":"Food","value":500},{"name":"Rent","value":1200}] }
 */

// The theme's series colors, so chats look like the rest of the app.
const PALETTE = Array.from({ length: 12 }, (_, i) => `var(--chart-${i + 1})`)

type ChartType = 'line' | 'bar' | 'area' | 'pie'

interface SeriesSpec {
  key: string
  name?: string
  color?: string
}

export interface ChartSpec {
  type: ChartType
  title?: string
  subtitle?: string
  currency?: string
  x_label?: string
  y_label?: string
  data: Record<string, unknown>[]
  series?: SeriesSpec[]
}

interface Props {
  spec: ChartSpec
}

const tooltipStyle: React.CSSProperties = {
  backgroundColor: 'var(--popover)',
  color: 'var(--popover-foreground)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  fontSize: 12,
}

function formatY(v: number | string | undefined | null, currency?: string): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (currency) return formatCurrency(v, currency, undefined)
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(v)
}

/**
 * Compact axis labels — "R$100K", "R$1.2M", "12K" — so big numbers
 * actually fit in the axis gutter. Tooltips still get the full
 * precision via formatY().
 */
function formatYAxis(v: number | string | undefined | null, currency?: string): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (Math.abs(v) < 1000) {
    if (currency) {
      try {
        return new Intl.NumberFormat(undefined, {
          style: 'currency',
          currency,
          minimumFractionDigits: 0,
          maximumFractionDigits: 0,
        }).format(v)
      } catch {
        return `${currency} ${v.toFixed(0)}`
      }
    }
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(v)
  }
  if (currency) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency,
        notation: 'compact',
        maximumFractionDigits: 1,
      }).format(v)
    } catch {
      return `${currency} ${v}`
    }
  }
  return new Intl.NumberFormat(undefined, {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(v)
}

function AgentChartImpl({ spec }: Props) {
  const { t } = useTranslation()
  if (!spec || !Array.isArray(spec.data) || spec.data.length === 0) {
    return (
      <div className="my-3 rounded-md border border-dashed border-border px-3 py-4 text-xs text-muted-foreground text-center">
        {t('agents.chart.noData')}
      </div>
    )
  }

  const series: SeriesSpec[] =
    spec.series && spec.series.length > 0
      ? spec.series
      : [{ key: 'y' }]

  const seriesWithColor = series.map((s, i) => ({ ...s, color: s.color || PALETTE[i % PALETTE.length] }))

  return (
    <figure className="my-3 rounded-lg border border-border bg-card overflow-hidden">
      {(spec.title || spec.subtitle) && (
        <figcaption className="px-3 py-2 border-b border-border">
          {spec.title && <div className="text-sm font-semibold">{spec.title}</div>}
          {spec.subtitle && <div className="text-xs text-muted-foreground mt-0.5">{spec.subtitle}</div>}
        </figcaption>
      )}
      <div className="h-[260px] px-2 pt-3 pb-2">
        <ResponsiveContainer width="100%" height="100%">
          {renderChart(spec.type, spec.data, seriesWithColor, spec.currency)}
        </ResponsiveContainer>
      </div>
    </figure>
  )
}

type ResolvedSeries = { key: string; name?: string; color: string }

function renderChart(
  type: ChartType,
  data: Record<string, unknown>[],
  series: ResolvedSeries[],
  currency?: string,
) {
  if (type === 'pie') {
    // Pie expects { name, value } shape. Map a friendly fallback.
    const items = data.map((d, i) => ({
      name: String(d.name ?? d.x ?? d.label ?? `Slice ${i + 1}`),
      value: Number(d.value ?? d.y ?? 0),
    }))
    return (
      <PieChart>
        <Pie
          data={items}
          dataKey="value"
          nameKey="name"
          outerRadius={90}
          innerRadius={48}
          paddingAngle={1}
          stroke="var(--background)"
          strokeWidth={1.5}
        >
          {items.map((_, i) => (
            <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(v) => formatY(v as number | string | undefined, currency)}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
      </PieChart>
    )
  }

  const ChartCmp = type === 'bar' ? BarChart : type === 'area' ? AreaChart : LineChart

  return (
    <ChartCmp data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
      <XAxis
        dataKey="x"
        tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
        axisLine={false}
        tickLine={false}
        interval="preserveStartEnd"
      />
      <YAxis
        tickFormatter={(v) => formatYAxis(v, currency)}
        tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
        axisLine={false}
        tickLine={false}
        width={56}
        tickCount={5}
      />
      <Tooltip
        contentStyle={tooltipStyle}
        formatter={(v, name) => [formatY(v as number | string | undefined, currency), String(name ?? '')]}
      />
      {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
      {series.map((s) => {
        const common = {
          key: s.key,
          dataKey: s.key,
          name: s.name || s.key,
          stroke: s.color,
        }
        if (type === 'bar') {
          return <Bar key={s.key} dataKey={s.key} name={s.name || s.key} fill={s.color} radius={[4, 4, 0, 0]} />
        }
        if (type === 'area') {
          return (
            <Area
              {...common}
              type="monotone"
              fill={s.color}
              fillOpacity={0.18}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 3 }}
            />
          )
        }
        // line
        return (
          <Line
            {...common}
            type="monotone"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3 }}
          />
        )
      })}
    </ChartCmp>
  )
}

/** Memoized export. The whole reason this exists: every keystroke in
 *  the chat textarea triggers a parent re-render, and Recharts treats
 *  any new prop reference as new data — reanimating slices and bars.
 *  We compare specs by serialized string (cheap; specs are tiny JSON
 *  blobs the LLM emitted once) so a re-render with the same spec is a
 *  no-op. */
export const AgentChart = memo(
  AgentChartImpl,
  (prev, next) => JSON.stringify(prev.spec) === JSON.stringify(next.spec),
)
