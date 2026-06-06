import { Badge } from '@/components/ui/badge'
import type { InstallmentItem } from '@/types'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'

interface Props {
  items: InstallmentItem[]
}

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

export function InstallmentTimeline({ items }: Props) {
  const { privacyMode } = usePrivacyMode()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'

  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div
          key={item.number}
          className="flex items-center justify-between py-2 px-3 rounded-lg bg-muted/50"
        >
          <div className="flex items-center gap-3">
            <span className="text-xs font-mono text-muted-foreground w-6 text-center">
              {item.number}
            </span>
            <div>
              <p className="text-sm font-medium">
                {item.due_date}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">
              {privacyMode ? '***' : formatCurrency(item.amount, currency)}
            </span>
            <Badge
              variant={item.status === 'PAID' ? 'default' : 'secondary'}
              className={
                item.status === 'PAID'
                  ? 'bg-green-100 text-green-700 hover:bg-green-100'
                  : 'bg-amber-100 text-amber-700 hover:bg-amber-100'
              }
            >
              {item.status === 'PAID' ? 'Paid' : 'Pending'}
            </Badge>
          </div>
        </div>
      ))}
    </div>
  )
}
