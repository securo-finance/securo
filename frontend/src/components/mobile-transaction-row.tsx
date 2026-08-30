import { getAccountName } from '@/lib/account-utils'
import { AccountIcon } from '@/components/account-icon'
import { CategoryIcon } from '@/components/category-icon'
import type { Transaction, Account } from '@/types'
import { AlertTriangle, ArrowLeftRight, CalendarClock, Clock, EyeClosed, Paperclip } from 'lucide-react'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useTranslation } from 'react-i18next'
import { formatCurrency } from '@/lib/format'
import { shouldShowPendingBadge } from '@/lib/transaction-status'

/** Keep long merchant references on one line when there are no word breaks. */
function hasWordBreaks(text: string): boolean {
  return /\s/.test(text)
}

interface MobileTransactionRowProps {
  tx: Transaction
  account: Account | undefined
  groupName: string | undefined
  selected: boolean
  selectable: boolean
  canWrite: boolean
  highlighted: boolean
  highlightedRowRef?: React.Ref<HTMLDivElement>
  locale: string
  userCurrency: string
  onSelect: (id: string, shiftKey: boolean) => void
  onClick: (tx: Transaction) => void
  /** Show the payee instead of the account name in an account-scoped view. */
  showPayee?: boolean
}

export function MobileTransactionRow({
  tx,
  account,
  groupName,
  selected,
  selectable,
  canWrite,
  highlighted,
  highlightedRowRef,
  locale,
  userCurrency,
  onSelect,
  onClick,
  showPayee = false,
}: MobileTransactionRowProps) {
  const { mask } = usePrivacyMode()
  const { t } = useTranslation()

  const displayAmount = tx.is_shared && tx.viewer_share != null
    ? Number(tx.viewer_share)
    : Number(tx.amount)

  const amountColor = tx.is_ignored
    ? 'text-gray-500'
    : tx.type === 'credit'
      ? 'text-emerald-600'
      : 'text-rose-500'

  const virtual = tx.virtual === true

  return (
    <div
      ref={highlighted ? highlightedRowRef : undefined}
      className={`flex items-center gap-3 pl-3 pr-3 py-3 border-b border-border last:border-0 transition-colors ${
        selected ? 'bg-primary/5' : 'bg-card'
      } ${highlighted ? 'securo-highlight-flash' : ''} ${
        virtual ? 'opacity-80' : ''
      } ${
        virtual || tx.is_shared || !canWrite ? 'cursor-default' : 'cursor-pointer active:bg-muted/60'
      }`}
      onClick={() => {
        if (virtual) return
        if (tx.is_shared) return
        if (!canWrite) return
        onClick(tx)
      }}
    >
      {/* Checkbox */}
      {selectable && (
        <div className="shrink-0">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => {}}
            onClick={(e) => {
              e.stopPropagation()
              onSelect(tx.id, e.shiftKey)
            }}
            className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
          />
        </div>
      )}

      {/* Category Icon */}
      <div className="shrink-0">
        <CategoryIcon icon={tx.category?.icon} color={tx.category?.color} size="md" />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        {/* Description row */}
        <div className="flex items-center gap-1.5">
          <p className={`text-sm font-semibold text-foreground leading-tight min-w-0 ${hasWordBreaks(tx.description) ? 'break-words whitespace-normal' : 'truncate'}`}>
            {tx.description}
          </p>
          {tx.group_id && (
            <span className="inline-flex items-center text-[9px] font-semibold uppercase tracking-wide text-violet-700 bg-violet-50 border border-violet-200 dark:bg-violet-950/40 dark:text-violet-300 dark:border-violet-900 px-1 py-0.5 rounded-full shrink-0">
              {tx.is_shared && tx.parent_owner_name ? tx.parent_owner_name : t('splitGroups.ownerRowBadge', { group: groupName ?? '' })}
            </span>
          )}
          {!!tx.transfer_pair_id && (
            <ArrowLeftRight className="h-3 w-3 text-blue-600 shrink-0" />
          )}
          {virtual && (
            <CalendarClock className="h-3 w-3 text-primary shrink-0" />
          )}
          {tx.is_ignored && (
            <EyeClosed className="h-3 w-3 text-gray-500 shrink-0" />
          )}
          {tx.recurring_transaction_id != null && !virtual && (
            <span className="text-[9px] font-semibold uppercase tracking-wide text-primary bg-primary/5 border border-primary/10 px-1 py-0.5 rounded-full shrink-0">
              R
            </span>
          )}
          {tx.installment_number != null && tx.total_installments != null && (
            <span className="text-[9px] font-bold tabular-nums text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-500/20 border border-amber-200 dark:border-amber-500/30 px-1 py-0.5 rounded-full shrink-0">
              {tx.installment_number}/{tx.total_installments}
            </span>
          )}
          {shouldShowPendingBadge(tx) && (
            <span
              title={t('transactions.pending')}
              className="shrink-0 inline-flex items-center justify-center rounded-full border border-amber-200 bg-amber-50 p-0.5 dark:border-amber-500/30 dark:bg-amber-500/10"
            >
              <Clock size={12} className="text-amber-500" role="img" aria-label={t('transactions.pending')} />
            </span>
          )}
          {(tx.attachment_count ?? 0) > 0 && (
            <Paperclip size={11} className="text-muted-foreground shrink-0" />
          )}
        </div>

        {/* Account row or payee row */}
        {showPayee ? (
          (tx.payee_name || tx.payee) && (tx.payee_name || tx.payee) !== tx.description ? (
            <p className="text-xs text-muted-foreground mt-0.5 truncate">
              {tx.payee_name || tx.payee}
            </p>
          ) : null
        ) : (
          account && (
            <div className="flex items-center gap-1.5 mt-0.5">
              <AccountIcon account={account} size="xs" />
              <span className="text-xs text-muted-foreground truncate">
                {getAccountName(account)}
              </span>
            </div>
          )
        )}
      </div>

      {/* Amount */}
      <div className="shrink-0 text-right">
        <span className={`text-sm font-bold tabular-nums ${amountColor}`}>
          {mask(
            `${tx.is_ignored ? ' ' : tx.type === 'credit' ? '+' : '\u2212'}${formatCurrency(
              Math.abs(displayAmount),
              tx.currency,
              locale,
            )}`,
          )}
        </span>
        {tx.amount_primary != null && tx.currency !== userCurrency && (
          <div className="flex items-center justify-end gap-0.5 mt-0.5">
            {tx.fx_fallback && (
              <AlertTriangle size={10} className="text-amber-500 shrink-0" />
            )}
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {mask(formatCurrency(Math.abs(tx.amount_primary), userCurrency, locale))}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
