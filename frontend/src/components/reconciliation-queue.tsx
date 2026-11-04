/** The doubtful space: matches that are plausible without being certain.
 *
 *  This queue is the **residue**, never the main road. Every row here is a
 *  payment the automatic rules could not claim, and if it is where the
 *  volume goes then the rules are wrong and the fix belongs one card up,
 *  not in asking somebody to work through a list every morning.
 *
 *  Which is why the empty state is written as good news rather than as an
 *  apology for having nothing to show.
 *
 *  Each row states its evidence (the amount is exact, the payer is known,
 *  the date is four days out) instead of a confidence score. A percentage
 *  is not something a person can check, and a queue nobody can check is a
 *  queue they clear without reading.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { reconciliation as reconciliationApi, accounts as accountsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import type { Account, ReconciliationSuggestion } from '@/types'
import { Check, X, CircleCheck } from 'lucide-react'
import { ReconciliationPair, type PairSide } from '@/components/reconciliation-pair'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { cn } from '@/lib/utils'

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}

/** What kind of question this row is.
 *
 *  The queue holds several kinds at once, and until you know which one
 *  you are looking at, the sentence underneath does not parse: *may be
 *  the other half of Nu* and *may settle invoice 41* are different acts
 *  with different consequences. Colour and word together, because colour
 *  alone is not a label anybody can read out loud. */
function KindBadge({ kind }: { kind: ReconciliationSuggestion['expectation_kind'] }) {
  const { t } = useTranslation()
  const tone =
    kind === 'transaction'
      ? 'bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300'
      : kind === 'invoice'
        ? 'bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300'
        : 'bg-teal-50 text-teal-700 dark:bg-teal-950 dark:text-teal-300'
  return (
    <span
      className={cn(
        'shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded-full',
        tone,
      )}
    >
      {t(`reconciliation.queue.kind.${kind}`)}
    </span>
  )
}

/** The evidence, as short claims a reader can agree or disagree with. */
function Evidence({ suggestion }: { suggestion: ReconciliationSuggestion }) {
  const { t } = useTranslation()
  const { scores } = suggestion
  const claims: { label: string; good: boolean }[] = []
  // Both legs of a transfer are the reader's own accounts, so there is no
  // payer to recognise and the chip would say *payer not identified* on
  // every transfer row, forever, meaning nothing.
  const payerMatters = suggestion.expectation_kind !== 'transaction'

  if (scores.amount_exact !== undefined) {
    claims.push({
      label: scores.amount_exact
        ? t('reconciliation.evidence.amountExact')
        : t('reconciliation.evidence.amountOff', {
            expected: scores.amount_expected,
            moved: scores.amount_moved,
          }),
      good: !!scores.amount_exact,
    })
  }
  if (payerMatters && scores.same_counterparty !== undefined) {
    claims.push({
      label: scores.same_counterparty
        ? t('reconciliation.evidence.knownPayer')
        : t('reconciliation.evidence.unknownPayer'),
      good: !!scores.same_counterparty,
    })
  }
  if (scores.days_apart !== undefined) {
    claims.push({
      label:
        scores.days_apart === 0
          ? t('reconciliation.evidence.sameDay')
          : t('reconciliation.evidence.daysApart', { count: Math.abs(scores.days_apart) }),
      good: Math.abs(scores.days_apart) <= 3,
    })
  }

  return (
    <div className="flex flex-wrap gap-1.5 mt-1.5">
      {claims.map((claim) => (
        <span
          key={claim.label}
          className={
            claim.good
              ? 'text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
              : 'text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground'
          }
        >
          {claim.label}
        </span>
      ))}
    </div>
  )
}

