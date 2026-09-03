import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { InvoiceState } from '@/types'

/**
 * Shared chrome for the invoicing screens.
 *
 * Everything here mirrors patterns already established elsewhere in
 * Securo (recurring, accounts, dashboard) rather than inventing a
 * parallel vocabulary. A module that looks like its own product inside
 * the product is the thing to avoid.
 */

/** The card every section sits in. Same shell as `recurring`. */
export function SectionCard({
  children,
  className = '',
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'bg-card rounded-xl border border-border shadow-sm overflow-hidden',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function SectionHeader({
  title,
  action,
}: {
  title: string
  action?: React.ReactNode
}) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-semibold text-foreground">{title}</p>
      {action}
    </div>
  )
}

/** Table header cell. Byte-identical to the constant in `recurring`. */
export const TH = 'text-xs font-medium text-muted-foreground py-3'

/**
 * State pill, in the app's badge shape.
 *
 * The tones carry meaning and only three of them are loud: overdue is
 * the one thing on this screen a person has to act on, paid is the one
 * that closes a loop, and everything else stays quiet. A palette where
 * every row shouts is a palette where nothing does.
 */
const STATE_TONE: Record<InvoiceState, string> = {
  draft: 'bg-muted text-muted-foreground border-border',
  open: 'bg-muted text-foreground border-border',
  partial: 'bg-amber-50 text-amber-700 border-amber-100 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/20',
  paid: 'bg-emerald-50 text-emerald-600 border-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/20',
  overdue: 'bg-rose-50 text-rose-600 border-rose-100 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/20',
  void: 'bg-muted text-muted-foreground border-border line-through',
  uncollectible: 'bg-muted text-muted-foreground border-border',
}

export function StateBadge({ state }: { state: InvoiceState }) {
  const { t } = useTranslation()
  return (
    <span
      data-testid={`invoice-state-${state}`}
      className={cn(
        'text-[11px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap',
        STATE_TONE[state],
      )}
    >
      {t(`invoices.state.${state}`)}
    </span>
  )
}

/**
 * Segmented control, the shape `account-detail` already uses for its
 * currency switch. Serves both the state filter and the detail tabs, so
 * the two read as the same control doing the same job.
 */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  testIdPrefix,
}: {
  value: T
  onChange: (value: T) => void
  options: { value: T; label: React.ReactNode; count?: number }[]
  testIdPrefix: string
}) {
  return (
    <div className="inline-flex rounded-lg border border-border bg-muted p-0.5 text-xs font-medium">
      {options.map((option) => {
        const active = value === option.value
        return (
          <button
            key={option.value}
            onClick={() => onChange(option.value)}
            data-testid={`${testIdPrefix}-${option.value}`}
            className={cn(
              'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md transition-colors whitespace-nowrap',
              active
                ? 'bg-card text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {option.label}
            {/* A zero is worth showing: "Overdue 0" is the answer to the
                question, and hiding it makes the reader click to find
                out. `undefined` means the count is still loading. */}
            {option.count !== undefined && (
              <span
                data-testid={`${testIdPrefix}-${option.value}-count`}
                className={cn(
                  'tabular-nums text-[11px]',
                  active ? 'text-muted-foreground' : 'text-muted-foreground/70',
                )}
              >
                {option.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

/** Icon-only row action, same affordance as the one in `recurring`. */
export function IconAction({
  onClick,
  label,
  children,
  destructive = false,
}: {
  onClick: () => void
  label: string
  children: React.ReactNode
  destructive?: boolean
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className={cn(
        'p-1.5 rounded-md text-muted-foreground transition-colors',
        destructive
          ? 'hover:text-destructive hover:bg-destructive/5'
          : 'hover:text-primary hover:bg-primary/5',
      )}
    >
      {children}
    </button>
  )
}
