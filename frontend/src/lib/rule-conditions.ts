import type { RuleCondition, RuleConditionGroup, RuleConditionNode } from '../types'

/** A condition list entry is a group when it carries its own condition list. */
export function isConditionGroup(node: RuleConditionNode): node is RuleConditionGroup {
  return Array.isArray((node as RuleConditionGroup).conditions)
}

/** Every leaf condition of a rule, unwrapping one level of AND/OR groups. */
export function flattenConditions(nodes: RuleConditionNode[]): RuleCondition[] {
  return nodes.flatMap(node => (isConditionGroup(node) ? node.conditions : [node]))
}

/** True when a rule mixes AND and OR, i.e. it holds at least one group. */
export function hasConditionGroups(nodes: RuleConditionNode[]): boolean {
  return nodes.some(isConditionGroup)
}
