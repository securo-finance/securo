import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { CloudOff } from 'lucide-react'
import { Button } from '@/components/ui/button'

/** How often the shell asks the server again while it can't be reached. */
export const UNREACHABLE_RETRY_MS = 10_000

/** Shown in place of every page while the session or the workspace can't
 *  be loaded for a reason other than a rejected token (offline, server
 *  restarting). Rendering the pages instead shows zeros that look like
 *  real money (#292). The URL is left alone, so the page asked for opens
 *  once the server answers. */
export function ServerUnreachable({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()
  const offline = typeof navigator !== 'undefined' && navigator.onLine === false

  useEffect(() => {
    const timer = window.setInterval(onRetry, UNREACHABLE_RETRY_MS)
    window.addEventListener('online', onRetry)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('online', onRetry)
    }
  }, [onRetry])

  return (
    <div role="alert" className="flex min-h-screen items-center justify-center p-6">
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        <CloudOff className="h-10 w-10 text-muted-foreground" aria-hidden="true" />
        <h1 className="text-lg font-semibold">
          {offline ? t('connection.offlineTitle') : t('connection.unreachableTitle')}
        </h1>
        <p className="text-sm text-muted-foreground">{t('connection.description')}</p>
        <Button variant="outline" onClick={onRetry}>
          {t('connection.retry')}
        </Button>
      </div>
    </div>
  )
}
