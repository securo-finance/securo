import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bell } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { NotificationItem } from '@/components/notification-item'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useWorkspace } from '@/contexts/workspace-context'
import { useNotificationActions } from '@/hooks/use-notifications'
import { notifications as notificationsApi } from '@/lib/api'
import { notificationTarget } from '@/lib/notification-text'
import { cn } from '@/lib/utils'
import type { AppNotification } from '@/types'

/** `className` is the button's: the phone header and the top bar dress it differently. */
export function NotificationBell({ className, size = 16 }: { className?: string; size?: number }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { current } = useWorkspace()
  const { markRead, markAllRead } = useNotificationActions()
  const [open, setOpen] = useState(false)

  const queryKey = ['notifications', current?.id, 'open']
  const { data, isError } = useQuery({
    queryKey,
    queryFn: () => notificationsApi.list(),
    enabled: !!current,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })
  const items = data?.items ?? []
  const unread = data?.unread_count ?? 0
  const label = unread ? t('notifications.unreadLabel', { number: unread }) : t('notifications.title')

  const follow = (item: AppNotification) => {
    if (!item.read_at) markRead.mutate(item.id)
    setOpen(false)
    navigate(notificationTarget(item))
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button type="button" title={label} aria-label={label} className={cn('relative', className)}>
          <Bell size={size} />
          {unread > 0 && (
            <span
              aria-hidden
              className="absolute -end-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[9px] font-semibold leading-none tabular-nums text-primary-foreground"
            >
              {unread > 9 ? '9+' : unread}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={10}
        collisionPadding={12}
        className="flex max-h-[min(34rem,var(--radix-popover-content-available-height))] w-96 max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden p-0"
      >
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">{t('notifications.title')}</h2>
          {unread > 0 && (
            <button
              type="button"
              className="text-xs text-primary hover:underline disabled:opacity-50"
              disabled={markAllRead.isPending}
              onClick={() => markAllRead.mutate()}
            >
              {t('notifications.markAllRead')}
            </button>
          )}
        </div>
        {items.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-muted-foreground">
            {t(isError ? 'notifications.loadError' : 'notifications.empty')}
          </p>
        ) : (
          <ul className="min-h-0 flex-1 overflow-y-auto py-1">
            {items.map((item) => (
              <li key={item.id}>
                <NotificationItem item={item} onFollow={() => follow(item)} />
              </li>
            ))}
          </ul>
        )}
        <button
          type="button"
          onClick={() => {
            setOpen(false)
            navigate('/notifications')
          }}
          className="shrink-0 border-t border-border px-4 py-3 text-center text-sm font-medium text-primary transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
        >
          {t('notifications.seeAll')}
        </button>
      </PopoverContent>
    </Popover>
  )
}
