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

/** Members who can write to the workspace see the payee controls.
 *
 * `listImpl` answers the payee refetches instead of `payeeList`, for a test
 * whose list has to change between the mount and a later fetch. */
function renderWritable(payeeList: Payee[] = [], listImpl?: () => Promise<Payee[]>) {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockImplementation(listImpl ?? (() => Promise.resolve(payeeList)))
  vi.spyOn(groups, 'list').mockResolvedValue([])
  vi.spyOn(fiscal, 'taxIdKinds').mockResolvedValue({
    jurisdiction: null,
    kinds: [],
    jurisdictions: [],
  })
  return renderWithProviders(
    <WorkspaceContext.Provider value={{ canWrite: true } as unknown as React.ContextType<typeof WorkspaceContext>}>
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
    await waitFor(() => expect(screen.queryByRole('dialog', { name: t('payees.add') })).not.toBeInTheDocument())
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

  // The saved copy is only a bridge over the gap before the refetched list
  // arrives. Once it does, it is the server's answer: another member may have
  // changed the record in between, and editing it has to show their change
  // instead of the copy this dialog saved.
  it('shows the refetched record, not the saved copy, when another member changed it', async () => {
    const refreshed: Payee = {
      ...saved,
      name: 'Padaria do Zé',
      phone: '+55 11 98888-7777',
    }
    vi.spyOn(payees, 'update').mockResolvedValue({ ...saved, name: 'Padaria Central' })
    let reads = 0
    const { user } = renderWritable([saved], async () => (reads++ === 0 ? [saved] : [refreshed]))

    await user.click(await screen.findByRole('button', { name: new RegExp(t('payees.noPayee')) }))
    await user.click(await screen.findByRole('option', { name: 'Padaria Aurora' }))
    await user.click(screen.getByRole('button', { name: t('payees.edit') }))
    const form = await screen.findByRole('dialog', { name: t('payees.edit') })
    const name = within(form).getByLabelText(t('payees.name'))
    await user.clear(name)
    await user.type(name, 'Padaria Central')
    await user.click(within(form).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: t('payees.edit') })).not.toBeInTheDocument())

    // The refetched row is another member's edit of the same record; once
    // it is on screen, reopening reads it, phone included.
    await screen.findByRole('button', { name: /Padaria do Zé/ })
    await user.click(screen.getByRole('button', { name: t('payees.edit') }))
    const reopened = await screen.findByRole('dialog', { name: t('payees.edit') })
    expect(within(reopened).getByLabelText(t('payees.name'))).toHaveValue('Padaria do Zé')
    expect(within(reopened).getByLabelText(t('payees.phone'))).toHaveValue('+55 11 98888-7777')
  })

  // The row the list holds is the baseline each save replaces. The record the
  // form was opened on can be an earlier save's copy, which the list never
  // carried: with that as the baseline, the still-stale row would read as
  // someone else's change and the picker would go back to the record before
  // the earlier save — showing values a later edit would then write back.
  it('keeps the latest save while the list still holds the row before it', async () => {
    const central = { ...saved, name: 'Padaria Central' }
    const ze = { ...saved, name: 'Padaria do Zé' }
    vi.spyOn(payees, 'update').mockResolvedValueOnce(central).mockResolvedValueOnce(ze)
    const { user } = renderWritable([saved])

    await user.click(await screen.findByRole('button', { name: new RegExp(t('payees.noPayee')) }))
    await user.click(await screen.findByRole('option', { name: 'Padaria Aurora' }))

    const edit = async (from: string, to: string) => {
      await user.click(screen.getByRole('button', { name: t('payees.edit') }))
      const form = await screen.findByRole('dialog', { name: t('payees.edit') })
      const name = within(form).getByLabelText(t('payees.name'))
      expect(name).toHaveValue(from)
      await user.clear(name)
      await user.type(name, to)
      await user.click(within(form).getByRole('button', { name: t('common.save') }))
      await waitFor(() => expect(screen.queryByRole('dialog', { name: t('payees.edit') })).not.toBeInTheDocument())
    }

    await edit('Padaria Aurora', 'Padaria Central')
    expect(await screen.findByRole('button', { name: /Padaria Central/ })).toBeInTheDocument()

    // The refetch from the first save has not landed yet: the list still
    // carries the row from before it.
    await edit('Padaria Central', 'Padaria do Zé')
    expect(await screen.findByRole('button', { name: /Padaria do Zé/ })).toBeInTheDocument()
  })

  it('hides both controls from members who cannot write', async () => {
    vi.spyOn(currencies, 'list').mockResolvedValue([])
    vi.spyOn(payees, 'list').mockResolvedValue([saved])
    vi.spyOn(groups, 'list').mockResolvedValue([])
    const { user } = renderWithProviders(
      <WorkspaceContext.Provider value={{ canWrite: false } as unknown as React.ContextType<typeof WorkspaceContext>}>
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

    // A payee has to be selected: with no selection the Edit check passes
    // even if its `canWrite` guard goes away, so the test would not catch
    // the regression it exists for.
    await user.click(await screen.findByRole('button', { name: new RegExp(t('payees.noPayee')) }))
    await user.click(await screen.findByRole('option', { name: 'Padaria Aurora' }))
    await screen.findByRole('button', { name: /Padaria Aurora/ })
    expect(screen.queryByRole('button', { name: t('payees.add') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('payees.edit') })).not.toBeInTheDocument()
  })
})
