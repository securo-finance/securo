import type { Asset } from '@/types'

type ValuedAsset = Pick<Asset, 'current_value' | 'current_value_primary'>

// The API only fills current_value_primary when the asset is held in a
// currency other than the user's primary one, so fall back to current_value
// for assets already in the primary currency.
export function getAssetValuePrimary(asset: ValuedAsset): number | null {
  const value = asset.current_value_primary ?? asset.current_value
  return value == null ? null : Number(value)
}

export function getPortfolioTotalPrimary(assets: ValuedAsset[]): number {
  return assets.reduce((acc, a) => acc + (getAssetValuePrimary(a) ?? 0), 0)
}

export function getPortfolioShare(asset: ValuedAsset, totalPrimary: number): number | null {
  const value = getAssetValuePrimary(asset)
  if (value == null || !(totalPrimary > 0)) return null
  return (value / totalPrimary) * 100
}
