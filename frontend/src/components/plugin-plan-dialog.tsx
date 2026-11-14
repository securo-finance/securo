import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Globe, KeyRound, RefreshCw, ShieldAlert, ShieldCheck, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { auth as authApi, plugins as pluginsApi } from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { pluginThemeId } from '@/lib/themes'
import { useDescribe } from '@/hooks/use-describe-plugin-items'
import { useWorkspace } from '@/contexts/workspace-context'
import { localized } from '@/lib/localized-text'
import { scopeLabel } from '@/lib/plugin-format'
import type { PluginAction, PluginCodeConsent, PluginItem, PluginPlan } from '@/types'

/** A plugin and what the user asked to do with it. */
export interface PluginTarget {
  id: string
  name: string
  action: PluginAction
}

/** Item names listed before the rest are summed up as "and N more". */
const NAMES_SHOWN = 5

/** The preview of installing, updating or removing a plugin, and its confirmation. */
export function PluginPlanDialog({ target, onClose }: { target: PluginTarget; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const describe = useDescribe()
  const queryClient = useQueryClient()
  const { canManage } = useWorkspace()
  const { user, updateUser } = useAuth()

  const { data: plan, isFetching, isError, refetch } = useQuery({
    queryKey: ['plugins', 'plan', target.id, target.action],
    queryFn: () => pluginsApi.plan(target.id, target.action),
    // Every opening starts from a fresh plan, never a cached one.
    gcTime: 0,
    retry: 1,
  })

  const applyMutation = useMutation({
    mutationFn: (confirmed: PluginPlan) => pluginsApi.apply(target.id, confirmed),
    onSuccess: (_result, confirmed) => {
      // Both group keys are in use across pages; refresh every view of what changed.
      for (const key of ['plugins', 'plugin-connections', 'categories', 'category-groups', 'categoryGroups', 'rules']) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
      // Someone using a theme that left the library is back on Securo's own: pick that up.
      if (confirmed.themes?.removed.length) authApi.me().then(updateUser).catch(() => {})
      toast.success(t(`plugins.done.${target.action}`, { name: target.name }))
      onClose()
    },
    onError: (error: unknown) => {
      const err = error as { response?: { status?: number } }
      if (err?.response?.status === 403) {
        toast.error(t('plugins.code.ownerOnly'))
      } else if (err?.response?.status === 409) {
        // The plugin or the workspace changed since this plan: show the current one.
        toast.error(t('plugins.planChanged'))
        void refetch()
      } else {
        toast.error(t('common.error'))
      }
    },
  })

  const pending = applyMutation.isPending
  // A failed refresh counts as failed even with an older plan on screen:
  // that plan is exactly what the server would refuse, so it isn't shown.
  const failed = isError && !isFetching
  const shown = !failed ? plan : undefined
  const addedThemes = (shown?.themes?.added ?? []).map((theme) => localized(theme.name, i18n.language))
  const removedThemes = shown?.themes?.removed ?? []
  // The theme this person uses leaves the library: they go back to Securo's own.
  const themeInUse = removedThemes.find((theme) => user?.theme === pluginThemeId(target.id, theme.key))
  const changes = shown
    ? shown.create.length + shown.update.length + shown.delete.length + shown.release.length +
        addedThemes.length + removedThemes.length > 0
    : false
  // A plan that touches no item still records something: the installed version
  // moves, or an installation whose items are all gone is taken off the list.
  const code = shown?.code ?? null
  // Code runs on the server: only the owner adds, updates or removes it.
  const allowed = !code || canManage
  const confirmable = shown !== undefined && !isFetching && allowed

  const lines: { key: string; items: PluginItem[]; names: boolean }[] = shown
    ? [
        { key: 'create', items: shown.create, names: false },
        { key: 'update', items: shown.update, names: false },
        { key: 'delete', items: shown.delete, names: false },
        { key: 'keep', items: shown.keep, names: true },
        { key: 'release', items: shown.release, names: true },
        { key: 'existing', items: shown.existing, names: true },
        { key: 'unresolved', items: shown.unresolved, names: true },
      ].filter((line) => line.items.length > 0)
    : []

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !pending) onClose() }}>
      <DialogContent className="sm:max-w-md" showCloseButton={!pending}>
        <DialogHeader>
          <DialogTitle>{t(`plugins.planTitle.${target.action}`, { name: target.name })}</DialogTitle>
          <DialogDescription>
            {failed
              ? t('plugins.planFailed')
              : !shown
                ? t('common.loading')
                : changes
                  ? t('plugins.planIntro')
                  : shown.code
                    ? t(`plugins.code.intro.${target.action}`)
                    : t(`plugins.planRecordOnly.${target.action}`, { version: shown.version ?? '' })}
          </DialogDescription>
        </DialogHeader>

        {/* Long consent lists scroll; the buttons below always stay in reach. */}
        <div className="-mx-1 max-h-[55vh] space-y-4 overflow-y-auto px-1">

        {addedThemes.length > 0 && (
          <p className="text-sm">{t('plugins.plan.themes', { names: addedThemes.join(', ') })}</p>
        )}
        {removedThemes.length > 0 && (
          <p className="text-sm">
            {t('plugins.plan.themesRemove', {
              names: removedThemes.map((theme) => localized(theme.name, i18n.language)).join(', '),
            })}
          </p>
        )}
        {themeInUse && (
          <p className="rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            {t('plugins.plan.themeInUse', { name: localized(themeInUse.name, i18n.language) })}
          </p>
        )}

        {lines.length > 0 && (
          <ul className="space-y-2 text-sm">
            {lines.map((line) => (
              <li key={line.key}>
                <p>{t(`plugins.plan.${line.key}`, { items: describe(line.items) })}</p>
                {line.names && (
                  <p className="text-xs text-muted-foreground">
                    {line.items.slice(0, NAMES_SHOWN).map((i) => i.name).join(', ')}
                    {line.items.length > NAMES_SHOWN &&
                      ` ${t('plugins.andMore', { count: line.items.length - NAMES_SHOWN })}`}
                  </p>
                )}
              </li>
            ))}
            {shown && shown.default_categories.length > 0 && (
              <li>{t('plugins.plan.defaults', { count: shown.default_categories.length })}</li>
            )}
          </ul>
        )}

        {code && <CodeConsent code={code} action={target.action} />}

        {shown && !code && (
          <div className="flex items-start gap-2 rounded-lg border bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground">
            <ShieldCheck size={14} className="mt-0.5 shrink-0" />
            <span>{target.action === 'remove' ? t('plugins.history') : t('plugins.privacy')}</span>
          </div>
        )}

        {code && !allowed && (
          <p className="text-xs text-muted-foreground">{t('plugins.code.ownerOnly')}</p>
        )}

        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={pending}>
            {t('common.cancel')}
          </Button>
          {failed ? (
            <Button variant="outline" onClick={() => void refetch()} disabled={isFetching}>
              <RefreshCw size={14} className="mr-1" />
              {t('common.retry')}
            </Button>
          ) : (
            <Button
              variant={target.action === 'remove' ? 'destructive' : 'default'}
              onClick={() => shown && applyMutation.mutate(shown)}
              disabled={pending || !confirmable}
            >
              {target.action === 'remove' && <Trash2 size={14} className="mr-1" />}
              {t(`plugins.confirm.${target.action}`)}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** What a plugin with code may do, shown before the owner agrees to it. */
function CodeConsent({ code, action }: { code: PluginCodeConsent; action: PluginAction }) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language

  // Removing, or updating to a version without code: say what is taken away.
  if (action === 'remove' || code.drops_code) {
    return (
      <div className="flex items-start gap-2 rounded-lg border bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground">
        <ShieldCheck size={14} className="mt-0.5 shrink-0" />
        <span>
          {code.connections > 0
            ? t('plugins.code.removeConnections', { count: code.connections })
            : t('plugins.code.removeNothing')}
        </span>
      </div>
    )
  }

  const isNew = (value: string, list: string[]) =>
    list.includes(value) && (
      <span className="ml-1.5 rounded bg-amber-100 px-1 py-px text-[10px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
        {t('plugins.code.new')}
      </span>
    )

  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-start gap-2 rounded-lg border border-amber-300/70 bg-amber-50 px-3 py-2.5 text-xs text-amber-900 dark:border-amber-700/40 dark:bg-amber-900/20 dark:text-amber-200">
        <ShieldAlert size={14} className="mt-0.5 shrink-0" />
        <span>{t('plugins.code.runsCode')}</span>
      </div>

      <section className="space-y-1.5">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <Globe size={12} />
          {t('plugins.code.network')}
        </h3>
        {code.network.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t('plugins.code.noNetwork')}</p>
        ) : (
          <ul className="space-y-1.5">
            {code.network.map((host) => (
              <li key={host.host}>
                <p className="font-mono text-xs">
                  {host.host}
                  {isNew(host.host, code.new_hosts)}
                </p>
                <p className="text-xs text-muted-foreground">{localized(host.purpose, lang)}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-1.5">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <KeyRound size={12} />
          {t('plugins.code.access')}
        </h3>
        {code.api_scopes.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t('plugins.code.noAccess')}</p>
        ) : (
          <ul className="space-y-1.5">
            {code.api_scopes.map((scope) => (
              <li key={scope.scope}>
                <p className="text-xs font-medium">
                  {scopeLabel(scope.scope, t)}
                  {isNew(scope.scope, code.new_scopes)}
                </p>
                <p className="text-xs text-muted-foreground">{localized(scope.purpose, lang)}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

