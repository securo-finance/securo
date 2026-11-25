import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { notifications as notificationsApi } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import type { NotificationTextContext } from '@/lib/notification-text'

/** Reading notifications, refreshing the bell and the page together. */
export function useNotificationActions() {
  const queryClient = useQueryClient()
  const onSettled = () => queryClient.invalidateQueries({ queryKey: ['notifications'] })
  return {
    markRead: useMutation({ mutationFn: notificationsApi.markRead, onSettled }),
    markAllRead: useMutation({ mutationFn: notificationsApi.markAllRead, onSettled }),
  }
}

/** How dates and amounts are written in a notification, privacy mode included. */
export function useNotificationText(): NotificationTextContext {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  return {
    t,
    // A plain day is written as that day, whatever the browser's timezone.
    date: (iso) => new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(dateLocale),
    money: (amount, currency) => mask(formatCurrency(Number(amount), currency, locale)),
  }
}
