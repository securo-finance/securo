import type { TFunction } from 'i18next'
import { ArchiveX, CalendarX2, Flag, HandCoins, Link2, PiggyBank, ReceiptText, Unplug, Users, type LucideIcon } from 'lucide-react'
import { forwardArrow } from '@/lib/format'
import type { AppNotification, AppNotificationKind } from '@/types'

export const NOTIFICATION_ICONS: Record<AppNotificationKind, LucideIcon> = {
  connection_error: Unplug,
  recurring_failed: CalendarX2,
  budget_threshold: PiggyBank,
  flag_overdue: Flag,
  reconciliation_pending: Link2,
  invoice_overdue: ReceiptText,
  group_expense: Users,
  group_settlement: HandCoins,
  backup_failed: ArchiveX,
}

/** Whether this bundle has the words and the icon of a kind. */
export function isKnownKind(kind: string): kind is AppNotificationKind {
  return Object.hasOwn(NOTIFICATION_ICONS, kind)
}

export interface NotificationTextContext {
  t: TFunction
  date: (iso: string) => string
  money: (amount: string, currency: string) => string
}

/** The sentence is written here, from ids and numbers, never by the server. */
export function describeNotification(
  item: AppNotification,
  { t, date, money }: NotificationTextContext,
): { title: string; detail: string } {
  switch (item.kind) {
    case 'connection_error':
      return {
        title: t('notifications.connection.title', { name: item.data.name }),
        detail:
          item.data.code === 'rate_limited'
            ? t('notifications.connection.rateLimited')
            : t(`accounts.syncFailure.${item.data.code}`),
      }
    case 'recurring_failed':
      return {
        title: t('notifications.recurring.title', { name: item.data.name }),
        detail: t('notifications.recurring.detail', { date: date(item.data.date) }),
      }
    case 'budget_threshold': {
      const percent = Math.floor((Number(item.data.actual) / Number(item.data.budget)) * 100)
      return {
        title:
          item.data.threshold >= 100
            ? t('notifications.budget.reached', { name: item.data.name })
            : t('notifications.budget.close', { name: item.data.name, percent }),
        detail: t('notifications.budget.detail', {
          actual: money(item.data.actual, item.data.currency),
          budget: money(item.data.budget, item.data.currency),
        }),
      }
    }
    case 'flag_overdue':
      return {
        title: t('notifications.flag.title', { number: item.data.count }),
        detail: t('notifications.flag.detail'),
      }
    case 'reconciliation_pending':
      return {
        title: t('notifications.match.title', { number: item.data.count }),
        detail: t('notifications.match.detail'),
      }
    case 'invoice_overdue':
      return {
        title: t('notifications.invoice.title', { name: item.data.name }),
        detail: t('notifications.invoice.detail', {
          amount: money(item.data.amount, item.data.currency),
          date: date(item.data.date),
        }),
      }
    case 'group_expense': {
      const { data } = item
      if ('count' in data) {
        return {
          title: t('notifications.group.batch', { actor: data.actor, number: data.count }),
          detail: data.group,
        }
      }
      return {
        title: t(data.changed ? 'notifications.group.changed' : 'notifications.group.added', {
          actor: data.actor,
          name: data.name,
        }),
        detail: t('notifications.group.share', { group: data.group, amount: money(data.share, data.currency) }),
      }
    }
    case 'group_settlement': {
      const { actor, payer, receiver, amount, currency, group } = item.data
      return {
        title: t('notifications.group.paid', { actor }),
        detail: `${payer} ${forwardArrow()} ${receiver}: ${money(amount, currency)} · ${group}`,
      }
    }
    case 'backup_failed':
      return {
        title: t('notifications.backup.title'),
        detail: t('notifications.backup.detail', { date: date(item.data.failed_at) }),
      }
    // A kind newer than this bundle: said without its words, so the rest still shows.
    default:
      return { title: t('notifications.title'), detail: '' }
  }
}

/** Where it gets fixed. */
export function notificationTarget(item: AppNotification): string {
  switch (item.kind) {
    case 'connection_error':
      return item.data.plugin_id ? `/plugins/${item.data.plugin_id}` : '/accounts'
    case 'recurring_failed':
      return '/recurring'
    case 'budget_threshold':
      return '/budgets'
    case 'flag_overdue':
      return '/transactions?flagged=overdue'
    case 'reconciliation_pending':
      return '/rules?tab=queue'
    case 'invoice_overdue':
      return `/invoices/${item.data.invoice_id}`
    case 'group_expense':
    case 'group_settlement':
      return `/groups/${item.data.group_id}`
    case 'backup_failed':
      return '/backups'
    // A kind newer than this bundle.
    default:
      return '/notifications'
  }
}
