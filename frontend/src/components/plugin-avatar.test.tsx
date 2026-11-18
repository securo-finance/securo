import { fireEvent } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PluginAvatar } from '@/components/plugin-avatar'
import { renderWithProviders } from '@/test/utils'

const ICON = `/api/plugins/icons/${'a'.repeat(64)}`

describe('PluginAvatar', () => {
  it('draws the generated badge when the plugin has no icon', () => {
    const { container } = renderWithProviders(<PluginAvatar seed="maria/home" label="Home pets" />)
    expect(container.querySelector('img')).toBeNull()
    expect(container).toHaveTextContent('HP')
  })

  it("shows the icon Securo serves, not one from the plugin's site", () => {
    const { container } = renderWithProviders(<PluginAvatar seed="maria/home" label="Home" src={ICON} />)
    expect(container.querySelector('img')).toHaveAttribute('src', ICON)
  })

  it('falls back to the badge when the icon fails to load', () => {
    const { container } = renderWithProviders(<PluginAvatar seed="maria/home" label="Home pets" src={ICON} />)
    fireEvent.error(container.querySelector('img')!)
    expect(container.querySelector('img')).toBeNull()
    expect(container).toHaveTextContent('HP')
  })
})
