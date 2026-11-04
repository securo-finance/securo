import type { RuleAction } from '../types'

export function isInvalidDescriptionAction(action: RuleAction): boolean {
  if (action.op !== 'set_description') return false
  const value = String(action.value ?? '').trim()
  return value === '' || value.length > 500
}

/** The actions a draft is complete enough to preview.
 *
 * The editor holds an action row from the moment it is added, so a draft in
 * progress routinely carries `set_category` with nothing picked yet. That is a
 * rule still being written, not a broken one — the engine already treats it as
 * a no-op — and the preview should keep answering the question about the
 * conditions instead of failing the backend's action validation. Actions the
 * form itself flags are dropped for the same reason: the inline error already
 * says what is wrong. `ignore` carries no value at all.
 */
export function previewableActions(actions: RuleAction[]): RuleAction[] {
  return actions.filter((action) => {
    if (action.op === 'ignore') return true
    if (isInvalidDescriptionAction(action)) return false
    return String(action.value ?? '').trim() !== ''
  })
}

export function parseRulePriority(value: string): number {
  if (value.trim() === '') return 0
  const priority = Number(value)
  return Number.isFinite(priority) ? priority : 0
}
