import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { connections } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Building2, ChevronLeft, Globe } from 'lucide-react'
import { toast } from 'sonner'

type Institution = {
  name: string
  display_name: string
  country: string
  logo?: string | null
}

interface OAuthConnectDialogProps {
  open: boolean
  onClose: () => void
  provider: string
  supportsAssetSync?: boolean
}

const LAST_COUNTRY_KEY = 'securo:lastOAuthCountry'

const REGION_NAMES: Intl.DisplayNames | null = (() => {
  try {
    return new Intl.DisplayNames(navigator.language || 'en', { type: 'region' })
  } catch {
    return null
  }
})()

function countryLabel(code: string): string {
  if (!REGION_NAMES) return code
  return REGION_NAMES.of(code) || code
}

export function OAuthConnectDialog({ open, onClose, provider, supportsAssetSync = false }: OAuthConnectDialogProps) {
  const { t } = useTranslation()
  const [step, setStep] = useState<'country' | 'bank'>('country')
  const [country, setCountry] = useState<string | null>(null)
  const [countries, setCountries] = useState<string[]>([])
  const [institutions, setInstitutions] = useState<Institution[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [redirecting, setRedirecting] = useState(false)
  const [syncAssets, setSyncAssets] = useState(true)

  // Reset when dialog opens.
  useEffect(() => {
    if (!open) return
    setStep('country')
    setCountry(null)
    setInstitutions([])
    setError(null)
    setRedirecting(false)
    setSyncAssets(true)
    setLoading(true)
    connections
      .listInstitutions(provider)
      .then((data) => {
        setCountries(data.countries)
        const stored = localStorage.getItem(LAST_COUNTRY_KEY)
        if (stored && data.countries.includes(stored)) {
          setCountry(stored)
          setStep('bank')
        }
      })
      .catch(() => setError(t('accounts.loadingInstitutionsError')))
      .finally(() => setLoading(false))
  }, [open, provider, t])

  // Load banks when a country is picked.
  useEffect(() => {
    if (!open || !country || step !== 'bank') return
    setLoading(true)
    setError(null)
    connections
      .listInstitutions(provider, country)
      .then((data) => setInstitutions(data.institutions))
      .catch(() => setError(t('accounts.loadingInstitutionsError')))
      .finally(() => setLoading(false))
  }, [open, provider, country, step, t])

  const sortedCountries = useMemo(
    () =>
      [...countries].sort((a, b) =>
        countryLabel(a).localeCompare(countryLabel(b)),
      ),
    [countries],
  )

  const handleCountrySelect = (code: string) => {
    setCountry(code)
    localStorage.setItem(LAST_COUNTRY_KEY, code)
    setStep('bank')
  }

  const handleBankSelect = async (institution: Institution) => {
    if (!country) return
    setRedirecting(true)
    try {
      const url = await connections.getOAuthUrl(provider, {
        country,
        institution_name: institution.name,
        ...(supportsAssetSync ? { sync_assets: syncAssets } : {}),
      })
      window.location.assign(url)
    } catch (e) {
      setRedirecting(false)
      const message = e instanceof Error ? e.message : String(e)
      toast.error(message || t('accounts.connectError'))
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !redirecting && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {step === 'bank' && (
              <button
                onClick={() => setStep('country')}
                className="text-muted-foreground hover:text-foreground"
                aria-label={t('accounts.back')}
              >
                <ChevronLeft size={18} />
              </button>
            )}
            {step === 'country' ? t('accounts.selectCountry') : t('accounts.selectBank')}
          </DialogTitle>
          <p className="text-sm text-muted-foreground">
            {step === 'country'
              ? t('accounts.selectCountryDesc')
              : t('accounts.selectBankDesc')}
          </p>
        </DialogHeader>

        {!redirecting && supportsAssetSync && (
          <div className="flex items-start justify-between gap-4 rounded-lg border border-border p-3">
            <div className="space-y-1">
              <label htmlFor="oauth-sync-assets" className="text-sm font-medium text-foreground">
                {t('connections.syncAssets')}
              </label>
              <p className="text-xs text-muted-foreground">{t('connections.syncAssetsHint')}</p>
            </div>
            <input
              id="oauth-sync-assets"
              type="checkbox"
              checked={syncAssets}
              onChange={(e) => setSyncAssets(e.target.checked)}
              className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-primary"
            />
          </div>
        )}

        {redirecting ? (
          <div className="py-12 flex flex-col items-center gap-3">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <p className="text-sm text-muted-foreground">{t('accounts.redirecting')}</p>
          </div>
        ) : loading ? (
          <div className="flex justify-center py-8">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          </div>
        ) : error ? (
          <div className="py-8 text-center text-sm text-destructive">{error}</div>
        ) : step === 'country' ? (
          <div className="space-y-1 pt-2 max-h-[60vh] overflow-y-auto">
            {sortedCountries.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">
                {t('accounts.noInstitutionsFound')}
              </p>
            ) : (
              sortedCountries.map((code) => (
                <button
                  key={code}
                  onClick={() => handleCountrySelect(code)}
                  className="w-full flex items-center gap-3 rounded-lg border border-border p-3 text-left transition-colors hover:border-primary hover:bg-muted/50"
                >
                  <div className="w-8 h-8 rounded-md bg-muted flex items-center justify-center shrink-0">
                    <Globe size={14} className="text-muted-foreground" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground">{countryLabel(code)}</p>
                    <p className="text-xs text-muted-foreground">{code}</p>
                  </div>
                </button>
              ))
            )}
          </div>
        ) : (
          <div className="space-y-1 pt-2 max-h-[60vh] overflow-y-auto">
            {institutions.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">
                {t('accounts.noInstitutionsFound')}
              </p>
            ) : (
              institutions.map((inst) => (
                <button
                  key={`${inst.country}-${inst.name}`}
                  onClick={() => handleBankSelect(inst)}
                  className="w-full flex items-center gap-3 rounded-lg border border-border p-3 text-left transition-colors hover:border-primary hover:bg-muted/50"
                >
                  <div className="w-8 h-8 rounded-md bg-muted overflow-hidden flex items-center justify-center shrink-0">
                    {inst.logo ? (
                      <img
                        src={inst.logo}
                        alt=""
                        className="w-full h-full object-contain"
                        onError={(e) => {
                          (e.target as HTMLImageElement).style.display = 'none'
                        }}
                      />
                    ) : (
                      <Building2 size={14} className="text-muted-foreground" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground truncate">
                      {inst.display_name}
                    </p>
                  </div>
                </button>
              ))
            )}
            <div className="pt-2">
              <Button variant="ghost" size="sm" onClick={() => setStep('country')}>
                <ChevronLeft size={14} className="mr-1" />
                {t('accounts.back')}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
