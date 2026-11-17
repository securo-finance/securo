import { useTranslation } from 'react-i18next'
import { Columns3 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

import { COLUMN_REGISTRY, type ColumnId, type TransactionsGridState } from './transactions-grid-columns'

interface Props {
  state: TransactionsGridState
}

export function TransactionsColumnPicker({ state }: Props) {
  const { t } = useTranslation()
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" title={t('transactions.columnsTooltip')}>
          <Columns3 size={16} className="me-1.5" />
          {t('transactions.columns')}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-56 p-2">
        <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">{t('transactions.columns')}</div>
        <ul className="max-h-72 overflow-y-auto">
          {COLUMN_REGISTRY.map((col) => {
            const checked = state.isVisible(col.id)
            const disabled = !!col.alwaysOn
            return (
              <li key={col.id}>
                <label
                  className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted ${
                    disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={() => state.toggleColumn(col.id as ColumnId)}
                    className="h-4 w-4 rounded border-border accent-primary"
                  />
                  <span className="flex-1">{t(col.labelKey)}</span>
                </label>
              </li>
            )
          })}
        </ul>
        <div className="mt-1 border-t border-border pt-2">
          <button
            type="button"
            onClick={() => state.resetColumns()}
            className="w-full rounded-md px-2 py-1.5 text-start text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {t('transactions.resetColumns')}
          </button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
