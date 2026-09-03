import { describe, expect, it } from 'vitest'
import type { AxiosError } from 'axios'

import { isServerUnreachable } from '@/lib/auth-errors'

/** Minimal stand-in for the shape the auth screens actually branch on. */
function axiosError(status?: number): AxiosError {
  return (status === undefined
    ? { isAxiosError: true, response: undefined }
    : { isAxiosError: true, response: { status } }) as AxiosError
}

describe('isServerUnreachable', () => {
  it('treats a response-less failure as unreachable', () => {
    // Connection refused, DNS failure, timeout, CORS: axios gives no response.
    expect(isServerUnreachable(axiosError())).toBe(true)
  })

  it('treats 5xx as unreachable', () => {
    // A reverse proxy answering 502 while the backend container is down is
    // an outage, not a credentials problem (issue #318).
    expect(isServerUnreachable(axiosError(500))).toBe(true)
    expect(isServerUnreachable(axiosError(502))).toBe(true)
    expect(isServerUnreachable(axiosError(503))).toBe(true)
    expect(isServerUnreachable(axiosError(504))).toBe(true)
  })

  it('treats a real rejection as reachable, so the credentials message shows', () => {
    expect(isServerUnreachable(axiosError(400))).toBe(false)
    expect(isServerUnreachable(axiosError(401))).toBe(false)
    expect(isServerUnreachable(axiosError(403))).toBe(false)
    expect(isServerUnreachable(axiosError(404))).toBe(false)
    expect(isServerUnreachable(axiosError(422))).toBe(false)
  })

  it('treats 499 as reachable and 500 as not, at the boundary', () => {
    expect(isServerUnreachable(axiosError(499))).toBe(false)
    expect(isServerUnreachable(axiosError(500))).toBe(true)
  })

  it('treats a non-axios throw as unreachable rather than crashing', () => {
    expect(isServerUnreachable(new Error('boom'))).toBe(true)
    expect(isServerUnreachable(null)).toBe(true)
    expect(isServerUnreachable(undefined)).toBe(true)
  })
})
