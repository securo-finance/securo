import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { DateRangePicker } from '@/components/ui/date-range-picker'
import { renderWithProviders, t } from '@/test/utils'

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

// The segment variant's active label drops the year — same compact form the
// transactions filter bar's applied-range chip uses.
function fmtCompact(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', {
    day: '2-digit',
    month: 'short',
  })
}

describe('DateRangePicker — segment variant', () => {
  it('shows the plain label while inactive, even if a range is already stored', () => {
    renderWithProviders(
      <DateRangePicker
        variant="segment"
        active={false}
        from="2026-02-01"
        to="2026-02-10"
        onChange={vi.fn()}
        label="Custom"
      />,
    )

    // The picked dates only surface once the segment is the active range —
    // otherwise it reads exactly like the other preset segments beside it.
    expect(screen.getByRole('button', { name: 'Custom' })).toHaveTextContent('Custom')
  })

  it('shows the compact, year-less range once active', () => {
    renderWithProviders(
      <DateRangePicker
        variant="segment"
        active
        from="2026-03-01"
        to="2026-03-10"
        onChange={vi.fn()}
        label="Custom"
      />,
    )

    // aria-label stays the fixed "Custom" (same as every other preset
    // segment); it's the visible text that swaps to the picked dates, in
    // the same compact (no-year) form as the transactions filter bar's
    // applied-range chip.
    const trigger = screen.getByRole('button', { name: 'Custom' })
    expect(trigger).toHaveTextContent(`${fmtCompact('2026-03-01')} — ${fmtCompact('2026-03-10')}`)
    expect(trigger).not.toHaveTextContent('2026')
  })

  it('keeps the compact form even when the range crosses a year boundary', () => {
    renderWithProviders(
      <DateRangePicker
        variant="segment"
        active
        from="2025-09-26"
        to="2026-01-30"
        onChange={vi.fn()}
        label="Custom"
      />,
    )

    // Crossing a year boundary is exactly the case a year-inclusive format
    // would need to disambiguate — the segment still drops it, since the
    // year is visible while the calendar itself is open.
    expect(screen.getByRole('button', { name: 'Custom' })).toHaveTextContent(
      `${fmtCompact('2025-09-26')} — ${fmtCompact('2026-01-30')}`,
    )
  })

  it('seeds drafts on open and commits defaults only on Apply', async () => {
    const onOpen = vi.fn()
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker
        variant="segment"
        active={false}
        from=""
        to=""
        defaultFrom="2026-01-01"
        defaultTo="2026-12-31"
        onOpen={onOpen}
        onChange={onChange}
        label="Custom"
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Custom' }))

    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onChange).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith('2026-01-01', '2026-12-31')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('does not overwrite an already-picked range when reopened', async () => {
    const onOpen = vi.fn()
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker
        variant="segment"
        active
        from="2026-02-01"
        to="2026-02-10"
        defaultFrom="2026-01-01"
        defaultTo="2026-12-31"
        onOpen={onOpen}
        onChange={onChange}
        label="Custom"
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Custom' }))

    expect(onOpen).toHaveBeenCalledTimes(1)
    // A range is already set, so the default seed must not clobber it.
    expect(onChange).not.toHaveBeenCalled()
    expect(await screen.findByText(t('transactions.filtersBar.fromLabel'))).toBeInTheDocument()
  })
})

describe('DateRangePicker — standalone button variant (regression)', () => {
  it('still renders as a bordered button with a placeholder when unset', () => {
    renderWithProviders(
      <DateRangePicker
        from=""
        to=""
        onChange={vi.fn()}
        label="Custom range"
        placeholder="Pick a range"
      />,
    )

    const trigger = screen.getByRole('button', { name: 'Custom range' })
    expect(trigger).toHaveTextContent('Pick a range')
  })

  it('confirms a picked range through Apply', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker
        from="2026-05-01"
        to="2026-05-15"
        onChange={onChange}
        label="Custom range"
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Custom range' }))
    await user.click(await screen.findByRole('button', { name: t('transactions.filtersBar.apply') }))

    expect(onChange).toHaveBeenCalledWith('2026-05-01', '2026-05-15')
  })
})


