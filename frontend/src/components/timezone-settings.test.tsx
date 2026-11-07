import { QueryClient } from '@tanstack/react-query'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import { admin } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

import { TimezoneSettings } from './timezone-settings'

vi.mock('@/lib/api', () => ({
  admin: { timezone: vi.fn(), updateSetting: vi.fn(), deleteSetting: vi.fn() },
}))

const settings = {
  timezone: 'Etc/UTC',
  saved: null,
  fallback: 'Etc/UTC',
  available: ['Etc/UTC', 'America/Sao_Paulo'],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.timezone).mockResolvedValue(settings)
  vi.mocked(admin.updateSetting).mockResolvedValue({ key: 'timezone', value: 'America/Sao_Paulo' })
  vi.mocked(admin.deleteSetting).mockResolvedValue(undefined)
})

it('lets administrators retry a failed timezone load', async () => {
  vi.mocked(admin.timezone).mockRejectedValueOnce(new Error('Offline'))

  const { user } = renderWithProviders(<TimezoneSettings />)

  await screen.findByRole('alert')
  await user.click(screen.getByRole('button', { name: 'Retry' }))
  // Nothing saved reads as the server default, named after where it lands.
  expect(await screen.findByLabelText('Application timezone')).toHaveTextContent('Server default (Etc/UTC)')
})

it('refreshes date-sensitive data without invalidating unrelated settings', async () => {
  // First load, then the refetch after saving reports the saved zone.
  vi.mocked(admin.timezone)
    .mockResolvedValueOnce(settings)
    .mockResolvedValue({ ...settings, timezone: 'America/Sao_Paulo', saved: 'America/Sao_Paulo' })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const affected = [
    ['accounts'],
    ['transactions'],
    ['recurring'],
    ['dashboard'],
    ['reports'],
    ['budgets'],
    ['goals'],
    ['assets'],
    ['asset-values'],
    ['asset-trend'],
    ['portfolio-trend'],
    ['fx-rates'],
    ['invoice', 'invoice-id'],
    ['invoices'],
    ['invoice-summary'],
    ['invoice-facets'],
    ['invoice-document', 'invoice-id'],
    ['reconciliation-suggestions'],
    ['timezones'],
    ['drill-down'],
  ]
  const unrelated = [
    ['admin', 'users'],
    ['admin', 'number-format'],
    ['categories'],
    ['connections', 'providers'],
  ]
  for (const key of [...affected, ...unrelated]) queryClient.setQueryData(key, [])

  const { user } = renderWithProviders(<TimezoneSettings />, { queryClient })
  const select = await screen.findByLabelText('Application timezone')
  expect(select).toHaveAccessibleDescription(/Calendar dates/)
  await user.click(select)
  await user.type(screen.getByPlaceholderText('Search timezone...'), 'sao paulo')
  await user.click(screen.getByRole('option', { name: /America\/Sao_Paulo/ }))
  await user.click(screen.getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(admin.updateSetting).toHaveBeenCalledWith('timezone', 'America/Sao_Paulo'))
  // The field showed the draft before Save was pressed, so that alone proves
  // nothing. Only a finished save clears the draft (Save goes back to
  // disabled) and only the refetch keeps the field on the saved zone.
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled())
  await waitFor(() => expect(admin.timezone).toHaveBeenCalledTimes(2))
  expect(select).toHaveTextContent('America/Sao_Paulo')
  await waitFor(() => {
    for (const key of affected) expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true)
  })
  for (const key of unrelated) expect(queryClient.getQueryState(key)?.isInvalidated).toBe(false)
  queryClient.clear()
})

it('forgets the saved timezone when the server default is chosen', async () => {
  // First load has a saved zone; the refetch after saving reports it gone.
  vi.mocked(admin.timezone)
    .mockResolvedValueOnce({ ...settings, timezone: 'America/Sao_Paulo', saved: 'America/Sao_Paulo' })
    .mockResolvedValue(settings)

  const { user } = renderWithProviders(<TimezoneSettings />)
  const select = await screen.findByLabelText('Application timezone')
  expect(select).toHaveTextContent('America/Sao_Paulo')

  await user.click(select)
  await user.click(screen.getByRole('option', { name: /Server default/ }))
  await user.click(screen.getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(admin.deleteSetting).toHaveBeenCalledWith('timezone'))
  expect(admin.updateSetting).not.toHaveBeenCalled()
  await waitFor(() => expect(select).toHaveTextContent('Server default (Etc/UTC)'))
})

it('warns when the saved value is not a timezone the server knows', async () => {
  vi.mocked(admin.timezone).mockResolvedValue({
    ...settings,
    timezone: 'Etc/UTC',
    saved: 'Mars/Olympus_Mons',
  })

  const { user } = renderWithProviders(<TimezoneSettings />)

  const warning = await screen.findByRole('alert')
  expect(warning).toHaveTextContent('Mars/Olympus_Mons')
  expect(warning).toHaveTextContent('Etc/UTC')
  // The picker shows the broken value as it is, so choosing the server
  // default is a real change that clears it.
  const select = screen.getByLabelText('Application timezone')
  expect(select).toHaveTextContent('Mars/Olympus_Mons')
  await user.click(select)
  await user.click(screen.getByRole('option', { name: /Server default/ }))
  await user.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(admin.deleteSetting).toHaveBeenCalledWith('timezone'))
})
