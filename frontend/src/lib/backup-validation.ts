export function parsePositiveInteger(value: string, errorMessage: string): number {
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed < 1) throw new Error(errorMessage)
  return parsed
}
