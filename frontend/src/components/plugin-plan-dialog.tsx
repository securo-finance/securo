import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { plugins as pluginsApi } from '@/lib/api'
import { useDescribe } from '@/hooks/use-describe-plugin-items'
import type { PluginAction, PluginItem, PluginPlan } from '@/types'

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
      // Both group keys are in use across pages; refresh every view of what changed.
      for (const key of ['plugins', 'categories', 'category-groups', 'categoryGroups', 'rules']) {
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
  // A plan that touches no item still records something: the installed version
  // moves, or an installation whose items are all gone is taken off the list.
  const confirmable = shown !== undefined && !isFetching

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
                  : t(`plugins.planRecordOnly.${target.action}`, { version: shown.version ?? '' })}
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
