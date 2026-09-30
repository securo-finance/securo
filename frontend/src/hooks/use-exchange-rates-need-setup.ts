import { useQuery } from '@tanstack/react-query'

import { fxRates } from '@/lib/api'
import type { Asset } from '@/types'

/** A held value that needs a rate to reach the user's currency. */
export function needsConversion(asset: Asset, userCurrency: string): boolean {
  return asset.current_value != null && asset.current_value !== 0 && asset.currency !== userCurrency
}

/** Only asks the server when some asset actually needs converting. */
export function useExchangeRatesNeedSetup(assets: Asset[] | undefined, userCurrency: string): boolean {
  const hasForeignCurrencyAssets = assets?.some((asset) => needsConversion(asset, userCurrency)) ?? false
  const { data } = useQuery({
    queryKey: ['fx-rates', 'status'],
    queryFn: fxRates.status,
    enabled: hasForeignCurrencyAssets,
    staleTime: 0,
  })
  return hasForeignCurrencyAssets && data?.configured === false
}
