import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import axios from 'axios'
import { Copy, LogIn, Plus, RefreshCw, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { useWorkspace } from '@/contexts/workspace-context'
import { plugins as pluginsApi } from '@/lib/api'
import { localized } from '@/lib/localized-text'
import { relativeDate } from '@/lib/plugin-format'
import { cn } from '@/lib/utils'
import type { InstalledPlugin, PluginConnection, PluginIntegration } from '@/types'

const STATUS_STYLE: Record<PluginConnection['status'], string> = {
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  pending: 'bg-muted text-muted-foreground',
  error: 'bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300',
  needs_auth: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
}

function errorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err) && typeof err.response?.data?.detail === 'string') return err.response.data.detail
  return fallback
}

/** The connections a workspace made through one installed plugin with code. */
export function PluginConnections({ plugin }: { plugin: InstalledPlugin }) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<PluginConnection | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['plugin-connections'],
    queryFn: pluginsApi.connections,
    // A new connection syncs in the background: follow it until it settles.
    refetchInterval: (query) => (query.state.data?.some((c) => c.status === 'pending') ? 3000 : false),
  })
  const connections = (data ?? []).filter((c) => c.plugin_id === plugin.plugin_id)
  const integrations = new Map(plugin.integrations.map((i) => [i.key, i]))

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['plugin-connections'] })

  const syncMutation = useMutation({
    mutationFn: pluginsApi.sync,
    onSuccess: (connection) => {
      void refresh()
      if (connection.status === 'active') toast.success(t('plugins.connections.synced', { name: connection.name }))
      else toast.error(connection.last_error || t('plugins.connections.syncFailed'))
    },
    onError: (err) => toast.error(errorMessage(err, t('plugins.connections.syncFailed'))),
  })

  const authorizeMutation = useMutation({
    mutationFn: pluginsApi.authorize,
    onSuccess: ({ authorize_url }) => window.location.assign(authorize_url),
    onError: (err) => toast.error(errorMessage(err, t('common.error'))),
  })

  return (
    <section className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h2 className="text-sm font-semibold">{t('plugins.connections.title')}</h2>
          <p className="max-w-[65ch] text-sm text-muted-foreground">{t('plugins.connections.hint')}</p>
        </div>
        {canWrite && (
          <Button size="sm" onClick={() => setAdding(true)}>
            <Plus size={14} className="mr-1" />
            {t('plugins.connections.add')}
          </Button>
        )}
      </div>

      {isLoading ? (
        <Skeleton className="h-16 w-full rounded-xl" />
      ) : isError ? (
        <div className="flex items-center justify-between gap-3 rounded-xl border px-4 py-3 text-sm">
          <span className="text-muted-foreground">{t('plugins.connections.loadFailed')}</span>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            <RefreshCw size={14} className="mr-1" />
            {t('common.retry')}
          </Button>
        </div>
      ) : connections.length === 0 ? (
        <p className="rounded-xl border border-dashed px-6 py-8 text-center text-sm text-muted-foreground">
          {t('plugins.connections.none')}
        </p>
      ) : (
        <ul className="divide-y rounded-xl border bg-card">
          {connections.map((connection) => {
            const integration = integrations.get(connection.integration)
            // Sign-in integrations sign in again; for keys, "needs auth" means the key was refused.
            const signIn = connection.status === 'needs_auth' && integration?.auth === 'oauth'
            const statusKey =
              connection.status === 'needs_auth' && integration?.auth !== 'oauth' ? 'keysRefused' : connection.status
            const busy =
              (syncMutation.isPending && syncMutation.variables === connection.id) ||
              (authorizeMutation.isPending && authorizeMutation.variables === connection.id)
            return (
              <li key={connection.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1 space-y-0.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-medium">{connection.name}</p>
                    <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', STATUS_STYLE[connection.status])}>
                      {t(`plugins.connections.status.${statusKey}`)}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {integration ? localized(integration.name, lang) : connection.integration}
                    {' · '}
                    {connection.last_sync_at
                      ? t('plugins.connections.lastSync', { when: relativeDate(connection.last_sync_at, lang) })
                      : t('plugins.connections.neverSynced')}
                  </p>
                  {connection.last_error && connection.status !== 'active' && (
                    <p className="text-xs text-rose-700 dark:text-rose-300">{connection.last_error}</p>
                  )}
                </div>
                {canWrite && (
                  <div className="flex items-center gap-1">
                    {signIn ? (
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => authorizeMutation.mutate(connection.id)}>
                        <LogIn size={14} className="mr-1" />
                        {t('plugins.connections.signIn')}
                      </Button>
                    ) : (
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => syncMutation.mutate(connection.id)}>
                        <RefreshCw size={14} className={cn('mr-1', busy && 'animate-spin')} />
                        {t('plugins.connections.syncNow')}
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                      aria-label={t('plugins.connections.removeAria', { name: connection.name })}
                      title={t('plugins.connections.remove')}
                      onClick={() => setRemoving(connection)}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {adding && (
        <AddConnectionDialog plugin={plugin} onClose={() => setAdding(false)} onDone={() => void refresh()} />
      )}
      {removing && (
        <RemoveConnectionDialog connection={removing} onClose={() => setRemoving(null)} onDone={() => void refresh()} />
      )}
    </section>
  )
}

function AddConnectionDialog({
  plugin,
  onClose,
  onDone,
}: {
  plugin: InstalledPlugin
  onClose: () => void
  onDone: () => void
}) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language
  const [key, setKey] = useState(plugin.integrations[0]?.key ?? '')
  const [name, setName] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})
  const integration: PluginIntegration | undefined = plugin.integrations.find((i) => i.key === key)
  const redirectUri = plugin.redirect_uri ?? `${window.location.origin}/plugins/oauth/callback`

  const mutation = useMutation({
    mutationFn: () => pluginsApi.connect({ plugin_id: plugin.plugin_id, integration: key, name, values }),
    onSuccess: ({ connection, authorize_url }) => {
      onDone()
      if (authorize_url) {
        window.location.assign(authorize_url)
        return
      }
      toast.success(t('plugins.connections.added', { name: connection.name }))
      onClose()
    },
    onError: (err) => toast.error(errorMessage(err, t('common.error'))),
  })

  const missing = integration?.fields.some((f) => f.required && !values[f.key]?.trim()) ?? true

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !mutation.isPending) onClose() }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('plugins.connections.addTitle', { name: localized(plugin.name, lang) })}</DialogTitle>
          <DialogDescription>{t('plugins.connections.addHint')}</DialogDescription>
        </DialogHeader>

        <form
          id="plugin-connection-form"
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!missing) mutation.mutate()
          }}
        >
          {plugin.integrations.length > 1 && (
            <div className="space-y-2">
              <Label>{t('plugins.connections.integration')}</Label>
              <div className="grid gap-2">
                {plugin.integrations.map((option) => (
                  <button
                    key={option.key}
                    type="button"
                    onClick={() => { setKey(option.key); setValues({}) }}
                    className={cn(
                      'rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                      option.key === key ? 'border-primary bg-primary/5' : 'hover:bg-muted/50',
                    )}
                  >
                    <span className="font-medium">{localized(option.name, lang)}</span>
                    {option.description && (
                      <span className="block text-xs text-muted-foreground">{localized(option.description, lang)}</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="plugin-connection-name">{t('plugins.connections.name')}</Label>
            <Input
              id="plugin-connection-name"
              value={name}
              maxLength={100}
              placeholder={integration ? localized(integration.name, lang) : ''}
              onChange={(event) => setName(event.target.value)}
            />
          </div>

          {integration?.fields.map((field) => (
            <div key={field.key} className="space-y-2">
              <Label htmlFor={`plugin-field-${field.key}`}>
                {localized(field.label, lang)}
                {!field.required && <span className="ml-1 font-normal text-muted-foreground">({t('plugins.connections.optional')})</span>}
              </Label>
              <Input
                id={`plugin-field-${field.key}`}
                type={field.secret ? 'password' : 'text'}
                autoComplete="off"
                value={values[field.key] ?? ''}
                onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))}
              />
            </div>
          ))}

          {integration?.fields.some((f) => f.secret) && (
            <p className="text-xs text-muted-foreground">{t('plugins.connections.secretNote')}</p>
          )}

          {integration?.auth === 'oauth' && (
            <div className="space-y-1.5 rounded-lg border bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground">
              <p>{t('plugins.connections.redirectHint')}</p>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate font-mono text-foreground">{redirectUri}</code>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 w-7 p-0"
                  aria-label={t('plugins.connections.copy')}
                  onClick={() => {
                    void navigator.clipboard?.writeText(redirectUri)
                    toast.success(t('plugins.connections.copied'))
                  }}
                >
                  <Copy size={13} />
                </Button>
              </div>
            </div>
          )}
        </form>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="plugin-connection-form" disabled={mutation.isPending || missing || !integration}>
            {integration?.auth === 'oauth' ? t('plugins.connections.continueSignIn') : t('plugins.connections.connect')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RemoveConnectionDialog({
  connection,
  onClose,
  onDone,
}: {
  connection: PluginConnection
  onClose: () => void
  onDone: () => void
}) {
  const { t } = useTranslation()
  const mutation = useMutation({
    mutationFn: () => pluginsApi.disconnect(connection.id),
    onSuccess: () => {
      onDone()
      toast.success(t('plugins.connections.removed', { name: connection.name }))
      onClose()
    },
    onError: (err) => toast.error(errorMessage(err, t('common.error'))),
  })
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !mutation.isPending) onClose() }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('plugins.connections.removeTitle', { name: connection.name })}</DialogTitle>
          <DialogDescription>{t('plugins.connections.removeHint')}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            {t('common.cancel')}
          </Button>
          <Button variant="destructive" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            <Trash2 size={14} className="mr-1" />
            {t('plugins.connections.remove')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
