import { describe, expect, it } from 'vitest'
import {
  formatNetWorthAxisTick,
  getNetWorthDomain,
  shouldFillNetWorthArea,
} from './net-worth-chart-utils'

describe('getNetWorthDomain', () => {
  it('rounds bounds to readable values for ordinary net worth ranges', () => {
    expect(getNetWorthDomain([23_000, 203_000])).toEqual([0, 250_000])
  })

  it('keeps the scale close to a narrow range at a high balance', () => {
    expect(getNetWorthDomain([999_990, 1_000_110])).toEqual([999_960, 1_000_140])
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

describe('formatNetWorthAxisTick', () => {
  it('keeps adjacent high-balance ticks distinguishable', () => {
    const domain: [number, number] = [999_960, 1_000_120]
    expect(formatNetWorthAxisTick(1_000_000, domain)).toBe('$1,000,000')
    expect(formatNetWorthAxisTick(1_000_040, domain)).toBe('$1,000,040')
  })

  it('preserves cents when the tick interval is below one currency unit', () => {
    const domain: [number, number] = [1.005, 1.045]
    expect(formatNetWorthAxisTick(1.01, domain)).toBe('$1.01')
    expect(formatNetWorthAxisTick(1.04, domain)).toBe('$1.04')
  })

  it('distinguishes irregular sub-cent ticks on a narrow currency scale', () => {
    const domain: [number, number] = [1, 1.05]
    expect(formatNetWorthAxisTick(1.045, domain)).toBe('$1.045')
    expect(formatNetWorthAxisTick(1.05, domain)).toBe('$1.05')
  })

  it('uses compact currency labels when the scale supports them', () => {
    expect(formatNetWorthAxisTick(46_500, [45_000, 52_000])).toBe('$46.5K')
  })
})
