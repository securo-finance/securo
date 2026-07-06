import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { connections } from '@/lib/api'
import { toast } from 'sonner'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { BankConnection, ConnectionSettings } from '@/types'

type PayeeSource = NonNullable<ConnectionSettings['payee_source']>

interface ConnectionSettingsDialogProps {
  open: boolean
  onClose: () => void
  connection: BankConnection | null
  supportsAssetSync?: boolean
}

export function ConnectionSettingsDialog({
  open,
  onClose,
  connection,
  supportsAssetSync = false,
}: ConnectionSettingsDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [displayName, setDisplayName] = useState('')
  const [payeeSource, setPayeeSource] = useState<PayeeSource>('auto')
  const [importPending, setImportPending] = useState(true)
  const [syncAssets, setSyncAssets] = useState(true)

  useEffect(() => {
    if (connection) {
      setDisplayName(connection.display_name ?? '')
      setPayeeSource(connection.settings?.payee_source ?? 'auto')
      setImportPending(connection.settings?.import_pending ?? true)
      setSyncAssets(connection.settings?.sync_assets ?? true)
    }
  }, [connection])

  const mutation = useMutation({
    mutationFn: () =>
      connections.updateSettings(connection!.id, {
        display_name: displayName.trim() || null,
        payee_source: payeeSource,
        import_pending: importPending,
        // Only persist asset-sync for connectors that actually import holdings.
        ...(supportsAssetSync ? { sync_assets: syncAssets } : {}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t('accounts.updated'))
      onClose()
    },
    onError: () => toast.error(t('common.error')),
  })

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('connections.settings')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="connection-display-name">{t('connections.displayName')}</Label>
            <Input
              id="connection-display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={connection?.institution_name ?? t('connections.displayNamePlaceholder')}
              maxLength={255}
            />
            <p className="text-[11px] text-muted-foreground">{t('connections.displayNameHint')}</p>
          </div>
          <div className="space-y-2">
            <Label>{t('connections.payeeSource')}</Label>
            <select
              className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
              value={payeeSource}
              onChange={(e) => setPayeeSource(e.target.value as PayeeSource)}
            >
              <option value="auto">{t('connections.payeeAuto')}</option>
              <option value="merchant">{t('connections.payeeMerchant')}</option>
              <option value="payment_data">{t('connections.payeePaymentData')}</option>
              <option value="description">{t('connections.payeeDescription')}</option>
              <option value="none">{t('connections.payeeNone')}</option>
            </select>
          </div>
          <div className="flex items-center justify-between">
            <Label htmlFor="import-pending">{t('connections.importPending')}</Label>
            <input
              id="import-pending"
              type="checkbox"
              checked={importPending}
              onChange={(e) => setImportPending(e.target.checked)}
              className="h-4 w-4 rounded border-border text-primary focus:ring-primary"
            />
          </div>
          {supportsAssetSync && (
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1">
                <Label htmlFor="sync-assets">{t('connections.syncAssets')}</Label>
                <p className="text-[11px] text-muted-foreground">{t('connections.syncAssetsHint')}</p>
              </div>
              <input
                id="sync-assets"
                type="checkbox"
                checked={syncAssets}
                onChange={(e) => setSyncAssets(e.target.checked)}
                className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-primary"
              />
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? t('common.loading') : t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
