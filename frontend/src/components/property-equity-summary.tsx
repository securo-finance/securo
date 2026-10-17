import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { formatCurrency } from '@/lib/format'
import { getLoanDebtPrimary, getPropertyEquity } from '@/lib/property-equity'
import type { Account, Asset } from '@/types'

/**
 * The loans secured against a property, with its value, debt and equity.
 * Renders nothing for a property without a linked loan.
 */
export function PropertyEquitySummary({
  asset,
  loans,
  currency,
  locale,
  mask,
}: {
  asset: Asset
  loans: Account[]
  currency: string
  locale: string
  mask: (value: string) => string
}) {
  const { t } = useTranslation()
  const linked = loans.filter(loan => loan.type === 'loan' && loan.secured_asset_id === asset.id)
  const equity = getPropertyEquity(asset, linked)
  if (linked.length === 0 || !equity) return null

  return (
    <div className="px-4 py-3 border-t border-border bg-muted/20 space-y-2">
      <h3 className="text-xs font-semibold">{t('assets.linkedLoans')}</h3>
      <div className="space-y-1">
        {linked.map(loan => (
          <div key={loan.id} className="flex items-center justify-between gap-3 text-xs">
            <Link to={`/accounts/${loan.id}`} className="text-muted-foreground hover:underline truncate">
              {loan.display_name || loan.name}
            </Link>
            <span className="tabular-nums shrink-0">
              {mask(formatCurrency(getLoanDebtPrimary(loan), currency, locale))}
            </span>
          </div>
        ))}
      </div>
      <p className="pt-2 border-t border-border text-xs font-medium">
        {t('assets.propertyEquity', {
          value: mask(formatCurrency(equity.value, currency, locale)),
          debt: mask(formatCurrency(equity.debt, currency, locale)),
          equity: mask(formatCurrency(equity.equity, currency, locale)),
        })}
      </p>
    </div>
  )
}
