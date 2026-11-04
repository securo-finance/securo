import { afterEach, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.doUnmock('./de.json')
  vi.resetModules()
  window.history.replaceState({}, '', '/')
})

it('loads a selected locale on demand and keeps English fallback keys', async () => {
  vi.resetModules()
  const { default: i18n, i18nReady } = await import('@/lib/i18n')
  await i18nReady
  expect(i18n.hasResourceBundle('hi', 'translation')).toBe(false)
  await i18n.changeLanguage('hi')
  expect(i18n.hasResourceBundle('hi', 'translation')).toBe(true)
  expect(i18n.t('nav.assets')).toBe((await import('./hi.json')).default.nav.assets)
  i18n.addResource('en', 'translation', 'testOnlyFallback', 'English fallback')
  expect(i18n.t('testOnlyFallback')).toBe('English fallback')
  expect(document.documentElement.lang).toBe('hi')
})

it('finishes loading a persisted locale before initialization resolves', async () => {
  localStorage.setItem('i18nextLng', 'de')
  vi.resetModules()
  const { default: i18n, i18nReady } = await import('@/lib/i18n')
  await i18nReady
  expect(i18n.language).toBe('de')
  expect(i18n.t('nav.assets')).toBe((await import('./de.json')).default.nav.assets)
  expect(document.documentElement.lang).toBe('de')
})

it('still initializes with English fallback if a selected locale asset fails', async () => {
  localStorage.setItem('i18nextLng', 'de')
  vi.doMock('./de.json', () => { throw new Error('synthetic unavailable locale asset') })
  vi.resetModules()
  const { default: i18n, i18nReady } = await import('@/lib/i18n')
  await i18nReady
  expect(i18n.hasResourceBundle('de', 'translation')).toBe(false)
  expect(i18n.t('nav.assets')).toBe((await import('./en.json')).default.nav.assets)
})
