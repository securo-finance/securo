import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithProviders, t } from '@/test/utils'
import { localDateString } from '@/lib/date-utils'

vi.mock('@/lib/api', () => ({ accountantExport: { download: vi.fn() } }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
// The workspace timezone comes from a query of its own; without one the
// component falls back to the browser's day, which is what this test expects.
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => undefined }))

import { accountantExport } from '@/lib/api'

import { AccountantExportSection } from './accountant-export'

const download = vi.mocked(accountantExport.download)

describe('AccountantExportSection', () => {
  beforeEach(() => {
    download.mockReset()
    download.mockResolvedValue(undefined)
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

  it('refuses an incomplete period', async () => {
    const { user } = renderWithProviders(<AccountantExportSection />)

    await user.clear(screen.getByLabelText(t('accountantExport.endDate')))

    expect(screen.getByRole('button', { name: t('accountantExport.download') })).toBeDisabled()
    expect(download).not.toHaveBeenCalled()
  })
})
