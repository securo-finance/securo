import type { Asset } from '@/types'

// Providers refresh synced positions daily, and fixed income is dated on the
// last business day, so a week covers weekends and long holidays without
// flagging a healthy position.
export const PROVIDER_VALUE_STALE_AFTER_DAYS = 7

type FreshnessAsset = Pick<Asset, 'provider_value_date' | 'sell_date'>

/**
 * The provider value date of an open position whose value stopped updating.
 * Null when the position is sold, carries no provider date, or is fresh
 * enough. Only the user decides what to do with it: nothing is hidden.
 */
export function getStaleProviderValueDate(asset: FreshnessAsset, today: string): string | null {
  if (asset.sell_date || !asset.provider_value_date) return null
  const ageMs = Date.parse(`${today}T00:00:00Z`) - Date.parse(`${asset.provider_value_date}T00:00:00Z`)
  return ageMs > PROVIDER_VALUE_STALE_AFTER_DAYS * 86_400_000 ? asset.provider_value_date : null
}
