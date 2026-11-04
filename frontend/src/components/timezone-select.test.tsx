import { useState } from 'react'
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { renderWithProviders } from '@/test/utils'

import { timezoneCityLabel, timezoneOffsetLabel } from '@/lib/timezone-utils'

import { TimezoneSelect } from './timezone-select'

const options = ['America/Sao_Paulo', 'America/New_York', 'Asia/Tokyo', 'Europe/Paris', 'UTC']

function renderSelect(initial = '', emptyOption?: string) {
  const onChange = vi.fn()
  function Harness() {
    const [value, setValue] = useState(initial)
    return (
      <TimezoneSelect
        id="tz"
        value={value}
        onChange={(v) => { setValue(v); onChange(v) }}
        options={options}
        emptyOption={emptyOption}
      />
    )
  }
  return { ...renderWithProviders(<Harness />), onChange }
}

describe('timezone labels', () => {
  it('reads the city out of the IANA name', () => {
    expect(timezoneCityLabel('America/Sao_Paulo')).toBe('Sao Paulo')
    expect(timezoneCityLabel('America/Argentina/Buenos_Aires')).toBe('Argentina/Buenos Aires')
    expect(timezoneCityLabel('UTC')).toBe('UTC')
  })

  it('gives the current offset, or nothing for a name the runtime does not know', () => {
    const winter = new Date('2026-01-15T12:00:00Z')
    expect(timezoneOffsetLabel('America/Sao_Paulo', winter)).toBe('GMT-3')
    expect(timezoneOffsetLabel('Asia/Kolkata', winter)).toBe('GMT+5:30')
    expect(timezoneOffsetLabel('UTC', winter)).toBe('GMT')
    expect(timezoneOffsetLabel('Mars/Olympus_Mons', winter)).toBeNull()
  })
})

describe('TimezoneSelect', () => {
  it('finds a zone by city, by IANA name or by offset', async () => {
    const { user, onChange } = renderSelect()
    await user.click(screen.getByRole('combobox'))
    const search = screen.getByPlaceholderText('Search timezone...')

    await user.type(search, 'sao paulo')
    expect(screen.getAllByRole('option')).toHaveLength(1)
    await user.clear(search)
    await user.type(search, 'asia/to')
    expect(screen.getAllByRole('option')).toHaveLength(1)
    await user.clear(search)
    await user.type(search, 'gmt-3')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      expect.stringContaining('America/Sao_Paulo'),
    ])

    await user.click(screen.getByRole('option', { name: /America\/Sao_Paulo/ }))
    expect(onChange).toHaveBeenCalledWith('America/Sao_Paulo')
    expect(screen.getByRole('combobox')).toHaveTextContent('America/Sao_Paulo')
  })

  it('groups zones by region', async () => {
    const { user } = renderSelect('Asia/Tokyo')
    await user.click(screen.getByRole('combobox'))
    const list = screen.getByRole('listbox')
    expect(within(list).getByText('America')).toBeInTheDocument()
    expect(within(list).getByText('Europe')).toBeInTheDocument()
    // UTC has no region, so it is listed without a heading, after the regions.
    const texts = within(list).getAllByRole('option').map((o) => o.textContent ?? '')
    expect(texts[texts.length - 1]).toContain('UTC')
  })

  it('offers the empty choice first when one is given', async () => {
    const { user, onChange } = renderSelect('Asia/Tokyo', 'Server default (UTC)')
    await user.click(screen.getByRole('combobox'))
    const [first] = screen.getAllByRole('option')
    expect(first).toHaveTextContent('Server default (UTC)')
    await user.click(first)
    expect(onChange).toHaveBeenCalledWith('')
    expect(screen.getByRole('combobox')).toHaveTextContent('Server default (UTC)')
  })
})
