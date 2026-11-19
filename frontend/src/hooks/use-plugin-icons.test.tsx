import { QueryClient } from '@tanstack/react-query'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import { renderWithProviders } from '@/test/utils'
import { usePluginIcons } from './use-plugin-icons'

const api = vi.hoisted(() => ({ catalog: vi.fn() }))
vi.mock('@/lib/api', () => ({ plugins: { catalog: api.catalog } }))

const LEDGER_ICON = `/api/plugins/icons/${'b'.repeat(64)}.png`

function LedgerAvatar() {
  const icons = usePluginIcons(true)
  return <output>{icons.get('acme/ledger') ?? 'badge'}</output>
}

/** The test client turns retries off; the query's own options still win. */
function immediateRetries() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0, gcTime: 0, staleTime: 0 } },
  })
}

beforeEach(() => vi.resetAllMocks())

it('retries the catalog once before leaving the generated badge', async () => {
  api.catalog
    .mockRejectedValueOnce(new Error('index unreachable'))
    .mockResolvedValueOnce([{ id: 'acme/ledger', icon_url: LEDGER_ICON }])

  renderWithProviders(<LedgerAvatar />, { queryClient: immediateRetries() })

  expect(await screen.findByText(LEDGER_ICON)).toBeVisible()
  expect(api.catalog).toHaveBeenCalledTimes(2)
})

it('leaves the generated badge when the retried catalog still fails', async () => {
  api.catalog.mockRejectedValue(new Error('index unreachable'))

  renderWithProviders(<LedgerAvatar />, { queryClient: immediateRetries() })

  expect(await screen.findByText('badge')).toBeVisible()
  // The badge shows while the catalog is in flight, so wait for the retry.
  await waitFor(() => expect(api.catalog).toHaveBeenCalledTimes(2))
  expect(screen.getByText('badge')).toBeVisible()
})
