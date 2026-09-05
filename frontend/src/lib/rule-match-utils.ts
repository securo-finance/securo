import { flattenConditions } from './rule-conditions'
import type { Category, Rule } from '../types'

export function normalizeRuleMatchValue(value: string | number): string {
  return String(value ?? '')
    .trim()
    .toUpperCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
}

/** The text a rule is searchable by: its name, the values it matches on, and
 * the categories it assigns — so typing "uber" finds the rule whose condition
 * value is UBER, not just one named after it. Normalized the same way as
 * {@link normalizeRuleMatchValue}, so the query can be compared against it
 * case- and accent-insensitively. */
export function ruleSearchText(rule: Rule, categories: Category[]): string {
  const categoryNames = rule.actions
    .filter(a => a.op === 'set_category')
    .map(a => categories.find(c => c.id === a.value)?.name ?? '')
  return [
    rule.name,
    ...flattenConditions(rule.conditions).map(c => String(c.value ?? '')),
    ...categoryNames,
  ].map(normalizeRuleMatchValue).join(' ')
}
