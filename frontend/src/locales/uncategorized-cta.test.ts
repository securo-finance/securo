import i18n from 'i18next'
import { beforeAll, describe, expect, it } from 'vitest'

import en from '@/locales/en.json'
import ptBR from '@/locales/pt-BR.json'

// Issue #469: the notice above the categorize list said "1 transactions need
// categorizing" because the key had no plural forms in the shipped locales.
describe('dashboard.uncategorizedCta plural', () => {
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
    ['en', 1, '1 transaction needs categorizing'],
    ['en', 2, '2 transactions need categorizing'],
    ['pt-BR', 1, '1 transação precisa ser categorizada'],
    ['pt-BR', 3, '3 transações precisam ser categorizadas'],
  ])('reads %s with count %i as "%s"', async (language, count, expected) => {
    await i18n.changeLanguage(language)
    expect(i18n.t('dashboard.uncategorizedCta', { count })).toBe(expected)
  })
})
