import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { PluggyConnect } from 'react-pluggy-connect'
import { connections } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'

interface BankConnectDialogProps {
  open: boolean
  onClose: () => void
  reconnectConnectionId?: string
  updateItemId?: string
  provider?: string
  supportsAssetSync?: boolean
}

export function BankConnectDialog({
  open,
  onClose,
  reconnectConnectionId,
  updateItemId,
  provider = 'pluggy',
  supportsAssetSync = false,
}: BankConnectDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [connectToken, setConnectToken] = useState<string | null>(null)
  const [syncAssets, setSyncAssets] = useState(true)
  const [optionsConfirmed, setOptionsConfirmed] = useState(false)
  // Only prompt for asset-sync when the provider actually imports holdings.
  const needsInitialOptions = !reconnectConnectionId && supportsAssetSync

  useEffect(() => {
    if (!open) {
      setConnectToken(null)
      setSyncAssets(true)
      setOptionsConfirmed(false)
      return
    }

    if (needsInitialOptions && !optionsConfirmed) return

    let cancelled = false
    const fetchToken = async () => {
      try {
        const token = reconnectConnectionId
          ? await connections.getReconnectToken(reconnectConnectionId)
          : await connections.getConnectToken(provider)
        if (!cancelled) setConnectToken(token)
      } catch {
        if (!cancelled) {
          toast.error(t('accounts.connectError'))
          onClose()
        }
      }
    }
    fetchToken()

    return () => { cancelled = true }
  }, [open, reconnectConnectionId, provider, needsInitialOptions, optionsConfirmed])

  const handleSuccess = async (data: { item: { id: string } }) => {
    try {
      if (reconnectConnectionId) {
        await connections.sync(reconnectConnectionId)
      } else {
        await connections.handleCallback(
          data.item.id,
          provider,
          undefined,
          supportsAssetSync ? { sync_assets: syncAssets } : undefined,
        )
      }
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t('accounts.connected'))
    } catch {
      toast.error(t('accounts.connectError'))
    } finally {
      handleClose()
    }
  }

  const handleClose = () => {
    setConnectToken(null)
    onClose()
  }

  if (open && needsInitialOptions && !optionsConfirmed) {
    return (
      <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('connections.initialSyncSettings')}</DialogTitle>
            <p className="text-sm text-muted-foreground">{t('connections.initialSyncSettingsDesc')}</p>
          </DialogHeader>
          <div className="flex items-start justify-between gap-4 rounded-lg border border-border p-3">
            <div className="space-y-1">
              <Label htmlFor="initial-sync-assets">{t('connections.syncAssets')}</Label>
              <p className="text-xs text-muted-foreground">{t('connections.syncAssetsHint')}</p>
            </div>
            <input
              id="initial-sync-assets"
              type="checkbox"
              checked={syncAssets}
              onChange={(e) => setSyncAssets(e.target.checked)}
              className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-primary"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={handleClose}>{t('common.cancel')}</Button>
            <Button onClick={() => setOptionsConfirmed(true)}>{t('connections.continueToConnector')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }

  if (!open || !connectToken) return null

  return (
    <PluggyConnect
      connectToken={connectToken}
      updateItem={updateItemId}
      onSuccess={handleSuccess}
      onClose={handleClose}
      onError={() => {
        toast.error(t('accounts.connectError'))
        handleClose()
      }}
    />
  )
}
