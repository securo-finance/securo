export type CashFlowSeries = 'inflow' | 'outflow'

export interface CashFlowBar {
  inflowAmount: number
  outflowAmount: number
  netFlow: number
  inflow: [number, number]
  outflow: [number, number]
}

const amount = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : 0)

/**
 * Inflow and outflow arrive as positive magnitudes. Each period is drawn as
 * two contiguous ranges, outflow below inflow, shifted down by the outflow so
 * the column ends at net flow (inflow - outflow) relative to zero while each
 * segment keeps its own length.
 */
export function cashFlowBar(inflow: unknown, outflow: unknown): CashFlowBar {
  const inflowAmount = amount(inflow)
  const outflowAmount = amount(outflow)
  const netFlow = inflowAmount - outflowAmount
  return {
    inflowAmount,
    outflowAmount,
    netFlow,
    outflow: [-2 * outflowAmount, -outflowAmount],
    inflow: [-outflowAmount, netFlow],
  }
}

/** The segment that ends at net flow, which gets the rounded top. */
export function cashFlowTopSeries(bar: Pick<CashFlowBar, 'inflowAmount' | 'outflowAmount'>): CashFlowSeries | null {
  if (bar.inflowAmount !== 0) return 'inflow'
  if (bar.outflowAmount !== 0) return 'outflow'
  return null
}
