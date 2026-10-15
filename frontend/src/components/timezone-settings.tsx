import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CalendarClock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { TimezoneSelect } from '@/components/timezone-select'
import { admin, type TimezoneSetting } from '@/lib/api'

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
  'timezones',
  'drill-down',
])

/** The option that means "nothing saved, follow the server". */
const SERVER_DEFAULT = ''

export function TimezoneSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<string>()
  const timezoneQuery = useQuery({
    queryKey: ['admin', 'timezone'],
    queryFn: admin.timezone,
  })
  const saveTimezone = useMutation({
    mutationFn: async (timezone: string) => {
      if (timezone === SERVER_DEFAULT) await admin.deleteSetting('timezone')
      else await admin.updateSetting('timezone', timezone)
      return timezone
    },
    onSuccess: (timezone) => {
      queryClient.setQueryData<TimezoneSetting>(['admin', 'timezone'], (current) =>
        current
          ? {
              ...current,
              saved: timezone === SERVER_DEFAULT ? null : timezone,
              timezone: timezone === SERVER_DEFAULT ? current.fallback : timezone,
            }
          : current,
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

  const setting = timezoneQuery.data
  // A saved value the server cannot load is shown as what it is, so the
  // administrator sees the problem here and not only in the logs, and
  // picking anything else (the server default included) is a real change
  // that clears it.
  const savedIsBroken = !!setting?.saved && !setting.available.includes(setting.saved)
  const currentChoice = setting?.saved ?? SERVER_DEFAULT
  const selectedChoice = draft ?? currentChoice

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
        ) : !setting ? (
          <p role="status" className="text-sm text-muted-foreground">{t('common.loading')}</p>
        ) : (
          <>
            {savedIsBroken && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300"
              >
                <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                <span>
                  {t('admin.settings.timezoneSavedInvalid', {
                    value: setting.saved,
                    zone: setting.timezone,
                  })}
                </span>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-3">
              <TimezoneSelect
                id="application-timezone"
                aria-describedby="application-timezone-help"
                className="h-10 w-80 max-w-full"
                disabled={saveTimezone.isPending}
                value={selectedChoice}
                onChange={setDraft}
                options={setting.available}
                emptyOption={t('admin.settings.timezoneServerDefault', { zone: setting.fallback })}
              />
              <Button
                disabled={saveTimezone.isPending || draft === undefined || draft === currentChoice}
                onClick={() => draft !== undefined && saveTimezone.mutate(draft)}
              >
                {saveTimezone.isPending ? t('common.loading') : t('common.save')}
              </Button>
            </div>
          </>
        )}
      </div>
    </section>
  )
}
