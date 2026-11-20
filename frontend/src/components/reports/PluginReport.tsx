import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertCircle } from 'lucide-react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { Skeleton } from '@/components/ui/skeleton'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { reports } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { formatCompactCurrency, formatCurrency, ltrAmount } from '@/lib/format'
import type { PluginReportEntry } from '@/lib/library'
import { localized } from '@/lib/localized-text'
import type { PluginReportResponse } from '@/types'

// The theme's series colors, as Securo's own charts use them.
const PALETTE = Array.from({ length: 12 }, (_, i) => `var(--chart-${i + 1})`)

const tooltipStyle: React.CSSProperties = {
  backgroundColor: 'var(--popover)',
  color: 'var(--popover-foreground)',
  border: '1px solid var(--border)',
  borderRadius: 8,
  fontSize: 12,
}

/** "2026-11" as "Nov 2026", in the app's language. */
function shortMonth(yearMonth: string, locale: string): string {
  const [y, m] = yearMonth.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleDateString(locale, { month: 'short', year: 'numeric' })
}

interface Row {
  key: string
  label: string
  value: number
  color: string
}

/**
 * A report a plugin declares (ADR 0028): Securo adds it up for the page's
 * period and accounts, and draws it here with its own components. Nothing of
 * the plugin runs.
 */
