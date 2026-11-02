import type { TFunction } from 'i18next'

/** Translate synthetic rows by provenance, never by user-authored text. */
export function transactionDescription(description: string, source: unknown, t: TFunction): string {
  return source === 'opening_balance' ? t('accounts.openingBalanceDescription') : description
}