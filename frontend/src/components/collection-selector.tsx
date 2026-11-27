import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import { Check, ChevronsUpDown, Layers, Settings2, X } from 'lucide-react'

/**
 * Global "active collection" selector (issue #105). Filters the app to the
 * accounts (and wallets) of the chosen collection.
 *
 * Always shown in the top bar, so what the numbers cover is said on every
 * page; without collections it says "all accounts" and leads to making one.
 */
export function CollectionSelector() {
  const { t } = useTranslation()
  const nav = useNavigate()
  const { collections, activeCollection, setActiveCollectionId } = useCollectionFilter()

  const menu = (
    <DropdownMenuContent align="start" className="w-72">
      <DropdownMenuItem onClick={() => setActiveCollectionId(null)} className="flex items-center gap-2">
        <Layers size={14} className="text-muted-foreground" />
        <span className="flex-1">{t('collections.allAccounts')}</span>
        {!activeCollection && <Check size={14} className="text-primary" />}
      </DropdownMenuItem>
      {collections.length > 0 && <DropdownMenuSeparator />}
      {collections.map((c) => (
        <DropdownMenuItem key={c.id} onClick={() => setActiveCollectionId(c.id)} className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: c.color }} />
          <span className="min-w-0 flex-1 truncate">{c.name}</span>
          {/* Spelled out and translated: "8a · 3w" read as cropped text, and the
              a/w abbreviations were English-only in a ten-locale UI. */}
          <span className="shrink-0 whitespace-nowrap text-[11px] tabular-nums text-muted-foreground/70">
            {[
              c.account_count > 0 && t('collections.accountCount', { count: c.account_count }),
              c.wallet_count > 0 && t('collections.walletCount', { count: c.wallet_count }),
            ].filter(Boolean).join(' · ')}
          </span>
          {activeCollection?.id === c.id && <Check size={14} className="shrink-0 text-primary" />}
        </DropdownMenuItem>
      ))}
      <DropdownMenuSeparator />
      <DropdownMenuItem onClick={() => nav('/collections')} className="flex items-center gap-2 text-muted-foreground">
        <Settings2 size={14} />
        {t('collections.manage')}
      </DropdownMenuItem>
    </DropdownMenuContent>
  )

  // When a collection is active the trigger is tinted with the collection
  // color and gains a one-click clear, so the filtered state is impossible to
  // miss. Idle ("All accounts") it stays quiet.
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-muted-foreground/70 max-sm:sr-only">
        {t('collections.viewing')}
      </span>
      {/* Non-modal: the default locks body scroll while open, which hides the
          page scrollbar and shifts the layout under the sticky bar. */}
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <button
            className="group inline-flex min-w-0 items-center gap-2 rounded-full border border-border/60 px-3 py-1.5 text-[13px] transition-colors hover:bg-muted/50"
            style={
              activeCollection
                ? { borderColor: `${activeCollection.color}55`, backgroundColor: `${activeCollection.color}14` }
                : undefined
            }
          >
            {activeCollection ? (
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: activeCollection.color }} />
            ) : (
              <Layers size={14} className="shrink-0 text-muted-foreground" />
            )}
            <span
              className={
                activeCollection
                  ? 'truncate font-medium text-foreground'
                  : 'truncate text-muted-foreground group-hover:text-foreground'
              }
            >
              {activeCollection?.name ?? t('collections.allAccounts')}
            </span>
            <ChevronsUpDown size={13} className="shrink-0 text-muted-foreground/70" />
          </button>
        </DropdownMenuTrigger>
        {menu}
      </DropdownMenu>
      {activeCollection && (
        <button
          onClick={() => setActiveCollectionId(null)}
          className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          aria-label={t('collections.clearFilter')}
        >
          <X size={13} />
          <span className="max-sm:sr-only">{t('collections.clearFilter')}</span>
        </button>
      )}
    </div>
  )
}
