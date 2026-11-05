import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'

import ImportPage from '@/pages/import'
import { ImportCategoryMapping } from '@/components/import-category-mapping'
import { ImportSummaryBar } from '@/components/import-summary-bar'
import { renderWithProviders, t } from '@/test/utils'
import type { Category, CategoryGroup, ImportReviewTransaction, PresetCategorySuggestion } from '@/types'

const api = vi.hoisted(() => ({
  transactions: {
    previewImport: vi.fn(),
    import: vi.fn(),
    list: vi.fn(),
  },
  accounts: { list: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
  importLogs: { list: vi.fn(), delete: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/contexts/auth-context')>()
  return {
    ...actual,
    useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
  }
})

vi.mock('@/contexts/workspace-context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/contexts/workspace-context')>()
  return {
    ...actual,
    useWorkspace: () => ({ canWrite: true }),
  }
})

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

const accounts = [
  { id: 'acc-1', name: 'Everyday Checking', type: 'checking', currency: 'BRL', balance: 100 },
  { id: 'acc-2', name: 'Second destination', type: 'checking', currency: 'BRL', balance: 0 },
  { id: 'acc-3', name: 'Third destination', type: 'checking', currency: 'BRL', balance: 0 },
]

const categories: Category[] = [
  { id: 'cat-food', user_id: 'u1', group_id: 'grp-1', name: 'Food & Dining', icon: '', color: '#000', is_system: true, is_hidden: false, treat_as_transfer: false, is_ignored: false },
  { id: 'cat-salary', user_id: 'u1', group_id: 'grp-2', name: 'Salary', icon: '', color: '#000', is_system: true, is_hidden: false, treat_as_transfer: false, is_ignored: false },
  { id: 'cat-handmade', user_id: 'u1', group_id: null, name: 'Handmade Odds', icon: '', color: '#000', is_system: false, is_hidden: false, treat_as_transfer: false, is_ignored: false },
]

const groups: CategoryGroup[] = [
  { id: 'grp-1', user_id: 'u1', name: 'Food', icon: '', color: '#000', position: 0, is_system: true, is_hidden: false, categories: [] },
  { id: 'grp-2', user_id: 'u1', name: 'Income', icon: '', color: '#000', position: 1, is_system: true, is_hidden: false, categories: [] },
]

const suggestions: PresetCategorySuggestion[] = [
  { value: 'Food & Dining: Coffee Shops', row_count: 2, suggested_category_id: 'cat-food', suggested_category_name: 'Food & Dining' },
  { value: 'Income: Paycheck', row_count: 1, suggested_category_id: 'cat-salary', suggested_category_name: 'Salary' },
  { value: 'Mystery Ledger', row_count: 1, suggested_category_id: null, suggested_category_name: null },
]

function monarchPreview(overrides: Record<string, unknown> = {}) {
  return {
    transactions: [
      { description: 'Fernwood Coffee', amount: 4.99, date: '2026-03-16', type: 'debit', category_name: 'Food & Dining: Coffee Shops' },
      { description: 'Harborline Payroll', amount: 1200, date: '2026-03-05', type: 'credit', category_name: 'Income: Paycheck' },
      { description: 'Odd Vendor', amount: 9, date: '2026-03-17', type: 'debit', category_name: 'Mystery Ledger' },
    ],
    detected_format: 'csv',
    csv_columns: ['Date', 'Merchant/Description', 'Category', 'Amount'],
    preset: 'monarch',
    failed_rows: [],
    preset_category_suggestions: suggestions,
    dry_run: { rows_in: 3, would_import: 3, would_skip_dupes: 0, failed_rows: 0, unmapped_categories: 1 },
    ...overrides,
  }
}

function genericPreview() {
  return {
    transactions: [
      { description: 'Plain Row', amount: 5, date: '2026-01-05', type: 'debit', category_name: null },
    ],
    detected_format: 'csv',
    csv_columns: ['date', 'description', 'amount'],
    preset: null,
    failed_rows: [],
    preset_category_suggestions: [],
    dry_run: null,
  }
}

async function uploadFile(user: ReturnType<typeof renderWithProviders>['user'], confirmMonarch = true) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File(['date,amount\n'], 'monarch-combined.csv', { type: 'text/csv' })
  await user.upload(input, file)
  if (confirmMonarch) await user.selectOptions(await screen.findByLabelText(t('import.sourcePreset')), 'monarch')
}

