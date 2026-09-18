import i18n, { type BackendModule, type ResourceLanguage } from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import ptBR from '@/locales/pt-BR.json'
import en from '@/locales/en.json'

// Keep the default and Brazilian Portuguese bundles available immediately;
// other languages are separate build assets loaded by i18next when selected.
const localeLoaders = import.meta.glob<{ default: ResourceLanguage }>([
  '../locales/*.json', '!../locales/en.json', '!../locales/pt-BR.json',
])

function syncHtmlLang(lng: string) {
  document.documentElement.lang = lng
}

export const i18nReady = i18n
  .use<BackendModule>({
    type: 'backend',
    init() {},
    read(language, _namespace, callback) {
      const load = localeLoaders[`../locales/${language}.json`]
      if (!load) {
        callback(null, {})
        return
      }
      load().then(
        ({ default: resource }) => callback(null, resource),
        (error: Error) => callback(error, false),
      )
    },
  })
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      'pt-BR': { translation: ptBR },
      en: { translation: en },
    },
    partialBundledLanguages: true,
    initAsync: false,
    fallbackLng: 'en',
    // English is the default. Honour an explicit, persisted choice
    // (querystring/localStorage/cookie) but do NOT auto-pick the browser
    // language — otherwise a pt-BR/es-* browser would override the English
    // default before the user ever chooses.
    detection: {
      order: ['querystring', 'localStorage', 'cookie'],
      caches: ['localStorage'],
    },
    interpolation: {
      escapeValue: false,
    },
  })

syncHtmlLang(i18n.language || 'en')
i18n.on('languageChanged', syncHtmlLang)

export type SupportedLang =
  | 'pt-BR'
  | 'pt-PT'
  | 'en'
  | 'es'
  | 'pl'
  | 'it'
  | 'ru'
  | 'uk'
  | 'de'
  | 'fr'
  | 'nl'
  | 'sk'
  | 'el'
  | 'hi'
  | 'ja'

// Single source of truth for language pickers. When adding a locale, register
// a locale JSON file and add one entry here; every picker stays in sync instead
// of each hand-rolling its own list (the setup screen's button row broke a
// little more with every translation PR before this existed).
export const SUPPORTED_LANGS: { code: SupportedLang; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'pt-BR', label: 'Português (BR)' },
  { code: 'pt-PT', label: 'Português (PT)' },
  { code: 'es', label: 'Español' },
  { code: 'fr', label: 'Français' },
  { code: 'de', label: 'Deutsch' },
  { code: 'it', label: 'Italiano' },
  { code: 'pl', label: 'Polski' },
  { code: 'ru', label: 'Русский' },
  { code: 'uk', label: 'Українська' },
  { code: 'nl', label: 'Nederlands' },
  { code: 'sk', label: 'Slovenčina' },
  { code: 'el', label: 'Ελληνικά' },
  { code: 'hi', label: 'हिन्दी' },
  { code: 'ja', label: '日本語' },
]

// Normalise any browser/i18n language tag to one of our supported keys. The
// backend and resource bundles key Portuguese as the region-tagged 'pt-BR'
// while 'en'/'es' are bare, so naively truncating to the primary subtag
// (e.g. 'pt-BR'.split('-')[0] -> 'pt') yields a value neither side recognises
// and silently falls back to English. Match on the primary subtag instead.
// Portuguese has two bundles, so the region matters: only an explicit pt-PT
// tag selects European Portuguese; a bare 'pt' keeps its historical pt-BR
// mapping so existing users are unaffected.
export function resolveSupportedLang(lng?: string | null): SupportedLang {
  const tag = (lng ?? '').toLowerCase()
  if (tag.startsWith('pt-pt')) return 'pt-PT'
  if (tag.startsWith('pt')) return 'pt-BR'
  if (tag.startsWith('es')) return 'es'
  if (tag.startsWith('pl')) return 'pl'
  if (tag.startsWith('it')) return 'it'
  if (tag.startsWith('ru')) return 'ru'
  if (tag.startsWith('uk')) return 'uk'
  if (tag.startsWith('de')) return 'de'
  if (tag.startsWith('fr')) return 'fr'
  if (tag.startsWith('nl')) return 'nl'
  if (tag.startsWith('sk')) return 'sk'
  if (tag.startsWith('el')) return 'el'
  if (tag.startsWith('hi')) return 'hi'
  if (tag.startsWith('ja')) return 'ja'
  return 'en'
}

export default i18n
