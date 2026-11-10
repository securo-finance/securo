import { describe, expect, it } from 'vitest'

import {
  NO_FILTERS,
  filterPlugins,
  newPlugins,
  pluginTypes,
  sortPlugins,
  trendingPlugins,
} from '@/lib/plugin-catalog'
import type { PluginCatalogEntry } from '@/types'

function entry(overrides: Partial<PluginCatalogEntry>): PluginCatalogEntry {
  return {
    id: 'maria/home',
    owner: 'maria',
    repository: 'https://github.com/maria/home',
    release: 'v1.0.0',
    commit: 'a'.repeat(40),
    name: { en: 'Home & pets', 'pt-BR': 'Casa e pets' },
    description: { en: 'Categories for pets.' },
    version: '1.0.0',
    license: 'MIT',
    min_securo_version: '0.19.0',
    contributes: { categories: 3, rules: 2 },
    locales: [],
    topics: ['brazil'],
    stars: 10,
    stars_7d: 0,
    added_at: null,
    updated_at: null,
    revoked: false,
    revoked_reason: null,
    compatible: true,
    supported: true,
    installed_version: null,
    update_available: false,
    ...overrides,
  }
}

const name = (e: PluginCatalogEntry) => e.name.en

describe('plugin catalog', () => {
  it('derives the plugin type from what it contributes', () => {
    expect(pluginTypes(entry({}))).toEqual(['data'])
    expect(pluginTypes(entry({ contributes: { widgets: 1, connectors: 1, rules: 0 } }))).toEqual(['interface', 'code'])
  })

  it('searches names in every language, accents aside, plus owner and topics', () => {
    const plugins = [entry({}), entry({ id: 'joao/wise', owner: 'joao', name: { en: 'Wise' }, topics: [] })]
    const search = (query: string) => filterPlugins(plugins, { ...NO_FILTERS, query }).map((e) => e.id)
    expect(search('casa')).toEqual(['maria/home'])
    expect(search('CÁSA PETS')).toEqual(['maria/home'])
    expect(search('joao')).toEqual(['joao/wise'])
    expect(search('brazil')).toEqual(['maria/home'])
    expect(search('nothing')).toEqual([])
  })

  it('filters by type, content, language and status', () => {
    const data = entry({ id: 'a/data' })
    const code = entry({ id: 'b/code', contributes: { connectors: 1 }, installed_version: '1.0.0', update_available: true })
    const plugins = [data, code]
    const ids = (filters: Partial<typeof NO_FILTERS>) => filterPlugins(plugins, { ...NO_FILTERS, ...filters }).map((e) => e.id)
    expect(ids({ type: 'code' })).toEqual(['b/code'])
    expect(ids({ kind: 'rules' })).toEqual(['a/data'])
    expect(ids({ locale: 'pt-BR' })).toEqual(['a/data', 'b/code'])
    const english = entry({ id: 'c/english', name: { en: 'Other' } })
    expect(filterPlugins([data, english], { ...NO_FILTERS, locale: 'pt-BR' }).map((e) => e.id)).toEqual(['a/data'])
    expect(ids({ status: 'installed' })).toEqual(['b/code'])
    expect(ids({ status: 'updates' })).toEqual(['b/code'])
  })

  it('sorts by stars, recency and name', () => {
    const old = entry({ id: 'a/old', name: { en: 'Alpha' }, stars: 50, added_at: '2026-01-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z' })
    const fresh = entry({ id: 'b/new', name: { en: 'Beta' }, stars: 5, added_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-02T00:00:00Z' })
    expect(sortPlugins([fresh, old], 'popular', name).map((e) => e.id)).toEqual(['a/old', 'b/new'])
    expect(sortPlugins([old, fresh], 'newest', name).map((e) => e.id)).toEqual(['b/new', 'a/old'])
    expect(sortPlugins([old, fresh], 'updated', name).map((e) => e.id)).toEqual(['b/new', 'a/old'])
    expect(sortPlugins([fresh, old], 'name', name).map((e) => e.id)).toEqual(['a/old', 'b/new'])
  })

  it('trends by growth relative to size, so a small plugin can lead', () => {
    const big = entry({ id: 'a/big', stars: 1000, stars_7d: 20 })
    const small = entry({ id: 'b/small', stars: 12, stars_7d: 8 })
    const quiet = entry({ id: 'c/quiet', stars: 50, stars_7d: 0 })
    expect(trendingPlugins([big, small, quiet], 6).map((e) => e.id)).toEqual(['b/small', 'a/big'])
  })

  it('lists plugins that joined recently, never revoked ones', () => {
    const now = Date.parse('2026-11-07T00:00:00Z')
    const recent = entry({ id: 'a/recent', added_at: '2026-11-01T00:00:00Z' })
    const revoked = entry({ id: 'b/revoked', added_at: '2026-11-02T00:00:00Z', revoked: true })
    const old = entry({ id: 'c/old', added_at: '2026-06-01T00:00:00Z' })
    expect(newPlugins([old, revoked, recent], 6, 30, now).map((e) => e.id)).toEqual(['a/recent'])
  })
})
