import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'

/** Shared spending-versus-budget bar for Overview and Budgets. */
export function BudgetSpendingProgress({ percentage, budgetAmount, currency, categoryName, showBudgetLabel = true }: {
  percentage: number
  budgetAmount: number
  currency: string
  categoryName: string
  showBudgetLabel?: boolean
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const barColor = percentage > 100 ? 'bg-rose-500' : percentage >= 80 ? 'bg-amber-400' : 'bg-emerald-500'

  return (
    <div className="flex items-center gap-2">
      <div
        role="progressbar"
        aria-label={`${t('dashboard.spendingByCategory')}: ${categoryName}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(Math.max(percentage, 0), 100)}
        aria-valuetext={mask(`${percentage}%`)}
        className="flex-1 h-1.5 bg-muted/60 rounded-full overflow-hidden"
      >
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${Math.min(Math.max(percentage, 0), 100)}%` }}
        />
      </div>
      {showBudgetLabel && <span className={`text-[11px] tabular-nums font-medium shrink-0 ${percentage > 100 ? 'text-rose-500' : percentage >= 80 ? 'text-amber-500' : 'text-muted-foreground'}`}>
        {mask(t('dashboard.ofBudget', { budget: formatCurrency(budgetAmount, currency, locale) }))}
      </span>}
    </div>
  )
}
