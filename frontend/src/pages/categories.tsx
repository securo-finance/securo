import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi } from '@/lib/api'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Category } from '@/types'
import { Pencil, Trash2, Plus, PiggyBank } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { CategoryIcon } from '@/components/category-icon'
import { IconPicker } from '@/components/icon-picker'
import { useCurrentMonth } from '@/hooks/use-current-month'
import { useAuth } from '@/contexts/auth-context'

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}
function SectionHeader({ title, titleExtra, action }: { title: string; titleExtra?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-3">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {titleExtra}
      </div>
      {action}
    </div>
  )
}

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

export default function CategoriesPage() {
  const { t, i18n } = useTranslation()
  const { user } = useAuth()
  const { data: currentMonthState } = useCurrentMonth()
  const currentMonthDefined = currentMonthState?.is_defined ?? false
  const isSnapshotView = currentMonthState?.is_snapshot_view ?? false
  const editableCategories = !isSnapshotView
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const queryClient = useQueryClient()
  const [catDialogOpen, setCatDialogOpen] = useState(false)
  const [editingCat, setEditingCat] = useState<Category | null>(null)
  const [formIcon, setFormIcon] = useState('circle-help')
  const [formColor, setFormColor] = useState('#6366f1')
  const [formHasBudget, setFormHasBudget] = useState(false)
  const [formBudgetAmount, setFormBudgetAmount] = useState('')

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['categories'] })
  }

  const createCatMutation = useMutation({
    mutationFn: (cat: Partial<Category>) => categoriesApi.create(cat),
    onSuccess: () => { invalidateAll(); setCatDialogOpen(false); toast.success(t('categories.created')) },
  })
  const updateCatMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Category> & { id: string }) => categoriesApi.update(id, data),
    onSuccess: () => { invalidateAll(); setCatDialogOpen(false); setEditingCat(null); toast.success(t('categories.updated')) },
  })
  const deleteCatMutation = useMutation({
    mutationFn: (id: string) => categoriesApi.delete(id),
    onSuccess: () => { invalidateAll(); toast.success(t('categories.deleted')) },
  })

  const openCatDialog = (cat: Category | null) => {
    setEditingCat(cat)
    setFormIcon(cat?.icon ?? 'circle-help')
    setFormColor(cat?.color ?? '#6366f1')
    setFormHasBudget(cat?.has_budget ?? false)
    setFormBudgetAmount(cat?.budget_amount?.toString() ?? '')
    setCatDialogOpen(true)
  }

  const sortedCategories = [...(categoriesList ?? [])].sort((a, b) => a.name.localeCompare(b.name, locale))

  const renderCategoryItem = (cat: Category) => (
    <div key={cat.id} className="border-b border-border last:border-0 hover:bg-muted transition-colors">
      <div className="flex items-start gap-3 px-4 sm:px-5 py-3">
        <CategoryIcon icon={cat.icon} color={cat.color} size="md" />
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-foreground truncate">{cat.name}</span>
            <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              <PiggyBank size={11} />
              {cat.has_budget && cat.budget_amount != null
                ? formatCurrency(Number(cat.budget_amount), userCurrency, locale)
                : t('categories.unbudgeted')}
            </span>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">{t('categories.colorLabel', { color: cat.color })}</div>
        </div>
        <div className="flex items-center gap-1 shrink-0 ml-2">
        <button
          className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
          onClick={() => openCatDialog(cat)}
          disabled={!editableCategories}
          title={t('common.edit')}
        >
          <Pencil size={13} />
        </button>
        {!cat.is_system && (
          <button
            className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
            onClick={() => deleteCatMutation.mutate(cat.id)}
            disabled={deleteCatMutation.isPending || !editableCategories}
            title={t('common.delete')}
          >
            <Trash2 size={13} />
          </button>
        )}
        </div>
      </div>
    </div>
  )

  return (
    <div>
      <PageHeader section={t('categories.title')} title={t('categories.title')} />

      {!currentMonthDefined ? (
        <div className="mb-6 rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground shadow-sm">
          <p className="font-medium text-foreground">{t('categories.currentMonthFlexibleTitle')}</p>
          <p className="mt-1">{t('categories.currentMonthFlexibleHint')}</p>
        </div>
      ) : isSnapshotView ? (
        <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 shadow-sm">
          <p className="font-medium">{t('common.snapshotReadOnlyTitle', { period: currentMonthState?.selected_period_label })}</p>
          <p className="mt-1">{t('common.snapshotReadOnlyHint')}</p>
        </div>
      ) : null}

      <SectionCard>
        <SectionHeader
          title={t('categories.title')}
          titleExtra={
            <p className="text-xs text-muted-foreground">{t('categories.flatDescription')}</p>
          }
          action={
            <Button
              size="sm"
              className="gap-1.5 h-8"
              onClick={() => openCatDialog(null)}
              disabled={!editableCategories}
              title={isSnapshotView ? t('common.snapshotReadOnlyLocked') : undefined}
            >
              <Plus size={13} /> <span className="hidden sm:inline">{t('categories.addCategory')}</span>
            </Button>
          }
        />
        <div>
          {sortedCategories.map(renderCategoryItem)}
          {sortedCategories.length === 0 ? (
            <div className="px-4 sm:px-5 py-6 text-sm text-muted-foreground">{t('categories.empty')}</div>
          ) : null}
        </div>
      </SectionCard>

      {/* Category Dialog */}
      <Dialog open={catDialogOpen} onOpenChange={() => { setCatDialogOpen(false); setEditingCat(null) }}>
        <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{editingCat ? t('categories.editCategory') : t('categories.newCategory')}</DialogTitle>
            <DialogDescription>{t('categories.formDescription')}</DialogDescription>
          </DialogHeader>
          <form
            key={editingCat?.id ?? 'new'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              const budgetAmount = formBudgetAmount.trim() === '' ? null : Number(formBudgetAmount)
              const data = {
                name: formData.get('name') as string,
                icon: formData.get('icon') as string,
                color: formData.get('color') as string,
                has_budget: formHasBudget,
                budget_amount: formHasBudget ? budgetAmount : null,
              }
              if (editingCat) {
                updateCatMutation.mutate({ id: editingCat.id, ...data })
              } else {
                createCatMutation.mutate(data)
              }
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label>{t('groups.name')}</Label>
              <Input name="name" defaultValue={editingCat?.name ?? ''} required />
            </div>
            <div className="space-y-2">
              <Label>{t('groups.color')}</Label>
              <Input name="color" type="color" value={formColor} onChange={(e) => setFormColor(e.target.value)} required className="h-9 px-2 py-1" />
            </div>
            <div className="space-y-2">
              <Label>{t('groups.icon')}</Label>
              <IconPicker value={formIcon} color={formColor} onChange={setFormIcon} />
              <input type="hidden" name="icon" value={formIcon} />
            </div>
            <div className="rounded-xl border border-border bg-muted/40 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                  <Label htmlFor="has-budget" className="text-sm font-semibold text-foreground">
                    {t('categories.budgetToggle')}
                  </Label>
                  <p className="text-xs text-muted-foreground">{t('categories.budgetHint')}</p>
                </div>
                <button
                  id="has-budget"
                  type="button"
                  role="switch"
                  aria-checked={formHasBudget}
                  onClick={() => {
                    setFormHasBudget((current) => {
                      const next = !current
                      if (!next) setFormBudgetAmount('')
                      return next
                    })
                  }}
                  className={`relative inline-flex h-7 w-12 shrink-0 rounded-full border transition-colors ${
                    formHasBudget ? 'border-primary bg-primary' : 'border-border bg-background'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 size-5 rounded-full bg-white shadow-sm transition-transform ${
                      formHasBudget ? 'translate-x-6' : 'translate-x-0.5'
                    }`}
                  />
                  <span className="sr-only">{t('categories.budgetToggle')}</span>
                </button>
              </div>
              {formHasBudget ? (
                <div className="mt-4 space-y-2">
                  <Label htmlFor="budget-amount">{t('budgets.amount')}</Label>
                  <Input
                    id="budget-amount"
                    name="budget_amount"
                    type="number"
                    inputMode="decimal"
                    min="0"
                    step="0.01"
                    value={formBudgetAmount}
                    onChange={(e) => setFormBudgetAmount(e.target.value)}
                    placeholder={t('categories.budgetPlaceholder')}
                    required={formHasBudget}
                    className="h-11"
                  />
                </div>
              ) : (
                <p className="mt-4 text-sm text-muted-foreground">{t('categories.unbudgetedHint')}</p>
              )}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => { setCatDialogOpen(false); setEditingCat(null) }}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={createCatMutation.isPending || updateCatMutation.isPending}>
                {t('common.save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
