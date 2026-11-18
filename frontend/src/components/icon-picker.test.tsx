import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

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
    const { user } = renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)
    await user.type(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), 'health')
    expect(screen.getByTitle('Health / Wellbeing')).toBeInTheDocument()
  })

  it('finds icons by the translated label when searching (pt-BR)', async () => {
    await i18n.changeLanguage('pt-BR')
    const { user } = renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)
    await user.type(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), 'saúde')
    expect(screen.getByTitle('Saúde / Bem-estar')).toBeInTheDocument()
  })

  it('hides icons that do not match a current-language search', async () => {
    await i18n.changeLanguage('en')
    const { user } = renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)
    await user.type(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), 'saúde')
    expect(screen.queryByTitle('Health / Wellbeing')).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('common.noIconsFound'))).toBeInTheDocument()
  })

  it('offers emoji on their own tab, one per catalogue icon', async () => {
    await i18n.changeLanguage('en')
    const { user } = renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)

    await user.click(screen.getByRole('button', { name: i18n.t('common.iconPickerEmojis') }))

    expect(screen.getByTitle('Health / Wellbeing')).toHaveTextContent('❤️')
    // The Lucide grid is gone; the emoji tab replaced it.
    expect(document.querySelector('svg.lucide-heart')).toBeNull()
  })

  it('opens on the emoji tab when the saved icon is an emoji', async () => {
    await i18n.changeLanguage('en')
    renderWithProviders(<IconPicker value="🍔" color="" onChange={() => {}} />)

    expect(screen.getByRole('button', { name: i18n.t('common.iconPickerEmojis') })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('offers a typed emoji as a one-click choice and returns it whole', async () => {
    await i18n.changeLanguage('en')
    const onChange = vi.fn()
    renderWithProviders(<IconPicker value="" color="" onChange={onChange} />)

    fireEvent.change(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), {
      target: { value: '🦄' },
    })

    await screen.findByRole('button', { name: i18n.t('common.useEmoji') })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.useEmoji') }))

    expect(onChange).toHaveBeenCalledWith('🦄')
  })

  it('keeps a compound emoji in one piece when it is typed or pasted', async () => {
    await i18n.changeLanguage('en')
    const onChange = vi.fn()
    renderWithProviders(<IconPicker value="" color="" onChange={onChange} />)

    fireEvent.change(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), {
      target: { value: '👨‍👩‍👧‍👦' },
    })

    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.useEmoji') }))

    expect(onChange).toHaveBeenCalledWith('👨‍👩‍👧‍👦')
  })

  it('picks the emoji out of text pasted around it', async () => {
    await i18n.changeLanguage('en')
    const onChange = vi.fn()
    renderWithProviders(<IconPicker value="" color="" onChange={onChange} />)

    fireEvent.change(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), {
      target: { value: 'party 🎉' },
    })

    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.useEmoji') }))

    expect(onChange).toHaveBeenCalledWith('🎉')
  })

  it('finds emoji by an English keyword', async () => {
    await i18n.changeLanguage('en')
    const { user } = renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)

    await user.click(screen.getByRole('button', { name: i18n.t('common.iconPickerEmojis') }))
    await user.type(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), 'wine')

    expect(screen.getByTitle(i18n.t('categoryEmojis.wine'))).toHaveTextContent('🍷')
  })

  it('names the extra emoji in the active language', async () => {
    // A preset with no Lucide twin used to show its raw English keyword as
    // the tooltip in every language.
    await i18n.changeLanguage('pt-BR')
    const { user } = renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)

    await user.click(screen.getByRole('button', { name: i18n.t('common.iconPickerEmojis') }))

    expect(screen.getByTitle('Vinho')).toHaveTextContent('🍷')
    expect(screen.queryByTitle('wine')).not.toBeInTheDocument()
  })

  it('points to the emoji that matches a search from the icon tab', async () => {
    await i18n.changeLanguage('en')
    const { user } = renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)

    await user.type(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), 'wine')
    await user.click(screen.getByRole('button', { name: i18n.t('common.seeMatchingEmojis', { count: 1 }) }))

    expect(screen.getByRole('button', { name: i18n.t('common.iconPickerEmojis') })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByTitle(i18n.t('categoryEmojis.wine'))).toHaveTextContent('🍷')
  })

  it('finds emoji by the Portuguese label while the UI is in English', async () => {
    await i18n.changeLanguage('en')
    const { user } = renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)

    await user.click(screen.getByRole('button', { name: i18n.t('common.iconPickerEmojis') }))
    await user.type(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), 'seguro')

    expect(screen.getByTitle('Insurance')).toHaveTextContent('☂️')
  })

  it('says so when an emoji search matches nothing', async () => {
    await i18n.changeLanguage('en')
    const { user } = renderWithProviders(<IconPicker value="" color="" onChange={() => {}} />)

    await user.click(screen.getByRole('button', { name: i18n.t('common.iconPickerEmojis') }))
    await user.type(screen.getByPlaceholderText(i18n.t('common.searchIconOrEmoji')), 'zzzz')

    expect(screen.getByText(i18n.t('common.noEmojisFound'))).toBeInTheDocument()
  })
})
