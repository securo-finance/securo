import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import ProductsPage from '@/pages/products'
import { InvoiceLineEditor } from '@/components/invoice-line-editor'
import { renderWithProviders, t } from '@/test/utils'
import type { InvoiceLineInput, Product } from '@/types'

const api = vi.hoisted(() => ({
  products: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    addPrice: vi.fn(),
    updatePrice: vi.fn(),
    removePrice: vi.fn(),
  },
  invoices: { settings: vi.fn() },
  fiscal: { productFields: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  products: api.products,
  invoices: api.invoices,
  fiscal: api.fiscal,
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

let canWrite = true
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

function product(overrides: Partial<Product> = {}): Product {
  return {
    id: 'hour',
    name: 'Consulting hour',
    description: 'Senior engineer',
    kind: 'service',
    unit: 'h',
    active: true,
    origin: 'local',
    external_source: null,
    external_id: null,
    custom_fields: null,
    fiscal_refs: null,
    prices: [
      {
        id: 'usd', product_id: 'hour', currency: 'USD', unit_price: '200.00', tax_rate: null,
        billing: 'one_time', interval: null, nickname: null, lookup_key: null, active: true,
        external_source: null, external_id: null, created_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'eur', product_id: 'hour', currency: 'EUR', unit_price: '900.00', tax_rate: null,
        billing: 'recurring', interval: 'monthly', nickname: 'Monthly', lookup_key: null, active: true,
        external_source: null, external_id: null, created_at: '2026-01-02T00:00:00Z',
      },
    ],
    created_at: '2026-01-01T00:00:00Z',
    invoice_count: 3,
    ...overrides,
  }
}

describe('ProductsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    canWrite = true
    api.products.list.mockResolvedValue([
      product(),
      product({ id: 'logo', name: 'Logo design', kind: 'product', description: null, unit: null, prices: [], invoice_count: 0 }),
    ])
    api.invoices.settings.mockResolvedValue({ tax_fields: 'hidden' })
    api.fiscal.productFields.mockResolvedValue({ jurisdiction: null, fields: [] })
  })

  it('lists the catalog with every live price and what each product is worth to invoices', async () => {
    renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    const rows = await screen.findAllByTestId('product-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('Consulting hour')).toBeInTheDocument()
    expect(within(rows[0]).getByText(t('invoices.products.kind.service'))).toBeInTheDocument()
    expect(within(rows[0]).getByText('$200.00 · €900.00 / month')).toBeInTheDocument()
    expect(within(rows[0]).getByText('3')).toBeInTheDocument()
    expect(within(rows[1]).getByText(t('invoices.products.noPrice'))).toBeInTheDocument()
    expect(api.products.list).toHaveBeenLastCalledWith({ active: true })
  })

  it('offers archive to a named product and delete only to an unnamed one', async () => {
    renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    const rows = await screen.findAllByTestId('product-row')
    expect(within(rows[0]).getByLabelText(t('invoices.products.action.archive'))).toBeInTheDocument()
    expect(within(rows[0]).queryByLabelText(t('common.delete'))).not.toBeInTheDocument()
    expect(within(rows[1]).getByLabelText(t('common.delete'))).toBeInTheDocument()
  })

  it('switches to the archived list through the server', async () => {
    const { user } = renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    await screen.findAllByTestId('product-row')
    await user.click(screen.getByTestId('product-filter-archived'))
    expect(api.products.list).toHaveBeenLastCalledWith({ active: false })
  })

  it('hides every write from a viewer', async () => {
    canWrite = false
    renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    await screen.findAllByTestId('product-row')
    expect(screen.queryByTestId('product-new-button')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(t('common.edit'))).not.toBeInTheDocument()
  })

  it('creates a product with its prices in one call', async () => {
    api.products.create.mockResolvedValue(product())
    const { user } = renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    await screen.findAllByTestId('product-row')
    await user.click(screen.getByTestId('product-new-button'))
    await screen.findByTestId('product-name-input')
    expect(screen.getByTestId('product-save')).toBeDisabled()
    await user.type(screen.getByTestId('product-name-input'), 'Retainer')
    await user.type(screen.getByTestId('product-unit-input'), 'month')
    await user.type(screen.getByTestId('price-amount-0'), '3000')
    await user.click(screen.getByTestId('product-save'))
    expect(api.products.create).toHaveBeenCalledWith({
      name: 'Retainer',
      kind: 'service',
      unit: 'month',
      description: null,
      fiscal_refs: null,
      prices: [{ currency: 'USD', unit_price: '3000', tax_rate: null, billing: 'one_time', interval: null, nickname: null }],
    })
  })
})

describe('ProductsPage editing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    canWrite = true
    api.products.list.mockResolvedValue([product()])
    api.invoices.settings.mockResolvedValue({ tax_fields: 'hidden' })
    api.products.update.mockResolvedValue(product())
    api.products.updatePrice.mockResolvedValue(product())
    api.fiscal.productFields.mockResolvedValue({ jurisdiction: null, fields: [] })
  })

  it('archives an existing price instead of deleting it, and never deletes from the dialog', async () => {
    const { user } = renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    const [row] = await screen.findAllByTestId('product-row')
    await user.click(within(row).getByLabelText(t('common.edit')))
    await screen.findByTestId('product-name-input')
    await user.click(screen.getByTestId('price-archive-1'))
    expect(screen.getByTestId('price-restore-1')).toBeInTheDocument()
    await user.click(screen.getByTestId('product-save'))
    expect(api.products.removePrice).not.toHaveBeenCalled()
    expect(api.products.updatePrice).toHaveBeenCalledWith('hour', 'eur', expect.objectContaining({ active: false }))
    expect(api.products.updatePrice).toHaveBeenCalledWith('hour', 'usd', expect.objectContaining({ active: true }))
  })

  it('closes on the server state when a request in the sequence fails', async () => {
    // The product update commits, the price update fails: the list must
    // then show what the server holds (the new name), and the dialog
    // must be gone so a retry does not replay the committed part.
    const updated = product({ name: 'Updated consulting hour' })
    api.products.list.mockReset()
    api.products.list.mockResolvedValueOnce([product()]).mockResolvedValue([updated])
    api.products.update.mockResolvedValue(updated)
    api.products.updatePrice.mockRejectedValueOnce({ response: { data: { detail: { code: 'negative_price' } } } })
    const { user } = renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    const [row] = await screen.findAllByTestId('product-row')
    await user.click(within(row).getByLabelText(t('common.edit')))
    const nameInput = await screen.findByTestId('product-name-input')
    await user.clear(nameInput)
    await user.type(nameInput, updated.name)
    await user.click(screen.getByTestId('product-save'))
    await screen.findByText(updated.name)
    expect(screen.queryByTestId('product-name-input')).not.toBeInTheDocument()
  })
})

