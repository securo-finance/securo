import { useTranslation } from 'react-i18next'
import type { DryRunSummary, ImportReviewTransaction } from '@/types'
import { formatCurrency } from '@/lib/format'

interface ImportSummaryBarProps {
  transactions: ImportReviewTransaction[]
  userCurrency: string
  locale: string
  /** Read-only pre-flight counts from a dry-run preview (§7.3). When the
      preview ran without dry_run (no target account yet), the bar keeps its
      original shape. */
  dryRun?: DryRunSummary | null
  /** Client-computed unmapped count: distinct preset values with no
      effective category target (§4.4 + mapping card state). Falls back to
      the server's dry-run count. */
  unmappedCount?: number
}

export function ImportSummaryBar({
  transactions,
  userCurrency,
  locale,
  dryRun,
  unmappedCount,
}: ImportSummaryBarProps) {
  const { t } = useTranslation()

  const total = transactions.length
  const included = transactions.filter(t => !t.excluded).length
  const excluded = total - included

  const balanceImpact = transactions
    .filter(t => !t.excluded)
    .reduce((sum, t) => sum + (t.type === 'credit' ? Number(t.amount) : -Number(t.amount)), 0)

  const unmapped = unmappedCount ?? dryRun?.unmapped_categories ?? 0

  return (
    <div className="sticky top-0 z-10 bg-card border border-border px-5 py-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
      <span className="text-muted-foreground">
        {total} {t('import.transactionsFound')}
      </span>
      <span className="text-foreground font-medium">
        {included} {t('import.willBeImported')}
      </span>
      {excluded > 0 && (
        <span className="text-muted-foreground line-through">
          {excluded} {t('import.excluded').toLowerCase()}
        </span>
      )}
      {dryRun && (
        <>
          <span className="text-muted-foreground" data-testid="summary-rows-in">
            {dryRun.rows_in} {t('import.summaryRowsIn')}
          </span>
          <span className="text-foreground font-medium" data-testid="summary-will-import">
            {dryRun.would_import} {t('import.summaryWillImport')}
          </span>
          <span
            className={
              dryRun.would_skip_dupes > 0 ? 'text-amber-600' : 'text-muted-foreground'
            }
            data-testid="summary-dupes"
          >
            {dryRun.would_skip_dupes} {t('import.summaryDupes')}
          </span>
          <span
            className={dryRun.failed_rows > 0 ? 'text-rose-500' : 'text-muted-foreground'}
            data-testid="summary-failed"
          >
            {dryRun.failed_rows} {t('import.summaryFailed')}
          </span>
          <span
            className={unmapped > 0 ? 'text-amber-600' : 'text-muted-foreground'}
            data-testid="summary-unmapped"
          >
            {unmapped} {t('import.summaryUnmapped')}
          </span>
        </>
      )}
      <span className="ml-auto text-xs text-muted-foreground">
        {t('import.balanceImpact')}: {' '}
        <span className={balanceImpact >= 0 ? 'text-emerald-600 font-medium' : 'text-rose-500 font-medium'}>
          {formatCurrency(balanceImpact, userCurrency, locale)}
        </span>
      </span>
    </div>
  )
}
