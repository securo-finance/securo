import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { renderWithProviders } from '@/test/utils'

const directory = vi.hoisted(() => vi.fn())

vi.mock('@/lib/api', () => ({
  users: {
    directory,
    lookupByEmail: vi.fn().mockResolvedValue(null),
  },
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: 'user-viewer' } }),
}))

import { MemberForm } from './member-form'

function Harness() {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [linkedUserId, setLinkedUserId] = useState<string | null>(null)
  return (
    <MemberForm
      name={name}
      onChangeName={setName}
      email={email}
      onChangeEmail={setEmail}
      linkedUserId={linkedUserId}
      onChangeLinkedUserId={setLinkedUserId}
    />
  )
}

/** The form renders the name input first and the email input after it. */
function nameInput(container: HTMLElement) {
  return container.querySelectorAll('input')[0] as HTMLInputElement
}

async function pickLinkedUser(
  container: HTMLElement,
  user: ReturnType<typeof renderWithProviders>['user'],
  userId: string,
) {
  const select = await vi.waitFor(() => {
    const el = container.querySelector('select') as HTMLSelectElement
    if (el.options.length < 2) throw new Error('directory not loaded yet')
    return el
  })
  await user.selectOptions(select, userId)
}

describe('MemberForm', () => {
  it('fills the name with the linked user display name, not the email prefix', async () => {
    directory.mockResolvedValue([{ id: 'user-1', email: 'user@mydomain.org', display_name: 'User Name' }])
    const { user, container } = renderWithProviders(<Harness />)

    await pickLinkedUser(container, user, 'user-1')

    expect(nameInput(container).value).toBe('User Name')
  })

  it('falls back to the email prefix when the linked user has no display name', async () => {
    directory.mockResolvedValue([{ id: 'user-2', email: 'user@mydomain.org', display_name: null }])
    const { user, container } = renderWithProviders(<Harness />)

    await pickLinkedUser(container, user, 'user-2')

    expect(nameInput(container).value).toBe('user')
  })

  it('keeps showing a linked user who is outside the directory', async () => {
    directory.mockResolvedValue([{ id: 'user-viewer', email: 'me@mydomain.org', display_name: 'Me' }])
    const { container } = renderWithProviders(
      <MemberForm
        name="Outsider"
        onChangeName={vi.fn()}
        email="outsider@elsewhere.org"
        onChangeEmail={vi.fn()}
        linkedUserId="user-outsider"
        onChangeLinkedUserId={vi.fn()}
      />,
    )

    const select = await vi.waitFor(() => {
      const el = container.querySelector('select') as HTMLSelectElement
      if (!el.querySelector('option[value="user-viewer"]')) {
        throw new Error('directory not loaded yet')
      }
      return el
    })

    expect(select.value).toBe('user-outsider')
    expect(select.selectedOptions[0].textContent).toBe('outsider@elsewhere.org')
  })

  // A screen reader announces a field by the label tied to it (issue #282).
  it('ties each field to its label', () => {
    directory.mockResolvedValue([])
    const { getByLabelText } = renderWithProviders(<Harness />)

    expect(getByLabelText('Name').tagName).toBe('INPUT')
    expect(getByLabelText('Email').tagName).toBe('INPUT')
    expect(getByLabelText('Linked Securo user').tagName).toBe('SELECT')
  })
})
