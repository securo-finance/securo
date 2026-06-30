import { useEffect, useMemo, useRef, useState, useCallback, useSyncExternalStore } from 'react'
import { Command } from 'cmdk'
import { Dialog as DialogPrimitive } from 'radix-ui'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { useQuery } from '@tanstack/react-query'
import {
  Search,
  CornerDownLeft,
  LayoutDashboard,
  ArrowLeftRight,
  Building2,
  Upload,
  SlidersHorizontal,
  Tag,
  PiggyBank,
  Target,
  Repeat,
  Landmark,
  Users,
  BarChart3,
  Plus,
  Receipt,
  CircleDollarSign,
  Wallet,
  Flame,
  FileSpreadsheet,
  History,
  ArrowUpRight,
  Zap,
  Compass,
} from 'lucide-react'

import { search as searchApi, type SearchHit, type SearchHitType } from '@/lib/api'
import { cn, normalizeText } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Static actions & navigation registry
// ---------------------------------------------------------------------------

type StaticItem = {
  id: string
  labelKey: string
  icon: React.ElementType
  path?: string
  onSelect?: (nav: (to: string) => void) => void
  keywords?: string[]
}

const NAV_ITEMS: StaticItem[] = [
  { id: 'nav-dashboard', labelKey: 'nav.dashboard', icon: LayoutDashboard, path: '/', keywords: ['home', 'inicio', 'início'] },
  { id: 'nav-transactions', labelKey: 'nav.transactions', icon: ArrowLeftRight, path: '/transactions', keywords: ['tx', 'transacoes', 'transações'] },
  { id: 'nav-accounts', labelKey: 'nav.accounts', icon: Building2, path: '/accounts', keywords: ['contas'] },
  { id: 'nav-import', labelKey: 'nav.import', icon: Upload, path: '/import', keywords: ['csv', 'ofx', 'importar'] },
  { id: 'nav-reports', labelKey: 'nav.reports', icon: BarChart3, path: '/reports', keywords: ['relatorios', 'relatórios', 'charts'] },
  { id: 'nav-assets', labelKey: 'nav.assets', icon: Landmark, path: '/assets', keywords: ['patrimonio', 'patrimônio'] },
  { id: 'nav-budgets', labelKey: 'nav.budgets', icon: PiggyBank, path: '/budgets', keywords: ['orcamentos', 'orçamentos'] },
  { id: 'nav-goals', labelKey: 'nav.goals', icon: Target, path: '/goals', keywords: ['metas'] },
  { id: 'nav-recurring', labelKey: 'nav.recurring', icon: Repeat, path: '/recurring', keywords: ['recorrentes'] },
  { id: 'nav-categories', labelKey: 'nav.categories', icon: Tag, path: '/categories', keywords: ['categorias'] },
  { id: 'nav-payees', labelKey: 'nav.payees', icon: Users, path: '/payees', keywords: ['beneficiarios', 'beneficiários'] },
  { id: 'nav-rules', labelKey: 'nav.rules', icon: SlidersHorizontal, path: '/rules', keywords: ['regras'] },
]

const QUICK_ACTIONS: StaticItem[] = [
  {
    id: 'action-new-transaction',
    labelKey: 'cmdk.actions.newTransaction',
    icon: Plus,
    onSelect: (nav) => nav('/transactions?new=1'),
    keywords: ['new', 'add', 'create', 'nova', 'adicionar'],
  },
  {
    id: 'action-import',
    labelKey: 'cmdk.actions.importFile',
    icon: FileSpreadsheet,
    onSelect: (nav) => nav('/import'),
    keywords: ['upload', 'csv', 'ofx', 'qif'],
  },
  {
    id: 'action-new-budget',
    labelKey: 'cmdk.actions.newBudget',
    icon: PiggyBank,
    onSelect: (nav) => nav('/budgets?new=1'),
    keywords: ['budget', 'orcamento'],
  },
  {
    id: 'action-new-goal',
    labelKey: 'cmdk.actions.newGoal',
    icon: Target,
    onSelect: (nav) => nav('/goals?new=1'),
    keywords: ['goal', 'meta', 'target'],
  },
  {
    id: 'action-reports',
    labelKey: 'cmdk.actions.openReports',
    icon: BarChart3,
    onSelect: (nav) => nav('/reports'),
  },
]

