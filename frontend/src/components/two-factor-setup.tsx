import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { QRCodeSVG } from 'qrcode.react'
import { useAuth } from '@/contexts/auth-context'
import { auth } from '@/lib/api'
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

interface TwoFactorSetupProps {
  open: boolean
  onClose: () => void
  localAuthEnabled?: boolean
}

export function TwoFactorSetup({ open, onClose, localAuthEnabled = true }: TwoFactorSetupProps) {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const is2faEnabled = user?.is_2fa_enabled ?? false

  // Enable flow
  const [secret, setSecret] = useState('')
  const [otpauthUri, setOtpauthUri] = useState('')
  const [setupCode, setSetupCode] = useState('')
  const [setupLoading, setSetupLoading] = useState(false)
  const [setupStep, setSetupStep] = useState<'idle' | 'qr'>('idle')
  const [error, setError] = useState('')

  // Disable flow
  const [disablePassword, setDisablePassword] = useState('')
  const [disableCode, setDisableCode] = useState('')
  const [disableLoading, setDisableLoading] = useState(false)

  const handleSetup = async () => {
    if (!localAuthEnabled) return
    setSetupLoading(true)
    setError('')
    try {
      const data = await auth.setup2fa()
      setSecret(data.secret)
      setOtpauthUri(data.otpauth_uri)
      setSetupStep('qr')
    } catch {
      setError(t('common.error'))
    } finally {
      setSetupLoading(false)
    }
  }

  const handleEnable = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!localAuthEnabled) return
    setSetupLoading(true)
    setError('')
    try {
      await auth.enable2fa(setupCode)
      toast.success(t('auth.twoFactorEnabled'))
      if (user) updateUser({ ...user, is_2fa_enabled: true })
      handleClose()
    } catch {
      setError(t('auth.invalid2faCode'))
    } finally {
      setSetupLoading(false)
    }
  }

  const handleDisable = async (e: React.FormEvent) => {
    e.preventDefault()
    setDisableLoading(true)
    setError('')
    try {
      await auth.disable2fa(disablePassword, disableCode)
      toast.success(t('auth.twoFactorDisabled'))
      if (user) updateUser({ ...user, is_2fa_enabled: false })
      handleClose()
    } catch {
      setError(t('auth.invalid2faCode'))
    } finally {
      setDisableLoading(false)
    }
  }

  const handleClose = () => {
    setSecret('')
    setOtpauthUri('')
    setSetupCode('')
    setSetupStep('idle')
    setError('')
    setDisablePassword('')
    setDisableCode('')
    onClose()
  }

  if (is2faEnabled) {
    // Disable flow
    return (
      <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('auth.disable2fa')}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleDisable} className="space-y-4">
            <p className="text-sm text-muted-foreground">{t('auth.disable2faDescription')}</p>
            <div className="space-y-2">
              <Label htmlFor="disable-password">{t('auth.password')}</Label>
              <Input
                id="disable-password"
                type="password"
                value={disablePassword}
                onChange={(e) => setDisablePassword(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="disable-code">{t('auth.twoFactor')}</Label>
              <Input
                id="disable-code"
                type="text"
                inputMode="numeric"
                value={disableCode}
                onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="000000"
                className="text-center text-lg tracking-[0.3em] font-mono"
                maxLength={6}
                required
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" variant="destructive" disabled={disableLoading || disableCode.length !== 6}>
                {disableLoading ? t('common.loading') : t('auth.disable2fa')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    )
  }

  // Enrollment is a local-credential feature: with local auth off the backend
  // refuses /2fa/setup, so the dialog exists only to let an already-enrolled
  // user disable 2FA.
  if (!localAuthEnabled) return null

  // Enable flow
  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('auth.setup2fa')}</DialogTitle>
        </DialogHeader>

        {setupStep === 'idle' ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">{t('auth.setup2faDescription')}</p>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                {t('common.cancel')}
              </Button>
              <Button onClick={handleSetup} disabled={setupLoading}>
                {setupLoading ? t('common.loading') : t('auth.enable2fa')}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={handleEnable} className="space-y-4">
            <div className="flex flex-col items-center gap-4">
              <div className="bg-white p-3 rounded-lg">
                <QRCodeSVG value={otpauthUri} size={180} />
              </div>
              <div className="text-center">
                <p className="text-xs text-muted-foreground mb-1">{t('auth.manualEntry')}</p>
                <code className="text-xs bg-muted px-2 py-1 rounded select-all">{secret}</code>
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="setup-code">{t('auth.twoFactor')}</Label>
              <Input
                id="setup-code"
                type="text"
                inputMode="numeric"
                value={setupCode}
                onChange={(e) => setSetupCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="000000"
                className="text-center text-lg tracking-[0.3em] font-mono"
                maxLength={6}
                required
                autoFocus
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={setupLoading || setupCode.length !== 6}>
                {setupLoading ? t('common.loading') : t('auth.verify')}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
