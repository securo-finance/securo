import { useContext, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CheckIcon, ChevronDownIcon, PlusIcon } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { payees as payeesApi } from '@/lib/api'
import { inlineCreateItemValue, inlineCreateName } from '@/lib/inline-create'
import { payeeErrorMessage } from '@/lib/payee-error-message'
import { cn, normalizeText } from '@/lib/utils'
import type { Payee } from '@/types'

interface PayeeSelectProps {
  value: string
  onChange: (value: string) => void
  payees: Payee[]
  disabled?: boolean
  className?: string
  /**
   * Offer to create a payee named after the search text when nothing
   * matches it. Only shown to members who can write to the workspace.
   */
  creatable?: boolean
}

/** Searchable payee picker. An empty value means "no payee". */
export function PayeeSelect({
  value,
  onChange,
  payees,
  disabled = false,
  className,
  creatable = false,
}: PayeeSelectProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  // The payee just created here, shown until the refetched list has it.
  const [created, setCreated] = useState<Payee | null>(null)
  const canWrite = useContext(WorkspaceContext)?.canWrite ?? false
  const canCreate = creatable && canWrite

  const sortedPayees = useMemo(
    () => [...(payees ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [payees],
  )

  const selected = useMemo(
    () => sortedPayees.find((p) => p.id === value)
      ?? (created && created.id === value ? created : undefined),
    [sortedPayees, created, value],
  )

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) setSearch('')
  }

  const createMutation = useMutation({
    mutationFn: (name: string) => payeesApi.create({ name }),
    onSuccess: (payee) => {
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      setCreated(payee)
      onChange(payee.id)
      handleOpenChange(false)
      toast.success(t('payees.created'))
    },
    onError: (err: unknown) => toast.error(payeeErrorMessage(err, t) ?? t('common.error')),
  })

  const createName = canCreate
    ? inlineCreateName(search, sortedPayees.map((p) => p.name))
    : null

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-md border border-input bg-card px-3 py-2 text-sm text-left shadow-xs transition-[color,box-shadow] outline-hidden focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50 h-9 cursor-pointer",
            className,
          )}
        >
          <span className="min-w-0 truncate">
            {selected ? (
              selected.name
            ) : (
              <span className="text-muted-foreground">{t('payees.noPayee')}</span>
            )}
          </span>
          <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-0 overflow-hidden">
        <Command
          filter={(itemValue, query, keywords) => {
            // Payee rows match on their name (keywords), never on the id
            // that only keeps their cmdk value unique.
            const text = keywords?.length ? keywords.join(' ') : itemValue
            return normalizeText(text).includes(normalizeText(query)) ? 1 : 0
          }}
        >
          <CommandInput
            placeholder={t('payees.searchPlaceholder')}
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>{t('payees.noPayeeFound')}</CommandEmpty>
            <CommandGroup>
              <CommandItem
                value={`__none__ ${t('payees.noPayee')}`}
                onSelect={() => {
                  onChange('')
                  handleOpenChange(false)
                }}
                className="italic text-muted-foreground cursor-pointer"
              >
                <span className="flex-1">{t('payees.noPayee')}</span>
                {value === '' && <CheckIcon className="size-4 shrink-0" />}
              </CommandItem>
              {sortedPayees.map((payee) => (
                <CommandItem
                  key={payee.id}
                  value={payee.id}
                  keywords={[payee.name]}
                  onSelect={() => {
                    onChange(payee.id)
                    handleOpenChange(false)
                  }}
                  className="cursor-pointer"
                >
                  <span className="flex-1 truncate">{payee.name}</span>
                  {value === payee.id && <CheckIcon className="size-4 shrink-0" />}
                </CommandItem>
              ))}
            </CommandGroup>
            {createName && (
              <CommandGroup>
                <CommandItem
                  value={inlineCreateItemValue(search)}
                  disabled={createMutation.isPending}
                  onSelect={() => createMutation.mutate(createName)}
                  className="cursor-pointer"
                >
                  <PlusIcon className="size-4 shrink-0 text-muted-foreground" />
                  <span className="truncate">{t('common.createNamed', { name: createName })}</span>
                </CommandItem>
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
