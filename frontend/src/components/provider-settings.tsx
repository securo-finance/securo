import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AlertCircle, ArrowRightLeft, Cable, Check, ChevronRight, Landmark, Link2, Plug2, type LucideIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ProviderSettingsDialog } from '@/components/provider-settings-dialog'
import { admin, type ProviderSettingStatus } from '@/lib/api'

type Category = 'connections' | 'exchangeRates' | 'other'
type ProviderPresentation = { name: string; category: Category; description: string; icon: LucideIcon }

// New providers need only presentation metadata; forms use the API's field definitions.
const providers: Record<string, ProviderPresentation> = {
  pluggy: { name: 'Pluggy', category: 'connections', description: 'pluggyDescription', icon: Cable },
  enable_banking: { name: 'Enable Banking', category: 'connections', description: 'enableBankingDescription', icon: Landmark },
  simplefin: { name: 'SimpleFIN', category: 'connections', description: 'simplefinDescriptionShort', icon: Link2 },
  openexchangerates: { name: 'Open Exchange Rates', category: 'exchangeRates', description: 'openExchangeRatesDescription', icon: ArrowRightLeft },
}
const categories: Category[] = ['connections', 'exchangeRates', 'other']
const setupGuides: Partial<Record<Category, string>> = {
  connections: 'https://docs.usesecuro.com/docs/getting-started/installation#optional-integrations',
  exchangeRates: 'https://docs.usesecuro.com/docs/getting-started/installation#exchange-rates',
}

function ProviderRow({ provider }: { provider: ProviderSettingStatus }) {
  const { t } = useTranslation()
  const presentation = providers[provider.name]
  const name = presentation?.name ?? provider.name
  const Icon = presentation?.icon ?? Plug2
  const description = presentation ? t(`providerSettings.catalog.${presentation.description}`) : undefined
  const invalid = Object.values(provider.fields).some((field) => field.invalid)
  const hasSettings = provider.configured || Object.values(provider.fields).some((field) => field.source !== 'none')
  const status = invalid
    ? t('providerSettings.catalog.needsAttention')
    : provider.name === 'simplefin'
      ? t(`admin.users.${provider.configured ? 'enabled' : 'disabled'}`)
      : t(`providerSettings.${provider.configured ? 'configured' : 'notConfigured'}`)

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-5 sm:flex-nowrap sm:px-5">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-muted/40 text-foreground" aria-hidden="true">
        <Icon className="size-5" strokeWidth={1.75} />
      </div>
      <div className="min-w-0 flex-1 basis-1/2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <h3 className="text-sm font-semibold text-foreground">{name}</h3>
          <Badge variant="outline" className={invalid ? 'border-destructive/20 bg-destructive/5 text-destructive' : provider.configured ? 'border-primary/15 bg-primary/5 text-primary' : 'text-muted-foreground'}>
            {invalid ? <AlertCircle aria-hidden="true" /> : provider.configured ? <Check aria-hidden="true" /> : null}
            {status}
          </Badge>
        </div>
        {description && <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{description}</p>}
      </div>
      <ProviderSettingsDialog provider={provider} name={name} description={description} icon={<Icon className="size-5" aria-hidden="true" />} guideUrl={presentation && setupGuides[presentation.category]}>
        <Button
          variant="outline"
          className="ml-14 h-10 gap-2 shadow-none sm:ml-0"
          aria-label={t(`providerSettings.catalog.${hasSettings ? 'manageProvider' : 'configureProvider'}`, { provider: name })}
        >
          {t(`providerSettings.catalog.${hasSettings ? 'manage' : 'configure'}`)}
          <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
        </Button>
      </ProviderSettingsDialog>
    </li>
  )
}

export function ProviderSettings() {
  const { t } = useTranslation()
  const { hash } = useLocation()
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ['admin', 'provider-settings'],
    queryFn: admin.providerSettings,
  })

  useEffect(() => {
    if (hash === '#provider-connections' || hash === '#provider-exchangeRates') {
      document.getElementById(hash.slice(1))?.scrollIntoView({ block: 'start' })
    }
  }, [hash, isPending])

  if (isError && !data) {
    return (
      <div role="alert" className="mb-8 flex flex-wrap items-center gap-3 rounded-xl border border-border/60 bg-card p-5">
        <AlertCircle className="size-5 text-destructive" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">{t('providerSettings.title')}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('common.error')}</p>
        </div>
        <Button variant="outline" onClick={() => refetch()}>{t('common.retry')}</Button>
      </div>
    )
  }

  return (
    <div className="mb-8 space-y-8">
      {categories.map((category) => {
        const items = data?.filter((provider) => (providers[provider.name]?.category ?? 'other') === category) ?? []
        if (!items.length && (!isPending || category === 'other')) return null
        return (
          <section key={category} id={`provider-${category}`} aria-labelledby={`provider-${category}-heading`} className="scroll-mt-6 space-y-4">
            <div>
              <h2 id={`provider-${category}-heading`} className="text-base font-semibold tracking-tight text-foreground">
                {t(`providerSettings.catalog.${category}Title`)}
              </h2>
              <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                {t(`providerSettings.catalog.${category}Description`)}
              </p>
            </div>
            <div className="min-w-0 overflow-hidden rounded-xl border border-border/60 bg-card">
              {isPending ? (
                <div role="status" aria-label={t('common.loading')} className="divide-y divide-border/40">
                  {Array.from({ length: category === 'connections' ? 3 : 1 }, (_, index) => (
                    <div key={index} className="flex items-center gap-4 p-5" aria-hidden="true">
                      <Skeleton className="size-10 shrink-0 rounded-lg" />
                      <div className="min-w-0 flex-1 space-y-2">
                        <Skeleton className="h-4 w-28" />
                        <Skeleton className="h-3 w-3/4" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <ul className="divide-y divide-border/40">
                  {items.map((provider) => <ProviderRow key={provider.name} provider={provider} />)}
                </ul>
              )}
            </div>
          </section>
        )
      })}
      {data?.length === 0 && (
        <p role="status" className="rounded-xl border border-dashed border-border px-5 py-8 text-center text-sm text-muted-foreground">
          {t('providerSettings.catalog.empty')}
        </p>
      )}
    </div>
  )
}
