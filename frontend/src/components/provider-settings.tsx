import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { admin, type ProviderSettingStatus } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'

const names: Record<string, string> = {
  openexchangerates: 'Open Exchange Rates', pluggy: 'Pluggy',
  enable_banking: 'Enable Banking', simplefin: 'SimpleFIN',
}

function ProviderForm({ provider }: { provider: ProviderSettingStatus }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Record<string, string | boolean | null>>({})
  const [resetField, setResetField] = useState<string | null>(null)
  const cannotStoreSecrets = provider.name !== 'simplefin' && !provider.can_store_secrets
  function replaceCredential(key: string, value: string) {
    setDraft((current) => {
      const next = { ...current }
      if (value) next[key] = value
      else delete next[key]
      return next
    })
  }
  const save = useMutation({
    gcTime: 0,
    mutationFn: async (resetField?: string) => {
      try {
        return await admin.updateProviderSettings(provider.name, resetField ? { [resetField]: null } : draft)
      } catch {
        // Axios errors retain request bodies; keep secrets out of mutation state.
        throw new Error('Could not update provider settings')
      }
    },
    onSuccess: (_data, resetField) => {
      setResetField(null)
      setDraft((current) => {
        if (!resetField) return {}
        const next = { ...current }
        delete next[resetField]
        return next
      })
      queryClient.invalidateQueries({ queryKey: ['admin', 'provider-settings'] })
      queryClient.invalidateQueries({ queryKey: ['connections', 'providers'] })
      queryClient.invalidateQueries({ queryKey: ['fx-rates'] })
      toast.success(t('admin.settings.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  return (
    <form className="rounded-xl border border-border/60 bg-card p-5 space-y-4" onSubmit={(event) => { event.preventDefault(); save.mutate(undefined) }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">{names[provider.name]}</h3>
        <Badge variant={provider.configured ? 'secondary' : 'outline'}>{t(`providerSettings.${provider.configured ? 'configured' : 'notConfigured'}`)}</Badge>
      </div>
      {provider.name === 'simplefin' && <p className="text-sm text-muted-foreground">{t('providerSettings.simplefinHelp')}</p>}
      {cannotStoreSecrets && <p role="alert" className="text-sm text-destructive">{t('providerSettings.secretStorageUnavailable')}</p>}
      {Object.entries(provider.fields).map(([key, field]) => (
        <div key={key} className="space-y-2">
          <Label htmlFor={key}>{t(`providerSettings.fields.${key}`)}</Label>
          {key === 'simplefin_enabled' ? (
            <select id={key} className="w-full h-10 rounded-lg border border-input bg-card px-3 text-sm" disabled={save.isPending}
              value={String(draft[key] ?? field.configured)} onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value === 'true' }))}>
              <option value="true">{t('admin.users.enabled')}</option>
              <option value="false">{t('admin.users.disabled')}</option>
            </select>
          ) : key === 'enable_banking_private_key' ? (
            <textarea id={key} rows={4} autoComplete="off" spellCheck={false} disabled={save.isPending}
              className="w-full rounded-lg border border-input bg-card px-3 py-2 text-sm font-mono"
              value={String(draft[key] ?? '')} placeholder={t('providerSettings.secretPlaceholder')}
              onChange={(event) => replaceCredential(key, event.target.value)} />
          ) : (
            <Input id={key} type="password" autoComplete="new-password" disabled={save.isPending}
              value={String(draft[key] ?? '')} placeholder={t('providerSettings.secretPlaceholder')}
              onChange={(event) => replaceCredential(key, event.target.value)} />
          )}
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>{t(`providerSettings.${field.invalid ? 'invalid' : field.source}`)}</span>
            {field.source === 'app' && <Button type="button" variant="ghost" size="sm" disabled={save.isPending} onClick={() => setResetField(key)}>{t(`providerSettings.${field.environment_configured ? 'reset' : 'remove'}`)}</Button>}
          </div>
        </div>
      ))}
      <Button type="submit" disabled={save.isPending || cannotStoreSecrets || !Object.keys(draft).length || Object.values(draft).some((value) => typeof value === 'string' && !value.trim())}>
        {save.isPending ? t('common.loading') : t('common.save')}
      </Button>
      {resetField && <DeleteConfirmationDialog
        open title={t('providerSettings.removeTitle', { field: t(`providerSettings.fields.${resetField}`) })}
        description={t(`providerSettings.${provider.fields[resetField].environment_configured ? 'removeWithFallback' : 'removeWithoutFallback'}`)}
        isPending={save.isPending} onClose={() => setResetField(null)} onConfirm={() => save.mutate(resetField)}
      />}
    </form>
  )
}

export function ProviderSettings() {
  const { t } = useTranslation()
  const { data, isPending, isError, refetch } = useQuery({ queryKey: ['admin', 'provider-settings'], queryFn: admin.providerSettings })
  return (
    <section className="mb-8 space-y-4" aria-labelledby="provider-settings-title">
      <div>
        <h2 id="provider-settings-title" className="text-lg font-semibold">{t('providerSettings.title')}</h2>
        <p className="text-sm text-muted-foreground">{t('providerSettings.description')}</p>
      </div>
      {isPending && <p role="status">{t('common.loading')}</p>}
      {isError && <div role="alert">{t('common.error')} <Button variant="outline" onClick={() => refetch()}>{t('common.retry')}</Button></div>}
      <div className="grid gap-4 lg:grid-cols-2">{data?.map((provider) => <ProviderForm key={provider.name} provider={provider} />)}</div>
    </section>
  )
}
