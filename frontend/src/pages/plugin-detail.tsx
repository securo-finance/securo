import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, ExternalLink, Globe, KeyRound, Plug, RefreshCw, ShieldCheck, Star, Trash2 } from 'lucide-react'

import { CategoryIcon } from '@/components/category-icon'
import { PluginAvatar } from '@/components/plugin-avatar'
import { PluginConnections } from '@/components/plugin-connections'
import { ThemePreview } from '@/components/theme-preview'
import { PluginStatus, PluginTypes } from '@/components/plugin-meta'
import { PluginPlanDialog, type PluginTarget } from '@/components/plugin-plan-dialog'
import { PluginReadme } from '@/components/plugin-readme'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useWorkspace } from '@/contexts/workspace-context'
import { plugins as pluginsApi } from '@/lib/api'
import { localized } from '@/lib/localized-text'
import { pluginTheme } from '@/lib/themes'
import { contributedKinds, pluginLocales, pluginTypes } from '@/lib/plugin-catalog'
import { compactNumber, languageName, relativeDate, scopeLabel } from '@/lib/plugin-format'
import { conditionSummary } from '@/lib/rule-summary'
import { cn } from '@/lib/utils'
import type { PluginAction, PluginCatalogEntry, PluginContents, PluginRef } from '@/types'

type Tab = 'overview' | 'inside' | 'connections'

const ACTION_LABEL: Record<string, string> = {
  set_category: 'rules.setCategory',
  set_description: 'rules.setDescription',
  append_notes: 'rules.appendNotes',
  ignore: 'rules.ignoreAction',
}

