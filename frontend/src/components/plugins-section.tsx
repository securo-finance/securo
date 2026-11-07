import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ExternalLink, Puzzle, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { useWorkspace } from '@/contexts/workspace-context'
import { plugins as pluginsApi } from '@/lib/api'
import { localized } from '@/lib/localized-text'
import type { PluginAction, PluginCatalogEntry, PluginItem, PluginItemKind, PluginPlan } from '@/types'

/** A plugin and what the user asked to do with it. */
interface Target {
  id: string
  name: string
  action: PluginAction
}

const KINDS: PluginItemKind[] = ['category_groups', 'categories', 'rules']

/** Item names listed before the rest are summed up as "and N more". */
const NAMES_SHOWN = 5

/** "1 group, 2 categories, 2 rules" for a list of items. */
function useDescribe() {
  const { t } = useTranslation()
  return (items: { kind: PluginItemKind }[] | Record<PluginItemKind, number>) => {
    const counts = Array.isArray(items)
      ? Object.fromEntries(KINDS.map((kind) => [kind, items.filter((i) => i.kind === kind).length]))
      : items
    return KINDS.filter((kind) => counts[kind] > 0)
      .map((kind) => t(`plugins.kind.${kind}`, { count: counts[kind] }))
      .join(', ')
  }
}

