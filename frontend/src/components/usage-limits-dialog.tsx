import { useTranslation } from 'react-i18next'
import { useMyQuotas, useQuotaNotice } from '@/hooks/use-quota'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/**
 * The account's usage limits on a managed instance (ADR 0049, #1972): each
 * bounded counter with its count and maximum. Unbounded counters are left out.
 */
export function UsageLimitsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const { data, isLoading, isError, refetch } = useMyQuotas()
  const { infoUrl } = useQuotaNotice()
  const counters = data?.counters ?? []

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('quota.title')}</DialogTitle>
          <DialogDescription>{t('quota.description')}</DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
        ) : isError ? (
          <div className="space-y-2">
            <p className="text-sm text-destructive">{t('quota.loadError')}</p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        ) : (
          <ul className="divide-y rounded-lg border">
            {counters.map((usage) => {
              const full = usage.current >= usage.maximum
              return (
                <li key={usage.counter} className="flex items-center justify-between gap-3 px-3 py-2.5">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{t(`quota.counters.${usage.counter}`, usage.counter)}</p>
                    {usage.counter === 'max_members_per_workspace' && (
                      <p className="text-xs text-muted-foreground">{t('quota.membersHint')}</p>
                    )}
                  </div>
                  <span className={cn('shrink-0 text-sm tabular-nums', full && 'font-semibold text-destructive')}>
                    {t('quota.usage', { current: usage.current, maximum: usage.maximum })}
                  </span>
                </li>
              )
            })}
          </ul>
        )}
        {infoUrl && (
          <a href={infoUrl} target="_blank" rel="noopener noreferrer" className="text-sm text-primary underline-offset-4 hover:underline">
            {t('quota.learnMore')}
          </a>
        )}
      </DialogContent>
    </Dialog>
  )
}
