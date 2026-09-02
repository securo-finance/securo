/** The promise and the money, side by side.
 *
 *  Reconciling is a comparison, and until now the two halves of it lived
 *  on different pages. The queue showed evidence *about* an invoice —
 *  "the amount is exact, the payer is known" — without ever showing the
 *  invoice, so answering the question meant opening another tab and
 *  holding two screens in your head. The history had the same shape.
 *
 *  Left is what is owed, right is what arrived. That order is not
 *  arbitrary: the promise came first in every case except the look-back,
 *  and reading left to right is reading the story in the order it
 *  happened.
 *
 *  A payment can answer several promises at once, so the left column is a
 *  list. The right is always one movement — money arrives once.
 */
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import {
  invoices as invoicesApi,
  transactions as transactionsApi,
  accounts as accountsApi,
} from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { Account, Invoice, Transaction } from '@/types'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { ArrowRight } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface PairSide {
  kind: 'invoice' | 'recurring'
  id: string
  label?: string | null
  /** What this movement puts, or would put, against this promise. */
  amount: string
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 text-xs">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="text-foreground text-right tabular-nums">{value}</span>
    </div>
  )
}

function InvoiceSide({
  id,
  label,
  amount,
  money,
  showDate,
}: {
  id: string
  label?: string | null
  amount: string
  money: (value: string | number | null | undefined, currency?: string | null) => string
  showDate: (iso: string) => string
}) {
  const { t } = useTranslation()
  const { data: invoice } = useQuery<Invoice>({
    queryKey: ['invoice', id],
    queryFn: () => invoicesApi.get(id),
  })

  if (!invoice) {
    return (
      <div className="rounded-lg border border-border p-3">
        <p className="text-xs text-muted-foreground">{t('common.loading')}</p>
      </div>
    )
  }

  // The label the API already resolved. Composing it here from `series`
  // and `number` gets it wrong — `series` is the fiscal year, and the
  // prefix a reader recognises lives in the snapshot taken at issue.
  // Recomputing what the server already worked out is how the same number
  // ends up written two ways on one screen.
  const name = label ?? invoice.external_number ?? null

  return (
    <div className="rounded-lg border border-border p-3 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-foreground">
          {name ?? t('reconciliation.pair.draftInvoice')}
        </p>
        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
          {t(`invoices.state.${invoice.state}`, invoice.state)}
        </span>
      </div>
      {invoice.payee?.name && (
        <p className="text-xs text-muted-foreground truncate">{invoice.payee.name}</p>
      )}

      <div className="pt-1.5 space-y-1 border-t border-border">
        <Field
          label={t('reconciliation.pair.total')}
          value={money(invoice.total, invoice.currency)}
        />
        <Field
          label={t('reconciliation.pair.balance')}
          value={money(invoice.balance, invoice.currency)}
        />
        <Field label={t('reconciliation.pair.due')} value={showDate(invoice.due_date)} />
      </div>

      {/* The share of the movement that this promise takes. On its own the
          two columns only say "these are related"; this says how. */}
      <div className="pt-1.5 border-t border-border">
        <Field
          label={t('reconciliation.pair.applied')}
          value={
            <span className="font-semibold text-emerald-600">
              {money(amount, invoice.currency)}
            </span>
          }
        />
      </div>
    </div>
  )
}

export function ReconciliationPair({
  open,
  onClose,
  transactionId,
  sides,
  title,
}: {
  open: boolean
  onClose: () => void
  transactionId?: string | null
  sides: PairSide[]
  title?: string
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

  const money = (value: string | number | null | undefined, currency?: string | null) =>
    mask(formatCurrency(Number(value ?? 0), currency || 'USD', locale))
  const showDate = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale)
  const accountName = accounts.find((a) => a.id === transaction?.account_id)?.name

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>{title ?? t('reconciliation.pair.title')}</DialogTitle>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-[1fr_auto_1fr] items-start flex-1 overflow-y-auto">
          <div className="space-y-2">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              {t('reconciliation.pair.owed')}
            </p>
            {sides.map((side) =>
              side.kind === 'invoice' ? (
                <InvoiceSide
                  key={side.id}
                  id={side.id}
                  label={side.label}
                  amount={side.amount}
                  money={money}
                  showDate={showDate}
                />
              ) : (
                // A recurring bill is a promise too, but it has no
                // document behind it — so it shows what it is rather than
                // pretending to a shape it does not have.
                <div key={side.id} className="rounded-lg border border-border p-3">
                  <p className="text-sm font-semibold text-foreground">
                    {side.label ?? t('reconciliation.pair.recurringBill')}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {t('reconciliation.pair.recurringBill')}
                  </p>
                </div>
              ),
            )}
          </div>

          <div className="hidden sm:flex items-center justify-center pt-8 text-muted-foreground">
            <ArrowRight size={16} />
          </div>

          <div className="space-y-2">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              {t('reconciliation.pair.arrived')}
            </p>
            {transaction ? (
              <div className="rounded-lg border border-border p-3 space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-foreground truncate">
                    {transaction.description}
                  </p>
                  <span
                    className={cn(
                      'text-sm font-bold tabular-nums shrink-0',
                      transaction.type === 'credit'
                        ? 'text-emerald-600'
                        : 'text-foreground',
                    )}
                  >
                    {money(transaction.amount, transaction.currency)}
                  </span>
                </div>
                <div className="pt-1.5 space-y-1 border-t border-border">
                  <Field
                    label={t('reconciliation.pair.date')}
                    value={showDate(transaction.date)}
                  />
                  {accountName && (
                    <Field
                      label={t('reconciliation.pair.account')}
                      value={accountName}
                    />
                  )}
                  {/* `payee_name` is the resolved one; `payee` is the raw
                      string the bank sent, which is worth falling back to
                      when nothing has been mapped yet. */}
                  {(transaction.payee_name || transaction.payee) && (
                    <Field
                      label={t('reconciliation.pair.payer')}
                      value={transaction.payee_name || transaction.payee}
                    />
                  )}
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                {transactionId ? t('common.loading') : t('reconciliation.pair.noMoney')}
              </p>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
