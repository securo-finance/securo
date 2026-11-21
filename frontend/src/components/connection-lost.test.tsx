import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, screen } from '@testing-library/react'

import { ConnectionLost } from '@/components/connection-lost'
import { i18n, renderWithProviders } from '@/test/utils'

afterEach(async () => {
  await act(async () => {
    await i18n.changeLanguage('en')
  })
})

describe('ConnectionLost', () => {
  it('says the data cannot be loaded, in English', () => {
    renderWithProviders(<ConnectionLost onRetry={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent("Can't reach the server")
    expect(screen.getByRole('status')).toHaveTextContent("You're offline or Securo is restarting.")
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('is translated to pt-BR', async () => {
    await act(async () => {
      await i18n.changeLanguage('pt-BR')
    })

    renderWithProviders(<ConnectionLost onRetry={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent('Não foi possível falar com o servidor')
    expect(screen.getByRole('button')).toBeInTheDocument()
  })

  it('calls the retry handler', async () => {
    const onRetry = vi.fn()
    const { user } = renderWithProviders(<ConnectionLost onRetry={onRetry} />)

    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
