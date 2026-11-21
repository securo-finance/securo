import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { assets } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import type { Asset, MarketSymbolMatch } from '@/types'

const SEARCH_DEBOUNCE_MS = 300

/**
 * Links a market ticker to a holding synced from a bank connection, for when
 * the sync could not identify the symbol on its own (or picked one the user
 * wants to change). The connection keeps owning the quantity; the ticker
 * brings the market price, the logo and the daily refresh, and later syncs
 * keep it. A holding already priced from a ticker can go back to the value
 * the bank reports.
 */
export function LinkTickerDialog({ asset, onClose }: { asset: Asset | null; onClose: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState<MarketSymbolMatch[]>([])
  const [searching, setSearching] = useState(false)

  // Start from the current link, else from the holding's name: brokers name
  // B3 positions after their symbol ("PETR4"), which is the best first guess.
  useEffect(() => {
    if (asset) setQuery(asset.ticker ?? asset.name)
    setMatches([])
  }, [asset])

  useEffect(() => {
    const q = query.trim()
    if (!asset || q.length < 1) {
      setMatches([])
      return
    }
    setSearching(true)
    const handle = window.setTimeout(async () => {
      try {
        setMatches(await assets.marketSearch(q, 8))
      } catch {
        setMatches([])
      } finally {
        setSearching(false)
      }
    }, SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(handle)
  }, [asset, query])

  // The valuation changes, so the chart and total (portfolio-trend) refresh
  // with the list, as refetchAssetViews does on the assets page.
  const refetchAssetViews = () => {
    queryClient.refetchQueries({ queryKey: ['assets'] })
    queryClient.refetchQueries({ queryKey: ['portfolio-trend'] })
  }

  const mutation = useMutation({
    mutationFn: (ticker: string) => assets.linkTicker(asset!.id, ticker),
    onSuccess: (linked) => {
      refetchAssetViews()
      toast.success(t('assets.linkTickerLinked', { ticker: linked.ticker }))
      onClose()
    },
    onError: (error) => toast.error(extractApiError(error, t('common.error'))),
  })

  const unlink = useMutation({
    mutationFn: () => assets.unlinkTicker(asset!.id),
    onSuccess: () => {
      refetchAssetViews()
      toast.success(t('assets.linkTickerUnlinked'))
      onClose()
    },
    onError: (error) => toast.error(extractApiError(error, t('common.error'))),
  })

  const ticker = query.trim()
  const isMarketPriced = asset?.valuation_method === 'market_price'
  const busy = mutation.isPending || unlink.isPending

  return (
    <Dialog open={!!asset} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('assets.linkTickerTitle')}</DialogTitle>
          <DialogDescription>{t('assets.linkTickerHint')}</DialogDescription>
        </DialogHeader>
        <form
          className="min-w-0 space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (ticker) mutation.mutate(ticker)
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="link-ticker-query">{t('assets.linkTickerLabel')}</Label>
            <Input
              id="link-ticker-query"
              value={query}
              maxLength={32}
              autoComplete="off"
              placeholder={t('assets.linkTickerPlaceholder')}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <ul className="max-h-56 min-w-0 overflow-y-auto overflow-x-hidden rounded-md border border-border divide-y divide-border" aria-live="polite">
            {matches.map((match) => (
              <li key={match.symbol} className="min-w-0">
                <button
                  type="button"
                  onClick={() => setQuery(match.symbol)}
                  title={[match.symbol, match.name, match.exchange].filter(Boolean).join(' · ')}
                  className="w-full min-w-0 flex items-center gap-3 px-3 py-2 text-left text-sm hover:bg-muted transition-colors"
                >
                  <span className="font-medium text-foreground shrink-0">{match.symbol}</span>
                  {/* Fund names run long ("… FUNDO DE INDICE RESPONSABILIDADE LIMITADA");
                      they truncate so the dialog never grows wider than its frame. */}
                  <span className="min-w-0 flex-1 truncate text-right text-xs text-muted-foreground">
                    {[match.name, match.exchange].filter(Boolean).join(' · ')}
                  </span>
                </button>
              </li>
            ))}
            {!searching && matches.length === 0 && (
              <li className="px-3 py-2 text-xs text-muted-foreground">{t('assets.linkTickerNoResults')}</li>
            )}
            {searching && matches.length === 0 && (
              <li className="px-3 py-2 text-xs text-muted-foreground">{t('common.loading')}</li>
            )}
          </ul>
          <DialogFooter>
            {isMarketPriced && (
              <Button type="button" variant="ghost" className="sm:mr-auto" disabled={busy} onClick={() => unlink.mutate()}>
                {unlink.isPending ? t('common.loading') : t('assets.linkTickerUnlink')}
              </Button>
            )}
            <Button type="button" variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!ticker || busy}>
              {mutation.isPending ? t('common.loading') : t('assets.linkTickerAction')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
