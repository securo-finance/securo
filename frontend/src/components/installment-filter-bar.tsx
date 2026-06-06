import { useTranslation } from 'react-i18next'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { Account } from '@/types'

interface Props {
  statusFilter: string
  onStatusChange: (status: string) => void
  sortBy: string
  onSortChange: (sort: string) => void
  accountId: string
  onAccountChange: (accountId: string) => void
  accounts: Account[]
  activeCount: number
  finishedCount: number
  showOccluded: boolean
  onShowOccludedChange: (show: boolean) => void
}

export function InstallmentFilterBar({
  statusFilter,
  onStatusChange,
  sortBy,
  onSortChange,
  accountId,
  onAccountChange,
  accounts,
  activeCount,
  finishedCount,
  showOccluded,
  onShowOccludedChange,
}: Props) {
  const { t } = useTranslation()

  return (
    <div className="space-y-3">
      {/* Tab navigation */}
      <div className="flex items-center gap-1 border-b border-border">
        <button
          onClick={() => onStatusChange('ACTIVE')}
          className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 ${
            statusFilter === 'ACTIVE'
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          Em andamento ({activeCount})
        </button>
        <button
          onClick={() => onStatusChange('FINISHED')}
          className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 ${
            statusFilter === 'FINISHED'
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          Finalizadas ({finishedCount})
        </button>
      </div>

      {/* Controls row */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Select value={sortBy} onValueChange={onSortChange}>
            <SelectTrigger className="w-[140px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="date">{t('installments.sort.date', 'Data')}</SelectItem>
              <SelectItem value="amount">{t('installments.sort.amount', 'Valor')}</SelectItem>
              <SelectItem value="remaining">{t('installments.sort.remaining', 'Restante')}</SelectItem>
            </SelectContent>
          </Select>

          <Select value={accountId} onValueChange={onAccountChange}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder={t('installments.filter.allAccounts', 'Todas as contas')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('installments.filter.allAccounts', 'Todas as contas')}</SelectItem>
              {accounts
                .filter((a) => a.type === 'credit_card')
                .map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.display_name || a.name}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
        </div>

        <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer ml-auto">
          <input
            type="checkbox"
            checked={showOccluded}
            onChange={(e) => onShowOccludedChange(e.target.checked)}
            className="w-4 h-4 rounded border-border accent-primary"
          />
          {t('installments.filter.showOccluded', 'Mostrar ocultos')}
        </label>
      </div>
    </div>
  )
}
