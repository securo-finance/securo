import { useTranslation } from 'react-i18next'

import { Label } from '@/components/ui/label'
import type { Account } from '@/types'

export const ROUTE_SKIP = '__skip__'

export interface RoutingTarget {
  value: string
  count: number
}

interface AccountRoutingProps {
  targets: RoutingTarget[]
  accounts: Account[]
  /** hint -> account id, or ROUTE_SKIP. Missing = not routed yet. */
  routing: Record<string, string>
  onRoute: (hint: string, target: string) => void
}

/**
 * Combined-export account routing strip (§7.2): distinct Monarch account
 * values with row counts → Securo account picker (normalized-name prefill is
 * resolved by the page) + "Skip these rows". Unrouted values block the import
 * button — the block message renders with the missing-account warning.
 */
export function AccountRouting({ targets, accounts, routing, onRoute }: AccountRoutingProps) {
  const { t } = useTranslation()
  return (
    <div className="space-y-3" data-testid="account-routing-strip">
      <Label className="text-sm text-muted-foreground">{t('import.routingTitle')}</Label>
      {targets.map(({ value, count }) => (
        <div key={value} className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4">
          <div className="text-sm text-foreground sm:flex-1 min-w-0">
            <span className="font-medium truncate block">{value}</span>
            <span className="text-xs text-muted-foreground">
              {count} {t('import.mappingRows')}
            </span>
          </div>
          <select
            className="flex-1 border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
            data-testid={`routing-select-${value}`}
            value={routing[value] ?? ''}
            aria-label={value}
            onChange={(e) => onRoute(value, e.target.value)}
          >
            <option value="">
              {t('import.routingNoTarget')}
            </option>
            {accounts.map((acc) => (
              <option key={acc.id} value={acc.id}>
                {acc.name}
              </option>
            ))}
            <option value={ROUTE_SKIP}>{t('import.routingSkip')}</option>
          </select>
        </div>
      ))}
    </div>
  )
}