export function ReconciliationQueue({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()

  const { data: suggestions } = useQuery<ReconciliationSuggestion[]>({
    queryKey: ['reconciliation-suggestions'],
    queryFn: reconciliationApi.suggestions,
  })
  // The same cached query the expanded row already uses, so naming the
  // account on every row costs nothing extra.
  const { data: accounts = [] } = useQuery<Account[]>({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })
  const accountName = (id: string | null | undefined) =>
    id ? accounts.find((account) => account.id === id)?.name : undefined

  // Deciding is a comparison, and the evidence chips describe an invoice
  // without ever showing it. Expanding the row puts both halves on screen
  // without taking the Accept button away with a modal.
  const [openId, setOpenId] = useState<string | null>(null)

  const accept = useSettleMutation('accept', queryClient, t)
  const decline = useSettleMutation('decline', queryClient, t)

  const money = (value: string | number | null | undefined, currency?: string | null) =>
    mask(formatCurrency(Number(value ?? 0), currency || 'USD', locale))
  const showDate = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale)

  if (!suggestions) return null

  return (
    <SectionCard>
      <div className="px-4 sm:px-5 py-4 border-b border-border">
        <p className="text-sm font-semibold text-foreground">
          {t('reconciliation.queue.title')}
          {suggestions.length > 0 && (
            <span className="ml-2 text-[10px] font-semibold bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300 px-1.5 py-0.5 rounded-full">
              {suggestions.length}
            </span>
          )}
        </p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {t('reconciliation.queue.hint')}
        </p>
      </div>

      {suggestions.length === 0 ? (
        <div className="py-10 text-center">
          <CircleCheck size={20} className="mx-auto text-emerald-500 mb-2" />
          <p className="text-sm text-muted-foreground">{t('reconciliation.queue.empty')}</p>
        </div>
      ) : (
        <div className="divide-y divide-border">
          {suggestions.map((suggestion) => (
            <div
              key={suggestion.id}
              className="px-4 sm:px-5 py-3 hover:bg-muted/60 transition-colors cursor-pointer"
              onClick={() =>
                setOpenId((current) => (current === suggestion.id ? null : suggestion.id))
              }
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground flex items-center gap-2">
                    <span className="truncate">
                      {suggestion.transaction?.description ??
                        t('reconciliation.queue.aPayment')}
                    </span>
                    <KindBadge kind={suggestion.expectation_kind} />
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {suggestion.transaction?.date
                      ? showDate(suggestion.transaction.date)
                      : ''}
                    {/* Which of the reader's accounts this row sits on.
                        The sentence that follows names the other side,
                        and without this one the reader has to open the
                        row to find out where they are standing. */}
                    {accountName(suggestion.transaction?.account_id) && (
                      <>
                        {' · '}
                        <span className="font-medium text-foreground">
                          {accountName(suggestion.transaction?.account_id)}
                        </span>
                      </>
                    )}
                    {' · '}
                    {t(
                      suggestion.expectation_kind === 'invoice'
                        ? 'reconciliation.queue.maySettle'
                        : suggestion.expectation_kind === 'transaction'
                          // "may settle" and "may be" both read as one
                          // record explaining another. Two legs of a
                          // transfer are neither: they are the same money
                          // seen twice, and the row has to say so or the
                          // reader answers a question we did not ask.
                          ? 'reconciliation.queue.mayPairWith'
                          : 'reconciliation.queue.mayBe',
                      {
                        // One payment can answer several promises, and the
                        // question is the whole question, so the row names
                        // all of them rather than the first and a count.
                        name:
                          suggestion.covers.length > 1
                            ? suggestion.covers
                                .map((c) => c.label ?? '—')
                                .join(', ')
                            : suggestion.expectation_label ?? '—',
                      },
                    )}
                  </p>

                  {suggestion.covers.length > 1 && (
                    <ul className="mt-1 space-y-0.5">
                      {suggestion.covers.map((cover) => (
                        <li
                          key={cover.expectation_id}
                          className="text-xs text-muted-foreground flex justify-between gap-3 max-w-xs"
                        >
                          <span className="truncate">{cover.label ?? '—'}</span>
                          <span className="tabular-nums">
                            {money(cover.amount, suggestion.scores.currency)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <Evidence suggestion={suggestion} />
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-sm font-bold tabular-nums text-foreground">
                    {money(suggestion.amount, suggestion.scores.currency)}
                  </span>
                  {canWrite && (
                    <div
                      className="flex items-center gap-1"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-8 gap-1"
                        onClick={() => accept.mutate(suggestion.id)}
                        disabled={accept.isPending || decline.isPending}
                      >
                        <Check size={13} />
                        <span className="hidden sm:inline">
                          {t('reconciliation.queue.accept')}
                        </span>
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-8 gap-1 text-muted-foreground"
                        onClick={() => decline.mutate(suggestion.id)}
                        disabled={accept.isPending || decline.isPending}
                        title={t('reconciliation.queue.declineHint')}
                      >
                        <X size={13} />
                      </Button>
                    </div>
                  )}
                </div>
              </div>

              <div onClick={(e) => e.stopPropagation()}>
                <ReconciliationPair
                  open={openId === suggestion.id}
                  transactionId={suggestion.transaction?.id}
                  pending
                  sides={suggestion.covers.map<PairSide>((cover) => ({
                    kind: cover.expectation_kind,
                    id: cover.expectation_id,
                    label: cover.label,
                    amount: cover.amount,
                  }))}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  )
}

/** Accept and decline differ only in which endpoint they call and what
 *  they say afterwards, so they share one definition rather than two
 *  near-identical blocks that drift. */
function useSettleMutation(
  action: 'accept' | 'decline',
  queryClient: ReturnType<typeof useQueryClient>,
  t: (key: string) => string,
) {
  return useMutation({
    mutationFn: (id: string) =>
      action === 'accept'
        ? reconciliationApi.accept(id)
        : reconciliationApi.decline(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reconciliation-suggestions'] })
      // Answering a question is itself an event, so the stream below has
      // to be re-read or it shows a history that stops one act ago.
      void queryClient.invalidateQueries({ queryKey: ['reconciliation-history'] })
      if (action === 'accept') invalidateFinancialQueries(queryClient)
      toast.success(
        t(action === 'accept' ? 'reconciliation.queue.linked' : 'reconciliation.queue.declined'),
      )
    },
    onError: (error) => toast.error(extractApiError(error, t('common.error'))),
  })
}
