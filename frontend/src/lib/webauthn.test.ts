/// <reference lib="dom" />

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  isConditionalPasskeySupported,
  startConditionalPasskeyAuthentication,
} from './webauthn'

class FakePublicKeyCredential {
  static isConditionalMediationAvailable = vi.fn<() => Promise<boolean>>()

  id = 'credential-id'
  rawId = new Uint8Array([1]).buffer
  type = 'public-key'
  authenticatorAttachment = 'platform'
  response = {
    authenticatorData: new Uint8Array([2]).buffer,
    clientDataJSON: new Uint8Array([3]).buffer,
    signature: new Uint8Array([4]).buffer,
    userHandle: new Uint8Array([5]).buffer,
  }

  getClientExtensionResults() {
    return {}
  }
}

function installWebAuthn(get = vi.fn()) {
  vi.stubGlobal('PublicKeyCredential', FakePublicKeyCredential)
  vi.stubGlobal('navigator', { credentials: { get } })
  vi.stubGlobal('window', {
    isSecureContext: true,
    location: { hostname: 'securo.example.com' },
    PublicKeyCredential: FakePublicKeyCredential,
  })
  return get
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('conditional passkey authentication', () => {
  it('uses the browser feature-detection result', async () => {
    installWebAuthn()
    FakePublicKeyCredential.isConditionalMediationAvailable.mockResolvedValue(true)

    await expect(isConditionalPasskeySupported()).resolves.toBe(true)
    expect(FakePublicKeyCredential.isConditionalMediationAvailable).toHaveBeenCalledOnce()
  })

  it('requests credentials with conditional mediation and an abort signal', async () => {
    const credential = new FakePublicKeyCredential()
    const get = installWebAuthn(vi.fn().mockResolvedValue(credential))
    const abortController = new AbortController()

    const result = await startConditionalPasskeyAuthentication(
      { challenge: 'AQ', rpId: 'example.com', userVerification: 'required' },
      abortController.signal,
    )

    expect(get).toHaveBeenCalledWith(expect.objectContaining({
      mediation: 'conditional',
      signal: abortController.signal,
      publicKey: expect.objectContaining({ rpId: 'example.com' }),
    }))
    expect(result).toMatchObject({
      id: 'credential-id',
      rawId: 'AQ',
      response: { userHandle: 'BQ' },
    })
  })
})
