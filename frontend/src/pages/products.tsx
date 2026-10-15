import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, ArrowLeft, Package, Pencil, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { PageHeader } from '@/components/page-header'
import { IconAction, SectionCard, Segmented, TH } from '@/components/invoice-ui'
import { CurrencySelect } from '@/components/currency-select'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { fiscal as fiscalApi, invoices as invoicesApi, products as productsApi, type PricePayload } from '@/lib/api'
import { invoiceErrorKey } from '@/lib/invoice-utils'
import { FREQUENCIES } from '@/lib/invoice-schedule-utils'
import { productActions } from '@/lib/product-utils'
import type { InvoiceScheduleFrequency, PriceBilling, Product, ProductKind } from '@/types'

/**
 * The catalog: what this workspace sells, and for how much.
 *
 * Optional by design. A workspace that never opens this page writes
 * invoice lines exactly as before; one that does gets a picker beside
 * every line. Archiving is the way out for a product an invoice names,
 * because the invoice keeps pointing at it.
 */
type Filter = 'active' | 'archived'

export default function ProductsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()

  const [filter, setFilter] = useState<Filter>('active')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<Product | 'new' | null>(null)

  const { data: list, isLoading } = useQuery({
    queryKey: ['products', filter],
    queryFn: () => productsApi.list({ active: filter === 'active' }),
  })

  const visible = useMemo(() => {
    if (!list) return []
    const q = search.trim().toLowerCase()
    if (!q) return list
    return list.filter(
      (p) => p.name.toLowerCase().includes(q) || (p.description ?? '').toLowerCase().includes(q),
    )
  }, [list, search])

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['products'] })
  }
  const onError = (error: unknown) => {
    const key = invoiceErrorKey(error)
    toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
  }
  const archiveMutation = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => productsApi.update(id, { active }),
    onSuccess: (_, { active }) => {
      toast.success(active ? t('invoices.products.restored') : t('invoices.products.archived'))
      refresh()
    },
    onError,
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => productsApi.remove(id),
    onSuccess: () => {
      toast.success(t('invoices.products.deleted'))
      refresh()
    },
    onError,
  })

  const money = (value: string, code: string) => mask(formatCurrency(Number(value), code, locale))
  const priceLabel = (product: Product) => {
    const live = product.prices.filter((p) => p.active)
    if (live.length === 0) return t('invoices.products.noPrice')
    return live
      .map(
        (p) =>
          money(p.unit_price, p.currency) +
          (p.interval ? ` / ${t(`invoices.products.per.${p.interval}`)}` : ''),
      )
      .join(' · ')
  }

  return (
    <div>
      <button
        onClick={() => navigate('/invoices')}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors mb-3"
        data-testid="products-back"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t('invoices.backToList')}
      </button>

      <PageHeader
        section={t('invoices.title')}
        title={t('invoices.products.title')}
        action={
          canWrite ? (
            <Button size="sm" onClick={() => setEditing('new')} data-testid="product-new-button">
              <Plus className="h-4 w-4 mr-1.5" />
              {t('invoices.products.new')}
            </Button>
          ) : undefined
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Segmented<Filter>
          value={filter}
          onChange={setFilter}
          testIdPrefix="product-filter"
          options={(['active', 'archived'] as const).map((value) => ({
            value,
            label: t(`invoices.products.filter.${value}`),
          }))}
        />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('invoices.products.searchPlaceholder')}
          className="h-9 w-full sm:w-64"
          data-testid="product-search"
        />
      </div>

      <SectionCard>
        {isLoading ? (
          <div className="p-5 space-y-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <div className="px-5 py-14 text-center" data-testid="products-empty">
            <Package className="h-8 w-8 mx-auto text-muted-foreground/50" />
            <p className="mt-3 text-sm text-muted-foreground max-w-sm mx-auto">
              {search
                ? t('invoices.products.noneFound')
                : filter === 'archived'
                  ? t('invoices.products.emptyArchived')
                  : t('invoices.products.empty')}
            </p>
            {!search && filter === 'active' && canWrite && (
              <Button size="sm" className="mt-4" onClick={() => setEditing('new')}>
                <Plus className="h-4 w-4 mr-1.5" />
                {t('invoices.products.new')}
              </Button>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border">
                  <th className={`${TH} pl-4 sm:pl-5 text-left`}>{t('invoices.products.column.name')}</th>
                  <th className={`${TH} text-left w-24 hidden sm:table-cell`}>{t('invoices.products.column.kind')}</th>
                  <th className={`${TH} text-left hidden md:table-cell`}>{t('invoices.products.column.prices')}</th>
                  <th className={`${TH} text-right w-24 hidden lg:table-cell`}>{t('invoices.products.column.invoices')}</th>
                  {canWrite && (
                    <th className={`${TH} pr-4 sm:pr-5 w-28`}>
                      <span className="sr-only">{t('invoices.moreActions')}</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {visible.map((product) => {
                  const actions = productActions(product)
                  return (
                    <tr key={product.id} data-testid="product-row" className="border-b border-border last:border-0">
                      <td className="py-3 pl-4 sm:pl-5">
                        <div className="text-sm font-medium text-foreground truncate">{product.name}</div>
                        <div className="text-xs text-muted-foreground truncate">
                          {product.description ?? (product.unit ? t('invoices.products.perUnit', { unit: product.unit }) : '')}
                        </div>
                      </td>
                      <td className="py-3 hidden sm:table-cell text-xs text-muted-foreground">
                        {t(`invoices.products.kind.${product.kind}`)}
                      </td>
                      <td className="py-3 hidden md:table-cell text-xs text-muted-foreground tabular-nums">
                        {priceLabel(product)}
                      </td>
                      <td className="py-3 text-right hidden lg:table-cell text-xs text-muted-foreground tabular-nums">
                        {product.invoice_count}
                      </td>
                      {canWrite && (
                        <td className="py-3 pr-4 sm:pr-5">
                          <div className="flex justify-end gap-1">
                            <IconAction onClick={() => setEditing(product)} label={t('common.edit')}>
                              <Pencil className="h-3.5 w-3.5" />
                            </IconAction>
                            {actions.canArchive && (
                              <IconAction
                                onClick={() => archiveMutation.mutate({ id: product.id, active: false })}
                                label={t('invoices.products.action.archive')}
                              >
                                <Archive className="h-3.5 w-3.5" />
                              </IconAction>
                            )}
                            {actions.canRestore && (
                              <IconAction
                                onClick={() => archiveMutation.mutate({ id: product.id, active: true })}
                                label={t('invoices.products.action.restore')}
                              >
                                <ArchiveRestore className="h-3.5 w-3.5" />
                              </IconAction>
                            )}
                            {actions.canDelete && (
                              <IconAction onClick={() => deleteMutation.mutate(product.id)} label={t('common.delete')}>
                                <Trash2 className="h-3.5 w-3.5" />
                              </IconAction>
                            )}
                          </div>
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <ProductDialog
        key={editing === null ? 'closed' : editing === 'new' ? 'new' : editing.id}
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        product={editing === 'new' ? null : editing}
        onSaved={refresh}
      />
    </div>
  )
}

interface PriceRow {
  id: string | null
  currency: string
  unit_price: string
  tax_rate: string
  billing: PriceBilling
  interval: InvoiceScheduleFrequency | ''
  nickname: string
  active: boolean
}

function blankPrice(currency: string): PriceRow {
  return { id: null, currency, unit_price: '', tax_rate: '', billing: 'one_time', interval: '', nickname: '', active: true }
}

function toPayload(row: PriceRow): PricePayload {
  return {
    currency: row.currency,
    unit_price: row.unit_price,
    tax_rate: row.tax_rate || null,
    billing: row.billing,
    interval: row.billing === 'recurring' && row.interval ? row.interval : null,
    nickname: row.nickname || null,
  }
}

export function ProductDialog({
  open,
  onOpenChange,
  product,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Null creates. */
  product: Product | null
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
    enabled: open,
  })
  const showTax = (settings?.tax_fields ?? 'hidden') !== 'hidden'
  const defaultCurrency = user?.preferences?.currency_display ?? 'USD'
  // What this workspace's jurisdiction asks for on a catalog item. A
  // suggestion: the keys below are offered, and any other key can be
  // added by hand, so a Brazilian studio selling to Berlin can carry an
  // HS code beside its NCM.
  const { data: suggested } = useQuery({
    queryKey: ['fiscal', 'product-fields'],
    queryFn: fiscalApi.productFields,
    enabled: open,
  })

  const [name, setName] = useState(product?.name ?? '')
  const [kind, setKind] = useState<ProductKind>(product?.kind ?? 'service')
  const [unit, setUnit] = useState(product?.unit ?? '')
  const [description, setDescription] = useState(product?.description ?? '')
  const [refs, setRefs] = useState<{ key: string; value: string }[]>(() =>
    Object.entries(product?.fiscal_refs ?? {}).map(([key, value]) => ({ key, value })),
  )
  const [customKey, setCustomKey] = useState('')
  const [prices, setPrices] = useState<PriceRow[]>(
    product
      ? product.prices.map((p) => ({
          id: p.id,
          currency: p.currency,
          unit_price: p.unit_price,
          tax_rate: p.tax_rate ?? '',
          billing: p.billing,
          interval: p.interval ?? '',
          nickname: p.nickname ?? '',
          active: p.active,
        }))
      : [blankPrice(defaultCurrency)],
  )

  const updatePrice = (index: number, patch: Partial<PriceRow>) =>
    setPrices(prices.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  const onError = (error: unknown) => {
    const key = invoiceErrorKey(error)
    toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
  }

  const mutation = useMutation({
    mutationFn: async () => {
      const filled = prices.filter((row) => row.unit_price !== '')
      const fiscal_refs = Object.fromEntries(
        refs.filter((r) => r.key.trim() && r.value.trim()).map((r) => [r.key.trim().toLowerCase(), r.value.trim()]),
      )
      const base = {
        name,
        kind,
        unit: unit || null,
        description: description || null,
        fiscal_refs: Object.keys(fiscal_refs).length ? fiscal_refs : null,
      }
      if (!product) {
        return productsApi.create({ ...base, prices: filled.map(toPayload) })
      }
      // Product fields, then each price by what happened to it: new rows
      // are added, existing rows updated. Nothing is deleted from here:
      // an existing price the person no longer wants is archived, which
      // is the only outcome the server allows once an invoice was billed
      // at it, and the same outcome either way keeps the dialog honest.
      let saved = await productsApi.update(product.id, base)
      for (const row of prices) {
        if (row.id) {
          // A cleared amount on an existing price is not a change to it.
          const patch = row.unit_price === '' ? {} : toPayload(row)
          saved = await productsApi.updatePrice(product.id, row.id, { ...patch, active: row.active })
        } else if (row.unit_price !== '') {
          saved = await productsApi.addPrice(product.id, toPayload(row))
        }
      }
      return saved
    },
    onSuccess: () => {
      toast.success(product ? t('invoices.products.saved') : t('invoices.products.created'))
      onOpenChange(false)
      onSaved()
    },
    onError: (error) => {
      onError(error)
      // The edit path is several requests, and the ones before the
      // failure have committed. Closing on the server's state means a
      // retry starts from what is really there, instead of adding the
      // same new price twice.
      if (product) {
        onSaved()
        onOpenChange(false)
      }
    },
  })

  const ready = name.trim().length > 0 && prices.every((row) => row.billing !== 'recurring' || row.interval !== '' || row.unit_price === '')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex flex-col max-h-[calc(100dvh-2rem)] sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{product ? t('invoices.products.editTitle') : t('invoices.products.new')}</DialogTitle>
          <DialogDescription>{t('invoices.products.newDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_9rem_6rem] gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="product-name">{t('invoices.products.field.name')}</Label>
              <Input id="product-name" data-testid="product-name-input" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('invoices.products.field.namePlaceholder')} />
            </div>
            <div className="space-y-1.5">
              <Label>{t('invoices.products.field.kind')}</Label>
              <Select value={kind} onValueChange={(v) => setKind(v as ProductKind)}>
                <SelectTrigger data-testid="product-kind-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(['service', 'product'] as const).map((value) => (
                    <SelectItem key={value} value={value}>{t(`invoices.products.kind.${value}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="product-unit">{t('invoices.field.unit')}</Label>
              <Input id="product-unit" data-testid="product-unit-input" value={unit} onChange={(e) => setUnit(e.target.value)} placeholder={t('invoices.field.unitPlaceholder')} maxLength={20} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="product-description">{t('invoices.products.field.description')}</Label>
            <Input id="product-description" data-testid="product-description-input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>

          {/* Fiscal references. The keys the jurisdiction suggests for
              this kind are offered as fields; anything else is added by
              key, and an empty value drops the key. With nothing
              suggested the section is one line and an "add" row, so a
              workspace whose country has no pack yet can still carry
              what its documents need. */}
          {(() => {
            const offered = (suggested?.fields ?? []).filter(
              (f) => f.kinds.length === 0 || f.kinds.includes(kind),
            )
            const offeredKeys = new Set(offered.map((f) => f.key))
            const extra = refs.filter((r) => !offeredKeys.has(r.key))
            const valueOf = (key: string) => refs.find((r) => r.key === key)?.value ?? ''
            const setRef = (key: string, value: string) =>
              setRefs((prev) =>
                prev.some((r) => r.key === key)
                  ? prev.map((r) => (r.key === key ? { ...r, value } : r))
                  : [...prev, { key, value }],
              )
            const anyField = offered.length > 0 || extra.length > 0
            return (
              <div className="space-y-2" data-testid="product-fiscal-refs">
                <Label>{t('invoices.products.field.fiscalRefs')}</Label>
                <p className="text-[11px] text-muted-foreground">
                  {anyField
                    ? t('invoices.products.field.fiscalRefsHint')
                    : t('invoices.products.field.fiscalRefsNone')}
                </p>
                <div className={cn('grid grid-cols-2 sm:grid-cols-3 gap-3', !anyField && 'hidden')}>
                  {offered.map((f) => (
                    <div key={f.key} className="space-y-1.5">
                      <Label htmlFor={`ref-${f.key}`} className="text-xs">{t(f.label_key, f.key)}</Label>
                      <Input
                        id={`ref-${f.key}`}
                        data-testid={`product-ref-${f.key}`}
                        className="h-9"
                        value={valueOf(f.key)}
                        onChange={(e) => setRef(f.key, e.target.value)}
                        maxLength={100}
                      />
                    </div>
                  ))}
                  {extra.map((r) => (
                    <div key={r.key} className="space-y-1.5">
                      <Label htmlFor={`ref-${r.key}`} className="text-xs">{t(`fiscal.productField.${r.key}`, r.key)}</Label>
                      <Input
                        id={`ref-${r.key}`}
                        data-testid={`product-ref-${r.key}`}
                        className="h-9"
                        value={r.value}
                        onChange={(e) => setRef(r.key, e.target.value)}
                        maxLength={100}
                      />
                    </div>
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    className="h-8 w-48"
                    placeholder={t('invoices.products.field.customRefKey')}
                    value={customKey}
                    onChange={(e) => setCustomKey(e.target.value)}
                    data-testid="product-ref-custom-key"
                    maxLength={40}
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!/^[a-z][a-z0-9_]*$/.test(customKey.trim().toLowerCase()) || refs.some((r) => r.key === customKey.trim().toLowerCase())}
                    onClick={() => {
                      setRef(customKey.trim().toLowerCase(), '')
                      setCustomKey('')
                    }}
                    data-testid="product-ref-add"
                  >
                    <Plus className="h-3.5 w-3.5 mr-1" />
                    {t('invoices.products.field.addRef')}
                  </Button>
                </div>
              </div>
            )
          })()}

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>{t('invoices.products.field.prices')}</Label>
              <Button size="sm" variant="ghost" onClick={() => setPrices([...prices, blankPrice(defaultCurrency)])} data-testid="product-add-price">
                <Plus className="h-3.5 w-3.5 mr-1" />
                {t('invoices.products.field.addPrice')}
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">{t('invoices.products.field.pricesHint')}</p>
            <div className="rounded-lg border border-border divide-y divide-border">
              {prices.map((row, index) => (
                <div key={row.id ?? `new-${index}`} className={cn('grid grid-cols-2 sm:grid-cols-[7rem_1fr_9rem_8rem_2rem] gap-2 px-3 py-2.5 items-center', !row.active && 'opacity-60')} data-testid="product-price-row">
                  <CurrencySelect id={`price-currency-${index}`} value={row.currency} onChange={(code) => updatePrice(index, { currency: code })} />
                  <Input
                    className="h-9 text-right"
                    inputMode="decimal"
                    placeholder="0.00"
                    value={row.unit_price}
                    onChange={(e) => updatePrice(index, { unit_price: e.target.value })}
                    data-testid={`price-amount-${index}`}
                    aria-label={t('invoices.field.unitPrice')}
                  />
                  <Select
                    value={row.billing === 'recurring' ? row.interval || 'recurring' : 'one_time'}
                    onValueChange={(v) =>
                      v === 'one_time'
                        ? updatePrice(index, { billing: 'one_time', interval: '' })
                        : updatePrice(index, { billing: 'recurring', interval: v === 'recurring' ? '' : (v as InvoiceScheduleFrequency) })
                    }
                  >
                    <SelectTrigger data-testid={`price-billing-${index}`}><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="one_time">{t('invoices.products.billing.one_time')}</SelectItem>
                      {FREQUENCIES.map((f) => (
                        <SelectItem key={f} value={f}>{t('invoices.products.billing.every', { interval: t(`invoices.products.per.${f}`) })}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    className="h-9"
                    placeholder={t('invoices.products.field.nickname')}
                    value={row.nickname}
                    onChange={(e) => updatePrice(index, { nickname: e.target.value })}
                    data-testid={`price-nickname-${index}`}
                    maxLength={100}
                  />
                  {/* A new row is simply dropped. An existing price is
                      archived instead: invoices may already name it, and
                      the picker stops offering it either way. */}
                  {row.id ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 w-8 p-0 text-muted-foreground hover:text-foreground"
                      onClick={() => updatePrice(index, { active: !row.active })}
                      data-testid={`price-${row.active ? 'archive' : 'restore'}-${index}`}
                      aria-label={row.active ? t('invoices.products.action.archive') : t('invoices.products.action.restore')}
                    >
                      {row.active ? <Archive className="h-4 w-4" /> : <ArchiveRestore className="h-4 w-4" />}
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                      onClick={() => setPrices(prices.filter((_, i) => i !== index))}
                      data-testid={`price-remove-${index}`}
                      aria-label={t('common.delete')}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                  {showTax && (
                    <div className="col-span-2 sm:col-span-5 flex items-center gap-2">
                      <span className="text-[11px] text-muted-foreground">{t('invoices.field.taxRate')}</span>
                      <Input className="h-8 w-20 text-right" inputMode="decimal" placeholder="%" value={row.tax_rate} onChange={(e) => updatePrice(index, { tax_rate: e.target.value })} data-testid={`price-tax-${index}`} />
                    </div>
                  )}
                </div>
              ))}
              {prices.length === 0 && (
                <p className="px-3 py-3 text-xs text-muted-foreground">{t('invoices.products.field.noPrices')}</p>
              )}
            </div>
          </div>
        </div>

        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button onClick={() => mutation.mutate()} disabled={!ready || mutation.isPending} data-testid="product-save">
            {product ? t('common.save') : t('common.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
