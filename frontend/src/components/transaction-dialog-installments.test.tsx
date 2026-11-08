import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { TransactionDialog } from '@/components/transaction-dialog'
import { currencies, payees, settings } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'

vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-US',
  useDisplayLocale: () => 'de-DE',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ privacyMode: false, MASK: '••••' }),
}))

vi.mock('@/contexts/auth-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/auth-context')>()),
  useAuth: () => ({ user: null }),
}))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  currencies: { list: vi.fn() },
  payees: { list: vi.fn() },
  settings: { attachments: vi.fn() },
}))

describe('TransactionDialog with comma decimals', () => {
  beforeEach(() => {
    vi.mocked(currencies.list).mockResolvedValue([])
    vi.mocked(payees.list).mockResolvedValue([])
    vi.mocked(settings.attachments).mockResolvedValue({
      allowed_extensions: ['pdf'],
      max_file_size_mb: 10,
      max_attachments_per_transaction: 10,
    })
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  async function saveNewTransaction(typedAmount: string, repeatAsInstallments: boolean) {
    const onSave = vi.fn()
    const { user } = renderWithProviders(
      <TransactionDialog
        open
        onClose={vi.fn()}
        transaction={null}
        categories={[]}
        categoryGroups={[]}
        accounts={[{ id: 'acct-1', name: 'Checking', currency: 'EUR' }]}
        defaultAccountId="acct-1"
        onSave={onSave}
        loading={false}
        error={null}
      />,
    )
    const dialog = await screen.findByRole('dialog')
    await user.type(dialog.querySelector('input[required]:not([inputmode])')!, 'Notebook')
    await user.type(dialog.querySelector('input[inputmode="decimal"]')!, typedAmount)
    if (repeatAsInstallments) {
      await user.click(within(dialog).getByLabelText(t('transactions.makeInstallment')))
    }
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    return onSave.mock.calls[0]
  }

  it.each([
    ['50,25', 50.25],
    ['1.234,56', 1234.56],
  ])('saves %s as %d', async (typedAmount, expected) => {
    const [data, , installmentData] = await saveNewTransaction(typedAmount, false)

    expect(data.amount).toBe(expected)
    expect(installmentData).toBeUndefined()
  })

  it.each([
    ['50,25', 50.25],
    ['1.234,56', 1234.56],
  ])('repeats %s as installments of %d', async (typedAmount, expected) => {
    const [data, , installmentData] = await saveNewTransaction(typedAmount, true)

    expect(data.amount).toBe(expected)
    expect(installmentData.base.amount).toBe(expected)
  })
})
