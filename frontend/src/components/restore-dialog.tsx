import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { HardDriveUpload, Info, TriangleAlert } from 'lucide-react'
import { backup as backupApi } from '@/lib/api'
import { useWorkspace } from '@/contexts/workspace-context'
import type { BackupRestoreResult } from '@/types'
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

interface RestoreDialogProps {
  open: boolean
  onClose: () => void
}

/** The counts worth showing after a restore, by their `entity_counts` key. */
const COUNT_KEYS = ['accounts', 'transactions', 'categories', 'rules'] as const

/**
 * What the backup leaves out, in the order worth reading. The family names come
 * from the archive (`backup_service.OUT_OF_SCOPE_FAMILIES`); a family this list
 * does not know is not shown, so a newer archive never prints a raw key.
 */
const LEFT_OUT_KEYS = [
  { family: 'invoices', label: 'backup.restore.leftOut.invoices' },
  { family: 'reconciliation', label: 'backup.restore.leftOut.reconciliation' },
  { family: 'connections', label: 'backup.restore.leftOut.connections' },
  { family: 'plugins', label: 'backup.restore.leftOut.plugins' },
  { family: 'ui_layouts', label: 'backup.restore.leftOut.uiLayouts' },
  { family: 'mortgage_allocations', label: 'backup.restore.leftOut.mortgageAllocations' },
] as const

/**
 * Restores an archive into a brand-new workspace.
 *
 * The dialog says what the API guarantees: nothing in the workspace the user
 * has open is touched. That is the whole reason a restore asks no "are you
 * sure you want to overwrite?" — there is nothing to overwrite. The result
 * screen names the new workspace and offers to switch to it; the restored data
 * never lands under the name of the book the user was already looking at.
 */
export function RestoreDialog({ open, onClose }: RestoreDialogProps) {
  const { t } = useTranslation()
  const { refresh, switchWorkspace } = useWorkspace()
  const [file, setFile] = useState<File | null>(null)
  const [password, setPassword] = useState('')
  const [restoring, setRestoring] = useState(false)
  const [result, setResult] = useState<BackupRestoreResult | null>(null)
  const [error, setError] = useState('')
  // Each dialog session is numbered. Closing bumps the number, so a restore
  // that finishes after the user dismissed the dialog cannot repaint it: the
  // result screen they closed does not come back the next time they open it.
  const session = useRef(0)

  const handleClose = () => {
    session.current += 1
    setFile(null)
    setPassword('')
    setResult(null)
    setError('')
    setRestoring(false)
    onClose()
  }

  // `?? 0` because an older instance's response may predate the field.
  const leftOut: { family: string; label: string }[] = result
    ? LEFT_OUT_KEYS.filter(({ family }) => (result.left_out?.[family] ?? 0) > 0)
    : []

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!file || restoring) return
    const current = ++session.current
    setError('')
    setRestoring(true)
    try {
      const restored = await backupApi.restore(file, password || undefined)
      // The new workspace exists either way, so the switcher learns about it
      // even when the dialog that asked for it is already gone.
      await refresh()
      if (session.current !== current) return
      setResult(restored)
      toast.success(t('backup.restore.successToast', { name: restored.workspace_name }))
    } catch (err: unknown) {
      if (session.current !== current) return
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail || t('backup.restore.error'))
    } finally {
      if (session.current === current) setRestoring(false)
    }
  }

  const handleOpenWorkspace = async () => {
    if (!result) return
    await switchWorkspace(result.workspace_id)
    handleClose()
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('backup.restore.title')}</DialogTitle>
          <DialogDescription>{t('backup.restore.description')}</DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-4">
            <p className="text-sm font-medium">
              {t('backup.restore.success', { name: result.workspace_name })}
            </p>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md bg-muted p-3 text-sm">
              {COUNT_KEYS.map((key) => (
                <div key={key} className="flex items-center justify-between gap-2">
                  <dt className="text-muted-foreground">{t(`backup.restore.counts.${key}`)}</dt>
                  <dd className="font-medium tabular-nums">
                    {result.entity_counts[key] ?? 0}
                  </dd>
                </div>
              ))}
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted-foreground">{t('backup.restore.counts.attachments')}</dt>
                <dd className="font-medium tabular-nums">{result.attachments_restored}</dd>
              </div>
            </dl>
            {result.attachments_missing > 0 && (
              <p className="text-xs text-muted-foreground">
                {t('backup.restore.attachmentsMissing', { missing: result.attachments_missing })}
              </p>
            )}
            {leftOut.length > 0 && (
              <div className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
                <p className="flex items-center gap-2 font-medium">
                  <TriangleAlert size={14} className="shrink-0" />
                  {t('backup.restore.leftOut.title')}
                </p>
                <p className="text-muted-foreground">{t('backup.restore.leftOut.description')}</p>
                <ul className="space-y-0.5">
                  {leftOut.map(({ family, label }) => (
                    <li key={family} className="flex items-center justify-between gap-2">
                      <span>{t(label)}</span>
                      <span className="tabular-nums">{result.left_out?.[family]}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                {t('backup.restore.close')}
              </Button>
              <Button type="button" onClick={handleOpenWorkspace}>
                {t('backup.restore.openWorkspace')}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="restore-file">{t('backup.restore.fileLabel')}</Label>
              <Input
                id="restore-file"
                type="file"
                accept=".zip,application/zip"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null)
                  setError('')
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="restore-password">{t('backup.restore.passwordLabel')}</Label>
              <Input
                id="restore-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="off"
                placeholder={t('backup.restore.passwordPlaceholder')}
              />
            </div>
            <p className="flex items-start gap-2 rounded-md bg-muted p-3 text-xs text-muted-foreground">
              <Info size={14} className="mt-0.5 shrink-0" />
              {t('backup.restore.newWorkspaceNote')}
            </p>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={!file || restoring}>
                <HardDriveUpload size={14} className="me-2" />
                {restoring ? t('backup.restore.restoring') : t('backup.restore.submit')}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
