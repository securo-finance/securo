/**
 * Parse a principal or interest input into whole cents. Empty counts as zero;
 * anything negative, non-numeric or with more than two decimals is rejected,
 * matching the backend's `decimal_places=2` validation.
 */
export function parseCents(value: string): number | null {
  const trimmed = value.trim()
  if (trimmed === '') return 0
  const match = /^(\d+)(?:[.,](\d{0,2}))?$/.exec(trimmed)
  if (!match) return null
  return Number(match[1]) * 100 + Number((match[2] ?? '').padEnd(2, '0'))
}

/** Whole cents of a payment amount as the API reports it. */
export function amountToCents(amount: number): number {
  return Math.round(amount * 100)
}
