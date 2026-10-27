import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { AccountIcon } from '@/components/account-icon'
import { AccountRowActions } from '@/components/account-row-actions'
import { formatAccountMask, getAccountLabel, getAccountName } from '@/lib/account-utils'
import { getAccountTypeConfig } from '@/lib/account-type-config'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import type { Account } from '@/types'

function daysUntil(dateStr: string | null): number | null {
  if (!dateStr) return null
  const due = new Date(dateStr + 'T00:00:00')
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((due.getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

type BaseProps = {
  account: Account
}

type AccountCardProps = BaseProps & (
  | { variant: 'closed'; onReopen: () => void; reopenPending: boolean }
  | { variant?: 'active'; onEdit: () => void; onClose: () => void; onDelete?: () => void; deletePending: boolean }
)

export default function AccountCard(props: AccountCardProps) {
  const { account: acc } = props
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  if (props.variant === 'closed') {
    return (
      <div className="flex items-center px-5 py-3">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <AccountIcon account={acc} />
          <p className="text-sm font-medium text-muted-foreground truncate">{getAccountLabel(acc)}</p>
        </div>
        {canWrite && (
          <Button
            variant="ghost"
            size="sm"
            className="text-xs text-muted-foreground hover:text-foreground h-7 px-2 mr-3"
            onClick={props.onReopen}
            disabled={props.reopenPending}
          >
            {t('accounts.reopen')}
          </Button>
        )}
        <p className="text-sm font-semibold tabular-nums text-muted-foreground w-32 text-right">
          {mask(formatCurrency(Number(acc.current_balance), acc.currency, locale))}
        </p>
      </div>
    )
  }

  const cfg = getAccountTypeConfig(acc.type)
  const bal = Number(acc.current_balance)
  const isCC = acc.type === 'credit_card'
  const dueIn = isCC ? daysUntil(acc.next_due_date) : null
  const dueText =
    dueIn == null ? null
      : dueIn < 0 ? t('accounts.overdue')
      : dueIn === 0 ? t('accounts.dueToday')
      : t('accounts.dueIn', { count: dueIn })
  const dueClass = dueIn != null && dueIn <= 3 ? 'text-amber-600' : 'text-muted-foreground'
  const accountMask = formatAccountMask(acc)

  return (
    <div className="group flex items-center px-5 py-3 hover:bg-muted/50 transition-colors">
      <Link to={`/accounts/${acc.id}`} className="flex items-center gap-3 flex-1 min-w-0">
        <AccountIcon account={acc} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground truncate">{getAccountName(acc)}</p>
          <p className="text-xs text-muted-foreground">
            {t(cfg.label)}
            {accountMask && <> · <span className="tabular-nums">{accountMask}</span></>}
            {acc.shared_balance_group && <> · <span>{t('accounts.sharedCreditBalance')}</span></>}
            {dueText && <> · <span className={dueClass}>{dueText}</span></>}
          </p>
        </div>
      </Link>
      <div className="shrink-0 text-right">
        <p className={`text-xs sm:text-sm font-semibold tabular-nums ${(acc.type === 'credit_card' ? bal > 0 : bal < 0) ? 'text-rose-500' : 'text-foreground'}`}>
          {mask(formatCurrency(bal, acc.currency, locale))}
        </p>
        {isCC && acc.available_credit != null ? (
          <p className="text-[10px] text-muted-foreground tabular-nums">
            {t('accounts.availableCredit')}: {mask(formatCurrency(Number(acc.available_credit), acc.currency, locale))}
          </p>
        ) : acc.balance_primary != null && acc.currency !== userCurrency && (
          <p className="text-[10px] text-muted-foreground tabular-nums">
            {mask(formatCurrency(acc.balance_primary, userCurrency, locale))}
          </p>
        )}
      </div>
      {canWrite && (
        <AccountRowActions
          accountName={getAccountName(acc)}
          onEdit={props.onEdit}
          onClose={props.onClose}
          onDelete={props.onDelete}
          deletePending={props.deletePending}
        />
      )}
    </div>
  )
}
