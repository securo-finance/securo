import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PluginStatus } from '@/components/plugin-meta'
import { renderWithProviders, t } from '@/test/utils'
import type { PluginCatalogEntry } from '@/types'

function entry(overrides: Partial<PluginCatalogEntry>): PluginCatalogEntry {
  return {
    id: 'maria/mei', owner: 'maria', repository: 'https://github.com/maria/mei', release: 'v1.0.0',
    commit: 'a'.repeat(40), name: { en: 'MEI' }, description: { en: 'Taxes for a small business.' },
    version: '1.0.0', license: 'MIT', min_securo_version: '0.19.0', contributes: { categories: 2 },
    locales: [], topics: [], stars: 0, stars_7d: 0, added_at: null, updated_at: null,
    revoked: false, revoked_reason: null, compatible: true, supported: true,
    workspaces: ['business'], fits_workspace: false, icon_url: null, installed_version: null, update_available: false,
    ...overrides,
  }
}

describe('PluginStatus', () => {
  it('says which workspace type a plugin is for when it does not fit this one', () => {
    renderWithProviders(<PluginStatus entry={entry({})} />)
    expect(screen.getByText(t('plugins.onlyForBusiness'))).toBeInTheDocument()
  })

  it('says nothing about the workspace type when the plugin fits', () => {
    renderWithProviders(<PluginStatus entry={entry({ fits_workspace: true })} />)
    expect(screen.queryByText(t('plugins.onlyForBusiness'))).not.toBeInTheDocument()
  })

  it('says why when an update is only for the other workspace type', () => {
    renderWithProviders(
      <PluginStatus entry={entry({ installed_version: '1.0.0', update_available: true })} />,
    )
    expect(screen.getByText(t('plugins.onlyForBusiness'))).toBeInTheDocument()
    expect(screen.queryByText(t('plugins.updateAvailable'))).not.toBeInTheDocument()
  })
})
