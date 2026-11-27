import { waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import { useQuotaNotice } from './use-quota'

const mocks = vi.hoisted(() => ({
  mine: vi.fn(),
  toastError: vi.fn(),
  creator: 'me' as string | null,
}))
vi.mock('@/lib/api', () => ({ quotas: { mine: mocks.mine } }))
vi.mock('sonner', () => ({ toast: { error: mocks.toastError } }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { id: 'me' } }) }))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ current: { id: 'ws-1', created_by_user_id: mocks.creator } }),
}))

const refusal = (counter: string, maximum: number) => ({
  isAxiosError: true,
  response: { status: 403, data: { detail: { code: 'quota_exceeded', counter, current: maximum, maximum, message: 'x' } } },
})

type Notice = ReturnType<typeof useQuotaNotice>
let notice: Notice

function Probe({ onNotice }: { onNotice: (value: Notice) => void }) {
  const value = useQuotaNotice()
  useEffect(() => onNotice(value))
  return null
}

async function mount(infoUrl: string | null) {
  mocks.mine.mockResolvedValue({ enforced: true, info_url: infoUrl, counters: [] })
  renderWithProviders(<Probe onNotice={(value) => { notice = value }} />)
  await waitFor(() => expect(mocks.mine).toHaveBeenCalled())
  await waitFor(() => expect(notice.quotas).toBeDefined())
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.creator = 'me'
})

it('tells the account it holds the limit in a workspace it created', async () => {
  await mount(null)

  expect(notice.handle(refusal('max_bank_connections', 2))).toBe(true)

  expect(mocks.toastError).toHaveBeenCalledWith(
    t('quota.reached.max_bank_connections', { maximum: 2 }),
    undefined,
  )
})

it('tells an invited member the limit belongs to the workspace owner', async () => {
  mocks.creator = 'someone-else'
  await mount(null)

  notice.handle(refusal('max_members_per_workspace', 3))

  expect(mocks.toastError).toHaveBeenCalledWith(
    t('quota.ownerReached.max_members_per_workspace', { maximum: 3 }),
    undefined,
  )
})

it('links to the operator page only when the instance sets one', async () => {
  const open = vi.spyOn(window, 'open').mockImplementation(() => null)
  await mount('/help/limits')

  notice.handle(refusal('max_personal_workspaces', 1))

  const [, options] = mocks.toastError.mock.calls[0]
  expect(options.action.label).toBe(t('quota.learnMore'))
  options.action.onClick()
  expect(open).toHaveBeenCalledWith('/help/limits', '_blank', 'noopener,noreferrer')
})

it('leaves any other error to the caller', async () => {
  await mount(null)

  expect(notice.handle({ response: { status: 403, data: { detail: 'Not enough permissions' } } })).toBe(false)
  expect(mocks.toastError).not.toHaveBeenCalled()
})
