import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { PluggyConnect } from 'react-pluggy-connect'
import { connections } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { FintocConnectWidget } from '@/hooks/use-fintoc-widget'

interface BankConnectDialogProps {
  open: boolean
  onClose: () => void
  reconnectConnectionId?: string
  updateItemId?: string
  provider?: string
}

export function BankConnectDialog({
  open,
  onClose,
  reconnectConnectionId,
  updateItemId,
  provider = 'pluggy',
}: BankConnectDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [connectToken, setConnectToken] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setConnectToken(null)
      return
    }

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
  }, [open, reconnectConnectionId, provider])

  const handleSuccess = async (data: { item: { id: string } }) => {
    try {
      if (reconnectConnectionId) {
        await connections.sync(reconnectConnectionId)
      } else {
        await connections.handleCallback(data.item.id, provider)
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

  if (!open) return null

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