export function PluginReport({
  entry,
  startDate,
  endDate,
  accountIds,
}: {
  entry: PluginReportEntry
  startDate: string | undefined
  endDate: string | undefined
  accountIds: string[] | undefined
}) {
  const { t, i18n } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { report } = entry

  const { data, isLoading, isError, error, refetch } = useQuery<PluginReportResponse>({
    queryKey: ['reports', 'plugin', entry.id, startDate ?? null, endDate ?? null, accountIds ?? null],
    queryFn: () => reports.plugin(entry.plugin.id, report.key, startDate!, endDate!, accountIds),
    enabled: !!startDate && !!endDate,
  })

  const currency = data?.currency ?? 'USD'
  const money = (value: number) => mask(ltrAmount(formatCurrency(value, currency, locale)))
  const label = (row: PluginReportResponse['rows'][number]) => {
    if (row.key === 'other') return t('reports.other')
    if (row.label === null) return t(`reports.pluginReport.none.${report.group_by}`)
    return report.group_by === 'month' ? shortMonth(row.key, dateLocale) : row.label
  }
  const rows: Row[] = (data?.rows ?? []).map((row, i) => ({
    key: row.key,
    label: label(row),
    value: row.value,
    color: row.color ?? PALETTE[i % PALETTE.length],
  }))
  const byMonth = report.group_by === 'month'
  const empty = rows.length === 0 || rows.every((row) => row.value === 0)

  return (
    <div className="space-y-5">
      <div className="bg-card rounded-xl border border-border shadow-sm px-5 py-4">
        {isLoading ? (
          <Skeleton className="h-14 w-56" />
        ) : (
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-medium text-muted-foreground">
                {t(`reports.pluginReport.total.${report.measure}`)}
              </p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{money(data?.total ?? 0)}</p>
            </div>
            <div className="min-w-0 text-end text-xs text-muted-foreground">
              {entry.plugin.description && (
                <p className="truncate">{localized(entry.plugin.description, i18n.language)}</p>
              )}
              <p>{t('library.fromPlugin', { name: localized(entry.plugin.name, i18n.language) })}</p>
            </div>
          </div>
        )}
      </div>

      {isError && (
        <div className="flex items-center justify-between gap-3 bg-rose-50 dark:bg-rose-500/10 border border-rose-200 dark:border-rose-500/30 rounded-lg px-4 py-2.5">
          <div className="flex items-center gap-2.5 min-w-0">
            <AlertCircle size={16} className="shrink-0 text-rose-600 dark:text-rose-400" />
            <span className="text-sm text-rose-900 dark:text-rose-200 truncate">
              {extractApiError(error, t('reports.loadError'))}
            </span>
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            className="shrink-0 text-sm font-semibold text-rose-600 dark:text-rose-400 hover:underline"
          >
            {t('common.retry')}
          </button>
        </div>
      )}

      {!isError && (
        <div className="bg-card rounded-xl border border-border shadow-sm p-5">
          {isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : empty ? (
            <p className="py-16 text-center text-sm text-muted-foreground">{t('reports.noData')}</p>
          ) : (
            <>
              {report.chart !== 'table' && (
                <div className="h-72" role="img" aria-label={localized(entry.plugin.title, i18n.language)}>
                  <ResponsiveContainer width="100%" height="100%">
                    {report.chart === 'pie' ? (
                      <PieChart>
                        <Pie data={rows} dataKey="value" nameKey="label" innerRadius="55%" outerRadius="85%" paddingAngle={1}>
                          {rows.map((row) => (
                            <Cell key={row.key} fill={row.color} stroke="var(--card)" />
                          ))}
                        </Pie>
                        <Tooltip contentStyle={tooltipStyle} formatter={(v) => money(Number(v))} />
                      </PieChart>
                    ) : report.chart === 'line' ? (
                      <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                        <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                        <YAxis width={64} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={(v) => mask(formatCompactCurrency(Number(v), currency, locale))} />
                        <Tooltip contentStyle={tooltipStyle} formatter={(v) => money(Number(v))} />
                        <Line type="monotone" dataKey="value" stroke="var(--chart-1)" strokeWidth={2} dot={false} />
                      </LineChart>
                    ) : report.chart === 'area' ? (
                      <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                        <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                        <YAxis width={64} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={(v) => mask(formatCompactCurrency(Number(v), currency, locale))} />
                        <Tooltip contentStyle={tooltipStyle} formatter={(v) => money(Number(v))} />
                        <Area type="monotone" dataKey="value" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.15} strokeWidth={2} />
                      </AreaChart>
                    ) : byMonth ? (
                      <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                        <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                        <YAxis width={64} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={(v) => mask(formatCompactCurrency(Number(v), currency, locale))} />
                        <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'var(--muted)' }} formatter={(v) => money(Number(v))} />
                        <Bar dataKey="value" fill="var(--chart-1)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    ) : (
                      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                        <XAxis type="number" tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={(v) => mask(formatCompactCurrency(Number(v), currency, locale))} />
                        <YAxis type="category" dataKey="label" width={120} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                        <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'var(--muted)' }} formatter={(v) => money(Number(v))} />
                        <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                          {rows.map((row) => (
                            <Cell key={row.key} fill={row.color} />
                          ))}
                        </Bar>
                      </BarChart>
                    )}
                  </ResponsiveContainer>
                </div>
              )}
              <table className={report.chart === 'table' ? 'w-full text-sm' : 'mt-4 w-full text-sm'}>
                <thead>
                  <tr className="border-b text-xs text-muted-foreground">
                    <th className="py-2 text-start font-medium">{t(`reports.pluginReport.groupBy.${report.group_by}`)}</th>
                    <th className="py-2 text-end font-medium">{t('reports.amount')}</th>
                    {!byMonth && report.measure !== 'net' && (
                      <th className="py-2 ps-4 text-end font-medium">{t('reports.pluginReport.share')}</th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.key} className="border-b last:border-0">
                      <td className="py-2 pe-3">
                        <span className="flex min-w-0 items-center gap-2">
                          {!byMonth && <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: row.color }} />}
                          <span className="truncate">{row.label}</span>
                        </span>
                      </td>
                      <td className="py-2 text-end tabular-nums">{money(row.value)}</td>
                      {!byMonth && report.measure !== 'net' && (
                        <td className="py-2 ps-4 text-end tabular-nums text-muted-foreground">
                          {data && data.total ? `${Math.round((row.value / data.total) * 100)}%` : '0%'}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  )
}
