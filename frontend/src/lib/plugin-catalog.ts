import type { PluginCatalogEntry } from '@/types'

export type PluginType = 'data' | 'interface' | 'code'
export type PluginSort = 'popular' | 'trending' | 'updated' | 'newest' | 'name'
export type PluginStatus = 'installed' | 'updates'

/** What each contribution kind makes a plugin: data, interface, or code. */
export const KIND_TYPE: Record<string, PluginType> = {
  category_groups: 'data',
  categories: 'data',
  rules: 'data',
  csv_presets: 'data',
  fiscal_jurisdictions: 'data',
  invoice_labels: 'data',
  widgets: 'interface',
  reports: 'interface',
  dashboard_layouts: 'interface',
  themes: 'interface',
  connectors: 'code',
  integrations: 'code',
  parsers: 'code',
  price_providers: 'code',
  agent_tools: 'code',
}

export const PLUGIN_TYPES: PluginType[] = ['data', 'interface', 'code']

export interface PluginFilters {
  query: string
  type: PluginType | null
  kind: string | null
  locale: string | null
  status: PluginStatus | null
}

export const NO_FILTERS: PluginFilters = { query: '', type: null, kind: null, locale: null, status: null }

export function hasFilters(filters: PluginFilters): boolean {
  return Boolean(filters.query.trim() || filters.type || filters.kind || filters.locale || filters.status)
}

/** The contribution kinds a plugin actually brings. */
export function contributedKinds(entry: PluginCatalogEntry): string[] {
  return Object.entries(entry.contributes)
    .filter(([, count]) => count > 0)
    .map(([kind]) => kind)
}

export function pluginTypes(entry: PluginCatalogEntry): PluginType[] {
  const types = new Set(contributedKinds(entry).map((kind) => KIND_TYPE[kind]).filter(Boolean))
  return PLUGIN_TYPES.filter((type) => types.has(type))
}

/** Languages the plugin speaks: those it declares and those its name is in. */
export function pluginLocales(entry: PluginCatalogEntry): string[] {
  return [...new Set([...entry.locales, ...Object.keys(entry.name)])]
}

/** Growth this week relative to the plugin's size before it, so small plugins can trend. */
export function trendScore(entry: PluginCatalogEntry): number {
  return entry.stars_7d / (entry.stars - entry.stars_7d + 10)
}

function fold(text: string): string {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

function matches(entry: PluginCatalogEntry, query: string): boolean {
  const words = fold(query).split(/\s+/).filter(Boolean)
  if (!words.length) return true
  const haystack = fold(
    [
      ...Object.values(entry.name),
      ...Object.values(entry.description),
      entry.id,
      entry.owner,
      ...entry.topics,
    ].join(' '),
  )
  return words.every((word) => haystack.includes(word))
}

export function filterPlugins(entries: PluginCatalogEntry[], filters: PluginFilters): PluginCatalogEntry[] {
  return entries.filter(
    (entry) =>
      matches(entry, filters.query) &&
      (!filters.type || pluginTypes(entry).includes(filters.type)) &&
      (!filters.kind || contributedKinds(entry).includes(filters.kind)) &&
      (!filters.locale || pluginLocales(entry).includes(filters.locale)) &&
      (filters.status !== 'installed' || entry.installed_version !== null) &&
      (filters.status !== 'updates' || entry.update_available),
  )
}

function time(value: string | null): number {
  return value ? Date.parse(value) : 0
}

export function sortPlugins(entries: PluginCatalogEntry[], sort: PluginSort, nameOf: (e: PluginCatalogEntry) => string) {
  const byName = (a: PluginCatalogEntry, b: PluginCatalogEntry) => nameOf(a).localeCompare(nameOf(b))
  const compare: Record<PluginSort, (a: PluginCatalogEntry, b: PluginCatalogEntry) => number> = {
    popular: (a, b) => b.stars - a.stars || byName(a, b),
    trending: (a, b) => trendScore(b) - trendScore(a) || b.stars - a.stars || byName(a, b),
    updated: (a, b) => time(b.updated_at) - time(a.updated_at) || byName(a, b),
    newest: (a, b) => time(b.added_at) - time(a.added_at) || byName(a, b),
    name: byName,
  }
  return [...entries].sort(compare[sort])
}

/** Plugins that gained stars this week, fastest-growing first. */
export function trendingPlugins(entries: PluginCatalogEntry[], limit: number): PluginCatalogEntry[] {
  return entries
    .filter((entry) => entry.stars_7d > 0 && !entry.revoked)
    .sort((a, b) => trendScore(b) - trendScore(a))
    .slice(0, limit)
}

/** Plugins that joined the catalog in the last `days` days, newest first. */
export function newPlugins(entries: PluginCatalogEntry[], limit: number, days = 30, now = Date.now()): PluginCatalogEntry[] {
  const since = now - days * 24 * 60 * 60 * 1000
  return entries
    .filter((entry) => time(entry.added_at) >= since && !entry.revoked)
    .sort((a, b) => time(b.added_at) - time(a.added_at))
    .slice(0, limit)
}
