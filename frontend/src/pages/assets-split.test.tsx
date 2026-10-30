/**
 * Stock splits in the holding ledger (issue #944).
 *
 * The transaction dialogs offer a split only on a holding the server will
 * accept one for and ask for the ratio instead of a quantity and a price. The
 * ledger lists a split as a ratio rather than as an amount of money. Saving one
 * from an expanded holding refreshes that holding's own value chart. The
 * Transactions tab is reached through the whole page, since it isn't exported.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import AssetsPage, { AddHoldingTransactionDialog, HoldingLedger } from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset, AssetTransaction } from '@/types'

const api = vi.hoisted(() => ({
  assets: {
    list: vi.fn(),
    portfolioTrend: vi.fn(),
    allTransactions: vi.fn(),
    transactions: vi.fn(),
    addTransaction: vi.fn(),
    updateTransaction: vi.fn(),
    deleteTransaction: vi.fn(),
    values: vi.fn(),
    valueTrend: vi.fn(),
  },
  assetGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  currencies: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  assets: api.assets,
  assetGroups: api.assetGroups,
  accounts: api.accounts,
  currencies: api.currencies,
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: null }),
}))

function makeHolding(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'asset-1',
    user_id: 'user-1',
    name: 'Apple Inc.',
    type: 'stock',
    currency: 'USD',
    units: 1.628024,
    valuation_method: 'market_price',
    purchase_date: '2025-01-15',
    purchase_price: 300,
    sell_date: null,
    sell_price: null,
    growth_type: null,
    growth_rate: null,
    growth_frequency: null,
    growth_start_date: null,
    is_archived: false,
    position: 0,
    current_value: 489,
    current_value_primary: 489,
    gain_loss: 0,
    gain_loss_primary: 0,
    value_count: 1,
    source: 'manual',
    connection_id: null,
    isin: null,
    maturity_date: null,
    group_id: null,
    ticker: 'AAPL',
    ticker_exchange: null,
    last_price: 300,
    last_price_at: null,
    logo_url: null,
    average_price: 300,
    total_invested: 489,
    realized_gain: 0,
    transaction_count: 2,
    ...overrides,
  }
}

function makeTransaction(overrides: Partial<AssetTransaction> = {}): AssetTransaction {
  return {
    id: 'tx-1',
    asset_id: 'asset-1',
    kind: 'buy',
    quantity: 10,
    price: 20,
    fee: 0,
    date: '2026-01-15',
    source: 'manual',
    notes: null,
    old_units: null,
    new_units: null,
    asset_name: 'Apple Inc.',
    ticker: 'AAPL',
    currency: 'USD',
    logo_url: null,
    ...overrides,
  }
}

function renderDialog(holding: Asset) {
  return renderWithProviders(
    <AddHoldingTransactionDialog
      assetId={holding.id}
      holding={holding}
      locale="en-US"
      onClose={vi.fn()}
      onChanged={vi.fn()}
    />,
  )
}

const splitButton = () => screen.getByRole('button', { name: t('assets.txSplit') })

beforeEach(() => {
  vi.clearAllMocks()
  // Only Date is faked, so the dialog opens on a known day.
  vi.setSystemTime(new Date('2026-10-27T12:00:00'))
  api.assets.addTransaction.mockResolvedValue(makeHolding())
})

afterEach(() => vi.useRealTimers())

describe('AddHoldingTransactionDialog', () => {
  it('records a split from its ratio, in place of a quantity and a price', async () => {
    const { user } = renderDialog(makeHolding())

    expect(screen.getByText(t('assets.quantity'))).toBeInTheDocument()
    await user.click(splitButton())

    expect(screen.queryByText(t('assets.quantity'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('assets.unitPrice'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('assets.fee'))).not.toBeInTheDocument()

    await user.type(screen.getByLabelText(t('assets.splitOldUnits')), '1')
    await user.type(screen.getByLabelText(t('assets.splitNewUnits')), '100')
    await user.click(screen.getByRole('button', { name: t('common.save') }))
    await waitFor(() =>
      expect(api.assets.addTransaction).toHaveBeenCalledExactlyOnceWith('asset-1', {
        kind: 'split',
        old_units: 1,
        new_units: 100,
        date: '2026-10-27',
      }),
    )
  })

  it('keeps recording a buy as before', async () => {
    const { user } = renderDialog(makeHolding())

    const [quantity, price] = screen.getAllByRole('spinbutton')
    await user.type(quantity, '2')
    await user.type(price, '10')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() =>
      expect(api.assets.addTransaction).toHaveBeenCalledExactlyOnceWith('asset-1', {
        kind: 'buy',
        quantity: 2,
        price: 10,
        fee: 0,
        date: '2026-10-27',
      }),
    )
  })

  it('does not save a split until both sides are filled in', async () => {
    const { user } = renderDialog(makeHolding())
    await user.click(splitButton())

    const save = screen.getByRole('button', { name: t('common.save') })
    expect(save).toBeDisabled()

    await user.type(screen.getByLabelText(t('assets.splitOldUnits')), '1')
    expect(save).toBeDisabled()
    expect(screen.queryByText(t('assets.splitInvalid'))).not.toBeInTheDocument()
  })

  it.each([
    ['the same number on both sides', '5', '5'],
    ['a zero', '0', '10'],
    ['a decimal', '1.5', '3'],
  ])('refuses a split with %s', async (_label, oldUnits, newUnits) => {
    const { user } = renderDialog(makeHolding())
    await user.click(splitButton())

    await user.type(screen.getByLabelText(t('assets.splitOldUnits')), oldUnits)
    await user.type(screen.getByLabelText(t('assets.splitNewUnits')), newUnits)

    expect(screen.getByText(t('assets.splitInvalid'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()
    expect(api.assets.addTransaction).not.toHaveBeenCalled()
  })

  it.each([
    ['synced from a bank connection', { connection_id: 'conn-1' }],
    ['with no transactions yet', { transaction_count: 0 }],
    ['that is not priced from the market', { valuation_method: 'manual' }],
  ])('offers no split on a holding %s', (_label, overrides) => {
    renderDialog(makeHolding(overrides))

    expect(screen.getByRole('button', { name: t('assets.txBuy') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('assets.txSell') })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.txSplit') })).not.toBeInTheDocument()
  })
})

describe('HoldingLedger', () => {
  it('lists a split as its ratio, with no amount', async () => {
    api.assets.transactions.mockResolvedValue([
      makeTransaction({
        id: 'tx-split',
        kind: 'split',
        quantity: 0,
        price: 0,
        old_units: 1,
        new_units: 100,
        date: '2026-10-27',
      }),
      makeTransaction(),
    ])
    renderWithProviders(
      <HoldingLedger
        asset={makeHolding()}
        locale="en-US"
        dateLocale="en-US"
        mask={(value) => value}
        canWrite
        onAdd={vi.fn()}
        onChanged={vi.fn()}
      />,
    )

    const splitRow = (await screen.findByText(t('assets.txSplit'))).parentElement!
    expect(splitRow).toHaveTextContent('1 → 100')
    expect(splitRow).not.toHaveTextContent('$')

    // A trade in the same list still shows its size and its amount.
    const buyRow = screen.getByText(t('assets.txBuy')).parentElement!
    expect(buyRow).toHaveTextContent('10 × $20.00')
    expect(buyRow).toHaveTextContent('$200.00')
  })
})

describe('Transactions tab', () => {
  const apple = makeHolding()
  // No buys on record, so there are no units for the server to split.
  const microsoft = makeHolding({
    id: 'asset-2',
    name: 'Microsoft',
    ticker: 'MSFT',
    transaction_count: 0,
  })
  const splitEntry = makeTransaction({
    id: 'tx-split',
    kind: 'split',
    quantity: 0,
    price: 0,
    old_units: 1,
    new_units: 100,
    date: '2026-10-20',
  })

  async function openTransactionsTab(transactions: AssetTransaction[] = []) {
    api.assets.list.mockResolvedValue([apple, microsoft])
    api.assets.allTransactions.mockResolvedValue(transactions)
    const view = renderWithProviders(<AssetsPage />, { route: '/assets' })
    // Holdings are in once their rows show; the dialog picks its holding from them.
    await screen.findByText('Apple Inc.')
    await view.user.click(screen.getByRole('button', { name: t('assets.tabTransactions') }))
    return view
  }

  beforeEach(() => {
    api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
    api.assets.updateTransaction.mockResolvedValue(apple)
    api.assetGroups.list.mockResolvedValue([])
    api.accounts.list.mockResolvedValue([])
    api.currencies.list.mockResolvedValue([])
  })

  it('records a split on the holding picked in the dialog', async () => {
    const { user } = await openTransactionsTab([makeTransaction()])

    await user.click(screen.getByRole('button', { name: t('assets.addTransaction') }))
    await user.click(splitButton())
    await user.type(screen.getByLabelText(t('assets.splitOldUnits')), '1')
    await user.type(screen.getByLabelText(t('assets.splitNewUnits')), '100')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() =>
      expect(api.assets.addTransaction).toHaveBeenCalledExactlyOnceWith('asset-1', {
        kind: 'split',
        old_units: 1,
        new_units: 100,
        date: '2026-10-27',
      }),
    )
  })

  it('goes back to a buy when the holding picked cannot take a split', async () => {
    const { user } = await openTransactionsTab([makeTransaction()])

    await user.click(screen.getByRole('button', { name: t('assets.addTransaction') }))
    await user.click(splitButton())
    expect(screen.queryByText(t('assets.quantity'))).not.toBeInTheDocument()

    await user.selectOptions(screen.getByRole('combobox'), 'asset-2')

    expect(screen.queryByRole('button', { name: t('assets.txSplit') })).not.toBeInTheDocument()
    expect(screen.getByText(t('assets.quantity'))).toBeInTheDocument()
  })

  it('edits a split without letting its type change', async () => {
    const { user } = await openTransactionsTab([splitEntry])

    const row = (await screen.findByText(t('assets.txSplit'))).parentElement!
    expect(row).toHaveTextContent('1 → 100')
    expect(row).not.toHaveTextContent('$')
    await user.click(screen.getByTitle(t('common.edit')))

    expect(screen.getByRole('heading', { name: t('assets.editTransaction') })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.txBuy') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.txSell') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.txSplit') })).not.toBeInTheDocument()
    expect(screen.getByLabelText(t('assets.splitOldUnits'))).toHaveValue(1)
    expect(screen.getByLabelText(t('assets.splitNewUnits'))).toHaveValue(100)

    const newUnits = screen.getByLabelText(t('assets.splitNewUnits'))
    await user.clear(newUnits)
    await user.type(newUnits, '50')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() =>
      expect(api.assets.updateTransaction).toHaveBeenCalledExactlyOnceWith('tx-split', {
        old_units: 1,
        new_units: 50,
        date: '2026-10-20',
      }),
    )
  })

  it('offers no split while editing a buy', async () => {
    const { user } = await openTransactionsTab([makeTransaction()])

    await user.click(await screen.findByTitle(t('common.edit')))

    expect(screen.getByRole('button', { name: t('assets.txBuy') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('assets.txSell') })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.txSplit') })).not.toBeInTheDocument()
  })
})

describe('Expanded holding', () => {
  beforeEach(() => {
    api.assets.list.mockResolvedValue([makeHolding()])
    api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
    api.assets.transactions.mockResolvedValue([makeTransaction()])
    api.assets.values.mockResolvedValue([])
    api.assets.valueTrend.mockResolvedValue([{ date: '2026-10-01', amount: 489 }])
    api.assetGroups.list.mockResolvedValue([])
    api.accounts.list.mockResolvedValue([])
    api.currencies.list.mockResolvedValue([])
  })

  it('fetches its own value chart again once a split is saved from its ledger', async () => {
    const { user } = renderWithProviders(<AssetsPage />, { route: '/assets' })

    await user.click(await screen.findByText('Apple Inc.'))
    await waitFor(() => expect(api.assets.valueTrend).toHaveBeenCalledTimes(1))

    await user.click(await screen.findByRole('button', { name: t('assets.addTransaction') }))
    await user.click(splitButton())
    await user.type(screen.getByLabelText(t('assets.splitOldUnits')), '1')
    await user.type(screen.getByLabelText(t('assets.splitNewUnits')), '100')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    // The chart is where the user looks to check that the split took.
    await waitFor(() => expect(api.assets.valueTrend).toHaveBeenCalledTimes(2))
  })
})
