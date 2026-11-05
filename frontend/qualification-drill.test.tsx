import { useState } from 'react'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TransactionDrillDown } from '@/components/transaction-drill-down'
import { renderWithProviders } from '@/test/utils'
import { transactions } from '@/lib/api'
import { toast } from 'sonner'

const permission = vi.hoisted(() => ({ canWrite: true }))
vi.mock('@/contexts/workspace-context', async (load) => ({
  ...await load<typeof import('@/contexts/workspace-context')>(),
  useWorkspace: () => ({ canWrite: permission.canWrite }),
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/lib/api', () => ({
  transactions: { list: vi.fn(), setReview: vi.fn() },
  dashboard: { projectedTransactions: vi.fn().mockResolvedValue([]) },
  admin: { accountingMode: vi.fn().mockResolvedValue({ mode: 'cash' }) },
}))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
const tx = {
  id: 'tx-1', description: 'Qualification coffee', date: '2026-03-02',
  type: 'debit', amount: 4.5, amount_primary: 4.5, currency: 'USD',
  category: null, status: 'posted', attachment_count: 0, review_status: 'needs_review',
  review_attribution: null, flagged: false, splits: [],
}
async function mount() {
  renderWithProviders(<><input aria-label="Outside editor" /><TransactionDrillDown
    filter={{ title: 'Qualification', uncategorized: true }} onClose={vi.fn()} /></>)
  const description = await screen.findByText(tx.description)
  // Independent of the hint: a correct permission fix may remove the title.
  const row = description.parentElement!.parentElement!.parentElement!
  return row
}
async function shortcut(target: EventTarget = document, modifiers = {}) {
  await act(async () => { fireEvent.keyDown(target as Element, { key: 'R', shiftKey: true, ...modifiers }) })
}
describe('qualification: drill-down real handlers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permission.canWrite = true
    vi.mocked(transactions.list).mockResolvedValue({ items: [tx], total: 1 } as never)
    vi.mocked(transactions.setReview).mockResolvedValue({ ...tx, review_status: 'reviewed' } as never)
  })
  it.each(['close/reopen', 'replace filter'])('retires intent on %s even when the same ID returns without mouseleave', async (transition) => {
    function Lifecycle() {
      const [filter, setFilter] = useState<{ title: string, uncategorized?: boolean, account_id?: string } | null>({ title: 'First', uncategorized: true })
      return <>
        <button onClick={() => setFilter({ title: 'First', account_id: 'replacement-account' })}>Replace filter</button>
        <button onClick={() => setFilter({ title: 'First', uncategorized: true })}>Reopen</button>
        <TransactionDrillDown filter={filter} onClose={() => setFilter(null)} />
      </>
    }
    renderWithProviders(<Lifecycle />)
    const row = (await screen.findByText(tx.description)).parentElement!.parentElement!.parentElement!
    fireEvent.mouseEnter(row)
    if (transition === 'close/reopen') {
      fireEvent.click(screen.getByTitle('Close'))
      fireEvent.click(screen.getByRole('button', { name: 'Reopen' }))
    } else {
      fireEvent.click(screen.getByRole('button', { name: 'Replace filter' }))
    }
    await screen.findByText(tx.description)
    await shortcut()
    expect(transactions.setReview).not.toHaveBeenCalled()
    fireEvent.mouseEnter(screen.getByText(tx.description).parentElement!.parentElement!.parentElement!)
    await shortcut()
    expect(transactions.setReview).toHaveBeenCalledExactlyOnceWith('tx-1', 'reviewed')
  })
  it('control: an intentional currently hovered row can be reviewed', async () => {
    fireEvent.mouseEnter(await mount())
    await shortcut()
    expect(transactions.setReview).toHaveBeenCalledExactlyOnceWith('tx-1', 'reviewed')
  })
  it('hover then leave must retire the target', async () => {
    const row = await mount()
    fireEvent.mouseEnter(row)
    fireEvent.mouseLeave(row)
    await shortcut()
    expect(transactions.setReview).not.toHaveBeenCalled()
  })
  it('typing in an outside editable must not review a hovered row', async () => {
    fireEvent.mouseEnter(await mount())
    const input = screen.getByRole('textbox', { name: 'Outside editor' })
    input.focus()
    await shortcut(input)
    expect(transactions.setReview).not.toHaveBeenCalled()
  })
  it.each(['ctrlKey', 'altKey', 'metaKey'])('must ignore extra modifier %s', async (modifier) => {
    fireEvent.mouseEnter(await mount())
    await shortcut(document, { [modifier]: true })
    expect(transactions.setReview).not.toHaveBeenCalled()
  })
  it('viewer must not invoke a write or see its shortcut hint', async () => {
    permission.canWrite = false
    const row = await mount()
    fireEvent.mouseEnter(row)
    await shortcut()
    expect(transactions.setReview).not.toHaveBeenCalled()
    expect(row).not.toHaveAttribute('title', 'Toggle reviewed (Shift+R)')
  })
  it('pending review must suppress duplicate shortcuts', async () => {
    const finishes: Array<(value: never) => void> = []
    vi.mocked(transactions.setReview).mockImplementation(() => new Promise(resolve => { finishes.push(resolve) }))
    fireEvent.mouseEnter(await mount())
    await shortcut()
    await waitFor(() => expect(transactions.setReview).toHaveBeenCalledTimes(1))
    await shortcut()
    const calls = vi.mocked(transactions.setReview).mock.calls.length
    await act(async () => { finishes.forEach(finish => finish({ ...tx, review_status: 'reviewed' } as never)) })
    expect(calls).toBe(1)
  })
  it('contenteditable text is not a shortcut target', async () => {
    const row = await mount()
    fireEvent.mouseEnter(row)
    const editor = document.createElement('div')
    editor.setAttribute('contenteditable', 'true')
    screen.getByRole('textbox', { name: 'Outside editor' }).parentElement!.append(editor)
    await shortcut(editor)
    expect(transactions.setReview).not.toHaveBeenCalled()
  })
  it('ignores repeated keys', async () => {
    fireEvent.mouseEnter(await mount())
    await shortcut(document, { repeat: true })
    expect(transactions.setReview).not.toHaveBeenCalled()
  })
  it.each([{ is_shared: true }, { virtual: true }])('does not review non-owned real-row intent %j', async (flags) => {
    vi.mocked(transactions.list).mockResolvedValue({ items: [{ ...tx, ...flags }], total: 1 } as never)
    fireEvent.mouseEnter(await mount())
    await shortcut()
    expect(transactions.setReview).not.toHaveBeenCalled()
  })
  it('409 rejection must give actionable feedback and retain needs-review', async () => {
    vi.mocked(transactions.setReview).mockRejectedValue({ response: { status: 409, data: { detail: 'Synthetic reviewer was deleted' } } })
    fireEvent.mouseEnter(await mount())
    await shortcut()
    await waitFor(() => expect(transactions.setReview).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('img', { name: 'Needs review' })).toBeInTheDocument()
    // Repository peer mutations use toast.error; this is not a network call.
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/deleted|review|save/i)))
  })
})
