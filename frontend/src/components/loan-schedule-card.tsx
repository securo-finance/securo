import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CalendarCheck, Gauge, ListChecks, Wallet } from 'lucide-react'

import { LoanBalanceDialog } from '@/components/loan-balance-dialog'
import { Badge } from '@/components/ui/badge'
import { accounts } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Below this difference from the contract, the loan reads as on schedule. */
const ON_SCHEDULE_TOLERANCE = 1

/**
 * Where the schedule should sit so a row reads as the current installment:
 * roughly centred, which keeps the installments already paid visible above it.
 * Clamped at zero so an early installment leaves the table at its top.
 */
function scheduleScrollTop(rowOffset: number, viewportHeight: number): number {
  return Math.max(0, Math.round(rowOffset - viewportHeight / 2))
}

/** One contract figure as a muted tile: an icon-led label over a bold value. */
function Metric({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="rounded-lg bg-muted/40 p-3">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground [&>svg]:size-3.5 [&>svg]:shrink-0">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      {children}
    </div>
  )
}

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
  const scheduleRef = useRef<HTMLDivElement>(null)
  const currentRowRef = useRef<HTMLTableRowElement>(null)
  const currentNumber = data?.next_payment?.number

  // A long contract opens on installment 1, which on a mortgage is hundreds of
  // rows above where the borrower actually is. Put the current one in view.
  useEffect(() => {
    const container = scheduleRef.current
    const row = currentRowRef.current
    if (!showSchedule || !container || !row) return
    const offset = row.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop
    container.scrollTop = scheduleScrollTop(offset, container.clientHeight)
  }, [showSchedule, currentNumber])

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
      <div className="bg-card rounded-xl border border-border shadow-sm p-5 space-y-2 mb-6">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <h2 className="text-base font-bold text-foreground">{t('loans.title')}</h2>
          {adjust}
        </div>
        <p className="text-sm text-muted-foreground">{t('loans.notConfigured')}</p>
      </div>
    )
  }

  const total = data.installments.length
  const aheadBy = data.ahead_by ?? 0
  const behind = !data.paid_off && aheadBy <= -ON_SCHEDULE_TOLERANCE
  const status = data.paid_off
    ? t('loans.paidOff')
    : Math.abs(aheadBy) < ON_SCHEDULE_TOLERANCE
      ? t('loans.onSchedule')
      : aheadBy > 0
        ? t('loans.ahead', { amount: money(aheadBy) })
        : t('loans.behind', { amount: money(-aheadBy) })
  const statusTone = data.paid_off ? 'good' : behind ? 'bad' : aheadBy > 0 ? 'good' : 'neutral'

  const remaining = data.remaining_payments ?? 0
  const paid = Math.max(0, total - remaining)
  const progress = total > 0 ? Math.min(100, Math.max(0, (paid / total) * 100)) : 0

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm mb-6">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-5 pt-5 pb-3">
        <h2 className="text-base font-bold text-foreground">{t('loans.title')}</h2>
        <div className="flex items-center gap-3">
          <Badge
            className={cn(
              'border-transparent',
              statusTone === 'good' && 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400',
              statusTone === 'bad' && 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-400',
              statusTone === 'neutral' && 'bg-muted text-muted-foreground',
            )}
          >
            {status}
          </Badge>
          {adjust}
        </div>
      </div>
      {total > 0 && (
        <div className="px-5 pb-4">
          <div
            className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={paid}
            aria-label={t('loans.progress', { paid, total })}
          >
            <div
              className={cn('h-full rounded-full', data.paid_off ? 'bg-emerald-500' : 'bg-primary')}
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 px-5 pb-4">
        <Metric icon={<Wallet />} label={t('loans.nextPayment')}>
          <span className="text-lg font-bold tabular-nums text-foreground">
            {data.next_payment ? money(data.next_payment.payment) : '—'}
          </span>
          {data.next_payment && (
            <span className="mt-1 block text-xs text-muted-foreground tabular-nums">
              {t('loans.nextPaymentSplit', {
                date: day(data.next_payment.due_date),
                interest: money(data.next_payment.interest),
                principal: money(data.next_payment.principal),
              })}
            </span>
          )}
        </Metric>
        <Metric icon={<ListChecks />} label={t('loans.remaining')}>
          <span className="text-lg font-bold tabular-nums text-foreground">
            {t('loans.remainingValue', { remaining, total })}
          </span>
          <span className="mt-1 block text-xs text-muted-foreground tabular-nums">
            {t('loans.totalInterest')}: {money(data.total_interest ?? 0)}
          </span>
        </Metric>
        <Metric icon={<CalendarCheck />} label={t('loans.payoff')}>
          <span className="text-lg font-bold tabular-nums text-foreground">
            {data.payoff_date ? day(data.payoff_date) : '—'}
          </span>
        </Metric>
        <Metric icon={<Gauge />} label={t('loans.projectedPayoff')}>
          <span
            className={cn(
              'text-lg font-bold tabular-nums',
              !data.projected_payoff_date ? 'text-muted-foreground' : behind ? 'text-rose-500' : 'text-emerald-600',
            )}
          >
            {data.projected_payoff_date ? day(data.projected_payoff_date) : '—'}
          </span>
        </Metric>
      </div>
      <div className="border-t border-border px-5 py-2">
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
        <div ref={scheduleRef} className="max-h-96 overflow-auto border-t border-border">
          <table className="w-full text-xs tabular-nums">
            <thead className="sticky top-0 bg-card text-muted-foreground">
              <tr>
                <th className="px-5 py-2 text-left font-medium">#</th>
                <th className="px-2 py-2 text-left font-medium">{t('loans.colDate')}</th>
                <th className="px-2 py-2 text-right font-medium">{t('loans.colPayment')}</th>
                <th className="px-2 py-2 text-right font-medium">{t('loans.colInterest')}</th>
                <th className="px-2 py-2 text-right font-medium">{t('loans.colPrincipal')}</th>
                <th className="px-5 py-2 text-right font-medium">{t('loans.colBalance')}</th>
              </tr>
            </thead>
            <tbody>
              {data.installments.map((row) => (
                <tr
                  key={row.number}
                  ref={row.number === currentNumber ? currentRowRef : undefined}
                  aria-current={row.number === currentNumber ? 'step' : undefined}
                  className={`border-t border-border ${row.number === currentNumber ? 'bg-primary/5 font-medium' : ''}`}
                >
                  <td className="px-5 py-2">{row.number}</td>
                  <td className="px-2 py-2">{day(row.due_date)}</td>
                  <td className="px-2 py-2 text-right">{money(row.payment)}</td>
                  <td className="px-2 py-2 text-right">{money(row.interest)}</td>
                  <td className="px-2 py-2 text-right">{money(row.principal)}</td>
                  <td className="px-5 py-2 text-right">{money(row.balance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
