import i18n from 'i18next'
import { beforeAll, describe, expect, it } from 'vitest'

import en from '@/locales/en.json'
import ptBR from '@/locales/pt-BR.json'

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

describe('localized synchronized transaction errors', () => {
  beforeAll(async () => {
    await i18n.init({
      lng: 'en',
      fallbackLng: false,
      resources: {
        en: { translation: en },
        'pt-BR': { translation: ptBR },
      },
    })
  })

  it.each([
    ['en', 'The amount or date of a synchronized transaction cannot be changed'],
    ['pt-BR', 'Não é possível alterar o valor ou a data de uma transação sincronizada'],
  ])('shows the synchronized amount/date error in %s', async (language, expected) => {
    await i18n.changeLanguage(language)
    const message = 'Cannot change amount or date on a synchronized transaction'

    expect(extractApiError(apiError(message))).toBe(expected)
    expect(extractApiError(apiError({ message }))).toBe(expected)
  })
})
