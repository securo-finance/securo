import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import { connections } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { ExternalLink } from 'lucide-react'
import { toast } from 'sonner'

interface TokenConnectDialogProps {
  open: boolean
  onClose: () => void
  provider: string
  supportsAssetSync?: boolean
  reconnectConnectionId?: string
}

const PROVIDER_BRIDGE_URLS: Record<string, string> = {
  simplefin: 'https://bridge.simplefin.org/simplefin/create',
}

export function TokenConnectDialog({
  open,
  onClose,
  provider,
  supportsAssetSync = false,
  reconnectConnectionId,
}: TokenConnectDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [token, setToken] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [syncAssets, setSyncAssets] = useState(true)

  useEffect(() => {
    if (!open) {
      setToken('')
      setSubmitting(false)
      setSyncAssets(true)
    }
  }, [open])

  const bridgeUrl = PROVIDER_BRIDGE_URLS[provider]
  const i18nKey = `accounts.tokenConnect.${provider}`
  const isReconnect = Boolean(reconnectConnectionId)

  const handleSubmit = async () => {
    if (!token.trim()) return
    setSubmitting(true)
    try {
      await connections.handleCallback(
        token.trim(),
        provider,
        undefined,
        supportsAssetSync && !isReconnect ? { sync_assets: syncAssets } : undefined,
        reconnectConnectionId,
      )
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t(isReconnect ? 'accounts.reconnected' : 'accounts.connected'))
      onClose()
    } catch (err) {
      const detail =
        axios.isAxiosError(err) && err.response?.data?.detail
          ? typeof err.response.data.detail === 'string'
            ? err.response.data.detail
            : err.response.data.detail.message
          : null
      toast.error(detail || t('accounts.connectError'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !submitting && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isReconnect
              ? t(`${i18nKey}.reconnectTitle`, t('accounts.tokenConnect.reconnectTitle'))
              : t(`${i18nKey}.title`, t('accounts.tokenConnect.defaultTitle'))}
          </DialogTitle>
          <p className="text-sm text-muted-foreground">
            {isReconnect
              ? t(`${i18nKey}.reconnectDescription`, t('accounts.tokenConnect.reconnectDescription'))
              : t(`${i18nKey}.description`, t('accounts.tokenConnect.defaultDescription'))}
          </p>
        </DialogHeader>

        {bridgeUrl && (
          <Button asChild variant="outline" className="w-full justify-between">
            <a href={bridgeUrl} target="_blank" rel="noreferrer">
              <span>{t('accounts.tokenConnect.openBridge')}</span>
              <ExternalLink size={14} />
            </a>
          </Button>
        )}

        {supportsAssetSync && !isReconnect && (
          <div className="flex items-start justify-between gap-4 rounded-lg border border-border p-3">
            <div className="space-y-1">
              <label htmlFor="token-sync-assets" className="text-sm font-medium text-foreground">
                {t('connections.syncAssets')}
              </label>
              <p className="text-xs text-muted-foreground">{t('connections.syncAssetsHint')}</p>
            </div>
            <input
              id="token-sync-assets"
              type="checkbox"
              checked={syncAssets}
              onChange={(e) => setSyncAssets(e.target.checked)}
              className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-primary"
              disabled={submitting}
            />
          </div>
        )}

        <div className="space-y-1.5">
          <label className="text-sm font-medium" htmlFor="securo-token-input">
            {t('accounts.tokenConnect.tokenLabel')}
          </label>
          <textarea
            id="securo-token-input"
            className="w-full min-h-[110px] rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-0"
            placeholder={t('accounts.tokenConnect.tokenPlaceholder')}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            spellCheck={false}
            autoComplete="off"
            disabled={submitting}
          />
          <p className="text-xs text-muted-foreground">
            {t('accounts.tokenConnect.tokenHelp')}
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button onClick={handleSubmit} disabled={!token.trim() || submitting}>
            {submitting
              ? t('accounts.tokenConnect.connecting')
              : t(isReconnect ? 'accounts.tokenConnect.reconnect' : 'accounts.tokenConnect.connect')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
