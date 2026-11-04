import { describe, expect, it } from 'vitest'

import { extractApiError } from './api-errors'

const legacyFallback = 'An unexpected error occurred'

function apiError(detail: unknown): unknown {
  return { response: { data: { detail } } }
}

describe('extractApiError', () => {
  it('returns a string detail verbatim', () => {
    expect(extractApiError(apiError('Category is still in use'))).toBe(
      'Category is still in use',
    )
  })

  it('formats FastAPI validation details using the legacy format', () => {
    expect(
      extractApiError(
        apiError([
          { loc: ['body', 'name'], msg: 'Field required' },
          { loc: ['body', 'amount'] },
        ]),
      ),
    ).toBe('name: Field required, amount: invalid')
  })

  it('uses the legacy fallback when detail is missing', () => {
    expect(extractApiError({ response: { data: {} } })).toBe(legacyFallback)
  })

  it('uses the legacy fallback for non-API errors', () => {
    expect(extractApiError(new Error('network failure'))).toBe(legacyFallback)
    expect(extractApiError(null)).toBe(legacyFallback)
  })

  it('preserves an empty string detail', () => {
    expect(extractApiError(apiError(''))).toBe('')
  })

  it('preserves a whitespace-only string detail', () => {
    expect(extractApiError(apiError('   '))).toBe('   ')
  })

  it('uses an explicit fallback when no detail is available', () => {
    expect(extractApiError(new Error('network failure'), 'Localized fallback')).toBe(
      'Localized fallback',
    )
  })
})
