import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Fingerprint, Trash2 } from 'lucide-react'
import { auth } from '@/lib/api'
import { isPasskeySupported, startPasskeyRegistration } from '@/lib/webauthn'
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

export function PasskeyManagementDialog({ open, onClose }: PasskeyManagementDialogProps) {
  const { t } = useTranslation()
  const [passkeys, setPasskeys] = useState<Passkey[]>([])
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const supported = isPasskeySupported()

  const loadPasskeys = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setPasskeys(await auth.listPasskeys())
    } catch {
      setError(t('auth.passkeyLoadError'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    if (open) void loadPasskeys()
  }, [open, loadPasskeys])

  const formatDate = (value: string | null) => {
    if (!value) return t('auth.passkeyNeverUsed')
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  }

  const handleClose = () => {
    setName('')
    setError('')
    onClose()
  }

  const handleRegister = async (event: React.FormEvent) => {
    event.preventDefault()
    const passkeyName = name.trim() || t('auth.defaultPasskeyName')
    setSaving(true)
    setError('')
    try {
      const options = await auth.registerPasskeyOptions(passkeyName)
      const credential = await startPasskeyRegistration(options.options)
      const created = await auth.verifyPasskeyRegistration(options.challenge_id, passkeyName, credential)
      setPasskeys((current) => [...current, created])
      setName('')
      toast.success(t('auth.passkeyAdded'))
    } catch (err) {
      const domError = err as { name?: string }
      const message = domError?.name === 'NotAllowedError'
        ? t('auth.passkeyCancelled')
        : t('auth.passkeyRegisterError')
      setError(message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (passkey: Passkey) => {
    if (!window.confirm(t('auth.passkeyDeleteConfirm', { name: passkey.name }))) return
    setDeletingId(passkey.id)
    setError('')
    try {
      await auth.deletePasskey(passkey.id)
      setPasskeys((current) => current.filter((item) => item.id !== passkey.id))
      toast.success(t('auth.passkeyDeleted'))
    } catch {
      setError(t('auth.passkeyDeleteError'))
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

          {!supported && (
            <div className="rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
              {t('auth.passkeyUnsupported')}
            </div>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}

          <form onSubmit={handleRegister} className="space-y-3 rounded-lg border p-3">
            <div className="space-y-1.5">
              <Label htmlFor="passkey-name">{t('auth.passkeyName')}</Label>
              <Input
                id="passkey-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t('auth.passkeyNamePlaceholder')}
                maxLength={100}
                disabled={saving}
              />
            </div>
            <Button type="submit" disabled={!supported || saving} className="w-full">
              {saving ? t('common.loading') : t('auth.addPasskey')}
            </Button>
          </form>

          <div className="space-y-2">
            {loading ? (
              <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
            ) : passkeys.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t('auth.noPasskeys')}</p>
            ) : (
              passkeys.map((passkey) => (
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
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleDelete(passkey)}
                    disabled={deletingId === passkey.id}
                    aria-label={t('auth.deletePasskey')}
                  >
                    <Trash2 size={15} />
                  </Button>
                </div>
              ))
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
