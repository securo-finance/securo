import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { IconPicker } from '@/components/icon-picker'
import { i18n, renderWithProviders } from '@/test/utils'

describe('IconPicker', () => {
  afterEach(() => {
    void i18n.changeLanguage('en')
  })

  it('shows icon tooltips in the active language (en)', async () => {
    await i18n.changeLanguage('en')
    renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)
    expect(screen.getByTitle('Health / Wellbeing')).toBeInTheDocument()
    expect(screen.queryByTitle('Saúde / Bem-estar')).not.toBeInTheDocument()
  })

  it('shows icon tooltips in the active language (pt-BR)', async () => {
    await i18n.changeLanguage('pt-BR')
    renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)
    expect(screen.getByTitle('Saúde / Bem-estar')).toBeInTheDocument()
    expect(screen.queryByTitle('Health / Wellbeing')).not.toBeInTheDocument()
  })

  it('finds icons by the translated label when searching (en)', async () => {
    await i18n.changeLanguage('en')
    const { user } = renderWithProviders(
      <IconPicker value="" color="" onChange={() => {}} />
    )
    await user.type(
      screen.getByPlaceholderText(i18n.t('common.searchIcon')),
      'health'
    )
    expect(screen.getByTitle('Health / Wellbeing')).toBeInTheDocument()
  })

  it('finds icons by the translated label when searching (pt-BR)', async () => {
    await i18n.changeLanguage('pt-BR')
    const { user } = renderWithProviders(
      <IconPicker value="" color="" onChange={() => {}} />
    )
    await user.type(
      screen.getByPlaceholderText(i18n.t('common.searchIcon')),
      'saúde'
    )
    expect(screen.getByTitle('Saúde / Bem-estar')).toBeInTheDocument()
  })

  it('hides icons that do not match a current-language search', async () => {
    await i18n.changeLanguage('en')
    const { user } = renderWithProviders(
      <IconPicker value="" color="" onChange={() => {}} />
    )
    await user.type(
      screen.getByPlaceholderText(i18n.t('common.searchIcon')),
      'saúde'
    )
    expect(screen.queryByTitle('Health / Wellbeing')).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('common.noIconsFound'))).toBeInTheDocument()
  })
})
