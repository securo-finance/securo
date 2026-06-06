import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import type { InstallmentPurchase } from '@/types'

interface Props {
  purchases: InstallmentPurchase[]
}

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

export function InstallmentCategoryChart({ purchases }: Props) {
  const { t } = useTranslation()
  const { privacyMode } = usePrivacyMode()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'

  const chartData = useMemo(() => {
    const activePurchases = purchases.filter((p) => p.status === 'ACTIVE' && p.remaining_amount > 0)
    if (activePurchases.length === 0) return []

    const categoryMap = new Map<string, { name: string; color: string; value: number }>()
    for (const purchase of activePurchases) {
      const key = purchase.category?.id ?? 'uncategorized'
      const existing = categoryMap.get(key)
      if (existing) {
        existing.value += purchase.remaining_amount
      } else {
        categoryMap.set(key, {
          name: purchase.category?.name ?? t('installments.category.uncategorized', 'Sem categoria'),
          color: purchase.category?.color ?? '#6B7280',
          value: purchase.remaining_amount,
        })
      }
    }

    return Array.from(categoryMap.values()).sort((a, b) => b.value - a.value)
  }, [purchases, t])

  if (chartData.length === 0) return null

  const total = chartData.reduce((sum, d) => sum + d.value, 0)

  const tooltipStyle = {
    contentStyle: {
      backgroundColor: 'hsl(var(--card))',
      border: '1px solid hsl(var(--border))',
      borderRadius: '8px',
      fontSize: '12px',
    },
    itemStyle: { color: 'hsl(var(--foreground))' },
  }

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      <div className="px-4 sm:px-5 py-4 border-b border-border">
        <p className="text-sm font-semibold text-foreground">
          {t('installments.chart.title', 'Remaining by Category')}
        </p>
      </div>
      <div className="p-5 flex flex-col items-center">
        <div className="relative" style={{ width: 200, height: 200 }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={chartData}
                cx="50%"
                cy="50%"
                innerRadius={55}
                outerRadius={85}
                paddingAngle={3}
                dataKey="value"
                strokeWidth={0}
              >
                {chartData.map((entry, idx) => (
                  <Cell key={idx} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value?: number) => {
                  const v = value ?? 0
                  const pct = total > 0 ? ((v / total) * 100).toFixed(1) : '0'
                  return [
                    privacyMode ? '***' : `${formatCurrency(v, currency)} (${pct}%)`,
                    '',
                  ]
                }}
                {...tooltipStyle}
                wrapperStyle={{ zIndex: 10 }}
                offset={20}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none" style={{ zIndex: 0 }}>
            <span className="text-[10px] text-muted-foreground">{t('installments.chart.remaining', 'Remaining')}</span>
            <span className="text-base font-bold text-foreground tabular-nums">
              {privacyMode ? '***' : formatCurrency(total, currency)}
            </span>
          </div>
        </div>
        {/* Legend */}
        <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 px-3 mt-3">
          {chartData.map((d) => (
            <div key={d.name} className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
              <span className="text-xs text-muted-foreground whitespace-nowrap">
                {d.name}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
