import { Code2, Database, LayoutPanelTop, type LucideIcon } from 'lucide-react'

import type { PluginType } from '@/lib/plugin-catalog'

export const TYPE_ICON: Record<PluginType, LucideIcon> = {
  data: Database,
  interface: LayoutPanelTop,
  code: Code2,
}

/** "3 days ago" in the UI language. */
export function relativeDate(iso: string, lang: string): string {
  const days = Math.round((Date.parse(iso) - Date.now()) / 86_400_000)
  const format = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' })
  if (Math.abs(days) < 30) return format.format(days, 'day')
  if (Math.abs(days) < 365) return format.format(Math.round(days / 30), 'month')
  return format.format(Math.round(days / 365), 'year')
}

export function languageName(locale: string, lang: string): string {
  try {
    const name = new Intl.DisplayNames([lang], { type: 'language' }).of(locale) ?? locale
    // Some languages write language names in lowercase; as a label it starts a sentence.
    return name.charAt(0).toLocaleUpperCase(lang) + name.slice(1)
  } catch {
    return locale
  }
}

export function compactNumber(value: number, lang: string): string {
  return new Intl.NumberFormat(lang, { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

/** The route of a plugin's page: `/plugins/owner/name`. */
export function pluginPath(id: string): string {
  return `/plugins/${id}`
}
