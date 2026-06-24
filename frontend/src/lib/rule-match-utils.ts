export function normalizeRuleMatchValue(value: string | number): string {
  return String(value ?? '')
    .trim()
    .toUpperCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
}
