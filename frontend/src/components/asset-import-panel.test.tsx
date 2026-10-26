import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { AssetImportPanel } from '@/components/asset-import-panel'
import { renderWithProviders, t } from '@/test/utils'
import type { AssetImportPreview, AssetOrderImport } from '@/types'

const api = vi.hoisted(() => ({
  previewImport: vi.fn(),
  importOrders: vi.fn(),
  importTemplate: vi.fn(),
  assetGroupsList: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  assets: {
    previewImport: api.previewImport,
    importOrders: api.importOrders,
    importTemplate: api.importTemplate,
  },
  assetGroups: { list: api.assetGroupsList },
}))

vi.mock('@/components/import-history', () => ({ ImportHistory: () => null }))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

const CSV_COLUMNS = ['Sym', 'TDay', 'Shrs', 'Px', 'Comm', 'Dir']

const FILE = new File(
  ['Sym,TDay,Shrs,Px,Comm,Dir', 'AAPL,2026-01-15,10,150.00,1.20,Buy'],
  'orders.csv',
  { type: 'text/csv' },
)

function order(row: number, ticker: string, date: string): AssetOrderImport {
  return {
    row,
    ticker,
    date,
    kind: 'buy',
    quantity: 10,
    price: 150,
    fee: 1.2,
    currency: null,
    name: null,
    notes: null,
    external_id: null,
  }
}

/** The first read: the headers are unknown, so the backend asks for a mapping. */
const errorPreview: AssetImportPreview = {
  orders: [],
  errors: [],
  warnings: [],
  csv_columns: CSV_COLUMNS,
  parse_error: 'Missing required column mapping: ticker, date, quantity, price',
  holdings_created: 0,
  holdings_matched: 0,
  skipped: 0,
}

/** The read after mapping: two orders, two rows that will be skipped. */
const successPreview: AssetImportPreview = {
  orders: [order(2, 'AAPL', '2026-01-15'), order(3, 'VOO', '2026-04-01')],
  errors: [
    { row: 10, reason: 'missing_ticker', ticker: null, detail: null },
    { row: 11, reason: 'invalid_date', ticker: 'XPTO', detail: null },
  ],
  warnings: [],
  csv_columns: CSV_COLUMNS,
  parse_error: null,
  holdings_created: 2,
  holdings_matched: 0,
  skipped: 2,
}

async function uploadFile(rendered: ReturnType<typeof renderWithProviders>) {
  const fileInput = rendered.container.querySelector('input[type="file"]')
  expect(fileInput).not.toBeNull()
  await rendered.user.upload(fileInput as HTMLInputElement, FILE)
}

beforeEach(() => {
  vi.clearAllMocks()
  api.assetGroupsList.mockResolvedValue([])
})

