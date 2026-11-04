import { describe, expect, it } from 'vitest'

import { cashFlowBar, cashFlowTopSeries } from '@/lib/cash-flow-bars'

const top = (range: [number, number]) => range[1]
const length = (range: [number, number]) => range[1] - range[0]

describe('cashFlowBar', () => {
  it('ends a surplus period above zero at its net flow', () => {
    const bar = cashFlowBar(3000, 2500)

    expect(bar.netFlow).toBe(500)
    expect(top(bar.inflow)).toBe(500)
  })

  it('ends a deficit period below zero at its net flow', () => {
    const bar = cashFlowBar(2500, 3000)

    expect(bar.netFlow).toBe(-500)
    expect(top(bar.inflow)).toBe(-500)
  })

  it('ends a break-even period exactly at zero', () => {
    const bar = cashFlowBar(2000, 2000)

    expect(bar.netFlow).toBe(0)
    expect(top(bar.inflow)).toBe(0)
  })

  it('keeps each segment as long as its own amount, stacked without a gap', () => {
    const bar = cashFlowBar(3000, 2500)

    expect(length(bar.inflow)).toBe(3000)
    expect(length(bar.outflow)).toBe(2500)
    expect(bar.outflow[1]).toBe(bar.inflow[0])
  })

  it('ends a period with only outflow at the top of the outflow segment', () => {
    const bar = cashFlowBar(0, 1000)

    expect(length(bar.inflow)).toBe(0)
    expect(top(bar.outflow)).toBe(-1000)
    expect(bar.netFlow).toBe(-1000)
  })

  it('keeps the original amounts for the tooltip', () => {
    expect(cashFlowBar(3000, 2500)).toMatchObject({ inflowAmount: 3000, outflowAmount: 2500 })
  })

  it.each([
    ['missing', undefined],
    ['null', null],
    ['non-numeric', 'n/a'],
    ['non-finite', Number.NaN],
  ])('treats a %s amount as zero', (_label, value) => {
    const bar = cashFlowBar(value, value)

    expect(bar).toMatchObject({ inflowAmount: 0, outflowAmount: 0, netFlow: 0 })
    expect(length(bar.inflow)).toBe(0)
    expect(length(bar.outflow)).toBe(0)
  })
})

describe('cashFlowTopSeries', () => {
  it('rounds the inflow segment whenever the period has inflow', () => {
    expect(cashFlowTopSeries(cashFlowBar(3000, 2500))).toBe('inflow')
    expect(cashFlowTopSeries(cashFlowBar(100, 0))).toBe('inflow')
  })

  it('rounds the outflow segment when there is no inflow to sit on top of it', () => {
    expect(cashFlowTopSeries(cashFlowBar(0, 1000))).toBe('outflow')
  })

  it('rounds nothing for an empty period', () => {
    expect(cashFlowTopSeries(cashFlowBar(0, 0))).toBeNull()
  })
})
