import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { CreditCard, ChevronRight } from 'lucide-react'

import { accounts as accountsApi } from '@/lib/api'
import { PageHeader } from '@/components/page-header'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { useCurrentMonth } from '@/hooks/use-current-month'

export default function CardsPage() {
  const { t } = useTranslation()
  const { data: currentMonthState } = useCurrentMonth()

  const { data: accountsList, isLoading } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const cards = (accountsList ?? []).filter((account) => account.type === 'credit_card')
  const periodLabel = currentMonthState?.selected_period_label ?? currentMonthState?.current_period_label

  return (
    <div>
      <PageHeader section={t('cards.section')} title={t('cards.title')} />

      <div className="mb-6 rounded-xl border border-border bg-card p-4 shadow-sm">
        <p className="text-sm text-muted-foreground">{t('cards.description')}</p>
        {periodLabel ? (
          <p className="mt-2 text-xs font-medium text-foreground">
            {t('cards.snapshotLabel')}: {periodLabel}
          </p>
        ) : null}
      </div>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-40 rounded-xl" />
          ))}
        </div>
      ) : cards.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-card px-6 py-12 text-center shadow-sm">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
            <CreditCard size={20} />
          </div>
          <h2 className="mt-4 text-lg font-semibold text-foreground">{t('cards.empty')}</h2>
          <p className="mt-2 text-sm text-muted-foreground">{t('cards.emptyHint')}</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {cards.map((card) => (
            <Link
              key={card.id}
              to={`/cards/${card.id}`}
              className="group rounded-xl border border-border bg-card p-5 shadow-sm transition-colors hover:border-primary/40 hover:bg-primary/[0.03]"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <CreditCard size={18} />
                  </div>
                  <div className="min-w-0">
                    <p className="truncate text-base font-semibold text-foreground">{card.name}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{t('accounts.typeCreditCard')}</p>
                  </div>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-1 text-[11px] font-semibold ${
                    card.bill_import_enabled
                      ? 'bg-emerald-100 text-emerald-700'
                      : 'bg-muted text-muted-foreground'
                  }`}
                >
                  {card.bill_import_enabled ? t('accounts.billImportEnabled') : t('accounts.billImportDisabled')}
                </span>
              </div>

              <div className="mt-5 flex items-center justify-between border-t border-border pt-4">
                <div className="text-sm text-muted-foreground">
                  <p>{card.currency}</p>
                </div>
                <Button variant="ghost" size="sm" className="pointer-events-none gap-1 text-primary">
                  {t('cards.open')}
                  <ChevronRight size={14} />
                </Button>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
