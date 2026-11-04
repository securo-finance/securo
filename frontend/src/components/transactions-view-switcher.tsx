import { CalendarDays, List } from 'lucide-react'
import { Button } from '@/components/ui/button'

export type TransactionsViewMode = 'list' | 'calendar'

export type TransactionsViewSwitcherProps = {
  value: TransactionsViewMode
  onChange: (value: TransactionsViewMode) => void
  listLabel: string
  calendarLabel: string
}

/**
 * Segmented List/Calendar switch, shared by the transactions page header and
 * the dashboard's transactions section so both read as the same control.
 * @example `<TransactionsViewSwitcher value={view} onChange={setView} listLabel="List" calendarLabel="Calendar" />`
 */
export function TransactionsViewSwitcher({ value, onChange, listLabel, calendarLabel }: TransactionsViewSwitcherProps) {
  return (
    <div className="inline-flex rounded-lg border border-border bg-card p-0.5">
      <Button
        variant={value === 'list' ? 'secondary' : 'ghost'}
        size="sm"
        className="h-8 gap-1.5 px-2.5"
        aria-pressed={value === 'list'}
        onClick={() => onChange('list')}
      >
        <List size={14} />
        {listLabel}
      </Button>
      <Button
        variant={value === 'calendar' ? 'secondary' : 'ghost'}
        size="sm"
        className="h-8 gap-1.5 px-2.5"
        aria-pressed={value === 'calendar'}
        onClick={() => onChange('calendar')}
      >
        <CalendarDays size={14} />
        {calendarLabel}
      </Button>
    </div>
  )
}
