import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LinkTickerDialog } from '@/components/link-ticker-dialog'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset } from '@/types'

const marketSearch = vi.fn()
const linkTicker = vi.fn()
const unlinkTicker = vi.fn()

vi.mock('@/lib/api', () => ({
  assets: {
    marketSearch: (q: string, limit: number) => marketSearch(q, limit),
    linkTicker: (id: string, ticker: string) => linkTicker(id, ticker),
    unlinkTicker: (id: string) => unlinkTicker(id),
  },
}))

const toastSuccess = vi.fn()
const toastError = vi.fn()
vi.mock('sonner', () => ({
  toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) },
}))

function syncedHolding(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'asset-1',
    name: 'PSSA3',
    type: 'investment',
    currency: 'BRL',
    ticker: null,
    connection_id: 'conn-1',
    source: 'pluggy',
    valuation_method: 'manual',
    ...overrides,
  } as Asset
}

function renderDialog(asset: Asset | null = syncedHolding(), onClose = vi.fn()) {
  renderWithProviders(<LinkTickerDialog asset={asset} onClose={onClose} />)
  return onClose
}

describe('LinkTickerDialog', () => {
  beforeEach(() => {
    marketSearch.mockReset().mockResolvedValue([])
    linkTicker.mockReset()
    unlinkTicker.mockReset()
    toastSuccess.mockReset()
    toastError.mockReset()
  })

  it('starts from the holding name and searches it', async () => {
    marketSearch.mockResolvedValue([{ symbol: 'PSSA3.SA', name: 'Porto Seguro', exchange: 'SAO', quote_type: 'EQUITY' }])
    renderDialog()

    expect(screen.getByLabelText(t('assets.linkTickerLabel'))).toHaveValue('PSSA3')
    expect(await screen.findByText('PSSA3.SA')).toBeInTheDocument()
    expect(marketSearch).toHaveBeenCalledWith('PSSA3', 8)
  })

  it('starts from the current link when the holding already has one', () => {
    renderDialog(syncedHolding({ ticker: 'PSSA3.SA', valuation_method: 'market_price' }))

    expect(screen.getByLabelText(t('assets.linkTickerLabel'))).toHaveValue('PSSA3.SA')
  })

  it('links the picked symbol and closes', async () => {
    marketSearch.mockResolvedValue([{ symbol: 'PSSA3.SA', name: 'Porto Seguro', exchange: 'SAO', quote_type: 'EQUITY' }])
    linkTicker.mockResolvedValue(syncedHolding({ ticker: 'PSSA3.SA', valuation_method: 'market_price' }))
    const onClose = renderDialog()

    fireEvent.click(await screen.findByText('PSSA3.SA'))
    fireEvent.click(screen.getByRole('button', { name: t('assets.linkTickerAction') }))

    await waitFor(() => expect(linkTicker).toHaveBeenCalledWith('asset-1', 'PSSA3.SA'))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(toastSuccess).toHaveBeenCalledWith(t('assets.linkTickerLinked', { ticker: 'PSSA3.SA' }))
  })

  it('keeps the dialog open and reports a rejected symbol', async () => {
    linkTicker.mockRejectedValue({ isAxiosError: true, response: { data: { detail: 'No quote found for NOPE3.SA' } } })
    const onClose = renderDialog()

    fireEvent.change(screen.getByLabelText(t('assets.linkTickerLabel')), { target: { value: 'NOPE3.SA' } })
    fireEvent.click(screen.getByRole('button', { name: t('assets.linkTickerAction') }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(onClose).not.toHaveBeenCalled()
  })

  it('offers the way back to the bank value only for a holding priced from a ticker', () => {
    renderDialog()

    expect(screen.queryByRole('button', { name: t('assets.linkTickerUnlink') })).not.toBeInTheDocument()
  })

  it('returns a linked holding to the bank value and closes', async () => {
    unlinkTicker.mockResolvedValue(syncedHolding({ ticker: 'PSSA3.SA', valuation_method: 'manual' }))
    const onClose = renderDialog(syncedHolding({ ticker: 'PSSA3.SA', valuation_method: 'market_price' }))

    fireEvent.click(screen.getByRole('button', { name: t('assets.linkTickerUnlink') }))

    await waitFor(() => expect(unlinkTicker).toHaveBeenCalledWith('asset-1'))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(toastSuccess).toHaveBeenCalledWith(t('assets.linkTickerUnlinked'))
    expect(linkTicker).not.toHaveBeenCalled()
  })

  it('cannot submit a blank ticker', () => {
    renderDialog()

    fireEvent.change(screen.getByLabelText(t('assets.linkTickerLabel')), { target: { value: '   ' } })

    expect(screen.getByRole('button', { name: t('assets.linkTickerAction') })).toBeDisabled()
  })
})
