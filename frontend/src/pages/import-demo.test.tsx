/**
 * The public demo's import page, as the demo branch serves it.
 *
 * Uploads are off there (a visitor could crash the worker, and files die at
 * the hourly reset), so the page shows the history of the statements the demo
 * was seeded with and nothing else. The undo stays hidden too: one click would
 * empty the data every screenshot depends on.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { ImportHistory } from '@/components/import-history'
import ImportPage from '@/pages/import'
import { renderWithProviders } from '@/test/utils'
import type { ImportLog } from '@/types'

const api = vi.hoisted(() => ({
  info: vi.fn(),
  list: vi.fn(),
  remove: vi.fn(),
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    info: { ...actual.info, get: api.info },
    importLogs: { list: api.list, delete: api.remove },
  }
})

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true, hasModule: () => true }),
}))

const LOG: ImportLog = {
  id: 'log-1',
  entity: 'transactions',
  filename: 'extrato-conta-pj-2026-11.ofx',
  format: 'ofx',
  transaction_count: 9,
  total_credit: 11300,
  total_debit: 6150,
  account_name: 'Conta PJ',
  account_currency: 'BRL',
  created_at: '2026-11-24T12:00:00Z',
} as ImportLog

describe('import in the public demo', () => {
  beforeEach(() => {
    api.info.mockReset()
    api.list.mockReset()
    api.info.mockResolvedValue({ version: '0.22.0', features: { demo: true } })
    api.list.mockResolvedValue([LOG])
  })

  it('shows the seeded statements without an upload control', async () => {
    renderWithProviders(<ImportPage />, { route: '/import' })

    // The page first renders as the normal one and switches once /api/info
    // says this is the demo; the upload zone going away is that switch.
    await waitFor(() => {
      expect(screen.queryByText('Drag a file or click to select')).not.toBeInTheDocument()
    })

    expect(await screen.findByText('extrato-conta-pj-2026-11.ofx')).toBeInTheDocument()
    expect(screen.getByText('Import History')).toBeInTheDocument()
  })

  it('hides the undo when the history is read-only', async () => {
    const readOnly = renderWithProviders(<ImportHistory entity="transactions" readOnly />)
    expect(await screen.findByText('extrato-conta-pj-2026-11.ofx')).toBeInTheDocument()
    expect(screen.queryByLabelText('Undo import?')).not.toBeInTheDocument()
    readOnly.unmount()

    renderWithProviders(<ImportHistory entity="transactions" />)
    expect(await screen.findByLabelText('Undo import?')).toBeInTheDocument()
  })
})
