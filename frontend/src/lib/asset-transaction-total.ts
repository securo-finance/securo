import { formatCurrency } from '@/lib/format'
import type { Asset, AssetTransaction } from '@/types'

type CurrencyHolding = Pick<Asset, 'id' | 'currency'>
type CurrencyTransaction = Pick<AssetTransaction, 'currency'>

// Currency the transaction form's total is in. A transaction is stored in its
// holding's currency, so that is the answer for an existing holding and for an
// edit. A new ticker has no currency until the server prices it on save, so
// this returns null rather than guessing.
export function getTransactionTotalCurrency(
  holdings: CurrencyHolding[],
  holdingId: string,
  editingTx: CurrencyTransaction | null,
): string | null {
  const holding = holdings.find((h) => h.id === holdingId)
  return holding?.currency ?? editingTx?.currency ?? null
}

// Renders the total in its currency, or as a bare amount while the currency
// is still unknown.
export function formatTransactionTotal(
  total: number,
  currency: string | null,
  locale: string,
): string {
  if (currency) return formatCurrency(total, currency, locale)
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(total)
}
