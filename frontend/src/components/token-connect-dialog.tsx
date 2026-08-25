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
  ibkr: 'https://www.ibkrguides.com/brokerportal/performanceandstatements/flex3.htm',
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
  const [queryId, setQueryId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [syncAssets, setSyncAssets] = useState(true)
  const [providerError, setProviderError] = useState<{
    message: string
    helpUrl?: string
  } | null>(null)

  useEffect(() => {
    if (!open) {
      setToken('')
      setQueryId('')
      setSubmitting(false)
      setSyncAssets(true)
      setProviderError(null)
    }
  }, [open])

  const bridgeUrl = PROVIDER_BRIDGE_URLS[provider]
  const i18nKey = `accounts.tokenConnect.${provider}`
  const isReconnect = Boolean(reconnectConnectionId)
  const isIbkr = provider === 'ibkr'

  const handleSubmit = async () => {
    if (!token.trim()) return
    setSubmitting(true)
    setProviderError(null)
    try {
      await connections.handleTokenCallback(
        token.trim(),
        provider,
        isIbkr ? { query_id: queryId.trim() } : undefined,
        supportsAssetSync && !isReconnect ? { sync_assets: syncAssets } : undefined,
        reconnectConnectionId,
      )
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t(isReconnect ? 'accounts.reconnected' : 'accounts.connected'))
      onClose()
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
      const errorData =
        detail && typeof detail === 'object'
          ? (detail as { message?: string; code?: string; help_url?: string })
          : null
      const fallback =
        typeof detail === 'string' ? detail : errorData?.message || t('accounts.connectError')
      const message =
        isIbkr && errorData?.code
          ? t(`${i18nKey}.errors.${errorData.code}`, fallback)
          : fallback
      setProviderError({ message, helpUrl: errorData?.help_url })
      toast.error(message)
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
              <span>
                {t(`${i18nKey}.openSetup`, t('accounts.tokenConnect.openBridge'))}
              </span>
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
            {t(`${i18nKey}.tokenLabel`, t('accounts.tokenConnect.tokenLabel'))}
          </label>
          {isIbkr ? (
            <input
              id="securo-token-input"
              type="password"
              className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder={t(`${i18nKey}.tokenPlaceholder`, t('accounts.tokenConnect.tokenPlaceholder'))}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              spellCheck={false}
              autoComplete="new-password"
              autoCapitalize="none"
              maxLength={4096}
              disabled={submitting}
            />
          ) : (
            <textarea
              id="securo-token-input"
              className="w-full min-h-[110px] rounded-md border border-input bg-card px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-0"
              placeholder={t(`${i18nKey}.tokenPlaceholder`, t('accounts.tokenConnect.tokenPlaceholder'))}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              spellCheck={false}
              autoComplete="off"
              autoCapitalize="none"
              maxLength={4096}
              disabled={submitting}
            />
          )}
          <p className="text-xs text-muted-foreground">
            {t(`${i18nKey}.tokenHelp`, t('accounts.tokenConnect.tokenHelp'))}
          </p>
        </div>

        {isIbkr && (
          <>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="ibkr-query-id">
                {t(`${i18nKey}.queryIdLabel`)}
              </label>
              <input
                id="ibkr-query-id"
                className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
                placeholder={t(`${i18nKey}.queryIdPlaceholder`)}
                value={queryId}
                onChange={(e) => setQueryId(e.target.value.replace(/\D/g, ''))}
                autoComplete="off"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={32}
                disabled={submitting}
              />
            </div>
            <div className="rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">{t(`${i18nKey}.queryChecklistTitle`)}</p>
              <ul className="mt-1.5 list-disc space-y-1 pl-4">
                <li>{t(`${i18nKey}.queryChecklistAccount`)}</li>
                <li>{t(`${i18nKey}.queryChecklistCash`)}</li>
                <li>{t(`${i18nKey}.queryChecklistFunds`)}</li>
                <li>{t(`${i18nKey}.queryChecklistPositions`)}</li>
              </ul>
            </div>
          </>
        )}

        {providerError && (
          <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm">
            <p>{providerError.message}</p>
            {providerError.helpUrl && (
              <a
                href={providerError.helpUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-flex items-center gap-1 text-xs font-medium underline"
              >
                {t(`${i18nKey}.errorHelp`)}
                <ExternalLink size={12} />
              </a>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!token.trim() || (isIbkr && !queryId.trim()) || submitting}
          >
            {submitting
              ? t('accounts.tokenConnect.connecting')
              : t(isReconnect ? 'accounts.tokenConnect.reconnect' : 'accounts.tokenConnect.connect')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
