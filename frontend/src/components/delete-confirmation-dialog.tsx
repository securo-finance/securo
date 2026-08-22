import { useTranslation } from 'react-i18next'
import { Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

type DeleteConfirmationDialogProps = {
  open: boolean
  title: string
  description: string
  isPending: boolean
  onClose: () => void
  onCancel?: () => void
  onConfirm: () => void
}

export function DeleteConfirmationDialog({
  open,
  title,
  description,
  isPending,
  onClose,
  onCancel = onClose,
  onConfirm,
}: DeleteConfirmationDialogProps) {
  const { t } = useTranslation()

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && !isPending) onClose()
      }}
    >
      <DialogContent className="sm:max-w-md" showCloseButton={!isPending}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={isPending}>
            {t('common.cancel')}
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={isPending}>
            <Trash2 size={14} className="mr-1" />
            {t('common.delete')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