/** One plugin from the catalog: what it is, its README, and every item it brings. */
export default function PluginDetailPage() {
  const { owner = '', name = '' } = useParams()
  const id = `${owner}/${name}`
  const [searchParams] = useSearchParams()
  const { t, i18n } = useTranslation()
  const lang = i18n.language
  const { canWrite } = useWorkspace()
  const [tab, setTab] = useState<Tab>(searchParams.get('tab') === 'connections' ? 'connections' : 'overview')
  const [target, setTarget] = useState<PluginTarget | null>(null)

  // Fetched only now, because the user opened this plugin.
  const { data, isError, isFetching, refetch, error } = useQuery({
    queryKey: ['plugins', 'detail', id],
    queryFn: () => pluginsApi.details(id),
    staleTime: 5 * 60 * 1000,
    retry: 1,
  })

  // From the database: connections stay manageable even when the catalog isn't reachable.
  const { data: installedList } = useQuery({ queryKey: ['plugins', 'installed'], queryFn: pluginsApi.installed })
  const installed = installedList?.find((p) => p.plugin_id === id)
  const connectable = Boolean(installed?.integrations.length)

  const back = (
    <Link to="/plugins" className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground">
      <ArrowLeft size={14} />
      {t('plugins.title')}
    </Link>
  )

  if (!data && installed && connectable && isError && !isFetching) {
    return (
      <div className="pb-12">
        {back}
        <h1 className="mb-6 text-2xl font-semibold tracking-tight">{localized(installed.name, lang)}</h1>
        <PluginConnections plugin={installed} />
      </div>
    )
  }

  if (!data) {
    const notFound = (error as { response?: { status?: number } } | null)?.response?.status === 404
    return (
      <div className="pb-12">
        {back}
        {isError && !isFetching ? (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-14 text-center text-sm text-muted-foreground">
            {notFound ? t('plugins.detail.notFound') : t('plugins.detail.loadFailed')}
            {!notFound && (
              <Button variant="outline" size="sm" onClick={() => void refetch()}>
                <RefreshCw size={14} className="mr-1" />
                {t('common.retry')}
              </Button>
            )}
          </div>
        ) : (
          <div className="space-y-6">
            <div className="flex gap-5">
              <Skeleton className="size-16 rounded-[28%]" />
              <div className="flex-1 space-y-3 pt-1">
                <Skeleton className="h-7 w-64" />
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-4 w-full max-w-xl" />
              </div>
            </div>
            <Skeleton className="h-64 w-full rounded-xl" />
          </div>
        )}
      </div>
    )
  }

  const plugin = data.plugin
  const pluginName = localized(plugin.name, lang)
  const itemCount = Object.values(plugin.contributes).reduce((sum, count) => sum + count, 0)
  const act = (action: PluginAction) => setTarget({ id: plugin.id, name: pluginName, action })
  const about = (
    <>
      <Facts plugin={plugin} />
      <p className="flex gap-2 text-xs leading-relaxed text-muted-foreground">
        <ShieldCheck size={14} className="mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
        {pluginTypes(plugin).includes('code') ? t('plugins.detail.codeNote') : t('plugins.detail.previewNote')}
      </p>
    </>
  )

  return (
    <div className="pb-12">
      {back}

      <header className="mb-8 flex gap-5">
        <PluginAvatar seed={plugin.id} label={pluginName} className="size-14 text-lg sm:size-16 sm:text-xl" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{pluginName}</h1>
            <PluginStatus entry={plugin} />
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span>{t('plugins.page.byOwner', { owner: plugin.owner })}</span>
            <span className="inline-flex items-center gap-1 tabular-nums" title={t('plugins.page.stars', { count: plugin.stars })}>
              <Star size={14} className="fill-amber-400 text-amber-400" />
              {compactNumber(plugin.stars, lang)}
            </span>
          </div>
          <p className="mt-3 max-w-[65ch] text-[15px] leading-relaxed text-foreground/80">{localized(plugin.description, lang)}</p>
        </div>
      </header>

      <div className="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <aside className="lg:order-last">
          <div className="space-y-6 lg:sticky lg:top-6">
            <Actions plugin={plugin} canWrite={canWrite} onAction={act} />
            {/* On a phone the facts follow the content, so the README isn't pushed off screen. */}
            <div className="hidden space-y-6 lg:block">{about}</div>
          </div>
        </aside>

        <div className="min-w-0">
          <div role="tablist" className="mb-6 flex gap-6 border-b">
            {(connectable ? (['overview', 'inside', 'connections'] as const) : (['overview', 'inside'] as const)).map((value) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={tab === value}
                onClick={() => setTab(value)}
                className={cn(
                  '-mb-px inline-flex items-center gap-2 border-b-2 pb-2.5 text-sm font-medium transition-colors',
                  tab === value ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
                )}
              >
                {t(`plugins.detail.${value}`)}
                {value === 'inside' && (
                  <span className="rounded-full bg-muted px-1.5 text-[11px] font-medium tabular-nums text-muted-foreground">{itemCount}</span>
                )}
              </button>
            ))}
          </div>

          {tab === 'connections' && installed ? (
            <PluginConnections plugin={installed} />
          ) : tab === 'overview' ? (
            data.readme ? (
              <PluginReadme markdown={data.readme} base={`${plugin.repository}/blob/${plugin.commit}/`} />
            ) : (
              <p className="rounded-xl border border-dashed px-6 py-10 text-center text-sm text-muted-foreground">{t('plugins.detail.noReadme')}</p>
            )
          ) : data.contents ? (
            <Contents contents={data.contents} />
          ) : (
            <div className="space-y-3 text-sm">
              <p className="text-muted-foreground">{t('plugins.detail.contentsUnavailable')}</p>
              <ul className="divide-y rounded-xl border">
                {contributedKinds(plugin).map((kind) => (
                  <li key={kind} className="flex justify-between px-4 py-2.5">
                    {t(`plugins.kindName.${kind}`, { defaultValue: kind })}
                    <span className="tabular-nums text-muted-foreground">{plugin.contributes[kind]}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      <div className="mt-10 space-y-6 lg:hidden">{about}</div>

      {target && <PluginPlanDialog target={target} onClose={() => setTarget(null)} />}
    </div>
  )
}

function Actions({ plugin, canWrite, onAction }: { plugin: PluginCatalogEntry; canWrite: boolean; onAction: (action: PluginAction) => void }) {
  const { t } = useTranslation()
  const installable = canWrite && !plugin.revoked && plugin.compatible && plugin.supported
  return (
    <div className="space-y-2">
      {installable && plugin.update_available && (
        <Button className="w-full" onClick={() => onAction('update')}>{t('plugins.update')}</Button>
      )}
      {installable && !plugin.installed_version && (
        <Button className="w-full" onClick={() => onAction('install')}>{t('plugins.install')}</Button>
      )}
      {canWrite && plugin.installed_version && (
        // Always offered to an installed plugin, revoked or not: removing never needs the catalog.
        <Button variant="outline" className="w-full text-destructive hover:text-destructive" onClick={() => onAction('remove')}>
          <Trash2 size={14} className="mr-1.5" />
          {t('plugins.remove')}
        </Button>
      )}
      {plugin.revoked && plugin.revoked_reason && (
        <p className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{plugin.revoked_reason}</p>
      )}
      <Button asChild variant="outline" className="w-full">
        <a href={`${plugin.repository}/tree/${plugin.commit}`} target="_blank" rel="noopener noreferrer">
          <ExternalLink size={14} className="mr-1.5" />
          {t('plugins.viewSource')}
        </a>
      </Button>
    </div>
  )
}

function Facts({ plugin }: { plugin: PluginCatalogEntry }) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language
  const locales = pluginLocales(plugin)
  const rows: [string, React.ReactNode][] = [
    [t('plugins.detail.type'), <span className="inline-flex flex-wrap justify-end gap-x-2"><PluginTypes entry={plugin} /></span>],
    [t('plugins.detail.version'), <span className="tabular-nums">{plugin.version}</span>],
    [t('plugins.detail.license'), plugin.license],
    [t('plugins.detail.requires'), <span className="tabular-nums">Securo {plugin.min_securo_version}+</span>],
    ...(plugin.updated_at ? [[t('plugins.detail.updated'), relativeDate(plugin.updated_at, lang)] as [string, React.ReactNode]] : []),
    ...(plugin.added_at ? [[t('plugins.detail.added'), relativeDate(plugin.added_at, lang)] as [string, React.ReactNode]] : []),
    ...(locales.length
      ? [[t('plugins.detail.languages'), locales.map((locale) => languageName(locale, lang)).join(', ')] as [string, React.ReactNode]]
      : []),
  ]
  return (
    <div className="space-y-4">
      <dl className="divide-y border-y text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-4 py-2.5">
            <dt className="shrink-0 text-muted-foreground">{label}</dt>
            <dd className="min-w-0 text-right font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      {plugin.topics.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {plugin.topics.map((topic) => (
            <span key={topic} className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">{topic}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function RefName({ item }: { item: PluginRef }) {
  const { t, i18n } = useTranslation()
  return (
    <>
      {localized(item.name, i18n.language)}
      {item.default && <span className="font-normal text-muted-foreground"> ({t('plugins.detail.builtIn')})</span>}
    </>
  )
}

function ContentSection({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  if (!count) return null
  return (
    <section className="space-y-3">
      <h2 className="flex items-baseline gap-2 text-sm font-semibold">
        {title}
        <span className="text-xs font-normal tabular-nums text-muted-foreground">{count}</span>
      </h2>
      <ul className="divide-y rounded-xl border bg-card">{children}</ul>
    </section>
  )
}

function Contents({ contents }: { contents: PluginContents }) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language
  return (
    <div className="space-y-8">
      <p className="max-w-[65ch] text-sm text-muted-foreground">{t('plugins.detail.insideHint')}</p>
      <ContentSection title={t('plugins.kindName.category_groups')} count={contents.category_groups.length}>
        {contents.category_groups.map((group) => (
          <li key={group.key} className="flex items-center gap-3 px-4 py-2.5">
            <CategoryIcon icon={group.icon} color={group.color} size="sm" />
            <span className="text-sm font-medium">{localized(group.name, lang)}</span>
          </li>
        ))}
      </ContentSection>
      <ContentSection title={t('plugins.kindName.categories')} count={contents.categories.length}>
        {contents.categories.map((category) => (
          <li key={category.key} className="flex items-center gap-3 px-4 py-2.5">
            <CategoryIcon icon={category.icon} color={category.color} size="sm" />
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{localized(category.name, lang)}</span>
            {category.group && (
              <span className="shrink-0 text-xs text-muted-foreground">
                <RefName item={category.group} />
              </span>
            )}
          </li>
        ))}
      </ContentSection>
      <ContentSection title={t('plugins.kindName.rules')} count={contents.rules.length}>
        {contents.rules.map((rule) => (
          <li key={rule.key} className="space-y-2 px-4 py-3">
            <p className="text-sm font-medium">{rule.name}</p>
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              <span className="font-medium text-foreground/70">{t('plugins.detail.when')}</span>{' '}
              {conditionSummary(rule.conditions, rule.conditions_op, t)}
            </p>
            <div className="flex flex-wrap items-center gap-1.5 text-[13px]">
              <ArrowRight size={13} className="text-muted-foreground" />
              {rule.actions.map((action, i) => (
                <span key={i} className="rounded-md bg-muted px-2 py-0.5">
                  <span className="text-muted-foreground">{t(ACTION_LABEL[action.op] ?? action.op)}: </span>
                  <span className="font-medium">
                    {action.category ? <RefName item={action.category} /> : typeof action.value === 'string' ? action.value : ''}
                  </span>
                </span>
              ))}
            </div>
          </li>
        ))}
      </ContentSection>
      {(contents.themes?.length ?? 0) > 0 && (
        <section className="space-y-3">
          <h2 className="flex items-baseline gap-2 text-sm font-semibold">
            {t('plugins.kindName.themes')}
            <span className="text-xs font-normal tabular-nums text-muted-foreground">{contents.themes!.length}</span>
          </h2>
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {contents.themes!.map((theme) => (
              <li key={theme.key} className="rounded-xl border bg-card p-2">
                <ThemePreview theme={pluginTheme({ plugin_id: 'preview', name: {} }, theme)} />
                <p className="mt-2 truncate px-1 text-sm font-medium">{localized(theme.name, lang)}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
      <ContentSection title={t('plugins.kindName.integrations')} count={contents.integrations?.length ?? 0}>
        {(contents.integrations ?? []).map((integration) => (
          <li key={integration.key} className="flex items-center gap-3 px-4 py-2.5">
            <Plug size={15} className="shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{localized(integration.name, lang)}</span>
            <span className="shrink-0 text-xs text-muted-foreground">{t(`plugins.code.auth.${integration.auth}`)}</span>
          </li>
        ))}
      </ContentSection>
      <ContentSection title={t('plugins.code.network')} count={contents.network?.length ?? 0}>
        {(contents.network ?? []).map((host) => (
          <li key={host.host} className="flex items-start gap-3 px-4 py-2.5">
            <Globe size={15} className="mt-0.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block font-mono text-xs">{host.host}</span>
              <span className="block text-xs text-muted-foreground">{localized(host.purpose, lang)}</span>
            </span>
          </li>
        ))}
      </ContentSection>
      <ContentSection title={t('plugins.code.access')} count={contents.api_scopes?.length ?? 0}>
        {(contents.api_scopes ?? []).map((scope) => (
          <li key={scope.scope} className="flex items-start gap-3 px-4 py-2.5">
            <KeyRound size={15} className="mt-0.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium">{scopeLabel(scope.scope, t)}</span>
              <span className="block text-xs text-muted-foreground">{localized(scope.purpose, lang)}</span>
            </span>
          </li>
        ))}
      </ContentSection>
    </div>
  )
}
