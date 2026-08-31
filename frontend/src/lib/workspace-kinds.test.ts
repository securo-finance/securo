import { describe, expect, it } from 'vitest'

import i18n from '@/lib/i18n'
import {
  WORKSPACE_KINDS,
  WORKSPACE_KIND_ICON,
  WORKSPACE_KIND_LABEL_KEY,
} from '@/lib/workspace-kinds'

describe('WORKSPACE_KINDS', () => {
  it('has no duplicates', () => {
    expect(new Set(WORKSPACE_KINDS).size).toBe(WORKSPACE_KINDS.length)
  })

  it('gives every kind a label key and an icon', () => {
    for (const kind of WORKSPACE_KINDS) {
      expect(WORKSPACE_KIND_LABEL_KEY[kind], `${kind} label key`).toBeDefined()
      expect(WORKSPACE_KIND_ICON[kind], `${kind} icon`).toBeDefined()
    }
  })

  it('resolves every label key against the English bundle', () => {
    // A missing key renders as "workspace.kindPersonal" in the create dialog.
    for (const kind of WORKSPACE_KINDS) {
      const key = WORKSPACE_KIND_LABEL_KEY[kind]
      expect(i18n.exists(key), `${key} missing from en`).toBe(true)
      expect(i18n.t(key), key).not.toBe(key)
    }
  })

  it('resolves every label key in pt-BR as well', async () => {
    // en and pt-BR are the two locales that must always be complete.
    for (const kind of WORKSPACE_KINDS) {
      const key = WORKSPACE_KIND_LABEL_KEY[kind]
      expect(i18n.exists(key, { lng: 'pt-BR' }), `${key} missing`).toBe(true)
    }
  })
})
