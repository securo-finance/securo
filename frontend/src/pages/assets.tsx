import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { assets, currencies as currenciesApi } from '@/lib/api'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import type { Asset, AssetValue } from '@/types'
import {
  Home,
  Car,
  Gem,
  TrendingUp,
  Package,
  Plus,
  Pencil,
  Trash2,
  ChevronDown,
  ChevronUp,
} from 'lucide-react'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import { PageHeader } from '@/components/page-header'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  try {
    return new Intl.NumberFormat(locale, { style: 'currency', currency: currency || 'USD' }).format(value)
  } catch {
    return new Intl.NumberFormat(locale, { style: 'currency', currency: 'USD' }).format(value)
  }
}

const ASSET_TYPE_CONFIG: Record<string, { icon: React.ElementType; color: string; bg: string }> = {
  real_estate: { icon: Home, color: 'text-blue-600', bg: 'bg-blue-100' },
  vehicle: { icon: Car, color: 'text-violet-600', bg: 'bg-violet-100' },
  valuable: { icon: Gem, color: 'text-amber-600', bg: 'bg-amber-100' },
  investment: { icon: TrendingUp, color: 'text-emerald-600', bg: 'bg-emerald-100' },
  other: { icon: Package, color: 'text-slate-600', bg: 'bg-slate-100' },
}

function getTypeConfig(type: string) {
  return ASSET_TYPE_CONFIG[type] ?? ASSET_TYPE_CONFIG['other']
}

const ASSET_TYPES = ['real_estate', 'vehicle', 'valuable', 'investment', 'other'] as const
const VALUATION_METHODS = ['manual', 'growth_rule'] as const
const GROWTH_TYPES = ['percentage', 'absolute'] as const
const GROWTH_FREQUENCIES = ['daily', 'weekly', 'monthly', 'yearly'] as const

