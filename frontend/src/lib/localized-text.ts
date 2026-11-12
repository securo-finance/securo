import type { LocalizedText } from '@/types'

/** `text` in the UI language, its base language, or English. */
export function localized(text: LocalizedText, lang: string): string {
  return text[lang] ?? text[lang.split('-')[0]] ?? text.en ?? Object.values(text)[0] ?? ''
}