describe('AssetImportPanel column mapping', () => {
  it('keeps the column mapping reachable after a successful preview', async () => {
    // Like the real backend: the preview only succeeds once every required
    // column is mapped, so the grid stays open through the whole mapping.
    api.previewImport.mockImplementation(
      (_file: File, opts: { column_mapping?: Record<string, string> }) => {
        const m = opts?.column_mapping ?? {}
        const missing = ['ticker', 'date', 'quantity', 'price'].filter((f) => !m[f])
        if (missing.length) {
          return Promise.resolve({
            ...errorPreview,
            parse_error: `Missing required column mapping: ${missing.join(', ')}`,
          })
        }
        return Promise.resolve(successPreview)
      },
    )
    const rendered = renderWithProviders(<AssetImportPanel />)
    const { user } = rendered
    await uploadFile(rendered)

    // Unknown headers: the grid opens with the required fields flagged.
    await screen.findByText(t('assetImport.mapPrompt'))
    expect(screen.getByRole('combobox', { name: /Ticker/i })).toBeInTheDocument()

    // Map the four required columns, one select at a time.
    await user.selectOptions(screen.getByRole('combobox', { name: /Ticker/i }), 'Sym')
    await user.selectOptions(screen.getByRole('combobox', { name: /Date/i }), 'TDay')
    await user.selectOptions(screen.getByRole('combobox', { name: /Quantity/i }), 'Shrs')
    await user.selectOptions(screen.getByRole('combobox', { name: /Price/i }), 'Px')

    // The preview now succeeds: two orders ready, two rows that will be skipped.
    await screen.findByText(t('assetImport.rowsSkipped', { count: 2 }))
    expect(
      screen.getByText(
        (content) =>
          content.startsWith('Row 10') &&
          content.includes(t('assetImport.reason.missing_ticker')),
      ),
    ).toBeInTheDocument()

    // The grid collapses, but the bar stays with the choices the user made,
    // so a wrong column is visible without re-uploading the file.
    expect(screen.queryByRole('combobox', { name: /Ticker/i })).not.toBeInTheDocument()
    const bar = screen.getByRole('button', { name: /Column mapping/ })
    expect(bar).toHaveTextContent('Ticker: Sym')
    expect(bar).toHaveTextContent('Date: TDay')
    expect(bar).toHaveTextContent('Quantity: Shrs')
    expect(bar).toHaveTextContent('Price: Px')

    // The skipped-rows block offers a shortcut straight to the grid.
    await user.click(screen.getByRole('button', { name: t('assetImport.adjustMapping') }))
    expect(await screen.findByRole('combobox', { name: /Ticker/i })).toBeInTheDocument()

    // The bar itself is a toggle: close it, open it again.
    await user.click(screen.getByRole('button', { name: /Column mapping/ }))
    expect(screen.queryByRole('combobox', { name: /Ticker/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Column mapping/ }))
    expect(await screen.findByRole('combobox', { name: /Ticker/i })).toBeInTheDocument()
  })

  it('re-previews with the updated mapping when a column is re-mapped after success', async () => {
    // A file whose headers auto-detect: the first preview already succeeds.
    api.previewImport.mockResolvedValue(successPreview)
    const rendered = renderWithProviders(<AssetImportPanel />)
    const { user } = rendered
    await uploadFile(rendered)

    await screen.findByText(t('assetImport.confirm', { count: 2 }))

    // No explicit choices yet, so the bar is collapsed with no summary.
    expect(screen.queryByRole('combobox', { name: /Quantity/i })).not.toBeInTheDocument()
    const bar = screen.getByRole('button', { name: /Column mapping/ })
    expect(bar).not.toHaveTextContent('Quantity')

    await user.click(bar)
    const quantity = await screen.findByRole('combobox', { name: /Quantity/i })
    await user.selectOptions(quantity, 'Comm')

    await waitFor(() => {
      const last = api.previewImport.mock.calls.at(-1)
      expect(last?.[1]).toEqual({
        column_mapping: { quantity: 'Comm' },
        group_id: null,
      })
    })
  })

  it('blocks the import until the re-mapped preview comes back', async () => {
    api.previewImport.mockResolvedValueOnce(successPreview)
    const rendered = renderWithProviders(<AssetImportPanel />)
    const { user } = rendered
    await uploadFile(rendered)
    const confirm = await screen.findByRole('button', { name: t('assetImport.confirm', { count: 2 }) })
    expect(confirm).toBeEnabled()

    // The next preview hangs: the orders on screen still come from the old mapping.
    let settle: (value: AssetImportPreview) => void = () => {}
    api.previewImport.mockImplementationOnce(() => new Promise((resolve) => { settle = resolve }))
    await user.click(screen.getByRole('button', { name: /Column mapping/ }))
    await user.selectOptions(await screen.findByRole('combobox', { name: /Quantity/i }), 'Comm')

    await waitFor(() => expect(confirm).toBeDisabled())
    settle(successPreview)
    await waitFor(() => expect(confirm).toBeEnabled())
  })
})
