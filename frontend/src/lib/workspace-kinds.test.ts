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

  it('resolves every label key in pt-BR as well', () => {
    // en and pt-BR are the two locales that must always be complete.
    //
    // fallbackLng:false is load-bearing. Without it i18next resolves through
    // the English fallback, so `exists(key, { lng: 'pt-BR' })` answers true for
    // a key pt-BR does not have and this test proves nothing.
    for (const kind of WORKSPACE_KINDS) {
      const key = WORKSPACE_KIND_LABEL_KEY[kind]
      expect(
        i18n.exists(key, { lng: 'pt-BR', fallbackLng: false }),
        `${key} missing from pt-BR`,
      ).toBe(true)
    }
  })

  it('the pt-BR check would actually catch a missing key', () => {
    // Guards the guard: accounts.multiInstitutionLink ships in en only, so a
    // check that passes here is resolving through the fallback.
    expect(
      i18n.exists('accounts.multiInstitutionLink', {
        lng: 'pt-BR',
        fallbackLng: false,
      }),
    ).toBe(false)
  })
})
