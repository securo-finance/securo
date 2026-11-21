import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { admin } from '@/lib/api'
import { resolveDisplayLocale, resolveDateLocale } from '@/lib/format'
import { resolveSupportedLang } from '@/lib/i18n'
import { useAuth } from '@/contexts/auth-context'

/**
 * The admin number-format setting, asked once per session.
 *
 * `retryOnMount` stays off on purpose: an errored query with no data is fetched
 * again whenever a new observer mounts, and that fetch puts the query back to
 * `pending`. A form gated on {@link useDisplayLocaleSettled} mounts its fields
 * as soon as the setting fails, so a retry from any of them would unmount the
 * fields, fail again and mount them again — a loop that never shows the form.
 * Sharing these options keeps every observer on the whole workspace agreeing on
 * that, instead of one of them bringing the fetch back.
 */
const NUMBER_FORMAT_QUERY = {
  queryKey: ['admin', 'number-format'],
  queryFn: () => admin.numberFormat(),
  staleTime: Infinity,
  retry: false,
  retryOnMount: false,
} as const

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

  const { data } = useQuery(NUMBER_FORMAT_QUERY)

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
  const { isPending } = useQuery(NUMBER_FORMAT_QUERY)
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

  const { data: numberData } = useQuery(NUMBER_FORMAT_QUERY)

  const { data: dateData } = useQuery({
    queryKey: ['admin', 'date-format'],
    queryFn: () => admin.dateFormat(),
    staleTime: Infinity,
    retry: false,
  })

  const language = resolveSupportedLang(i18n.resolvedLanguage ?? i18n.language)
  return resolveDateLocale(dateData?.format, numberData?.format, user?.preferences?.currency_display, language)
}
