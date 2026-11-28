import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import axios from 'axios'

import { plugins as pluginsApi } from '@/lib/api'
import { invalidateSyncedRecords } from '@/lib/invalidate-queries'
import { pluginPath } from '@/lib/plugin-format'
import { clearPluginOAuthState, peekPluginOAuthState } from '@/lib/plugin-oauth'

/**
 * States already sent from this page load. StrictMode mounts twice, and the
 * state is single use, so it is sent once; kept in memory rather than in
 * storage, so a reload after a crash still finishes the sign-in.
 */
const submitted = new Set<string>()

/** Where a service sends the browser back after signing in to a plugin's integration. */
export default function PluginOAuthCallbackPage() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const code = params.get('code')
  const urlState = params.get('state')
  const [storedState] = useState<string | null>(() => (urlState ? null : peekPluginOAuthState()))
  const state = urlState ?? storedState
  const error = params.get('error_description') || params.get('error')
  const queryString = params.toString()

  useEffect(() => {
    if (error || !state) {
      clearPluginOAuthState()
      toast.error(error ? t('plugins.oauth.serviceError', { message: error }) : t('plugins.oauth.missing'))
      navigate('/plugins', { replace: true })
      return
    }
    if (submitted.has(state)) return
    submitted.add(state)
    const callbackParams: Record<string, string> = {}
    for (const [key, value] of new URLSearchParams(queryString).entries()) {
      if (key !== 'state') callbackParams[key] = value
    }
    ;(async () => {
      try {
        const connection = await pluginsApi.oauthCallback(code, state, callbackParams)
        clearPluginOAuthState()
        await queryClient.invalidateQueries({ queryKey: ['plugin-connections'] })
        invalidateSyncedRecords(queryClient)
        toast.success(t('plugins.oauth.connected', { name: connection.name }))
        navigate(`${pluginPath(connection.plugin_id)}?tab=connections`, { replace: true })
      } catch (err) {
        submitted.delete(state)
        clearPluginOAuthState()
        const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
        toast.error(typeof detail === 'string' ? detail : t('plugins.oauth.failed'))
        navigate('/plugins', { replace: true })
      }
    })()
  }, [code, state, error, queryString, navigate, queryClient, t])

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-5 px-4 text-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      <div className="max-w-md space-y-1.5">
        <p className="text-base font-medium">{t('plugins.oauth.title')}</p>
        <p className="text-sm text-muted-foreground">{t('plugins.oauth.wait')}</p>
      </div>
    </div>
  )
}
