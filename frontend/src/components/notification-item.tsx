import { Bell } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useNotificationText } from '@/hooks/use-notifications'
import { describeNotification, NOTIFICATION_ICONS } from '@/lib/notification-text'
import { cn } from '@/lib/utils'
import type { AppNotification } from '@/types'

/** One notification, in the bell's panel and on its page. */
export function NotificationItem({ item, onFollow }: { item: AppNotification; onFollow: () => void }) {
  const { t } = useTranslation()
  const text = useNotificationText()
  const Icon = NOTIFICATION_ICONS[item.kind] ?? Bell
  const { title, detail } = describeNotification(item, text)
  // One whose cause is gone has nothing left to call attention to.
  const unread = !item.read_at && !item.resolved_at
  return (
    <button
      type="button"
      onClick={onFollow}
      className="flex w-full min-w-0 items-start gap-3 px-4 py-3 text-start transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
    >
      <span
        aria-hidden
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
          unread ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
        )}
      >
        <Icon size={16} />
      </span>
      <span className="min-w-0 flex-1">
        <span className={cn('block break-words text-sm', unread && 'font-semibold')}>
          {title}
        </span>
        <span className="block break-words text-xs text-muted-foreground">{detail}</span>
      </span>
      {unread && (
        <span className="h-2 w-2 shrink-0 self-center rounded-full bg-primary">
          <span className="sr-only">{t('notifications.unread')}</span>
        </span>
      )}
    </button>
  )
}
