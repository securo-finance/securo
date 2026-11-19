import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ oidcConfig: vi.fn() }))
vi.mock('@/lib/api', () => ({ auth }))

import { endProviderSession } from '@/lib/provider-logout'

const CONFIG = {
  enabled: true,
  provider_name: 'Keycloak',
  local_auth_enabled: true,
  logout_url: 'https://id.example.com/logout?client_id=securo',
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('endProviderSession', () => {
  it('sends the browser to the provider logout URL (issue #1623)', async () => {
    auth.oidcConfig.mockResolvedValue(CONFIG)
    const redirect = vi.fn()

    await endProviderSession(redirect)

    expect(redirect).toHaveBeenCalledWith('https://id.example.com/logout?client_id=securo')
  })

  it('stays put when the instance has no logout URL', async () => {
    auth.oidcConfig.mockResolvedValue({ ...CONFIG, logout_url: '' })
    const redirect = vi.fn()

    await endProviderSession(redirect)

    expect(redirect).not.toHaveBeenCalled()
  })

  it('swallows a failing config request so the local sign-out stands', async () => {
    auth.oidcConfig.mockRejectedValue(new Error('network down'))
    const redirect = vi.fn()

    await expect(endProviderSession(redirect)).resolves.toBeUndefined()
    expect(redirect).not.toHaveBeenCalled()
  })
})
