import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'

import { TransactionAttachments } from '@/components/transaction-attachments'
import { transactions, settings } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Attachment } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'en-US' }))

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
