import { beforeEach, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { QueryClient } from '@tanstack/react-query'
import { renderWithProviders } from '@/test/utils'
import { admin } from '@/lib/api'
import { TimezoneSettings } from './timezone-settings'

vi.mock('@/lib/api', () => ({ admin: { timezone: vi.fn(), updateSetting: vi.fn() } }))
const settings = { timezone: 'UTC', available: ['UTC', 'America/Sao_Paulo'] }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.timezone).mockResolvedValue(settings)
  vi.mocked(admin.updateSetting).mockResolvedValue({ key: 'timezone', value: 'America/Sao_Paulo' })
})

it('lets administrators retry a failed timezone load', async () => {
  vi.mocked(admin.timezone).mockRejectedValueOnce(new Error('Offline'))
  const { user } = renderWithProviders(<TimezoneSettings />)
  await screen.findByRole('alert')
  await user.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByLabelText('Backend timezone')).toHaveValue('UTC')
})

it('refreshes date-sensitive data without invalidating unrelated settings', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const affected = [
    ['reports'],
    ['goals'],
    ['accounts'],
    ['transactions'],
    ['recurring'],
    ['invoice', 'invoice-id'],
    ['invoices'],
    ['invoice-summary'],
    ['invoice-facets'],
    ['invoice-document', 'invoice-id'],
  ]
  const unrelated = [['admin', 'users'], ['admin', 'number-format'], ['categories'], ['connections', 'providers']]
  for (const key of [...affected, ...unrelated]) queryClient.setQueryData(key, [])
  const { user } = renderWithProviders(<TimezoneSettings />, { queryClient })
  const select = await screen.findByLabelText('Backend timezone')
  expect(select).toHaveAccessibleDescription(/Calendar dates/)
  await user.selectOptions(select, 'America/Sao_Paulo')
  await user.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(admin.updateSetting).toHaveBeenCalledWith('timezone', 'America/Sao_Paulo'))
  await waitFor(() => {
    for (const key of affected) expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true)
  })
  for (const key of unrelated) expect(queryClient.getQueryState(key)?.isInvalidated).toBe(false)
  queryClient.clear()
})
