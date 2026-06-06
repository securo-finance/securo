import type { InstallmentSummary } from '@/types'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'

interface Props {
  summary: InstallmentSummary | undefined
  isLoading: boolean
}

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

export function InstallmentSummaryCard({ summary, isLoading }: Props) {
  const { privacyMode } = usePrivacyMode()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'

  if (isLoading) {
    return (
      <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
        <div className="p-5 space-y-3">
          <div className="grid grid-cols-4 gap-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="space-y-2">
                <div className="h-3 bg-muted rounded w-2/3 animate-pulse" />
                <div className="h-6 bg-muted rounded w-1/2 animate-pulse" />
              </div>
            ))}
          </div>
          <div className="h-2 bg-muted rounded w-full animate-pulse" />
        </div>
      </div>
    )
  }

  if (!summary) return null

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      <div className="p-5 space-y-4">
        <div className="grid grid-cols-4 gap-4">
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              {summary.active_purchases_count} compras parceladas
            </p>
            <p className="text-2xl font-bold tabular-nums">
              {summary.active_purchases_count}
            </p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              Valor Total (Estimado)
            </p>
            <p className="text-2xl font-bold tabular-nums">
              {privacyMode ? '***' : formatCurrency(summary.total_estimated_amount, currency)}
            </p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              Já Pago
            </p>
            <p className="text-2xl font-bold tabular-nums text-green-600">
              {privacyMode ? '***' : formatCurrency(summary.total_paid_amount, currency)}
            </p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              Restante
            </p>
            <p className="text-2xl font-bold tabular-nums text-amber-600">
              {privacyMode ? '***' : formatCurrency(summary.total_remaining_amount, currency)}
            </p>
          </div>
        </div>

        <div className="space-y-1.5">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>Progresso geral</span>
            <span className="font-medium text-green-600">
              {summary.overall_progress_percentage.toFixed(0)}% pago
            </span>
          </div>
          <div className="h-2 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all"
              style={{ width: `${Math.min(summary.overall_progress_percentage, 100)}%` }}
            />
          </div>
        </div>

        {summary.final_maturity_date && (
          <p className="text-xs text-muted-foreground">
            Última parcela: {summary.final_maturity_date}
          </p>
        )}
      </div>
    </div>
  )
}
