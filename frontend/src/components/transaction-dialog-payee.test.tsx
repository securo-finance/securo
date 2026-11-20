import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TransactionDialog } from '@/components/transaction-dialog'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { currencies, fiscal, groups, payees } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Payee } from '@/types'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-US',
  useDisplayLocale: () => 'en-US',
}))

afterEach(() => vi.restoreAllMocks())

const saved: Payee = {
  id: 'payee-new',
  user_id: null,
  name: 'Padaria Aurora',
  type: null,
  source: 'manual',
  is_favorite: false,
  notes: null,
  email: null,
  phone: null,
  address: null,
  website: null,
  tax_ids: [],
  created_at: '2026-11-19T00:00:00Z',
  transaction_count: 0,
}

/** Members who can write to the workspace see the payee controls. */
function renderWritable(payeeList: Payee[] = []) {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue(payeeList)
  vi.spyOn(groups, 'list').mockResolvedValue([])
  vi.spyOn(fiscal, 'taxIdKinds').mockResolvedValue({
    jurisdiction: null,
    kinds: [],
    jurisdictions: [],
  })
  return renderWithProviders(
    <WorkspaceContext.Provider
      value={{ canWrite: true } as unknown as React.ContextType<typeof WorkspaceContext>}
    >
      <TransactionDialog
        open
        onClose={vi.fn()}
        transaction={null}
        categories={[]}
        categoryGroups={[]}
        accounts={[]}
        onSave={vi.fn()}
        loading={false}
        error={null}
      />
    </WorkspaceContext.Provider>,
  )
}

describe('TransactionDialog payee form (#1069)', () => {
  it('creates the payee from the form and keeps it selected while the list refetches', async () => {
    const create = vi.spyOn(payees, 'create').mockResolvedValue(saved)
    const { user } = renderWritable()

    await user.click(await screen.findByRole('button', { name: t('payees.add') }))
    const form = await screen.findByRole('dialog', { name: t('payees.add') })
    await user.type(within(form).getByLabelText(t('payees.name')), 'Padaria Aurora')
    await user.click(within(form).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(create).toHaveBeenCalledOnce())
    expect(create.mock.calls[0][0]).toMatchObject({ name: 'Padaria Aurora' })
    // The form closes and the picker shows the payee the server returned,
    // even though the refetched list does not carry it yet.
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: t('payees.add') })).not.toBeInTheDocument(),
    )
    expect(await screen.findByRole('button', { name: /Padaria Aurora/ })).toBeInTheDocument()
  })

  it('opens the form on the payee already chosen, to fix its record', async () => {
    const update = vi.spyOn(payees, 'update').mockResolvedValue({ ...saved, name: 'Padaria Central' })
    const { user } = renderWritable([saved])

    await user.click(await screen.findByRole('button', { name: new RegExp(t('payees.noPayee')) }))
    await user.click(await screen.findByRole('option', { name: 'Padaria Aurora' }))
    await user.click(screen.getByRole('button', { name: t('payees.edit') }))

    const form = await screen.findByRole('dialog', { name: t('payees.edit') })
    const name = within(form).getByLabelText(t('payees.name'))
    expect(name).toHaveValue('Padaria Aurora')
    await user.clear(name)
    await user.type(name, 'Padaria Central')
    await user.click(within(form).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(update).toHaveBeenCalledOnce())
    expect(update.mock.calls[0][0]).toBe('payee-new')
    expect(await screen.findByRole('button', { name: /Padaria Central/ })).toBeInTheDocument()
  })

  it('hides both controls from members who cannot write', async () => {
    vi.spyOn(currencies, 'list').mockResolvedValue([])
    vi.spyOn(payees, 'list').mockResolvedValue([])
    vi.spyOn(groups, 'list').mockResolvedValue([])
    renderWithProviders(
      <WorkspaceContext.Provider
        value={{ canWrite: false } as unknown as React.ContextType<typeof WorkspaceContext>}
      >
        <TransactionDialog
          open
          onClose={vi.fn()}
          transaction={null}
          categories={[]}
          categoryGroups={[]}
          accounts={[]}
          onSave={vi.fn()}
          loading={false}
          error={null}
        />
      </WorkspaceContext.Provider>,
    )

    await screen.findByRole('button', { name: new RegExp(t('payees.noPayee')) })
    expect(screen.queryByRole('button', { name: t('payees.add') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('payees.edit') })).not.toBeInTheDocument()
  })
})
