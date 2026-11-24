import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TransactionDialog } from '@/components/transaction-dialog'
import { currencies, documentAi, payees, settings } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'
import type { DocumentExtraction } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'en-US', useDisplayLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ privacyMode: false, MASK: '••••' }) }))
vi.mock('@/hooks/use-feature-flags', () => ({ useFeatureFlags: () => ({ documentAiEnabled: true }) }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))
vi.mock('@/contexts/auth-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/auth-context')>()),
  useAuth: () => ({ user: null }),
}))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  currencies: { list: vi.fn() },
  payees: { list: vi.fn() },
  settings: { attachments: vi.fn() },
  documentAi: { status: vi.fn(), upload: vi.fn(), get: vi.fn(), remove: vi.fn() },
}))

const read: DocumentExtraction = {
  id: 'r1', kind: 'receipt', status: 'done', filename: 'r.pdf', content_type: 'application/pdf', size: 1,
  provider: 'ollama', model: 'qwen2.5vl', method: 'text', pages: 1, error_code: null, error: null,
  created_at: '', completed_at: null, confirmed_at: null, transaction_id: null, import_log_id: null,
  statement: null, transactions: [], balance_check: null, suggested_account_id: null, dropped: 0, truncated: false, unread_pages: [], attached: null,
  receipt: {
    merchant: 'Blue Bottle Coffee', merchant_tax_id: null, date: '2026-11-20', total: '12.75', currency: 'EUR',
    kind: 'purchase', payment_method: null, category_hint: null, items: [],
  },
  matches: [], suggested_category_id: null,
}

beforeEach(() => {
  vi.mocked(currencies.list).mockResolvedValue([])
  vi.mocked(payees.list).mockResolvedValue([])
  vi.mocked(settings.attachments).mockResolvedValue({
    allowed_extensions: ['pdf', 'jpg'], max_file_size_mb: 10, max_attachments_per_transaction: 10,
  })
  vi.mocked(documentAi.status).mockResolvedValue({
    enabled: true, configured: true, provider: 'ollama', model: 'qwen2.5vl', connection_id: 'c1', connection_name: 'Local',
    vision: 'yes', vision_source: 'provider', max_file_size_mb: 10, max_pages: 20,
  })
  vi.mocked(documentAi.upload).mockReset().mockResolvedValue(read)
  vi.mocked(documentAi.remove).mockReset().mockResolvedValue(undefined)
})

function renderNew() {
  const result = renderWithProviders(
    <TransactionDialog
      open onClose={vi.fn()} transaction={null} categories={[]} categoryGroups={[]}
      accounts={[{ id: 'acct-1', name: 'Checking', currency: 'EUR' }]} defaultAccountId="acct-1"
      onSave={vi.fn()} loading={false} error={null}
    />,
  )
  return result
}

describe('TransactionDialog reading an attached file', () => {
  it('fills the empty form from a file dropped first, then forgets the read', async () => {
    const { user } = renderNew()
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(documentAi.status).toHaveBeenCalled())
    const input = dialog.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, new File(['%PDF'], 'receipt.pdf', { type: 'application/pdf' }))

    await waitFor(() => expect(dialog.querySelector('input[required]:not([inputmode])')).toHaveValue('Blue Bottle Coffee'))
    expect(dialog.querySelector('input[inputmode="decimal"]')).toHaveValue('12.75')
    expect(documentAi.upload).toHaveBeenCalledWith(expect.any(File), 'receipt')
    await waitFor(() => expect(documentAi.remove).toHaveBeenCalledWith('r1'))
  })

  it('leaves a started form alone until asked', async () => {
    const { user } = renderNew()
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(documentAi.status).toHaveBeenCalled())
    const description = dialog.querySelector('input[required]:not([inputmode])') as HTMLInputElement
    await user.type(description, 'Coffee with Ana')
    const input = dialog.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, new File(['%PDF'], 'receipt.pdf', { type: 'application/pdf' }))
    expect(documentAi.upload).not.toHaveBeenCalled()

    await user.click(await screen.findByRole('button', { name: 'Fill in from this file' }))
    await waitFor(() => expect(dialog.querySelector('input[inputmode="decimal"]')).toHaveValue('12.75'))
    // What the person typed stays.
    expect(description).toHaveValue('Coffee with Ana')
  })
})