describe('ProductsPage fiscal references', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    canWrite = true
    api.products.list.mockResolvedValue([])
    api.invoices.settings.mockResolvedValue({ tax_fields: 'hidden' })
    api.products.create.mockResolvedValue(product())
    api.fiscal.productFields.mockResolvedValue({
      jurisdiction: 'BR',
      fields: [
        { key: 'ncm', label_key: 'fiscal.productField.ncm', kinds: ['product'] },
        { key: 'service_code', label_key: 'fiscal.productField.service_code', kinds: ['service'] },
      ],
    })
  })

  it('offers the keys the jurisdiction suggests for the kind, plus any key by hand', async () => {
    const { user } = renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    await screen.findByTestId('products-empty')
    await user.click(screen.getByTestId('product-new-button'))
    // A service: the service code is offered, the goods code is not.
    expect(await screen.findByTestId('product-ref-service_code')).toBeInTheDocument()
    expect(screen.queryByTestId('product-ref-ncm')).not.toBeInTheDocument()
    await user.type(screen.getByTestId('product-name-input'), 'Design')
    await user.type(screen.getByTestId('product-ref-service_code'), ' 1.05 ')
    // A key the pack never mentions, typed in any case.
    await user.type(screen.getByTestId('product-ref-custom-key'), 'HS_code')
    await user.click(screen.getByTestId('product-ref-add'))
    await user.type(screen.getByTestId('product-ref-hs_code'), '8471')
    await user.click(screen.getByTestId('product-save'))
    expect(api.products.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Design', fiscal_refs: { service_code: '1.05', hs_code: '8471' } }),
    )
  })

  it('refuses a key that is not a key', async () => {
    const { user } = renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    await screen.findByTestId('products-empty')
    await user.click(screen.getByTestId('product-new-button'))
    await screen.findByTestId('product-ref-custom-key')
    await user.type(screen.getByTestId('product-ref-custom-key'), 'has space')
    expect(screen.getByTestId('product-ref-add')).toBeDisabled()
  })

  it('shows the section with an add row when the jurisdiction suggests nothing', async () => {
    api.fiscal.productFields.mockResolvedValue({ jurisdiction: null, fields: [] })
    const { user } = renderWithProviders(<ProductsPage />, { route: '/invoices/products' })
    await screen.findByTestId('products-empty')
    await user.click(screen.getByTestId('product-new-button'))
    expect(await screen.findByTestId('product-ref-custom-key')).toBeInTheDocument()
    expect(screen.getByText(t('invoices.products.field.fiscalRefsNone'))).toBeInTheDocument()
  })
})

describe('InvoiceLineEditor with the catalog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.products.list.mockResolvedValue([product()])
  })

  function Harness({ currency, initial }: { currency: string; initial: InvoiceLineInput[] }) {
    const [lines, setLines] = useState<InvoiceLineInput[]>(initial)
    return <InvoiceLineEditor lines={lines} onChange={setLines} currency={currency} showTax={false} required />
  }

  it('fills the line from the product at the price in the invoice currency, and marks it', async () => {
    const { user } = renderWithProviders(<Harness currency="USD" initial={[{ description: '', quantity: '2', unit_price: '0' }]} />)
    await user.click(screen.getByTestId('invoice-line-pick-product'))
    await user.click(await screen.findByTestId('product-option-hour'))
    expect(screen.getByTestId('invoice-line-description-0')).toHaveValue('Consulting hour')
    expect(screen.getByTestId('invoice-line-unit-0')).toHaveValue('h')
    expect(screen.getByTestId('invoice-line-price-0')).toHaveValue('200.00')
    expect(screen.getByTestId('invoice-line-quantity-0')).toHaveValue('2')
    expect(screen.getByTestId('invoice-line-amount-0')).toHaveTextContent('$400.00')
    expect(screen.getByTestId('invoice-line-from-catalog-0')).toBeInTheDocument()
  })

  it('fills only the name when the product has no price in the currency', async () => {
    const { user } = renderWithProviders(<Harness currency="BRL" initial={[{ description: '', quantity: '1', unit_price: '50' }]} />)
    await user.click(screen.getByTestId('invoice-line-pick-product'))
    const option = await screen.findByTestId('product-option-hour')
    expect(option).toHaveTextContent(t('invoices.products.noPriceIn', { currency: 'BRL' }))
    await user.click(option)
    expect(screen.getByTestId('invoice-line-description-0')).toHaveValue('Consulting hour')
    expect(screen.getByTestId('invoice-line-price-0')).toHaveValue('50')
  })
})