// ---------------------------------------------------------------------------
// Entity type → icon + accent color
// ---------------------------------------------------------------------------

const ENTITY_META: Record<SearchHitType, { icon: React.ElementType; tintClass: string; bgClass: string; labelKey: string; pathFor: (hit: SearchHit) => string }> = {
  transaction: {
    icon: Receipt,
    tintClass: 'text-indigo-500 dark:text-indigo-300',
    bgClass: 'bg-indigo-500/10',
    labelKey: 'cmdk.groups.transactions',
    pathFor: (hit) => {
      const params = new URLSearchParams()
      if (hit.label) params.set('q', hit.label)
      params.set('highlight', hit.id)
      return `/transactions?${params.toString()}`
    },
  },
  account: {
    icon: Wallet,
    tintClass: 'text-emerald-500 dark:text-emerald-300',
    bgClass: 'bg-emerald-500/10',
    labelKey: 'cmdk.groups.accounts',
    pathFor: (hit) => `/accounts/${hit.id}`,
  },
  payee: {
    icon: Users,
    tintClass: 'text-sky-500 dark:text-sky-300',
    bgClass: 'bg-sky-500/10',
    labelKey: 'cmdk.groups.payees',
    pathFor: (hit) => `/transactions?payee_id=${hit.id}`,
  },
  category: {
    icon: Tag,
    tintClass: 'text-fuchsia-500 dark:text-fuchsia-300',
    bgClass: 'bg-fuchsia-500/10',
    labelKey: 'cmdk.groups.categories',
    pathFor: (hit) => `/transactions?category_id=${hit.id}`,
  },
  goal: {
    icon: Target,
    tintClass: 'text-amber-500 dark:text-amber-300',
    bgClass: 'bg-amber-500/10',
    labelKey: 'cmdk.groups.goals',
    pathFor: () => '/goals',
  },
  asset: {
    icon: Landmark,
    tintClass: 'text-rose-500 dark:text-rose-300',
    bgClass: 'bg-rose-500/10',
    labelKey: 'cmdk.groups.assets',
    pathFor: () => '/assets',
  },
}

// ---------------------------------------------------------------------------
// Recent items (localStorage)
// ---------------------------------------------------------------------------

type RecentItem = {
  id: string
  label: string
  path: string
  icon: string // lucide name, stored as string; resolved via fallback icon
  sublabel?: string | null
}

const RECENT_KEY = 'securo.cmdk.recent'
const RECENT_MAX = 5

function loadRecents(): RecentItem[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.slice(0, RECENT_MAX)
  } catch {
    return []
  }
}

// Tiny external store so we can read `localStorage` recents through
// useSyncExternalStore and avoid setState-in-effect lint warnings.
type Listener = () => void
const recentListeners = new Set<Listener>()
let recentSnapshot: RecentItem[] = loadRecents()

function subscribeRecents(listener: Listener) {
  recentListeners.add(listener)
  return () => {
    recentListeners.delete(listener)
  }
}

function getRecentSnapshot(): RecentItem[] {
  return recentSnapshot
}

function saveRecent(item: RecentItem) {
  const existing = recentSnapshot.filter((r) => r.id !== item.id)
  const next = [item, ...existing].slice(0, RECENT_MAX)
  recentSnapshot = next
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next))
  } catch {
    // ignore
  }
  recentListeners.forEach((l) => l())
}

// ---------------------------------------------------------------------------
// Amount formatting helper
// ---------------------------------------------------------------------------

function formatHitAmount(amount: number | null, currency: string | null, locale: string): string | null {
  if (amount === null || amount === undefined) return null
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: currency ?? 'USD',
      maximumFractionDigits: 2,
    }).format(amount)
  } catch {
    return `${amount}`
  }
}

function formatHitDate(iso: string | null, locale: string): string | null {
  if (!iso) return null
  try {
    const d = new Date(iso)
    return new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric' }).format(d)
  } catch {
    return iso
  }
}


