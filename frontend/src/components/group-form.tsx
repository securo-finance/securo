import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { currencies as currenciesApi } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { GroupKind } from '@/types'

export const KIND_OPTIONS: { value: GroupKind; tKey: string }[] = [
  { value: 'social', tKey: 'splitGroups.kind.social' },
  { value: 'cost_center', tKey: 'splitGroups.kind.cost_center' },
  { value: 'project', tKey: 'splitGroups.kind.project' },
  { value: 'client', tKey: 'splitGroups.kind.client' },
  { value: 'other', tKey: 'splitGroups.kind.other' },
]

interface GroupFormProps {
  name: string
  onChangeName: (value: string) => void
  kind: GroupKind
  onChangeKind: (value: GroupKind) => void
  defaultCurrency: string
  onChangeDefaultCurrency: (value: string) => void
  notes: string
  onChangeNotes: (value: string) => void
}

export function GroupForm({
  name,
  onChangeName,
  kind,
  onChangeKind,
  defaultCurrency,
  onChangeDefaultCurrency,
  notes,
  onChangeNotes,
}: GroupFormProps) {
  const { t } = useTranslation()

  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currenciesApi.list,
    staleTime: Infinity,
  })

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>{t('splitGroups.name')}</Label>
        <Input
          placeholder={t('splitGroups.name')}
          value={name}
          onChange={(e) => onChangeName(e.target.value)}
        />
      </div>
      <div className="space-y-2">
        <Label>{t('splitGroups.kindLabel')}</Label>
        <select
          className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background h-9 focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
          value={kind}
          onChange={(e) => onChangeKind(e.target.value as GroupKind)}
        >
          {KIND_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {t(opt.tKey)}
            </option>
          ))}
        </select>
        <p className="text-xs text-muted-foreground">{t('splitGroups.kindHint')}</p>
      </div>
      <div className="space-y-2">
        <Label>{t('splitGroups.defaultCurrency')}</Label>
        <select
          className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background h-9 focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
          value={defaultCurrency}
          onChange={(e) => onChangeDefaultCurrency(e.target.value)}
        >
          {(supportedCurrencies ?? [
            { code: defaultCurrency, symbol: defaultCurrency, name: defaultCurrency, flag: '' },
          ]).map((c) => (
            <option key={c.code} value={c.code}>
              {c.flag} {c.name}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-2">
        <Label>{t('splitGroups.notes')}</Label>
        <textarea
          className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background resize-none h-20"
          value={notes}
          onChange={(e) => onChangeNotes(e.target.value)}
        />
      </div>
    </div>
  )
}
