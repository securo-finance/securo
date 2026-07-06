import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { PluggyConnect } from 'react-pluggy-connect'
import { connections } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { FintocConnectWidget } from '@/hooks/use-fintoc-widget'
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
  const fetchKeyRef = useRef<string | null>(null)
  const [syncAssets, setSyncAssets] = useState(true)
  const [optionsConfirmed, setOptionsConfirmed] = useState(false)
  // Only prompt for asset-sync when the provider actually imports holdings.
  const needsInitialOptions = !reconnectConnectionId && supportsAssetSync

  useEffect(() => {
    if (!open) {
      setConnectToken(null)
      fetchKeyRef.current = null
      setSyncAssets(true)
      setOptionsConfirmed(false)
      return
    }

    if (needsInitialOptions && !optionsConfirmed) return

    const key = `${provider}:${reconnectConnectionId ?? ''}`
    if (fetchKeyRef.current === key) {
      // React StrictMode double-invokes this effect in dev with identical
      // inputs. getConnectToken/getReconnectToken issue a real, single-use
      // token server-side (e.g. a Fintoc link_intent) — firing it twice per
      // open wastes one and can leave the widget that actually opens (built
      // from the second token) intermittently failing to finish loading.
      // Only the first invocation of a given open cycle requests one.
      return
    }
    fetchKeyRef.current = key

    const fetchToken = async () => {
      try {
        const token = reconnectConnectionId
          ? await connections.getReconnectToken(reconnectConnectionId)
          : await connections.getConnectToken(provider)
        setConnectToken(token)
      } catch {
        toast.error(t('accounts.connectError'))
        onClose()
      }
    }
    fetchToken()
  }, [open, reconnectConnectionId, provider, needsInitialOptions, optionsConfirmed])

  const handleSuccess = async (data: { item: { id: string } }) => {
    const toastId = toast.loading(t('accounts.syncingConnection'))
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
      toast.success(t('accounts.connected'), { id: toastId })
    } catch {
      toast.error(t('accounts.connectError'), { id: toastId })
    } finally {
      handleClose()
    }
  }

  const handleClose = () => {
    setConnectToken(null)
    onClose()
  }

  if (!open) return null

  if (needsInitialOptions && !optionsConfirmed) {
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

  if (provider === 'fintoc') {
    if (!connectToken) return null
    return (
      <FintocConnectWidget
        widgetToken={connectToken}
        onSuccess={(exchangeToken) => handleSuccess({ item: { id: exchangeToken } })}
        onExit={handleClose}
      />
    )
  }

  if (!connectToken) return null

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
