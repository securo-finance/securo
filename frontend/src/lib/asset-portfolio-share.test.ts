import { describe, expect, it } from 'vitest'

import {
  getAssetValuePrimary,
  getPortfolioShare,
  getPortfolioTotalPrimary,
} from '@/lib/asset-portfolio-share'

describe('asset portfolio share', () => {
  const inPrimary = { currency: 'USD', current_value: 600, current_value_primary: null }
  const foreign = { currency: 'EUR', current_value: 100, current_value_primary: 400 }
  // Foreign asset the backend could not convert: no FX rate available.
  const unconverted = { currency: 'EUR', current_value: 100, current_value_primary: null }
  const unvalued = { currency: 'USD', current_value: null, current_value_primary: null }

  it('falls back to current_value only for assets in the display currency', () => {
    expect(getAssetValuePrimary(inPrimary, 'USD')).toBe(600)
    expect(getAssetValuePrimary(foreign, 'USD')).toBe(400)
    expect(getAssetValuePrimary(unconverted, 'USD')).toBeNull()
    expect(getAssetValuePrimary(unvalued, 'USD')).toBeNull()
  })

  it('excludes foreign assets without an FX rate from total and shares', () => {
    const assets = [inPrimary, foreign, unconverted, unvalued]
    const total = getPortfolioTotalPrimary(assets, 'USD')
    expect(total).toBe(1000)
    expect(getPortfolioShare(inPrimary, total, 'USD')).toBe(60)
    expect(getPortfolioShare(foreign, total, 'USD')).toBe(40)
    expect(getPortfolioShare(unconverted, total, 'USD')).toBeNull()
    expect(getPortfolioShare(unvalued, total, 'USD')).toBeNull()
  })

  it('returns null when the portfolio total is zero', () => {
    expect(getPortfolioShare(inPrimary, 0, 'USD')).toBeNull()
  })
})
