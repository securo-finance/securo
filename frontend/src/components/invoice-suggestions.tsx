/** The questions matching could not answer, where the invoice is.
 *
 *  Matching produced these, and until now the only place they existed was
 *  a tab on the rules page. That is the wrong place for them to be the
 *  *only* place: somebody looking at an unpaid invoice is exactly the
 *  person who can say whether that payment is it, and they had no way of
 *  knowing a question was waiting unless they went looking for a queue
 *  they had no reason to visit.
 *
 *  So the same question appears here, answerable in place. The queue is
 *  still where you go to clear a backlog; this is where you find one
 *  without having gone looking.
 */
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Check, HelpCircle, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { reconciliation as reconciliationApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { suggestionsFor } from '@/lib/invoice-suggestions'
import type { ReconciliationSuggestion } from '@/types'

export function InvoiceSuggestions({
  invoiceId,
  canWrite,
}: {
  invoiceId: string
  canWrite: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()

  // The same key the queue uses, so answering in either place updates
  // both without either knowing the other exists.
  const { data: pending = [] } = useQuery<ReconciliationSuggestion[]>({
    queryKey: ['reconciliation-suggestions'],
    queryFn: reconciliationApi.suggestions,
  })

  const answer = useMutation({
    mutationFn: ({ id, accept }: { id: string; accept: boolean }) =>
      accept ? reconciliationApi.accept(id) : reconciliationApi.decline(id),
    onSuccess: (_result, { accept }) => {
      void queryClient.invalidateQueries({ queryKey: ['reconciliation-suggestions'] })
      void queryClient.invalidateQueries({ queryKey: ['invoice', invoiceId] })
      void queryClient.invalidateQueries({ queryKey: ['reconciliation-history'] })
      // Accepting moves money against a debt, so the figures everywhere
      // else are now stale. Declining changes nothing but the question.
      if (accept) invalidateFinancialQueries(queryClient)
      // The same words the queue says for the same act. Two vocabularies
      // for one decision would read as two features.
      toast.success(
        t(accept ? 'reconciliation.queue.linked' : 'reconciliation.queue.declined'),
      )
    },
    onError: (error) => toast.error(extractApiError(error, t('common.error'))),
  })

  const mine = suggestionsFor(pending, invoiceId)
  if (mine.length === 0) return null

  const showDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale)
  const money = (value: string | number, code?: string | null) =>
    mask(formatCurrency(Number(value), code || 'USD', locale))

  return (
    <div className="bg-amber-50/60 dark:bg-amber-950/20 rounded-xl border border-amber-200 dark:border-amber-900/60 overflow-hidden">
      <div className="px-4 sm:px-5 py-3 flex items-center gap-2 border-b border-amber-200 dark:border-amber-900/60">
        <HelpCircle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0" />
        <p className="text-sm font-semibold text-foreground">
          {t('invoices.suggestions.title', { count: mine.length })}
        </p>
      </div>

      <ul className="divide-y divide-amber-200 dark:divide-amber-900/60">
        {mine.map((suggestion) => (
          <li
            key={suggestion.id}
            className="px-4 sm:px-5 py-3 flex flex-wrap items-center justify-between gap-3"
            data-testid="invoice-suggestion"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground truncate">
                {suggestion.transaction?.description ?? t('invoices.linkedPayment')}
              </p>
              <p className="text-xs text-muted-foreground tabular-nums mt-0.5">
                {suggestion.transaction?.date ? showDate(suggestion.transaction.date) : ''}
                {' · '}
                {t('invoices.suggestions.wouldApply', {
                  amount: money(suggestion.amount, suggestion.transaction?.currency),
                })}
                {/* A question naming several invoices is answered whole or
                    not at all, so saying so before the buttons is the
                    difference between a decision and a surprise. */}
                {(suggestion.covers?.length ?? 0) > 1 && (
                  <> · {t('invoices.suggestions.alsoCovers', {
                    count: (suggestion.covers?.length ?? 1) - 1,
                  })}</>
                )}
              </p>
            </div>

            {/* Same order, same icons, same words as the queue: this is
                the same decision reached from a different door. */}
            {canWrite && (
              <div className="flex items-center gap-1 shrink-0">
                <Button
                  size="sm"
                  className="h-8 gap-1.5"
                  disabled={answer.isPending}
                  onClick={() => answer.mutate({ id: suggestion.id, accept: true })}
                >
                  <Check size={13} />
                  {t('reconciliation.queue.accept')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-8 gap-1 text-muted-foreground"
                  disabled={answer.isPending}
                  title={t('reconciliation.queue.declineHint')}
                  onClick={() => answer.mutate({ id: suggestion.id, accept: false })}
                >
                  <X size={13} />
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
