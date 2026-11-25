import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { useWorkspace } from '@/contexts/workspace-context'
import { notifications as notificationsApi } from '@/lib/api'
import { isKnownKind, NOTIFICATION_ICONS } from '@/lib/notification-text'
import type { AppNotificationKind, NotificationPreference } from '@/types'

/** The only channel there is so far; another one would be a switch beside it. */
const IN_APP = 'in_app'

/** What this person wants to hear of in this workspace, one switch per kind. */
export function NotificationPreferences() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { current } = useWorkspace()
  const queryKey = ['notification-preferences', current?.id]
  const { data, isError, refetch } = useQuery({
    queryKey,
    queryFn: notificationsApi.preferences,
    enabled: !!current,
  })
  const put = (change: { kind: AppNotificationKind; channel: string; enabled: boolean; workspaceId?: string }) =>
    queryClient.setQueryData<NotificationPreference[]>(['notification-preferences', change.workspaceId], (rows) =>
      rows?.map((row) =>
        row.kind === change.kind
          ? { ...row, channels: { ...row.channels, [change.channel]: change.enabled } }
          : row,
      ),
    )
  const save = useMutation({
    mutationKey: queryKey,
    // One at a time and in order, so the last click is the last one saved.
    scope: { id: 'notification-preferences' },
    mutationFn: notificationsApi.setPreference,
    // The switch answers at once, and goes back if it could not be saved.
    onMutate: async (change) => {
      await queryClient.cancelQueries({ queryKey })
      put(change)
    },
    onError: (_error, change) => {
      put({ ...change, enabled: !change.enabled })
      toast.error(t('notifications.preferences.saveError'))
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notifications'] }),
    // After the last of them, what is shown is what was saved.
    onSettled: () => {
      if (queryClient.isMutating({ mutationKey: queryKey }) === 1) return queryClient.invalidateQueries({ queryKey })
    },
  })

  if (isError && !data) {
    return (
      <div className="rounded-xl border border-border bg-card px-6 py-14 text-center text-sm text-muted-foreground">
        <p>{t('notifications.preferences.loadError')}</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => void refetch()}>
          {t('common.retry')}
        </Button>
      </div>
    )
  }
  if (!data) return <Skeleton className="h-64 w-full rounded-xl" />

  return (
    <div className="space-y-3">
      <p className="px-1 text-sm text-muted-foreground">{t('notifications.preferences.hint')}</p>
      <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
        {data.filter(({ kind }) => isKnownKind(kind)).map(({ kind, channels }) => {
          const Icon = NOTIFICATION_ICONS[kind]
          const labelId = `notification-preference-${kind}`
          return (
            <li key={kind} className="flex items-center gap-3 px-4 py-3">
              <span
                aria-hidden
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"
              >
                <Icon size={16} />
              </span>
              <div className="min-w-0 flex-1">
                <p id={labelId} className="text-sm font-medium">
                  {t(`notifications.kind.${kind}.label`)}
                </p>
                <p className="text-xs text-muted-foreground">{t(`notifications.kind.${kind}.hint`)}</p>
              </div>
              <Switch
                checked={channels[IN_APP]}
                aria-labelledby={labelId}
                onCheckedChange={(next) => save.mutate({ kind, channel: IN_APP, enabled: next, workspaceId: current?.id })}
              />
            </li>
          )
        })}
      </ul>
    </div>
  )
}
