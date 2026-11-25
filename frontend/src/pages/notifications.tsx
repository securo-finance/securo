import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'
import { Check, CheckCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { NotificationItem } from '@/components/notification-item'
import { NotificationPreferences } from '@/components/notification-preferences'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDateLocale } from '@/hooks/use-display-locale'
import { useNotificationActions } from '@/hooks/use-notifications'
import { notifications as notificationsApi, type NotificationFilter } from '@/lib/api'
import { notificationTarget } from '@/lib/notification-text'
import type { AppNotification } from '@/types'

const PAGE_SIZE = 30
const FILTERS = ['all', 'unread'] as const satisfies readonly NotificationFilter[]
type Filter = (typeof FILTERS)[number]
const PREFERENCES = 'preferences'

export default function NotificationsPage() {
  const { t } = useTranslation()
  const { current } = useWorkspace()
  // In the address, so going back from where a notification led lands on the same tab.
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get('tab')
  const filter = FILTERS.find((value) => value === requested) ?? 'all'
  const tab = requested === PREFERENCES ? PREFERENCES : filter
  const { markAllRead } = useNotificationActions()
  const query = useNotificationPages(filter)
  const unread = query.data?.pages[0]?.unread_count ?? 0

  return (
    <div>
      <PageHeader
        section={current?.name ?? ''}
        title={t('notifications.title')}
        action={
          <Button variant="outline" size="sm" disabled={unread === 0 || markAllRead.isPending} onClick={() => markAllRead.mutate()}>
            <CheckCheck size={15} className="me-1.5" />
            {t('notifications.markAllRead')}
          </Button>
        }
      />
      <Tabs value={tab} onValueChange={(value) => setSearchParams(value === 'all' ? {} : { tab: value }, { replace: true })} className="gap-4">
        {/* Wide labels scroll sideways; the padding keeps the focus ring from being cut. */}
        <div className="-m-1 max-w-full overflow-x-auto overflow-y-hidden p-1">
          <TabsList className="w-max min-w-full sm:min-w-0">
            {FILTERS.map((value) => (
              <TabsTrigger key={value} value={value} className="grow shrink-0 sm:flex-none sm:px-4">
                {t(`notifications.filter.${value}`)}
                {value === 'unread' && unread > 0 && (
                  <span className="ms-1.5 rounded-full bg-primary/15 px-1.5 text-[10px] font-semibold tabular-nums text-primary">
                    {unread}
                  </span>
                )}
              </TabsTrigger>
            ))}
            <TabsTrigger value={PREFERENCES} className="grow shrink-0 sm:flex-none sm:px-4">
              {t('notifications.preferences.title')}
            </TabsTrigger>
          </TabsList>
        </div>
        {/* One panel for the lists: the filter only changes what it shows. */}
        {tab !== PREFERENCES && (
          <TabsContent value={filter}>
            <NotificationPages query={query} filter={filter} />
          </TabsContent>
        )}
        <TabsContent value={PREFERENCES}>
          <NotificationPreferences />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function useNotificationPages(filter: Filter) {
  const { current } = useWorkspace()
  return useInfiniteQuery({
    queryKey: ['notifications', current?.id, 'page', filter],
    queryFn: ({ pageParam }) => notificationsApi.list({ status: filter, limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0)
      return last.items.length > 0 && loaded < last.total ? loaded : undefined
    },
    enabled: !!current,
    placeholderData: keepPreviousData,
  })
}

function NotificationPages({ query, filter }: { query: ReturnType<typeof useNotificationPages>; filter: Filter }) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { markRead } = useNotificationActions()
  const dateLocale = useDateLocale()
  const label = (day: string) => dayLabel(day, dateLocale, i18n.language)

  if (query.isPending) return <Skeleton className="h-64 w-full rounded-xl" />
  if (query.isError && !query.data) {
    return (
      <Panel>
        <p>{t('notifications.loadError')}</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => void query.refetch()}>
          {t('common.retry')}
        </Button>
      </Panel>
    )
  }

  // A row that moved between two pages while they loaded is listed once.
  const items = [...new Map(query.data.pages.flatMap((page) => page.items).map((item) => [item.id, item])).values()]
  if (items.length === 0) return <Panel>{t(`notifications.emptyFilter.${filter}`)}</Panel>

  const follow = (item: AppNotification) => {
    if (!item.read_at) markRead.mutate(item.id)
    navigate(notificationTarget(item))
  }

  return (
    <div className="space-y-6">
      {groupByDay(items).map(([day, rows]) => (
        <section key={day} aria-label={label(day)}>
          <h2 className="mb-2 px-1 text-xs font-medium text-muted-foreground">{label(day)}</h2>
          <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
            {rows.map((item) => (
              <li key={item.id} className="flex items-stretch">
                <div className="min-w-0 flex-1">
                  <NotificationItem item={item} onFollow={() => follow(item)} />
                </div>
                {!item.read_at && !item.resolved_at && (
                  <button
                    type="button"
                    onClick={() => markRead.mutate(item.id)}
                    title={t('notifications.markRead')}
                    aria-label={t('notifications.markRead')}
                    className="flex w-12 shrink-0 items-center justify-center text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                  >
                    <Check size={16} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
      {query.hasNextPage && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
            {t('notifications.loadMore')}
          </Button>
        </div>
      )}
    </div>
  )
}

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-card px-6 py-14 text-center text-sm text-muted-foreground">{children}</div>
  )
}

function localDay(date: Date): string {
  return [date.getFullYear(), date.getMonth() + 1, date.getDate()].map((n) => String(n).padStart(2, '0')).join('-')
}

function groupByDay(items: AppNotification[]): [string, AppNotification[]][] {
  const days = new Map<string, AppNotification[]>()
  for (const item of items) {
    const day = localDay(new Date(item.notified_at))
    days.set(day, [...(days.get(day) ?? []), item])
  }
  return [...days]
}

/** "Today" and "Yesterday" in the app's language, or the date in its own format. */
function dayLabel(day: string, dateLocale: string, language: string): string {
  const date = new Date(`${day}T00:00:00`)
  const daysAgo = Math.round((new Date(`${localDay(new Date())}T00:00:00`).getTime() - date.getTime()) / 86_400_000)
  if (daysAgo !== 0 && daysAgo !== 1) return date.toLocaleDateString(dateLocale, { day: 'numeric', month: 'long', year: 'numeric' })
  const label = new Intl.RelativeTimeFormat(language, { numeric: 'auto' }).format(-daysAgo, 'day')
  return label.charAt(0).toLocaleUpperCase(language) + label.slice(1)
}
