import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { Info } from 'lucide-react'

const SETUP_PATH = '/admin#provider-exchangeRates'

export function ExchangeRateSetupBanner({ isAdmin }: { isAdmin: boolean }) {
  const { t } = useTranslation()
  return (
    <div role="status" className="flex flex-wrap items-start gap-2.5 rounded-lg border border-amber-300/70 bg-amber-50/70 px-3 py-2.5 text-sm text-amber-950 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-100">
      <Info size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
      <p className="min-w-0 flex-1">{t('assets.exchangeRateSetupWarning')}</p>
      {isAdmin ? (
        <Link to={SETUP_PATH} className="shrink-0 font-medium underline underline-offset-2 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {t('assets.exchangeRateSetupLink')}
        </Link>
      ) : (
        <span className="text-amber-800 dark:text-amber-200">{t('assets.exchangeRateSetupContactAdmin')}</span>
      )}
    </div>
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
