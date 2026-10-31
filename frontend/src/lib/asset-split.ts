import type { Asset } from '@/types'

// The server's cap on each side of a split ratio.
export const MAX_SPLIT_UNITS = 1_000_000

// "1 → 100" for a 100-for-1 split, "10 → 1" for a 1-for-10 reverse split.
export function formatSplitRatio(oldUnits: number, newUnits: number, locale: string): string {
  const format = new Intl.NumberFormat(locale)
  return `${format.format(oldUnits)} → ${format.format(newUnits)}`
}

function wholeUnits(input: string): number | null {
  const value = Number(input)
  return Number.isInteger(value) && value >= 1 && value <= MAX_SPLIT_UNITS ? value : null
}

// The two form inputs as a split ratio, or null unless both are different
// whole numbers from 1 to MAX_SPLIT_UNITS (the server's rule).
export function parseSplitUnits(
  oldInput: string,
  newInput: string,
): { oldUnits: number; newUnits: number } | null {
  const oldUnits = wholeUnits(oldInput)
  const newUnits = wholeUnits(newInput)
  if (oldUnits === null || newUnits === null || oldUnits === newUnits) return null
  return { oldUnits, newUnits }
}

// Splits go in the ledger of a market-priced holding tracked here: it needs
// buys on record, and a bank connection would report the split itself.
export function canRecordSplit(
  holding: Pick<Asset, 'valuation_method' | 'connection_id' | 'transaction_count'>,
): boolean {
  return (
    holding.valuation_method === 'market_price' &&
    !holding.connection_id &&
    holding.transaction_count > 0
  )
}
