import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChartPie } from 'lucide-react'

import { CategoryIcon } from '@/components/category-icon'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { isSplitByCategory } from '@/lib/category-lines'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Transaction } from '@/types'

type SplitSource = Pick<Transaction, 'has_category_lines' | 'category_lines' | 'currency'>

const ICON_BOX = {
  xs: 'w-4 h-4 rounded',
  sm: 'w-6 h-6 rounded-md',
  md: 'w-8 h-8 rounded-lg',
  lg: 'w-10 h-10 rounded-xl',
  xl: 'w-11 h-11 rounded-xl',
} as const
const ICON_SIZE = { xs: 10, sm: 14, md: 16, lg: 20, xl: 22 } as const

/**
 * The icon a transaction shows in lists: its category's, or a split marker
 * when the amount is spread across several categories.
 */
export function TransactionCategoryIcon({
  tx,
  size = 'md',
  className,
}: {
  tx: SplitSource & Pick<Transaction, 'category'>
  size?: keyof typeof ICON_BOX
  className?: string
}) {
  if (!isSplitByCategory(tx)) {
    return (
      <CategoryIcon icon={tx.category?.icon} color={tx.category?.color} size={size} className={className} />
    )
  }
  return <SplitCategoryIcon size={size} className={className} />
}

/** The marker for a row split across categories. */
export function SplitCategoryIcon({
  size = 'md',
  className,
}: {
  size?: keyof typeof ICON_BOX
  className?: string
}) {
  return (
    <div
      className={cn(
        ICON_BOX[size],
        'flex items-center justify-center shrink-0 bg-violet-500 dark:bg-violet-600',
        className,
      )}
    >
      <ChartPie size={ICON_SIZE[size]} className="text-white" strokeWidth={2} />
    </div>
  )
}

/** Every part of a split, one per line, with its share. */
export function CategoryLinesList({ tx, className }: { tx: SplitSource; className?: string }) {
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const lines = [...(tx.category_lines ?? [])].sort((a, b) => a.position - b.position)
  return (
    <ul className={cn('space-y-1', className)}>
      {lines.map((line) => (
        <li key={line.id} className="flex items-center gap-2 text-xs">
          <CategoryIcon icon={line.category?.icon} color={line.category?.color} size="xs" />
          <span className="flex-1 min-w-0 truncate">{line.category?.name ?? ''}</span>
          {line.share_pct != null && (
            <span className="text-muted-foreground tabular-nums">{Number(line.share_pct)}%</span>
          )}
          <span className="tabular-nums">{mask(formatCurrency(Number(line.amount), tx.currency, locale))}</span>
        </li>
      ))}
    </ul>
  )
}

/**
 * The category label a list shows: the name, or a "Split" badge whose
 * tooltip lists the parts.
 */
export function TransactionCategoryLabel({
  tx,
  className,
  emptyLabel,
}: {
  tx: SplitSource & Pick<Transaction, 'category'>
  className?: string
  emptyLabel?: string
}) {
  const { t } = useTranslation()
  if (!isSplitByCategory(tx)) {
    return tx.category ? (
      <span className={cn('text-sm text-muted-foreground', className)}>{tx.category.name}</span>
    ) : (
      <span className={cn('text-xs text-muted-foreground italic', className)}>
        {emptyLabel ?? t('transactions.noCategory')}
      </span>
    )
  }
  return <CategorySplitBadge tx={tx} className={className} />
}

/**
 * "Split · N" with the parts in a tooltip. `compact` drops the word for
 * narrow rows (mobile), where the full label squeezes the description.
 */
export function CategorySplitBadge({
  tx,
  className,
  compact = false,
}: {
  tx: SplitSource
  className?: string
  compact?: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const count = tx.category_lines?.length ?? 0
  // A popover rather than a tooltip: it opens on hover with a mouse and on
  // tap with touch, and a tap shows the parts instead of opening the row.
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            'inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide',
            'text-violet-700 bg-violet-50 border border-violet-200',
            'dark:bg-violet-950/40 dark:text-violet-300 dark:border-violet-900',
            'px-1.5 py-0.5 rounded-full cursor-pointer whitespace-nowrap shrink-0',
            className,
          )}
          aria-label={t('categoryLines.badge', { count })}
          onClick={(e) => {
            // Opens on tap; a click outside or Escape closes it.
            e.stopPropagation()
            setOpen(true)
          }}
          onPointerEnter={(e) => { if (e.pointerType === 'mouse') setOpen(true) }}
          onPointerLeave={(e) => { if (e.pointerType === 'mouse') setOpen(false) }}
        >
          <ChartPie size={10} />
          {compact ? count : t('categoryLines.badge', { count })}
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-auto min-w-48 max-w-72 p-3"
        onOpenAutoFocus={(e) => e.preventDefault()}
        onClick={(e) => e.stopPropagation()}
      >
        <CategoryLinesList tx={tx} />
      </PopoverContent>
    </Popover>
  )
}
