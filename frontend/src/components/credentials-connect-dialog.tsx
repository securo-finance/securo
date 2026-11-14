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
import { Input } from '@/components/ui/input'
import { toast } from 'sonner'

interface CredentialsConnectDialogProps {
  open: boolean
  onClose: () => void
  provider: string
  displayName: string
  fields: string[]
  reconnectConnectionId?: string
}

const I18N = 'accounts.credentialsConnect'
const SECRET_FIELDS = new Set(['password'])

/**
 * Connect a provider that logs in with the user's own details (flow_type
 * "credentials": Max, Isracard). The details go to the backend once, which
 * stores them encrypted; the form never keeps them after it closes.
 */
export function CredentialsConnectDialog({
  open,
  onClose,
  provider,
  displayName,
  fields,
  reconnectConnectionId,
}: CredentialsConnectDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [values, setValues] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const isReconnect = Boolean(reconnectConnectionId)
  const complete = fields.length > 0 && fields.every((f) => (values[f] ?? '').trim())

  // Forget whatever was typed as soon as the dialog closes.
  useEffect(() => {
    if (open) return
    const forget = setTimeout(() => setValues({}), 0)
    return () => clearTimeout(forget)
  }, [open])

  const handleSubmit = async () => {
    if (!complete) return
    setSubmitting(true)
    try {
      await connections.handleCallback(
        JSON.stringify(values),
        provider,
        undefined,
        undefined,
        reconnectConnectionId,
      )
      // A reconnect only stores the new login; read with it right away.
      if (reconnectConnectionId) await connections.sync(reconnectConnectionId)
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t(isReconnect ? 'accounts.reconnected' : 'accounts.connected'))
      setValues({})
      onClose()
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
      const message = typeof detail === 'string' ? detail : detail?.message
      toast.error(message || t('accounts.connectError'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !submitting && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {t(isReconnect ? `${I18N}.reconnectTitle` : `${I18N}.title`, { name: displayName })}
          </DialogTitle>
          <p className="text-sm text-muted-foreground">{t(`${I18N}.description`, { name: displayName })}</p>
        </DialogHeader>

        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            void handleSubmit()
          }}
        >
          {fields.map((field) => (
            <div key={field} className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor={`cred-${field}`}>
                {t(`${I18N}.fields.${field}`, field)}
              </label>
              <Input
                id={`cred-${field}`}
                type={SECRET_FIELDS.has(field) ? 'password' : 'text'}
                inputMode={field === 'id' || field === 'card6Digits' ? 'numeric' : undefined}
                autoComplete={SECRET_FIELDS.has(field) ? 'current-password' : 'off'}
                value={values[field] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
                disabled={submitting}
              />
            </div>
          ))}
          <p className="text-xs text-muted-foreground">
            {submitting ? t(`${I18N}.reading`) : t(`${I18N}.storageNote`, { name: displayName })}
          </p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!complete || submitting}>
              {submitting ? t(`${I18N}.connecting`) : t(isReconnect ? `${I18N}.reconnect` : `${I18N}.connect`)}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
