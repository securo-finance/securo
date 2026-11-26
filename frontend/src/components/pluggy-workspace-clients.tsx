import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertCircle, Check, Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { admin, type WorkspacePluggyStatus } from '@/lib/api'

const QUERY_KEY = ['admin', 'pluggy-workspaces']

// Do not retain Axios errors containing the credential request body.
function credentialError(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return new Error(detail === 'pluggy_client_change' ? 'clientChange' : 'failed')
}

function useErrorToast() {
  const { t } = useTranslation()
  return (error: Error) =>
    toast.error(error.message === 'clientChange' ? t('providerSettings.workspaces.clientChanged') : t('common.error'))
}

function ClientDialog({ workspace, onClose }: { workspace: WorkspacePluggyStatus; onClose: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const showError = useErrorToast()
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')

  const save = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      try {
        await admin.setWorkspacePluggyClient(workspace.workspace_id, clientId, clientSecret)
      } catch (error) {
        throw credentialError(error)
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY })
      toast.success(t('admin.settings.updated'))
      onClose()
    },
    onError: showError,
  })
  const canSave = Boolean(clientId.trim() && clientSecret.trim()) && !save.isPending

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !save.isPending) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{workspace.name}</DialogTitle>
          <DialogDescription>{t('providerSettings.workspaces.dialogDescription')}</DialogDescription>
        </DialogHeader>
        <form
          aria-label={workspace.name}
          className="space-y-4"
          onSubmit={(event) => { event.preventDefault(); if (canSave) save.mutate() }}
        >
          <div className="space-y-2">
            <Label htmlFor="workspace-pluggy-client-id">{t('providerSettings.fields.pluggy_client_id')}</Label>
            <Input
              id="workspace-pluggy-client-id"
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              disabled={save.isPending}
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="workspace-pluggy-client-secret">{t('providerSettings.fields.pluggy_client_secret')}</Label>
            <Input
              id="workspace-pluggy-client-secret"
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              disabled={save.isPending}
              value={clientSecret}
              onChange={(event) => setClientSecret(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" disabled={save.isPending} onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!canSave}>
              {save.isPending && <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />}
              {t(`providerSettings.catalog.${save.isPending ? 'saving' : 'saveChanges'}`)}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function PluggyWorkspaceClients({ canStoreSecrets, readOnly = false }: { canStoreSecrets: boolean; readOnly?: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const showError = useErrorToast()
  const [editing, setEditing] = useState<WorkspacePluggyStatus | null>(null)
  const [removing, setRemoving] = useState<WorkspacePluggyStatus | null>(null)
  const { data } = useQuery({ queryKey: QUERY_KEY, queryFn: admin.workspacePluggyClients })

  const remove = useMutation({
    mutationFn: async (workspaceId: string) => {
      try {
        await admin.removeWorkspacePluggyClient(workspaceId)
      } catch (error) {
        throw credentialError(error)
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY })
      toast.success(t('admin.settings.updated'))
    },
    onError: showError,
    onSettled: () => setRemoving(null),
  })

  if (!data?.length) return null

  return (
    <div className="min-w-0 overflow-hidden rounded-xl border border-border/60 bg-card">
      <div className="border-b border-border/40 px-4 py-4 sm:px-5">
        <h3 className="text-sm font-semibold text-foreground">{t('providerSettings.workspaces.title')}</h3>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{t('providerSettings.workspaces.description')}</p>
      </div>
      <ul className="divide-y divide-border/40">
        {data.map((workspace) => (
          <li key={workspace.workspace_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-4 sm:px-5">
            <div className="min-w-0 flex-1 basis-1/2">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <p className="text-sm font-medium text-foreground">{workspace.name}</p>
                {workspace.invalid ? (
                  <Badge variant="outline" className="border-destructive/20 bg-destructive/5 text-destructive">
                    <AlertCircle aria-hidden="true" />
                    {t('providerSettings.workspaces.invalid')}
                  </Badge>
                ) : workspace.has_credentials ? (
                  <Badge variant="outline" className="border-primary/15 bg-primary/5 text-primary">
                    <Check aria-hidden="true" />
                    {t('providerSettings.workspaces.own')}
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-muted-foreground">{t('providerSettings.workspaces.shared')}</Badge>
                )}
              </div>
              <p className="mt-1 break-all text-xs text-muted-foreground">
                {workspace.owner_email && <>{workspace.owner_email} · </>}
                {t('providerSettings.workspaces.connections', { count: workspace.connections })}
              </p>
            </div>
            {readOnly ? (
              <span className="text-xs font-medium text-primary">{t('admin.settings.managedReadOnly')}</span>
            ) : (
            <div className="flex items-center gap-2">
              {workspace.has_credentials && (
                <Button
                  variant="ghost"
                  className="h-9 shadow-none"
                  aria-label={`${t('providerSettings.workspaces.remove')}: ${workspace.name}`}
                  onClick={() => setRemoving(workspace)}
                >
                  {t('providerSettings.workspaces.remove')}
                </Button>
              )}
              <Button
                variant="outline"
                className="h-9 shadow-none"
                disabled={!canStoreSecrets}
                aria-label={`${t(`providerSettings.workspaces.${workspace.has_credentials ? 'replace' : 'set'}`)}: ${workspace.name}`}
                onClick={() => setEditing(workspace)}
              >
                {t(`providerSettings.workspaces.${workspace.has_credentials ? 'replace' : 'set'}`)}
              </Button>
            </div>
            )}
          </li>
        ))}
      </ul>
      {editing && <ClientDialog workspace={editing} onClose={() => setEditing(null)} />}
      {removing && (
        <DeleteConfirmationDialog
          open
          title={t('providerSettings.workspaces.removeTitle', { workspace: removing.name })}
          description={t('providerSettings.workspaces.removeDescription')}
          isPending={remove.isPending}
          onClose={() => setRemoving(null)}
          onConfirm={() => remove.mutate(removing.workspace_id)}
        />
      )}
    </div>
  )
}
