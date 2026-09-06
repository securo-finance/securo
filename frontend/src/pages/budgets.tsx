import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { monthLabel } from '@/lib/month-utils'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi, categoryGroups as groupsApi, budgets as budgetsApi } from '@/lib/api'
import { toast } from 'sonner'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Budget } from '@/types'
import { Pencil, Trash2, Plus, Repeat, CalendarIcon, AlertCircle, Copy } from 'lucide-react'
import { format } from 'date-fns'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { MonthPicker } from '@/components/ui/monthpicker'
import { PageHeader } from '@/components/page-header'
import { CategoryIcon } from '@/components/category-icon'
import { TransactionDrillDown, type DrillDownFilter } from '@/components/transaction-drill-down'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { formatCurrency } from '@/lib/format'

function currentMonth() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function getPreviousMonth(monthStr: string) {
  const [y, m] = monthStr.split('-').map(Number)
  const d = new Date(y, m - 2, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function monthLastDay(m: string) {
  const [y, mon] = m.split('-').map(Number)
  return new Date(y, mon, 0).getDate()
}

const TH = 'text-[12px] font-mono font-medium tracking-wider text-muted-foreground uppercase py-4 px-6 border-b border-[#27272A]'

export default function BudgetsPage() {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const [selectedMonth, setSelectedMonth] = useState(currentMonth)
  const [monthCalOpen, setMonthCalOpen] = useState(false)
  const dateFnsLocale = resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language)
  const monthParam = `${selectedMonth}-01`
  
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Budget | null>(null)
  const [preselectCategory, setPreselectCategory] = useState<string | null>(null)
  const [drillDown, setDrillDown] = useState<DrillDownFilter | null>(null)

  // Recurring delete modal state
  const [deleteTarget, setDeleteTarget] = useState<{ categoryId: string; categoryName: string; isRecurring: boolean; budgetId?: string } | null>(null)
  const [deleteScope, setDeleteScope] = useState<'future' | 'month' | 'all'>('future')
  const [isDeleting, setIsDeleting] = useState(false)

  // Copy budgets modal state
  const [copyDialogOpen, setCopyDialogOpen] = useState(false)
  const [sourceMonth, setSourceMonth] = useState(() => getPreviousMonth(selectedMonth))
  const [sourceMonthCalOpen, setSourceMonthCalOpen] = useState(false)
  const [overwriteExisting, setOverwriteExisting] = useState(true)

  const monthStart = `${selectedMonth}-01`
  const monthEnd = `${selectedMonth}-${String(monthLastDay(selectedMonth)).padStart(2, '0')}`

  const { data: budgetsList } = useQuery({
    queryKey: ['budgets', selectedMonth],
    queryFn: () => budgetsApi.list(monthParam),
  })

  const { data: comparisonList } = useQuery({
    queryKey: ['budgets-comparison', selectedMonth],
    queryFn: () => budgetsApi.comparison(monthParam),
  })

  const { data: sourceComparison, isLoading: sourceLoading } = useQuery({
    queryKey: ['budgets-comparison', sourceMonth],
    queryFn: () => budgetsApi.comparison(`${sourceMonth}-01`),
    enabled: copyDialogOpen,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: groupsList } = useQuery({
    queryKey: ['category-groups'],
    queryFn: groupsApi.list,
  })

  const createMutation = useMutation({
    mutationFn: (data: { category_id: string; amount: number; month: string; is_recurring?: boolean }) =>
      budgetsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      queryClient.invalidateQueries({ queryKey: ['budgets-comparison'] })
      setDialogOpen(false)
      toast.success(t('budgets.created', 'Budget created successfully'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, amount }: { id: string; amount: number }) =>
      budgetsApi.update(id, { amount }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      queryClient.invalidateQueries({ queryKey: ['budgets-comparison'] })
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('budgets.updated', 'Budget updated successfully'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => budgetsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      queryClient.invalidateQueries({ queryKey: ['budgets-comparison'] })
      toast.success(t('budgets.deleted', 'Budget deleted'))
    },
  })

  const copyMutation = useMutation({
    mutationFn: (data: { source_month: string; target_month: string; overwrite_existing: boolean }) =>
      budgetsApi.copy(data),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      queryClient.invalidateQueries({ queryKey: ['budgets-comparison'] })
      setCopyDialogOpen(false)
      if (res.copied_count === 0) {
        toast.info(t('budgets.noSourceBudgets', 'No budgets found in the selected source month.'))
      } else {
        toast.success(
          t('budgets.copySuccess', {
            count: res.copied_count,
            defaultValue: `${res.copied_count} budgets copied successfully`,
          })
        )
      }
    },
    onError: () => toast.error(t('common.error')),
  })

  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const monthTitle = monthLabel(selectedMonth, uiLocale).replace(/^\w/, c => c.toUpperCase())

  // Dashboard calculations
  const { budgeted, unbudgeted, kpis } = useMemo(() => {
    if (!comparisonList) return { budgeted: [], unbudgeted: [], kpis: null }
    
    const budgeted = comparisonList.filter((b) => b.budget_amount !== null && b.budget_amount > 0)
    const unbudgeted = comparisonList.filter((b) => (b.budget_amount === null || b.budget_amount === 0) && b.actual_amount > 0)
    
    let totalPlanned = 0
    let totalRealized = 0
    
    let within = 0
    let exceeded = 0

    budgeted.forEach((b) => {
      totalPlanned += Number(b.budget_amount ?? 0)
      totalRealized += Number(b.actual_amount)
      
      const pct = b.percentage_used ?? 0
      if (pct <= 100) within++
      else exceeded++
    })
    
    let totalUnbudgeted = 0
    unbudgeted.forEach((b) => {
      totalUnbudgeted += Number(b.actual_amount ?? 0)
    })
    
    const available = totalPlanned - totalRealized
    const executionRate = totalPlanned > 0 ? (totalRealized / totalPlanned) * 100 : 0
    
    return {
      budgeted,
      unbudgeted,
      kpis: {
        totalPlanned,
        totalRealized,
        totalUnbudgeted,
        available,
        executionRate,
        within,
        exceeded
      }
    }
  }, [comparisonList])

  const sourceBudgetedCategories = useMemo(() => {
    if (!sourceComparison) return []
    return sourceComparison.filter((b) => b.budget_amount !== null && b.budget_amount > 0)
  }, [sourceComparison])

  const sourceTotalPlanned = useMemo(() => {
    return sourceBudgetedCategories.reduce((acc, b) => acc + Number(b.budget_amount ?? 0), 0)
  }, [sourceBudgetedCategories])

  const openCopyDialog = (customSourceMonth?: string) => {
    setSourceMonth(customSourceMonth ?? getPreviousMonth(selectedMonth))
    setOverwriteExisting(true)
    setCopyDialogOpen(true)
  }

  const openNewBudgetDialog = (categoryId?: string) => {
    setEditing(null)
    setPreselectCategory(categoryId ?? null)
    setDialogOpen(true)
  }
  
  const handleEditBudget = (categoryId: string) => {
    const budgetObj = budgetsList?.find(b => b.category_id === categoryId)
    if (budgetObj) {
      setEditing(budgetObj)
      setDialogOpen(true)
    }
  }

  const handleDeleteBudget = (categoryId: string, categoryName: string, isRecurring: boolean) => {
    const budgetObj = budgetsList?.find(b => b.category_id === categoryId)
    if (!isRecurring) {
      if (budgetObj) {
        deleteMutation.mutate(budgetObj.id)
      }
      return
    }

    setDeleteTarget({
      categoryId,
      categoryName,
      isRecurring: true,
      budgetId: budgetObj?.id,
    })
    setDeleteScope('future')
  }

  const handleConfirmDelete = async () => {
    if (!deleteTarget) return
    const { categoryId, budgetId } = deleteTarget
    const budgetObj = budgetsList?.find(b => b.category_id === categoryId)
    setIsDeleting(true)

    try {
      if (deleteScope === 'all') {
        if (budgetId) {
          await budgetsApi.delete(budgetId)
          toast.success(t('budgets.deleteSuccessAll', 'Recurring budget deleted from all months.'))
        }
      } else if (deleteScope === 'month') {
        await budgetsApi.create({
          category_id: categoryId,
          amount: 0,
          month: monthParam,
          is_recurring: false,
        })
        toast.success(t('budgets.deleteSuccessMonth', 'Budget removed for this month only.'))
      } else if (deleteScope === 'future') {
        if (budgetObj && budgetObj.month >= monthParam) {
          await budgetsApi.delete(budgetObj.id)
        } else {
          await budgetsApi.create({
            category_id: categoryId,
            amount: 0,
            month: monthParam,
            is_recurring: true,
          })
        }
        toast.success(t('budgets.deleteSuccessFuture', 'Recurring budget stopped from this month.'))
      }

      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      queryClient.invalidateQueries({ queryKey: ['budgets-comparison'] })
      setDeleteTarget(null)
    } catch {
      toast.error(t('common.error'))
    } finally {
      setIsDeleting(false)
    }
  }

  const handleCategoryClick = (categoryId: string, categoryName: string) => {
    setDrillDown({
      title: `${categoryName} — ${monthTitle}`,
      category_id: categoryId,
      type: 'debit',
      from: monthStart,
      to: monthEnd,
    })
  }

  return (
    <div>
      <PageHeader
        section={t('budgets.title', 'Budgets')}
        title={monthTitle}
        action={
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              className="h-8 px-2.5 text-xs gap-1"
              onClick={() => {
                const prev = getPreviousMonth(selectedMonth)
                setSelectedMonth(prev)
              }}
            >
              ←
            </Button>
            <Popover open={monthCalOpen} onOpenChange={setMonthCalOpen}>
              <PopoverTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 px-3 text-xs gap-1.5 font-medium"
                >
                  <CalendarIcon className="size-3.5 text-muted-foreground" />
                  {monthTitle}
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-auto p-0">
                <MonthPicker
                  locale={dateFnsLocale}
                  selectedMonth={new Date(`${selectedMonth}-01T00:00:00`)}
                  onMonthSelect={(date) => {
                    if (!date) return
                    setSelectedMonth(format(date, 'yyyy-MM'))
                    setMonthCalOpen(false)
                  }}
                />
              </PopoverContent>
            </Popover>
            <Button
              variant="outline"
              size="sm"
              className="h-8 px-2.5 text-xs gap-1"
              onClick={() => {
                const [y, m] = selectedMonth.split('-').map(Number)
                const d = new Date(y, m, 1)
                setSelectedMonth(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
              }}
            >
              →
            </Button>
          </div>
        }
      />

      {/* Bento-style Monthly Budget Card */}
      {kpis && (
        <div className="bg-[#18181B] border border-[#27272A] rounded-xl p-6 shadow-sm mb-8">
          <h2 className="text-[18px] font-semibold text-foreground mb-6">
            {t('budgets.monthlyBudget', 'Monthly Budget')}
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
            <div>
              <div className="text-sm text-muted-foreground mb-2">{t('budgets.totalPlanned', 'Total Planned')}</div>
              <div className="text-[24px] font-semibold font-mono tabular-nums text-foreground">
                {mask(formatCurrency(kpis.totalPlanned, userCurrency, locale))}
              </div>
            </div>
            <div>
              <div className="text-sm text-muted-foreground mb-2">{t('budgets.totalRealized', 'Total Realized')}</div>
              <div className="text-[24px] font-semibold font-mono tabular-nums text-foreground">
                {mask(formatCurrency(kpis.totalRealized, userCurrency, locale))}
              </div>
            </div>
            <div>
              <div className="text-sm text-muted-foreground mb-2">{t('budgets.available', 'Available')}</div>
              <div className={`text-[24px] font-semibold font-mono tabular-nums ${kpis.available < 0 ? 'text-[#ffb4ab]' : 'text-[#4edea3]'}`}>
                {kpis.available > 0 ? '+' : ''}{mask(formatCurrency(kpis.available, userCurrency, locale))}
              </div>
            </div>
            <div>
              <div className="text-sm text-muted-foreground mb-2">{t('budgets.executionRate', 'Execution')}</div>
              <div className="flex items-center gap-3">
                <span className="text-[24px] font-semibold font-mono tabular-nums text-foreground">
                  {kpis.executionRate.toFixed(1).replace('.', ',')}%
                </span>
                <div className="flex-1 max-w-[100px]">
                  <div className="h-2 w-full bg-[#333539] rounded-full overflow-hidden">
                    <div
                      className={`h-full transition-all duration-300 ${
                        kpis.executionRate > 100 ? 'bg-[#ffb4ab]' : 'bg-[#4edea3]'
                      }`}
                      style={{ width: `${Math.min(kpis.executionRate, 100)}%` }}
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-6 pt-6 border-t border-[#27272A] flex flex-wrap gap-6 text-sm">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-[#4edea3]" />
              <span className="text-muted-foreground font-medium">
                {kpis.within} {t('budgets.statusWithin', 'within')}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-[#ffb4ab]" />
              <span className="text-muted-foreground font-medium">
                {kpis.exceeded} {t('budgets.statusExceeded', 'exceeded')}
              </span>
            </div>
            {unbudgeted.length > 0 && (
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-[#ffb3ad]" />
                <span className="text-muted-foreground font-medium">
                  {unbudgeted.length} {t('budgets.statusUnbudgeted', 'sem orçamento')} ({mask(formatCurrency(kpis.totalUnbudgeted, userCurrency, locale))})
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Main Budget Details Card */}
      <div className="bg-[#18181B] border border-[#27272A] rounded-xl overflow-hidden mb-8 shadow-sm">
        <div className="p-6 border-b border-[#27272A] flex justify-between items-center bg-[#18181B] flex-wrap gap-4">
          <h2 className="text-[18px] font-semibold text-foreground">
            {t('budgets.categoryBreakdown', 'Category Details')}
          </h2>
          {canWrite && (
            <div className="flex items-center gap-3">
              <Button
                type="button"
                variant="outline"
                className="border-[#27272A] bg-[#18181B] hover:bg-[#27272A]/50 text-foreground flex items-center gap-2 text-sm font-medium"
                onClick={() => openCopyDialog()}
              >
                <Copy size={15} />
                {t('budgets.copyPreviousMonth', 'Copy Previous Month')}
              </Button>
              <button
                className="bg-primary text-on-primary hover:bg-primary-container px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 cursor-pointer"
                onClick={() => openNewBudgetDialog()}
              >
                <Plus size={18} /> {t('budgets.add', 'New Budget')}
              </button>
            </div>
          )}
        </div>
        
        {budgeted && budgeted.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse min-w-[700px]">
              <thead>
                <tr className="bg-[#1a1c20]/50 border-b border-[#27272A]">
                  <th className={`${TH} text-left`}>{t('budgets.category')}</th>
                  <th className={`${TH} text-right w-36`}>{t('budgets.planned', 'Planned')}</th>
                  <th className={`${TH} text-right w-36`}>{t('budgets.realized', 'Realized')}</th>
                  <th className={`${TH} text-right w-36`}>{t('budgets.difference', 'Difference')}</th>
                  <th className={`${TH} text-left w-56`}>{t('budgets.usage', 'Usage')}</th>
                  {canWrite && (
                    <th className={`${TH} text-right w-20 pr-6`}>
                      <span className="sr-only">{t('common.actions', 'Actions')}</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-[#27272A]/50 text-sm font-mono text-foreground">
                {budgeted.map((b) => {
                  const diff = Number(b.budget_amount ?? 0) - Number(b.actual_amount)
                  const pct = b.percentage_used ?? 0
                  
                  return (
                    <tr
                      key={b.category_id}
                      className="hover:bg-[#333539]/20 transition-colors group cursor-pointer"
                      onClick={() => handleCategoryClick(b.category_id, b.category_name)}
                    >
                      <td className="py-4 px-6">
                        <div className="flex items-center gap-3">
                          <CategoryIcon icon={b.category_icon} color={b.category_color} size="sm" />
                          <span className="font-sans text-sm font-medium text-foreground">{b.category_name}</span>
                          {b.is_recurring && (
                            <span title={t('budgets.recurringLabel')} className="text-muted-foreground ml-1">
                              <Repeat size={12} />
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-4 px-6 text-right tabular-nums text-muted-foreground">
                        {mask(formatCurrency(b.budget_amount ?? 0, userCurrency, locale))}
                      </td>
                      <td className="py-4 px-6 text-right tabular-nums text-foreground">
                        {mask(formatCurrency(b.actual_amount, userCurrency, locale))}
                      </td>
                      <td className={`py-4 px-6 text-right tabular-nums font-semibold ${diff < 0 ? 'text-[#ffb4ab]' : 'text-[#4edea3]'}`}>
                        {diff < 0 ? '-' : ''}{mask(formatCurrency(Math.abs(diff), userCurrency, locale))}
                      </td>
                      <td className="py-4 px-6">
                        <div className="flex items-center gap-3">
                          <div className="flex-1">
                            <div className="h-2 w-full bg-[#333539] rounded-full overflow-hidden">
                              <div
                                className={`h-full transition-all duration-300 ${
                                  pct > 100 ? 'bg-[#ffb4ab]' : 'bg-[#4edea3]'
                                }`}
                                style={{ width: `${Math.min(pct, 100)}%` }}
                              />
                            </div>
                          </div>
                          <span className={`w-12 text-right text-xs font-bold tabular-nums ${
                            pct > 100 ? 'text-[#ffb4ab]' : 'text-[#4edea3]'
                          }`}>
                            {pct.toFixed(0)}%
                          </span>
                        </div>
                      </td>
                      {canWrite && (
                        <td className="py-4 px-6 text-right pr-6" onClick={(e) => e.stopPropagation()}>
                          <div className="flex items-center justify-end gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors cursor-pointer"
                              onClick={() => handleEditBudget(b.category_id)}
                              aria-label={t('common.edit')}
                              title={t('common.edit')}
                            >
                              <Pencil size={14} />
                            </button>
                            <button
                              className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors cursor-pointer"
                              onClick={() => handleDeleteBudget(b.category_id, b.category_name, b.is_recurring)}
                              disabled={deleteMutation.isPending || isDeleting}
                              aria-label={t('common.delete')}
                              title={t('common.delete')}
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="py-12 px-6 flex flex-col items-center justify-center text-center">
            <div className="w-12 h-12 rounded-full bg-[#27272A]/50 border border-[#27272A] flex items-center justify-center text-muted-foreground mb-4">
              <Copy size={20} className="opacity-70" />
            </div>
            <h3 className="text-base font-semibold text-foreground mb-1.5">
              {t('budgets.emptyStateTitle', 'You haven\'t set a budget for this month yet.')}
            </h3>
            <p className="text-xs text-muted-foreground max-w-md mb-6 leading-relaxed">
              {t('budgets.emptyStateSubtitle', 'Create a budget manually or copy definitions from the previous month to get started.')}
            </p>
            {canWrite && (
              <div className="flex flex-wrap items-center justify-center gap-3">
                <Button
                  type="button"
                  variant="outline"
                  className="border-[#27272A] bg-[#18181B] hover:bg-[#27272A]/50 text-foreground flex items-center gap-2 text-sm"
                  onClick={() => openCopyDialog()}
                >
                  <Copy size={15} />
                  {t('budgets.copyPreviousMonth', 'Copy Previous Month')}
                </Button>
                <Button
                  type="button"
                  className="bg-primary text-on-primary hover:bg-primary-container flex items-center gap-2 text-sm"
                  onClick={() => openNewBudgetDialog()}
                >
                  <Plus size={16} />
                  {t('budgets.createBudgetAction', 'Create Budget')}
                </Button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Expenses without Budget Section */}
      {unbudgeted && unbudgeted.length > 0 && (
        <div className="bg-[#18181B] border border-[#27272A] rounded-xl overflow-hidden mb-8 shadow-sm">
          <div className="p-6 border-b border-[#27272A] bg-rose-500/5">
            <div className="flex items-center gap-2 text-[#ffb3ad] mb-1">
              <AlertCircle size={18} />
              <h2 className="text-[18px] font-semibold">
                {t('budgets.unbudgetedExpenses', 'Expenses without Budget')}
              </h2>
            </div>
            <p className="text-xs text-muted-foreground ml-6">
              {t('budgets.unbudgetedSubtitle', 'Categories with recorded expenses but no defined limit.')}
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse min-w-[600px]">
              <tbody className="divide-y divide-[#27272A]/50 text-sm font-mono text-foreground">
                {unbudgeted.map((b) => (
                  <tr
                    key={b.category_id}
                    className="hover:bg-[#333539]/20 transition-colors cursor-pointer"
                    onClick={() => handleCategoryClick(b.category_id, b.category_name)}
                  >
                    <td className="py-4 px-6 w-[250px]">
                      <div className="flex items-center gap-3">
                        <CategoryIcon icon={b.category_icon} color={b.category_color} size="sm" />
                        <span className="font-sans text-sm font-medium text-foreground">{b.category_name}</span>
                      </div>
                    </td>
                    <td className="py-4 px-6 text-right tabular-nums text-muted-foreground">
                      {mask(formatCurrency(0, userCurrency, locale))}
                    </td>
                    <td className="py-4 px-6 text-right tabular-nums text-foreground font-semibold">
                      {mask(formatCurrency(b.actual_amount, userCurrency, locale))}
                    </td>
                    <td className="py-4 px-6 text-right tabular-nums font-semibold text-[#ffb4ab]">
                      -{mask(formatCurrency(b.actual_amount, userCurrency, locale))}
                    </td>
                    {canWrite && (
                      <td className="py-4 px-6 text-right pr-6" onClick={(e) => e.stopPropagation()}>
                        <button
                          className="text-primary hover:text-primary-container text-xs font-semibold uppercase tracking-wider bg-transparent border-0 cursor-pointer"
                          onClick={() => openNewBudgetDialog(b.category_id)}
                        >
                          {t('budgets.createBudgetAction', 'Create Budget')}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Drill-down Transaction Drawer */}
      <TransactionDrillDown
        filter={drillDown}
        onClose={() => setDrillDown(null)}
      />

      {/* Intelligent Recurring Delete Dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}>
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle className="text-lg font-semibold text-foreground">
              {t('budgets.deleteRecurringTitle', 'Delete Recurring Budget')}
            </DialogTitle>
            <DialogDescription className="text-sm text-muted-foreground pt-1">
              {t('budgets.deleteRecurringDesc', 'This budget repeats every month. How would you like to apply the deletion?')}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 py-3">
            {/* Option 1: Future */}
            <div
              className={`p-4 rounded-xl border transition-all cursor-pointer flex items-start gap-3.5 ${
                deleteScope === 'future'
                  ? 'border-primary bg-primary/5 ring-1 ring-primary'
                  : 'border-[#27272A] bg-[#18181B]/60 hover:bg-[#27272A]/40'
              }`}
              onClick={() => setDeleteScope('future')}
            >
              <div className="mt-0.5">
                <input
                  type="radio"
                  name="delete_scope"
                  checked={deleteScope === 'future'}
                  onChange={() => setDeleteScope('future')}
                  className="text-primary focus:ring-primary h-4 w-4"
                />
              </div>
              <div className="flex-1">
                <div className="text-sm font-semibold text-foreground">
                  {t('budgets.deleteScopeFuture', 'This and following months')}
                </div>
                <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                  {t('budgets.deleteScopeFutureDesc', 'Preserves past history and stops the budget starting from this month.')}
                </div>
              </div>
            </div>

            {/* Option 2: Month only */}
            <div
              className={`p-4 rounded-xl border transition-all cursor-pointer flex items-start gap-3.5 ${
                deleteScope === 'month'
                  ? 'border-primary bg-primary/5 ring-1 ring-primary'
                  : 'border-[#27272A] bg-[#18181B]/60 hover:bg-[#27272A]/40'
              }`}
              onClick={() => setDeleteScope('month')}
            >
              <div className="mt-0.5">
                <input
                  type="radio"
                  name="delete_scope"
                  checked={deleteScope === 'month'}
                  onChange={() => setDeleteScope('month')}
                  className="text-primary focus:ring-primary h-4 w-4"
                />
              </div>
              <div className="flex-1">
                <div className="text-sm font-semibold text-foreground">
                  {t('budgets.deleteScopeMonth', 'Only this month')}
                </div>
                <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                  {t('budgets.deleteScopeMonthDesc', 'Zeros the budget only in this month. Resumes normally next month.')}
                </div>
              </div>
            </div>

            {/* Option 3: All months */}
            <div
              className={`p-4 rounded-xl border transition-all cursor-pointer flex items-start gap-3.5 ${
                deleteScope === 'all'
                  ? 'border-rose-500/60 bg-rose-500/5 ring-1 ring-rose-500/60'
                  : 'border-[#27272A] bg-[#18181B]/60 hover:bg-[#27272A]/40'
              }`}
              onClick={() => setDeleteScope('all')}
            >
              <div className="mt-0.5">
                <input
                  type="radio"
                  name="delete_scope"
                  checked={deleteScope === 'all'}
                  onChange={() => setDeleteScope('all')}
                  className="text-rose-500 focus:ring-rose-500 h-4 w-4"
                />
              </div>
              <div className="flex-1">
                <div className="text-sm font-semibold text-foreground">
                  {t('budgets.deleteScopeAll', 'All months')}
                </div>
                <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                  {t('budgets.deleteScopeAllDesc', 'Completely removes the budget from all past and future months.')}
                </div>
              </div>
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setDeleteTarget(null)}
              disabled={isDeleting}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              type="button"
              variant={deleteScope === 'all' ? 'destructive' : 'default'}
              onClick={handleConfirmDelete}
              disabled={isDeleting}
            >
              {isDeleting ? t('common.loading', 'Loading...') : t('common.confirm', 'Confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Creation/Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={() => { setDialogOpen(false); setEditing(null); setPreselectCategory(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t('budgets.edit') : t('budgets.add')}</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              const fd = new FormData(e.currentTarget)
              const amount = parseFloat(fd.get('amount') as string)
              const is_recurring = fd.get('is_recurring') === 'on'
              if (editing) {
                updateMutation.mutate({ id: editing.id, amount })
              } else {
                const category_id = fd.get('category_id') as string
                createMutation.mutate({ category_id, amount, month: monthParam, is_recurring })
              }
            }}
            className="space-y-4"
          >
            {editing ? (
              <div>
                <Label className="text-xs text-muted-foreground">{t('budgets.category')}</Label>
                <div className="mt-1 flex items-center gap-2">
                  {(() => {
                    const cat = categoriesList?.find((c) => c.id === editing.category_id)
                    if (!cat) return <span className="text-sm font-medium">{editing.category_id}</span>
                    return (
                      <>
                        <CategoryIcon icon={cat.icon} color={cat.color} size="sm" />
                        <span className="text-sm font-medium">{cat.name}</span>
                      </>
                    )
                  })()}
                </div>
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label>{t('budgets.category')}</Label>
                <select
                  name="category_id"
                  defaultValue={preselectCategory ?? ''}
                  className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                  required
                >
                  <option value="" disabled>
                    {t('budgets.selectCategory')}
                  </option>
                  {groupsList && groupsList.length > 0
                    ? groupsList.map((g) => {
                        const cats = categoriesList?.filter((c) => c.group_id === g.id && !c.is_hidden) || []
                        if (cats.length === 0) return null
                        return (
                          <optgroup key={g.id} label={g.name}>
                            {cats.map((c) => (
                              <option key={c.id} value={c.id}>
                                {c.name}
                              </option>
                            ))}
                          </optgroup>
                        )
                      })
                    : categoriesList
                        ?.filter((c) => !c.is_hidden)
                        .map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                          </option>
                        ))}
                </select>
              </div>
            )}
            <div className="space-y-1.5">
              <Label>{t('budgets.amount')}</Label>
              <Input
                name="amount"
                type="number"
                step="0.01"
                min="0.01"
                defaultValue={editing?.amount ?? ''}
                placeholder="0.00"
                required
                autoFocus
              />
            </div>
            {!editing && (
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="is_recurring"
                  name="is_recurring"
                  className="rounded border-border"
                />
                <Label htmlFor="is_recurring" className="text-sm font-normal cursor-pointer">
                  {t('budgets.repeatEveryMonth')}
                </Label>
              </div>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => { setDialogOpen(false); setEditing(null); setPreselectCategory(null) }}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={createMutation.isPending || updateMutation.isPending}>
                {editing ? t('common.save') : t('common.create')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Copy Budgets Dialog */}
      <Dialog open={copyDialogOpen} onOpenChange={setCopyDialogOpen}>
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle className="text-lg font-semibold text-foreground flex items-center gap-2">
              <Copy size={18} className="text-primary" />
              {t('budgets.copyTitle', 'Copy Budgets')}
            </DialogTitle>
            <DialogDescription className="text-sm text-muted-foreground pt-1">
              {t('budgets.copyDesc', { month: monthTitle, defaultValue: `Copy budget definitions into ${monthTitle}.` })}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-3">
            {/* Source Month Picker */}
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t('budgets.sourceMonth', 'Source Month')}
              </Label>
              <div className="flex items-center gap-2">
                <Popover open={sourceMonthCalOpen} onOpenChange={setSourceMonthCalOpen}>
                  <PopoverTrigger asChild>
                    <button
                      type="button"
                      className="w-full inline-flex items-center justify-between border border-border rounded-lg px-3.5 py-2 text-sm bg-card text-foreground hover:bg-muted/50 transition-all cursor-pointer"
                    >
                      <div className="flex items-center gap-2">
                        <CalendarIcon className="size-4 text-muted-foreground" />
                        <span className="font-medium">
                          {monthLabel(sourceMonth, uiLocale).replace(/^\w/, c => c.toUpperCase())}
                        </span>
                      </div>
                      <span className="text-xs text-muted-foreground font-mono">{sourceMonth}</span>
                    </button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-auto p-0">
                    <MonthPicker
                      locale={dateFnsLocale}
                      selectedMonth={new Date(`${sourceMonth}-01T00:00:00`)}
                      onMonthSelect={(date) => {
                        if (!date) return
                        setSourceMonth(format(date, 'yyyy-MM'))
                        setSourceMonthCalOpen(false)
                      }}
                    />
                  </PopoverContent>
                </Popover>
              </div>
            </div>

            {/* Source Month Summary Card */}
            <div className="p-3.5 rounded-xl border border-[#27272A] bg-[#18181B]/80 text-sm">
              {sourceLoading ? (
                <div className="text-xs text-muted-foreground py-2 text-center">
                  {t('common.loading', 'Loading...')}
                </div>
              ) : sourceBudgetedCategories.length > 0 ? (
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <div className="text-xs text-muted-foreground">
                      {t('budgets.sourceSummaryLabel', 'Available to copy:')}
                    </div>
                    <div className="text-sm font-semibold text-foreground mt-0.5">
                      {t('budgets.copySummary', {
                        count: sourceBudgetedCategories.length,
                        total: mask(formatCurrency(sourceTotalPlanned, userCurrency, locale)),
                        defaultValue: `${sourceBudgetedCategories.length} categories (${mask(formatCurrency(sourceTotalPlanned, userCurrency, locale))})`,
                      })}
                    </div>
                  </div>
                  <div className="text-right font-mono text-sm font-semibold text-[#4edea3] tabular-nums">
                    {mask(formatCurrency(sourceTotalPlanned, userCurrency, locale))}
                  </div>
                </div>
              ) : (
                <div className="text-xs text-[#ffb4ab] flex items-center gap-2">
                  <AlertCircle size={14} />
                  <span>{t('budgets.noSourceBudgets', 'No budgets found in the selected source month.')}</span>
                </div>
              )}
            </div>

            {/* Overwrite / Merge Options */}
            <div className="space-y-2">
              <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t('budgets.conflictResolution', 'Existing Budgets')}
              </Label>
              
              <div
                className={`p-3.5 rounded-xl border transition-all cursor-pointer flex items-start gap-3 ${
                  overwriteExisting
                    ? 'border-primary bg-primary/5 ring-1 ring-primary'
                    : 'border-[#27272A] bg-[#18181B]/60 hover:bg-[#27272A]/40'
                }`}
                onClick={() => setOverwriteExisting(true)}
              >
                <div className="mt-0.5">
                  <input
                    type="radio"
                    name="overwrite_option"
                    checked={overwriteExisting}
                    onChange={() => setOverwriteExisting(true)}
                    className="text-primary focus:ring-primary h-4 w-4"
                  />
                </div>
                <div className="flex-1">
                  <div className="text-sm font-medium text-foreground">
                    {t('budgets.overwriteExisting', 'Replace existing budgets')}
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                    {t('budgets.overwriteExistingDesc', 'Updates categories that already have a budget in this month.')}
                  </div>
                </div>
              </div>

              <div
                className={`p-3.5 rounded-xl border transition-all cursor-pointer flex items-start gap-3 ${
                  !overwriteExisting
                    ? 'border-primary bg-primary/5 ring-1 ring-primary'
                    : 'border-[#27272A] bg-[#18181B]/60 hover:bg-[#27272A]/40'
                }`}
                onClick={() => setOverwriteExisting(false)}
              >
                <div className="mt-0.5">
                  <input
                    type="radio"
                    name="overwrite_option"
                    checked={!overwriteExisting}
                    onChange={() => setOverwriteExisting(false)}
                    className="text-primary focus:ring-primary h-4 w-4"
                  />
                </div>
                <div className="flex-1">
                  <div className="text-sm font-medium text-foreground">
                    {t('budgets.onlyMissing', 'Copy only missing categories')}
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                    {t('budgets.onlyMissingDesc', 'Preserves budgets already configured and only fills missing categories.')}
                  </div>
                </div>
              </div>
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setCopyDialogOpen(false)}
              disabled={copyMutation.isPending}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              type="button"
              onClick={() => {
                copyMutation.mutate({
                  source_month: `${sourceMonth}-01`,
                  target_month: monthParam,
                  overwrite_existing: overwriteExisting,
                })
              }}
              disabled={copyMutation.isPending || sourceLoading || sourceBudgetedCategories.length === 0}
            >
              {copyMutation.isPending ? t('common.loading', 'Loading...') : t('budgets.confirmCopy', 'Confirm & Copy')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