export default function AssetsPage() {
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()

  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currenciesApi.list,
    staleTime: Infinity,
  })

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingAsset, setEditingAsset] = useState<Asset | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [pendingGrowthSave, setPendingGrowthSave] = useState<Record<string, unknown> | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // Form state
  const [formName, setFormName] = useState('')
  const [formType, setFormType] = useState<string>('other')
  const [formCurrency, setFormCurrency] = useState(userCurrency)
  const [formMethod, setFormMethod] = useState<string>('manual')
  const [formPurchaseDate, setFormPurchaseDate] = useState<string>('')
  const [formPurchasePrice, setFormPurchasePrice] = useState('')
  const [formSellDate, setFormSellDate] = useState<string>('')
  const [formSellPrice, setFormSellPrice] = useState('')
  const [formCurrentValue, setFormCurrentValue] = useState('')
  const [formGrowthType, setFormGrowthType] = useState<string>('percentage')
  const [formGrowthRate, setFormGrowthRate] = useState('')
  const [formGrowthFrequency, setFormGrowthFrequency] = useState<string>('monthly')
  const [formGrowthStartDate, setFormGrowthStartDate] = useState<string>('')

  const { data: assetsList, isLoading } = useQuery({
    queryKey: ['assets'],
    queryFn: () => assets.list(true),
  })

  const { data: portfolioData } = useQuery({
    queryKey: ['portfolio-trend'],
    queryFn: () => assets.portfolioTrend(),
  })

  const createMutation = useMutation({
    mutationFn: (data: Parameters<typeof assets.create>[0]) => assets.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['portfolio-trend'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      setDialogOpen(false)
      toast.success(t('assets.created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, _regenerateGrowth, ...data }: Partial<Asset> & { id: string; _regenerateGrowth?: boolean }) =>
      assets.update(id, data, { regenerateGrowth: _regenerateGrowth }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['portfolio-trend'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      setDialogOpen(false)
      setEditingAsset(null)
      toast.success(t('assets.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => assets.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['portfolio-trend'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      setDeletingId(null)
      if (expandedId === deletingId) setExpandedId(null)
      toast.success(t('assets.deleted'))
    },
    onError: () => toast.error(t('common.error')),
  })

  // Compute projected current value for growth_rule preview in the form
  const projectedGrowthValue = useMemo(() => {
    if (formMethod !== 'growth_rule') return null
    const baseAmount = parseFloat(formPurchasePrice)
    const rate = parseFloat(formGrowthRate)
    if (!baseAmount || !rate || !formGrowthFrequency) return null

    const startDate = formGrowthStartDate || formPurchaseDate
    if (!startDate) return null

    const today = new Date()
    today.setHours(0, 0, 0, 0)
    let current = baseAmount
    let d = new Date(startDate + 'T00:00:00')

    let iterations = 0
    while (iterations < 10000) {
      const next = new Date(d)
      if (formGrowthFrequency === 'daily') next.setDate(next.getDate() + 1)
      else if (formGrowthFrequency === 'weekly') next.setDate(next.getDate() + 7)
      else if (formGrowthFrequency === 'monthly') next.setMonth(next.getMonth() + 1)
      else if (formGrowthFrequency === 'yearly') next.setFullYear(next.getFullYear() + 1)
      else break
      if (next > today) break
      if (formGrowthType === 'percentage') {
        current = current * (1 + rate / 100)
      } else {
        current = current + rate
      }
      d = next
      iterations++
    }
    return Math.round(current * 100) / 100
  }, [formMethod, formPurchasePrice, formGrowthRate, formGrowthType, formGrowthFrequency, formGrowthStartDate, formPurchaseDate])

  const activeAssets = assetsList?.filter(a => !a.sell_date && !a.is_archived) ?? []
  const soldAssets = assetsList?.filter(a => a.sell_date) ?? []

  function openCreate() {
    setEditingAsset(null)
    setFormName('')
    setFormType('other')
    setFormCurrency(userCurrency)
    setFormMethod('manual')
    setFormPurchaseDate('')
    setFormPurchasePrice('')
    setFormSellDate('')
    setFormSellPrice('')
    setFormCurrentValue('')
    setFormGrowthType('percentage')
    setFormGrowthRate('')
    setFormGrowthFrequency('monthly')
    setFormGrowthStartDate('')
    setDialogOpen(true)
  }

  function openEdit(asset: Asset) {
    setEditingAsset(asset)
    setFormName(asset.name)
    setFormType(asset.type)
    setFormCurrency(asset.currency)
    setFormMethod(asset.valuation_method)
    setFormPurchaseDate(asset.purchase_date ?? '')
    setFormPurchasePrice(asset.purchase_price?.toString() ?? '')
    setFormSellDate(asset.sell_date ?? '')
    setFormSellPrice(asset.sell_price?.toString() ?? '')
    setFormCurrentValue('')
    setFormGrowthType(asset.growth_type ?? 'percentage')
    setFormGrowthRate(asset.growth_rate?.toString() ?? '')
    setFormGrowthFrequency(asset.growth_frequency ?? 'monthly')
    setFormGrowthStartDate(asset.growth_start_date ?? '')
    setDialogOpen(true)
  }

  function buildPayload() {
    const payload: Record<string, unknown> = {
      name: formName,
      type: formType,
      currency: formCurrency,
      valuation_method: formMethod,
      purchase_date: formPurchaseDate || null,
      purchase_price: formPurchasePrice ? parseFloat(formPurchasePrice) : null,
      sell_date: formSellDate || null,
      sell_price: formSellPrice ? parseFloat(formSellPrice) : null,
    }

    if (formMethod === 'growth_rule') {
      payload.growth_type = formGrowthType
      payload.growth_rate = formGrowthRate ? parseFloat(formGrowthRate) : null
      payload.growth_frequency = formGrowthFrequency
      payload.growth_start_date = formGrowthStartDate || null
    }

    if (!editingAsset && formCurrentValue) {
      payload.current_value = parseFloat(formCurrentValue)
    }

    return payload
  }

  function hasGrowthParamsChanged(): boolean {
    if (!editingAsset || editingAsset.valuation_method !== 'growth_rule') return false
    return (
      formGrowthType !== (editingAsset.growth_type ?? 'percentage') ||
      formGrowthRate !== (editingAsset.growth_rate?.toString() ?? '') ||
      formGrowthFrequency !== (editingAsset.growth_frequency ?? 'monthly') ||
      formGrowthStartDate !== (editingAsset.growth_start_date ?? '') ||
      formPurchasePrice !== (editingAsset.purchase_price?.toString() ?? '') ||
      formPurchaseDate !== (editingAsset.purchase_date ?? '')
    )
  }

  function handleSave() {
    const payload = buildPayload()

    if (editingAsset) {
      // If growth params changed, ask confirmation before regenerating
      if (hasGrowthParamsChanged() && editingAsset.value_count > 0) {
        setPendingGrowthSave(payload)
        return
      }
      updateMutation.mutate({ id: editingAsset.id, ...payload } as Partial<Asset> & { id: string })
    } else {
      createMutation.mutate(payload as Parameters<typeof assets.create>[0])
    }
  }

  function confirmRegenerateGrowth() {
    if (!editingAsset || !pendingGrowthSave) return
    updateMutation.mutate(
      { id: editingAsset.id, ...pendingGrowthSave, _regenerateGrowth: true } as Partial<Asset> & { id: string },
    )
    setPendingGrowthSave(null)
  }

  function renderAssetCard(asset: Asset) {
    const config = getTypeConfig(asset.type)
    const Icon = config.icon
    const isExpanded = expandedId === asset.id

    return (
      <div key={asset.id} className="border border-border rounded-xl bg-card shadow-sm overflow-hidden">
        <div
          className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-muted/30 transition-colors"
          onClick={() => setExpandedId(isExpanded ? null : asset.id)}
        >
          <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${config.bg}`}>
            <Icon size={20} className={config.color} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-foreground truncate">{asset.name}</span>
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                {t(`assets.type${asset.type.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()).replace(/^./, c => c.toUpperCase())}`)}
              </Badge>
              {asset.valuation_method === 'growth_rule' && asset.growth_rate && (
                <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-emerald-600 border-emerald-200">
                  +{asset.growth_type === 'percentage' ? `${asset.growth_rate}%` : formatCurrency(asset.growth_rate, asset.currency, locale)}
                  /{t(`assets.${asset.growth_frequency}`).toLowerCase().charAt(0)}
                </Badge>
              )}
              {asset.sell_date && (
                <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-rose-600 border-rose-200">
                  {t('assets.sold')}
                </Badge>
              )}
            </div>
          </div>
          <div className="text-right shrink-0">
            {asset.current_value != null ? (
              <>
                <p className="text-sm font-bold tabular-nums text-foreground">
                  {mask(formatCurrency(asset.current_value, asset.currency, locale))}
                  {asset.current_value_primary != null && (
                    <span className="text-[10px] font-medium text-muted-foreground ml-1">
                      ({mask(formatCurrency(asset.current_value_primary, userCurrency, locale))})
                    </span>
                  )}
                </p>
                {asset.gain_loss != null && (
                  <p className={`text-xs font-medium tabular-nums ${asset.gain_loss >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
                    {mask(`${asset.gain_loss >= 0 ? '+' : ''}${formatCurrency(asset.gain_loss, asset.currency, locale)}`)}
                    {asset.gain_loss_primary != null && (
                      <span className="text-[10px] text-muted-foreground ml-1">
                        ({mask(formatCurrency(asset.gain_loss_primary, userCurrency, locale))})
                      </span>
                    )}
                  </p>
                )}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">—</p>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button
              onClick={(e) => { e.stopPropagation(); openEdit(asset) }}
              className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              <Pencil size={14} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setDeletingId(asset.id) }}
              className="p-1.5 rounded-lg text-muted-foreground hover:text-rose-600 hover:bg-rose-50 transition-colors"
            >
              <Trash2 size={14} />
            </button>
            {isExpanded ? <ChevronUp size={16} className="text-muted-foreground" /> : <ChevronDown size={16} className="text-muted-foreground" />}
          </div>
        </div>

        {isExpanded && <AssetDetail assetId={asset.id} currency={asset.currency} locale={locale} purchasePrice={asset.purchase_price} purchaseDate={asset.purchase_date} valuationMethod={asset.valuation_method} />}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <PageHeader
        section={t('assets.title')}
        title={t('assets.title')}
        action={
          <Button onClick={openCreate} className="gap-1.5">
            <Plus size={16} />
            {t('assets.addAsset')}
          </Button>
        }
      />

      {/* Portfolio Stacked Area Chart */}
      {portfolioData && portfolioData.trend.length > 1 && (
        <PortfolioChart
          data={portfolioData}
          currency={userCurrency}
          locale={locale}
          mask={mask}
        />
      )}

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-xl" />)}
        </div>
      ) : (
        <div className="space-y-6">
          {/* Active Assets */}
          {activeAssets.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider px-1">
                {t('assets.activeAssets')}
              </h3>
              <div className="space-y-2">
                {activeAssets.map(renderAssetCard)}
              </div>
            </div>
          )}

          {/* Sold Assets */}
          {soldAssets.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider px-1">
                {t('assets.soldAssets')}
              </h3>
              <div className="space-y-2">
                {soldAssets.map(renderAssetCard)}
              </div>
            </div>
          )}

          {activeAssets.length === 0 && soldAssets.length === 0 && (
            <div className="text-center py-16">
              <Package className="mx-auto h-12 w-12 text-muted-foreground/40 mb-3" />
              <p className="text-muted-foreground">{t('assets.noAssets')}</p>
            </div>
          )}
        </div>
      )}

      {/* Create/Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingAsset ? t('assets.editAsset') : t('assets.addAsset')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {/* Name */}
            <div className="space-y-2">
              <Label>{t('assets.name')}</Label>
              <Input value={formName} onChange={e => setFormName(e.target.value)} />
            </div>

            {/* Type + Currency */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t('assets.type')}</Label>
                <select
                  className="bg-card border border-border focus:outline-none focus:ring-2 focus:ring-primary px-3 py-2 rounded-lg text-foreground text-sm w-full"
                  value={formType}
                  onChange={e => setFormType(e.target.value)}
                >
                  {ASSET_TYPES.map(at => (
                    <option key={at} value={at}>
                      {t(`assets.type${at.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()).replace(/^./, c => c.toUpperCase())}`)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label>{t('assets.currency')}</Label>
                <select
                  className="bg-card border border-border focus:outline-none focus:ring-2 focus:ring-primary px-3 py-2 rounded-lg text-foreground text-sm w-full"
                  value={formCurrency}
                  onChange={e => setFormCurrency(e.target.value)}
                >
                  {(supportedCurrencies ?? [{ code: userCurrency, symbol: userCurrency, name: userCurrency, flag: '' }]).map((c) => (
                    <option key={c.code} value={c.code}>{c.flag} {c.name}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Valuation Method — locked on edit */}
            <div className="space-y-2">
              <Label>{t('assets.valuationMethod')}</Label>
              <div className="grid grid-cols-2 gap-2">
                {VALUATION_METHODS.map(m => (
                  <button
                    key={m}
                    type="button"
                    disabled={!!editingAsset}
                    className={`px-3 py-2.5 rounded-lg text-sm font-medium border transition-all ${
                      formMethod === m
                        ? 'border-primary bg-primary/10 text-primary shadow-sm'
                        : 'border-border text-muted-foreground hover:border-primary/50 hover:bg-muted/50'
                    } ${editingAsset ? 'opacity-50 cursor-not-allowed' : ''}`}
                    onClick={() => !editingAsset && setFormMethod(m)}
                  >
                    {t(`assets.${m === 'growth_rule' ? 'growthRule' : 'manual'}`)}
                  </button>
                ))}
              </div>
            </div>

            {/* Growth Rule Settings */}
            {formMethod === 'growth_rule' && (
              <div className="space-y-3 p-3.5 rounded-xl border border-primary/20 bg-primary/5">
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t('assets.growthType')}</Label>
                    <select
                      className="bg-card border border-border focus:outline-none focus:ring-2 focus:ring-primary px-3 py-2 rounded-lg text-foreground text-sm w-full"
                      value={formGrowthType}
                      onChange={e => setFormGrowthType(e.target.value)}
                    >
                      {GROWTH_TYPES.map(gt => (
                        <option key={gt} value={gt}>{t(`assets.${gt}`)}</option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-2">
                    <Label>{t('assets.growthRate')}</Label>
                    <div className="relative">
                      <Input type="number" step="any" value={formGrowthRate} onChange={e => setFormGrowthRate(e.target.value)} className={formGrowthType === 'percentage' ? 'pr-8' : ''} />
                      {formGrowthType === 'percentage' && (
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground pointer-events-none">%</span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>{t('assets.growthFrequency')}</Label>
                    <select
                      className="bg-card border border-border focus:outline-none focus:ring-2 focus:ring-primary px-3 py-2 rounded-lg text-foreground text-sm w-full"
                      value={formGrowthFrequency}
                      onChange={e => setFormGrowthFrequency(e.target.value)}
                    >
                      {GROWTH_FREQUENCIES.map(gf => (
                        <option key={gf} value={gf}>{t(`assets.${gf}`)}</option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-2">
                    <Label>{t('assets.growthStartDate')}</Label>
                    <DatePickerInput value={formGrowthStartDate} onChange={setFormGrowthStartDate} />
                  </div>
                </div>
              </div>
            )}

            {/* Purchase Info */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t('assets.purchaseDate')}</Label>
                <DatePickerInput value={formPurchaseDate} onChange={setFormPurchaseDate} />
              </div>
              <div className="space-y-2">
                <Label>{t('assets.purchasePrice')}</Label>
                <Input type="number" step="0.01" value={formPurchasePrice} onChange={e => setFormPurchasePrice(e.target.value)} />
              </div>
            </div>

            {/* Sell Info */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t('assets.sellDate')}</Label>
                <DatePickerInput value={formSellDate} onChange={setFormSellDate} />
              </div>
              <div className="space-y-2">
                <Label>{t('assets.sellPrice')}</Label>
                <Input type="number" step="0.01" value={formSellPrice} onChange={e => setFormSellPrice(e.target.value)} />
              </div>
            </div>

            {/* Current Value — manual only */}
            {!editingAsset && formMethod === 'manual' && (
              <div className="space-y-2">
                <Label>{t('assets.currentValue')}</Label>
                <Input
                  type="number"
                  step="any"
                  value={formCurrentValue}
                  onChange={e => setFormCurrentValue(e.target.value)}
                />
              </div>
            )}

            {/* Projected Value — growth rule preview */}
            {formMethod === 'growth_rule' && projectedGrowthValue != null && (() => {
              const base = parseFloat(formPurchasePrice) || 0
              const isLoss = projectedGrowthValue < base
              const diff = projectedGrowthValue - base
              return (
                <div className={`flex items-center justify-between p-3.5 rounded-xl border ${isLoss ? 'bg-rose-50 dark:bg-rose-950/30 border-rose-200 dark:border-rose-800' : 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800'}`}>
                  <div>
                    <span className="text-xs font-medium text-muted-foreground">{t('assets.currentValue')}</span>
                    {base > 0 && (
                      <p className={`text-[11px] tabular-nums font-medium mt-0.5 ${isLoss ? 'text-rose-500' : 'text-emerald-600'}`}>
                        {diff >= 0 ? '+' : ''}{formatCurrency(diff, formCurrency, locale)}
                      </p>
                    )}
                  </div>
                  <span className={`text-xl font-bold tabular-nums ${isLoss ? 'text-rose-600' : 'text-emerald-600'}`}>
                    {formatCurrency(projectedGrowthValue, formCurrency, locale)}
                  </span>
                </div>
              )
            })()}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={handleSave}
              disabled={!formName || createMutation.isPending || updateMutation.isPending}
            >
              {t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Regenerate Growth Confirmation */}
      <Dialog open={!!pendingGrowthSave} onOpenChange={() => setPendingGrowthSave(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('assets.confirmRegenerateTitle')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{t('assets.confirmRegenerate')}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingGrowthSave(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={confirmRegenerateGrowth}
              disabled={updateMutation.isPending}
            >
              {t('assets.regenerate')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={!!deletingId} onOpenChange={() => setDeletingId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('assets.confirmDeleteTitle')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{t('assets.confirmDelete')}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeletingId(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => deletingId && deleteMutation.mutate(deletingId)}
              disabled={deleteMutation.isPending}
            >
              {t('common.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

const PORTFOLIO_COLORS = ['#6366F1', '#F43F5E', '#F59E0B', '#10B981', '#8B5CF6', '#EC4899', '#06B6D4', '#84CC16']

function PortfolioChart({ data, currency, locale: loc, mask }: {
  data: { assets: { id: string; name: string; type: string }[]; trend: Record<string, unknown>[]; total: number }
  currency: string
  locale: string
  mask: (v: string) => string
}) {
  const { t } = useTranslation()

  const formatCompact = (v: number) => {
    const abs = Math.abs(v)
    if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
    if (abs >= 1_000) return `${(v / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`
    return v.toLocaleString(loc, { maximumFractionDigits: 0 })
  }

  return (
    <div className="border border-border rounded-xl bg-card shadow-sm p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-foreground">{t('assets.portfolioValue')}</h3>
        <div className="text-right">
          <span className="text-xs text-muted-foreground">{t('assets.total')}</span>
          <p className="text-lg font-bold tabular-nums text-foreground">
            {mask(formatCurrency(data.total, currency, loc))}
          </p>
        </div>
      </div>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data.trend} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
            <defs>
              {data.assets.map((asset, i) => (
                <linearGradient key={asset.id} id={`portfolio-grad-${asset.id}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={PORTFOLIO_COLORS[i % PORTFOLIO_COLORS.length]} stopOpacity={0.5} />
                  <stop offset="100%" stopColor={PORTFOLIO_COLORS[i % PORTFOLIO_COLORS.length]} stopOpacity={0.1} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" strokeOpacity={0.5} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v: string) => new Date(v + 'T00:00:00').toLocaleDateString(loc, { month: 'short', year: '2-digit' })}
            />
            <YAxis
              tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
              axisLine={false}
              tickLine={false}
              width={56}
              tickFormatter={(v: number) => mask(formatCompact(v))}
            />
            <RechartsTooltip
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null
                const totalEntry = payload.find(p => p.dataKey === '_total')
                const dateTotal = totalEntry?.value as number ?? 0
                // Show real asset values (from original keys, not _neg_ keys)
                const items = data.assets
                  .map((a, i) => {
                    const row = data.trend.find(r => r.date === label)
                    const val = row ? (row[a.id] as number ?? 0) : 0
                    return { id: a.id, name: a.name, value: val, color: PORTFOLIO_COLORS[i % PORTFOLIO_COLORS.length] }
                  })
                  .filter(item => item.value !== 0)
                  .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
                if (items.length === 0) return null
                return (
                  <div style={{ background: 'var(--card)', color: 'var(--foreground)', border: '1px solid var(--border)', borderRadius: '0.75rem', fontSize: '12px', boxShadow: '0 4px 12px rgba(0,0,0,0.08)', padding: '10px 12px' }}>
                    <p style={{ fontWeight: 600, marginBottom: 6 }}>
                      {new Date(label + 'T00:00:00').toLocaleDateString(loc, { day: 'numeric', month: 'long', year: 'numeric' })}
                    </p>
                    {items.map(item => (
                      <div key={item.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 2 }}>
                        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: item.color, display: 'inline-block' }} />
                          {item.name}
                        </span>
                        <span style={{ fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>{mask(formatCurrency(item.value, currency, loc))}</span>
                      </div>
                    ))}
                    <div style={{ borderTop: '1px solid var(--border)', marginTop: 6, paddingTop: 6, display: 'flex', justifyContent: 'space-between', fontWeight: 700 }}>
                      <span>{t('assets.total')}</span>
                      <span style={{ fontVariantNumeric: 'tabular-nums' }}>{mask(formatCurrency(dateTotal, currency, loc))}</span>
                    </div>
                  </div>
                )
              }}
            />
            {/* Stacked areas — each asset is a colored band */}
            {data.assets.map((asset, i) => (
              <Area
                key={asset.id}
                type="monotone"
                dataKey={asset.id}
                stackId="portfolio"
                stroke={PORTFOLIO_COLORS[i % PORTFOLIO_COLORS.length]}
                strokeWidth={1}
                fill={`url(#portfolio-grad-${asset.id})`}
                dot={false}
                activeDot={{ r: 3, strokeWidth: 1.5, fill: 'var(--card)' }}
              />
            ))}
            {/* Hidden total for tooltip */}
            <Area dataKey="_total" stroke="none" fill="none" dot={false} activeDot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {/* Legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 px-1">
        {data.assets.map((asset, i) => (
          <div key={asset.id} className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: PORTFOLIO_COLORS[i % PORTFOLIO_COLORS.length] }} />
            <span className="text-[11px] text-muted-foreground">{asset.name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function AssetDetail({ assetId, currency, locale: loc, purchasePrice, purchaseDate, valuationMethod }: {
  assetId: string; currency: string; locale: string
  purchasePrice: number | null; purchaseDate: string | null
  valuationMethod: string
}) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const queryClient = useQueryClient()

  const [valueAmount, setValueAmount] = useState('')
  const [valueDate, setValueDate] = useState(new Date().toISOString().slice(0, 10))

  const { data: values, isLoading: valuesLoading } = useQuery({
    queryKey: ['asset-values', assetId],
    queryFn: () => assets.values(assetId),
  })

  const { data: trend } = useQuery({
    queryKey: ['asset-trend', assetId],
    queryFn: () => assets.valueTrend(assetId),
  })

  // Build full trend: purchase point + stored values
  const trendWithPurchase = useMemo(() => {
    if (!trend) return []
    let result = [...trend]

    // Prepend purchase point if it predates the first value
    if (purchasePrice && purchaseDate) {
      if (result.length === 0 || purchaseDate < result[0].date) {
        result = [{ date: purchaseDate, amount: purchasePrice }, ...result]
      }
    }

    return result
  }, [trend, purchasePrice, purchaseDate])

  // Build value history with purchase as the initial entry
  const valuesWithPurchase = useMemo(() => {
    if (!values) return []
    if (!purchasePrice || !purchaseDate) return values
    const hasPurchaseValue = values.some(v => v.date === purchaseDate && v.amount === purchasePrice)
    if (hasPurchaseValue) return values
    const purchaseEntry: AssetValue = {
      id: 'purchase',
      asset_id: assetId,
      amount: purchasePrice,
      date: purchaseDate,
      source: 'purchase',
    }
    return [...values, purchaseEntry]
  }, [values, purchasePrice, purchaseDate, assetId])

  const addValueMutation = useMutation({
    mutationFn: ({ assetId: id, ...data }: { assetId: string; amount: number; date: string }) =>
      assets.addValue(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset-values', assetId] })
      queryClient.invalidateQueries({ queryKey: ['asset-trend', assetId] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      setValueAmount('')
      toast.success(t('assets.valueAdded'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteValueMutation = useMutation({
    mutationFn: (valueId: string) => assets.deleteValue(valueId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset-values', assetId] })
      queryClient.invalidateQueries({ queryKey: ['asset-trend', assetId] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      toast.success(t('assets.valueDeleted'))
    },
    onError: () => toast.error(t('common.error')),
  })

  // Determine chart color based on trend direction
  const trendIsPositive = trendWithPurchase.length >= 2
    ? trendWithPurchase[trendWithPurchase.length - 1].amount >= trendWithPurchase[0].amount
    : true
  const chartColor = trendIsPositive ? '#10B981' : '#F43F5E'

  return (
    <div className="border-t border-border px-5 py-5 space-y-5 bg-muted/5">
      {/* Value Trend Chart */}
      {trendWithPurchase.length > 1 && (
        <div>
          <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider mb-3">{t('assets.valueTrend')}</p>
          <div className="h-44 -mx-1">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trendWithPurchase} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id={`gradient-${assetId}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={chartColor} stopOpacity={0.2} />
                    <stop offset="100%" stopColor={chartColor} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" strokeOpacity={0.5} />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v: string) => new Date(v + 'T00:00:00').toLocaleDateString(loc, { month: 'short', year: '2-digit' })}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                  axisLine={false}
                  tickLine={false}
                  width={56}
                  domain={['dataMin', 'dataMax']}
                  tickFormatter={(v: number) => {
                    const abs = Math.abs(v)
                    let formatted: string
                    if (abs >= 1_000_000) formatted = `${(v / 1_000_000).toFixed(1)}M`
                    else if (abs >= 1_000) formatted = `${(v / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`
                    else formatted = v.toLocaleString(loc, { maximumFractionDigits: 0 })
                    return mask(formatted)
                  }}
                />
                <RechartsTooltip
                  formatter={(value: number | undefined) => [mask(formatCurrency(value ?? 0, currency, loc)), t('assets.currentValue')]}
                  labelFormatter={(label: unknown) => new Date(String(label) + 'T00:00:00').toLocaleDateString(loc, { day: 'numeric', month: 'long', year: 'numeric' })}
                  contentStyle={{
                    background: 'var(--card)',
                    color: 'var(--foreground)',
                    border: '1px solid var(--border)',
                    borderRadius: '0.75rem',
                    fontSize: '12px',
                    boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="amount"
                  stroke={chartColor}
                  strokeWidth={2}
                  fill={`url(#gradient-${assetId})`}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 2, fill: 'var(--card)', stroke: chartColor }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Add Value Form — only for manual assets */}
      {valuationMethod === 'manual' && <div className="flex items-end gap-2">
        <div className="flex-1">
          <Label className="text-[11px] text-muted-foreground">{t('assets.amount')}</Label>
          <Input
            type="number"
            step="any"
            value={valueAmount}
            onChange={e => setValueAmount(e.target.value)}
            placeholder="0.00"
            className="h-8 text-sm"
          />
        </div>
        <div className="w-36">
          <Label className="text-[11px] text-muted-foreground">{t('assets.date')}</Label>
          <DatePickerInput value={valueDate} onChange={setValueDate} />
        </div>
        <Button
          size="sm"
          className="h-8 px-3 text-xs"
          disabled={!valueAmount || addValueMutation.isPending}
          onClick={() => {
            if (valueAmount) {
              addValueMutation.mutate({
                assetId,
                amount: parseFloat(valueAmount),
                date: valueDate,
              })
            }
          }}
        >
          <Plus size={14} className="mr-1" />
          {t('assets.addValue')}
        </Button>
      </div>}

      {/* Value History */}
      <div>
        <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider mb-2">{t('assets.valueHistory')}</p>
        {valuesLoading ? (
          <Skeleton className="h-20 w-full rounded-lg" />
        ) : valuesWithPurchase.length > 0 ? (
          <div className="rounded-lg border border-border overflow-hidden divide-y divide-border">
            {valuesWithPurchase.map((v: AssetValue, idx: number) => {
              const isPurchase = v.source === 'purchase'
              // Calculate change from previous entry (next in array since sorted desc)
              const prev = valuesWithPurchase[idx + 1]
              const change = prev ? v.amount - prev.amount : null
              const changePct = prev && prev.amount !== 0 ? (change! / prev.amount) * 100 : null

              return (
                <div key={v.id} className={`flex items-center justify-between py-2 px-3 transition-colors ${isPurchase ? 'bg-primary/5' : 'hover:bg-muted/30'}`}>
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-sm tabular-nums font-semibold text-foreground">
                      {mask(formatCurrency(v.amount, currency, loc))}
                    </span>
                    {change != null && (
                      <span className={`text-[11px] tabular-nums font-medium ${change >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
                        {change >= 0 ? '+' : ''}{mask(formatCurrency(change, currency, loc))}
                        {changePct != null && ` (${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%)`}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Badge variant={isPurchase ? 'default' : 'outline'} className={`text-[10px] px-1.5 py-0 ${isPurchase ? 'bg-primary/15 text-primary border-primary/30' : ''}`}>
                      {t(`assets.source${v.source.charAt(0).toUpperCase() + v.source.slice(1)}`)}
                    </Badge>
                    <span className="text-[11px] text-muted-foreground tabular-nums">
                      {new Date(v.date + 'T00:00:00').toLocaleDateString(loc)}
                    </span>
                    {valuationMethod === 'manual' && v.source === 'manual' && (
                      <button
                        onClick={() => deleteValueMutation.mutate(v.id)}
                        className="p-1 rounded text-muted-foreground/40 hover:text-rose-600 transition-colors"
                        disabled={deleteValueMutation.isPending}
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground py-3 text-center">{t('dashboard.noData')}</p>
        )}
      </div>
    </div>
  )
}
