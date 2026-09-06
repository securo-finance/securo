import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import axios from 'axios'
import { connections } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { Button } from '@/components/ui/button'
import { Building2, ExternalLink } from 'lucide-react'

type RestrictedDetail = {
  message?: string
  code?: string
  help_url?: string | null
}

export default function OAuthCallbackPage() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const ref = params.get('ref')
  const code = params.get('code') ?? ref
  const state = params.get('state') ?? ref
  const errorParam = params.get('error')
  const errorDescription = params.get('error_description')
  const [restricted, setRestricted] = useState<RestrictedDetail | null>(null)
  const [retrying, setRetrying] = useState(false)

  useEffect(() => {
    // Provider reported an error during the consent step.
    if (errorParam) {
      toast.error(
        t('accounts.oauthCallback.providerError', {
          message: errorDescription || errorParam,
        }),
      )
      navigate('/accounts', { replace: true })
      return
    }
    if (!code || !state) {
      toast.error(t('accounts.oauthCallback.missingState'))
      navigate('/accounts', { replace: true })
      return
    }
    // The state token is single-use server-side, so React StrictMode's
    // double-mount in dev (or any accidental remount) would fire a second
    // POST that gets rejected. Guard via sessionStorage so only the first
    // mount actually submits — and don't cancel on unmount; we want the
    // side-effect to finish even if the user navigates away mid-sync.
    const submitKey = `oauth-submitted:${code}:${state}`
    if (sessionStorage.getItem(submitKey)) return
    sessionStorage.setItem(submitKey, '1')

    ;(async () => {
      try {
        await connections.handleCallback(code, '', state)
        await queryClient.refetchQueries({ queryKey: ['connections'] })
        invalidateFinancialQueries(queryClient)
        toast.success(t('accounts.connected'))
        navigate('/accounts', { replace: true })
      } catch (err) {
        if (axios.isAxiosError(err) && err.response?.status === 409) {
          const detail = err.response.data?.detail as RestrictedDetail | string | undefined
          if (typeof detail === 'object' && detail?.code === 'no_accounts_linked') {
            // Let the user retry: clear the guard so a fresh consent flow works.
            sessionStorage.removeItem(submitKey)
            setRestricted(detail)
            return
          }
        }
        const message =
          axios.isAxiosError(err) && err.response?.data?.detail
            ? String(err.response.data.detail)
            : t('accounts.connectError')
        sessionStorage.removeItem(submitKey)
        toast.error(message)
        navigate('/accounts', { replace: true })
      }
    })()
  }, [code, state, errorParam, errorDescription, navigate, queryClient, t, retrying])

  if (restricted) {
    return (
      <div className="mx-auto max-w-md py-16 px-4">
        <div className="rounded-xl border border-amber-300 bg-amber-50 dark:border-amber-700/40 dark:bg-amber-900/20 p-6 space-y-4">
          <div className="flex items-start gap-3">
            <div className="rounded-lg bg-amber-100 dark:bg-amber-800/30 p-2 shrink-0">
              <Building2 size={18} className="text-amber-700 dark:text-amber-300" />
            </div>
            <div className="space-y-1">
              <h2 className="text-base font-semibold">
                {t('accounts.oauthCallback.linkAccountsFirstTitle')}
              </h2>
              <p className="text-sm text-muted-foreground">
                {t('accounts.oauthCallback.linkAccountsFirstDesc')}
              </p>
            </div>
          </div>
          <div className="flex flex-col gap-2">
            {restricted.help_url && (
              <Button asChild variant="outline">
                <a href={restricted.help_url} target="_blank" rel="noreferrer">
                  {t('accounts.oauthCallback.openPortal')}
                  <ExternalLink size={14} className="ml-2" />
                </a>
              </Button>
            )}
            <Button
              onClick={() => {
                setRestricted(null)
                setRetrying((v) => !v)
              }}
            >
              {t('accounts.oauthCallback.retry')}
            </Button>
            <Button variant="ghost" onClick={() => navigate('/accounts', { replace: true })}>
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-5 px-4 text-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      <div className="space-y-1.5 max-w-md">
        <p className="text-base font-medium">{t('accounts.oauthCallback.title')}</p>
        <p className="text-sm text-muted-foreground">
          {t('accounts.oauthCallback.linking')}
        </p>
        <p className="text-xs text-muted-foreground">
          {t('accounts.oauthCallback.dontClose')}
        </p>
      </div>
    </div>
  )
}
