/** The promise and the money, resolved into the arithmetic that decides.
 *
 *  This began as a dialog holding two bordered boxes and an arrow. Three
 *  things were wrong with it, and the structural one came first: a modal
 *  is the answer you reach for before thinking. You inspected, closed,
 *  and the Accept button was behind the thing you had just dismissed. So
 *  this expands the row in place. The list stays put, several can be read
 *  in sequence, and the decision stays under your cursor.
 *
 *  The second was that a reader had to diff two lists in their head. The
 *  question is never "what are the properties of this invoice", it is
 *  "does this money belong to this debt", and that is one subtraction. So
 *  the subtraction is the headline, and everything else is provenance
 *  underneath it, quieter and smaller.
 *
 *  The third was boxes inside a box. There are no cards here: a hairline
 *  between two columns says the same thing and costs nothing.
 */
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import {
  invoices as invoicesApi,
  transactions as transactionsApi,
  accounts as accountsApi,
} from '@/lib/api'
import type { Account, Invoice, Transaction } from '@/types'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { cn } from '@/lib/utils'

export interface PairSide {
  /** `transaction` is the other leg of a transfer. It is not a promise
   *  anybody wrote down, which is why it reads differently below: there
   *  is nothing outstanding to subtract from, only two records asking
   *  whether they are the same money. */
  kind: 'invoice' | 'recurring' | 'transaction'
  id: string
  label?: string | null
  /** What this movement puts, or would put, against this promise. */
  amount: string
}

/** A figure with its meaning beneath it, sized so the figures read as one
 *  row of numbers and the words stay out of the way. */
function Figure({
  value,
  caption,
  tone = 'neutral',
}: {
  value: string
  caption: string
  tone?: 'neutral' | 'applied' | 'quiet'
}) {
  return (
    <div className="min-w-0">
      <p
        className={cn(
          'text-lg font-semibold tabular-nums leading-none truncate',
          tone === 'applied' && 'text-emerald-600 dark:text-emerald-400',
          tone === 'quiet' && 'text-muted-foreground',
          tone === 'neutral' && 'text-foreground',
        )}
      >
        {value}
      </p>
      <p className="text-[11px] text-muted-foreground mt-1 truncate">{caption}</p>
    </div>
  )
}

/** One line of provenance. Deliberately not a label/value table: six rows
 *  of those compete with the figures above, and the figures are what the
 *  decision rests on. */
function Facts({ items }: { items: (string | null | undefined)[] }) {
  const shown = items.filter(Boolean) as string[]
  return (
    <p className="text-xs text-muted-foreground leading-relaxed">
      {shown.join(' · ')}
    </p>
  )
}

function useInvoice(id: string, enabled: boolean) {
  return useQuery<Invoice>({
    queryKey: ['invoice', id],
    queryFn: () => invoicesApi.get(id),
    enabled,
  })
}

function PromiseLine({
  side,
  open,
  money,
  showDate,
}: {
  side: PairSide
  open: boolean
  money: (value: string | number | null | undefined, code?: string | null) => string
  showDate: (iso: string) => string
}) {
  const { t } = useTranslation()
  const { data: invoice } = useInvoice(side.id, open && side.kind === 'invoice')
  const { data: leg } = useQuery<Transaction>({
    queryKey: ['transaction', side.id],
    queryFn: () => transactionsApi.get(side.id),
    enabled: open && side.kind === 'transaction',
  })

  if (side.kind === 'transaction') {
    // The account, then what it did and when. Never the description: on
    // the leg that matters it is a reference number, and the question
    // being asked is about two accounts.
    return (
      <div className="space-y-0.5">
        <p className="text-sm font-medium text-foreground truncate">
          {side.label ?? t('reconciliation.pair.otherLeg')}
        </p>
        <Facts
          items={[
            leg && showDate(leg.date),
            leg && money(leg.amount, leg.currency),
            leg?.description,
          ]}
        />
      </div>
    )
  }

  if (side.kind !== 'invoice') {
    return (
      <div>
        <p className="text-sm font-medium text-foreground">
          {side.label ?? t('reconciliation.pair.recurringBill')}
        </p>
        <Facts items={[t('reconciliation.pair.recurringBill')]} />
      </div>
    )
  }

  if (!invoice) {
    return <p className="text-xs text-muted-foreground">{t('common.loading')}</p>
  }

  // The label the API resolved. Composing it from `series` and `number`
  // gets it wrong: `series` is the fiscal year, and the prefix a reader
  // recognises lives in the snapshot taken at issue.
  const name = side.label ?? invoice.external_number ?? null

  return (
    <div className="space-y-0.5">
      <p className="text-sm font-medium text-foreground flex items-center gap-2">
        {name ?? t('reconciliation.pair.draftInvoice')}
        {invoice.state === 'overdue' && (
          <span className="text-[10px] font-semibold uppercase tracking-wide text-rose-600 dark:text-rose-400">
            {t('invoices.state.overdue', 'overdue')}
          </span>
        )}
      </p>
      <Facts
        items={[
          invoice.payee?.name,
          t('reconciliation.pair.dueOn', { date: showDate(invoice.due_date) }),
          t('reconciliation.pair.totalOf', {
            amount: money(invoice.total, invoice.currency),
          }),
        ]}
      />
    </div>
  )
}

