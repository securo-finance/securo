import type { Asset } from '@/types'

type ValuedAsset = Pick<Asset, 'currency' | 'current_value' | 'current_value_primary'>

// The API fills current_value_primary only for assets held in a currency
// other than the user's display one, and only when an FX rate could be
// resolved. A foreign asset with a NULL primary value has no available
// rate, so it must not fall back to its raw current_value: doing so would
// silently sum unconverted amounts into the display-currency total
// (issue #574).
export function getAssetValuePrimary(asset: ValuedAsset, displayCurrency: string): number | null {
  if (asset.current_value_primary != null) return Number(asset.current_value_primary)
  if (asset.current_value == null) return null
  if (asset.currency === displayCurrency) return Number(asset.current_value)
  return null
}

export function getPortfolioTotalPrimary(assets: ValuedAsset[], displayCurrency: string): number {
  return assets.reduce((acc, a) => acc + (getAssetValuePrimary(a, displayCurrency) ?? 0), 0)
}

export function getPortfolioShare(
  asset: ValuedAsset,
  totalPrimary: number,
  displayCurrency: string,
): number | null {
  const value = getAssetValuePrimary(asset, displayCurrency)
  if (value == null || !(totalPrimary > 0)) return null
  return (value / totalPrimary) * 100
}
