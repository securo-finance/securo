/** What matching did, in one order.
 *
 *  Sits under the queue because the two answer neighbouring questions:
 *  the queue is *what still needs me*, this is *what already happened*.
 *  Newest first here and oldest first there, deliberately — a queue is
 *  work to get through, so its oldest item is the most urgent, while a
 *  history is read to find out what just happened.
 *
 *  One line per event and nothing else. The thing a reader wants from a
 *  history like this is almost always the same: *was that me, or was that
 *  the rules?* — so that distinction is what the row leads with, and the
 *  rest is detail behind it.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { reconciliation as reconciliationApi } from '@/lib/api'
import type { ReconciliationHistoryEvent } from '@/types'
import { Zap, Link2Off, HelpCircle, Check, X, Clock } from 'lucide-react'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { cn } from '@/lib/utils'

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}

/** The six verbs, each with the shape a reader can scan for. */
const LOOK: Record<
  ReconciliationHistoryEvent['action'],
  { icon: typeof Zap; tone: string }
> = {
  linked: { icon: Zap, tone: 'text-emerald-600' },
  accepted: { icon: Check, tone: 'text-emerald-600' },
  suggested: { icon: HelpCircle, tone: 'text-amber-600' },
  declined: { icon: X, tone: 'text-muted-foreground' },
  expired: { icon: Clock, tone: 'text-muted-foreground' },
  unlinked: { icon: Link2Off, tone: 'text-rose-500' },
}

const SHOWN_AT_FIRST = 8

export function ReconciliationHistory() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const [expanded, setExpanded] = useState(false)

  const { data: events } = useQuery<ReconciliationHistoryEvent[]>({
    queryKey: ['reconciliation-history'],
    queryFn: () => reconciliationApi.history(),
  })

  if (!events) return null

  const shown = expanded ? events : events.slice(0, SHOWN_AT_FIRST)
  const money = (value: string) =>
    mask(formatCurrency(Number(value ?? 0), 'USD', locale))
  const when = (iso: string) =>
    new Date(iso).toLocaleString(dateLocale, {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })

  return (
    <SectionCard>
      <div className="px-4 sm:px-5 py-4 border-b border-border">
        <p className="text-sm font-semibold text-foreground">
          {t('reconciliation.history.title')}
        </p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {t('reconciliation.history.hint')}
        </p>
      </div>

      {events.length === 0 ? (
        // An empty state rather than nothing: as a card in a stack,
        // rendering nothing was right. As a tab somebody clicked, a blank
        // page reads as broken.
        <p className="text-sm text-muted-foreground text-center py-10">
          {t('reconciliation.history.empty')}
        </p>
      ) : (
      <div className="divide-y divide-border">
        {shown.map((event) => {
          const look = LOOK[event.action]
          const Icon = look.icon
          return (
            <div
              key={event.id}
              className="px-4 sm:px-5 py-2.5 flex items-start gap-3 text-sm"
            >
              <Icon size={14} className={cn('mt-0.5 shrink-0', look.tone)} />
              <div className="flex-1 min-w-0">
                <p className="text-foreground">
                  {t(`reconciliation.history.action.${event.action}`, {
                    name: event.expectation_label ?? '—',
                  })}
                  <span className="text-muted-foreground">
                    {' · '}
                    {money(event.amount)}
                  </span>
                </p>
                <p className="text-xs text-muted-foreground truncate">
                  {/* Whether a person or the rules did it is the first
                      thing anybody wants from a history like this, so it
                      leads the detail line. */}
                  {event.user_id
                    ? t('reconciliation.history.byPerson')
                    : t('reconciliation.history.byRules')}
                  {event.transaction_description
                    ? ` · ${event.transaction_description}`
                    : ''}
                </p>
              </div>
              <span className="text-xs text-muted-foreground shrink-0 tabular-nums">
                {when(event.at)}
              </span>
            </div>
          )
        })}
      </div>
      )}

      {events.length > SHOWN_AT_FIRST && (
        <button
          className="w-full px-4 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors border-t border-border"
          onClick={() => setExpanded((open) => !open)}
        >
          {expanded
            ? t('reconciliation.history.less')
            : t('reconciliation.history.more', {
                count: events.length - SHOWN_AT_FIRST,
              })}
        </button>
      )}
    </SectionCard>
  )
}