function matchesQuery(query: string, haystacks: Array<string | undefined>): boolean {
  const q = normalizeText(query.trim())
  if (!q) return true
  return haystacks.some((h) => (h ? normalizeText(h).includes(q) : false))
}

// ---------------------------------------------------------------------------
// The palette
// ---------------------------------------------------------------------------

export interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const recents = useSyncExternalStore(subscribeRecents, getRecentSnapshot, getRecentSnapshot)
  const inputRef = useRef<HTMLInputElement>(null)
  const locale = useDisplayLocale()

  // Focus the input whenever the palette opens. The cmdk Command is re-keyed
  // on `open` so results and selection reset automatically. We reset our own
  // query/debounced state in the close handler instead of here to avoid
  // calling setState inside an effect.
  useEffect(() => {
    if (open) {
      const id = setTimeout(() => inputRef.current?.focus(), 10)
      return () => clearTimeout(id)
    }
  }, [open])

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) {
        setQuery('')
        setDebounced('')
      }
      onOpenChange(next)
    },
    [onOpenChange]
  )

  // Debounce query → debounced (150ms)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(query), 150)
    return () => clearTimeout(id)
  }, [query])

  const { data: searchResults = [], isFetching } = useQuery({
    queryKey: ['search', debounced],
    queryFn: () => searchApi.query(debounced, 5),
    enabled: open && debounced.trim().length > 0,
    staleTime: 30_000,
  })

  // Group hits by entity type
  const grouped = useMemo(() => {
    const groups = new Map<SearchHitType, SearchHit[]>()
    for (const hit of searchResults) {
      const arr = groups.get(hit.type) ?? []
      arr.push(hit)
      groups.set(hit.type, arr)
    }
    return groups
  }, [searchResults])

  // Client-side filtered nav + quick actions so typing "regras" or "nova"
  // narrows the in-app items alongside the backend entity search.
  const filteredNavItems = useMemo(() => {
    if (debounced.trim().length === 0) return NAV_ITEMS
    return NAV_ITEMS.filter((n) =>
      matchesQuery(debounced, [t(n.labelKey), n.path, ...(n.keywords ?? [])])
    )
  }, [debounced, t])

  const filteredQuickActions = useMemo(() => {
    if (debounced.trim().length === 0) return QUICK_ACTIONS
    return QUICK_ACTIONS.filter((a) =>
      matchesQuery(debounced, [t(a.labelKey), ...(a.keywords ?? [])])
    )
  }, [debounced, t])

  const runAndClose = useCallback(
    (item: { id: string; label: string; path: string; iconName: string; sublabel?: string | null }) => {
      saveRecent({
        id: item.id,
        label: item.label,
        path: item.path,
        icon: item.iconName,
        sublabel: item.sublabel,
      })
      handleOpenChange(false)
      // Slight delay so dialog closes before navigation animation
      setTimeout(() => navigate(item.path), 0)
    },
    [navigate, handleOpenChange]
  )

  const showEmptyHome = debounced.trim().length === 0
  const hasAnyResults = searchResults.length > 0
  const hasAnyLocalMatch =
    filteredNavItems.length > 0 || filteredQuickActions.length > 0
  const showEmptyState = !showEmptyHome && !hasAnyResults && !hasAnyLocalMatch

  return (
    <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            'fixed inset-0 z-50 backdrop-blur-[3px] bg-background/40',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0'
          )}
        />
        <DialogPrimitive.Content
          className={cn(
            'fixed left-1/2 top-[22%] z-50 w-[92vw] max-w-[640px] -translate-x-1/2',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0',
            'data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95',
            'data-[state=open]:slide-in-from-top-4 data-[state=closed]:slide-out-to-top-4',
            'duration-150 outline-none'
          )}
        >
          <DialogPrimitive.Title className="sr-only">{t('cmdk.title')}</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">{t('cmdk.description')}</DialogPrimitive.Description>

          <Command
            key={open ? 'open' : 'closed'}
            shouldFilter={false}
            className={cn(
              'relative overflow-hidden rounded-2xl border border-border/80 bg-card',
              'shadow-[0_30px_80px_-20px_rgba(15,23,42,0.35)] dark:shadow-[0_30px_80px_-20px_rgba(0,0,0,0.8)]',
              'ring-1 ring-black/[0.02] dark:ring-white/[0.04]'
            )}
          >
            {/* Decorative gradient rim */}
            <div
              aria-hidden
              className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent"
            />

            {/* Input row */}
            <div className="flex items-center gap-3 border-b border-border/60 px-4 py-3.5">
              <Search size={17} className="shrink-0 text-muted-foreground" />
              <Command.Input
                ref={inputRef}
                value={query}
                onValueChange={setQuery}
                placeholder={t('cmdk.placeholder')}
                className={cn(
                  'flex-1 bg-transparent text-[14.5px] text-foreground outline-none',
                  'placeholder:text-muted-foreground/70',
                  'caret-primary'
                )}
              />
              {isFetching && debounced && (
                <div className="h-3 w-3 shrink-0 animate-spin rounded-full border border-muted-foreground/30 border-t-primary" />
              )}
              <kbd className="hidden sm:inline-flex h-5 items-center rounded border border-border/80 bg-muted/60 px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
                ESC
              </kbd>
            </div>

            {/* Scrollable results area */}
            <Command.List
              className={cn(
                'max-h-[min(480px,60vh)] overflow-y-auto overscroll-contain px-2 py-2',
                'scrollbar-thin'
              )}
            >
              {/* HOME (no query): recents → quick actions → navigation */}
              {showEmptyHome ? (
                <>
                  {recents.length > 0 && (
                    <Group
                      icon={<History size={11} />}
                      labelKey="cmdk.groups.recent"
                    >
                      {recents.map((r) => (
                        <Command.Item
                          key={`recent-${r.id}`}
                          value={`recent ${r.label}`}
                          onSelect={() =>
                            runAndClose({
                              id: r.id,
                              label: r.label,
                              path: r.path,
                              iconName: r.icon,
                              sublabel: r.sublabel,
                            })
                          }
                          className={itemClasses()}
                        >
                          <ItemIcon tintClass="text-muted-foreground" bgClass="bg-muted/60">
                            <Compass size={14} />
                          </ItemIcon>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[13.5px] text-foreground">{r.label}</div>
                            {r.sublabel && (
                              <div className="truncate text-[11.5px] text-muted-foreground">{r.sublabel}</div>
                            )}
                          </div>
                          <ArrowUpRight size={13} className="text-muted-foreground/50 group-data-[selected=true]:text-primary" />
                        </Command.Item>
                      ))}
                    </Group>
                  )}

                  {filteredQuickActions.length > 0 && (
                    <Group icon={<Zap size={11} />} labelKey="cmdk.groups.quickActions">
                      {filteredQuickActions.map((a) => {
                        const Icon = a.icon
                        return (
                          <Command.Item
                            key={a.id}
                            value={`action ${t(a.labelKey)} ${(a.keywords ?? []).join(' ')}`}
                            onSelect={() => {
                              if (a.onSelect) {
                                handleOpenChange(false)
                                setTimeout(() => a.onSelect!(navigate), 0)
                              }
                            }}
                            className={itemClasses()}
                          >
                            <ItemIcon tintClass="text-primary" bgClass="bg-primary/10">
                              <Icon size={14} />
                            </ItemIcon>
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-[13.5px] text-foreground">{t(a.labelKey)}</div>
                            </div>
                            <CornerDownLeft size={12} className="text-muted-foreground/40 group-data-[selected=true]:text-primary" />
                          </Command.Item>
                        )
                      })}
                    </Group>
                  )}

                  {filteredNavItems.length > 0 && (
                    <Group icon={<Compass size={11} />} labelKey="cmdk.groups.navigation">
                      {filteredNavItems.map((n) => {
                        const Icon = n.icon
                        return (
                          <Command.Item
                            key={n.id}
                            value={`nav ${t(n.labelKey)} ${(n.keywords ?? []).join(' ')}`}
                            onSelect={() =>
                              runAndClose({
                                id: n.id,
                                label: t(n.labelKey),
                                path: n.path!,
                                iconName: 'compass',
                              })
                            }
                            className={itemClasses()}
                          >
                            <ItemIcon tintClass="text-muted-foreground" bgClass="bg-muted/60">
                              <Icon size={14} />
                            </ItemIcon>
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-[13.5px] text-foreground">{t(n.labelKey)}</div>
                              <div className="truncate text-[11px] text-muted-foreground/80">{n.path}</div>
                            </div>
                          </Command.Item>
                        )
                      })}
                    </Group>
                  )}
                </>
              ) : (
                /* SEARCH (query active): entities FIRST, then actions, then nav */
                <>
                  {showEmptyState && !isFetching && (
                    <Command.Empty className="px-3 py-10 text-center">
                      <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-muted/60">
                        <Flame size={15} className="text-muted-foreground" />
                      </div>
                      <p className="text-[13px] font-medium text-foreground">
                        {t('cmdk.empty.title', { query: debounced })}
                      </p>
                      <p className="mt-1 text-[12px] text-muted-foreground">{t('cmdk.empty.hint')}</p>
                    </Command.Empty>
                  )}

                  {/* Entity results — highest priority when searching */}
                  {(['transaction', 'account', 'payee', 'category', 'goal', 'asset'] as SearchHitType[]).map((type) => {
                    const items = grouped.get(type) ?? []
                    if (items.length === 0) return null
                    const meta = ENTITY_META[type]
                    const GroupIcon = meta.icon
                    return (
                      <Group
                        key={type}
                        icon={<GroupIcon size={11} />}
                        labelKey={meta.labelKey}
                      >
                        {items.map((hit) => {
                          const amount = formatHitAmount(hit.amount, hit.currency, locale)
                          const dateLabel = formatHitDate(hit.date, locale)
                          const isPositive = (hit.amount ?? 0) > 0 && hit.type === 'transaction' && (hit.meta?.tx_type === 'credit' || hit.amount! > 0)
                          const isNegative = hit.type === 'transaction' && hit.amount !== null && (hit.meta?.tx_type === 'debit' || hit.amount! < 0)
                          return (
                            <Command.Item
                              key={`${hit.type}-${hit.id}`}
                              value={`${hit.type} ${hit.label} ${hit.subtitle ?? ''}`}
                              onSelect={() =>
                                runAndClose({
                                  id: `${hit.type}-${hit.id}`,
                                  label: hit.label,
                                  path: meta.pathFor(hit),
                                  iconName: hit.type,
                                  sublabel: hit.subtitle,
                                })
                              }
                              className={itemClasses()}
                            >
                              <ItemIcon tintClass={meta.tintClass} bgClass={meta.bgClass} style={hit.color ? { backgroundColor: `${hit.color}1A`, color: hit.color } : undefined}>
                                <GroupIcon size={14} />
                              </ItemIcon>
                              <div className="min-w-0 flex-1">
                                <div className="truncate text-[13.5px] text-foreground">{hit.label}</div>
                                {hit.subtitle && (
                                  <div className="truncate text-[11.5px] text-muted-foreground/90">
                                    {hit.subtitle}
                                    {dateLabel && <span className="mx-1.5 text-muted-foreground/40">•</span>}
                                    {dateLabel}
                                  </div>
                                )}
                              </div>
                              {amount && (
                                <div
                                  className={cn(
                                    'shrink-0 tabular-nums text-[12.5px] font-medium',
                                    isNegative && 'text-rose-500 dark:text-rose-400',
                                    isPositive && 'text-emerald-500 dark:text-emerald-400',
                                    !isNegative && !isPositive && 'text-muted-foreground'
                                  )}
                                >
                                  {amount}
                                </div>
                              )}
                            </Command.Item>
                          )
                        })}
                      </Group>
                    )
                  })}

                  {/* Quick actions — lower priority fallback while searching */}
                  {filteredQuickActions.length > 0 && (
                    <Group icon={<Zap size={11} />} labelKey="cmdk.groups.quickActions">
                      {filteredQuickActions.map((a) => {
                        const Icon = a.icon
                        return (
                          <Command.Item
                            key={a.id}
                            value={`action ${t(a.labelKey)} ${(a.keywords ?? []).join(' ')}`}
                            onSelect={() => {
                              if (a.onSelect) {
                                handleOpenChange(false)
                                setTimeout(() => a.onSelect!(navigate), 0)
                              }
                            }}
                            className={itemClasses()}
                          >
                            <ItemIcon tintClass="text-primary" bgClass="bg-primary/10">
                              <Icon size={14} />
                            </ItemIcon>
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-[13.5px] text-foreground">{t(a.labelKey)}</div>
                            </div>
                            <CornerDownLeft size={12} className="text-muted-foreground/40 group-data-[selected=true]:text-primary" />
                          </Command.Item>
                        )
                      })}
                    </Group>
                  )}

                  {/* Navigation — lowest priority fallback while searching */}
                  {filteredNavItems.length > 0 && (
                    <Group icon={<Compass size={11} />} labelKey="cmdk.groups.navigation">
                      {filteredNavItems.map((n) => {
                        const Icon = n.icon
                        return (
                          <Command.Item
                            key={n.id}
                            value={`nav ${t(n.labelKey)} ${(n.keywords ?? []).join(' ')}`}
                            onSelect={() =>
                              runAndClose({
                                id: n.id,
                                label: t(n.labelKey),
                                path: n.path!,
                                iconName: 'compass',
                              })
                            }
                            className={itemClasses()}
                          >
                            <ItemIcon tintClass="text-muted-foreground" bgClass="bg-muted/60">
                              <Icon size={14} />
                            </ItemIcon>
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-[13.5px] text-foreground">{t(n.labelKey)}</div>
                              <div className="truncate text-[11px] text-muted-foreground/80">{n.path}</div>
                            </div>
                          </Command.Item>
                        )
                      })}
                    </Group>
                  )}
                </>
              )}
            </Command.List>

            {/* Footer */}
            <div className="flex items-center justify-between gap-4 border-t border-border/60 bg-muted/30 px-4 py-2">
              <div className="flex items-center gap-1.5 text-[10.5px] text-muted-foreground">
                <CircleDollarSign size={11} className="text-primary" />
                <span className="font-semibold tracking-tight text-foreground/90">Securo</span>
                <span className="text-muted-foreground/50">/</span>
                <span>{t('cmdk.footer.tagline')}</span>
              </div>
              <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                <KbdHint>
                  <Kbd>↑</Kbd>
                  <Kbd>↓</Kbd>
                  <span>{t('cmdk.footer.navigate')}</span>
                </KbdHint>
                <KbdHint>
                  <Kbd>
                    <CornerDownLeft size={9} />
                  </Kbd>
                  <span>{t('cmdk.footer.open')}</span>
                </KbdHint>
                <KbdHint>
                  <Kbd>esc</Kbd>
                  <span>{t('cmdk.footer.close')}</span>
                </KbdHint>
              </div>
            </div>
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

