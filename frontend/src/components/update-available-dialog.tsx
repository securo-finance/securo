import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Check, Copy, Download, Info, Sparkles, Bug, Server, CheckCircle2, BellOff } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { APP_VERSION } from '@/lib/build-info'
import { useAutoUpdateCheck } from '@/hooks/use-auto-update-check'
import { useLatestRelease } from '@/hooks/use-latest-release'
import { isUpdateAvailable } from '@/lib/semver'

const UPGRADE_COMMAND = 'git pull && docker compose up -d --build'

interface UpdateAvailableDialogProps {
  open: boolean
  onClose: () => void
}

export function UpdateAvailableDialog({ open, onClose }: UpdateAvailableDialogProps) {
  const { t } = useTranslation()
  const { enabled, setEnabled } = useAutoUpdateCheck()
  const { data, isFetching } = useLatestRelease()
  const [copied, setCopied] = useState(false)

  const hasUpdate = data ? isUpdateAvailable(APP_VERSION, data.tagName) : false

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(UPGRADE_COMMAND)
      setCopied(true)
      toast.success(t('update.copied'))
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error(t('update.copyFailed'))
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <div className="flex items-center justify-between gap-3 pr-6">
            <DialogTitle>{t('update.title')}</DialogTitle>
            <span className="inline-flex items-center gap-1.5 rounded-full border bg-muted/60 px-2.5 py-1 text-[11px] font-medium text-muted-foreground">
              <Server size={12} />
              <span className="tabular-nums font-semibold text-foreground">{APP_VERSION}</span>
              <span className="h-1 w-1 rounded-full bg-muted-foreground/40" />
              <span>{t('update.runningLabel')}</span>
            </span>
          </div>
        </DialogHeader>

        <div className="space-y-5">
          {!enabled && (
            <div className="flex items-start gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3.5 py-3 text-sm text-amber-700 dark:text-amber-400">
              <BellOff size={16} className="shrink-0 mt-0.5" />
              <p className="leading-relaxed">{t('update.autoCheckDisabled')}</p>
            </div>
          )}

          {enabled && isFetching && !data && (
            <div className="rounded-lg border bg-muted/40 px-3.5 py-3 text-sm text-muted-foreground">
              {t('update.checking')}
            </div>
          )}

          {enabled && data && !hasUpdate && (
            <div className="flex items-center gap-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-3 text-sm font-medium text-emerald-700 dark:text-emerald-400">
              <CheckCircle2 size={18} className="shrink-0" />
              <span className="flex-1">
                {t('update.upToDate')} <span className="tabular-nums font-bold">{data.tagName}</span>
              </span>
            </div>
          )}

          {enabled && hasUpdate && data && (
            <>
              <div className="flex items-center gap-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-3 text-sm font-semibold text-emerald-700 dark:text-emerald-400">
                <Download size={18} className="shrink-0" />
                <span className="flex-1">
                  {t('update.newVersionAvailable')} <span className="font-bold tabular-nums">{data.tagName}</span>
                </span>
              </div>

              <div className="space-y-3">
                <p className="text-sm text-foreground/80">{t('update.description')}</p>
                <ul className="space-y-2 text-sm text-foreground/80">
                  <li className="flex items-start gap-2.5">
                    <Sparkles size={15} className="text-primary shrink-0 mt-0.5" />
                    <span>{t('update.reasonFeatures')}</span>
                  </li>
                  <li className="flex items-start gap-2.5">
                    <Bug size={15} className="text-primary shrink-0 mt-0.5" />
                    <span>{t('update.reasonBugs')}</span>
                  </li>
                </ul>
              </div>

              <div className="flex items-start gap-2.5 rounded-lg border border-blue-500/20 bg-blue-500/10 px-3.5 py-3 text-sm text-blue-700 dark:text-blue-300">
                <Info size={16} className="shrink-0 mt-0.5" />
                <p className="leading-relaxed">
                  {t('update.releaseNotesHintBefore')}{' '}
                  <a
                    href={data.htmlUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="font-medium underline underline-offset-2 hover:opacity-80"
                  >
                    {t('update.releaseNotesLink')}
                  </a>{' '}
                  {t('update.releaseNotesHintAfter')}
                </p>
              </div>

              <div className="space-y-2">
                <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  {t('update.commandLabel')}
                </div>
                <code className="block rounded-md border bg-muted/50 px-3 py-2.5 font-mono text-xs text-foreground break-all">
                  <span className="select-none text-muted-foreground mr-2">$</span>
                  {UPGRADE_COMMAND}
                </code>
              </div>
            </>
          )}

          <div className="flex items-center justify-between gap-3 rounded-lg border bg-muted/30 px-3.5 py-2.5">
            <label htmlFor="auto-update-check" className="text-sm font-medium cursor-pointer select-none">
              {t('update.autoCheckToggle')}
            </label>
            <Switch id="auto-update-check" checked={enabled} onCheckedChange={setEnabled} />
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            {hasUpdate ? t('common.cancel') : t('common.close')}
          </Button>
          {hasUpdate && (
            <Button type="button" onClick={handleCopy}>
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? t('update.copied') : t('update.copyCommand')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
