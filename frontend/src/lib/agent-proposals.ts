export type ProposalKind =
  | 'categorize'
  | 'create_category'
  | 'create_budget'
  | 'create_payee_rule'
  | 'create_transaction'
  | 'create_recurring_transaction'
  | 'update_recurring_transaction'
  | 'cancel_recurring_transaction'
  | 'create_goal'

export interface ProposalData {
  kind?: ProposalKind
  proposed?: Record<string, unknown>
  target?: Record<string, unknown>
  changes?: Record<string, unknown>
  affected?: { id: string; description?: string; amount?: number; currency?: string }[]
  affected_count?: number
  target_category?: { id: string; name: string }
  name_collision?: { id: string; name: string }
  mode?: 'deactivate' | 'delete'
  apply_endpoint?: string
  error?: string
}

/** Heuristic: a tool result is a proposal if its data has a known kind. */
export function isProposalData(data: unknown): data is ProposalData {
  if (!data || typeof data !== 'object') return false
  const k = (data as { kind?: unknown }).kind
  return typeof k === 'string' && [
    'categorize', 'create_category', 'create_budget', 'create_payee_rule',
    'create_transaction', 'create_recurring_transaction',
    'update_recurring_transaction', 'cancel_recurring_transaction',
    'create_goal',
  ].includes(k)
}

/** Treat the data as a proposal even when only `error` is present, since
 * a proposal that failed validation should still render a small error card
 * instead of a generic tool-debug chip. */
export function isProposalToolName(name: string): boolean {
  return name.includes('propose_')
}
