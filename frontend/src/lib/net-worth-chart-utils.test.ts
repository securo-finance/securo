import { describe, expect, it } from 'vitest'
import { getNetWorthDomain, shouldFillNetWorthArea } from './net-worth-chart-utils'

describe('getNetWorthDomain', () => {
  it('rounds bounds to readable values for ordinary net worth ranges', () => {
    expect(getNetWorthDomain([23_000, 203_000])).toEqual([0, 250_000])
  })

  it('keeps compact currency ticks distinct around a large balance', () => {
    expect(getNetWorthDomain([999_990, 1_000_110])).toEqual([950_000, 1_050_000])
  })

  it('keeps zero in range when positive values are close to zero', () => {
    expect(getNetWorthDomain([2, 100])).toEqual([0, 120])
  })

  it('adds padding around negative and positive values', () => {
    expect(getNetWorthDomain([-100, 100])).toEqual([-150, 150])
  })

  it('pads a constant series so the chart still has a usable scale', () => {
    expect(getNetWorthDomain([1_000_000, 1_000_000])).toEqual([950_000, 1_050_000])
  })

  it('falls back to a valid range when there are no finite values', () => {
    expect(getNetWorthDomain([Number.NaN, Number.POSITIVE_INFINITY])).toEqual([0, 1])
  })

  it('fills only when the chart domain includes zero', () => {
    expect(shouldFillNetWorthArea([0, 250_000])).toBe(true)
    expect(shouldFillNetWorthArea([950_000, 1_050_000])).toBe(false)
    expect(shouldFillNetWorthArea([-150, -50])).toBe(false)
  })
})
