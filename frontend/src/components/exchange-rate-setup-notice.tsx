import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { Info } from 'lucide-react'

const SETUP_PATH = '/admin#provider-exchangeRates'

/**
 * What a reader can do about missing exchange rates: an admin gets a link
 * to the settings, anyone else is told who can fix it. Placed inside the
 * Assets page's single exchange-rate warning.
 */
export function ExchangeRateSetupAction({ isAdmin }: { isAdmin: boolean }) {
  const { t } = useTranslation()
  return isAdmin ? (
    <Link to={SETUP_PATH} className="shrink-0 font-medium underline underline-offset-2 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      {t('assets.exchangeRateSetupLink')}
    </Link>
  ) : (
    <span>{t('assets.exchangeRateSetupContactAdmin')}</span>
  )
}

export function ExchangeRateSetupIcon({ from, to, isAdmin }: { from: string; to: string; isAdmin: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()

  if (isAdmin) {
    const label = t('assets.exchangeRateSetupAction', { from, to })
    return (
      <button
        type="button"
        className="inline-flex size-5 items-center justify-center rounded text-amber-700 hover:text-amber-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:text-amber-400 dark:hover:text-amber-300"
        aria-label={label}
        title={label}
        onClick={(event) => { event.stopPropagation(); navigate(SETUP_PATH) }}
      >
        <Info size={13} aria-hidden="true" />
      </button>
    )
  }

  const label = t('assets.exchangeRateSetupAskAdmin', { from, to })
  return (
    <span className="inline-flex size-5 items-center justify-center text-amber-700 dark:text-amber-400" title={label}>
      <Info size={13} aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  )
}
