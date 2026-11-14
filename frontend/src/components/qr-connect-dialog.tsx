import { useEffect, useRef, useState } from 'react'
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
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'

interface QrConnectDialogProps {
  open: boolean
  onClose: () => void
  provider: string
  reconnectConnectionId?: string
}

type Phase = 'starting' | 'waiting_for_scan' | 'reading' | 'importing' | 'expired' | 'error'

const POLL_MS = 2000
const I18N = 'accounts.qrConnect'

function errorDetail(err: unknown): string | null {
  if (!axios.isAxiosError(err)) return null
  const detail = err.response?.data?.detail
  return typeof detail === 'string' ? detail : detail?.message ?? null
}

/**
 * Connect (or re-sync) a bank that logs in by QR code (flow_type "qr", Bank
 * Leumi). The server opens the bank's login page and relays its QR code; the
 * user scans it with the bank's app, then the accounts are imported. No
 * password is typed or stored.
 */
export function QrConnectDialog({ open, onClose, provider, reconnectConnectionId }: QrConnectDialogProps) {
  const { t } = useTranslation()
  // Each attempt mounts a fresh session, so its state starts clean.
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const isReconnect = Boolean(reconnectConnectionId)

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !busy && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t(isReconnect ? `${I18N}.reconnectTitle` : `${I18N}.title`)}</DialogTitle>
          <p className="text-sm text-muted-foreground">{t(`${I18N}.description`)}</p>
        </DialogHeader>
        {open && (
          <QrSession
            key={attempt}
            provider={provider}
            reconnectConnectionId={reconnectConnectionId}
            onClose={onClose}
            onRetry={() => setAttempt((n) => n + 1)}
            onBusyChange={setBusy}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

interface QrSessionProps {
  provider: string
  reconnectConnectionId?: string
  onClose: () => void
  onRetry: () => void
  onBusyChange: (busy: boolean) => void
}

function QrSession({ provider, reconnectConnectionId, onClose, onRetry, onBusyChange }: QrSessionProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [phase, setPhase] = useState<Phase>('starting')
  const [qr, setQr] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The parent passes a new onClose on every render; keep the latest one in a
  // ref so a re-render of the accounts page never restarts the QR session.
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const fail = (err: unknown) => {
      if (cancelled) return
      if (axios.isAxiosError(err) && err.response?.status === 410) {
        setPhase('expired')
        return
      }
      setError(errorDetail(err) || t('accounts.connectError'))
      setPhase('error')
    }

    const importAccounts = async (sessionId: string) => {
      setPhase('importing')
      onBusyChange(true)
      try {
        await connections.handleCallback(sessionId, provider, undefined, undefined, reconnectConnectionId)
        // A reconnect only stores the new session; pull the data while it is fresh.
        if (reconnectConnectionId) await connections.sync(reconnectConnectionId)
        if (cancelled) return
        invalidateFinancialQueries(queryClient)
        queryClient.invalidateQueries({ queryKey: ['connections'] })
        toast.success(t(reconnectConnectionId ? 'accounts.reconnected' : 'accounts.connected'))
        onCloseRef.current()
      } catch (err) {
        fail(err)
      } finally {
        onBusyChange(false)
      }
    }

    const poll = async (sessionId: string) => {
      if (cancelled) return
      try {
        const state = await connections.getQrSession(provider, sessionId)
        if (cancelled) return
        if (state.status === 'ready') {
          setQr(null)
          await importAccounts(sessionId)
          return
        }
        if (state.status === 'error') {
          setError(state.error || t('accounts.connectError'))
          setPhase('error')
          return
        }
        setPhase(state.status)
        setQr(state.qr)
      } catch (err) {
        fail(err)
        return
      }
      timer = setTimeout(() => poll(sessionId), POLL_MS)
    }

    connections.getConnectToken(provider).then(poll, fail)

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [provider, reconnectConnectionId, queryClient, t, onBusyChange])

  return (
    <>
      <div className="flex min-h-[260px] flex-col items-center justify-center gap-3 text-center">
        {phase === 'waiting_for_scan' && qr ? (
          <>
            <img
              src={`data:image/png;base64,${qr}`}
              alt={t(`${I18N}.qrAlt`)}
              className="h-56 w-56 rounded-md bg-white p-2"
              style={{ imageRendering: 'pixelated' }}
            />
            <p className="text-sm text-muted-foreground">{t(`${I18N}.waiting`)}</p>
          </>
        ) : phase === 'expired' ? (
          <p className="text-sm text-muted-foreground">{t(`${I18N}.expired`)}</p>
        ) : phase === 'error' ? (
          <p className="text-sm text-destructive" role="alert">{error}</p>
        ) : (
          <>
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-sm text-muted-foreground">
              {t(`${I18N}.${phase === 'waiting_for_scan' ? 'starting' : phase}`)}
            </p>
          </>
        )}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={phase === 'importing'}>
          {t('common.cancel')}
        </Button>
        {(phase === 'expired' || phase === 'error') && (
          <Button onClick={onRetry}>{t(`${I18N}.retry`)}</Button>
        )}
      </DialogFooter>
    </>
  )
}
