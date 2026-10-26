import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { OnboardingTour } from './onboarding-tour'
import { renderWithProviders, t } from '@/test/utils'

function addTarget(rect: Partial<DOMRect>) {
  const el = document.createElement('div')
  el.setAttribute('data-tour', 'sidebar')
  el.getBoundingClientRect = () =>
    ({ top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}), ...rect }) as DOMRect
  document.body.appendChild(el)
  return el
}

// The tour measures its target on a short timer, so wait for the card to be placed.
async function tooltip() {
  const title = await screen.findByText(t('onboarding.sidebar'))
  const card = title.closest<HTMLElement>('.pointer-events-auto')!
  await waitFor(() => expect(card.style.display).not.toBe('none'))
  return card
}

describe('OnboardingTour layout (#1036)', () => {
  afterEach(() => {
    document.querySelectorAll('[data-tour]').forEach((el) => el.remove())
    vi.unstubAllGlobals()
  })

  it('gives the card a fixed width and lets the footer wrap', async () => {
    addTarget({ top: 100, left: 0, right: 240, bottom: 140, width: 240, height: 40 })
    renderWithProviders(<OnboardingTour onComplete={vi.fn()} />)

    const card = await tooltip()
    expect(card.style.width).toBe('320px')
    const footer = screen.getByRole('button', { name: t('onboarding.next') }).parentElement!.parentElement!
    expect(footer).toHaveClass('flex-wrap')
  })

  it('stays inside a phone-width viewport even when the target is off-screen', async () => {
    vi.stubGlobal('innerWidth', 375)
    addTarget({ top: 100, left: -280, right: -20, bottom: 140, width: 260, height: 40 })
    renderWithProviders(<OnboardingTour onComplete={vi.fn()} />)

    const card = await tooltip()
    expect(card.style.width).toBe('320px')
    expect(parseFloat(card.style.left)).toBeGreaterThanOrEqual(8)
    expect(parseFloat(card.style.left) + 320).toBeLessThanOrEqual(375)
  })
})