describe('Monarch preset import flow', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    api.accounts.list.mockResolvedValue(accounts)
    api.categories.list.mockResolvedValue(categories)
    api.categoryGroups.list.mockResolvedValue(groups)
    api.importLogs.list.mockResolvedValue([])
    api.transactions.list.mockResolvedValue({ items: [], total: 0, page: 1, pages: 1 })
  })

  it('renders preset selector with auto default', async () => {
    api.transactions.previewImport.mockResolvedValue(genericPreview())
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user, false)

    const select = await screen.findByLabelText(t('import.sourcePreset'))
    expect(select).toHaveValue('auto')
    expect(screen.getByRole('option', { name: t('import.presetAuto') })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: t('import.presetMonarch') })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: t('import.presetGeneric') })).toBeInTheDocument()
  })

  it('selecting monarch preset binds date format and flip amount', async () => {
    api.transactions.previewImport.mockResolvedValue(genericPreview())
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await screen.findByLabelText(t('import.sourcePreset'))

    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    await user.selectOptions(screen.getByLabelText(t('import.sourcePreset')), 'monarch')

    // Pre-bound controls (§7.1): strict MM/DD date format + flip on.
    await waitFor(() => {
      expect(screen.getByDisplayValue('MM/DD/YYYY')).toBeInTheDocument()
    })
    expect(screen.getByLabelText(t('import.flipAmounts'))).toBeChecked()
    // The re-preview carries the forced preset and its bound options.
    await waitFor(() => {
      expect(api.transactions.previewImport).toHaveBeenCalledWith(
        expect.anything(),
        expect.objectContaining({
          preset: 'monarch',
        }),
      )
    })
  })

  it.each([false, true])('preserves explicit flip %s across presets and re-previews', async (flip) => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    const preset = await screen.findByLabelText(t('import.sourcePreset'))
    await user.selectOptions(preset, 'monarch')
    const checkbox = screen.getByLabelText(t('import.flipAmounts'))
    if (!flip) await user.click(checkbox)
    else { await user.click(checkbox); await user.click(checkbox) }
    await user.selectOptions(preset, 'generic')
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    expect(api.transactions.previewImport.mock.lastCall?.[1]).toEqual(expect.objectContaining({ flip_amount: flip }))
    expect(checkbox).toHaveProperty('checked', flip)
    await user.selectOptions(preset, 'monarch')
    expect(api.transactions.previewImport.mock.lastCall?.[1]).toEqual(expect.objectContaining({ flip_amount: flip }))
    expect(checkbox).toHaveProperty('checked', flip)
  })

  it('inherits defaults without turning them into explicit choices', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    const preset = await screen.findByLabelText(t('import.sourcePreset'))
    await user.selectOptions(preset, 'monarch')
    expect(screen.getByLabelText(t('import.flipAmounts'))).toBeChecked()
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    await user.selectOptions(screen.getByLabelText(t('import.mappingTargetFor', { value: 'Mystery Ledger' })), 'cat-handmade')
    expect(api.transactions.previewImport.mock.lastCall?.[1]).not.toHaveProperty('flip_amount')
    expect(api.transactions.previewImport.mock.lastCall?.[1]).not.toHaveProperty('date_format')
    await user.selectOptions(preset, 'generic')
    expect(screen.getByLabelText(t('import.flipAmounts'))).not.toBeChecked()
    expect(screen.getByDisplayValue(t('import.dateFormatAuto'))).toBeInTheDocument()
    expect(api.transactions.previewImport.mock.lastCall?.[1]).not.toHaveProperty('date_format')
  })

  it('ignores older preview responses and keeps the latest mapping choice', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    const preset = await screen.findByLabelText(t('import.sourcePreset'))
    let resolveOld!: (value: ReturnType<typeof monarchPreview>) => void
    api.transactions.previewImport.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    await user.selectOptions(preset, 'monarch')
    api.transactions.previewImport.mockResolvedValue(genericPreview())
    await user.selectOptions(preset, 'generic')
    await screen.findByText('Plain Row')
    resolveOld(monarchPreview())
    await waitFor(() => expect(screen.queryByText('Fernwood Coffee')).not.toBeInTheDocument())
    expect(screen.getByText('Plain Row')).toBeInTheDocument()
  })

  it('mapping card lists distinct categories with suggestions', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)

    const card = await screen.findByTestId('category-mapping-card')
    expect(within(card).getByText('Food & Dining: Coffee Shops')).toBeInTheDocument()
    expect(within(card).getByText('Income: Paycheck')).toBeInTheDocument()
    expect(within(card).getByText('Mystery Ledger')).toBeInTheDocument()
    // Suggested targets are the default selections.
    const foodSelect = within(card).getByRole('combobox', {
      name: t('import.mappingTargetFor', { value: 'Food & Dining: Coffee Shops' }),
    })
    expect(foodSelect).toHaveValue('cat-food')
  })

  it('mapping selection survives re-preview', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)

    const card = await screen.findByTestId('category-mapping-card')
    const mysterySelect = within(card).getByRole('combobox', {
      name: t('import.mappingTargetFor', { value: 'Mystery Ledger' }),
    })
    // Unmapped value defaults to "leave to rules"; pick a real category.
    await user.selectOptions(mysterySelect, 'cat-handmade')

    // Choosing triggers one re-preview (§7.5); the response carries the
    // server suggestion again — the user's choice must override it.
    await waitFor(() => {
      expect(api.transactions.previewImport.mock.calls.length).toBeGreaterThan(1)
    })
    await waitFor(() => {
      expect(
        within(screen.getByTestId('category-mapping-card')).getByRole('combobox', {
          name: t('import.mappingTargetFor', { value: 'Mystery Ledger' }),
        }),
      ).toHaveValue('cat-handmade')
    })
  })

  it('summary bar shows dupe and unmapped counts', async () => {
    api.transactions.previewImport.mockResolvedValue(
      monarchPreview({
        dry_run: { rows_in: 17, would_import: 14, would_skip_dupes: 2, failed_rows: 3, unmapped_categories: 1 },
      }),
    )
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)

    const dupes = await screen.findByTestId('summary-dupes')
    expect(dupes).toHaveTextContent(`2 ${t('import.summaryDupes')}`)
    expect(screen.getByTestId('summary-rows-in')).toHaveTextContent(`17 ${t('import.summaryRowsIn')}`)
    expect(screen.getByTestId('summary-failed')).toHaveTextContent(`3 ${t('import.summaryFailed')}`)
    // Client-computed unmapped: Mystery Ledger has no target (§4.4).
    expect(screen.getByTestId('summary-unmapped')).toHaveTextContent(`1 ${t('import.summaryUnmapped')}`)
  })

  it('end-of-import report dialog lists unmapped categories', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    api.transactions.import.mockResolvedValue({ imported: 3, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await screen.findByTestId('category-mapping-card')

    // Only an explicit choice establishes the uncategorized outcome.
    await user.selectOptions(screen.getByLabelText(t('import.mappingTargetFor', { value: 'Mystery Ledger' })), '')

    await user.selectOptions(
      await screen.findByLabelText(t('import.importTo')),
      'acc-1',
    )
    await user.click(await screen.findByRole('button', { name: t('import.importButton', { count: 3 }) }))

    const list = await screen.findByTestId('import-report-list')
    // The explicit uncategorized choice is reported as such (§4.4) …
    const mysteryRow = within(list).getByTestId('report-row-Mystery Ledger')
    expect(mysteryRow).toHaveTextContent(t('import.reportUncategorized'))
    // … and mapped values show their resolved target.
    expect(within(list).getByTestId('report-row-Food & Dining: Coffee Shops')).toHaveTextContent(
      'Food & Dining',
    )
    expect(screen.getByText(t('import.reportFootnote'))).toBeInTheDocument()
  })

  it('reports leave-to-rules before category strings without a suggested target label', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    api.transactions.import.mockResolvedValue({ imported: 3, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByLabelText(t('import.mappingTargetFor', { value: 'Food & Dining: Coffee Shops' })), 'rules')
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 3 }) }))
    const row = within(await screen.findByTestId('import-report-list')).getByTestId('report-row-Food & Dining: Coffee Shops')
    expect(within(row).getAllByRole('cell')[2]).toHaveTextContent(t('import.reportLeftToRules'))
    expect(within(row).getAllByRole('cell')[2]).not.toHaveTextContent('Food & Dining')
    expect(api.transactions.import.mock.lastCall?.[1][0].category_id).toBeUndefined()
  })

  it.each([true, false])('resumes rejected bucket without reposting acknowledged buckets (duplicates %s)', async (detectDuplicates) => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview({
      transactions: [
        { description: 'First bucket', amount: 5, date: '2026-01-05', type: 'debit', account_hint: 'First' },
        { description: 'Second bucket', amount: 6, date: '2026-01-06', type: 'debit', account_hint: 'Second' },
        { description: 'Third bucket', amount: 7, date: '2026-01-07', type: 'debit', account_hint: 'Third' },
      ],
    }))
    api.transactions.import
      .mockResolvedValueOnce({ imported: 1, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
      .mockRejectedValueOnce({ response: { status: 404, headers: { 'x-import-outcome': 'not-committed' }, data: { detail: 'Account not found' } } })
      .mockResolvedValue({ imported: 1, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000002' })
    const { user, queryClient } = renderWithProviders(<ImportPage />, { route: '/import' })
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    await uploadFile(user)
    for (const [i, hint] of ['First', 'Second', 'Third'].entries()) await user.selectOptions(screen.getByTestId(`routing-select-${hint}`), `acc-${i + 1}`)
    if (!detectDuplicates) await user.click(screen.getByLabelText(t('import.detectDuplicates')))
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 3 }) }))
    const progress = await screen.findByTestId('import-progress')
    expect(progress).toHaveTextContent(t('import.progressTotals', { imported: 1, skipped: 0, excluded: 0 }))
    expect(progress).toHaveTextContent('00000000-0000-4000-8000-000000000001')
    expect(screen.queryByTestId('import-report-list')).not.toBeInTheDocument()
    for (const key of ['transactions', 'import-logs', 'payees', 'categories']) expect(invalidate).toHaveBeenCalledWith({ queryKey: [key] })
    expect(screen.getByLabelText(t('import.sourcePreset'))).toBeDisabled()
    expect(screen.getByTestId('routing-select-Second')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: t('import.resumeImport') }))
    await screen.findByTestId('import-report-list')
    expect(api.transactions.import.mock.calls.map((call) => call[1][0].description)).toEqual(['First bucket', 'Second bucket', 'Second bucket', 'Third bucket'])
    for (const call of api.transactions.import.mock.calls) expect(call[4]).toEqual({ detect_duplicates: detectDuplicates })
    expect(api.transactions.import.mock.calls.map((call) => call[0])).toEqual(['acc-1', 'acc-2', 'acc-2', 'acc-3'])
  })

  it.each([
    new Error('response lost after commit'),
    { response: { status: 500, data: { detail: 'unproven failure' } } },
    { malformed: true },
  ])('halts replay for uncertain outcomes %j', async (failure) => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview({
      transactions: ['First', 'Second', 'Third'].map((hint) => ({ description: hint, amount: 5, date: '2026-01-05', type: 'debit', account_hint: hint })),
    }))
    api.transactions.import.mockResolvedValueOnce({ imported: 1, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    if ('malformed' in failure) api.transactions.import.mockResolvedValueOnce({ imported: 1 })
    else api.transactions.import.mockRejectedValueOnce(failure)
    const { user, queryClient } = renderWithProviders(<ImportPage />, { route: '/import' })
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    await uploadFile(user)
    for (const [i, hint] of ['First', 'Second', 'Third'].entries()) await user.selectOptions(screen.getByTestId(`routing-select-${hint}`), `acc-${i + 1}`)
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 3 }) }))
    expect(await screen.findByTestId('import-progress')).toHaveTextContent(t('import.outcomeUnknown'))
    expect(screen.getByRole('button', { name: t('import.resumeImport') })).toBeDisabled()
    expect(api.transactions.import).toHaveBeenCalledTimes(2)
    expect(invalidate.mock.calls.filter(([arg]) => arg?.queryKey?.[0] === 'transactions')).toHaveLength(2)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await user.click(screen.getByRole('button', { name: t('common.cancel') }))
    expect(confirm).toHaveBeenCalled()
    expect(screen.getByTestId('import-progress')).toBeInTheDocument()
    confirm.mockReturnValue(true)
    await user.click(screen.getByRole('button', { name: t('common.cancel') }))
    // Even confirming abandonment cannot turn uncertainty into replay safety.
    expect(screen.getByTestId('import-progress')).toBeInTheDocument()
    expect(api.transactions.import).toHaveBeenCalledTimes(2)
    confirm.mockRestore()
  })

  it('prevents double submission and freezes edits while a bucket is in flight', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    api.transactions.import.mockImplementation(() => new Promise(() => {}))
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    await user.dblClick(screen.getByRole('button', { name: t('import.importButton', { count: 3 }) }))
    expect(api.transactions.import).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText(t('import.importTo'))).toBeDisabled()
    expect(screen.getByLabelText(t('import.detectDuplicates'))).toBeDisabled()
    expect(screen.getByRole('button', { name: t('common.cancel') })).toBeDisabled()
  })

  it.each([
    { names: ['Everyday Checking'], hint: 'Everyday-Checking', expected: 'acc-1' },
    { names: ['東京', '大阪'], hint: '大阪', expected: 'acc-2' },
    { names: ['Café'], hint: 'Cafe\u0301', expected: 'acc-1' },
    { names: ['Cafe', 'Café'], hint: 'Café', expected: 'acc-2' },
    { names: ['東京'], hint: '大阪', expected: '' },
    { names: ['Bank-A', 'Bank A'], hint: 'Bank A', expected: '' },
    { names: ['!!!', '???'], hint: '---', expected: '' },
  ])('shows safe effective routing for $hint', async ({ names, hint, expected }) => {
    api.accounts.list.mockResolvedValue(names.map((name, i) => ({ ...accounts[0], id: `acc-${i + 1}`, name })))
    api.transactions.previewImport.mockResolvedValue(monarchPreview({
      transactions: [{ description: 'Routed row', amount: 5, date: '2026-01-05', type: 'debit', account_hint: hint }],
    }))
    api.transactions.import.mockResolvedValue({ imported: 1, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    expect(screen.getByTestId(`routing-select-${hint}`)).toHaveValue(expected)
    const button = screen.getByRole('button', { name: t('import.importButton', { count: 1 }) })
    if (!expected) expect(button).toBeDisabled()
    else {
      expect(button).toBeEnabled()
      await user.click(button)
      await waitFor(() => expect(api.transactions.import).toHaveBeenCalledWith(expected, expect.anything(), expect.anything(), 'csv', expect.anything()))
    }
  })

  it('respects clearing an automatic routing match', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview({
      transactions: [{ description: 'Routed row', amount: 5, date: '2026-01-05', type: 'debit', account_hint: 'Everyday Checking' }],
    }))
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    const select = screen.getByTestId('routing-select-Everyday Checking')
    expect(select).toHaveValue('acc-1')
    await user.selectOptions(select, '')
    expect(select).toHaveValue('')
    expect(screen.getByRole('button', { name: t('import.importButton', { count: 1 }) })).toBeDisabled()
  })

  it.each(['generic', 'monarch', 'flip', 'date'])('blocks imports after failed current %s preview until a new success', async (change) => {
    const resolvedPreview = (_file: File, options?: { preset?: string }) => Promise.resolve(monarchPreview({
      preset: options?.preset === 'monarch' ? 'monarch' : null,
      preset_category_suggestions: options?.preset === 'monarch' ? suggestions : [],
    }))
    api.transactions.previewImport.mockImplementation(resolvedPreview)
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    const preset = screen.getByLabelText(t('import.sourcePreset'))
    if (change === 'monarch') await user.selectOptions(preset, 'generic')
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    const button = screen.getByRole('button', { name: t('import.importButton', { count: 3 }) })
    await waitFor(() => expect(button).toBeEnabled())
    api.transactions.previewImport.mockRejectedValue(new Error('synthetic current preview failure'))
    if (change === 'flip') await user.click(screen.getByLabelText(t('import.flipAmounts')))
    else if (change === 'date') await user.selectOptions(screen.getByDisplayValue('MM/DD/YYYY'), 'DD/MM/YYYY')
    else await user.selectOptions(preset, change)
    await waitFor(() => expect(screen.queryByText(t('import.processing'))).not.toBeInTheDocument())
    expect(button).toBeDisabled()
    await user.click(button)
    expect(api.transactions.import).not.toHaveBeenCalled()
    api.transactions.previewImport.mockImplementation(resolvedPreview)
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    await waitFor(() => expect(button).toBeEnabled())
  })

  it('blocks same-turn submission when an option starts a new preview', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    api.transactions.import.mockImplementation(() => new Promise(() => {}))
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    const button = screen.getByRole('button', { name: t('import.importButton', { count: 3 }) })
    await waitFor(() => expect(button).toBeEnabled())
    api.transactions.previewImport.mockImplementationOnce(() => new Promise(() => {}))
    await act(async () => {
      fireEvent.change(screen.getByLabelText(t('import.sourcePreset')), { target: { value: 'generic' } })
      fireEvent.click(button)
    })
    expect(api.transactions.import).not.toHaveBeenCalled()
    expect(button).toBeDisabled()
  })

  it('does not let an old success reauthorize rows after the latest preview failed', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    let resolveOld!: (value: ReturnType<typeof monarchPreview>) => void
    api.transactions.previewImport.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    await user.click(screen.getByLabelText(t('import.flipAmounts')))
    api.transactions.previewImport.mockRejectedValueOnce(new Error('latest revision failed'))
    await user.selectOptions(screen.getByDisplayValue('MM/DD/YYYY'), 'DD/MM/YYYY')
    const button = screen.getByRole('button', { name: t('import.importButton', { count: 3 }) })
    await waitFor(() => expect(screen.queryByText(t('import.processing'))).not.toBeInTheDocument())
    await act(async () => resolveOld(monarchPreview({ transactions: [] })))
    expect(screen.getByText('Fernwood Coffee')).toBeInTheDocument()
    expect(button).toBeDisabled()
    await user.click(button)
    expect(api.transactions.import).not.toHaveBeenCalled()
  })

  it('preserves an explicit auto date choice across preset switches', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByDisplayValue('MM/DD/YYYY'), '')
    for (const preset of ['generic', 'monarch']) {
      await user.selectOptions(screen.getByLabelText(t('import.sourcePreset')), preset)
      expect(api.transactions.previewImport.mock.lastCall?.[1]).toEqual(expect.objectContaining({ date_format: 'auto' }))
      expect(screen.getByDisplayValue(t('import.dateFormatAuto'))).toBeInTheDocument()
    }
  })

  it('does not apply retained Monarch category mappings to a resolved generic preview', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    api.transactions.import.mockResolvedValue({ imported: 1, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByLabelText(t('import.mappingTargetFor', { value: 'Mystery Ledger' })), 'cat-handmade')
    api.transactions.previewImport.mockResolvedValue({ ...genericPreview(), transactions: [monarchPreview().transactions[2]] })
    await user.selectOptions(screen.getByLabelText(t('import.sourcePreset')), 'generic')
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 1 }) }))
    await waitFor(() => expect(api.transactions.import).toHaveBeenCalled())
    expect(api.transactions.import.mock.lastCall?.[1][0]).toEqual(expect.objectContaining({ category_name: 'Mystery Ledger', force_uncategorized: false }))
    expect(api.transactions.import.mock.lastCall?.[1][0].category_id).toBeUndefined()
    expect(screen.queryByTestId('import-report-list')).not.toBeInTheDocument()
  })

  it('reports an undefined suggestion as no mapping contribution, not observed uncategorized', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    api.transactions.import.mockResolvedValue({ imported: 3, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    expect(screen.getByLabelText(t('import.mappingTargetFor', { value: 'Mystery Ledger' }))).toHaveValue('rules')
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 3 }) }))
    const row = within(await screen.findByTestId('import-report-list')).getByTestId('report-row-Mystery Ledger')
    expect(row).toHaveTextContent(t('import.reportLeftToRules'))
    expect(row).not.toHaveTextContent(t('import.reportUncategorized'))
    expect(api.transactions.import.mock.lastCall?.[1][2].category_id).toBeUndefined()
    expect(api.transactions.import.mock.lastCall?.[1][2].force_uncategorized).toBe(false)
  })

  it.each([true, false])('groups hints and unassigned occurrences once per destination (duplicates %s)', async (detect) => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview({ transactions: ['First', undefined, 'Second'].map((hint) => ({ description: 'Same', amount: 5, date: '2026-01-05', type: 'debit', account_hint: hint })) }))
    api.transactions.import.mockResolvedValue({ imported: 3, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    for (const hint of ['First', 'Second']) await user.selectOptions(screen.getByTestId(`routing-select-${hint}`), 'acc-1')
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    if (!detect) await user.click(screen.getByLabelText(t('import.detectDuplicates')))
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 3 }) }))
    await screen.findByTestId('import-report-list')
    expect(api.transactions.import).toHaveBeenCalledTimes(1)
    expect(api.transactions.import.mock.lastCall?.[1].map((r: { account_hint?: string }) => r.account_hint)).toEqual(['First', undefined, 'Second'])
    expect(api.transactions.import.mock.lastCall?.[4]).toEqual({ detect_duplicates: detect })
  })

  it('keeps every contributing source unresolved after losing a grouped acknowledgement', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview({ transactions: ['First', undefined, 'Second'].map((hint) => ({ description: 'Same', amount: 5, date: '2026-01-05', type: 'debit', account_hint: hint })) }))
    api.transactions.import.mockRejectedValue(new Error('lost grouped acknowledgement'))
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    for (const hint of ['First', 'Second']) await user.selectOptions(screen.getByTestId(`routing-select-${hint}`), 'acc-1')
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 3 }) }))
    const progress = await screen.findByTestId('import-progress')
    expect(progress).toHaveTextContent('First')
    expect(progress).toHaveTextContent('Second')
    expect(progress).toHaveTextContent(t('import.unassignedSource'))
    expect(progress).toHaveTextContent(t('import.outcomeUnknown'))
    const resume = screen.getByRole('button', { name: t('import.resumeImport') })
    expect(resume).toBeDisabled()
    await user.click(resume)
    expect(api.transactions.import).toHaveBeenCalledTimes(1)
    expect(api.transactions.import.mock.lastCall?.[1]).toHaveLength(3)
  })

  it.each(['account', 'mapping', 'date'])('warns, resets and requires renewed review after individual edits and %s change', async (change) => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    api.transactions.import.mockResolvedValue({ imported: 3, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    const row = screen.getByText('Odd Vendor').closest('tr')!
    await user.click(within(row).getByRole('checkbox'))
    await user.click(within(row).getByRole('button'))
    await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: /Handmade Odds/ }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const trigger = async () => {
      if (change === 'account') await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-2')
      else if (change === 'mapping') await user.selectOptions(screen.getByLabelText(t('import.mappingTargetFor', { value: 'Income: Paycheck' })), 'cat-handmade')
      else await user.selectOptions(screen.getByDisplayValue('MM/DD/YYYY'), 'DD/MM/YYYY')
    }
    const calls = api.transactions.previewImport.mock.calls.length
    await trigger()
    expect(confirm).toHaveBeenCalledWith(t('import.resetReviewConfirm'))
    expect(api.transactions.previewImport).toHaveBeenCalledTimes(calls)
    if (change === 'account') expect(screen.getByLabelText(t('import.importTo'))).toHaveValue('acc-1')
    expect(within(row).getByRole('checkbox')).not.toBeChecked()
    expect(within(row).getByRole('button')).toHaveTextContent('Handmade Odds')
    if (change === 'mapping') expect(screen.getByLabelText(t('import.mappingTargetFor', { value: 'Income: Paycheck' }))).toHaveValue('cat-salary')
    if (change === 'date') expect(screen.getByDisplayValue('MM/DD/YYYY')).toBeInTheDocument()
    confirm.mockReturnValue(true)
    await trigger()
    await screen.findByText(t('import.reviewReset'))
    const refreshed = within(screen.getByText('Odd Vendor').closest('tr')!)
    expect(refreshed.getByRole('checkbox')).toBeChecked()
    expect(refreshed.getByRole('button')).not.toHaveTextContent('Handmade Odds')
    const button = screen.getByRole('button', { name: t('import.importButton', { count: 3 }) })
    expect(button).toBeDisabled()
    await user.click(button)
    expect(api.transactions.import).not.toHaveBeenCalled()
    await user.click(screen.getByLabelText(t('import.reviewedPreview')))
    expect(button).toBeEnabled()
    await user.selectOptions(screen.getByLabelText(t('import.sourcePreset')), 'generic')
    expect(screen.getByLabelText(t('import.reviewedPreview'))).not.toBeChecked()
    expect(button).toBeDisabled()
    confirm.mockRestore()
  })

  it('does not transfer decisions to duplicate or shifted rows and blocks pending row edits', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview({ transactions: [monarchPreview().transactions[0], monarchPreview().transactions[0], monarchPreview().transactions[2]] }))
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.click(within(screen.getByText('Odd Vendor').closest('tr')!).getByRole('checkbox'))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    let resolve!: (value: ReturnType<typeof monarchPreview>) => void
    api.transactions.previewImport.mockImplementationOnce(() => new Promise((r) => { resolve = r }))
    await user.selectOptions(screen.getByDisplayValue('MM/DD/YYYY'), 'DD/MM/YYYY')
    expect(within(screen.getByText('Odd Vendor').closest('tr')!).getByRole('checkbox')).toBeDisabled()
    await act(async () => resolve(monarchPreview({ transactions: [monarchPreview().transactions[2], monarchPreview().transactions[0]], failed_rows: [{ line_number: 2, description: 'failed duplicate', raw_value: 'bad date', error_reason: 'invalid_date' }] })))
    expect(within(screen.getByText('Odd Vendor').closest('tr')!).getByRole('checkbox')).toBeChecked()
    expect(screen.getAllByText('Fernwood Coffee')).toHaveLength(1)
    expect(screen.getByLabelText(t('import.reviewedPreview'))).not.toBeChecked()
    confirm.mockRestore()
  })

  // Read the real React DOM callback without replacing React or page behavior.
  // Direct invocation deliberately bypasses disabled-event suppression, modelling
  // a queued/programmatic callback retained from a successfully rendered preview.
  function captureReviewChange() {
    const input = screen.getByLabelText(t('import.reviewedPreview'))
    const key = Object.keys(input).find((key) => key.startsWith('__reactProps$'))!
    const props = (input as unknown as Record<string, { onChange: React.ChangeEventHandler<HTMLInputElement> }>)[key]
    expect(props.onChange).toBeTypeOf('function')
    return vi.fn(props.onChange)
  }

  it.each(['same-turn', 'pending', 'replaced'])('rejects captured review confirmation from a superseded successful preview (%s)', async (timing) => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    api.transactions.import.mockResolvedValue({ imported: 3, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    await user.click(within(screen.getByText('Odd Vendor').closest('tr')!).getByRole('checkbox'))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await user.selectOptions(screen.getByDisplayValue('MM/DD/YYYY'), 'DD/MM/YYYY')
    const checkbox = await screen.findByLabelText(t('import.reviewedPreview'))
    await user.click(checkbox)
    const button = screen.getByRole('button', { name: t('import.importButton', { count: 3 }) })
    expect(button).toBeEnabled() // legitimate confirmation of the current success
    const captured = captureReviewChange()
    const event = { target: { checked: true } } as React.ChangeEvent<HTMLInputElement>
    let resolve!: (value: ReturnType<typeof monarchPreview>) => void
    api.transactions.previewImport.mockImplementationOnce(() => new Promise((r) => { resolve = r }))
    await act(async () => {
      fireEvent.change(screen.getByDisplayValue('DD/MM/YYYY'), { target: { value: 'YYYY-MM-DD' } })
      if (timing === 'same-turn') captured(event)
    })
    await waitFor(() => expect(resolve).toBeTypeOf('function'))
    if (timing === 'pending') await act(async () => captured(event))
    expect(button).toBeDisabled()
    expect(api.transactions.import).not.toHaveBeenCalled()
    await act(async () => resolve(monarchPreview({ transactions: [
      { ...monarchPreview().transactions[0], description: 'Replacement Coffee' },
      ...monarchPreview().transactions.slice(1),
    ] })))
    await screen.findByText('Replacement Coffee')
    if (timing === 'replaced') await act(async () => captured(event))
    expect(captured).toHaveBeenCalledTimes(1)
    // This oracle runs AFTER replacement success, not while React disables input.
    console.info(`captured production callback reached; replacement resolved; ${timing}; import enabled=${!button.hasAttribute('disabled')}`)
    expect(button).toBeDisabled()
    expect(screen.getByLabelText(t('import.reviewedPreview'))).not.toBeChecked()
    await user.click(button)
    expect(api.transactions.import).not.toHaveBeenCalled()
    await user.click(screen.getByLabelText(t('import.reviewedPreview')))
    expect(button).toBeEnabled()
    await user.click(button)
    await waitFor(() => expect(api.transactions.import).toHaveBeenCalledTimes(1))
    expect(api.transactions.import.mock.lastCall?.[1][0].description).toBe('Replacement Coffee')
    confirm.mockRestore()
  })

  it.each(['rules', 'suggestion', 'card'])('reports individual submitted overrides rather than the %s default', async (defaultKind) => {
    const value = defaultKind === 'rules' ? 'Mystery Ledger' : 'Food & Dining: Coffee Shops'
    const description = defaultKind === 'rules' ? 'Odd Vendor' : 'Fernwood Coffee'
    api.transactions.previewImport.mockResolvedValue(monarchPreview())
    api.transactions.import.mockResolvedValue({ imported: 3, skipped: 0, excluded: 0, import_log_id: '00000000-0000-4000-8000-000000000001' })
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    if (defaultKind === 'card') await user.selectOptions(screen.getByLabelText(t('import.mappingTargetFor', { value })), 'cat-salary')
    await user.click(within(screen.getByText(description).closest('tr')!).getByRole('button'))
    await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: /Handmade Odds/ }))
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 3 }) }))
    const row = within(await screen.findByTestId('import-report-list')).getByTestId(`report-row-${value}`)
    expect(within(row).getAllByRole('cell')[2]).toHaveTextContent('Handmade Odds')
    expect(api.transactions.import.mock.lastCall?.[1].find((r: { description: string }) => r.description === description)).toEqual(expect.objectContaining({ category_id: 'cat-handmade', force_uncategorized: false }))
  })

  it.each([true, false])('splits mixed submitted intentions, excludes noncontributing rows and resolves only matching target IDs (suggestions %s)', async (withSuggestions) => {
    const values = ['Override', 'Force empty', 'No contribution', 'Omitted row', 'Unattempted', 'Another ID', 'No contribution again']
    api.categoryGroups.list.mockResolvedValue(groups.map((group) => ({ ...group, categories: categories.filter((category) => category.group_id === group.id) })))
    api.transactions.previewImport.mockResolvedValue(monarchPreview({
      preset_category_suggestions: withSuggestions ? suggestions : [],
      transactions: values.map((description) => ({ description, amount: 5, date: '2026-01-05', type: 'debit', category_name: 'Mystery Ledger', account_hint: description === 'Unattempted' ? 'Skipped source' : undefined })),
    }))
    let finish!: (value: unknown) => void
    api.transactions.import.mockImplementationOnce(() => new Promise((r) => { finish = r }))
    const { user, queryClient } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByLabelText(t('import.importTo')), 'acc-1')
    await user.selectOptions(screen.getByTestId('routing-select-Skipped source'), '__skip__')
    for (const [description, option] of [['Override', /Handmade Odds/], ['Force empty', new RegExp(`^${t('transactions.noCategory')}$`)], ['Another ID', /Food & Dining/]] as const) {
      await user.click(within(screen.getByText(description).closest('tr')!).getByRole('button'))
      await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: option }))
    }
    await user.click(within(screen.getByText('Omitted row').closest('tr')!).getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 6 }) }))
    await waitFor(() => expect(finish).toBeTypeOf('function'))
    const submitted = api.transactions.import.mock.lastCall?.[1]
    expect(submitted).toHaveLength(6)
    expect(submitted.map((r: { description: string }) => r.description)).not.toContain('Unattempted')
    expect(submitted[1]).toEqual(expect.objectContaining({ force_uncategorized: true, category_id: undefined }))
    expect(submitted[2]).toEqual(expect.objectContaining({ force_uncategorized: false, category_id: undefined }))
    expect(submitted[3].excluded).toBe(true)
    // The frozen ID must not borrow a different suggestion's label if removed.
    await act(async () => { queryClient.setQueryData(['categories'], categories.filter((c) => c.id !== 'cat-handmade')) })
    await act(async () => finish({ imported: 4, skipped: 1, excluded: 1, import_log_id: '00000000-0000-4000-8000-000000000001' }))
    const list = await screen.findByTestId('import-report-list')
    const rows = within(list).getAllByTestId('report-row-Mystery Ledger')
    expect(rows).toHaveLength(4)
    expect(rows.map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))).toEqual([
      ['Mystery Ledger', '1', 'cat-handmade'],
      ['Mystery Ledger', '1', t('import.reportUncategorized')],
      ['Mystery Ledger', '2', t('import.reportLeftToRules')],
      ['Mystery Ledger', '1', 'Food & Dining'],
    ])
    expect(new Set(rows.map((row) => row.getAttribute('data-report-key'))).size).toBe(4)
    expect(screen.getByText(t('import.routingSkipped', { count: 1 }))).toBeInTheDocument()
    expect(screen.getByTestId('import-report-accounts')).toHaveTextContent(t('import.reportAccountLine', { name: 'Everyday Checking', imported: 4, skipped: 1 }))
  })

  it('preflights every destination size before dispatching even the first request', async () => {
    api.transactions.previewImport.mockResolvedValue(monarchPreview({ transactions: [
      { description: 'Small', amount: 5, date: '2026-01-05', type: 'debit', account_hint: 'First' },
      { description: 'Large notes', amount: 5, date: '2026-01-05', type: 'debit', account_hint: 'Second', notes: 'x'.repeat(15 * 1024 * 1024) },
    ] }))
    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)
    await user.selectOptions(screen.getByTestId('routing-select-First'), 'acc-1')
    await user.selectOptions(screen.getByTestId('routing-select-Second'), 'acc-2')
    await user.click(screen.getByRole('button', { name: t('import.importButton', { count: 2 }) }))
    expect(api.transactions.import).not.toHaveBeenCalled()
    expect(screen.queryByTestId('import-progress')).not.toBeInTheDocument()
  })

  // §8.4 case 6 / §7.2: combined export → routing strip; unrouted blocks import.
  it('combined file shows account routing strip and blocks unrouted import', async () => {
    const combined = monarchPreview({
      transactions: [
        {
          description: 'Fernwood Coffee',
          amount: 4.99,
          date: '2026-03-16',
          type: 'debit',
          category_name: 'Food & Dining: Coffee Shops',
          account_hint: 'BlueBank Checking',
        },
        {
          description: 'Harborline Payroll',
          amount: 1200,
          date: '2026-03-05',
          type: 'credit',
          category_name: 'Income: Paycheck',
          account_hint: 'BlueBank Checking',
        },
        {
          description: 'RedCard Payment',
          amount: 50,
          date: '2026-03-20',
          type: 'debit',
          category_name: 'Transfers',
          account_hint: 'RedCard Savings',
        },
      ],
    })
    api.transactions.previewImport.mockResolvedValue(combined)
    api.accounts.list.mockResolvedValue([
      { id: 'acc-1', name: 'Unrelated Bank', type: 'checking', currency: 'BRL', balance: 0 },
    ])

    const { user } = renderWithProviders(<ImportPage />, { route: '/import' })
    await uploadFile(user)

    const strip = await screen.findByTestId('account-routing-strip')
    expect(within(strip).getByText('BlueBank Checking')).toBeInTheDocument()
    expect(within(strip).getByText('RedCard Savings')).toBeInTheDocument()
    expect(within(strip).getByText('2 rows')).toBeInTheDocument()
    expect(within(strip).getByText('1 rows')).toBeInTheDocument()

    // No Securo account matches either value → both unrouted → blocked.
    expect(screen.getByTestId('routing-blocked')).toBeInTheDocument()
    const importName = t('import.importButton', { count: 3 })
    expect(screen.getByRole('button', { name: importName })).toBeDisabled()

    // Route one target, skip the other → the block clears.
    await user.selectOptions(screen.getByTestId('routing-select-BlueBank Checking'), 'acc-1')
    await user.selectOptions(screen.getByTestId('routing-select-RedCard Savings'), '__skip__')
    expect(screen.queryByTestId('routing-blocked')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: importName })).not.toBeDisabled()
  })
})

