export function accountDisplayName(account: { name: string; alias?: string | null }): string {
  return account.alias?.trim() || account.name
}

/**
 * Format a numeric value as currency using Intl.NumberFormat.
 */
export function formatCurrency(
  value: number | null | undefined,
  currency = 'USD',
  locale = 'en-US',
): string {
  if (value == null) return '—'
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}
