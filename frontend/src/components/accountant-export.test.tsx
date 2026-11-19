import { screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithProviders, t } from '@/test/utils'
import { localDateString } from '@/lib/date-utils'

vi.mock('@/lib/api', () => ({ accountantExport: { download: vi.fn() } }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
// The workspace timezone comes from a query of its own; without one the
// component falls back to the browser's day. Mutable so a test can resolve it
// after the first render, the way the query does.
const timezone = vi.hoisted(() => ({ current: undefined as string | undefined }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => timezone.current }))

import { accountantExport } from '@/lib/api'

import { AccountantExportSection } from './accountant-export'

const download = vi.mocked(accountantExport.download)

describe('AccountantExportSection', () => {
  beforeEach(() => {
    download.mockReset()
    download.mockResolvedValue(undefined)
    timezone.current = undefined
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts on the current month and exports that range', async () => {
    const { user } = renderWithProviders(<AccountantExportSection />)
    const today = localDateString()

    expect(screen.getByText(t('accountantExport.title'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('accountantExport.startDate'))).toHaveValue(
      `${today.slice(0, 7)}-01`,
    )
    expect(screen.getByLabelText(t('accountantExport.endDate'))).toHaveValue(today)

    await user.click(screen.getByRole('button', { name: t('accountantExport.download') }))

    expect(download).toHaveBeenCalledWith(`${today.slice(0, 7)}-01`, today)
  })

  it('derives the default dates from the timezone once it resolves', () => {
    // Noon UTC is already March 1st in Kiritimati (UTC+14) and still
    // February 28th in Midway (UTC-11), so the two zones give different days
    // whatever the browser's own zone is. A period fixed on the browser's day
    // while the workspace zone was still loading would stay wrong afterwards.
    vi.useFakeTimers({
      now: new Date('2026-02-28T12:00:00Z'),
      shouldAdvanceTime: true,
    })
    timezone.current = 'Pacific/Kiritimati'
    const { rerender } = renderWithProviders(<AccountantExportSection />)
    const start = screen.getByLabelText(t('accountantExport.startDate'))
    const end = screen.getByLabelText(t('accountantExport.endDate'))
    expect(end).toHaveValue('2026-03-01')
    expect(start).toHaveValue('2026-03-01')

    // The workspace query answers: the defaults follow the zone it names.
    timezone.current = 'Pacific/Midway'
    rerender(<AccountantExportSection />)

    expect(end).toHaveValue('2026-02-28')
    expect(start).toHaveValue('2026-02-01')
  })

  it('sends the period the user picked', async () => {
    const { user } = renderWithProviders(<AccountantExportSection />)

    const start = screen.getByLabelText(t('accountantExport.startDate'))
    const end = screen.getByLabelText(t('accountantExport.endDate'))
    await user.clear(start)
    await user.type(start, '2026-01-01')
    await user.clear(end)
    await user.type(end, '2026-01-31')
    await user.click(screen.getByRole('button', { name: t('accountantExport.download') }))

    expect(download).toHaveBeenCalledWith('2026-01-01', '2026-01-31')
  })

  it('states the full-value note once, in the description', () => {
    // The note explains why the file does not match the app's split report.
    // Saying it again in the format hint made the card repeat itself, so the
    // hint keeps the file format only.
    renderWithProviders(<AccountantExportSection />)

    const description = screen.getByText(t('accountantExport.description'))
    const hint = screen.getByText(t('accountantExport.formatHint'))

    expect(description.textContent).toMatch(/full transaction value/i)
    expect(hint.textContent).not.toMatch(/full.*value|share/i)
  })

  it('refuses an incomplete period', async () => {
    const { user } = renderWithProviders(<AccountantExportSection />)

    await user.clear(screen.getByLabelText(t('accountantExport.endDate')))

    expect(screen.getByRole('button', { name: t('accountantExport.download') })).toBeDisabled()
    expect(download).not.toHaveBeenCalled()
  })
})
