// @vitest-environment jsdom
import { afterAll, describe, expect, it } from 'vitest'

import i18n, { i18nReady, resolveSupportedLang } from '@/lib/i18n'

describe('document direction follows the language', () => {
  afterAll(async () => {
    await i18n.changeLanguage('en')
  })

  it('switches <html> to right-to-left for Hebrew and back', async () => {
    await i18nReady
    await i18n.changeLanguage('he')
    expect(document.documentElement.lang).toBe('he')
    expect(document.documentElement.dir).toBe('rtl')

    await i18n.changeLanguage('en')
    expect(document.documentElement.dir).toBe('ltr')
  })

  it('maps Hebrew browser tags, including the legacy iw code', () => {
    expect(resolveSupportedLang('he-IL')).toBe('he')
    expect(resolveSupportedLang('iw')).toBe('he')
  })
})
