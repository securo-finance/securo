import React, { useState } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi, recurring as recurringApi, accounts as accountsApi, currencies as currenciesApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Category, RecurringTransaction } from '@/types'
import { Pencil, Trash2, Plus, RefreshCw, Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/page-header'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

const TH = 'text-xs font-medium text-muted-foreground py-3'

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}

function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-semibold text-foreground">{title}</p>
      {action}
    </div>
  )
}

export default function RecurringPage() {
  const { t } = useTranslation()

  return (
    <div>
      <PageHeader section={t('recurring.title')} title={t('recurring.title')} />
      <RecurringTab />
    </div>
  )
}

function RecurringTab() {
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<RecurringTransaction | null>(null)

  const { data: recurringList } = useQuery({
    queryKey: ['recurring'],
    queryFn: recurringApi.list,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const createMutation = useMutation({
    mutationFn: (data: Partial<RecurringTransaction>) => recurringApi.create(data),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      setDialogOpen(false)
      toast.success(t('recurring.created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<RecurringTransaction> & { id: string }) =>
      recurringApi.update(id, data),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('recurring.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => recurringApi.delete(id),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      toast.success(t('recurring.deleted'))
    },
  })

  const generateMutation = useMutation({
    mutationFn: () => recurringApi.generate(),
    onSuccess: (data) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      toast.success(t('recurring.generated', { count: data.generated }))
    },
    onError: () => toast.error(t('common.error')),
  })

  const frequencyLabel = (f: string) => {
    const map: Record<string, string> = { monthly: t('recurring.monthly'), weekly: t('recurring.weekly'), yearly: t('recurring.yearly') }
    return map[f] ?? f
  }

  return (
    <>
      <SectionCard>
        <SectionHeader
          title={t('recurring.title')}
          action={
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 h-8"
                onClick={() => generateMutation.mutate()}
                disabled={generateMutation.isPending}
              >
                <RefreshCw size={12} />
                <span className="hidden sm:inline">{t('recurring.generatePending')}</span>
              </Button>
              <Button size="sm" className="gap-1.5 h-8" onClick={() => { setEditing(null); setDialogOpen(true) }}>
                <Plus size={13} /> <span className="hidden sm:inline">{t('recurring.add')}</span>
              </Button>
            </div>
          }
        />
        {recurringList && recurringList.length > 0 ? (
          <table className="w-full">
            <thead>
              <tr className="border-b border-border">
                <th className={`${TH} pl-4 sm:pl-5 text-left`}>{t('recurring.description')}</th>
                <th className={`${TH} text-left w-36`}>{t('recurring.amount')}</th>
                <th className={`${TH} text-left w-28 hidden md:table-cell`}>{t('recurring.frequency')}</th>
                <th className={`${TH} text-left w-32 hidden md:table-cell`}>{t('recurring.nextOccurrence')}</th>
                <th className={`${TH} text-left w-24 hidden sm:table-cell`}>{t('recurring.status')}</th>
                <th className={`${TH} pr-4 sm:pr-5 text-right w-24`}>{t('recurring.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {recurringList.map((rt) => (
                <tr key={rt.id} className="border-b border-border last:border-0 hover:bg-muted transition-colors">
                  <td className="py-3 pl-4 sm:pl-5 text-sm font-medium text-foreground">{rt.description}</td>
                  <td className={`py-3 text-xs sm:text-sm font-bold tabular-nums ${rt.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
                    {mask(`${rt.type === 'credit' ? '+' : '−'}${formatCurrency(rt.amount, rt.currency, locale)}`)}
                    {rt.currency !== userCurrency && rt.amount_primary != null && (
                      <div className="flex items-center gap-1 text-[11px] font-normal text-muted-foreground">
                        <span>{mask(formatCurrency(rt.amount_primary, userCurrency, locale))}</span>
                        <span title={t('recurring.fxEstimate', { rate: rt.fx_rate_used?.toFixed(4) ?? '–' })}>
                          <Info size={11} className="inline opacity-60" />
                        </span>
                      </div>
                    )}
                  </td>
                  <td className="py-3 hidden md:table-cell">
                    <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full font-medium">
                      {frequencyLabel(rt.frequency)}
                    </span>
                  </td>
                  <td className="py-3 text-xs text-muted-foreground tabular-nums hidden md:table-cell">
                    {new Date(rt.next_occurrence + 'T00:00:00').toLocaleDateString(locale)}
                  </td>
                  <td className="py-3 hidden sm:table-cell">
                    <span className={cn(
                      'text-[11px] font-semibold px-2 py-0.5 rounded-full border',
                      rt.is_active
                        ? 'bg-emerald-50 text-emerald-600 border-emerald-100'
                        : 'bg-muted text-muted-foreground border-border'
                    )}>
                      {rt.is_active ? t('recurring.active') : t('recurring.inactive')}
                    </span>
                  </td>
                  <td className="py-3 pr-4 sm:pr-5">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                        onClick={() => { setEditing(rt); setDialogOpen(true) }}
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                        onClick={() => deleteMutation.mutate(rt.id)}
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('recurring.empty')}</p>
        )}
      </SectionCard>

      <Dialog open={dialogOpen} onOpenChange={() => { setDialogOpen(false); setEditing(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t('recurring.edit') : t('recurring.add')}</DialogTitle>
          </DialogHeader>
          <RecurringForm
            key={editing?.id ?? 'new'}
            recurring={editing}
            categories={categoriesList ?? []}
            accounts={accountsList ?? []}
            onSave={(data) => {
              if (editing) {
                updateMutation.mutate({ id: editing.id, ...data })
              } else {
                createMutation.mutate(data)
              }
            }}
            onCancel={() => { setDialogOpen(false); setEditing(null) }}
            loading={createMutation.isPending || updateMutation.isPending}
          />
        </DialogContent>
      </Dialog>
    </>
  )
}

function RecurringForm({
  recurring,
  categories,
  accounts,
  onSave,
  onCancel,
  loading,
}: {
  recurring: RecurringTransaction | null
  categories: Category[]
  accounts: { id: string; name: string }[]
  onSave: (data: Partial<RecurringTransaction>) => void
  onCancel: () => void
  loading: boolean
}) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currenciesApi.list,
    staleTime: Infinity,
  })
  const [description, setDescription] = useState(recurring?.description ?? '')
  const [amount, setAmount] = useState(recurring?.amount?.toString() ?? '')
  const [currency, setCurrency] = useState(recurring?.currency ?? userCurrency)
  const [type, setType] = useState<'debit' | 'credit'>(recurring?.type ?? 'debit')
  const [frequency, setFrequency] = useState(recurring?.frequency ?? 'monthly')
  const [dayOfMonth, setDayOfMonth] = useState(recurring?.day_of_month?.toString() ?? '')
  const [startDate, setStartDate] = useState(recurring?.start_date ?? new Date().toISOString().split('T')[0])
  const [endDate, setEndDate] = useState(recurring?.end_date ?? '')
  const [categoryId, setCategoryId] = useState(recurring?.category_id ?? '')
  const [accountId, setAccountId] = useState(recurring?.account_id ?? accounts[0]?.id ?? '')
  const [isActive, setIsActive] = useState(recurring?.is_active ?? true)

  const selectClass = 'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        onSave({
          description,
          amount: parseFloat(amount),
          currency,
          type,
          frequency,
          day_of_month: dayOfMonth ? parseInt(dayOfMonth) : null,
          start_date: startDate,
          end_date: endDate || null,
          category_id: categoryId || null,
          account_id: accountId || null,
          is_active: isActive,
        } as Partial<RecurringTransaction>)
      }}
      className="space-y-4"
    >
      <div className="space-y-2">
        <Label>{t('recurring.description')}</Label>
        <Input value={description} onChange={(e) => setDescription(e.target.value)} required />
      </div>
      <div className="grid grid-cols-3 gap-4">
        <div className="space-y-2">
          <Label>{t('recurring.amount')}</Label>
          <Input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required />
        </div>
        <div className="space-y-2">
          <Label>{t('recurring.currency')}</Label>
          <select className={selectClass} value={currency} onChange={(e) => setCurrency(e.target.value)}>
            {(supportedCurrencies ?? [{ code: userCurrency, symbol: userCurrency, name: userCurrency, flag: '' }]).map((c) => (
              <option key={c.code} value={c.code}>{c.flag} {c.name}</option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label>{t('recurring.type')}</Label>
          <select className={selectClass} value={type} onChange={(e) => setType(e.target.value as 'debit' | 'credit')}>
            <option value="debit">{t('recurring.expense')}</option>
            <option value="credit">{t('recurring.income')}</option>
          </select>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>{t('recurring.frequency')}</Label>
          <select className={selectClass} value={frequency} onChange={(e) => setFrequency(e.target.value as 'monthly' | 'weekly' | 'yearly')}>
            <option value="monthly">{t('recurring.monthly')}</option>
            <option value="weekly">{t('recurring.weekly')}</option>
            <option value="yearly">{t('recurring.yearly')}</option>
          </select>
        </div>
        {frequency === 'monthly' && (
          <div className="space-y-2">
            <Label>{t('recurring.dayOfMonth')}</Label>
            <Input type="number" min="1" max="31" value={dayOfMonth} onChange={(e) => setDayOfMonth(e.target.value)} />
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>{t('recurring.startDate')}</Label>
          <DatePickerInput value={startDate} onChange={setStartDate} className="w-full justify-start" />
        </div>
        <div className="space-y-2">
          <Label>{t('recurring.endDate')}</Label>
          <DatePickerInput value={endDate} onChange={setEndDate} placeholder={t('recurring.endDate')} className="w-full justify-start" />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>{t('recurring.category')}</Label>
          <select className={selectClass} value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            <option value="">{t('transactions.noCategory')}</option>
            {categories.map((cat) => (
              <option key={cat.id} value={cat.id}>{cat.name}</option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label>{t('recurring.account')}</Label>
          <select
            className={selectClass}
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            required
          >
            {!accountId && <option value="" disabled>{t('recurring.noAccount')}</option>}
            {accounts.map((acc) => (
              <option key={acc.id} value={acc.id}>{getAccountName(acc)}</option>
            ))}
          </select>
        </div>
      </div>
      {recurring && (
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm text-foreground">{t('recurring.active')}</span>
        </label>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={loading}>
          {loading ? t('common.loading') : t('common.save')}
        </Button>
      </DialogFooter>
    </form>
  )
}
