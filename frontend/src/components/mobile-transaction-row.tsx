import { getAccountName } from '@/lib/account-utils'
import { AccountIcon } from '@/components/account-icon'
import { CategorySplitBadge, TransactionCategoryIcon } from '@/components/category-lines-display'
import { isSplitByCategory } from '@/lib/category-lines'
import type { Transaction, Account } from '@/types'
import { AlertTriangle, ArrowLeftRight, CalendarClock, ChartNoAxesColumn, CircleDashed, Clock, EyeClosed, Paperclip, Repeat } from 'lucide-react'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { TransactionFlagMarker } from '@/components/transaction-flag-marker'
import { useTranslation } from 'react-i18next'
import { formatCurrency, ltrAmount } from '@/lib/format'
import { shouldShowPendingBadge } from '@/lib/transaction-status'
import { isTransferTransaction } from '@/lib/transaction-transfer'
import { transactionDescription } from '@/lib/transaction-description'
import { ReportingDateBadge } from '@/components/reporting-date-badge'

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
  /** Locale for dates (follows the date-format setting); defaults to `locale`. */
  dateLocale?: string
  userCurrency: string
  onSelect: (id: string, shiftKey: boolean) => void
  onClick: (tx: Transaction) => void
  /** Turns the transaction into a bill; the phone list has no context menu. */
  onMakeRecurring?: (tx: Transaction) => void
  /** Show the payee instead of the account name in an account-scoped view. */
  showPayee?: boolean
  /** Localize synthetic opening-balance descriptions only in account detail. */
  localizeOpeningBalance?: boolean
  /**
   * Show the "new" dot. Opt-in: only a caller whose click clears the marker
   * should show it, or the dot would stay after the row is opened.
   */
  showUnread?: boolean
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
  dateLocale,
  userCurrency,
  onSelect,
  onClick,
  onMakeRecurring,
  showPayee = false,
  localizeOpeningBalance = false,
  showUnread = false,
}: MobileTransactionRowProps) {
  const { mask } = usePrivacyMode()
  const { t } = useTranslation()
  const description = localizeOpeningBalance
    ? transactionDescription(tx.description, tx.source, t)
    : tx.description

  const displayAmount = tx.is_shared && tx.viewer_share != null
    ? Number(tx.viewer_share)
    : Number(tx.amount)

  const amountColor = tx.is_ignored
    ? 'text-gray-500'
    : tx.type === 'credit'
      ? 'text-emerald-600'
      : 'text-rose-500'

  const virtual = tx.virtual === true

  // Badges that ride under the description. They are listed once here so the
  // row only renders the badge line when at least one of them is on.
  const showGroupBadge = !!tx.group_id
  const showUnreadDot = showUnread && tx.is_read === false && !tx.is_shared
  const showSplitBadge = isSplitByCategory(tx)
  const showTransferIcon = isTransferTransaction(tx)
  const showExcludedIcon = tx.exclude_from_pnl && !tx.is_ignored
  const showUnpaidIcon = tx.is_paid === false
  const showRecurringBadge = tx.recurring_transaction_id != null && !virtual
  const showInstallmentBadge = tx.installment_number != null && tx.total_installments != null
  const showPendingBadge = shouldShowPendingBadge(tx)
  const showAttachmentIcon = (tx.attachment_count ?? 0) > 0
  const hasBadges =
    showGroupBadge ||
    showUnreadDot ||
    showSplitBadge ||
    showTransferIcon ||
    virtual ||
    tx.is_ignored ||
    showExcludedIcon ||
    tx.is_flagged === true ||
    showUnpaidIcon ||
    showRecurringBadge ||
    showInstallmentBadge ||
    showPendingBadge ||
    showAttachmentIcon

  return (
    <div
      ref={highlighted ? highlightedRowRef : undefined}
      className={`flex items-center gap-3 ps-3 pe-3 py-3 border-b border-border last:border-0 transition-colors ${
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
        <TransactionCategoryIcon tx={tx} size="md" />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        {/* Description gets its own line with the row's full width: sharing
            it with the badges left it a few characters wide on a phone and
            broke words in the middle (#433). The badges wrap below. */}
        <p className={`text-sm font-semibold text-foreground leading-tight ${hasWordBreaks(description) ? 'break-words whitespace-normal' : 'truncate'}`}>
          {description}
        </p>
        {hasBadges && (
          <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5" data-testid="transaction-badges">
            {showGroupBadge && (
              <span className="inline-flex items-center text-[9px] font-semibold uppercase tracking-wide text-violet-700 bg-violet-50 border border-violet-200 dark:bg-violet-950/40 dark:text-violet-300 dark:border-violet-900 px-1 py-0.5 rounded-full shrink-0">
                {tx.is_shared && tx.parent_owner_name ? tx.parent_owner_name : t('splitGroups.ownerRowBadge', { group: groupName ?? '' })}
              </span>
            )}
            {showUnreadDot && (
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-sky-500"
                title={t('transactions.newBadge')}
                role="img"
                aria-label={t('transactions.newBadge')}
              />
            )}
            {showSplitBadge && <CategorySplitBadge tx={tx} compact className="shrink-0 text-[9px] px-1" />}
            {showTransferIcon && (
              <ArrowLeftRight
                className="h-3 w-3 text-blue-600 shrink-0"
                aria-label={t('transactions.transfer')}
              />
            )}
            {virtual && (
              <CalendarClock className="h-3 w-3 text-primary shrink-0" />
            )}
            {tx.is_ignored && (
              <EyeClosed className="h-3 w-3 text-gray-500 shrink-0" />
            )}
            {showExcludedIcon && (
              <ChartNoAxesColumn
                className="h-3 w-3 text-slate-500 shrink-0"
                aria-label={t('transactions.excludedFromReports')}
              />
            )}
            <TransactionFlagMarker tx={tx} locale={dateLocale ?? locale} className="[&>svg]:h-3 [&>svg]:w-3" />
            {/* Unpaid (issue #15). The mark itself is flipped in the dialog the
                row opens; here it only says the money has not moved. */}
            {showUnpaidIcon && (
              <CircleDashed
                className="h-3 w-3 shrink-0 text-amber-500"
                role="img"
                aria-label={t('transactions.unpaid')}
              >
                <title>{t('transactions.unpaid')}</title>
              </CircleDashed>
            )}
            {showRecurringBadge && (
              <span className="text-[9px] font-semibold uppercase tracking-wide text-primary bg-primary/5 border border-primary/10 px-1 py-0.5 rounded-full shrink-0">
                R
              </span>
            )}
            {showInstallmentBadge && (
              <span className="text-[9px] font-bold tabular-nums text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-500/20 border border-amber-200 dark:border-amber-500/30 px-1 py-0.5 rounded-full shrink-0">
                {tx.installment_number}/{tx.total_installments}
              </span>
            )}
            {showPendingBadge && (
              <span
                title={t('transactions.pending')}
                className="shrink-0 inline-flex items-center justify-center rounded-full border border-amber-200 bg-amber-50 p-0.5 dark:border-amber-500/30 dark:bg-amber-500/10"
              >
                <Clock size={12} className="text-amber-500" role="img" aria-label={t('transactions.pending')} />
              </span>
            )}
            {showAttachmentIcon && (
              <Paperclip size={11} className="text-muted-foreground shrink-0" />
            )}
          </div>
        )}

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
              {tx.transfer_counterpart && (
                <>
                  <ArrowLeftRight className="h-3 w-3 text-blue-600 shrink-0" aria-hidden="true" />
                  <span className="text-xs text-muted-foreground truncate">
                    {tx.transfer_counterpart.account_name}
                  </span>
                </>
              )}
            </div>
          )
        )}
        <ReportingDateBadge tx={tx} locale={dateLocale ?? locale} />
      </div>

      {/* Amount */}
      <div className="shrink-0 text-end">
        <span className={`text-sm font-bold tabular-nums ${amountColor}`}>
          {mask(
            ltrAmount(`${tx.is_ignored ? ' ' : tx.type === 'credit' ? '+' : '\u2212'}${formatCurrency(
              Math.abs(displayAmount),
              tx.currency,
              locale,
            )}`),
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

      {/* The phone list has no context menu, so the bill action sits on the
          row itself. */}
      {onMakeRecurring && (
        <button
          type="button"
          aria-label={t('transactions.makeRecurring')}
          onClick={(e) => {
            e.stopPropagation()
            onMakeRecurring(tx)
          }}
          className="shrink-0 rounded-md p-2 -me-1 text-muted-foreground hover:bg-muted hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
        >
          <Repeat size={16} />
        </button>
      )}
    </div>
  )
}
