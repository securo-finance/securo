import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { admin } from '@/lib/api'
import { resolveDisplayLocale, resolveDateLocale } from '@/lib/format'
import { resolveSupportedLang } from '@/lib/i18n'
import { useAuth } from '@/contexts/auth-context'

/**
 * Resolve the BCP-47 locale used for displaying currency and dates.
 *
 * Combines the admin-wide `number_format` setting with the signed-in user's
 * display currency. When the admin leaves the format on "auto" the separators
 * follow the currency (EUR → 1.000,00, USD → 1,000.00); an explicit choice
 * overrides that for everyone. Falls back to the UI language when neither is
 * known.
 *
 * Used everywhere a value is shown to replace the old language-derived locale,
 * so number/date formatting no longer silently tracks the UI language.
 */
export function useDisplayLocale(): string {
  const { i18n } = useTranslation()
  const { user } = useAuth()

  const { data } = useQuery({
    queryKey: ['admin', 'number-format'],
    queryFn: () => admin.numberFormat(),
    staleTime: Infinity,
    retry: false,
  })

  const fallback = i18n.language === 'en' ? 'en-US' : i18n.language
  return resolveDisplayLocale(data?.format, user?.preferences?.currency_display, fallback)
}

/**
 * Whether the admin number-format setting has settled (loaded or failed).
 *
 * A form that writes draft text in the workspace format must not mount before
 * this is true: the locale it formats with would otherwise be the fallback,
 * and the setting landing later would leave that text under a different one.
 */
export function useDisplayLocaleSettled(): boolean {
  const { isPending } = useQuery({
    queryKey: ['admin', 'number-format'],
    queryFn: () => admin.numberFormat(),
    staleTime: Infinity,
    retry: false,
  })
  return !isPending
}

/**
 * Resolve the locale used for displaying *dates*. Month/day words follow the
 * user's app language; only the field order (day-first vs month-first) follows
 * the admin number/date format setting. Pair currency rendering with
 * {@link useDisplayLocale} and date rendering with this hook.
 */
export function useDateLocale(): string {
  const { i18n } = useTranslation()
  const { user } = useAuth()

  const { data: numberData } = useQuery({
    queryKey: ['admin', 'number-format'],
    queryFn: () => admin.numberFormat(),
    staleTime: Infinity,
    retry: false,
  })

  const { data: dateData } = useQuery({
    queryKey: ['admin', 'date-format'],
    queryFn: () => admin.dateFormat(),
    staleTime: Infinity,
    retry: false,
  })

  const language = resolveSupportedLang(i18n.resolvedLanguage ?? i18n.language)
  return resolveDateLocale(
    dateData?.format,
    numberData?.format,
    user?.preferences?.currency_display,
    language,
  )
}
