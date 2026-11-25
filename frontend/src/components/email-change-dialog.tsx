import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { useAuth } from '@/contexts/auth-context'
import { auth } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

/**
 * An administrator asked to change the address on this account (ADR 0042,
 * #556). The address is a recovery credential, so the request waits here for
 * the person it belongs to: until they accept, the account keeps the address
 * it has and every credential tied to it.
 */
export function EmailChangeDialog() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  // The address that was put away, not a flag: an administrator can replace a
  // request that is still waiting, and the new one has to be read by its owner.
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [deciding, setDeciding] = useState<'accept' | 'decline' | null>(null)

  const pendingEmail = user?.pending_email ?? null
  const open = Boolean(pendingEmail) && pendingEmail !== dismissed

  const accept = async () => {
    if (!user || !pendingEmail) return
    setDeciding('accept')
    try {
      const updated = await auth.confirmEmailChange(pendingEmail)
      updateUser(updated)
      toast.success(t('emailChange.accepted', { email: updated.email }))
      setDismissed(pendingEmail)
    } catch {
      // The address may have been taken by someone else in the meantime.
      toast.error(t('emailChange.unavailable'))
    } finally {
      setDeciding(null)
    }
  }

  const decline = async () => {
    if (!user) return
    setDeciding('decline')
    try {
      await auth.declineEmailChange()
      updateUser({ ...user, pending_email: null })
      toast.success(t('emailChange.declined'))
      setDismissed(pendingEmail)
    } catch {
      toast.error(t('emailChange.failed'))
    } finally {
      setDeciding(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(value) => !value && setDismissed(pendingEmail)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('emailChange.title')}</DialogTitle>
          <DialogDescription>
            {t('emailChange.description', { email: pendingEmail ?? '' })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button
            type="button"
            variant="outline"
            onClick={decline}
            disabled={deciding !== null}
          >
            {t('emailChange.decline')}
          </Button>
          <Button type="button" onClick={accept} disabled={deciding !== null}>
            {deciding === 'accept' ? t('common.loading') : t('emailChange.accept')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
