import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'

export interface PageRefreshButtonProps {
  /**
   * Refetch the page's data (usually `queryClient.refetchQueries`). When it
   * returns a promise, the button stays busy until every refetch settles.
   */
  onRefresh: () => unknown
  /** Spin the icon and disable the button while a refetch is in flight. */
  refreshing?: boolean
}

/**
 * Icon-only "refresh" control for a page header. Mirrors the transactions
 * page's refresh button so every page reads the same word and behaves the
 * same: it spins and disables while a refetch is in flight.
 */
export function PageRefreshButton({ onRefresh, refreshing = false }: PageRefreshButtonProps) {
  const { t } = useTranslation()
  const label = t('transactions.refresh')
  // `refreshing` follows the page's main query; a page can show other queries
  // that finish later, so the button also waits for its own refetch promise.
  const [pending, setPending] = useState(false)
  const busy = refreshing || pending

  const handleClick = () => {
    const result = onRefresh()
    if (result instanceof Promise) {
      setPending(true)
      result.finally(() => setPending(false)).catch(() => {})
    }
  }

  return (
    <Button
      variant="outline"
      size="icon"
      className="!size-9"
      aria-label={label}
      title={label}
      data-testid="page-refresh-button"
      disabled={busy}
      onClick={handleClick}
    >
      <RefreshCw size={16} className={busy ? 'animate-spin' : ''} />
    </Button>
  )
}
