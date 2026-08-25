import { describe, expect, it } from 'vitest'

import { resolveLocalAuthEnabled } from './auth-config-utils'

describe('resolveLocalAuthEnabled', () => {
  it('hides local controls while configuration is pending', () => {
    expect(resolveLocalAuthEnabled(null, false)).toBe(false)
  })

  it('uses a successfully loaded server policy', () => {
    expect(resolveLocalAuthEnabled({ local_auth_enabled: false }, false)).toBe(false)
    expect(resolveLocalAuthEnabled({ local_auth_enabled: true }, false)).toBe(true)
  })

  it('falls back to local controls when the config request fails', () => {
    expect(resolveLocalAuthEnabled(null, true)).toBe(true)
  })

  it('never lets an error override a loaded OIDC-only policy', () => {
    expect(resolveLocalAuthEnabled({ local_auth_enabled: false }, true)).toBe(false)
  })
})
