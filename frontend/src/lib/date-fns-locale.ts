import type { Locale } from 'date-fns'
import { enUS, es, it, pl, pt, ptBR, ru, uk, de, fr, nl, sk } from 'date-fns/locale'

import { resolveSupportedLang } from '@/lib/i18n'

const DATE_FNS_LOCALE: Record<ReturnType<typeof resolveSupportedLang>, Locale> = {
  en: enUS,
  'pt-BR': ptBR,
  'pt-PT': pt,
  es,
  pl,
  it,
  ru,
  uk,
  de,
  fr,
  nl,
  sk,
}

export function resolveDateFnsLocale(language?: string | null): Locale {
  return DATE_FNS_LOCALE[resolveSupportedLang(language)]
}
