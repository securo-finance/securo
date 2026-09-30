import { describe, expect, it } from 'vitest'

import {
  getAssetValuePrimary,
  getPortfolioShare,
  getPortfolioTotalPrimary,
} from '@/lib/asset-portfolio-share'

describe('asset portfolio share', () => {
  const inPrimary = { current_value: 600, current_value_primary: null }
  const foreign = { current_value: 100, current_value_primary: 400 }
  const unvalued = { current_value: null, current_value_primary: null }

  it('falls back to current_value for assets in the primary currency', () => {
    expect(getAssetValuePrimary(inPrimary)).toBe(600)
    expect(getAssetValuePrimary(foreign)).toBe(400)
    expect(getAssetValuePrimary(unvalued)).toBeNull()
  })

  it('computes shares on the same basis as the total so they sum to 100', () => {
    const assets = [inPrimary, foreign, unvalued]
    const total = getPortfolioTotalPrimary(assets)
    expect(total).toBe(1000)
    expect(getPortfolioShare(inPrimary, total)).toBe(60)
    expect(getPortfolioShare(foreign, total)).toBe(40)
    expect(getPortfolioShare(unvalued, total)).toBeNull()
  })

  it('returns null when the portfolio total is zero', () => {
    expect(getPortfolioShare(inPrimary, 0)).toBeNull()
  })
})
