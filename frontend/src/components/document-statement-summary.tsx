import { useTranslation } from 'react-i18next'
import { AlertTriangle, CheckCircle2, ScanText } from 'lucide-react'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import type { DocumentExtraction } from '@/types'

/**
 * What a model read from a statement, above the review table: who issued it,
 * the period, and whether the rows add up to its own balances.
 */
export function DocumentStatementSummary({ extraction }: { extraction: DocumentExtraction }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const statement = extraction.statement
  const check = extraction.balance_check
  const currency = statement?.currency ?? 'USD'
  const money = (value: string) => formatCurrency(Number(value), currency, locale)
  const kindLabel = statement?.kind === 'credit_card'
    ? t('documentAi.kindCreditCard')
    : statement?.kind === 'bank' ? t('documentAi.kindBank') : null

  return (
    <div className="px-4 sm:px-5 py-3 border-b border-border space-y-2" data-testid="document-statement-summary">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5 font-medium text-foreground">
          <ScanText size={14} className="text-primary" />
          {[statement?.institution, kindLabel].filter(Boolean).join(' · ') || t('documentAi.statement')}
        </span>
        {statement?.period_start && statement?.period_end && (
          <span>{t('documentAi.period', { start: statement.period_start, end: statement.period_end })}</span>
        )}
        <span>
          {t(extraction.method === 'vision' ? 'documentAi.readFromImages' : 'documentAi.readFromText', {
            model: extraction.model ?? '',
          })}
        </span>
      </div>

      {check && (check.ok ? (
        <div className="flex items-start gap-1.5 text-xs text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 size={14} className="mt-px shrink-0" />
          <span>{t('documentAi.balanceOk', { opening: money(check.opening_balance), closing: money(check.closing_balance) })}</span>
        </div>
      ) : (
        <div className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle size={14} className="mt-px shrink-0" />
          <span>
            {t('documentAi.balanceMismatch', {
              computed: money(check.computed_closing_balance),
              closing: money(check.closing_balance),
              difference: money(check.difference),
            })}
          </span>
        </div>
      ))}

      {extraction.dropped > 0 && (
        <div className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle size={14} className="mt-px shrink-0" />
          <span>{t('documentAi.droppedLines', { count: extraction.dropped })}</span>
        </div>
      )}
      {extraction.truncated && (
        <div className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle size={14} className="mt-px shrink-0" />
          <span>{t('documentAi.truncated', { count: extraction.pages ?? 0 })}</span>
        </div>
      )}
      {extraction.unread_pages.length > 0 && (
        <div className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle size={14} className="mt-px shrink-0" />
          <span>{t('documentAi.unreadPages', { pages: extraction.unread_pages.join(', ') })}</span>
        </div>
      )}
      {extraction.suggested_account_id && (
        <p className="text-xs text-muted-foreground">{t('documentAi.suggestedAccount')}</p>
      )}
    </div>
  )
}