export function ReconciliationPair({
  open,
  transactionId,
  sides,
  /** Pending means the money has not been applied yet, so the arithmetic
   *  can say what *would* be left. Once applied, the invoice already
   *  reflects it, and subtracting again would be a lie told confidently. */
  pending,
}: {
  open: boolean
  transactionId?: string | null
  sides: PairSide[]
  pending: boolean
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()

  const { data: accounts = [] } = useQuery<Account[]>({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
    enabled: open,
  })
  const { data: transaction } = useQuery<Transaction>({
    queryKey: ['transaction', transactionId],
    queryFn: () => transactionsApi.get(transactionId as string),
    enabled: open && !!transactionId,
  })
  const first = sides.length > 0 ? sides[0] : undefined
  const { data: firstInvoice } = useInvoice(
    first?.id ?? '',
    open && first?.kind === 'invoice',
  )

  if (!open) return null

  const currency = firstInvoice?.currency ?? transaction?.currency
  const money = (value: string | number | null | undefined, code?: string | null) =>
    mask(formatCurrency(Number(value ?? 0), code || currency || 'USD', locale))
  const showDate = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale)
  const accountName = accounts.find((a) => a.id === transaction?.account_id)?.name

  const applied = sides.reduce((sum, side) => sum + Number(side.amount), 0)
  const outstanding = Number(firstInvoice?.balance ?? 0)
  const remaining = outstanding - applied
  const singleInvoice = sides.length === 1 && first?.kind === 'invoice'
  // A transfer has no balance to subtract from, so the three-figure
  // arithmetic above would be answering a question nobody asked. What
  // matters is the two amounts side by side, because when they differ
  // the difference is the whole reason this is a question.
  const isTransfer = first?.kind === 'transaction'

  return (
    <div className="mt-3 pt-3 border-t border-border">
      {/* The subtraction that decides, before anything else. One invoice
          gets the whole sentence; a payment spread over several gets the
          two totals, because "what is left" is not one number then. */}
      <div className="flex items-end gap-6 sm:gap-10 flex-wrap">
        {singleInvoice ? (
          <>
            <Figure
              value={money(outstanding)}
              caption={t('reconciliation.pair.stillOpen')}
              tone="quiet"
            />
            <Figure
              value={money(applied)}
              caption={
                pending
                  ? t('reconciliation.pair.thisPayment')
                  : t('reconciliation.pair.wasApplied')
              }
              tone="applied"
            />
            {pending && (
              <Figure
                value={money(remaining > 0 ? remaining : 0)}
                caption={
                  remaining <= 0
                    ? t('reconciliation.pair.wouldClose')
                    : t('reconciliation.pair.wouldRemain')
                }
                tone={remaining <= 0 ? 'applied' : 'neutral'}
              />
            )}
          </>
        ) : isTransfer ? (
          <>
            <Figure
              value={money(applied)}
              caption={t('reconciliation.pair.otherLegAmount')}
              tone="quiet"
            />
            {transaction && (
              <Figure
                value={money(transaction.amount, transaction.currency)}
                caption={t('reconciliation.pair.thisLegAmount')}
                tone="applied"
              />
            )}
          </>
        ) : (
          <>
            <Figure
              value={money(applied)}
              caption={t('reconciliation.pair.acrossInvoices', {
                count: sides.length,
              })}
              tone="applied"
            />
            {transaction && (
              <Figure
                value={money(transaction.amount, transaction.currency)}
                caption={t('reconciliation.pair.thePayment')}
                tone="quiet"
              />
            )}
          </>
        )}
      </div>

      {/* Provenance, quieter, split only by a hairline. Boxes here would be
          boxes inside a row inside a card. */}
      <div className="mt-4 grid gap-4 sm:grid-cols-2 sm:divide-x divide-border">
        <div className="space-y-2 sm:pr-5">
          <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
            {t(isTransfer ? 'reconciliation.pair.otherLeg' : 'reconciliation.pair.owed')}
          </p>
          {sides.map((side) => (
            <PromiseLine
              key={side.id}
              side={side}
              open={open}
              money={money}
              showDate={showDate}
            />
          ))}
        </div>

        <div className="space-y-2 sm:pl-5">
          <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
            {t(isTransfer ? 'reconciliation.pair.thisLeg' : 'reconciliation.pair.arrived')}
          </p>
          {transaction ? (
            <div className="space-y-0.5">
              {/* For a transfer both columns lead with the account, so the
                  two sides read as one comparison. Leading with the
                  description on this side and the account on the other
                  asked the reader to hold two different shapes in their
                  head and work out which field to compare with which. */}
              <p className="text-sm font-medium text-foreground truncate">
                {isTransfer ? (accountName ?? transaction.description) : transaction.description}
              </p>
              <Facts
                items={
                  isTransfer
                    ? [
                        showDate(transaction.date),
                        money(transaction.amount, transaction.currency),
                        transaction.description,
                      ]
                    : [
                        showDate(transaction.date),
                        accountName,
                        // `payee_name` is the resolved one; `payee` is the
                        // raw string the bank sent, worth falling back to
                        // when nothing has been mapped yet.
                        transaction.payee_name || transaction.payee,
                      ]
                }
              />
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {transactionId ? t('common.loading') : t('reconciliation.pair.noMoney')}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
