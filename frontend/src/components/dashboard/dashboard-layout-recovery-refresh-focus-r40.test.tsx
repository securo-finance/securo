import { useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DashboardLayoutRecoveryDialog } from '@/components/dashboard/dashboard-layout-recovery-dialog'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import type { RecoveryReview } from '@/lib/dashboard-layout-session'
import i18n, { i18nReady } from '@/lib/i18n'

// Presentation-scope TEST_DATA: actual product dialog + Radix + user-event.
// Explicit outcome props do not prove the App/store/issuing-owner integration.
const ticket = Object.freeze({ attemptId: 'r40-test-attempt', actorId: 'r40-test-actor',
  authGeneration: 1, workspaceId: 'r40-test-workspace', epoch: 1, readId: null })
const baseline = Object.freeze({ version: 1 as const, workspaceId: ticket.workspaceId,
  items: Object.freeze([Object.freeze({ id: 'hero-balance' as const, visible: true, order: 0 })]) })
const initialReview: RecoveryReview = Object.freeze({ ticket,
  recovery: Object.freeze({ ...ticket, baseline, submitted: baseline,
    category: 'unknown' as const, phase: 'needs-recheck' as const, recheck: null }),
  permissions: Object.freeze({ recheck: true, retry: false, reapply: false, discard: true }),
})
const discarded = vi.fn()
const refreshed = vi.fn()
const rechecked = vi.fn()
interface PresentationOptions {
  phase?: RecoveryReview['recovery']['phase']
  refresh?: 'enabled' | 'disabled' | 'disconnected'
  qualify?: boolean
  keepDisabledReview?: boolean
  displace?: boolean
  reopen?: boolean
  otherDialog?: boolean
}
const lifecycle: string[] = []
function Presentation({ phase = 'needs-recheck', refresh = 'enabled', qualify = false,
  keepDisabledReview = false, displace = false, reopen = false, otherDialog = false }: PresentationOptions) {
  const [review, setReview] = useState<RecoveryReview | null>(null)
  const [retained, setRetained] = useState(true)
  const [qualified, setQualified] = useState(false)
  const [otherOpen, setOtherOpen] = useState(false)
  const ownerRef = useRef(1)
  const [focusGuard, setFocusGuard] = useState<() => boolean>(() => () => false)
  const returnFocus = useRef<HTMLButtonElement>(null)
  const fallbackFocus = useRef<HTMLButtonElement>(null)
  // Retain an enabled, genuinely disconnected candidate rather than only null.
  const [detachedRefresh] = useState(() => document.createElement('button'))
  const refreshFocus = useRef<HTMLButtonElement>(refresh === 'disconnected' ? detachedRefresh : null)
  const openReview = () => {
    const openingOwner = ownerRef.current
    setFocusGuard(() => () => ownerRef.current === openingOwner)
    setReview({ ...initialReview, recovery: { ...initialReview.recovery, phase } })
  }
  return <>
    <button ref={returnFocus} disabled={!qualified}>Customize</button>
    {(retained || keepDisabledReview) && <button ref={fallbackFocus} disabled={!retained} onClick={openReview}>Review</button>}
    <button ref={refresh === 'disconnected' ? undefined : refreshFocus} disabled={refresh === 'disabled'}
      onClick={() => { refreshed() }}>Refresh</button>
    <DashboardLayoutRecoveryDialog review={review} busy={false}
      returnFocus={returnFocus} fallbackFocus={fallbackFocus} refreshFocus={refreshFocus}
      canReturnFocus={focusGuard} onClose={() => { setReview(null) }}
      onRecheck={async (value) => {
        rechecked(value)
        setReview((previous) => previous && { ...previous, recovery: { ...previous.recovery, phase: 'recheck-failed', recheck: null } })
        throw new Error('TEST_DATA rejected Recheck')
      }} onReplace={async () => { throw new Error('Unexpected replacement') }}
      onDiscard={(value) => {
        discarded(value); setRetained(false); setReview(null); setQualified(qualify)
        if (displace) { ownerRef.current += 1; setFocusGuard(() => () => true) }
        if (otherDialog) setOtherOpen(true)
        if (reopen) setTimeout(() => {
          lifecycle.push(document.querySelector('[role="dialog"]') === null ? 'old removed' : 'old present')
          // Commit the reopened modal before Radix delivers the old deferred close.
          // Merely queueing state here leaves the overlap precondition unenforced.
          flushSync(() => { openReview() })
        }, 0)
      }} />
    <Dialog open={otherOpen}>
      <DialogContent showCloseButton={false}>
        <DialogTitle>Another connected dialog</DialogTitle>
        <DialogDescription>TEST_DATA competing modal focus</DialogDescription>
        <button>Other dialog control</button>
      </DialogContent>
    </Dialog>
  </>
}
const consoleError = vi.spyOn(console, 'error')
const consoleWarn = vi.spyOn(console, 'warn')
beforeEach(async () => {
  await i18nReady
  await i18n.changeLanguage('en')
  vi.clearAllMocks()
  lifecycle.length = 0
})
afterEach(() => {
  cleanup()
  expect(consoleError).not.toHaveBeenCalled()
  expect(consoleWarn).not.toHaveBeenCalled()
})
it('unqualified direct Discard restores focus to connected enabled Refresh without activating it', async () => {
  const user = userEvent.setup()
  render(<Presentation />)
  await user.click(screen.getByRole('button', { name: 'Review' }))
  const dialog = screen.getByRole('dialog', { name: 'Review unsaved dashboard changes' })
  expect(within(dialog).getByRole('heading', { name: 'Review unsaved dashboard changes' })).toHaveFocus()
  const discard = within(dialog).getByRole('button', { name: 'Discard draft' })
  for (let step = 0; step < 6 && document.activeElement !== discard; step += 1) await user.tab()
  expect(discard).toHaveFocus()
  await user.keyboard('{Enter}')
  expect(discarded).toHaveBeenCalledExactlyOnceWith(ticket)
  expect(rechecked).not.toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Customize' })).toBeDisabled()
  const refresh = screen.getByRole('button', { name: 'Refresh' })
  expect(refresh).toBeEnabled()
  await waitFor(() => expect(refresh).toHaveFocus())
  expect(refreshed).not.toHaveBeenCalled()
})

