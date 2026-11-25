import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

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
      body: 'Hi, open {link} within {minutes} minutes.',
    },
    pt: {
      subject: 'Redefinir sua senha do {app_name}',
      body: 'Olá, abra {link} em {minutes} minutos.',
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
    expect(within(english).getByLabelText('Body')).toHaveValue('Hi, open {link} within {minutes} minutes.')

    const portuguese = within(region).getByRole('group', { name: 'Portuguese' })
    expect(within(portuguese).getByLabelText('Subject')).toHaveValue('Redefinir sua senha do {app_name}')
  })

  it('saves the edited template with the other language as it was', async () => {
    const saved: EmailState = {
      ...state,
      templates: { ...state.templates, en: { ...state.templates.en, subject: 'New password' } },
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
    // What was saved is what the fields show, and there is nothing left to save.
    await waitFor(() => expect(subject).toHaveValue('New password'))
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

  it('restores the built-in text by saving no template', async () => {
    const builtIn: EmailState = {
      ...state,
      templates: {
        en: { subject: 'Reset your {app_name} password', body: 'Open {link}.' },
        pt: { subject: 'Redefinir sua senha do {app_name}', body: 'Abra {link}.' },
      },
    }
    vi.mocked(admin.updateEmailTemplates).mockResolvedValue(builtIn)
    const { user } = renderWithProviders(<EmailSettings />)
    const region = await screen.findByRole('region', { name: 'Email' })
    const english = await within(region).findByRole('group', { name: 'English' })
    const subject = within(english).getByLabelText('Subject')

    await user.click(await within(region).findByRole('button', { name: 'Restore default text' }))

    await waitFor(() => expect(admin.updateEmailTemplates).toHaveBeenCalledWith({}))
    await waitFor(() => expect(subject).toHaveValue('Reset your {app_name} password'))
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
