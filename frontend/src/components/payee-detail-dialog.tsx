import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Pencil, Trash2 } from 'lucide-react'

import { payees as payeesApi, transactions as transactionsApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { formatCurrency } from '@/lib/format'
import type { Payee } from '@/types'

type PayeeDetailDialogProps = {
  /** The payee to describe, or null when the dialog is closed. */
  payee: Payee | null
  canWrite: boolean
  onOpenChange: (open: boolean) => void
  onEdit: (payee: Payee) => void
  onDelete: (payee: Payee) => void
}

/** What we know about one counterparty, opened from the list.
 *
 *  Takes the whole payee rather than an id so the title is right on the first
 *  frame: the row the user just clicked already carries the name, and a
 *  spinner where the name goes is a spinner over the one thing they came for.
 */
export function PayeeDetailDialog({
  payee,
  canWrite,
  onOpenChange,
  onEdit,
  onDelete,
}: PayeeDetailDialogProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'

  // Closing sets `payee` to null while Radix is still playing the exit
  // animation. Holding the last one keeps the content from blanking out
  // underneath the fade. Adjusted during render rather than in an effect:
  // that is React's own answer for state derived from a changing prop.
  const [lastPayee, setLastPayee] = useState<Payee | null>(payee)
  if (payee && payee !== lastPayee) setLastPayee(payee)
  const shown = payee ?? lastPayee

  const { data: summaryData, isLoading: summaryLoading } = useQuery({
    queryKey: ['payees', payee?.id, 'summary'],
    queryFn: () => payeesApi.summary(payee!.id),
    enabled: !!payee,
  })

  const { data: recentTxData } = useQuery({
    queryKey: ['payees', payee?.id, 'recent-transactions'],
    queryFn: () => transactionsApi.list({ payee_id: payee!.id, limit: 5 }),
    enabled: !!payee,
  })

  return (
    <Dialog open={!!payee} onOpenChange={onOpenChange}>
      {/* The body scrolls, the footer does not: a payee with five recent
          transactions is taller than a laptop viewport, and Delete below the
          fold is Delete nobody can reach. Mirrors the create/edit dialog. */}
      {/* The figures below are the description; Radix warns when neither a
          DialogDescription nor this opt-out is present. Same call as
          global-chat-panel and rule-dialog. */}
      <DialogContent
        className="sm:max-w-lg flex flex-col max-h-[calc(100dvh-2rem)]"
        aria-describedby={undefined}
      >
        <DialogHeader>
          {/* pr-8 clears the close button. Wrapping rather than truncating:
              the list ellipsizes long names, so this is the one place the
              whole thing is meant to be readable. */}
          <DialogTitle className="pr-8 break-words">{shown?.name}</DialogTitle>
        </DialogHeader>

        <div className="space-y-3 overflow-y-auto flex-1 -mx-1 px-1">
          {summaryLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : summaryData ? (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">{t('payees.totalSpent')}</p>
                  <p className="text-lg font-bold text-rose-500 tabular-nums">
                    {mask(formatCurrency(summaryData.total_spent, userCurrency, locale))}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{t('payees.totalReceived')}</p>
                  <p className="text-lg font-bold text-emerald-600 tabular-nums">
                    {mask(formatCurrency(summaryData.total_received, userCurrency, locale))}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{t('payees.transactionCount')}</p>
                  <p className="text-lg font-bold tabular-nums">{summaryData.transaction_count}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{t('payees.lastTransaction')}</p>
                  <p className="text-sm font-medium">
                    {summaryData.last_transaction_date
                      ? new Date(summaryData.last_transaction_date + 'T00:00:00').toLocaleDateString(dateLocale)
                      : '—'}
                  </p>
                </div>
              </div>

              {summaryData.most_common_category && (
                <p className="text-xs text-muted-foreground">
                  {t('payees.topCategory')}: <span className="font-medium text-foreground">{summaryData.most_common_category.name}</span>
                </p>
              )}

              {/* Recent transactions */}
              {recentTxData && recentTxData.items.length > 0 && (
                <div className="pt-3 border-t border-border space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">{t('dashboard.recentTransactions')}</p>
                  <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                    {recentTxData.items.map((tx) => (
                      <div key={tx.id} className="flex items-center justify-between px-3 py-2 bg-background text-sm">
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-foreground truncate">{tx.description}</p>
                          <p className="text-xs text-muted-foreground">
                            {new Date(tx.date + 'T00:00:00').toLocaleDateString(dateLocale)}
                            {tx.category?.name && <> · {tx.category.name}</>}
                          </p>
                        </div>
                        <span className={`text-sm font-semibold tabular-nums ml-3 ${tx.type === 'debit' ? 'text-rose-500' : 'text-emerald-600'}`}>
                          {mask(formatCurrency(tx.amount, tx.currency, locale))}
                        </span>
                      </div>
                    ))}
                  </div>
                  {/* Any transaction at all is worth a way through to it. The
                      list above caps at five, but the cap is not the reason
                      someone wants the full ledger. */}
                  {summaryData.transaction_count > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full text-xs text-muted-foreground hover:text-foreground gap-1"
                      onClick={() => {
                        // Close first: navigating out from inside the dialog
                        // leaves the scroll lock mounted over the next route.
                        onOpenChange(false)
                        navigate(`/transactions?payee_id=${summaryData.payee.id}`)
                      }}
                    >
                      {t('payees.viewAllTransactions', { count: summaryData.transaction_count })}
                      <ArrowRight size={12} />
                    </Button>
                  )}
                </div>
              )}
            </>
          ) : null}
        </div>

        {canWrite && shown && (
          <DialogFooter className="flex justify-between sm:justify-between">
            <Button variant="destructive" onClick={() => onDelete(shown)}>
              <Trash2 size={14} className="mr-1" />
              {t('common.delete')}
            </Button>
            <Button variant="outline" onClick={() => onEdit(shown)}>
              <Pencil size={14} className="mr-1" />
              {t('common.edit')}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}
