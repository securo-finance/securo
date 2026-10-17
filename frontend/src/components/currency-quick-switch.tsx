import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  auth as authApi,
  currencies as currenciesApi,
  settings as settingsApi,
} from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'

/**
 * Quick-switch between the currencies shortlisted in settings.
 *
 * Display currency is a server-side concern: the backend converts every
 * aggregate into `User.primary_currency` before sending it, so switching
 * means PATCHing the preference and refetching — never relabelling the
 * symbol client-side, which would show unconverted amounts.
 *
 * Renders nothing until at least two currencies are shortlisted.
 */
export function CurrencyQuickSwitch() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const queryClient = useQueryClient()

  const { data: prefs } = useQuery({
    queryKey: ['currency-preferences'],
    queryFn: settingsApi.currencyPreferences,
  })
  const { data: supported } = useQuery({
    queryKey: ['currencies'],
    queryFn: currenciesApi.list,
  })

  // Prefer the cached user so the active chip flips in the same render as the
  // rest of the app, rather than waiting on a preferences refetch.
  const active = user?.preferences?.currency_display ?? prefs?.currency_display
  const codes = prefs?.quick_currencies ?? []

  const switchMutation = useMutation({
    mutationFn: (code: string) => settingsApi.setDisplayCurrency(code),
    onSuccess: async () => {
      // Same refresh path as changing the workspace currency: re-read the
      // user so primary_currency is current, then drop every cached figure
      // because all of them were converted in the old currency.
      await authApi.me().then(updateUser).catch(() => {})
      void queryClient.invalidateQueries()
    },
    onError: (e: unknown) => {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : t('currencySwitch.error', 'Could not switch currency'))
      toast.error(detail)
    },
  })

  if (codes.length < 2) return null

  return (
    <div
      className="flex items-center gap-1"
      role="group"
      aria-label={t('currencySwitch.label', 'Display currency')}
    >
      {codes.map((code) => {
        const meta = supported?.find((c) => c.code === code)
        const isActive = code === active
        return (
          <button
            key={code}
            type="button"
            aria-pressed={isActive}
            // Disabled only while a switch is inflight, so a double-click
            // cannot queue two conversions of the whole dashboard.
            disabled={switchMutation.isPending}
            onClick={() => {
              if (!isActive) switchMutation.mutate(code)
            }}
            title={meta?.name ?? code}
            className={`h-7 px-2 rounded-lg border text-xs font-semibold tabular-nums transition-colors disabled:opacity-60 ${
              isActive
                ? 'border-primary bg-primary/10 text-foreground'
                : 'border-border bg-card text-muted-foreground hover:text-foreground'
            }`}
          >
            {meta?.symbol ?? code} {code}
          </button>
        )
      })}
    </div>
  )
}