async function openPresentation(options: PresentationOptions = {}) {
  const user = userEvent.setup()
  render(<Presentation {...options} />)
  await user.click(screen.getByRole('button', { name: 'Review' }))
  const dialog = screen.getByRole('dialog', { name: 'Review unsaved dashboard changes' })
  expect(within(dialog).getByRole('heading', { name: 'Review unsaved dashboard changes' })).toHaveFocus()
  return { user, dialog }
}
const settleAutofocus = () => new Promise<void>((resolve) => { setTimeout(resolve, 20) })

it('protected review after a failed explicit Recheck returns direct Discard focus to Refresh', async () => {
  const { user, dialog } = await openPresentation({ phase: 'protected' })
  expect(within(dialog).getByRole('status')).toHaveTextContent(i18n.t('dashboard.customize.persistence.protected'))
  await user.click(within(dialog).getByRole('button', { name: 'Recheck' }))
  expect(rechecked).toHaveBeenCalledExactlyOnceWith(ticket)
  expect(within(dialog).getByRole('status')).toHaveTextContent(i18n.t('dashboard.customize.persistence.recheckFailed'))
  await user.click(within(dialog).getByRole('button', { name: 'Discard draft' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Customize' })).toBeDisabled()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toHaveFocus())
  expect(discarded).toHaveBeenCalledExactlyOnceWith(ticket)
  expect(refreshed).not.toHaveBeenCalled()
})

it.each(['disabled', 'disconnected'] as const)('denies %s Refresh and does not query an unrelated enabled button', async (refresh) => {
  const { user, dialog } = await openPresentation({ refresh })
  await user.click(within(dialog).getByRole('button', { name: 'Discard draft' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await settleAutofocus()
  expect(document.body).toHaveFocus()
  expect(screen.getByRole('button', { name: 'Refresh' })).not.toHaveFocus()
  expect(refreshed).not.toHaveBeenCalled()
})

it('same opening owner qualified Discard prioritizes newly enabled Customize over Refresh', async () => {
  const { user, dialog } = await openPresentation({ qualify: true })
  await user.click(within(dialog).getByRole('button', { name: 'Discard draft' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Customize' })).toHaveFocus())
  expect(refreshed).not.toHaveBeenCalled()
})

it('ordinary Close retains recovery and prioritizes connected Review over Refresh', async () => {
  const { user, dialog } = await openPresentation()
  await user.click(within(dialog).getByRole('button', { name: 'Close' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Review' })).toHaveFocus())
  expect(discarded).not.toHaveBeenCalled()
  expect(refreshed).not.toHaveBeenCalled()
})

it('disabled connected Review falls through to Refresh', async () => {
  const { user, dialog } = await openPresentation({ keepDisabledReview: true })
  await user.click(within(dialog).getByRole('button', { name: 'Discard draft' }))
  expect(screen.getByRole('button', { name: 'Review' })).toBeDisabled()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toHaveFocus())
})

it('displaced opening-owner guard denies every destination despite new live guard and enabled Customize', async () => {
  const { user, dialog } = await openPresentation({ displace: true, qualify: true })
  await user.click(within(dialog).getByRole('button', { name: 'Discard draft' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Customize' })).toBeEnabled()
  await settleAutofocus()
  expect(document.body).toHaveFocus()
  expect(refreshed).not.toHaveBeenCalled()
})

it('deferred old close cannot steal focus from a reopened real Review heading', async () => {
  const { user, dialog } = await openPresentation({ reopen: true })
  const refreshFocused = vi.fn()
  screen.getByRole('button', { name: 'Refresh', hidden: true }).addEventListener('focus', refreshFocused)
  await user.click(within(dialog).getByRole('button', { name: 'Discard draft' }))
  await waitFor(() => expect(lifecycle).toStrictEqual(['old removed']))
  const reopened = await screen.findByRole('dialog', { name: 'Review unsaved dashboard changes' })
  expect(reopened).not.toBe(dialog)
  await settleAutofocus()
  expect(within(reopened).getByRole('heading', { name: 'Review unsaved dashboard changes' })).toHaveFocus()
  expect(refreshFocused).not.toHaveBeenCalled()
})

it('old close preserves active focus in another connected real dialog', async () => {
  const { user, dialog } = await openPresentation({ otherDialog: true })
  const refreshFocused = vi.fn()
  screen.getByRole('button', { name: 'Refresh', hidden: true }).addEventListener('focus', refreshFocused)
  await user.click(within(dialog).getByRole('button', { name: 'Discard draft' }))
  const other = await screen.findByRole('dialog', { name: 'Another connected dialog' })
  await settleAutofocus()
  expect(within(other).getByRole('button', { name: 'Other dialog control' })).toHaveFocus()
  expect(other.isConnected).toBe(true)
  expect(refreshFocused).not.toHaveBeenCalled()
})
