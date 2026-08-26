import type { RuleAction } from '../types'

export function isInvalidDescriptionAction(action: RuleAction): boolean {
  if (action.op !== 'set_description') return false
  const value = String(action.value ?? '').trim()
  return value === '' || value.length > 500
}

export function parseRulePriority(value: string): number {
  if (value.trim() === '') return 0
  const priority = Number(value)
  return Number.isFinite(priority) ? priority : 0
}
