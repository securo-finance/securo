import type { ReactNode } from 'react'
import type { Account } from '@/types'
import AccountCard from './account_card'

type BaseProps = {
  title?: string
  header?: ReactNode
  accounts: Account[]
  emptyMessage?: string
  centeredEmpty?: boolean
}

type AccountSectionProps = BaseProps & (
  | {
    variant: 'closed'
    onReopen: (account: Account) => void
    reopenPending: boolean
  }
  | {
    variant?: 'active'
    onEdit: (account: Account) => void
    onClose: (account: Account) => void
    onDelete?: (account: Account) => void
    deletePending: boolean
  }
)

export default function AccountSection(props: AccountSectionProps) {
  const { title, header, accounts, emptyMessage, centeredEmpty = false } = props
  const closed = props.variant === 'closed'

  return (
    <section className={`bg-card rounded-xl border border-border shadow-sm${closed ? ' opacity-60' : ''}`}>
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
        {title ? <h2 className="text-sm font-medium text-muted-foreground">{title}</h2> : header}
      </div>
      {accounts.length === 0 && emptyMessage ? (
        <div className={centeredEmpty ? 'px-5 py-8 text-center' : 'px-5 py-4'}>
          <p className="text-sm text-muted-foreground">{emptyMessage}</p>
        </div>
      ) : (
        <div className="divide-y divide-muted">
          {accounts.map((account) => closed ? (
            <AccountCard
              key={account.id}
              account={account}
              variant="closed"
              onReopen={() => props.onReopen(account)}
              reopenPending={props.reopenPending}
            />
          ) : (
            <AccountCard
              key={account.id}
              account={account}
              onEdit={() => props.onEdit(account)}
              onClose={() => props.onClose(account)}
              onDelete={props.onDelete ? () => props.onDelete?.(account) : undefined}
              deletePending={props.deletePending}
            />
          ))}
        </div>
      )}
    </section>
  )
}
