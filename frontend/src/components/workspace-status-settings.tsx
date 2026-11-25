import { useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

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
import { Input } from '@/components/ui/input'
import { useWorkspace } from '@/contexts/workspace-context'
import { admin } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import type { AdminWorkspace, WorkspaceStatus } from '@/types'

export function WorkspaceStatusSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { refresh } = useWorkspace()
  const [search, setSearch] = useState('')
  const [confirming, setConfirming] = useState<AdminWorkspace | null>(null)
  const { data, isError, refetch } = useQuery({
    queryKey: ['admin', 'workspaces', search],
    queryFn: () => admin.listWorkspaces({ search: search || undefined }),
    placeholderData: keepPreviousData,
  })
  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: WorkspaceStatus }) =>
      admin.updateWorkspaceStatus(id, status),
    onSuccess: () => {
      setConfirming(null)
      void queryClient.invalidateQueries({ queryKey: ['admin', 'workspaces'] })
      // The administrator may belong to the workspace that just changed.
      void refresh()
      toast.success(t('workspaceStatus.updated'))
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  return (
    <section className="mb-8 rounded-xl border border-border/60 bg-card overflow-hidden">
      <div className="px-5 py-4 border-b border-border/40">
        <div className="flex items-center gap-2 mb-0.5">
          <Building2 size={15} className="text-muted-foreground" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-foreground">{t('workspaceStatus.title')}</h2>
        </div>
        <p className="text-sm text-muted-foreground">{t('workspaceStatus.description')}</p>
      </div>
      <div className="p-5 space-y-3">
        <Input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label={t('workspaceStatus.search')}
          placeholder={t('workspaceStatus.search')}
          className="h-10 max-w-md"
        />
        {isError ? (
          <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-destructive">
            <span>{t('common.error')}</span>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        ) : !data ? (
          <p role="status" className="text-sm text-muted-foreground">{t('common.loading')}</p>
        ) : data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('workspaceStatus.empty')}</p>
        ) : (
          <ul className="max-h-96 divide-y divide-border/40 overflow-y-auto">
            {data.items.map((workspace) => {
              const suspended = workspace.status === 'suspended'
              return (
                <li key={workspace.id} className="flex items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{workspace.name}</span>
                      {suspended && (
                        <Badge variant="secondary">{t('workspaceStatus.suspended')}</Badge>
                      )}
                    </div>
                    <p className="truncate text-xs text-muted-foreground">
                      {workspace.owners.join(', ')}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={update.isPending}
                    aria-label={t(`workspaceStatus.${suspended ? 'reactivateNamed' : 'suspendNamed'}`, {
                      name: workspace.name,
                    })}
                    onClick={() =>
                      suspended
                        ? update.mutate({ id: workspace.id, status: 'active' })
                        : setConfirming(workspace)
                    }
                  >
                    {t(`workspaceStatus.${suspended ? 'reactivate' : 'suspend'}`)}
                  </Button>
                </li>
              )
            })}
          </ul>
        )}
        {data && data.items.length < data.total && (
          <p className="text-xs text-muted-foreground">
            {t('workspaceStatus.showing', { shown: data.items.length, total: data.total })}
          </p>
        )}
      </div>
      <Dialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('workspaceStatus.confirmTitle', { name: confirming?.name })}</DialogTitle>
            <DialogDescription>{t('workspaceStatus.confirmDescription')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              disabled={update.isPending}
              onClick={() =>
                confirming && update.mutate({ id: confirming.id, status: 'suspended' })
              }
            >
              {t('workspaceStatus.suspend')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}
