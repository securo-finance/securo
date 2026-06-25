import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { connections } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Building2 } from 'lucide-react'

export interface Provider {
  name: string
  display_name: string
  description: string
  flow_type: string
  configured: boolean
  requires_institution_select?: boolean
}

interface ConnectorSelectDialogProps {
  open: boolean
  onClose: () => void
  onSelect: (provider: Provider) => void
}

export function ConnectorSelectDialog({ open, onClose, onSelect }: ConnectorSelectDialogProps) {
  const { t } = useTranslation()
  const [providers, setProviders] = useState<Provider[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    connections.getProviders().then((data) => {
      setProviders(data)
      setLoading(false)
    }).catch(() => {
      setProviders([])
      setLoading(false)
    })
  }, [open])

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('accounts.selectConnector')}</DialogTitle>
          <p className="text-sm text-muted-foreground">{t('accounts.selectConnectorDesc')}</p>
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
            providers.map((p) => (
              <button
                key={p.name}
                disabled={!p.configured}
                onClick={() => {
                  onSelect(p)
                  onClose()
                }}
                className={`w-full flex items-start gap-3 rounded-lg border p-4 text-left transition-colors ${p.configured
                  ? 'border-border hover:border-primary hover:bg-muted/50 cursor-pointer'
                  : 'border-border/50 opacity-60 cursor-not-allowed'
                  }`}
              >
                <div className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center shrink-0 mt-0.5">
                  <Building2 size={16} className="text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground">{p.display_name}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">{t(`accounts.providers.${p.name}.description`, p.description)}</p>
                  {!p.configured && (
                    <p className="text-xs text-amber-600 mt-1.5">
                      {t('accounts.connectorNotConfigured')}
                    </p>
                  )}
                </div>
              </button>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
