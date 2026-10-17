import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/contexts/auth-context'
import { connections } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Building2, Settings2 } from 'lucide-react'

export interface Provider {
  name: string
  display_name: string
  description: string
  flow_type: string
  configured: boolean
  requires_institution_select?: boolean
  supports_asset_sync?: boolean
}

interface ConnectorSelectDialogProps {
  open: boolean
  onClose: () => void
  onSelect: (provider: Provider) => void
}

export function ConnectorSelectDialog(props: ConnectorSelectDialogProps) {
  return props.open ? <ConnectorSelectSession {...props} /> : null
}

function ConnectorSelectSession({ open, onClose, onSelect }: ConnectorSelectDialogProps) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const [providers, setProviders] = useState<Provider[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    connections.getProviders().then((data) => {
      if (cancelled) return
      setProviders(data)
      setLoading(false)
    }).catch(() => {
      if (cancelled) return
      setProviders([])
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('accounts.selectConnector')}</DialogTitle>
          <DialogDescription>{t('accounts.selectConnectorDesc')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2 pt-2">
          {loading ? (
            <div className="flex justify-center py-8">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          ) : providers.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              {t('accounts.noConnectorsAvailable')}
            </p>
          ) : (
            providers.map((p) => {
              const content = (
                <>
                  <div className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center shrink-0 mt-0.5" aria-hidden="true">
                    <Building2 size={16} className="text-muted-foreground" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground">{p.display_name}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">{t(`accounts.providers.${p.name}.description`, p.description)}</p>
                    {!p.configured && (
                      <p className="text-xs text-amber-700 dark:text-amber-400 mt-1.5">
                        {user?.is_superuser ? t('accounts.connectorNotConfigured') : t('accounts.connectorAskAdmin')}
                      </p>
                    )}
                  </div>
                </>
              )

              return p.configured ? (
                <button
                  key={p.name}
                  type="button"
                  onClick={() => { onSelect(p); onClose() }}
                  className="w-full flex items-start gap-3 rounded-lg border border-border p-4 text-left transition-colors hover:border-primary hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {content}
                </button>
              ) : (
                <div key={p.name} className="flex items-start gap-3 rounded-lg border border-border/60 bg-muted/20 p-4">
                  {content}
                  {user?.is_superuser && (
                    <Link
                      to="/admin#provider-connections"
                      onClick={onClose}
                      title={t('providerSettings.catalog.configureProvider', { provider: p.display_name })}
                      aria-label={t('providerSettings.catalog.configureProvider', { provider: p.display_name })}
                      className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <Settings2 className="size-4" aria-hidden="true" />
                    </Link>
                  )}
                </div>
              )
            })
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
