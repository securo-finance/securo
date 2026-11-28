import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Gauge } from 'lucide-react'
import { useNotificationText } from '@/hooks/use-notifications'
import { quotas } from '@/lib/api'
import { quotaExcessLines } from '@/lib/notification-text'

/**
 * Where the account is above its limits, once the operator set a date (ADR 0055):
 * what is above, and how many to remove to fit. Nothing on an instance that is not managed.
 */
export function QuotaExcessBanner() {
  const { t } = useTranslation()
  const { date } = useNotificationText()
  const { data } = useQuery({
    queryKey: ['quotas', 'mine'],
    queryFn: () => quotas.mine(),
    retry: false,
    staleTime: 5 * 60 * 1000,
    // The server decides when the date has come and when the account fits
    // again: the notice lags it by five minutes at most.
    refetchInterval: 5 * 60 * 1000,
  })

  if (!data || !data.above || !data.effective_at) return null
  const lines = quotaExcessLines(t, data.counters)

  return (
    <div className="px-3 pb-1">
      <div
        role="status"
        className="flex gap-2 w-full rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-sidebar-foreground"
      >
        <Gauge size={14} className="mt-0.5 shrink-0 text-amber-600" aria-hidden="true" />
        <div className="min-w-0 space-y-0.5">
          <p className="font-medium">
            {data.in_effect ? t('quota.title') : t('quota.titleFrom', { date: date(data.effective_at) })}
          </p>
          {data.sync_paused && <p>{t('quota.syncPaused')}</p>}
          {lines.map((line) => (
            <p key={line}>{line}</p>
          ))}
          <p className="text-sidebar-muted">{t('quota.hint')}</p>
        </div>
      </div>
    </div>
  )
}
