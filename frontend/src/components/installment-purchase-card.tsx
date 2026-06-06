import { CircleHelp } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ICON_MAP } from '@/lib/category-icons'
import type { InstallmentPurchase } from '@/types'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'

interface Props {
  purchase: InstallmentPurchase
}

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

export function InstallmentPurchaseCard({ purchase }: Props) {
  const { privacyMode } = usePrivacyMode()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'

  const CategoryIcon = purchase.category
    ? (ICON_MAP[purchase.category.icon] ?? CircleHelp)
    : CircleHelp

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start gap-4">
          {/* Icon */}
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 text-white"
            style={{ backgroundColor: purchase.category?.color ?? '#6B7280' }}
          >
            <CategoryIcon size={18} />
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm font-semibold text-foreground truncate">
                {purchase.merchant_name}
              </span>
              <Badge
                variant={purchase.status === 'ACTIVE' ? 'default' : 'secondary'}
                className={
                  purchase.status === 'ACTIVE'
                    ? 'bg-blue-100 text-blue-700 hover:bg-blue-100'
                    : 'bg-green-100 text-green-700 hover:bg-green-100'
                }
              >
                {purchase.status === 'ACTIVE' ? 'Ativo' : 'Finalizado'}
              </Badge>
              {purchase.is_manual && (
                <Badge variant="outline" className="text-[10px] shrink-0">Manual</Badge>
              )}
              {purchase.total_amount_estimated && (
                <Badge variant="outline" className="text-[10px] shrink-0 text-amber-600 border-amber-300">
                  Estimado
                </Badge>
              )}
            </div>

            <p className="text-xs text-muted-foreground mb-1.5">{purchase.institution_name}</p>

            {/* Metrics row */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground mb-1.5">
              <span className="tabular-nums font-medium">
                {purchase.paid_count}/{purchase.total_installments}x
              </span>
              <span className="tabular-nums">
                {privacyMode ? '***' : formatCurrency(purchase.installment_monthly_amount, currency)}/mês
              </span>
              {purchase.end_date && (
                <span className="tabular-nums">
                  Última parcela: {purchase.end_date}
                </span>
              )}
            </div>

            {/* Progress bar */}
            <div className="flex items-center gap-3">
              <div className="flex-1 h-2 bg-muted/60 rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full transition-all"
                  style={{ width: `${Math.min(purchase.progress_percentage, 100)}%` }}
                />
              </div>
            </div>
          </div>

          {/* Remaining amount - right aligned */}
          {purchase.remaining_amount > 0 && (
            <div className="shrink-0 text-right">
              <p className="text-sm font-semibold text-foreground tabular-nums">
                {privacyMode ? '***' : formatCurrency(purchase.remaining_amount, currency)}
              </p>
              <p className="text-[10px] text-muted-foreground">restante</p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
