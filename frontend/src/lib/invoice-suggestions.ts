import type { ReconciliationSuggestion } from '@/types'

/** Every question that names this invoice, whether it names it alone or
 *  alongside others. A payout settling three invoices is one question,
 *  and it has to be findable from any of the three.
 *
 *  Here rather than beside the component that renders it: a file that
 *  exports both a component and a plain function loses fast refresh for
 *  the whole module, and this half is pure enough to be tested without a
 *  render.
 */
export function suggestionsFor(
  suggestions: ReconciliationSuggestion[],
  invoiceId: string,
): ReconciliationSuggestion[] {
  return suggestions.filter(
    (s) =>
      (s.expectation_kind === 'invoice' && s.expectation_id === invoiceId) ||
      (s.covers ?? []).some(
        (c) => c.expectation_kind === 'invoice' && c.expectation_id === invoiceId,
      ),
  )
}
