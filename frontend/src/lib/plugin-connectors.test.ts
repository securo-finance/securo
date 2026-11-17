import { expect, it } from 'vitest'
import { pluginConnectors } from './plugin-connectors'
import type { InstalledPlugin } from '@/types'

it('keeps only the integrations that sync as a bank', () => {
  const base = { key: 'a', name: { en: 'A' }, auth: 'credentials' as const, fields: [], sync_minutes: null }
  const plugin = {
    plugin_id: 'acme/x', integrations: [{ ...base, provides: ['accounts'] }, { ...base, key: 'b' }, { ...base, key: 'c', provides: [] }],
  } as unknown as InstalledPlugin
  expect(pluginConnectors([plugin]).map((c) => c.integration.key)).toEqual(['a'])
  expect(pluginConnectors(undefined)).toEqual([])
})
