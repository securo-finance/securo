import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { LoanBalanceDialog } from '@/components/loan-balance-dialog'
import { accounts } from '@/lib/api'
import { formatCurrency } from '@/lib/format'

/** Below this difference from the contract, the loan reads as on schedule. */
const ON_SCHEDULE_TOLERANCE = 1

/**
 * A loan's contract next to its real balance: next payment, what is left,
 * payoff by the contract and at the current pace, and the full schedule.
 * The backend computes it from the account's contract terms on read. The
 * lender's statement can always correct the balance, with or without terms.
 */
export function LoanScheduleCard({
  accountId,
  locale,
  dateLocale,
  mask,
  canWrite = false,
}: {
  accountId: string
  locale: string
  dateLocale: string
  mask: (value: string) => string
  canWrite?: boolean
}) {
  const { t } = useTranslation()
  const [showSchedule, setShowSchedule] = useState(false)
  const [adjusting, setAdjusting] = useState(false)
  const { data } = useQuery({
    queryKey: ['accounts', accountId, 'loan-schedule'],
    queryFn: () => accounts.loanSchedule(accountId),
  })
  if (!data) return null

  const money = (value: number) => mask(formatCurrency(value, data.currency, locale))
  const day = (value: string) => new Date(value + 'T00:00:00').toLocaleDateString(dateLocale)
  const adjust = canWrite && (
    <>
      <button
        type="button"
        className="text-xs font-medium text-primary hover:underline"
        onClick={() => setAdjusting(true)}
      >
        {t('loans.adjustAction')}
      </button>
      {adjusting && (
        <LoanBalanceDialog
          open
          onClose={() => setAdjusting(false)}
          accountId={accountId}
          currentDebt={data.current_debt}
          currency={data.currency}
          locale={locale}
        />
      )}
    </>
  )

  if (!data.configured) {
    return (
      <div className="bg-card rounded-xl border border-border shadow-sm p-4 space-y-1">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">{t('loans.title')}</h2>
          {adjust}
        </div>
        <p className="text-sm text-muted-foreground">{t('loans.notConfigured')}</p>
      </div>
    )
  }

  const total = data.installments.length
  const aheadBy = data.ahead_by ?? 0
  const status = data.paid_off
    ? t('loans.paidOff')
    : Math.abs(aheadBy) < ON_SCHEDULE_TOLERANCE
      ? t('loans.onSchedule')
      : aheadBy > 0
        ? t('loans.ahead', { amount: money(aheadBy) })
        : t('loans.behind', { amount: money(-aheadBy) })

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm">
      <div className="flex items-center justify-between gap-3 px-4 pt-4">
        <h2 className="text-sm font-semibold">{t('loans.title')}</h2>
        <div className="flex items-center gap-3">
          <span
            className={`text-xs font-medium ${!data.paid_off && aheadBy <= -ON_SCHEDULE_TOLERANCE ? 'text-rose-500' : 'text-emerald-600'}`}
          >
            {status}
          </span>
          {adjust}
        </div>
      </div>
      <dl className="grid grid-cols-2 lg:grid-cols-4 gap-4 p-4 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">{t('loans.nextPayment')}</dt>
          <dd className="font-semibold tabular-nums">{data.next_payment ? money(data.next_payment.payment) : '—'}</dd>
          {data.next_payment && (
            <dd className="text-xs text-muted-foreground">
              {t('loans.nextPaymentSplit', {
                date: day(data.next_payment.due_date),
                interest: money(data.next_payment.interest),
                principal: money(data.next_payment.principal),
              })}
            </dd>
          )}
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('loans.remaining')}</dt>
          <dd className="font-semibold tabular-nums">
            {t('loans.remainingValue', { remaining: data.remaining_payments ?? 0, total })}
          </dd>
          <dd className="text-xs text-muted-foreground">
            {t('loans.totalInterest')}: {money(data.total_interest ?? 0)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('loans.payoff')}</dt>
          <dd className="font-semibold tabular-nums">{data.payoff_date ? day(data.payoff_date) : '—'}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('loans.projectedPayoff')}</dt>
          <dd className="font-semibold tabular-nums">
            {data.projected_payoff_date ? day(data.projected_payoff_date) : '—'}
          </dd>
        </div>
      </dl>
      <div className="border-t border-border px-4 py-2">
        <button
          type="button"
          className="text-xs font-medium text-primary hover:underline"
          onClick={() => setShowSchedule(!showSchedule)}
          aria-expanded={showSchedule}
        >
          {showSchedule ? t('loans.hideSchedule') : t('loans.showSchedule')}
        </button>
      </div>
      {showSchedule && (
        <div className="max-h-96 overflow-auto border-t border-border">
          <table className="w-full text-xs tabular-nums">
            <thead className="sticky top-0 bg-card text-muted-foreground">
              <tr>
                <th className="px-4 py-2 text-left font-medium">#</th>
                <th className="px-2 py-2 text-left font-medium">{t('loans.colDate')}</th>
                <th className="px-2 py-2 text-right font-medium">{t('loans.colPayment')}</th>
                <th className="px-2 py-2 text-right font-medium">{t('loans.colInterest')}</th>
                <th className="px-2 py-2 text-right font-medium">{t('loans.colPrincipal')}</th>
                <th className="px-4 py-2 text-right font-medium">{t('loans.colBalance')}</th>
              </tr>
            </thead>
            <tbody>
              {data.installments.map((row) => (
                <tr
                  key={row.number}
                  className={`border-t border-border ${row.number === data.next_payment?.number ? 'bg-primary/5 font-medium' : ''}`}
                >
                  <td className="px-4 py-1.5">{row.number}</td>
                  <td className="px-2 py-1.5">{day(row.due_date)}</td>
                  <td className="px-2 py-1.5 text-right">{money(row.payment)}</td>
                  <td className="px-2 py-1.5 text-right">{money(row.interest)}</td>
                  <td className="px-2 py-1.5 text-right">{money(row.principal)}</td>
                  <td className="px-4 py-1.5 text-right">{money(row.balance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
