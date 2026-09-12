import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { admin } from '@/lib/api'

const calendarQueryKeys = new Set([
  'accounts',
  'transactions',
  'recurring',
  'dashboard',
  'reports',
  'budgets',
  'goals',
  'assets',
  'asset-values',
  'asset-trend',
  'portfolio-trend',
  'fx-rates',
  'invoice',
  'invoices',
  'invoice-summary',
  'invoice-facets',
  'invoice-document',
  'reconciliation-suggestions',
])

export function TimezoneSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<string>()
  const timezoneQuery = useQuery({
    queryKey: ['admin', 'timezone'],
    queryFn: admin.timezone,
  })
  const saveTimezone = useMutation({
    mutationFn: (timezone: string) => admin.updateSetting('timezone', timezone),
    onSuccess: (_setting, timezone) => {
      queryClient.setQueryData<{ timezone: string; available: string[] }>(
        ['admin', 'timezone'],
        (current) => current ? { ...current, timezone } : current,
      )
      setDraft(undefined)
      void queryClient.invalidateQueries({ queryKey: ['admin', 'timezone'] })
      void queryClient.invalidateQueries({
        predicate: ({ queryKey }) => calendarQueryKeys.has(String(queryKey[0])),
      })
      toast.success(t('admin.settings.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const currentTimezone = timezoneQuery.data?.timezone
  const selectedTimezone = draft ?? currentTimezone

  return (
    <section className="mb-8 rounded-xl border border-border/60 bg-card overflow-hidden">
      <div className="px-5 py-4 border-b border-border/40">
        <div className="flex items-center gap-2 mb-0.5">
          <CalendarClock size={15} className="text-muted-foreground" />
          <h2 className="text-sm font-semibold text-foreground">
            {t('admin.settings.timezoneTitle')}
          </h2>
        </div>
      </div>
      <div className="p-5 space-y-3">
        <div className="space-y-1">
          <Label htmlFor="application-timezone">{t('admin.settings.timezone')}</Label>
          <p id="application-timezone-help" className="text-sm text-muted-foreground">
            {t('admin.settings.timezoneHelp')}
          </p>
        </div>
        {timezoneQuery.isError ? (
          <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-destructive">
            <span>{t('common.error')}</span>
            <Button variant="outline" size="sm" onClick={() => timezoneQuery.refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        ) : !timezoneQuery.data ? (
          <p role="status" className="text-sm text-muted-foreground">{t('common.loading')}</p>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <select
              id="application-timezone"
              aria-describedby="application-timezone-help"
              className="h-10 max-w-full rounded-lg border border-input bg-card px-3 text-sm"
              disabled={saveTimezone.isPending}
              value={selectedTimezone}
              onChange={(event) => setDraft(event.target.value)}
            >
              {timezoneQuery.data.available.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
            <Button
              disabled={saveTimezone.isPending || !draft || draft === currentTimezone}
              onClick={() => draft && saveTimezone.mutate(draft)}
            >
              {saveTimezone.isPending ? t('common.loading') : t('common.save')}
            </Button>
          </div>
        )}
      </div>
    </section>
  )
}
