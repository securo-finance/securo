import { useTranslation } from 'react-i18next'
import { RefreshCw, WifiOff } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/**
 * Shown instead of the routes while the session or the workspace could not be
 * loaded for a reason other than a rejected token. It says the data is
 * unavailable rather than rendering an app full of zeros, and leaves the URL
 * untouched: once the server answers, the page the person opened loads by
 * itself (issue #292).
 */
export function ConnectionLost({
  onRetry,
  className,
}: {
  onRetry: () => void
  className?: string
}) {
  const { t } = useTranslation()

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        'flex flex-col items-center justify-center gap-4 px-6 py-16 text-center',
        className,
      )}
    >
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <WifiOff size={24} aria-hidden="true" />
      </div>
      <div className="max-w-sm space-y-2">
        <h1 className="text-lg font-semibold">{t('offline.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('offline.description')}</p>
      </div>
      <Button variant="outline" onClick={onRetry}>
        <RefreshCw aria-hidden="true" />
        {t('common.retry')}
      </Button>
    </div>
  )
}
