import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { admin } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'

const calendarQueries = [
  'accounts', 'transactions', 'recurring', 'dashboard', 'reports', 'budgets', 'goals',
  'assets', 'asset-values', 'asset-trend', 'portfolio-trend', 'fx-rates',
  'invoice', 'invoices', 'invoice-summary', 'invoice-facets', 'invoice-document',
]

export function TimezoneSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<string>()
  const { data, isError, refetch } = useQuery({ queryKey: ['admin', 'timezone'], queryFn: admin.timezone })
  const save = useMutation({
    mutationFn: () => admin.updateSetting('timezone', draft!),
    onSuccess: () => {
      setDraft(undefined)
      queryClient.invalidateQueries({ queryKey: ['admin', 'timezone'] })
      queryClient.invalidateQueries({ predicate: (query) => calendarQueries.some((key) => key === query.queryKey[0]) })
      toast.success(t('admin.settings.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })
  return (
    <section className="mb-8 rounded-xl border border-border/60 bg-card p-5 space-y-3">
      <h2 className="text-lg font-semibold">{t('providerSettings.system')}</h2>
      <Label htmlFor="backend-timezone">{t('providerSettings.timezone')}</Label>
      <p id="backend-timezone-help" className="text-sm text-muted-foreground">{t('providerSettings.timezoneHelp')}</p>
      {isError ? <div role="alert">{t('common.error')} <Button variant="outline" onClick={() => refetch()}>{t('common.retry')}</Button></div> : !data ? <p role="status">{t('common.loading')}</p> : (
        <div className="flex flex-wrap items-center gap-3">
          <select id="backend-timezone" aria-describedby="backend-timezone-help" className="h-10 max-w-full rounded-lg border border-input bg-card px-3 text-sm" disabled={save.isPending}
            value={draft ?? data.timezone} onChange={(event) => setDraft(event.target.value)}>
            {data.available.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <Button disabled={save.isPending || !draft || draft === data.timezone} onClick={() => save.mutate()}>{t('common.save')}</Button>
        </div>
      )}
    </section>
  )
}
