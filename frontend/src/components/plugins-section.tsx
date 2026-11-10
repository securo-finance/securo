import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Puzzle, RefreshCw, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { PluginPlanDialog, type PluginTarget } from '@/components/plugin-plan-dialog'
import { useWorkspace } from '@/contexts/workspace-context'
import { plugins as pluginsApi } from '@/lib/api'
import { localized } from '@/lib/localized-text'

/** Workspace settings section: installed plugins, and the way to the catalog. */
export function PluginsSection() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { canWrite } = useWorkspace()
  const [target, setTarget] = useState<PluginTarget | null>(null)

  // The database only: opening settings never reaches the catalog.
  const { data: installed, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ['plugins', 'installed'],
    queryFn: pluginsApi.installed,
  })

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
        <Button variant="outline" size="sm" onClick={() => navigate('/plugins')}>
          {t('plugins.browse')}
        </Button>
      </div>

      {isLoading ? (
        <Skeleton className="h-14 w-full" />
      ) : isError && !installed ? (
        // Not "none installed": that would hide plugins the user may want to remove.
        <div className="flex items-center justify-between gap-3 rounded-lg border px-4 py-3 text-sm">
          <span className="text-muted-foreground">{t('plugins.installedFailed')}</span>
          <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
            <RefreshCw size={14} className="mr-1" />
            {t('common.retry')}
          </Button>
        </div>
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

      {target && <PluginPlanDialog target={target} onClose={() => setTarget(null)} />}
    </section>
  )
}
