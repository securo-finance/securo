import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import ptBR from '@/locales/pt-BR.json'
import ptPT from '@/locales/pt-PT.json'
import en from '@/locales/en.json'
import es from '@/locales/es.json'
import pl from '@/locales/pl.json'
import it from '@/locales/it.json'
import ru from '@/locales/ru.json'
import uk from '@/locales/uk.json'
import de from '@/locales/de.json'
import fr from '@/locales/fr.json'
import nl from '@/locales/nl.json'

function syncHtmlLang(lng: string) {
  document.documentElement.lang = lng
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      'pt-BR': { translation: ptBR },
      'pt-PT': { translation: ptPT },
      en: { translation: en },
      es: { translation: es },
      pl: { translation: pl },
      it: { translation: it },
      ru: { translation: ru },
      uk: { translation: uk },
      de: { translation: de },
      fr: { translation: fr },
      nl: { translation: nl },
    },
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

syncHtmlLang(i18n.language)
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

// Single source of truth for language pickers. When adding a locale, register
// the bundle above and add one entry here; every picker stays in sync instead
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
  return 'en'
}

export default i18n