describe('mapping card (direct render)', () => {
  it('offers category, uncategorized, and leave-to-rules targets', () => {
    renderWithProviders(
      <ImportCategoryMapping
        suggestions={suggestions}
        choices={{}}
        categories={categories}
        groups={groups}
        onChoose={() => {}}
      />,
    )
    const card = screen.getByTestId('category-mapping-card')
    // One picker per row — scope to a single row so queries stay unique.
    const row = within(card).getByTestId('mapping-row-Food & Dining: Coffee Shops')
    expect(within(row).getByRole('option', { name: t('import.leaveToRules') })).toBeInTheDocument()
    expect(within(row).getByRole('option', { name: t('import.uncategorizedOption') })).toBeInTheDocument()
    expect(within(card).getByText(/2 rows/)).toBeInTheDocument()
  })
})

describe('summary bar (direct render)', () => {
  const txns = [
    { _id: 'a', description: 'x', amount: 5, date: '2026-01-01', type: 'debit', excluded: false },
  ] as unknown as ImportReviewTransaction[]

  it('renders dry-run counts when provided', () => {
    renderWithProviders(
      <ImportSummaryBar
        transactions={txns}
        userCurrency="USD"
        locale="en-US"
        dryRun={{ rows_in: 10, would_import: 8, would_skip_dupes: 1, failed_rows: 1, unmapped_categories: 2 }}
        unmappedCount={2}
      />,
    )
    expect(screen.getByTestId('summary-dupes')).toHaveTextContent(`1 ${t('import.summaryDupes')}`)
    expect(screen.getByTestId('summary-unmapped')).toHaveTextContent(`2 ${t('import.summaryUnmapped')}`)
  })

  it('keeps the original shape without a dry-run', () => {
    renderWithProviders(
      <ImportSummaryBar transactions={txns} userCurrency="USD" locale="en-US" />,
    )
    expect(screen.queryByTestId('summary-dupes')).not.toBeInTheDocument()
  })
})
