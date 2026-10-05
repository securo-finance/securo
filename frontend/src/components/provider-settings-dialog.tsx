import { useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertCircle, ExternalLink, Loader2, LockKeyhole } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { ProviderSettingsField } from '@/components/provider-settings-field'
import { admin, type ProviderSettingStatus } from '@/lib/api'

type Props = {
  provider: ProviderSettingStatus
  name: string
  description?: string
  icon: ReactNode
  guideUrl?: string
}

function ProviderEditor({ provider, name, description, icon, guideUrl, onClose }: Props & { onClose: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Record<string, string | boolean>>({})
  const [resetField, setResetField] = useState<string | null>(null)
  const isSimplefin = provider.name === 'simplefin'
  const cannotStoreSecrets = !isSimplefin && !provider.can_store_secrets
  const hasChanges = Object.keys(draft).length > 0
  const hasBlankCredential = Object.values(draft).some((value) => typeof value === 'string' && !value.trim())

  function updateDraft(key: string, value: string | boolean) {
    setDraft((current) => {
      const next = { ...current }
      if (value === '' || (key === 'simplefin_enabled' && value === provider.fields[key].configured)) delete next[key]
      else next[key] = value
      return next
    })
  }

  const save = useMutation({
    gcTime: 0,
    mutationFn: async (fieldToReset?: string) => {
      try {
        return await admin.updateProviderSettings(provider.name, fieldToReset ? { [fieldToReset]: null } : draft)
      } catch {
        // Do not retain Axios errors containing the credential request body.
        throw new Error('Could not update provider settings')
      }
    },
    onSuccess: (updated, fieldToReset) => {
      queryClient.setQueryData<ProviderSettingStatus[]>(['admin', 'provider-settings'], (current) =>
        current?.map((item) => item.name === updated.name ? updated : item),
      )
      void queryClient.invalidateQueries({ queryKey: ['admin', 'provider-settings'] })
      void queryClient.invalidateQueries({ queryKey: ['connections', 'providers'] })
      void queryClient.invalidateQueries({ queryKey: ['fx-rates'] })
      setResetField(null)
      setDraft((current) => {
        if (!fieldToReset) return {}
        const next = { ...current }
        delete next[fieldToReset]
        return next
      })
      toast.success(t('admin.settings.updated'))
      if (!fieldToReset) onClose()
    },
    onError: () => toast.error(t('common.error')),
  })
  const canSave = hasChanges && !hasBlankCredential && !cannotStoreSecrets && !save.isPending

  return (
    <DialogContent
      className="max-h-[calc(100dvh-2rem)] gap-0 overflow-y-auto p-0 sm:max-w-lg"
      showCloseButton={!save.isPending}
      onInteractOutside={(event) => { if (save.isPending) event.preventDefault() }}
      onEscapeKeyDown={(event) => { if (save.isPending) event.preventDefault() }}
    >
      <DialogHeader className="border-b border-border/60 px-5 py-5 text-left sm:px-6">
        <div className="mb-2 flex size-10 items-center justify-center rounded-lg border border-border/60 bg-muted/40">{icon}</div>
        <DialogTitle className="pr-6 text-lg leading-snug">{name}</DialogTitle>
        <DialogDescription className="leading-relaxed">{description ?? t('providerSettings.catalog.credentialsDescription')}</DialogDescription>
        {guideUrl && (
          <a
            href={guideUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex w-fit items-center gap-1 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t('providerSettings.catalog.setupGuide')}
            <ExternalLink className="size-3" aria-hidden="true" />
          </a>
        )}
      </DialogHeader>
      <form aria-label={name} onSubmit={(event) => { event.preventDefault(); if (canSave) save.mutate(undefined) }}>
        <div className="space-y-6 px-5 py-6 sm:px-6">
          {!isSimplefin && (
            <div className="space-y-1.5">
              <h3 className="text-sm font-semibold">{t('providerSettings.catalog.credentialsTitle')}</h3>
              <p className="text-sm leading-relaxed text-muted-foreground">{t('providerSettings.catalog.credentialsDescription')}</p>
            </div>
          )}
          {cannotStoreSecrets && (
            <div role="alert" className="flex gap-3 rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-sm leading-relaxed text-destructive">
              <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <p>{t('providerSettings.secretStorageUnavailable')}</p>
            </div>
          )}
          {Object.entries(provider.fields).map(([key, field]) => (
            <ProviderSettingsField
              key={key}
              fieldKey={key}
              field={field}
              value={draft[key]}
              disabled={save.isPending || cannotStoreSecrets}
              pending={save.isPending}
              onChange={(value) => updateDraft(key, value)}
              onReset={() => setResetField(key)}
            />
          ))}
          {!isSimplefin && (
            <p className="flex items-center gap-2 text-xs leading-relaxed text-muted-foreground">
              <LockKeyhole className="size-3.5 shrink-0" aria-hidden="true" />
              {t('providerSettings.catalog.securityNote')}
            </p>
          )}
        </div>
        <DialogFooter className="sticky bottom-0 border-t border-border/60 bg-background px-5 py-4 sm:px-6">
          <Button type="button" variant="outline" className="h-10 shadow-none" disabled={save.isPending} onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" className="h-10" disabled={!canSave}>
            {save.isPending && <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />}
            {t(`providerSettings.catalog.${save.isPending ? 'saving' : 'saveChanges'}`)}
          </Button>
        </DialogFooter>
      </form>
      {resetField && (
        <DeleteConfirmationDialog
          open
          title={t('providerSettings.removeTitle', { field: t(`providerSettings.fields.${resetField}`) })}
          description={t(`providerSettings.${provider.fields[resetField].environment_configured ? 'removeWithFallback' : 'removeWithoutFallback'}`)}
          isPending={save.isPending}
          onClose={() => setResetField(null)}
          onConfirm={() => save.mutate(resetField)}
        />
      )}
    </DialogContent>
  )
}

export function ProviderSettingsDialog({ children, ...props }: Props & { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{children}</DialogTrigger>
      {open && <ProviderEditor {...props} onClose={() => setOpen(false)} />}
    </Dialog>
  )
}
