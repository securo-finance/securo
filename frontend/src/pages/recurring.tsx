import React, { useMemo, useState } from 'react'
import { getAccountName, sortAccountsByDisplayName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi, categoryGroups as categoryGroupsApi, recurring as recurringApi, accounts as accountsApi, currencies as currenciesApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { localDateString } from '@/lib/date-utils'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Category, CategoryGroup, RecurringTransaction } from '@/types'
import { Pencil, Trash2, Plus, RefreshCw, Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/page-header'
import { CategorySelect } from '@/components/category-select'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { formatCurrency } from '@/lib/format'
import { countryName } from '@/lib/country-name'

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
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<RecurringTransaction | null>(null)
  const [deletingRecurring, setDeletingRecurring] = useState<RecurringTransaction | null>(null)

  const { data: recurringList } = useQuery({
    queryKey: ['recurring'],
    queryFn: recurringApi.list,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: allCategoriesList } = useQuery({
    queryKey: ['categories', 'management'],
    queryFn: categoriesApi.listIncludingHidden,
    enabled: Boolean(editing?.category_id),
  })

  const { data: categoryGroupsList } = useQuery({
    queryKey: ['categoryGroups'],
    queryFn: categoryGroupsApi.list,
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
      setDeletingRecurring(null)
      toast.success(t('recurring.deleted'))
    },
    onError: (err: unknown) => {
      toast.error(extractApiError(err, t('common.error')))
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
    const map: Record<string, string> = {
      monthly: t('recurring.monthly'),
      quarterly: t('recurring.quarterly'),
      semiannual: t('recurring.semiannual'),
      weekly: t('recurring.weekly'),
      biweekly: t('recurring.biweekly'),
      yearly: t('recurring.yearly'),
    }
    return map[f] ?? f
  }

  return (
    <>
      <SectionCard>
        <SectionHeader
          title={t('recurring.title')}
          action={
            canWrite ? (
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
            ) : undefined
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
                {canWrite && <th className={`${TH} pr-4 sm:pr-5 text-right w-24`}>{t('recurring.actions')}</th>}
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
                    {new Date((rt.next_due_date ?? rt.next_occurrence) + 'T00:00:00').toLocaleDateString(dateLocale)}
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
                  {canWrite && (
                    <td className="py-3 pr-4 sm:pr-5">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                          onClick={() => { setEditing(rt); setDialogOpen(true) }}
                          aria-label={t('common.edit')}
                          title={t('common.edit')}
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                          onClick={() => setDeletingRecurring(rt)}
                          disabled={deleteMutation.isPending}
                          aria-label={t('common.delete')}
                          title={t('common.delete')}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('recurring.empty')}</p>
        )}
      </SectionCard>

      <Dialog open={dialogOpen} onOpenChange={() => { setDialogOpen(false); setEditing(null) }}>
        {/* Bounded to the viewport as a flex column, so the form body scrolls
            and the footer stays reachable however many rules are shown. */}
        <DialogContent className="flex flex-col max-h-[calc(100dvh-2rem)] overflow-hidden sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editing ? t('recurring.edit') : t('recurring.add')}</DialogTitle>
          </DialogHeader>
          <RecurringForm
            key={editing?.id ?? 'new'}
            recurring={editing}
            categories={categoriesList ?? []}
            categoryGroups={categoryGroupsList ?? []}
            currentCategory={allCategoriesList?.find(
              (category) => category.id === editing?.category_id
            )}
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

      <DeleteConfirmationDialog
        open={!!deletingRecurring}
        title={t('recurring.confirmDeleteTitle')}
        description={t('recurring.confirmDeleteDescription', { description: deletingRecurring?.description })}
        isPending={deleteMutation.isPending}
        onClose={() => setDeletingRecurring(null)}
        onConfirm={() => deletingRecurring && deleteMutation.mutate(deletingRecurring.id)}
      />
    </>
  )
}

function RecurringForm({
  recurring,
  categories,
  categoryGroups,
  currentCategory,
  accounts,
  onSave,
  onCancel,
  loading,
}: {
  recurring: RecurringTransaction | null
  categories: Category[]
  categoryGroups: CategoryGroup[]
  currentCategory?: Category
  accounts: { id: string; name: string; display_name?: string | null }[]
  onSave: (data: Partial<RecurringTransaction>) => void
  onCancel: () => void
  loading: boolean
}) {
  const { t, i18n } = useTranslation()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const sortedAccounts = useMemo(() => sortAccountsByDisplayName(accounts), [accounts])
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
  const [weekendAdjustment, setWeekendAdjustment] = useState<RecurringTransaction['weekend_adjustment']>(
    recurring?.weekend_adjustment ?? 'none'
  )
  const [dayOfMonth, setDayOfMonth] = useState(recurring?.day_of_month?.toString() ?? '')
  const [dayRule, setDayRule] = useState<'calendar' | 'business'>(recurring?.business_day ? 'business' : 'calendar')
  const [businessDay, setBusinessDay] = useState(recurring?.business_day?.toString() ?? '5')
  const [countSaturdays, setCountSaturdays] = useState(recurring?.count_saturdays ?? false)
  // holiday_calendar is "BR" or "BR-SP": the country, then an optional subdivision.
  const [holidayCountry, setHolidayCountry] = useState(recurring?.holiday_calendar?.split('-')[0] ?? '')
  const [holidayRegion, setHolidayRegion] = useState(
    recurring?.holiday_calendar?.split('-').slice(1).join('-') ?? ''
  )
  // Country names follow the UI language, not the currency-derived display
  // locale: a Hindi UI with BRL amounts should still read "ब्राज़ील".
  const displayLocale = i18n.language
  const { data: holidayCalendars } = useQuery({
    queryKey: ['holiday-calendars'],
    queryFn: recurringApi.holidayCalendars,
    staleTime: Infinity,
  })
  const holidayCountries = useMemo(
    () =>
      (holidayCalendars ?? [])
        .map((c) => ({ ...c, name: countryName(c.country, displayLocale) }))
        .sort((a, b) => a.name.localeCompare(b.name, displayLocale)),
    [holidayCalendars, displayLocale]
  )
  const holidayRegions = useMemo(
    () =>
      [...(holidayCountries.find((c) => c.country === holidayCountry)?.subdivisions ?? [])].sort((a, b) =>
        a.name.localeCompare(b.name, displayLocale)
      ),
    [holidayCountries, holidayCountry, displayLocale]
  )
  const holidayCalendar = holidayCountry ? (holidayRegion ? `${holidayCountry}-${holidayRegion}` : holidayCountry) : null
  const [graceDays, setGraceDays] = useState(recurring?.grace_days?.toString() ?? '0')
  const [amountTolerance, setAmountTolerance] = useState(recurring?.amount_tolerance_percent?.toString() ?? '0')
  const [matchKeywords, setMatchKeywords] = useState(recurring?.match_keywords ?? '')
  const monthBased = frequency === 'monthly' || frequency === 'quarterly' || frequency === 'semiannual'
  const useBusinessDay = monthBased && dayRule === 'business'
  const [startDate, setStartDate] = useState(recurring?.start_date ?? localDateString())
  const [endDate, setEndDate] = useState(recurring?.end_date ?? '')
  const [categoryId, setCategoryId] = useState(recurring?.category_id ?? '')
  const [accountId, setAccountId] = useState(recurring?.account_id ?? '')
  const [isActive, setIsActive] = useState(recurring?.is_active ?? true)
  const [autoGenerate, setAutoGenerate] = useState(recurring?.auto_generate ?? true)

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
          weekend_adjustment: weekendAdjustment,
          day_of_month: dayOfMonth ? parseInt(dayOfMonth) : null,
          business_day: useBusinessDay && businessDay ? parseInt(businessDay) : null,
          count_saturdays: useBusinessDay && countSaturdays,
          // The selector only shows for month-based frequencies; a calendar
          // left over from before a frequency change must not be saved unseen.
          holiday_calendar: monthBased ? holidayCalendar : null,
          grace_days: graceDays ? parseInt(graceDays) : 0,
          amount_tolerance_percent: amountTolerance ? parseFloat(amountTolerance) : 0,
          match_keywords: matchKeywords.trim() || null,
          start_date: startDate,
          end_date: endDate || null,
          category_id: categoryId || null,
          account_id: accountId || null,
          is_active: isActive,
          auto_generate: autoGenerate,
        } as Partial<RecurringTransaction>)
      }}
      className="flex flex-col flex-1 min-h-0"
    >
      <div className="space-y-4 overflow-y-auto overscroll-contain flex-1 min-h-0 -mx-1 px-1 pb-2">
      <div className="space-y-2">
        <Label>{t('recurring.description')}</Label>
        <Input value={description} onChange={(e) => setDescription(e.target.value)} required />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
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
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>{t('recurring.frequency')}</Label>
          <select className={selectClass} value={frequency} onChange={(e) => setFrequency(e.target.value as RecurringTransaction['frequency'])}>
            <option value="monthly">{t('recurring.monthly')}</option>
            <option value="quarterly">{t('recurring.quarterly')}</option>
            <option value="semiannual">{t('recurring.semiannual')}</option>
            <option value="weekly">{t('recurring.weekly')}</option>
            <option value="biweekly">{t('recurring.biweekly')}</option>
            <option value="yearly">{t('recurring.yearly')}</option>
          </select>
        </div>
        {monthBased && (
          <div className="space-y-2">
            <Label>{t('recurring.dayRule')}</Label>
            <select className={selectClass} value={dayRule} onChange={(e) => setDayRule(e.target.value as 'calendar' | 'business')}>
              <option value="calendar">{t('recurring.dayRuleCalendar')}</option>
              <option value="business">{t('recurring.dayRuleBusiness')}</option>
            </select>
          </div>
        )}
        {monthBased && (useBusinessDay ? (
          <div className="space-y-2">
            <Label>{t('recurring.businessDay')}</Label>
            <Input type="number" min="1" max="23" value={businessDay} onChange={(e) => setBusinessDay(e.target.value)} required />
          </div>
        ) : (
          <div className="space-y-2">
            <Label>{t('recurring.dayOfMonth')}</Label>
            <Input type="number" min="1" max="31" value={dayOfMonth} onChange={(e) => setDayOfMonth(e.target.value)} />
          </div>
        ))}
        <div className="space-y-2">
          <Label>{t('recurring.weekendAdjustment')}</Label>
          <select
            className={selectClass}
            value={weekendAdjustment}
            onChange={(e) => setWeekendAdjustment(e.target.value as RecurringTransaction['weekend_adjustment'])}
          >
            <option value="none">{t('recurring.weekendAdjustmentNone')}</option>
            <option value="previous_friday">{t('recurring.weekendAdjustmentPreviousFriday')}</option>
            <option value="next_monday">{t('recurring.weekendAdjustmentNextMonday')}</option>
          </select>
        </div>
        {monthBased && (
          <div className="space-y-2">
            <Label>{t('recurring.holidayCalendar')}</Label>
            <select
              className={selectClass}
              value={holidayCountry}
              onChange={(e) => { setHolidayCountry(e.target.value); setHolidayRegion('') }}
            >
              <option value="">{t('recurring.holidayCalendarNone')}</option>
              {/* Keep a saved calendar selectable while the list loads. */}
              {holidayCountry && !holidayCountries.some((c) => c.country === holidayCountry) && (
                <option value={holidayCountry}>{countryName(holidayCountry, displayLocale)}</option>
              )}
              {holidayCountries.map((c) => (
                <option key={c.country} value={c.country}>{c.name}</option>
              ))}
            </select>
          </div>
        )}
        {monthBased && holidayCountry && (holidayRegions.length > 0 || holidayRegion) && (
          <div className="space-y-2">
            <Label>{t('recurring.holidayRegion')}</Label>
            <select className={selectClass} value={holidayRegion} onChange={(e) => setHolidayRegion(e.target.value)}>
              <option value="">{t('recurring.holidayRegionNational')}</option>
              {holidayRegion && !holidayRegions.some((r) => r.code === holidayRegion) && (
                <option value={holidayRegion}>{holidayRegion}</option>
              )}
              {holidayRegions.map((r) => (
                <option key={r.code} value={r.code}>
                  {r.name === r.code ? r.code : `${r.name} (${r.code})`}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      {monthBased && holidayCountry === 'BR' && (
        <p className="text-xs text-muted-foreground -mt-2">{t('recurring.holidayCalendarBRHelp')}</p>
      )}
      {useBusinessDay && (
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={countSaturdays}
            onChange={(e) => setCountSaturdays(e.target.checked)}
            className="h-4 w-4 mt-0.5 rounded border-border"
          />
          <span className="text-sm text-foreground">
            {t('recurring.countSaturdays')}
            <span className="block text-xs text-muted-foreground">{t('recurring.countSaturdaysHelp')}</span>
          </span>
        </label>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>{t('recurring.startDate')}</Label>
          <DatePickerInput value={startDate} onChange={setStartDate} className="w-full justify-start" />
        </div>
        <div className="space-y-2">
          <Label>{t('recurring.endDate')}</Label>
          <DatePickerInput value={endDate} onChange={setEndDate} placeholder={t('recurring.endDate')} className="w-full justify-start" />
        </div>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>{t('recurring.category')}</Label>
          <CategorySelect
            value={categoryId}
            onChange={setCategoryId}
            categories={categories}
            groups={categoryGroups}
            currentCategory={currentCategory}
            allowNone={true}
            className={selectClass}
          />
        </div>
        <div className="space-y-2">
          <Label>{t('recurring.account')}</Label>
          <select
            className={selectClass}
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            required
          >
            {!accountId && <option value="" disabled hidden>{t('transactions.createCounterpartSelectAccount')}</option>}
            {sortedAccounts.map((acc) => (
              <option key={acc.id} value={acc.id}>{getAccountName(acc)}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="space-y-3 rounded-lg border border-border p-3">
        <div>
          <p className="text-sm font-medium text-foreground">{t('recurring.matching')}</p>
          <p className="text-xs text-muted-foreground">{t('recurring.matchingHelp')}</p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>{t('recurring.graceDays')}</Label>
            <Input type="number" min="0" max="15" value={graceDays} onChange={(e) => setGraceDays(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>{t('recurring.amountTolerance')}</Label>
            <Input type="number" min="0" max="50" step="0.5" value={amountTolerance} onChange={(e) => setAmountTolerance(e.target.value)} />
          </div>
        </div>
        <div className="space-y-2">
          <Label>{t('recurring.matchKeywords')}</Label>
          <Input
            value={matchKeywords}
            onChange={(e) => setMatchKeywords(e.target.value)}
            placeholder={t('recurring.matchKeywordsPlaceholder')}
            maxLength={500}
          />
          <p className="text-xs text-muted-foreground">{t('recurring.matchKeywordsHelp')}</p>
        </div>
      </div>
      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={autoGenerate}
          onChange={(e) => setAutoGenerate(e.target.checked)}
          className="h-4 w-4 mt-0.5 rounded border-border"
        />
        <span className="text-sm text-foreground">
          {t('recurring.autoGenerate')}
          <span className="block text-xs text-muted-foreground">{t('recurring.autoGenerateHelp')}</span>
        </span>
      </label>
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
      </div>
      <DialogFooter className="pt-4 border-t border-border">
        <Button type="button" variant="outline" onClick={onCancel}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={loading}>
          {loading ? t('common.loading') : t('common.save')}
        </Button>
      </DialogFooter>
    </form>
  )
}
