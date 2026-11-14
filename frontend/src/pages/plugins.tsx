import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Search, ShieldCheck, Star, X } from 'lucide-react'

import { PageHeader } from '@/components/page-header'
import { PluginAvatar } from '@/components/plugin-avatar'
import { PluginStatus, PluginTypes } from '@/components/plugin-meta'
import { usePluginSummary } from '@/hooks/use-plugin-summary'
import { PluginPlanDialog, type PluginTarget } from '@/components/plugin-plan-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useWorkspace } from '@/contexts/workspace-context'
import { plugins as pluginsApi } from '@/lib/api'
import { localized } from '@/lib/localized-text'
import {
  NO_FILTERS,
  PLUGIN_TYPES,
  type PluginFilters,
  type PluginSort,
  contributedKinds,
  filterPlugins,
  hasFilters,
  newPlugins,
  pluginLocales,
  sortPlugins,
  trendingPlugins,
} from '@/lib/plugin-catalog'
import { compactNumber, languageName, pluginPath, relativeDate } from '@/lib/plugin-format'
import { cn } from '@/lib/utils'
import type { PluginAction, PluginCatalogEntry } from '@/types'

type View = 'all' | 'trending' | 'new'
type FacetKey = 'type' | 'kind' | 'locale' | 'status'

const SORTS: PluginSort[] = ['popular', 'trending', 'updated', 'newest', 'name']
const STATUSES = ['installed', 'updates'] as const

interface Facet {
  key: FacetKey
  title: string
  options: { value: string; label: string; count: number }[]
}

