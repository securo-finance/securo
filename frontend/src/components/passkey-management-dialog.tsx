import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Fingerprint, Loader2, TriangleAlert, X } from 'lucide-react'
import { auth } from '@/lib/api'
import { passkeyBlocker, passkeyFailure, startPasskeyRegistration } from '@/lib/webauthn'
import type { PasskeyFailure } from '@/lib/webauthn'
import type { Passkey } from '@/types'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface PasskeyManagementDialogProps {
  open: boolean
  onClose: () => void
}

const FAILURE_KEYS: Record<PasskeyFailure, string> = {
  cancelled: 'auth.passkeyCancelled',
  duplicate: 'auth.passkeyDuplicate',
  domain: 'auth.passkeyDomainError',
  mismatch: 'auth.passkeyDomainMismatch',
  ip: 'auth.passkeyIpAddress',
  insecure: 'auth.passkeyInsecureContext',
  unsupported: 'auth.passkeyUnsupported',
  unknown: 'auth.passkeyRegisterError',
}

export function PasskeyManagementDialog({ open, onClose }: PasskeyManagementDialogProps) {
  const { t } = useTranslation()
  const [passkeys, setPasskeys] = useState<Passkey[]>([])
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const blocker = passkeyBlocker()

  const loadPasskeys = useCallback(async () => {
    setLoading(true)
    setLoadFailed(false)
    try {
      setPasskeys(await auth.listPasskeys())
    } catch {
      setLoadFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void loadPasskeys()
  }, [open, loadPasskeys])

  const formatDate = (value: string | null) => {
    if (!value) return t('auth.passkeyNeverUsed')
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  }

  const handleClose = () => {
    setName('')
    setConfirmDeleteId(null)
    onClose()
  }

  const handleRegister = async (event: React.FormEvent) => {
    event.preventDefault()
    const passkeyName = name.trim() || t('auth.defaultPasskeyName')
    setSaving(true)
    try {
      const options = await auth.registerPasskeyOptions(passkeyName)
      const credential = await startPasskeyRegistration(options.options)
      const created = await auth.verifyPasskeyRegistration(options.challenge_id, passkeyName, credential)
      setPasskeys((current) => [...current, created])
      setName('')
      toast.success(t('auth.passkeyAdded'))
    } catch (err) {
      toast.error(t(FAILURE_KEYS[passkeyFailure(err)]))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (passkey: Passkey) => {
    setDeletingId(passkey.id)
    try {
      await auth.deletePasskey(passkey.id)
      setPasskeys((current) => current.filter((item) => item.id !== passkey.id))
      setConfirmDeleteId(null)
      toast.success(t('auth.passkeyDeleted'))
    } catch {
      toast.error(t('auth.passkeyDeleteError'))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('auth.passkeysTitle')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">{t('auth.passkeysDescription')}</p>

          {blocker && (
            <div className="flex items-start gap-2.5 rounded-lg bg-amber-500/10 px-3 py-2.5 text-sm text-amber-700 dark:text-amber-300">
              <TriangleAlert size={16} className="mt-0.5 shrink-0" />
              <p>{t(FAILURE_KEYS[blocker])}</p>
            </div>
          )}

          <form onSubmit={handleRegister} className="space-y-3 rounded-lg border p-3">
            <div className="space-y-1.5">
              <Label htmlFor="passkey-name">{t('auth.passkeyName')}</Label>
              <Input
                id="passkey-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t('auth.passkeyNamePlaceholder')}
                maxLength={100}
                disabled={saving || !!blocker}
              />
            </div>
            <Button type="submit" disabled={!!blocker || saving} className="w-full">
              {saving && <Loader2 size={15} className="animate-spin" />}
              {saving ? t('auth.passkeyWaiting') : t('auth.addPasskey')}
            </Button>
          </form>

          <div className="space-y-2">
            {loading ? (
              <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
            ) : loadFailed ? (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 p-3">
                <p className="text-sm text-destructive">{t('auth.passkeyLoadError')}</p>
                <Button type="button" variant="outline" size="sm" onClick={() => void loadPasskeys()}>
                  {t('common.retry')}
                </Button>
              </div>
            ) : passkeys.length === 0 ? (
              <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed px-3 py-6 text-center">
                <Fingerprint size={20} className="text-muted-foreground" />
                <p className="text-sm text-muted-foreground">{t('auth.noPasskeys')}</p>
              </div>
            ) : (
              passkeys.map((passkey) => {
                const isConfirming = confirmDeleteId === passkey.id
                const isDeleting = deletingId === passkey.id

                return (
                  <div key={passkey.id} className="flex items-start gap-3 rounded-lg border p-3">
                    <div className="mt-0.5 rounded-full bg-primary/10 p-2 text-primary">
                      <Fingerprint size={16} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{passkey.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {t('auth.passkeyCreated')}: {formatDate(passkey.created_at)}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {t('auth.passkeyLastUsed')}: {formatDate(passkey.last_used_at)}
                      </p>
                    </div>
                    {isConfirming ? (
                      <div className="flex shrink-0 items-center gap-1">
                        <Button
                          type="button"
                          variant="destructive"
                          size="sm"
                          onClick={() => void handleDelete(passkey)}
                          disabled={isDeleting}
                        >
                          {isDeleting ? <Loader2 size={13} className="animate-spin" /> : t('common.delete')}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setConfirmDeleteId(null)}
                          disabled={isDeleting}
                          aria-label={t('common.cancel')}
                        >
                          <X size={14} />
                        </Button>
                      </div>
                    ) : (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="shrink-0 text-muted-foreground hover:text-destructive"
                        onClick={() => setConfirmDeleteId(passkey.id)}
                        aria-label={t('auth.deletePasskey')}
                      >
                        {t('common.delete')}
                      </Button>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={handleClose}>
            {t('common.close')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
