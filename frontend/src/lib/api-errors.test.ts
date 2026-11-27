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
    ['en', '"Household" has other members and no other owner. Make one of them an owner, or remove them, before deleting this user.'],
    ['pt-BR', '"Household" tem outros membros e nenhum outro dono. Torne um deles dono, ou remova-os, antes de excluir este usuário.'],
  ])('shows why a user deletion was refused, with the workspace, in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError({
      code: 'user_sole_owner',
      message: 'English text the API built',
      workspace: 'Household',
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

  // The budget amount bounds come back as a pydantic 422 with the message in
  // `msg`; the toast must show the sentence alone, translated, not the
  // `amount: ...` prefix built for the validation entries nobody translates.
  it.each([
    ['en', 'The budget limit must be at least 0.01.'],
    ['pt-BR', 'O limite do orçamento deve ser de pelo menos 0,01.'],
  ])('shows the budget minimum from a 422 detail in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError([
      {
        type: 'budget_amount_not_positive',
        loc: ['body', 'amount'],
        msg: 'The budget limit must be at least 0.01',
        ctx: {},
      },
    ]))).toBe(expected)
  })

  it.each([
    ['en', 'The budget limit is too large.'],
    ['pt-BR', 'O limite do orçamento é grande demais.'],
  ])('shows the budget maximum from a 422 detail in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError([
      {
        type: 'budget_amount_too_large',
        loc: ['body', 'amount'],
        msg: 'The budget limit is too large',
        ctx: {},
      },
    ]))).toBe(expected)
  })

  it.each([
    ['en', 'The budget limit cannot be negative.'],
    ['pt-BR', 'O limite do orçamento não pode ser negativo.'],
  ])('shows the budget negative-limit error in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError([
      {
        type: 'budget_amount_negative',
        loc: ['body', 'amount'],
        msg: 'The budget limit cannot be negative',
        ctx: {},
      },
    ]))).toBe(expected)
  })

  // A refused amount comes as a 422 list from pydantic ("Value error, ...")
  // or, when the service works it out, as a plain detail (issue #330).
  it.each([
    ['en', 'The converted amount cannot be negative.'],
    ['pt-BR', 'O valor convertido não pode ser negativo.'],
  ])('shows a refused converted amount in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError([
      {
        loc: ['body', 'amount_primary'],
        msg: 'Value error, amount_primary must be greater than zero',
      },
    ]))).toBe(expected)
    expect(extractApiError(apiError('amount_primary must be greater than zero'))).toBe(expected)
  })

  // The backend answers the same message for a negative rate (never accepted)
  // and for a zero rate next to a real conversion (only a converted amount that
  // rounds to zero allows it), so the text has to refuse the negative outright
  // and give the zero rule as the exception instead of the other way round.
  it.each([
    ['en', 'The exchange rate cannot be negative. A zero rate is only accepted when the converted amount rounds to zero.'],
    ['pt-BR', 'A taxa de câmbio não pode ser negativa. Uma taxa zero só é aceita quando o valor convertido arredonda para zero.'],
  ])('shows a refused exchange rate in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError([
      {
        loc: ['body', 'fx_rate_used'],
        msg: 'Value error, fx_rate_used must be greater than zero',
      },
    ]))).toBe(expected)
    expect(extractApiError(apiError('fx_rate_used must be greater than zero'))).toBe(expected)
  })

  it('shows a refused amount in pt-BR and keeps an unknown entry as it was', async () => {
    await i18n.changeLanguage('pt-BR')

    expect(extractApiError(apiError([
      { loc: ['body', 'amount'], msg: 'Value error, amount must be at most 9999999999999.99' },
      { loc: ['body', 'date'], msg: 'Input should be a valid date' },
    ]))).toBe('O valor é maior do que o Securo consegue guardar., date: Input should be a valid date')
  })

  // Linking an incoming transaction to a group settlement, refused by the
  // service: the settle dialog must say why in the user's language (#494).
  // The sentence names the settlement being saved as the one that goes over,
  // since the paybacks already there can still be under the credit.
  it.each([
    ['en', 'The paybacks linked to this credit would add up to more than its amount.'],
    ['pt-BR', 'Os acertos vinculados a este crédito passariam do valor dele.'],
  ])('shows why a payback cannot exceed the credit in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(
      extractApiError(apiError('Settlements linked to this transaction would exceed its amount')),
    ).toBe(expected)
  })

  it('translates the other refusals of a linked transaction', async () => {
    await i18n.changeLanguage('pt-BR')

    expect(extractApiError(apiError('Only the receiver can link their incoming transaction')))
      .toBe('Só quem recebe o dinheiro pode vincular a transação de entrada.')
    expect(extractApiError(
      apiError("Linked transaction must be your own credit in the settlement's currency"),
    )).toBe('Escolha uma transação de entrada sua, na moeda do acerto.')
  })

  it('falls back to the message of a code it does not know', async () => {
    await i18n.changeLanguage('pt-BR')

    expect(extractApiError(apiError({
      code: 'some_future_code',
      message: 'Cannot change amount or date on a synchronized transaction',
    }))).toBe('Não é possível alterar o valor ou a data de uma transação sincronizada')
  })

  // The calendar refuses a month far from today (#598). Its message spells the
  // window in, so the lookup matches the sentence with the number as a slot:
  // moving the limit must not send the toast back to English (#600).
  it.each([
    ['en', 'This month is too far from today.'],
    ['pt-BR', 'Este mês está longe demais de hoje.'],
  ])('shows the calendar month bound in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(extractApiError(apiError('month must be within 50 years of today'))).toBe(expected)
    expect(extractApiError(apiError('month must be within 25 years of today'))).toBe(expected)
  })

  it('keeps an English message that only looks like the calendar bound', async () => {
    await i18n.changeLanguage('pt-BR')

    expect(extractApiError(apiError('month must be within 50 days of today'))).toBe(
      'month must be within 50 days of today',
    )
  })

  // The pool refuses with 503 when no database connection is free (#1901).
  it.each([
    ['en', 'The instance is at its database connection limit. Try again in a moment.'],
    ['pt-BR', 'A instância atingiu o limite de conexões com o banco de dados. Tente de novo em instantes.'],
  ])('shows the database pool refusal in %s', async (language, expected) => {
    await i18n.changeLanguage(language)

    expect(
      extractApiError(
        apiError('The instance is at its database connection limit. Try again in a moment.'),
      ),
    ).toBe(expected)
  })
})
