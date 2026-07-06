import { useState, useEffect, useRef } from 'react'
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
  const fetchKeyRef = useRef<string | null>(null)

  useEffect(() => {
    if (!open) {
      setConnectToken(null)
      fetchKeyRef.current = null
      return
    }

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
  }, [open, reconnectConnectionId, provider])

  const handleSuccess = async (data: { item: { id: string } }) => {
    const toastId = toast.loading(t('accounts.syncingConnection'))
    try {
      if (reconnectConnectionId) {
        await connections.sync(reconnectConnectionId)
      } else {
        await connections.handleCallback(data.item.id, provider)
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