describe('DateRangePicker — draft lifecycle', () => {
  it('keeps calendar selections in draft state until Apply', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker from="2026-05-01" to="2026-05-15"
        onChange={onChange} label="Custom" />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    // getByRole's `name` matches the whole accessible name exactly by
    // default, so no `exact` option is needed (and this type doesn't have one).
    await user.click(screen.getAllByRole('button', { name: '12' })[0])
    await user.click(screen.getAllByRole('button', { name: '8' })[1])
    expect(onChange).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith('2026-05-08', '2026-05-12')
  })

  it.each(['Cancel', 'Escape', 'outside'] as const)(
    '%s discards edits and restores committed dates on reopening', async (dismiss) => {
      const onChange = vi.fn()
      const { user } = renderWithProviders(
        <DateRangePicker from="2026-05-01" to="2026-05-15"
          onChange={onChange} label="Custom" />,
      )
      const trigger = screen.getByRole('button', { name: 'Custom' })
      await user.click(trigger)
      await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
      if (dismiss === 'Cancel') {
        await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.cancel') }))
      } else if (dismiss === 'Escape') {
        await user.keyboard('{Escape}')
      } else {
        await user.click(document.body)
      }
      expect(onChange).not.toHaveBeenCalled()
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      await user.click(trigger)
      await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
      expect(onChange).toHaveBeenCalledExactlyOnceWith('2026-05-01', '2026-05-15')
    },
  )

  it('Reset only clears drafts; Apply commits the empty range', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker from="2026-05-01" to="2026-05-15"
        onChange={onChange} label="Custom" />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
    expect(onChange).not.toHaveBeenCalled()
    const apply = screen.getByRole('button', { name: t('transactions.filtersBar.apply') })
    expect(apply).toBeEnabled()
    await user.click(apply)
    expect(onChange).toHaveBeenCalledExactlyOnceWith('', '')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it.each([
    ['2026-05-15', '2026-05-01', '2026-05-01', '2026-05-15'],
    ['2026-05-15', '', '2026-05-15', '2026-05-15'],
    ['', '2026-05-15', '2026-05-15', '2026-05-15'],
  ])('normalizes %s → %s on Apply', async (from, to, expectedFrom, expectedTo) => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker from={from} to={to} onChange={onChange} label="Custom" />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    expect(onChange).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(expectedFrom, expectedTo)
  })
})

describe('DateRangePicker — draft validation', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 5, 15)) // June 15, 2026
  })
  afterEach(() => vi.useRealTimers())

  it('rejects a range ending after today and disables Apply', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker from="2026-06-01" to="2026-06-16"
        onChange={onChange} label="Custom" disallowFuture />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    expect(screen.getByText(t('transactions.filtersBar.futureDateError'))).toBeInTheDocument()
    const apply = screen.getByRole('button', { name: t('transactions.filtersBar.apply') })
    expect(apply).toBeDisabled()
    await user.click(apply)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('allows a range ending exactly today', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker from="2026-06-01" to="2026-06-15"
        onChange={onChange} label="Custom" disallowFuture />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    expect(screen.queryByText(t('transactions.filtersBar.futureDateError'))).not.toBeInTheDocument()
    const apply = screen.getByRole('button', { name: t('transactions.filtersBar.apply') })
    expect(apply).toBeEnabled()
    await user.click(apply)
    expect(onChange).toHaveBeenCalledExactlyOnceWith('2026-06-01', '2026-06-15')
  })

  it('accepts the inclusive maxRangeDays boundary', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker from="2026-01-01" to="2026-01-10"
        onChange={onChange} label="Custom" maxRangeDays={10} />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    expect(
      screen.queryByText(t('transactions.filtersBar.rangeTooWideError', { days: 10 })),
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith('2026-01-01', '2026-01-10')
  })

  it('rejects a range one day past maxRangeDays and disables Apply', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker from="2026-01-01" to="2026-01-11"
        onChange={onChange} label="Custom" maxRangeDays={10} />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    expect(
      screen.getByText(t('transactions.filtersBar.rangeTooWideError', { days: 10 })),
    ).toBeInTheDocument()
    const apply = screen.getByRole('button', { name: t('transactions.filtersBar.apply') })
    expect(apply).toBeDisabled()
    await user.click(apply)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('computes the day span across a daylight-saving transition without an off-by-one', async () => {
    const onChange = vi.fn()
    // 2026-03-08 is the US spring-forward transition; a millisecond-based
    // 24h-per-day division would lose an hour here and risks miscounting
    // the span by a day depending on the viewer's timezone.
    const { user } = renderWithProviders(
      <DateRangePicker from="2026-03-07" to="2026-03-09"
        onChange={onChange} label="Custom" maxRangeDays={3} />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    expect(
      screen.queryByText(t('transactions.filtersBar.rangeTooWideError', { days: 3 })),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('transactions.filtersBar.apply') })).toBeEnabled()
  })

  it('normalizes a reversed draft before validating and applying', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker from="2026-06-10" to="2026-06-01"
        onChange={onChange} label="Custom" disallowFuture maxRangeDays={30} />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    const apply = screen.getByRole('button', { name: t('transactions.filtersBar.apply') })
    expect(apply).toBeEnabled()
    await user.click(apply)
    expect(onChange).toHaveBeenCalledExactlyOnceWith('2026-06-01', '2026-06-10')
  })

  it('keeps an empty draft applicable for clearing even with validation props set', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <DateRangePicker from="2026-06-01" to="2026-06-10"
        onChange={onChange} label="Custom" disallowFuture maxRangeDays={30} />,
    )
    await user.click(screen.getByRole('button', { name: 'Custom' }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
    const apply = screen.getByRole('button', { name: t('transactions.filtersBar.apply') })
    expect(apply).toBeEnabled()
    await user.click(apply)
    expect(onChange).toHaveBeenCalledExactlyOnceWith('', '')
  })
})
