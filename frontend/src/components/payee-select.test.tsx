import { useState, type ReactNode } from 'react'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithProviders, t } from '@/test/utils'
import { WorkspaceContext } from '@/contexts/workspace-context'
import type { Payee } from '@/types'

const { create } = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('@/lib/api', () => ({ payees: { create } }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { PayeeSelect } from './payee-select'

const acme = { id: 'payee-1', name: 'Acme Market' } as Payee
const bakery = { id: 'payee-2', name: 'Corner Bakery' } as Payee

function Workspace({ canWrite, children }: { canWrite: boolean; children: ReactNode }) {
  return (
    <WorkspaceContext.Provider
      value={{ canWrite } as unknown as React.ContextType<typeof WorkspaceContext>}
    >
      {children}
    </WorkspaceContext.Provider>
  )
}

// Holds the value like a real form does, so a test sees what the trigger
// shows after a change rather than only that a callback ran.
function Harness({ initial, onChange }: { initial: string; onChange: (v: string) => void }) {
  const [value, setValue] = useState(initial)
  return (
    <PayeeSelect
      value={value}
      onChange={(v) => { setValue(v); onChange(v) }}
      payees={[bakery, acme]}
      creatable
    />
  )
}

function renderSelect({ canWrite = true, value = '' } = {}) {
  const onChange = vi.fn()
  const utils = renderWithProviders(
    <Workspace canWrite={canWrite}>
      <Harness initial={value} onChange={onChange} />
    </Workspace>,
  )
  return { ...utils, onChange }
}

async function openAndType(user: ReturnType<typeof renderWithProviders>['user'], text: string) {
  await user.click(screen.getByRole('button'))
  await user.type(screen.getByPlaceholderText(t('payees.searchPlaceholder')), text)
}

describe('PayeeSelect', () => {
  beforeEach(() => {
    create.mockReset()
  })

  it('names the trigger after the selected payee', () => {
    renderSelect({ value: 'payee-2' })
    expect(screen.getByRole('button', { name: 'Corner Bakery' })).toBeInTheDocument()
  })

  it('names the trigger "no payee" when none is selected', () => {
    renderSelect()
    expect(screen.getByRole('button', { name: t('payees.noPayee') })).toBeInTheDocument()
  })

  it('filters payees by name and selects one', async () => {
    const { user, onChange } = renderSelect()

    await openAndType(user, 'acme')

    expect(screen.queryByText('Corner Bakery')).not.toBeInTheDocument()
    await user.click(screen.getByText('Acme Market'))
    expect(onChange).toHaveBeenCalledWith('payee-1')
  })

  it('creates the searched payee and selects it', async () => {
    create.mockResolvedValue({ id: 'payee-new', name: 'Dog Groomer' })
    const { user, onChange, queryClient } = renderSelect()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    await openAndType(user, 'Dog Groomer')
    await user.click(screen.getByText(t('common.createNamed', { name: 'Dog Groomer' })))

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('payee-new'))
    // The supplied list has not refetched, yet the trigger shows the new payee.
    expect(screen.getByRole('button', { name: 'Dog Groomer' })).toBeInTheDocument()
    expect(create).toHaveBeenCalledWith({ name: 'Dog Groomer' })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['payees'] })
  })

  it('does not offer to create an existing payee or to a viewer', async () => {
    const { user, unmount } = renderSelect()
    await openAndType(user, 'ACME MARKET')
    expect(screen.queryByText(t('common.createNamed', { name: 'ACME MARKET' }))).not.toBeInTheDocument()
    unmount()

    const viewer = renderSelect({ canWrite: false })
    await openAndType(viewer.user, 'Dog Groomer')
    expect(screen.queryByText(t('common.createNamed', { name: 'Dog Groomer' }))).not.toBeInTheDocument()
  })
})
