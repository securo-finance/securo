import type { Locale } from 'date-fns'
import { enUS, es, it, pl, ptBR, ru, uk } from 'date-fns/locale'

import { resolveSupportedLang } from '@/lib/i18n'

const DATE_FNS_LOCALE: Record<ReturnType<typeof resolveSupportedLang>, Locale> = {
  en: enUS,
  'pt-BR': ptBR,
  es,
  pl,
  it,
  ru,
  uk,
}

export function resolveDateFnsLocale(language?: string | null): Locale {
  return DATE_FNS_LOCALE[resolveSupportedLang(language)]
}
