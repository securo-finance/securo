import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

/** One per-account POST of a combined fan-out (§7.2 item 3). */
export interface ImportReportAccountLine {
  name: string
  imported: number
  skipped: number
}

export interface ImportReportEntry {
  /** The raw Monarch category value (§4.1). */
  value: string
  rows: number
  status: 'mapped' | 'uncategorized' | 'rules'
  /** Actual submitted category ID, never inferred from a display label. */
  targetId: string | null
  /** Name resolved by matching ID, falling back to that ID if unavailable. */
  targetName: string | null
}

interface ImportReportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  entries: ImportReportEntry[]
  imported: number
  skipped: number
  failedRows: number
  /** Combined fan-out: one line per target account (§7.2). */
  accountLines?: ImportReportAccountLine[]
  routingSkipped?: number
}

/** End-of-import report (§4.4): per Monarch category value — rows, resolved
    target, and mapped / uncategorized / left-to-rules — plus the failed and
    dupe-skip counts. The footnote keeps it honest: the user's own rules may
    have reassigned rows after import. Pattern precedent: the failed-rows
    dialog in import.tsx. */
export function ImportReportDialog({
  open,
  onOpenChange,
  entries,
  imported,
  skipped,
  failedRows,
  accountLines,
  routingSkipped = 0,
}: ImportReportDialogProps) {
  const { t } = useTranslation()

  const statusLabel: Record<ImportReportEntry['status'], string> = {
    mapped: t('import.reportMapped'),
    uncategorized: t('import.reportUncategorized'),
    rules: t('import.reportLeftToRules'),
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col p-6">
        <DialogHeader>
          <DialogTitle className="text-base font-semibold">
            {t('import.reportTitle')}
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground mt-1">
            {t('import.reportSummary', {
              imported,
              skipped,
              failed: failedRows,
            })}
          </DialogDescription>
        </DialogHeader>
        <p>{t('import.routingSkipped', { count: routingSkipped })}</p>
        {accountLines && accountLines.length > 0 && (
          <div className="space-y-1 text-sm text-foreground mt-3" data-testid="import-report-accounts">
            {accountLines.map((line, index) => (
              <p key={index} data-testid={`import-report-account-${line.name}`}>
                {t('import.reportAccountLine', {
                  name: line.name,
                  imported: line.imported,
                  skipped: line.skipped,
                })}
              </p>
            ))}
          </div>
        )}
        <div
          className="flex-1 border border-border rounded-lg overflow-hidden bg-card text-foreground mt-4 max-h-[50vh] overflow-y-auto"
          data-testid="import-report-list"
        >
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="bg-muted text-muted-foreground border-b border-border sticky top-0">
                <th className="px-3 py-2 font-semibold">{t('import.categoryMapping')}</th>
                <th className="px-3 py-2 font-semibold w-16">{t('import.reportRows')}</th>
                <th className="px-3 py-2 font-semibold">{t('import.reportTarget')}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr
                  key={JSON.stringify([entry.value, entry.status, entry.targetId])}
                  data-report-key={JSON.stringify([entry.value, entry.status, entry.targetId])}
                  className="border-b border-border last:border-0 hover:bg-muted/50"
                  data-testid={`report-row-${entry.value}`}
                >
                  <td className="px-3 py-2" title={entry.value}>
                    {entry.value}
                  </td>
                  <td className="px-3 py-2">{entry.rows}</td>
                  <td
                    title={entry.targetId ?? undefined}
                    className={
                      entry.status === 'mapped'
                        ? 'px-3 py-2 text-foreground'
                        : 'px-3 py-2 text-amber-600'
                    }
                  >
                    {entry.status === 'mapped' ? entry.targetName : statusLabel[entry.status]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-muted-foreground mt-3">{t('import.reportFootnote')}</p>
        <DialogFooter className="mt-4 pt-4 border-t border-border flex justify-end">
          <Button onClick={() => onOpenChange(false)} size="sm">
            {t('common.close')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
