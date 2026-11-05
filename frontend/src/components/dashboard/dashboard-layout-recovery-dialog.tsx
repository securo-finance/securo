import { useRef, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { getWidgetDefinition } from '@/components/dashboard/widgets'
import type { RecoveryActionTicket, RecoveryReview, SnapshotLayout } from '@/lib/dashboard-layout-session'

interface Props {
  review: RecoveryReview | null
  busy: boolean
  onClose(): void
  onRecheck(ticket: RecoveryActionTicket): Promise<void>
  onReplace(ticket: RecoveryActionTicket, action: 'reapply' | 'retry'): Promise<void>
  onDiscard(ticket: RecoveryActionTicket): void
  returnFocus: RefObject<HTMLButtonElement | null>
  fallbackFocus: RefObject<HTMLButtonElement | null>
  refreshFocus: RefObject<HTMLButtonElement | null>
  canReturnFocus(): boolean
}

function Comparison({ label, layout }: { label: string; layout: SnapshotLayout | null }) {
  const { t } = useTranslation()
  const key = 'dashboard.customize.persistence.'
  return (
    <section className="min-w-0 space-y-2">
      <h3 className="font-medium text-sm">{label}</h3>
      <ul aria-label={label} className="space-y-2 text-sm">
        {layout ? layout.items.map((item) => (
          <li key={item.id} className="rounded-md border border-border p-2">
            <p className="font-medium">{t(getWidgetDefinition(item.id).translationKey)}{' '}</p>
            <p className="text-muted-foreground">
              {t(key + (item.visible ? 'visible' : 'hidden'))} · {t(key + 'position', { position: item.order + 1 })}
            </p>
          </li>
        )) : <span className="text-muted-foreground">{t(key + 'unavailable')}</span>}
      </ul>
    </section>
  )
}

/** Presentation only: all actions carry the native issuing binding's ticket. */
export function DashboardLayoutRecoveryDialog({ review, busy, onClose, onRecheck, onReplace, onDiscard, returnFocus, fallbackFocus, refreshFocus, canReturnFocus }: Props) {
  const { t } = useTranslation()
  const heading = useRef<HTMLHeadingElement>(null)
  const openingFocus = useRef<{ container: EventTarget | null; guard: () => boolean } | null>(null)
  const key = 'dashboard.customize.persistence.'
  const recovery = review?.recovery
  const permission = review?.permissions
  return (
    <Dialog open={review !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent showCloseButton={false} className="sm:max-w-3xl max-h-[calc(100dvh-2rem)] overflow-y-auto"
        aria-busy={busy}
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          openingFocus.current = { container: event.target, guard: canReturnFocus }
          heading.current?.focus()
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          // A deferred old close must not steal focus from a reopened review.
          const opening = openingFocus.current
          if (!opening || opening.container !== event.target || heading.current?.isConnected || !opening.guard()) return
          if (document.activeElement?.closest('[role="dialog"]')?.isConnected) return
          const node = returnFocus.current
          if (node?.isConnected && !node.disabled) node.focus()
          else {
            const fallback = fallbackFocus.current
            if (fallback?.isConnected && !fallback.disabled) fallback.focus()
            else {
              const refresh = refreshFocus.current
              if (refresh?.isConnected && !refresh.disabled) refresh.focus()
            }
          }
        }}>
        <DialogHeader>
          <DialogTitle ref={heading} tabIndex={-1}>{t(key + 'reviewTitle')}</DialogTitle>
          <DialogDescription>{t(key + 'reviewDescription')} {t(key + 'retention')}</DialogDescription>
        </DialogHeader>
        {recovery && <>
          <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
            {t(key + (recovery.phase === 'matched' ? 'matched' : recovery.phase === 'protected' ? 'protected' :
              recovery.phase === 'recheck-failed' ? 'recheckFailed' :
              recovery.category === 'correction-required' ? 'correctionRequired' : recovery.category))}
          </p>
          <div className="grid gap-4 sm:grid-cols-3">
            <Comparison label={t(key + 'baselineLayout')} layout={recovery.baseline} />
            <Comparison label={t(key + 'draftLayout')} layout={recovery.submitted} />
            <Comparison label={t(key + 'serverLayout')} layout={recovery.recheck?.target ?? null} />
          </div>
          <p className="text-sm text-muted-foreground">{t(key + 'replaceWarning')}</p>
          <p className="text-sm text-muted-foreground">{t(key + 'discardWarning')}</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={busy || !permission?.recheck}
              onClick={() => { void onRecheck(review.ticket).catch(() => {}) }}>{t(key + 'recheck')}</Button>
            {(recovery.category === 'conflict' || recovery.category === 'unknown') &&
              <Button disabled={busy || !(recovery.category === 'conflict' ? permission?.reapply : permission?.retry)}
                onClick={() => { void onReplace(review.ticket, recovery.category === 'conflict' ? 'reapply' : 'retry').catch(() => {}) }}>
                {t(key + (recovery.category === 'conflict' ? 'reapply' : 'retry'))}
              </Button>}
            <Button variant="outline" disabled={busy || !permission?.discard}
              onClick={() => { onDiscard(review.ticket) }}>{t(key + 'discard')}</Button>
          </div>
        </>}
        <DialogClose asChild><Button variant="ghost">{t('common.close')}</Button></DialogClose>
      </DialogContent>
    </Dialog>
  )
}
