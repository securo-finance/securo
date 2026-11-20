import { act, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithProviders, t } from '@/test/utils'
import type { Payee } from '@/types'

const { create, update, taxIdKinds } = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  taxIdKinds: vi.fn(),
}))
vi.mock('@/lib/api', () => ({
  payees: { create, update },
  fiscal: { taxIdKinds },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { PayeeFormDialog } from './payee-form-dialog'

const acme: Payee = {
  id: 'payee-1',
  user_id: null,
  name: 'Acme Market',
  type: 'company',
  source: 'manual',
  is_favorite: false,
  notes: 'Weekly groceries',
  email: 'fin@acme.test',
  phone: null,
  address: null,
  website: null,
  tax_ids: [],
  created_at: '2026-01-01T00:00:00Z',
  transaction_count: 3,
}

function renderForm(props: Partial<Parameters<typeof PayeeFormDialog>[0]> = {}) {
  const onOpenChange = vi.fn()
  const onSaved = vi.fn()
  const utils = renderWithProviders(
    <PayeeFormDialog
      open
      onOpenChange={onOpenChange}
      payee={null}
      onSaved={onSaved}
      {...props}
    />,
  )
  return { ...utils, onOpenChange, onSaved }
}

const nameInput = () => screen.getByLabelText(t('payees.name')) as HTMLInputElement

beforeEach(() => {
  vi.resetAllMocks()
  taxIdKinds.mockResolvedValue({ jurisdiction: null, kinds: [], jurisdictions: [] })
})

describe('PayeeFormDialog', () => {
  it('opens on the record it was asked to edit', () => {
    renderForm({ payee: acme })

    expect(nameInput()).toHaveValue('Acme Market')
    expect(screen.getByLabelText(t('payees.type'))).toHaveValue('company')
    expect(screen.getByLabelText(t('payees.notes'))).toHaveValue('Weekly groceries')
    expect(screen.getByLabelText(t('payees.email'))).toHaveValue('fin@acme.test')
    expect(screen.getByRole('heading', { name: t('payees.edit') })).toBeInTheDocument()
  })

  it('starts a new payee from the name already typed, so the form is not blank on open', async () => {
    const { user, onSaved } = renderForm({ initialName: 'Padaria Aurora' })
    expect(nameInput()).toHaveValue('Padaria Aurora')

    create.mockResolvedValue({ ...acme, id: 'payee-new', name: 'Padaria Aurora' })
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(create).toHaveBeenCalledOnce())
    expect(create.mock.calls[0][0]).toMatchObject({ name: 'Padaria Aurora' })
    await waitFor(() =>
      expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: 'payee-new' })),
    )
  })

  it('saves an edited record under its own id and hands the saved row back', async () => {
    const updated = { ...acme, name: 'Acme Market Lda' }
    update.mockResolvedValue(updated)
    const { user, onOpenChange, onSaved } = renderForm({ payee: acme })

    const input = nameInput()
    await user.clear(input)
    await user.type(input, 'Acme Market Lda')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(update).toHaveBeenCalledOnce())
    expect(update.mock.calls[0][0]).toBe('payee-1')
    expect(update.mock.calls[0][1]).toMatchObject({ name: 'Acme Market Lda', type: 'company' })
    expect(onOpenChange).toHaveBeenCalledWith(false)
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(updated))
  })

  it('leaves out the empty optional fields instead of sending blanks', async () => {
    create.mockResolvedValue({ ...acme, id: 'payee-new', name: 'Bar' })
    const { user } = renderForm()

    await user.type(nameInput(), 'Bar')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(create).toHaveBeenCalledOnce())
    expect(create.mock.calls[0][0]).toMatchObject({
      name: 'Bar',
      type: null,
      notes: undefined,
      email: null,
      phone: null,
      address: null,
      website: null,
      tax_ids: [],
    })
  })

  it('only offers Delete when the caller takes the confirmation', () => {
    const { unmount } = renderForm({ payee: acme })
    expect(screen.queryByRole('button', { name: t('common.delete') })).not.toBeInTheDocument()
    unmount()

    const onDelete = vi.fn()
    renderForm({ payee: acme, onDelete })
    screen.getByRole('button', { name: t('common.delete') }).click()
    expect(onDelete).toHaveBeenCalledWith(acme)
  })

  // The form stays mounted across records, so a save can outlive the record
  // it started on: the user cancels and opens another payee while the first
  // response is in flight. That completion must not close the newer form or
  // hand its record back as if it were the one on screen.
  it('leaves a form reopened for another payee alone when an earlier save lands',
    async () => {
      let resolveUpdate: (payee: Payee) => void = () => {}
      update.mockImplementation(() => new Promise<Payee>((resolve) => {
        resolveUpdate = resolve
      }))
      const { user, onOpenChange, onSaved, rerender } = renderForm({ payee: acme })

      await user.click(screen.getByRole('button', { name: t('common.save') }))
      await waitFor(() => expect(update).toHaveBeenCalledOnce())

      const bakery: Payee = { ...acme, id: 'payee-2', name: 'Corner Bakery', notes: null }
      rerender(
        <PayeeFormDialog
          open
          onOpenChange={onOpenChange}
          payee={bakery}
          onSaved={onSaved}
        />,
      )
      expect(nameInput()).toHaveValue('Corner Bakery')

      await act(async () => {
        resolveUpdate({ ...acme, name: 'Acme Market Lda' })
      })

      expect(onOpenChange).not.toHaveBeenCalledWith(false)
      expect(onSaved).not.toHaveBeenCalled()
      expect(nameInput()).toHaveValue('Corner Bakery')
    })

  it('reseeds when it is reopened on another payee', () => {
    const { rerender } = renderForm({ payee: acme })
    expect(nameInput()).toHaveValue('Acme Market')

    const bakery: Payee = { ...acme, id: 'payee-2', name: 'Corner Bakery', notes: null }
    rerender(
      <PayeeFormDialog
        open
        onOpenChange={vi.fn()}
        payee={bakery}
        onSaved={vi.fn()}
      />,
    )
    expect(nameInput()).toHaveValue('Corner Bakery')
    expect(screen.getByLabelText(t('payees.notes'))).toHaveValue('')
  })
})
