import type { ReactNode } from 'react'
import { ArrowLeftRight, CalendarDays, Copy, Download, List, MoreHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { MonthStepper } from '@/components/month-stepper'
import { TransactionsViewSwitcher, type TransactionsViewSwitcherProps } from '@/components/transactions-view-switcher'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

type MonthStepperConfig = {
  value: string
  onChange: (value: string) => void
  locale: string
  prevLabel: string
  nextLabel: string
}

type ViewSwitcherConfig = TransactionsViewSwitcherProps

export type TransactionsPageActionsProps = {
  month: MonthStepperConfig
  view: ViewSwitcherConfig
  columnPicker: ReactNode
  exportLabel: string
  exporting: boolean
  onExport: () => void
  onAdd?: () => void
  onDuplicate?: () => void
  onTransfer?: () => void
  testId?: string
}

function HeaderMonthStepper({ month }: { month: MonthStepperConfig }) {
  return (
    <div className="min-w-0 flex-1 sm:flex-none">
      <MonthStepper {...month} />
    </div>
  )
}

function DesktopSecondaryActions(props: TransactionsPageActionsProps) {
  const { t } = useTranslation()
  return (
    <div className="hidden sm:contents">
      <TransactionsViewSwitcher {...props.view} />
      {props.columnPicker}
      <Button variant="outline" disabled={props.exporting} onClick={props.onExport}>
        <Download size={16} className="mr-1.5" />{props.exportLabel}
      </Button>
      {props.onDuplicate && <Button variant="outline" onClick={props.onDuplicate}><Copy size={16} className="mr-1.5" />{t('transactions.duplicate')}</Button>}
      {props.onTransfer && <Button variant="outline" onClick={props.onTransfer}><ArrowLeftRight size={16} className="mr-1.5" />{t('transactions.transfer')}</Button>}
    </div>
  )
}

function PrimaryAddAction({ onAdd }: { onAdd?: () => void }) {
  const { t } = useTranslation()
  if (!onAdd) return null
  return (
    <Button className="shrink-0 px-3" onClick={onAdd}>
      + <span className="sm:hidden">{t('common.add')}</span>
      <span className="hidden sm:inline">{t('transactions.addManual')}</span>
    </Button>
  )
}

function MobileSecondaryMenu(props: TransactionsPageActionsProps) {
  const { t } = useTranslation()
  const changeView = (value: string) => {
    if (value === 'list' || value === 'calendar') props.view.onChange(value)
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="!size-9 sm:hidden" aria-label={t('common.more')}>
          <MoreHorizontal size={18} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={props.view.value} onValueChange={changeView}>
          <DropdownMenuRadioItem value="list"><List />{props.view.listLabel}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="calendar"><CalendarDays />{props.view.calendarLabel}</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={props.exporting} onClick={props.onExport}><Download size={16} className="mr-2" />{props.exportLabel}</DropdownMenuItem>
        {props.onDuplicate && <DropdownMenuItem onClick={props.onDuplicate}><Copy size={16} className="mr-2" />{t('transactions.duplicate')}</DropdownMenuItem>}
        {props.onTransfer && <DropdownMenuItem onClick={props.onTransfer}><ArrowLeftRight size={16} className="mr-2" />{t('transactions.transfer')}</DropdownMenuItem>}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/**
 * Keeps month, primary transaction action, and mobile overflow on one compact row.
 * @example `<TransactionsPageActions month={month} view={view} columnPicker={picker} exportLabel="Export" exporting={false} onExport={exportCsv} />`
 */
export function TransactionsPageActions(props: TransactionsPageActionsProps) {
  // Secondary actions stay labelled on desktop and collapse only where width is scarce.
  return (
    <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto sm:flex-wrap sm:justify-end" data-testid={props.testId}>
      <HeaderMonthStepper month={props.month} />
      <DesktopSecondaryActions {...props} />
      <PrimaryAddAction onAdd={props.onAdd} />
      <MobileSecondaryMenu {...props} />
    </div>
  )
}