/** Workspace settings section: installed data plugins and the way into the catalog. */
export function PluginsSection() {
  const { t, i18n } = useTranslation()
  const { canWrite } = useWorkspace()
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [target, setTarget] = useState<Target | null>(null)

  // The database only: opening settings never reaches the catalog.
  const { data: installed, isLoading } = useQuery({ queryKey: ['plugins', 'installed'], queryFn: pluginsApi.installed })

  return (
    <section className="space-y-4 rounded-xl border bg-card p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Puzzle className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-base font-semibold">{t('plugins.title')}</h2>
          </div>
          <p className="text-sm text-muted-foreground">{t('plugins.hint')}</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setCatalogOpen(true)}>
          {t('plugins.browse')}
        </Button>
      </div>

      {isLoading ? (
        <Skeleton className="h-14 w-full" />
      ) : !installed?.length ? (
        <p className="text-sm text-muted-foreground">{t('plugins.noneInstalled')}</p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {installed.map((plugin) => {
            const name = localized(plugin.name, i18n.language)
            return (
              <li key={plugin.plugin_id} className="flex items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {plugin.plugin_id} · v{plugin.version}
                  </p>
                </div>
                {canWrite && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                    aria-label={t('plugins.removeAria', { name })}
                    title={t('plugins.remove')}
                    onClick={() => setTarget({ id: plugin.plugin_id, name, action: 'remove' })}
                  >
                    <Trash2 size={14} />
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {catalogOpen && (
        <CatalogDialog
          canWrite={canWrite}
          onClose={() => setCatalogOpen(false)}
          onAction={(entry, action) =>
            setTarget({ id: entry.id, name: localized(entry.name ?? { en: entry.id }, i18n.language), action })
          }
        />
      )}
      {target && <PlanDialog target={target} onClose={() => setTarget(null)} />}
    </section>
  )
}

function CatalogDialog({
  canWrite,
  onClose,
  onAction,
}: {
  canWrite: boolean
  onClose: () => void
  onAction: (entry: PluginCatalogEntry, action: PluginAction) => void
}) {
  const { t, i18n } = useTranslation()
  const describe = useDescribe()
  const { data: entries, isFetching, isError, refetch } = useQuery({
    queryKey: ['plugins', 'catalog'],
    queryFn: pluginsApi.catalog,
    // Fetched from the index each time the catalog is opened, never cached for later.
    gcTime: 0,
    retry: 1,
  })

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('plugins.catalogTitle')}</DialogTitle>
          <DialogDescription>{t('plugins.catalogHint')}</DialogDescription>
        </DialogHeader>

        {isError && !isFetching ? (
          <div className="flex items-center justify-between gap-3 rounded-lg border px-4 py-3 text-sm">
            <span className="text-muted-foreground">{t('plugins.catalogFailed')}</span>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              <RefreshCw size={14} className="mr-1" />
              {t('common.retry')}
            </Button>
          </div>
        ) : !entries ? (
          <div className="space-y-2">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : !entries.length ? (
          <p className="text-sm text-muted-foreground">{t('plugins.catalogEmpty')}</p>
        ) : (
          <ul className="max-h-[60vh] space-y-2 overflow-y-auto">
            {entries.map((entry) => (
              <li key={entry.id} className="space-y-2 rounded-lg border p-4">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold">
                        {entry.name ? localized(entry.name, i18n.language) : entry.id}
                      </p>
                      <EntryBadge entry={entry} />
                    </div>
                    {entry.description && (
                      <p className="text-sm text-muted-foreground">{localized(entry.description, i18n.language)}</p>
                    )}
                    <p className="text-xs text-muted-foreground">
                      {[
                        entry.id,
                        entry.version && `v${entry.version}`,
                        entry.license,
                        entry.contributes && describe(entry.contributes),
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </p>
                  </div>
                  <EntryAction entry={entry} canWrite={canWrite} onAction={(action) => onAction(entry, action)} />
                </div>
                <a
                  href={`${entry.repository}/tree/${entry.commit}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <ExternalLink size={12} />
                  {t('plugins.viewSource')}
                </a>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  )
}

function EntryBadge({ entry }: { entry: PluginCatalogEntry }) {
  const { t } = useTranslation()
  if (entry.revoked) return <Badge variant="destructive">{t('plugins.revoked')}</Badge>
  if (entry.error) return <Badge variant="outline" title={entry.error}>{t('plugins.unavailable')}</Badge>
  if (!entry.compatible) {
    return <Badge variant="outline">{t('plugins.needsVersion', { version: entry.min_securo_version })}</Badge>
  }
  if (entry.update_available) return <Badge variant="secondary">{t('plugins.updateAvailable')}</Badge>
  if (entry.installed_version) return <Badge variant="secondary">{t('plugins.installed')}</Badge>
  return null
}

function EntryAction({
  entry,
  canWrite,
  onAction,
}: {
  entry: PluginCatalogEntry
  canWrite: boolean
  onAction: (action: PluginAction) => void
}) {
  const { t } = useTranslation()
  if (!canWrite || entry.revoked || entry.error || !entry.compatible) return null
  if (entry.update_available) {
    return <Button size="sm" onClick={() => onAction('update')}>{t('plugins.update')}</Button>
  }
  if (entry.installed_version) return null
  return <Button size="sm" variant="outline" onClick={() => onAction('install')}>{t('plugins.install')}</Button>
}

function PlanDialog({ target, onClose }: { target: Target; onClose: () => void }) {
  const { t } = useTranslation()
  const describe = useDescribe()
  const queryClient = useQueryClient()

  const { data: plan, isFetching, isError, refetch } = useQuery({
    queryKey: ['plugins', 'plan', target.id, target.action],
    queryFn: () => pluginsApi.plan(target.id, target.action),
    // Every opening starts from a fresh plan, never a cached one.
    gcTime: 0,
    retry: 1,
  })

  const applyMutation = useMutation({
    mutationFn: (confirmed: PluginPlan) => pluginsApi.apply(target.id, confirmed),
    onSuccess: () => {
      for (const key of ['plugins', 'categories', 'category-groups', 'rules']) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
      toast.success(t(`plugins.done.${target.action}`, { name: target.name }))
      onClose()
    },
    onError: (error: unknown) => {
      const err = error as { response?: { status?: number } }
      if (err?.response?.status === 409) {
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
  const changes = shown
    ? shown.create.length + shown.update.length + shown.delete.length + shown.release.length > 0
    : false
  const confirmable = shown !== undefined && !isFetching && changes

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
                  : t('plugins.planNothing')}
          </DialogDescription>
        </DialogHeader>

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

        {shown && (
          <div className="flex items-start gap-2 rounded-lg border bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground">
            <ShieldCheck size={14} className="mt-0.5 shrink-0" />
            <span>{target.action === 'remove' ? t('plugins.history') : t('plugins.privacy')}</span>
          </div>
        )}

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
