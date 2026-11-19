import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ oidcLogoutUrl: vi.fn() }))
vi.mock('@/lib/api', () => ({ auth }))

import { endProviderSession } from '@/lib/provider-logout'

const LOGOUT_URL = { logout_url: 'https://id.example.com/logout?client_id=securo' }

beforeEach(() => {
  vi.clearAllMocks()
})

describe('endProviderSession', () => {
  it('sends the browser to the provider logout URL (issue #1623)', async () => {
    auth.oidcLogoutUrl.mockResolvedValue(LOGOUT_URL)
    const redirect = vi.fn()

    await endProviderSession({ redirect })

    expect(redirect).toHaveBeenCalledWith('https://id.example.com/logout?client_id=securo')
  })

  it('stays put when the instance has no logout URL', async () => {
    auth.oidcLogoutUrl.mockResolvedValue({ logout_url: '' })
    const redirect = vi.fn()

    await endProviderSession({ redirect })

    expect(redirect).not.toHaveBeenCalled()
  })

  it('swallows a failing logout-url request so the local sign-out stands', async () => {
    auth.oidcLogoutUrl.mockRejectedValue(new Error('network down'))
    const redirect = vi.fn()

    await expect(endProviderSession({ redirect })).resolves.toBeUndefined()
    expect(redirect).not.toHaveBeenCalled()
  })

  it('skips the redirect when a new sign-in won the race', async () => {
    auth.oidcLogoutUrl.mockResolvedValue(LOGOUT_URL)
    const redirect = vi.fn()

    await endProviderSession({ redirect, shouldRedirect: () => false })

    expect(redirect).not.toHaveBeenCalled()
  })
})
