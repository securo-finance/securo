import { describe, expect, it } from 'vitest'

import { getAssetProfit } from '@/lib/asset-profit'

const baseAsset = {
  gain_loss: null,
  purchase_price: null,
  realized_gain: null,
  sell_date: null,
  sell_price: null,
  total_invested: null,
  value_count: 0,
}

describe('getAssetProfit', () => {
  it('uses the purchase price for a manually valued asset', () => {
    expect(getAssetProfit({
      ...baseAsset,
      gain_loss: 446.45,
      purchase_price: 3679,
      value_count: 1,
    })).toEqual({
      amount: 446.45,
      percentage: (446.45 / 3679) * 100,
    })
  })

  it('uses the ledger cost basis for a held market asset', () => {
    expect(getAssetProfit({
      ...baseAsset,
      gain_loss: -50,
      purchase_price: 950,
      total_invested: 1000,
      value_count: 1,
    })).toEqual({ amount: -50, percentage: -5 })
  })

  it('hides fallback zero profit until a manual valuation exists', () => {
    expect(getAssetProfit({
      ...baseAsset,
      gain_loss: 0,
      purchase_price: 1000,
    })).toBeNull()
  })

  it('shows the cumulative realized gain for a sold ledger asset', () => {
    expect(getAssetProfit({
      ...baseAsset,
      realized_gain: 249.48,
      sell_date: '2025-11-07',
    })).toEqual({ amount: 249.48, percentage: null })
  })

  it('derives profit for a manually sold asset', () => {
    expect(getAssetProfit({
      ...baseAsset,
      purchase_price: 1000,
      sell_date: '2025-11-07',
      sell_price: 1250,
    })).toEqual({ amount: 250, percentage: 25 })
  })
})
