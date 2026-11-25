import { useInfiniteQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { History, PenLine, Trash2 } from 'lucide-react'
import { accessLog as accessLogApi } from '@/lib/api'
import { useDateLocale } from '@/hooks/use-display-locale'
import type { AdminAccessLogEntry } from '@/types'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

const PAGE_SIZE = 50

const ACTION_LABELS: Record<string, string> = {
  'user.update': 'accessLog.actionUpdate',
  'user.delete': 'accessLog.actionDelete',
}

/**
 * What the action changed, said in the affected person's words (#556).
 *
 * The address is the one that matters to them — it is a recovery credential —
 * and whether the change is already in effect or still waiting for them. The
 * other fields are named as the record has them: this is an audit trail, and
 * the record is what it says.
 */
function ChangeLines({ entry }: { entry: AdminAccessLogEntry }) {
  const { t } = useTranslation()
  const changes = entry.changes ?? {}
  const lines: string[] = []

  const email = changes.email
  if (email && (email.from || email.to)) {
    const moved = t('accessLog.emailChange', {
      from: String(email.from ?? ''),
      to: String(email.to ?? ''),
    })
    lines.push(email.pending ? `${moved} · ${t('accessLog.emailPending')}` : moved)
  }

  const others = Object.keys(changes).filter((field) => field !== 'email')
  if (others.length > 0) {
    lines.push(t('accessLog.otherChanges', { fields: others.join(', ') }))
  }

  if (lines.length === 0) return null
  return (
    <ul className="space-y-0.5 text-xs break-words">
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  )
}

function ActionIcon({ action }: { action: string }) {
  if (action === 'user.delete') return <Trash2 size={14} />
  if (action === 'user.update') return <PenLine size={14} />
  return <History size={14} />
}

interface AccessLogDialogProps {
  open: boolean
  onClose: () => void
}

/**
 * What administrators did to this account, and to members of workspaces this
 * person owns (ADR 0042). Every administrative change carries a reason, so the
 * affected side can read why their account or workspace was touched.
 */
export function AccessLogDialog({ open, onClose }: AccessLogDialogProps) {
  const { t } = useTranslation()
  const dateLocale = useDateLocale()

  // Pages of a fixed size, appended: the endpoint refuses more than 100
  // entries per request, so asking for everything at once stops working as
  // soon as there is enough to read.
  const { data, isLoading, isError, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: ['access-log', 'mine'],
      queryFn: ({ pageParam }) => accessLogApi.mine({ page: pageParam, limit: PAGE_SIZE }),
      initialPageParam: 1,
      getNextPageParam: (lastPage, allPages) => {
        const loaded = allPages.reduce((count, page) => count + page.items.length, 0)
        return loaded < lastPage.total ? allPages.length + 1 : undefined
      },
      enabled: open,
    })

  const entries = data?.pages.flatMap((page) => page.items) ?? []

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('accessLog.title')}</DialogTitle>
          <DialogDescription>{t('accessLog.description')}</DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">{t('common.loading')}</p>
        ) : isError ? (
          <p className="py-6 text-center text-sm text-destructive">{t('accessLog.loadError')}</p>
        ) : entries.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">{t('accessLog.empty')}</p>
        ) : (
          <ul className="max-h-[60vh] divide-y divide-border overflow-y-auto rounded-xl border border-border">
            {entries.map((entry: AdminAccessLogEntry) => (
              <li key={entry.id} className="flex gap-3 p-3">
                <span className="mt-0.5 shrink-0 text-muted-foreground">
                  <ActionIcon action={entry.action} />
                </span>
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-sm font-medium">
                    {t(ACTION_LABELS[entry.action] ?? 'accessLog.actionOther')}
                  </p>
                  <p className="text-xs text-muted-foreground break-words">
                    {t('accessLog.target', { email: entry.target_email })}
                  </p>
                  <ChangeLines entry={entry} />
                  <p className="rounded-md bg-muted px-2 py-1 text-xs break-words">{entry.reason}</p>
                  <p className="text-[11px] text-muted-foreground">
                    {t('accessLog.by', { email: entry.actor_email })}
                    {' · '}
                    {new Date(entry.created_at).toLocaleString(dateLocale, {
                      dateStyle: 'short',
                      timeStyle: 'short',
                    })}
                    {entry.ip ? ` · ${t('accessLog.ip', { ip: entry.ip })}` : ''}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-center justify-between gap-2">
          {hasNextPage ? (
            <Button
              variant="outline"
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
            >
              {t('accessLog.loadMore')}
            </Button>
          ) : (
            <span />
          )}
          <Button variant="ghost" onClick={onClose}>
            {t('common.close')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
