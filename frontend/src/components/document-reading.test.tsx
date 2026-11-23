import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithProviders, t } from '@/test/utils'
import type { DocumentExtraction } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/hooks/use-feature-flags', () => ({ useFeatureFlags: () => ({ documentAiEnabled: true }) }))
vi.mock('@/lib/api', () => ({
  documentAi: {
    status: vi.fn(),
    upload: vi.fn(),
    get: vi.fn(),
    retry: vi.fn(),
    attach: vi.fn(),
    createTransaction: vi.fn(),
  },
  accounts: { list: vi.fn().mockResolvedValue([]) },
  categories: { list: vi.fn().mockResolvedValue([]) },
  categoryGroups: { list: vi.fn().mockResolvedValue([]) },
}))

import { documentAi } from '@/lib/api'
import { isDocumentFile, isImageFile } from '@/hooks/use-document-extraction'
import { DocumentStatementSummary } from './document-statement-summary'
import { ReceiptScanDialog } from './receipt-scan-dialog'

const status = vi.mocked(documentAi.status)

function extraction(overrides: Partial<DocumentExtraction> = {}): DocumentExtraction {
  return {
    id: 'e1', kind: 'statement', status: 'done', filename: 'march.pdf', content_type: 'application/pdf', size: 10,
    provider: 'ollama', model: 'qwen2.5vl:7b', method: 'text', pages: 2, error_code: null, error: null,
    created_at: '2026-03-31T00:00:00Z', completed_at: null, confirmed_at: null, transaction_id: null,
    import_log_id: null, transactions: [], dropped: 0, truncated: false, receipt: null, matches: [],
    suggested_category_id: null, suggested_account_id: null,
    statement: {
      kind: 'bank', institution: 'Banco Exemplo', account_name: null, account_last_digits: null, currency: 'USD',
      period_start: '2026-03-01', period_end: '2026-03-31', opening_balance: '100.00', closing_balance: '90.00',
    },
    balance_check: null,
    ...overrides,
  }
}

describe('document reading helpers', () => {
  it('tells documents and images apart', () => {
    expect(isDocumentFile('Extrato.PDF')).toBe(true)
    expect(isDocumentFile('statement.ofx')).toBe(false)
    expect(isImageFile('receipt.jpeg')).toBe(true)
    expect(isImageFile('statement.pdf')).toBe(false)
  })
})

describe('DocumentStatementSummary', () => {
  it('warns when the rows do not add up to the statement', () => {
    renderWithProviders(
      <DocumentStatementSummary
        extraction={extraction({
          dropped: 2,
          balance_check: {
            opening_balance: '100.00', closing_balance: '90.00', computed_closing_balance: '80.00',
            difference: '10.00', ok: false,
          },
        })}
      />,
    )
    expect(screen.getByText(/Banco Exemplo/)).toBeInTheDocument()
    expect(screen.getByText(/but the statement says/)).toBeInTheDocument()
    expect(screen.getByText(t('documentAi.droppedLines', { count: 2 }))).toBeInTheDocument()
  })

  it('confirms when they do', () => {
    renderWithProviders(
      <DocumentStatementSummary
        extraction={extraction({
          balance_check: {
            opening_balance: '100.00', closing_balance: '90.00', computed_closing_balance: '90.00',
            difference: '0.00', ok: true,
          },
        })}
      />,
    )
    expect(screen.getByText(/The rows add up/)).toBeInTheDocument()
  })
})

describe('ReceiptScanDialog', () => {
  beforeEach(() => status.mockReset())

  it('offers a file or a photo', async () => {
    status.mockResolvedValue({
      enabled: true, configured: true, provider: 'ollama', model: 'qwen2.5vl:7b', source: 'instance',
      vision: 'yes', vision_source: 'provider', max_file_size_mb: 10, max_pages: 20,
    })
    renderWithProviders(<ReceiptScanDialog open onOpenChange={() => {}} />)
    expect(screen.getByText(t('documentAi.receipt.chooseFile'))).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: t('documentAi.receipt.takePhoto') })).toBeEnabled())
  })

  it('says ahead of time that a text-only model cannot read photos', async () => {
    status.mockResolvedValue({
      enabled: true, configured: true, provider: 'ollama', model: 'llama3.1:8b', source: 'instance',
      vision: 'no', vision_source: 'catalog', max_file_size_mb: 10, max_pages: 20,
    })
    renderWithProviders(<ReceiptScanDialog open onOpenChange={() => {}} />)
    expect(await screen.findByText(t('documentAi.textOnlyModel', { model: 'llama3.1:8b' }))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('documentAi.receipt.takePhoto') })).toBeDisabled()
  })
})
