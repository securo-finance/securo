export type ProposalKind =
  | 'categorize'
  | 'create_category'
  | 'create_budget'
  | 'create_payee_rule'
  | 'create_rule'
  | 'update_rule'
  | 'delete_rule'
  | 'create_transaction'
  | 'create_recurring_transaction'
  | 'update_recurring_transaction'
  | 'cancel_recurring_transaction'
  | 'create_goal'
  | 'link_transfer'
  | 'unlink_transfer'

/** One leg of a transfer pair, as the transfer tools preview it. */
export interface TransferLeg {
  id: string
  description?: string | null
  date?: string | null
  amount?: number | null
  currency?: string | null
  type?: string | null
  account_name?: string | null
}

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
  transaction_ids?: string[]
  transfer_pair_id?: string
  legs?: TransferLeg[]
  error?: string
}

/** Heuristic: a tool result is a proposal if its data has a known kind. */
export function isProposalData(data: unknown): data is ProposalData {
  if (!data || typeof data !== 'object') return false
  const k = (data as { kind?: unknown }).kind
  return typeof k === 'string' && [
    'categorize', 'create_category', 'create_budget', 'create_payee_rule',
    'create_rule', 'update_rule', 'delete_rule',
    'create_transaction', 'create_recurring_transaction',
    'update_recurring_transaction', 'cancel_recurring_transaction',
    'create_goal', 'link_transfer', 'unlink_transfer',
  ].includes(k)
}

/** Treat the data as a proposal even when only `error` is present, since
 * a proposal that failed validation should still render a small error card
 * instead of a generic tool-debug chip. */
export function isProposalToolName(name: string): boolean {
  return name.includes('propose_')
}
