import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { ShieldCheck } from 'lucide-react'
import { backup as backupApi } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const MIN_PASSWORD_LENGTH = 8

interface BackupDialogProps {
  open: boolean
  onClose: () => void
}

/**
 * Asks for an optional password before downloading the workspace archive.
 *
 * Empty means the plain zip Securo has always produced. A password produces an
 * AES-256 zip, which any standard archiver can open, so the backup stays usable
 * even without Securo. Nothing about the password is sent anywhere else or
 * stored: lose it and the archive is gone, which the dialog says out loud.
 */
export function BackupDialog({ open, onClose }: BackupDialogProps) {
  const { t } = useTranslation()
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')

  const handleClose = () => {
    setPassword('')
    setConfirmPassword('')
    setError('')
    onClose()
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (password && password.length < MIN_PASSWORD_LENGTH) {
      setError(t('backup.passwordTooShort', { min: MIN_PASSWORD_LENGTH }))
      return
    }
    if (password !== confirmPassword) {
      setError(t('setup.passwordMismatch'))
      return
    }

    setDownloading(true)
    try {
      await backupApi.download(password || undefined)
      toast.success(password ? t('backup.successEncrypted') : t('backup.success'))
      handleClose()
    } catch {
      toast.error(t('backup.error'))
    } finally {
      setDownloading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('backup.dialogTitle')}</DialogTitle>
          <DialogDescription>{t('backup.dialogDescription')}</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="backup-password">{t('backup.passwordLabel')}</Label>
            <Input
              id="backup-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              placeholder={t('backup.passwordPlaceholder')}
            />
          </div>
          {password && (
            <div className="space-y-2">
              <Label htmlFor="backup-password-confirm">{t('setup.confirmPassword')}</Label>
              <Input
                id="backup-password-confirm"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
              />
            </div>
          )}
          {password && (
            <p className="flex items-start gap-2 rounded-md bg-muted p-3 text-xs text-muted-foreground">
              <ShieldCheck size={14} className="mt-0.5 shrink-0" />
              {t('backup.encryptionNote')}
            </p>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={handleClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={downloading}>
              {downloading ? t('backup.downloading') : t('backup.button')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
