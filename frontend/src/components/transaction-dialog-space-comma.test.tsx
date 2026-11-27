import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TransactionDialog } from '@/components/transaction-dialog'
import { currencies, payees, settings } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'

// Space grouping, comma decimal: the format from issue #1072.
vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-GB',
  useDisplayLocale: () => 'fr-FR',
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

beforeEach(() => {
  vi.mocked(currencies.list).mockResolvedValue([])
  vi.mocked(payees.list).mockResolvedValue([])
  vi.mocked(settings.attachments).mockResolvedValue({
    allowed_extensions: ['pdf'],
    max_file_size_mb: 10,
    max_attachments_per_transaction: 10,
  })
})

async function renderNewTransaction() {
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
  const description = dialog.querySelector('input[required]:not([inputmode])') as HTMLInputElement
  const amount = dialog.querySelector('input[inputmode="decimal"]') as HTMLInputElement
  return { user, dialog, description, amount, onSave }
}

describe('TransactionDialog under space grouping and comma decimals', () => {
  it('saves a comma decimal and keeps the cents in the installment series', async () => {
    const { user, dialog, description, amount, onSave } = await renderNewTransaction()

    await user.type(description, 'Notebook')
    await user.type(amount, '50,25')
    await user.click(within(dialog).getByLabelText(t('transactions.makeInstallment')))
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    expect(onSave).toHaveBeenCalledTimes(1)
    const [data, , installmentData] = onSave.mock.calls[0]
    expect(data.amount).toBe(50.25)
    expect(installmentData.base.amount).toBe(50.25)
  })

  it('explains the amount instead of a generic error, and does not save', async () => {
    const { user, dialog, description, amount, onSave } = await renderNewTransaction()

    await user.type(description, 'Notebook')
    await user.type(amount, '50.25')

    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      t('common.invalidAmount', { example: '1234,56' }),
    )
    expect(amount).toHaveAttribute('aria-invalid', 'true')

    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    expect(onSave).not.toHaveBeenCalled()
    expect(amount).toHaveAttribute('aria-invalid', 'true')
  })

  it('clears the message once the amount reads as one', async () => {
    const { user, dialog, description, amount, onSave } = await renderNewTransaction()

    await user.type(description, 'Notebook')
    await user.type(amount, '50.25')
    expect(within(dialog).getByRole('alert')).toBeInTheDocument()

    await user.clear(amount)
    await user.type(amount, '50,25')
    expect(within(dialog).queryByRole('alert')).toBeNull()

    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))
    expect(onSave.mock.calls[0][0].amount).toBe(50.25)
  })
})
