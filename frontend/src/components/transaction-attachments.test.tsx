import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'

import { TransactionAttachments } from '@/components/transaction-attachments'
import { transactions, settings } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Attachment } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'en-US' }))
const toastError = vi.hoisted(() => vi.fn())
vi.mock('sonner', () => ({ toast: { error: toastError, success: vi.fn() } }))

vi.mock('@/lib/api', () => ({
  transactions: {
    attachments: {
      list: vi.fn(),
      upload: vi.fn(),
    },
  },
  settings: { attachments: vi.fn() },
}))

describe('TransactionAttachments on HTTP', () => {
  beforeEach(() => {
    vi.stubGlobal('crypto', { randomUUID: undefined })
    vi.mocked(transactions.attachments.list).mockResolvedValue([])
    vi.mocked(settings.attachments).mockResolvedValue({
      allowed_extensions: ['pdf'],
      max_file_size_mb: 10,
      max_attachments_per_transaction: 10,
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.resetAllMocks()
  })

  it('takes an XML file when the server allows it, and offers it in the picker', async () => {
    vi.mocked(settings.attachments).mockResolvedValue({
      allowed_extensions: ['pdf', 'xml'],
      max_file_size_mb: 10,
      max_attachments_per_transaction: 10,
    })
    vi.mocked(transactions.attachments.upload).mockImplementation(async (transactionId, file) => ({
      id: 'x1', transaction_id: transactionId, filename: file.name, content_type: 'application/octet-stream',
      size: file.size, created_at: '2026-01-01T00:00:00Z',
    }))
    const { container, user } = renderWithProviders(<TransactionAttachments transactionId="transaction-1" />)
    await screen.findByText(t('transactions.attachmentsUpload'))
    const input = container.querySelector('input[type="file"]')!
    await waitFor(() => expect(input.getAttribute('accept')).toBe('.pdf,.xml'))
    const xml = new File(['<nfeProc/>'], 'NFe-3525.xml', { type: 'application/xml' })
    await user.upload(input as HTMLInputElement, xml)
    await waitFor(() => expect(transactions.attachments.upload).toHaveBeenCalledWith('transaction-1', xml))
  })

  it('names the types this server allows when it refuses a file', async () => {
    vi.mocked(settings.attachments).mockResolvedValue({
      allowed_extensions: ['pdf', 'xml'],
      max_file_size_mb: 10,
      max_attachments_per_transaction: 10,
    })
    const { container } = renderWithProviders(<TransactionAttachments transactionId="transaction-1" />)
    await screen.findByText(t('transactions.attachmentsUpload'))
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    await waitFor(() => expect(input.getAttribute('accept')).toBe('.pdf,.xml'))
    // Dropped past the picker's filter, as a drag and drop does.
    fireEvent.change(input, { target: { files: [new File(['x'], 'notes.txt')] } })
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(t('transactions.attachmentTypeNotAllowed', { types: 'pdf, xml' })))
    expect(transactions.attachments.upload).not.toHaveBeenCalled()
  })

  it('uploads multiple receipts and removes only the completed placeholder', async () => {
    const pending: Array<() => void> = []
    vi.mocked(transactions.attachments.upload).mockImplementation((transactionId, file) =>
      new Promise<Attachment>(resolve => {
        pending.push(() => resolve({
          id: file.name,
          transaction_id: transactionId,
          filename: file.name,
          content_type: file.type,
          size: file.size,
          created_at: '2026-01-01T00:00:00Z',
        }))
      }),
    )
    const { container, user } = renderWithProviders(
      <TransactionAttachments transactionId="transaction-1" />,
    )
    await screen.findByText(t('transactions.attachmentsUpload'))
    const first = new File(['first'], 'first.pdf', { type: 'application/pdf' })
    const second = new File(['second'], 'second.pdf', { type: 'application/pdf' })

    await user.upload(container.querySelector('input[type="file"]')!, [first, second])

    await waitFor(() => expect(transactions.attachments.upload).toHaveBeenCalledTimes(2))
    expect(transactions.attachments.upload).toHaveBeenCalledWith('transaction-1', first)
    expect(transactions.attachments.upload).toHaveBeenCalledWith('transaction-1', second)
    expect(screen.getByText('first.pdf')).toBeInTheDocument()
    expect(screen.getByText('second.pdf')).toBeInTheDocument()

    await act(async () => { pending[0]() })
    await waitFor(() => expect(screen.queryByText('first.pdf')).not.toBeInTheDocument())
    expect(screen.getByText('second.pdf')).toBeInTheDocument()
    await act(async () => { pending[1]() })
    await waitFor(() => expect(screen.queryByText('second.pdf')).not.toBeInTheDocument())
  })
})
