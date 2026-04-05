import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { connections } from '@/lib/api'
import type { BankConnection } from '@/types'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface ConnectionSettingsDialogProps {
  open: boolean
  onClose: () => void
  connection: BankConnection | null
}

export function ConnectionSettingsDialog({
  open,
  onClose,
  connection,
}: ConnectionSettingsDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [displayName, setDisplayName] = useState('')
  const [billImportEnabled, setBillImportEnabled] = useState(true)

  useEffect(() => {
    if (!connection) return
    setDisplayName(connection.settings?.display_name ?? connection.institution_name)
    setBillImportEnabled(connection.settings?.bill_import_enabled ?? true)
  }, [connection])

  const mutation = useMutation({
    mutationFn: () =>
      connections.updateSettings(connection!.id, {
        display_name: displayName.trim(),
        bill_import_enabled: billImportEnabled,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      toast.success(t('accounts.updated'))
      onClose()
    },
    onError: () => toast.error(t('common.error')),
  })

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('connections.settings')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="connection-name">{t('connections.displayName')}</Label>
            <Input
              id="connection-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder={connection?.institution_name}
              required
            />
            <p className="text-sm text-muted-foreground">{t('connections.displayNameHint')}</p>
          </div>
          <div className="flex items-center justify-between gap-4">
            <div className="space-y-1">
              <Label htmlFor="connection-bill-import">{t('connections.billImportEnabled')}</Label>
              <p className="text-sm text-muted-foreground">{t('connections.billImportHint')}</p>
            </div>
            <input
              id="connection-bill-import"
              type="checkbox"
              checked={billImportEnabled}
              onChange={(event) => setBillImportEnabled(event.target.checked)}
              className="h-4 w-4 rounded border-border text-primary focus:ring-primary"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || !displayName.trim()}>
            {mutation.isPending ? t('common.loading') : t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
