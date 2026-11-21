import i18n from 'i18next'
import { beforeAll, describe, expect, it } from 'vitest'

import en from '@/locales/en.json'
import ptBR from '@/locales/pt-BR.json'

import { extractApiError, extractApiErrorAsync } from './api-errors'

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

describe('extractApiErrorAsync', () => {
  it('decodes FastAPI JSON returned as a Blob', async () => {
    const error = {
      response: {
        data: new Blob([
          JSON.stringify({ detail: [{ loc: ['body', 'file'], msg: 'Invalid ZIP' }] }),
        ]),
      },
    }
    await expect(extractApiErrorAsync(error, 'Fallback')).resolves.toBe(
      'file: Invalid ZIP',
    )
  })

  it('preserves locally-thrown error messages', async () => {
    await expect(
      extractApiErrorAsync(new Error('Choose a backup ZIP first.'), 'Fallback'),
    ).resolves.toBe('Choose a backup ZIP first.')
  })

  it('uses the translated fallback for Axios transport failures', async () => {
    const error = Object.assign(new Error('Network Error'), { isAxiosError: true })
    await expect(extractApiErrorAsync(error, 'Localized fallback')).resolves.toBe(
      'Localized fallback',
    )
  })
})

describe('localized API errors', () => {
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

  it.each([
    ['en', 'Enter a valid http or https endpoint URL'],
    ['pt-BR', 'Informe uma URL de endpoint http ou https válida'],
  ])('shows the invalid connection base URL error in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError('Enter a valid http or https endpoint URL'))).toBe(expected)
  })

  // A refused undo carries the numbers the user needs to act on, so the
  // sentence comes from the code and the values from the detail, never from
  // the English message the API spelled them into.
  it.each([
    ['en', "This import can't be undone: an entry that stays in the ledger sells 10 units with 4 held. Edit or delete that entry first."],
    ['pt-BR', 'Não é possível desfazer esta importação: um lançamento que permanece vende 10 unidades com 4 em carteira. Edite ou exclua esse lançamento primeiro.'],
  ])('shows why an undo was refused, with its numbers, in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError({
      code: 'import_undo_oversell',
      message: 'This import cannot be undone (English text the API built)',
      attempted: '10',
      available: '4',
    }))).toBe(expected)
  })

  it.each([
    ['en', "This import can't be undone: the holding left without it is too large to store."],
    ['pt-BR', 'Não é possível desfazer esta importação: a posição que sobra sem ela é grande demais para armazenar.'],
  ])('shows the too-large undo refusal in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError({
      code: 'import_undo_too_large',
      message: 'This import cannot be undone (English text the API built)',
    }))).toBe(expected)
  })

  it('falls back to the message of a code it does not know', async () => {
    await i18n.changeLanguage('pt-BR')

    expect(extractApiError(apiError({
      code: 'some_future_code',
      message: 'Cannot change amount or date on a synchronized transaction',
    }))).toBe('Não é possível alterar o valor ou a data de uma transação sincronizada')
  })
})
