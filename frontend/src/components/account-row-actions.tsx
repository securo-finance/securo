import type { MouseEvent } from 'react'
import type { TFunction } from 'i18next'
import { Archive, MoreHorizontal, Pencil, Trash2, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

type AccountActionTone = 'default' | 'warning' | 'destructive'

type AccountAction = {
  label: string
  icon: LucideIcon
  run: () => void
  tone: AccountActionTone
}

export type AccountRowActionsProps = {
  accountName: string
  onEdit: () => void
  onClose: () => void
  onDelete?: () => void
  deletePending: boolean
}

function buildAccountActions(t: TFunction, props: AccountRowActionsProps) {
  const actions: AccountAction[] = [
    { label: t('common.edit'), icon: Pencil, run: props.onEdit, tone: 'default' },
    { label: t('accounts.close'), icon: Archive, run: props.onClose, tone: 'warning' },
  ]
  if (!props.onDelete) return actions
  actions.push({
    label: t('common.delete'),
    icon: Trash2,
    run: props.onDelete,
    tone: 'destructive',
  })
  return actions
}

function accountActionClass(tone: AccountActionTone) {
  if (tone === 'destructive')
    return 'rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-rose-50 hover:text-rose-500'
  if (tone === 'warning')
    return 'rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-amber-50 hover:text-amber-600'
  return 'rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground'
}

function DesktopActionButton({ action, deletePending }: {
  action: AccountAction
  deletePending: boolean
}) {
  const runAction = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
    action.run()
  }
  return (
    <button
      type="button"
      className={accountActionClass(action.tone)}
      onClick={runAction}
      disabled={action.tone === 'destructive' && deletePending}
      title={action.label}
      aria-label={action.label}
    >
      <action.icon size={13} />
    </button>
  )
}

function DesktopAccountActions({ actions, deletePending }: {
  actions: AccountAction[]
  deletePending: boolean
}) {
  return (
    <div className="ml-2 hidden items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 sm:flex">
      {actions.map((action) => (
        <DesktopActionButton key={action.label} action={action} deletePending={deletePending} />
      ))}
    </div>
  )
}

function MobileActionItem({ action, deletePending }: {
  action: AccountAction
  deletePending: boolean
}) {
  return (
    <DropdownMenuItem
      variant={action.tone === 'destructive' ? 'destructive' : 'default'}
      disabled={action.tone === 'destructive' && deletePending}
      onSelect={action.run}
    >
      <action.icon size={14} />
      {action.label}
    </DropdownMenuItem>
  )
}

function MobileAccountActions({ accountName, actions, deletePending }: {
  accountName: string
  actions: AccountAction[]
  deletePending: boolean
}) {
  const { t } = useTranslation()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className="ml-1 rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground sm:hidden" aria-label={`${t('common.more')}: ${accountName}`}>
          <MoreHorizontal size={16} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {actions.map((action) => (
          <MobileActionItem key={action.label} action={action} deletePending={deletePending} />
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/**
 * Keeps account edit/close/delete actions available on pointer and touch layouts.
 * @example `<AccountRowActions accountName="Cash" onEdit={edit} onClose={close} deletePending={false} />`
 */
export function AccountRowActions(props: AccountRowActionsProps) {
  const { t } = useTranslation()
  const actions = buildAccountActions(t, props)
  return (
    <>
      <DesktopAccountActions actions={actions} deletePending={props.deletePending} />
      <MobileAccountActions {...props} actions={actions} />
    </>
  )
}
