import React from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { categories as categoriesApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { ArrowRight } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { CategoryIcon } from '@/components/category-icon'
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

export default function BudgetsPage() {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const budgetedCategories = (categoriesList ?? [])
    .filter((category) => category.has_budget && category.budget_amount != null)
    .sort((a, b) => a.name.localeCompare(b.name, locale))

  const getCategoryDisplay = (categoryId: string) => {
    const category = categoriesList?.find((item) => item.id === categoryId)
    if (!category) return <span>{categoryId}</span>
    return (
      <span className="flex items-center gap-2">
        <CategoryIcon icon={category.icon} color={category.color} size="sm" />
        <span>{category.name}</span>
      </span>
    )
  }

  return (
    <div>
      <PageHeader
        section={t('budgets.title')}
        title={t('budgets.currentLimitsTitle')}
      />

      <SectionCard>
        <SectionHeader
          title={t('budgets.title')}
          action={
            <Button asChild size="sm" className="gap-1.5 h-8">
              <Link to="/categories">
                {t('budgets.manageInCategories')} <ArrowRight size={13} />
              </Link>
            </Button>
          }
        />
        <div className="border-b border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground sm:px-5">
          {t('budgets.readOnlyHint')}
        </div>
        {budgetedCategories.length > 0 ? (
          <table className="w-full">
            <thead>
              <tr className="border-b border-border">
                <th className={`${TH} pl-4 sm:pl-5 text-left`}>{t('budgets.category')}</th>
                <th className={`${TH} text-left w-36`}>{t('budgets.amount')}</th>
                <th className={`${TH} pr-4 sm:pr-5 text-right w-24`}>{t('budgets.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {budgetedCategories.map((category) => (
                <tr key={category.id} className="border-b border-border last:border-0 hover:bg-muted transition-colors">
                  <td className="py-3 pl-4 sm:pl-5 text-sm font-medium text-foreground">
                    {getCategoryDisplay(category.id)}
                  </td>
                  <td className="py-3 text-sm font-semibold tabular-nums text-foreground">
                    {mask(formatCurrency(Number(category.budget_amount), userCurrency, locale))}
                  </td>
                  <td className="py-3 pr-4 text-right text-xs text-muted-foreground sm:pr-5">
                    {t('budgets.editFromCategories')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('budgets.empty')}</p>
        )}
      </SectionCard>
    </div>
  )
}
