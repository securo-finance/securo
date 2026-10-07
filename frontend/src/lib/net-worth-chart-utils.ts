function roundToNiceStep(value: number): number {
  const power = 10 ** Math.floor(Math.log10(value))
  const fraction = value / power
  const niceFraction = fraction < 1.5 ? 1 : fraction < 3.5 ? 2 : fraction < 7.5 ? 5 : 10
  return niceFraction * power
}

export function getNetWorthDomain(values: readonly number[]): [number, number] {
  const finiteValues = values.filter(Number.isFinite)
  if (finiteValues.length === 0) return [0, 1]

  const min = Math.min(...finiteValues)
  const max = Math.max(...finiteValues)
  const range = max - min
  const magnitude = Math.max(Math.abs(min), Math.abs(max))
  const padding = range > 0 ? range * 0.1 : Math.max(magnitude * 0.005, 1)
  const step = roundToNiceStep(
    Math.max(range / 4, magnitude * 0.05, range === 0 ? padding : Number.EPSILON),
  )
  const paddedMin = min - padding
  const lower = min >= 0 && paddedMin < 0 ? 0 : paddedMin
  const upper = max + padding

  return [Math.floor(lower / step) * step, Math.ceil(upper / step) * step]
}

export function shouldFillNetWorthArea(domain: readonly [number, number]): boolean {
  return domain[0] <= 0 && domain[1] >= 0
}