export default function PluginsPage() {
  const { t, i18n } = useTranslation()
  const lang = i18n.language
  const { canWrite } = useWorkspace()
  const searchRef = useRef<HTMLInputElement>(null)
  const [filters, setFilters] = useState<PluginFilters>(NO_FILTERS)
  const [view, setView] = useState<View>('all')
  const [sort, setSort] = useState<PluginSort>('popular')
  const [target, setTarget] = useState<PluginTarget | null>(null)

  // One request to the catalog, made because the user opened this page.
  const { data: entries, isFetching, isError, refetch } = useQuery({
    queryKey: ['plugins', 'catalog'],
    queryFn: pluginsApi.catalog,
    staleTime: 5 * 60 * 1000,
    retry: 1,
  })

  // "/" focuses the search, as in most catalogs, unless the user is typing.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const field = (event.target as HTMLElement | null)?.closest('input, textarea, select, [contenteditable]')
      if (event.key === '/' && !field) {
        event.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const list = useMemo(() => entries ?? [], [entries])
  const nameOf = (entry: PluginCatalogEntry) => localized(entry.name, lang)
  const update = (change: Partial<PluginFilters>) => setFilters((current) => ({ ...current, ...change }))
  const clearFacets = () => setFilters((current) => ({ ...NO_FILTERS, query: current.query }))
  const facetsActive = hasFilters({ ...filters, query: '' })

  // Each option counts what choosing it would show, given the other filters.
  const countWith = (change: Partial<PluginFilters>) => filterPlugins(list, { ...filters, ...change }).length
  const facets: Facet[] = [
    {
      key: 'type',
      title: t('plugins.page.facet.type'),
      options: PLUGIN_TYPES.map((type) => ({ value: type, label: t(`plugins.page.type.${type}`), count: countWith({ type }) })),
    },
    {
      key: 'kind',
      title: t('plugins.page.facet.content'),
      options: [...new Set(list.flatMap(contributedKinds))]
        .map((kind) => ({ value: kind, label: t(`plugins.kindName.${kind}`, { defaultValue: kind }), count: countWith({ kind }) }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    },
    {
      key: 'locale',
      title: t('plugins.page.facet.language'),
      options: [...new Set(list.flatMap(pluginLocales))]
        .map((locale) => ({ value: locale, label: languageName(locale, lang), count: countWith({ locale }) }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    },
    {
      key: 'status',
      title: t('plugins.page.facet.status'),
      options: STATUSES.map((status) => ({ value: status, label: t(`plugins.page.status.${status}`), count: countWith({ status }) })),
    },
  ]
  const choose = (key: FacetKey, value: string | null) => update({ [key]: value } as Partial<PluginFilters>)

  const filtered = filterPlugins(list, filters)
  const shown =
    view === 'trending' ? trendingPlugins(filtered, filtered.length)
      : view === 'new' ? newPlugins(filtered, filtered.length)
        : sortPlugins(filtered, sort, nameOf)
  const authors = new Set(list.map((entry) => entry.owner)).size
  const act = (entry: PluginCatalogEntry, action: PluginAction) => setTarget({ id: entry.id, name: nameOf(entry), action })

  const views: { value: View; label: string }[] = [
    { value: 'all', label: t('plugins.page.view.all') },
    { value: 'trending', label: t('plugins.page.view.trending') },
    { value: 'new', label: t('plugins.page.view.new') },
  ]
  const notice = (
    <p className="flex gap-2 text-xs leading-relaxed text-muted-foreground">
      <ShieldCheck size={14} className="mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
      {t('plugins.page.notice')}
    </p>
  )

  return (
    <div className="pb-12">
      <PageHeader
        section={t('plugins.page.section')}
        title={t('plugins.title')}
        action={
          entries && entries.length > 0 ? (
            <p className="text-sm text-muted-foreground">
              {t('plugins.page.count', { count: entries.length })} · {t('plugins.page.authors', { count: authors })}
            </p>
          ) : undefined
        }
      />

      <div className="relative mb-8">
        <Search size={18} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input
          ref={searchRef}
          value={filters.query}
          onChange={(event) => update({ query: event.target.value })}
          placeholder={t('plugins.page.search')}
          aria-label={t('plugins.page.search')}
          className="h-11 rounded-xl bg-card pl-11 pr-11 text-base shadow-xs md:text-sm"
        />
        {filters.query ? (
          <button
            type="button"
            onClick={() => update({ query: '' })}
            aria-label={t('plugins.page.clearSearch')}
            className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X size={16} />
          </button>
        ) : (
          <kbd className="pointer-events-none absolute right-3.5 top-1/2 hidden -translate-y-1/2 rounded-md border bg-muted px-1.5 font-mono text-xs leading-5 text-muted-foreground sm:block">
            /
          </kbd>
        )}
      </div>

      <div className="grid gap-x-10 gap-y-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <aside className="hidden lg:block">
          <div className="sticky top-6 space-y-7">
            {facets.map((facet) => (facet.options.length ? <FacetList key={facet.key} facet={facet} value={filters[facet.key]} onChoose={choose} /> : null))}
            {facetsActive && (
              <Button variant="ghost" size="sm" className="h-8 px-2 text-muted-foreground" onClick={clearFacets}>
                <X size={14} className="mr-1" />
                {t('plugins.page.clear')}
              </Button>
            )}
            <div className="border-t px-2 pt-5">{notice}</div>
          </div>
        </aside>

        <div className="min-w-0">
          <div className="mb-4 flex items-end justify-between gap-3 border-b">
            <div role="tablist" aria-label={t('plugins.title')} className="-mb-px flex gap-6">
              {views.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="tab"
                  aria-selected={view === option.value}
                  onClick={() => setView(option.value)}
                  className={cn(
                    'border-b-2 pb-2.5 text-sm font-medium transition-colors',
                    view === option.value
                      ? 'border-foreground text-foreground'
                      : 'border-transparent text-muted-foreground hover:text-foreground',
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
            {view === 'all' && (
              <Select value={sort} onValueChange={(value) => setSort(value as PluginSort)}>
                <SelectTrigger
                  size="sm"
                  aria-label={t('plugins.page.sortBy')}
                  className="mb-1.5 h-8 w-auto gap-1.5 border-none bg-transparent px-2 text-sm shadow-none hover:bg-muted dark:bg-transparent"
                >
                  <span className="hidden text-muted-foreground sm:inline">{t('plugins.page.sortBy')}</span>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end">
                  {SORTS.map((option) => (
                    <SelectItem key={option} value={option}>{t(`plugins.page.sort.${option}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          <div className="-mx-4 mb-4 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] lg:hidden">
            {facets.map((facet) =>
              facet.options.length ? <FacetSelect key={facet.key} facet={facet} value={filters[facet.key]} onChoose={choose} /> : null,
            )}
            {facetsActive && (
              <Button variant="ghost" size="sm" className="h-8 shrink-0 rounded-full text-muted-foreground" onClick={clearFacets}>
                <X size={14} className="mr-1" />
                {t('plugins.page.clear')}
              </Button>
            )}
          </div>

          {view !== 'all' && (
            <p className="mb-3 text-sm text-muted-foreground">
              {view === 'trending' ? t('plugins.page.trendingHint') : t('plugins.page.newestHint')}
            </p>
          )}

          {isError && !isFetching ? (
            <Empty>
              {t('plugins.catalogFailed')}
              <Button variant="outline" size="sm" onClick={() => void refetch()}>
                <RefreshCw size={14} className="mr-1" />
                {t('common.retry')}
              </Button>
            </Empty>
          ) : !entries ? (
            <div className="divide-y rounded-xl border bg-card">
              {Array.from({ length: 5 }, (_, i) => (
                <div key={i} className="flex gap-4 p-5">
                  <Skeleton className="size-10 rounded-[28%]" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-4 w-1/3" />
                    <Skeleton className="h-3.5 w-2/3" />
                    <Skeleton className="h-3 w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          ) : !entries.length ? (
            <Empty>{t('plugins.catalogEmpty')}</Empty>
          ) : shown.length ? (
            <ol className="divide-y overflow-hidden rounded-xl border bg-card">
              {shown.map((entry, index) => (
                <PluginRow
                  key={entry.id}
                  entry={entry}
                  rank={view === 'trending' ? index + 1 : undefined}
                  canWrite={canWrite}
                  onAction={act}
                />
              ))}
            </ol>
          ) : (
            <Empty>
              {hasFilters(filters) ? t('plugins.page.noResults')
                : view === 'trending' ? t('plugins.page.trendingEmpty')
                  : t('plugins.page.newEmpty')}
              {hasFilters(filters) && (
                <Button variant="outline" size="sm" onClick={() => setFilters(NO_FILTERS)}>
                  {t('plugins.page.clear')}
                </Button>
              )}
            </Empty>
          )}

          <div className="mt-6 lg:hidden">{notice}</div>
        </div>
      </div>

      {target && <PluginPlanDialog target={target} onClose={() => setTarget(null)} />}
    </div>
  )
}

function FacetList({ facet, value, onChoose }: { facet: Facet; value: string | null; onChoose: (key: FacetKey, value: string | null) => void }) {
  return (
    <section aria-label={facet.title}>
      <h2 className="mb-1.5 px-2 text-xs font-medium text-muted-foreground">{facet.title}</h2>
      <ul className="space-y-px">
        {facet.options.map((option) => {
          const active = value === option.value
          return (
            <li key={option.value}>
              <button
                type="button"
                aria-pressed={active}
                disabled={!active && option.count === 0}
                onClick={() => onChoose(facet.key, active ? null : option.value)}
                className={cn(
                  'flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left text-sm transition-colors',
                  active ? 'bg-accent font-medium text-accent-foreground' : 'text-foreground/80 hover:bg-muted hover:text-foreground',
                  'disabled:pointer-events-none disabled:opacity-40',
                )}
              >
                <span className="truncate">{option.label}</span>
                <span className="text-xs tabular-nums text-muted-foreground">{option.count}</span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/** Below `lg` the facets sit in one scrolling row of selects instead of a sidebar. */
function FacetSelect({ facet, value, onChoose }: { facet: Facet; value: string | null; onChoose: (key: FacetKey, value: string | null) => void }) {
  const { t } = useTranslation()
  return (
    <Select value={value ?? 'any'} onValueChange={(next) => onChoose(facet.key, next === 'any' ? null : next)}>
      <SelectTrigger
        size="sm"
        aria-label={facet.title}
        className={cn('h-8 w-auto shrink-0 rounded-full text-sm', value && 'border-foreground/30 bg-accent dark:bg-accent')}
      >
        <span className={cn(!value && 'text-muted-foreground')}>
          {value ? facet.options.find((option) => option.value === value)?.label : facet.title}
        </span>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="any">{t('plugins.page.any')}</SelectItem>
        {facet.options.map((option) => (
          <SelectItem key={option.value} value={option.value} disabled={option.count === 0 && option.value !== value}>
            {option.label} ({option.count})
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-14 text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

function PluginRow({
  entry,
  rank,
  canWrite,
  onAction,
}: {
  entry: PluginCatalogEntry
  rank?: number
  canWrite: boolean
  onAction: (entry: PluginCatalogEntry, action: PluginAction) => void
}) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language
  const summary = usePluginSummary()
  const name = localized(entry.name, lang)
  const installable = canWrite && !entry.revoked && entry.compatible && entry.supported
  const action: PluginAction | null = !installable ? null : entry.update_available ? 'update' : entry.installed_version ? null : 'install'

  return (
    <li className={cn('relative flex gap-4 px-4 py-4 transition-colors hover:bg-muted/40 sm:px-5', entry.revoked && 'opacity-60')}>
      {rank !== undefined && (
        <span className="hidden w-4 shrink-0 pt-2.5 text-right text-sm font-medium tabular-nums text-muted-foreground sm:block">
          {rank}
        </span>
      )}
      <PluginAvatar seed={entry.id} label={name} className="size-10 text-sm" />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="min-w-0 truncate text-[15px] font-semibold leading-6">
            {/* The whole row opens the plugin; its own button sits above this link. */}
            <Link to={pluginPath(entry.id)} className="outline-none after:absolute after:inset-0 focus-visible:underline">
              {name}
            </Link>
          </h3>
          <PluginStatus entry={entry} />
        </div>
        <p className="truncate text-[13px] leading-5 text-muted-foreground">{entry.owner}</p>
        <p className="mt-1.5 line-clamp-2 max-w-[70ch] text-sm text-foreground/80 sm:line-clamp-1">
          {localized(entry.description, lang)}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <PluginTypes entry={entry} />
          <span>{summary(entry)}</span>
          <span className="tabular-nums">v{entry.version}</span>
          {entry.updated_at && <span>{t('plugins.page.updated', { when: relativeDate(entry.updated_at, lang) })}</span>}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <span className="flex items-center gap-1 text-sm tabular-nums text-muted-foreground" title={t('plugins.page.stars', { count: entry.stars })}>
          <Star size={14} className="fill-amber-400 text-amber-400" />
          {compactNumber(entry.stars, lang)}
        </span>
        {action && (
          <Button
            size="sm"
            variant={action === 'update' ? 'default' : 'outline'}
            className="relative z-10 hidden h-8 sm:inline-flex"
            onClick={() => onAction(entry, action)}
          >
            {t(`plugins.${action}`)}
          </Button>
        )}
      </div>
    </li>
  )
}
