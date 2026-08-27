import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckIcon, ChevronDownIcon, Pencil, Plus } from 'lucide-react'

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { filterPayees } from '@/lib/payee-select-utils'
import { cn } from '@/lib/utils'
import type { Payee } from '@/types'

interface PayeeSelectProps {
  value: string
  onChange: (value: string) => void
  payees: Payee[]
  /** Shown on the trigger when `value` is not in `payees` — a cache still
   *  refetching, a row deleted elsewhere — so the field never blanks out.
   *  Mirrors CategorySelect's `currentCategory`. */
  currentPayee?: Payee | null
  placeholder?: string
  disabled?: boolean
  className?: string
  allowNone?: boolean
  /** Undefined means a read-only picker with no pencils at all. Callers gate
   *  this on write access; the component knows nothing about roles. */
  onEditPayee?: (payee: Payee) => void
  /** Offered a "create" row seeded with whatever is in the search box. */
  onCreatePayee?: (name: string) => void
  contentProps?: React.ComponentProps<typeof PopoverContent>
}

/** Pick a counterparty, and fix one without leaving the form you are in.
 *
 *  Modelled on CategorySelect, with two divergences that the scale forces:
 *  the search is controlled and the list is capped (cmdk's own filter renders
 *  every item and hides the misses, which is fine for fifty categories and not
 *  for the thousands of rows sync creates), and each row carries a pencil.
 */
export function PayeeSelect({
  value,
  onChange,
  payees,
  currentPayee,
  placeholder,
  disabled = false,
  className,
  allowNone = false,
  onEditPayee,
  onCreatePayee,
  contentProps,
}: PayeeSelectProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  // Radix returns focus to the trigger when a popover closes. When the close is
  // on its way to opening a dialog, that fights the dialog's own opening focus
  // and can leave neither focused, so the handoff is suppressed for exactly
  // that transition.
  const suppressReturnFocus = useRef(false)

  const selectedPayee = useMemo(
    () => payees.find((p) => p.id === value) ?? (currentPayee?.id === value ? currentPayee : null),
    [payees, value, currentPayee],
  )
  const visible = useMemo(() => filterPayees(payees, search), [payees, search])

  const closeThenOpenDialog = (run: () => void) => {
    suppressReturnFocus.current = true
    setOpen(false)
    run()
  }

  const resolvedPlaceholder = placeholder ?? t('payees.selectPayee')
  const typed = search.trim()

  return (
    <div className={cn('flex items-center gap-1', className)}>
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) setSearch('')
        }}
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            className="flex flex-1 min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-card px-3 py-2 text-sm text-left shadow-xs transition-[color,box-shadow] outline-hidden focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50 h-9 cursor-pointer"
          >
            <span className="flex items-center gap-2 min-w-0 truncate">
              {selectedPayee ? (
                <span className="truncate">{selectedPayee.name}</span>
              ) : value === '' && allowNone ? (
                <span className="italic text-muted-foreground truncate">{t('payees.noPayee')}</span>
              ) : (
                <span className="text-muted-foreground truncate">{resolvedPlaceholder}</span>
              )}
            </span>
            <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="w-[var(--radix-popover-trigger-width)] p-0 overflow-hidden"
          onCloseAutoFocus={(e) => {
            if (suppressReturnFocus.current) {
              e.preventDefault()
              suppressReturnFocus.current = false
            }
          }}
          {...contentProps}
        >
          {/* Filtering is ours, so cmdk must not also filter: it would rank the
              already-ranked list by its own score. */}
          <Command shouldFilter={false}>
            <CommandInput
              value={search}
              onValueChange={setSearch}
              placeholder={t('payees.searchPayee')}
            />
            <CommandList>
              {allowNone && (
                <CommandGroup>
                  <CommandItem
                    value="__none__"
                    onSelect={() => {
                      onChange('')
                      setOpen(false)
                    }}
                    className="italic text-muted-foreground cursor-pointer"
                  >
                    <span className="flex-1">{t('payees.noPayee')}</span>
                    {value === '' && <CheckIcon className="size-4 shrink-0" />}
                  </CommandItem>
                </CommandGroup>
              )}
              {visible.length === 0 && (
                // Not CommandEmpty: with shouldFilter={false} cmdk counts the
                // rendered items, and the create row below is always one.
                <p className="py-6 text-center text-sm text-muted-foreground">
                  {t('payees.noPayeeFound')}
                </p>
              )}
              <CommandGroup>
                {visible.map((p) => (
                  <CommandItem
                    key={p.id}
                    value={p.id}
                    onSelect={() => {
                      onChange(p.id)
                      setOpen(false)
                    }}
                    className="cursor-pointer"
                  >
                    <span className="truncate flex-1 min-w-0">{p.name}</span>
                    {p.source !== 'manual' && (
                      <span className="shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                        {p.source === 'sync' ? t('payees.sourceSync') : t('payees.sourceImport')}
                      </span>
                    )}
                    {value === p.id && <CheckIcon className="size-4 shrink-0" />}
                    {onEditPayee && (
                      <button
                        type="button"
                        aria-label={t('payees.editNamed', { name: p.name })}
                        // cmdk's Item selects from its own onClick and
                        // highlights on pointer move. stopPropagation keeps the
                        // pencil from also picking the row; preventDefault on
                        // pointerdown keeps it from stealing focus from the
                        // search box on the way.
                        onPointerDown={(e) => e.preventDefault()}
                        onClick={(e) => {
                          e.stopPropagation()
                          closeThenOpenDialog(() => onEditPayee(p))
                        }}
                        className="shrink-0 p-1 rounded text-muted-foreground hover:text-primary hover:bg-primary/5"
                      >
                        <Pencil size={12} />
                      </button>
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
              {onCreatePayee && (
                <CommandGroup>
                  <CommandItem
                    value="__create__"
                    onSelect={() => closeThenOpenDialog(() => onCreatePayee(typed))}
                    className="cursor-pointer"
                  >
                    <Plus className="size-4 shrink-0" />
                    <span className="truncate">
                      {typed ? t('payees.createNamed', { name: typed }) : t('payees.createNew')}
                    </span>
                  </CommandItem>
                </CommandGroup>
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      {/* Sibling of the trigger, never inside it: `asChild` composition would
          make this also open the popover. It is the keyboard-reachable half of
          the pencil pair — the ones inside the list are not tab stops, because
          making cmdk items focusable would break its arrow-key navigation. */}
      {onEditPayee && selectedPayee && (
        <button
          type="button"
          aria-label={t('payees.editNamed', { name: selectedPayee.name })}
          disabled={disabled}
          onClick={() => onEditPayee(selectedPayee)}
          className="shrink-0 h-9 w-9 inline-flex items-center justify-center rounded-md border border-input text-muted-foreground hover:text-primary hover:bg-primary/5 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Pencil size={14} />
        </button>
      )}
    </div>
  )
}
