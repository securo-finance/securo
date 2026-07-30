import { Layers, MoreHorizontal, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export type AccountPageActionsProps = {
  canWrite: boolean
  onAddAccount: () => void
  onConnectBank: () => void
  onOpenCollections: () => void
  testId?: string
}

function DesktopSecondaryActions(props: AccountPageActionsProps) {
  const { t } = useTranslation()
  return (
    <>
      <Button variant="outline" className="hidden gap-1.5 sm:inline-flex" onClick={props.onOpenCollections}>
        <Layers size={16} />
        {t('collections.title')}
      </Button>
      {props.canWrite && (
        <Button variant="outline" className="hidden gap-1.5 sm:inline-flex" onClick={props.onConnectBank}>
          <Plus size={16} />
          {t('accounts.connectBank')}
        </Button>
      )}
    </>
  )
}

function MobileSecondaryMenu(props: AccountPageActionsProps) {
  const { t } = useTranslation()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="size-9 shrink-0 sm:hidden" aria-label={t('common.more')}>
          <MoreHorizontal size={18} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={props.onOpenCollections}>
          <Layers size={16} />{t('collections.title')}
        </DropdownMenuItem>
        {props.canWrite && <DropdownMenuItem onSelect={props.onConnectBank}><Plus size={16} />{t('accounts.connectBank')}</DropdownMenuItem>}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/**
 * Preserves the primary Add Account action while moving secondary mobile actions into a menu.
 * @example `<AccountPageActions canWrite onAddAccount={openForm} onConnectBank={connect} onOpenCollections={openCollections} />`
 */
export function AccountPageActions(props: AccountPageActionsProps) {
  const { t } = useTranslation()
  return (
    <div className="flex w-full items-center gap-2 sm:w-auto" data-testid={props.testId}>
      <DesktopSecondaryActions {...props} />
      {props.canWrite && <Button onClick={props.onAddAccount} className="flex-1 gap-1.5 sm:flex-none"><Plus size={16} />{t('accounts.addManual')}</Button>}
      <MobileSecondaryMenu {...props} />
    </div>
  )
}
