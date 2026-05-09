import { useTranslation } from 'react-i18next'
import type { ImportReviewTransaction } from '@/types'
import { formatCurrency } from '@/lib/format'

interface ImportSummaryBarProps {
  transactions: ImportReviewTransaction[]
  userCurrency: string
  locale: string
}

export function ImportSummaryBar({ transactions, userCurrency, locale }: ImportSummaryBarProps) {
  const { t } = useTranslation()

  const total = transactions.length
  const included = transactions.filter(t => !t.excluded).length
  const excluded = total - included

  const balanceImpact = transactions
    .filter(t => !t.excluded)
    .reduce((sum, t) => sum + (t.type === 'credit' ? Number(t.amount) : -Number(t.amount)), 0)

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
      <span className="ml-auto text-xs text-muted-foreground">
        {t('import.balanceImpact')}: {' '}
        <span className={balanceImpact >= 0 ? 'text-emerald-600 font-medium' : 'text-rose-500 font-medium'}>
          {formatCurrency(balanceImpact, userCurrency, locale)}
        </span>
      </span>
    </div>
  )
}
