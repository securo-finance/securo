import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertCircle } from 'lucide-react'

import { ReportView } from '@/components/reports/ReportView'
import { Skeleton } from '@/components/ui/skeleton'
import { reports } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import type { PluginReportEntry } from '@/lib/library'
import { localized } from '@/lib/localized-text'
import { queryReportView } from '@/lib/plugin-report-view'
import type { PluginReportResponse } from '@/types'

/**
 * A report a plugin adds (ADR 0028): a query Securo adds up, or the plugin's
 * own code run in the plugin runner. Either way it comes back as a view drawn
 * with Securo's components, for the page's period and accounts.
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
  const { report } = entry

  const { data, isLoading, isError, error, refetch } = useQuery<PluginReportResponse>({
    queryKey: ['reports', 'plugin', entry.id, startDate ?? null, endDate ?? null, accountIds ?? null],
    queryFn: () => reports.plugin(entry.plugin.id, report.key, startDate!, endDate!, accountIds),
    enabled: !!startDate && !!endDate,
    // A code report runs the plugin each time: no retries on a failure it reported.
    ...(report.code ? { retry: false } : {}),
  })

  const view = data ? (data.code ? data.view : queryReportView(report, data, t)) : null
  const empty = !view || view.blocks.length === 0 || (!data?.code && data?.rows.every((row) => row.value === 0))

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs text-muted-foreground">
        <span>{entry.plugin.description ? localized(entry.plugin.description, i18n.language) : null}</span>
        <span>
          {t('library.fromPlugin', { name: localized(entry.plugin.name, i18n.language) })}
          {report.code && <> · {t('reports.pluginReport.computed')}</>}
        </span>
      </div>

      {isLoading ? (
        <div className="space-y-5">
          <Skeleton className="h-20 w-full rounded-xl" />
          <Skeleton className="h-72 w-full rounded-xl" />
        </div>
      ) : isError ? (
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
      ) : view && !empty ? (
        <ReportView view={view} currency={data?.currency ?? 'USD'} />
      ) : (
        <div className="bg-card rounded-xl border border-border shadow-sm">
          <p className="py-16 text-center text-sm text-muted-foreground">{t('reports.noData')}</p>
        </div>
      )}
    </div>
  )
}
