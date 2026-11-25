import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import { admin, type EmailState } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

import { EmailSettings } from './email-settings'

vi.mock('@/lib/api', () => ({
  admin: { emailState: vi.fn(), updateEmailTemplates: vi.fn() },
}))

const state: EmailState = {
  enabled: true,
  provider: 'smtp',
  sender: 'Securo <no-reply@example.com>',
  templates: {
    en: {
      subject: 'Reset your {app_name} password',
      body: 'Hi,\n\n{link}\n\nIt lasts {minutes} minutes.',
    },
    pt: {
      subject: 'Redefinir sua senha do {app_name}',
      body: 'Olá,\n\n{link}\n\nVale {minutes} minutos.',
    },
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.emailState).mockResolvedValue(state)
  vi.mocked(admin.updateEmailTemplates).mockResolvedValue(state)
})

describe('email settings', () => {
  it('shows the provider, the sender and the template of each language', async () => {
    renderWithProviders(<EmailSettings />)

    const region = await screen.findByRole('region', { name: 'Email' })
    expect(await within(region).findByText('Email is on')).toBeVisible()
    expect(within(region).getByText('SMTP relay')).toBeVisible()
    expect(within(region).getByText('Securo <no-reply@example.com>')).toBeVisible()

    const english = within(region).getByRole('group', { name: 'English' })
    expect(within(english).getByLabelText('Subject')).toHaveValue('Reset your {app_name} password')
    expect(within(english).getByLabelText('Body')).toHaveValue('Hi,\n\n{link}\n\nIt lasts {minutes} minutes.')

    const portuguese = within(region).getByRole('group', { name: 'Portuguese' })
    expect(within(portuguese).getByLabelText('Subject')).toHaveValue('Redefinir sua senha do {app_name}')
  })

  it('saves the edited template with the other language as it was', async () => {
    // The server answers something the draft did not say, so a field that
    // ignores the response keeps the typed text and the test says so.
    const saved: EmailState = {
      ...state,
      templates: { ...state.templates, en: { ...state.templates.en, subject: 'Password changed' } },
    }
    vi.mocked(admin.updateEmailTemplates).mockResolvedValue(saved)
    const { user } = renderWithProviders(<EmailSettings />)
    const region = await screen.findByRole('region', { name: 'Email' })
    const english = await within(region).findByRole('group', { name: 'English' })

    const subject = within(english).getByLabelText('Subject')
    expect(within(region).getByRole('button', { name: 'Save templates' })).toBeDisabled()
    await user.clear(subject)
    await user.type(subject, 'New password')

    await user.click(within(region).getByRole('button', { name: 'Save templates' }))

    await waitFor(() =>
      expect(admin.updateEmailTemplates).toHaveBeenCalledWith({
        en: { ...state.templates.en, subject: 'New password' },
        pt: state.templates.pt,
      }),
    )
    // What the server saved is what the fields show, and there is nothing
    // left to save once it answered.
    await waitFor(() => expect(subject).toHaveValue('Password changed'))
    expect(within(region).getByRole('button', { name: 'Save templates' })).toBeDisabled()
  })

  it('refuses a body without the link and says why', async () => {
    const { user } = renderWithProviders(<EmailSettings />)
    const region = await screen.findByRole('region', { name: 'Email' })
    const english = await within(region).findByRole('group', { name: 'English' })

    const body = within(english).getByLabelText('Body')
    await user.clear(body)
    await user.type(body, 'Open the app.')

    expect(within(english).getByRole('alert')).toHaveTextContent('must contain {link}')
    expect(within(region).getByRole('button', { name: 'Save templates' })).toBeDisabled()
    expect(admin.updateEmailTemplates).not.toHaveBeenCalled()
  })

  it('refuses a body that wraps the link in another address and says why', async () => {
    renderWithProviders(<EmailSettings />)
    const region = await screen.findByRole('region', { name: 'Email' })
    const english = await within(region).findByRole('group', { name: 'English' })

    // Typed straight in, because user-event reads `{` as the start of a key
    // name and cannot write the placeholder unchanged.
    fireEvent.change(within(english).getByLabelText('Body'), {
      target: { value: 'https://evil.example/c?u={link}' },
    })

    expect(within(english).getByRole('alert')).toHaveTextContent('alone on its own line')
    expect(within(region).getByRole('button', { name: 'Save templates' })).toBeDisabled()
    expect(admin.updateEmailTemplates).not.toHaveBeenCalled()
  })

  it('saves a body whose braces are the escaped literal, not a second field', async () => {
    renderWithProviders(<EmailSettings />)
    const region = await screen.findByRole('region', { name: 'Email' })
    const english = await within(region).findByRole('group', { name: 'English' })

    // `{{link}}` is the literal text `{link}` the server's formatter leaves
    // alone, so it is not a second field.
    fireEvent.change(within(english).getByLabelText('Body'), {
      target: { value: 'Example: {{link}}\n\n{link}' },
    })

    expect(within(english).queryByRole('alert')).not.toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Save templates' })).toBeEnabled()
  })

  it('refuses a body that builds an address out of the values', async () => {
    renderWithProviders(<EmailSettings />)
    const region = await screen.findByRole('region', { name: 'Email' })
    const english = await within(region).findByRole('group', { name: 'English' })

    // No address in the template; `reset60.com` is one once {minutes} is filled.
    fireEvent.change(within(english).getByLabelText('Body'), {
      target: { value: 'Open:\n\n{link}\n\nHelp: reset{minutes}.com' },
    })

    expect(within(english).getByRole('alert')).toHaveTextContent('cannot lead anywhere but')
    expect(within(region).getByRole('button', { name: 'Save templates' })).toBeDisabled()
    expect(admin.updateEmailTemplates).not.toHaveBeenCalled()
  })

  it('refuses an address typed into the body beside the placeholder', async () => {
    renderWithProviders(<EmailSettings />)
    const region = await screen.findByRole('region', { name: 'Email' })
    const english = await within(region).findByRole('group', { name: 'English' })

    // The address the check itself puts in place of {link}: written out by
    // hand it is a second link the reader is sent to, so it is not the
    // placeholder and must be refused.
    fireEvent.change(within(english).getByLabelText('Body'), {
      target: { value: '{link}\n\nhttps://securo.example/reset-password?token=check' },
    })

    expect(within(english).getByRole('alert')).toHaveTextContent('cannot lead anywhere but')
    expect(within(region).getByRole('button', { name: 'Save templates' })).toBeDisabled()
    expect(admin.updateEmailTemplates).not.toHaveBeenCalled()
  })

  it('restores the built-in text by saving no template', async () => {
    const builtIn: EmailState = {
      ...state,
      templates: {
        en: { subject: 'Reset your {app_name} password', body: 'Open the app:\n\n{link}' },
        pt: { subject: 'Redefinir sua senha do {app_name}', body: 'Abra o app:\n\n{link}' },
      },
    }
    vi.mocked(admin.updateEmailTemplates).mockResolvedValue(builtIn)
    const { user } = renderWithProviders(<EmailSettings />)
    const region = await screen.findByRole('region', { name: 'Email' })
    const english = await within(region).findByRole('group', { name: 'English' })
    const body = within(english).getByLabelText('Body')
    // Built-in text the saved template did not have, so the field turning
    // into it proves the response was applied.
    expect(body).toHaveValue('Hi,\n\n{link}\n\nIt lasts {minutes} minutes.')

    await user.click(await within(region).findByRole('button', { name: 'Restore default text' }))

    await waitFor(() => expect(admin.updateEmailTemplates).toHaveBeenCalledWith({}))
    await waitFor(() => expect(body).toHaveValue('Open the app:\n\n{link}'))
    expect(within(region).getByRole('button', { name: 'Save templates' })).toBeDisabled()
  })

  it('drops an unsaved edit when the restore brings back the text already loaded', async () => {
    const { user } = renderWithProviders(<EmailSettings />)
    const region = await screen.findByRole('region', { name: 'Email' })
    const english = await within(region).findByRole('group', { name: 'English' })
    const subject = within(english).getByLabelText('Subject')

    await user.clear(subject)
    await user.type(subject, 'Half written')
    expect(subject).toHaveValue('Half written')

    await user.click(within(region).getByRole('button', { name: 'Restore default text' }))

    await waitFor(() => expect(subject).toHaveValue(state.templates.en.subject))
    expect(within(region).getByRole('button', { name: 'Save templates' })).toBeDisabled()
  })

  it('says when email is off and where the switch lives', async () => {
    vi.mocked(admin.emailState).mockResolvedValue({ ...state, enabled: false })
    renderWithProviders(<EmailSettings />)

    const region = await screen.findByRole('region', { name: 'Email' })
    expect(await within(region).findByText('Email is off')).toBeVisible()
    expect(within(region).getByRole('status')).toHaveTextContent('EMAIL_ENABLED')
  })

  it('names the provider in use when it is the hosted API', async () => {
    vi.mocked(admin.emailState).mockResolvedValue({ ...state, provider: 'resend' })
    renderWithProviders(<EmailSettings />)

    const region = await screen.findByRole('region', { name: 'Email' })
    expect(await within(region).findByText('Resend')).toBeVisible()
  })
})
