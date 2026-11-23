import { useQuery } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { useTranslation } from 'react-i18next'

import { reports } from '@/lib/api'
import type { LibraryEntry } from '@/lib/library'
import { queryReportView } from '@/lib/plugin-report-view'
import type { PluginReportDefinition, PluginReportResponse } from '@/types'

/**
 * A plugin's report (ADR 0028) or dashboard widget (ADR 0032) for a period
 * and accounts, as the view Securo draws: a query's rows turned into blocks,
 * or the view the plugin's code returned, already checked by the API.
 * `card` drops a query's table when there's a chart, to fit a widget.
 */
export function usePluginView(
  entry: { id: string; plugin: NonNullable<LibraryEntry['plugin']>; report: PluginReportDefinition },
  surface: 'reports' | 'dashboard',
  startDate: string | undefined,
  endDate: string | undefined,
  accountIds: string[] | undefined,
) {
  const { t } = useTranslation()
  const { report } = entry
  const query = useQuery<PluginReportResponse>({
    queryKey: [surface === 'reports' ? 'reports' : 'dashboard', 'plugin', entry.id, startDate ?? null, endDate ?? null, accountIds ?? null],
    queryFn: () => reports.plugin(entry.plugin.id, report.key, startDate!, endDate!, accountIds, surface),
    enabled: !!startDate && !!endDate,
    // Code runs the plugin each time: no retries on a failure it reported.
    ...(report.code ? { retry: false } : {}),
  })
  const { data, isError, error } = query
  const view = data ? (data.code ? data.view : queryReportView(report, data, t, { card: surface === 'dashboard' })) : null
  const empty = !view || view.blocks.length === 0 || (!data?.code && data?.rows.every((row) => row.value === 0))
  // A 422 here is the period: the API sends its limit in English, and retrying
  // the same window would only fail again.
  const periodRefused = isError && isAxiosError(error) && error.response?.status === 422
  return { ...query, view, empty, periodRefused }
}