function Group({
  icon,
  labelKey,
  children,
}: {
  icon: React.ReactNode
  labelKey: string
  children: React.ReactNode
}) {
  const { t } = useTranslation()
  return (
    <Command.Group
      heading={
        <div className="flex items-center gap-1.5 px-3 pt-3 pb-1.5">
          <span className="text-muted-foreground/60">{icon}</span>
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
            {t(labelKey)}
          </span>
        </div>
      }
      className="mb-1"
    >
      {children}
    </Command.Group>
  )
}

function itemClasses() {
  return cn(
    'group flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm',
    'transition-colors',
    'data-[selected=true]:bg-primary/[0.08] data-[selected=true]:text-foreground',
    'data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50',
    'hover:bg-muted/40'
  )
}

function ItemIcon({
  tintClass,
  bgClass,
  style,
  children,
}: {
  tintClass: string
  bgClass: string
  style?: React.CSSProperties
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        'flex h-7 w-7 shrink-0 items-center justify-center rounded-md',
        bgClass,
        tintClass
      )}
      style={style}
    >
      {children}
    </div>
  )
}

function KbdHint({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center gap-1">{children}</div>
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      className={cn(
        'inline-flex h-[17px] min-w-[17px] items-center justify-center rounded border border-border/80',
        'bg-background px-1 font-mono text-[9.5px] font-medium text-muted-foreground/90',
        'shadow-[0_1px_0_rgba(0,0,0,0.05)]'
      )}
    >
      {children}
    </kbd>
  )
}

