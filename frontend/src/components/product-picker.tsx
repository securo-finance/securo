import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { PackageSearch } from 'lucide-react'

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { products as productsApi } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { priceFor } from '@/lib/product-utils'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { cn, normalizeText } from '@/lib/utils'
import type { Product, ProductPrice } from '@/types'

/**
 * The catalog, as a button beside a line's description.
 *
 * A button rather than a combobox replacing the description field: the
 * field has to stay a plain input, because most lines are typed and a
 * dropdown that opens on every keystroke would get in the way of the
 * common case. The catalog is one click away for the lines that come
 * from it, and invisible to a workspace that never made a product.
 */
export function ProductPicker({
  currency,
  onPick,
  disabled = false,
  className,
}: {
  /** The invoice's currency: decides which of a product's prices is offered. */
  currency: string
  onPick: (product: Product, price: ProductPrice | null) => void
  disabled?: boolean
  className?: string
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  // Only once opened: a workspace without a catalog never asks for one.
  const { data: products = [] } = useQuery({
    queryKey: ['products', 'active'],
    queryFn: () => productsApi.list({ active: true }),
    enabled: open,
  })

  const rows = useMemo(
    () =>
      [...products]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((product) => ({ product, price: priceFor(product, currency) })),
    [products, currency],
  )

  return (
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
          aria-label={t('invoices.products.pick')}
          title={t('invoices.products.pick')}
          data-testid="invoice-line-pick-product"
          className={cn(
            'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-input bg-card text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50',
            className,
          )}
        >
          <PackageSearch className="h-4 w-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[340px] p-0 overflow-hidden">
        <Command
          filter={(itemValue, query, keywords) => {
            const text = keywords?.length ? keywords.join(' ') : itemValue
            return normalizeText(text).includes(normalizeText(query)) ? 1 : 0
          }}
        >
          <CommandInput
            placeholder={t('invoices.products.searchPlaceholder')}
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>
              {products.length === 0
                ? t('invoices.products.emptyPicker')
                : t('invoices.products.noneFound')}
            </CommandEmpty>
            <CommandGroup>
              {rows.map(({ product, price }) => (
                <CommandItem
                  key={product.id}
                  value={product.id}
                  keywords={[product.name, product.description ?? '']}
                  onSelect={() => {
                    onPick(product, price)
                    setOpen(false)
                    setSearch('')
                  }}
                  className="cursor-pointer"
                  data-testid={`product-option-${product.id}`}
                >
                  <span className="flex-1 min-w-0">
                    <span className="block truncate">{product.name}</span>
                    {product.description && (
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {product.description}
                      </span>
                    )}
                  </span>
                  {/* The price in this invoice's currency, or a quiet
                      note that there is none and the amount is typed. */}
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    {price
                      ? formatCurrency(Number(price.unit_price), price.currency, locale) +
                        (price.interval ? ` / ${t(`invoices.products.per.${price.interval}`)}` : '')
                      : t('invoices.products.noPriceIn', { currency })}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
