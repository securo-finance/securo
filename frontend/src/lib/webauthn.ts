type PublicKeyCredentialCreationOptionsJSON = Omit<PublicKeyCredentialCreationOptions, 'challenge' | 'user' | 'excludeCredentials'> & {
  challenge: string
  user: Omit<PublicKeyCredentialUserEntity, 'id'> & { id: string }
  excludeCredentials?: Array<Omit<PublicKeyCredentialDescriptor, 'id'> & { id: string }>
}

type PublicKeyCredentialRequestOptionsJSON = Omit<PublicKeyCredentialRequestOptions, 'challenge' | 'allowCredentials'> & {
  challenge: string
  allowCredentials?: Array<Omit<PublicKeyCredentialDescriptor, 'id'> & { id: string }>
}

type RegistrationCredentialJSON = {
  id: string
  rawId: string
  type: string
  authenticatorAttachment?: string | null
  transports?: string[]
  response: {
    attestationObject: string
    clientDataJSON: string
  }
  clientExtensionResults: AuthenticationExtensionsClientOutputs
}

type AuthenticationCredentialJSON = {
  id: string
  rawId: string
  type: string
  authenticatorAttachment?: string | null
  response: {
    authenticatorData: string
    clientDataJSON: string
    signature: string
    userHandle: string | null
  }
  clientExtensionResults: AuthenticationExtensionsClientOutputs
}

/** Why passkeys cannot be used here, or null when they can. */
export type PasskeyBlocker = 'ip' | 'insecure' | 'unsupported'

/** Every failure the passkey ceremonies can surface, as an i18n-friendly code. */
export type PasskeyFailure = PasskeyBlocker | 'cancelled' | 'duplicate' | 'domain' | 'mismatch' | 'unknown'

const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/

function isIpLiteral(hostname: string): boolean {
  // Browsers bracket IPv6 hosts; a bare colon can only be IPv6 here.
  return IPV4.test(hostname) || hostname.startsWith('[') || hostname.includes(':')
}

/**
 * WebAuthn requires a domain name: an IP address is never a valid relying-party
 * ID, and plain HTTP is only a secure context on localhost. Detecting that up
 * front lets the UI explain it instead of showing an opaque SecurityError.
 */
export function passkeyBlocker(): PasskeyBlocker | null {
  if (typeof window === 'undefined') return 'unsupported'
  if (isIpLiteral(window.location.hostname)) return 'ip'
  if (!window.isSecureContext) return 'insecure'
  if (!('PublicKeyCredential' in window) || !navigator.credentials) return 'unsupported'
  return null
}

export function isPasskeySupported(): boolean {
  return passkeyBlocker() === null
}

const SERVER_FAILURES: Record<string, PasskeyFailure> = {
  passkey_origin_ip: 'ip',
  passkey_origin_insecure: 'insecure',
  passkey_origin_mismatch: 'mismatch',
}

const BROWSER_FAILURES: Record<string, PasskeyFailure> = {
  NotAllowedError: 'cancelled',
  AbortError: 'cancelled',
  InvalidStateError: 'duplicate',
  SecurityError: 'domain',
  NotSupportedError: 'unsupported',
}

/** Classify a ceremony failure so the UI can say what actually went wrong. */
export function passkeyFailure(error: unknown): PasskeyFailure {
  const response = (error as { response?: { data?: { detail?: { code?: string } } } })?.response
  const serverCode = response?.data?.detail?.code
  if (serverCode && SERVER_FAILURES[serverCode]) return SERVER_FAILURES[serverCode]

  const name = (error as { name?: string })?.name
  if (name && BROWSER_FAILURES[name]) return BROWSER_FAILURES[name]

  return 'unknown'
}

function base64urlToArrayBuffer(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/')
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

function arrayBufferToBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let i = 0; i < bytes.byteLength; i += 1) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

function creationOptionsFromJSON(options: PublicKeyCredentialCreationOptionsJSON): PublicKeyCredentialCreationOptions {
  return {
    ...options,
    challenge: base64urlToArrayBuffer(options.challenge),
    user: {
      ...options.user,
      id: base64urlToArrayBuffer(options.user.id),
    },
    excludeCredentials: options.excludeCredentials?.map((credential) => ({
      ...credential,
      id: base64urlToArrayBuffer(credential.id),
    })),
  }
}

function requestOptionsFromJSON(options: PublicKeyCredentialRequestOptionsJSON): PublicKeyCredentialRequestOptions {
  return {
    ...options,
    challenge: base64urlToArrayBuffer(options.challenge),
    allowCredentials: options.allowCredentials?.map((credential) => ({
      ...credential,
      id: base64urlToArrayBuffer(credential.id),
    })),
  }
}

export async function startPasskeyRegistration(options: Record<string, unknown>): Promise<RegistrationCredentialJSON> {
  if (!isPasskeySupported()) {
    throw new Error('Passkeys are not supported in this browser or context')
  }

  const credential = await navigator.credentials.create({
    publicKey: creationOptionsFromJSON(options as PublicKeyCredentialCreationOptionsJSON),
  })

  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('No passkey credential was created')
  }

  const response = credential.response as AuthenticatorAttestationResponse
  return {
    id: credential.id,
    rawId: arrayBufferToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    transports: response.getTransports?.(),
    response: {
      attestationObject: arrayBufferToBase64url(response.attestationObject),
      clientDataJSON: arrayBufferToBase64url(response.clientDataJSON),
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  }
}

export async function startPasskeyAuthentication(options: Record<string, unknown>): Promise<AuthenticationCredentialJSON> {
  if (!isPasskeySupported()) {
    throw new Error('Passkeys are not supported in this browser or context')
  }

  const credential = await navigator.credentials.get({
    publicKey: requestOptionsFromJSON(options as PublicKeyCredentialRequestOptionsJSON),
  })

  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('No passkey credential was selected')
  }

  const response = credential.response as AuthenticatorAssertionResponse
  return {
    id: credential.id,
    rawId: arrayBufferToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    response: {
      authenticatorData: arrayBufferToBase64url(response.authenticatorData),
      clientDataJSON: arrayBufferToBase64url(response.clientDataJSON),
      signature: arrayBufferToBase64url(response.signature),
      userHandle: response.userHandle ? arrayBufferToBase64url(response.userHandle) : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  }
}
