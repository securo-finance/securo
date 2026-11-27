import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithProviders, t } from '@/test/utils'
import { goal } from '@/test/goal-fixtures'
import GoalsPage from '@/pages/goals'

const mocks = vi.hoisted(() => ({
  goals: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  accounts: { list: vi.fn() },
  assets: { list: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
  history: vi.fn(),
  writable: true,
  privacy: false,
}))
vi.mock('@/lib/api', () => mocks)
vi.mock('@/lib/goal-history', () => ({ loadGoalHistory: mocks.history }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({
    canWrite: mocks.writable,
    current: { id: 'ws-1', timezone: 'America/New_York' },
  }),
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({
    mask: (value: string) => (mocks.privacy ? '••••' : value),
    privacyMode: mocks.privacy,
  }),
}))
const today = '2026-11-21'
/**
 * Builds one goal for each tracking scenario exercised by the chart tests.
 *
 * @returns {import('@/types').Goal[]} Goal fixtures covering each tracking scenario.
 */
const fixtures = () => [
  goal(
    {
      id: 'server',
      name: 'Server Fund',
      tracking_type: 'account',
      account_id: 'server-account',
      monthly_contribution: 471.43,
    },
    today,
  ),
  goal(
    {
      id: 'house',
      name: 'House Fund',
      tracking_type: 'account',
      account_id: 'house-account',
      target_date: null,
    },
    today,
  ),
  goal({ id: 'studio', name: 'Studio Upgrade', tracking_type: 'asset', asset_id: 'studio' }, today),
  goal(
    {
      id: 'wallet',
      name: 'Retirement Wallet',
      tracking_type: 'asset_group',
      asset_group_id: 'wallet',
    },
    today,
  ),
  goal({ id: 'net-worth', name: 'Net Worth Milestone', tracking_type: 'net_worth' }, today),
  goal({ id: 'manual', name: 'Holiday Fund', tracking_type: 'manual' }, today),
]
beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(today + 'T18:00:00Z'))
  mocks.writable = true
  mocks.privacy = false
  mocks.goals.list.mockResolvedValue(fixtures())
  for (const api of [mocks.accounts, mocks.assets, mocks.assetGroups, mocks.currencies]) {
    api.list.mockResolvedValue([])
  }
  mocks.history.mockResolvedValue({
    points: [
      { date: '2026-03-01', amount: 500 },
      { date: today, amount: 2700 },
    ],
    notes: [],
  })
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

it('keeps native stats and shows charts only for nonmanual goals', async () => {
  const { container } = renderWithProviders(<GoalsPage />)
  const server = within(await screen.findByRole('region', { name: 'Progress for Server Fund' }))
  await waitFor(() =>
    expect(screen.getAllByRole('img', { name: /history and projection in USD/ })).toHaveLength(4),
  )
  expect(screen.getByText('Holiday Fund')).toBeVisible()
  expect(
    screen.queryByRole('region', { name: 'Progress for Holiday Fund' }),
  ).not.toBeInTheDocument()
  expect(screen.getByText('$471.43/mo')).toBeVisible()
  expect(server.queryByRole('combobox')).not.toBeInTheDocument()
  expect(server.queryByText('$471.43')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Save plan' })).not.toBeInTheDocument()
  expect(container.querySelector('path[stroke-dasharray="6 5"]')).toBeTruthy()
  expect(server.getByText('6.6K')).toBeVisible()
  expect(mocks.goals.update).not.toHaveBeenCalled()
})

it('starts undated goals collapsed and loads only history when expanded', async () => {
  mocks.goals.list.mockResolvedValue([{ ...fixtures()[1], monthly_contribution: 100 }])
  const { user } = renderWithProviders(<GoalsPage />)
  const house = await screen.findByRole('region', { name: 'Progress for House Fund' })
  const toggle = within(house).getByRole('button')

  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(within(house).queryByRole('img')).not.toBeInTheDocument()
  expect(mocks.history).not.toHaveBeenCalled()

  await user.click(toggle)

  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await waitFor(() => expect(house.querySelector('path.recharts-line-curve')).toBeTruthy())
  expect(house.querySelector('path[stroke-dasharray="6 5"]')).toBeNull()
  expect(within(house).queryByText('Current amount')).not.toBeInTheDocument()
  expect(within(house).queryByText('Projection')).not.toBeInTheDocument()
  expect(mocks.history).toHaveBeenCalledTimes(1)
})

it('draws a straight pace guide even without a monthly contribution amount', async () => {
  mocks.goals.list.mockResolvedValue([{ ...fixtures()[0], monthly_contribution: null }])
  renderWithProviders(<GoalsPage />)
  const region = await screen.findByRole('region', { name: 'Progress for Server Fund' })
  await waitFor(() => {
    const line = region.querySelector('path[stroke-dasharray="6 5"]')
    expect(line).toBeTruthy()
  })
})

it('allows a failed history request to be retried', async () => {
  mocks.goals.list.mockResolvedValue([fixtures()[0]])
  mocks.history
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue({ points: [{ date: today, amount: 2700 }], notes: [] })
  const { user } = renderWithProviders(<GoalsPage />)
  await screen.findByRole('alert')
  await user.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  expect(mocks.history).toHaveBeenCalledTimes(2)
})

it('preserves native viewer permissions', async () => {
  mocks.writable = false
  renderWithProviders(<GoalsPage />)
  await screen.findByRole('region', { name: 'Progress for Server Fund' })
  expect(screen.queryByRole('button', { name: t('goals.add') })).not.toBeInTheDocument()
})

it('hides chart values in privacy mode', async () => {
  mocks.privacy = true
  renderWithProviders(<GoalsPage />)
  await screen.findByRole('region', { name: 'Progress for Server Fund' })
  expect(screen.queryByRole('img', { name: /history and projection/ })).not.toBeInTheDocument()
  expect(screen.queryByText('$471.43/mo')).not.toBeInTheDocument()
})

it('does not project reached or overdue goals', async () => {
  mocks.goals.list.mockResolvedValue([
    { ...fixtures()[0], current_amount: 6500 },
    { ...fixtures()[2], target_date: '2026-10-01', monthly_contribution: 100 },
  ])
  renderWithProviders(<GoalsPage />)
  for (const name of ['Server Fund', 'Studio Upgrade']) {
    const region = await screen.findByRole('region', { name: `Progress for ${name}` })
    expect(within(region).getByText('Current amount')).toBeVisible()
    expect(region.querySelector('path[stroke-dasharray="6 5"]')).toBeNull()
  }
})

it.each([
  {
    scenario: 'a historical decline above the target',
    current: 2700,
    monthly: 471.43,
    top: '9.9K',
  },
  {
    scenario: 'a current amount above the historical peak',
    current: 10000,
    monthly: null,
    top: '11K',
  },
  {
    scenario: 'a large payment capped below the historical peak',
    current: 2700,
    monthly: 8000,
    top: '9.9K',
  },
])('keeps 10% headroom for $scenario', async ({ current, monthly, top }) => {
  mocks.goals.list.mockResolvedValue([
    { ...fixtures()[0], current_amount: current, monthly_contribution: monthly },
  ])
  mocks.history.mockResolvedValue({
    points: [
      { date: '2026-09-30', amount: 9000 },
      { date: '2026-10-31', amount: 8000 },
    ],
    notes: [],
  })

  renderWithProviders(<GoalsPage />)
  const region = await screen.findByRole('region', { name: 'Progress for Server Fund' })
  await waitFor(() => {
    expect(region.querySelector('path.recharts-line-curve')).toBeTruthy()
    expect(within(region).getByText(top)).toBeVisible()
  })
})

it('keeps the month labels distinct on a short x-axis window', async () => {
  mocks.goals.list.mockResolvedValue([
    { ...fixtures()[1], created_at: today + 'T12:00:00Z' },
  ])
  mocks.history.mockResolvedValue({ points: [{ date: today, amount: 2700 }], notes: [] })
  const { user } = renderWithProviders(<GoalsPage />)
  const region = await screen.findByRole('region', { name: 'Progress for House Fund' })
  await user.click(within(region).getByRole('button'))
  await waitFor(() => {
    const labels = Array.from(region.querySelectorAll('.recharts-xAxis-tick-labels text')).map(
      node => node.textContent,
    )
    expect(labels).toEqual(['Oct 21', 'Nov 1', 'Dec 1'])
  })
})

it('draws a dot for a single actual point carried into a sparse window', async () => {
  mocks.goals.list.mockResolvedValue([fixtures()[2]])
  mocks.history.mockResolvedValue({ points: [{ date: '2026-02-01', amount: 4800 }], notes: [] })
  renderWithProviders(<GoalsPage />)
  const region = await screen.findByRole('region', { name: 'Progress for Studio Upgrade' })
  await waitFor(() => {
    const dot = region.querySelector('circle.recharts-line-dot')
    expect(dot).toBeTruthy()
  })
})

it('moves the target label off the axis when the target is zero', async () => {
  mocks.goals.list.mockResolvedValue([
    { ...fixtures()[0], target_amount: 0, current_amount: 500 },
  ])
  mocks.history.mockResolvedValue({
    points: [
      { date: '2026-09-30', amount: 400 },
      { date: '2026-10-31', amount: 450 },
    ],
    notes: [],
  })
  renderWithProviders(<GoalsPage />)
  const region = await screen.findByRole('region', { name: 'Progress for Server Fund' })
  await waitFor(() => {
    const label = within(region)
      .getByText('Target')
      .closest('text')
    // Sits above the dashed target line, one text line clear of the x-axis
    // tick labels that share the bottom-right corner.
    const line = region.querySelector('.recharts-reference-line line[stroke-dasharray="4 4"]')
    const labelY = Number(label?.getAttribute('y'))
    const lineY = Number(line?.getAttribute('y1'))
    expect(label?.getAttribute('text-anchor')).toBe('end')
    expect(labelY).toBeLessThan(lineY)
    const tickY = Math.min(
      ...Array.from(region.querySelectorAll('.recharts-xAxis-tick-labels text')).map(node =>
        Number(node.getAttribute('y')),
      ),
    )
    expect(tickY - labelY).toBeGreaterThanOrEqual(11)
  })
})

it('anchors the x-axis one month before creation even when history starts later', async () => {
  mocks.goals.list.mockResolvedValue([{ ...fixtures()[0], created_at: '2026-03-15T12:00:00Z' }])
  renderWithProviders(<GoalsPage />)
  const region = await screen.findByRole('region', { name: 'Progress for Server Fund' })
  await waitFor(() => {
    const path = region.querySelector('path.recharts-line-curve:not([stroke-dasharray])')
    const firstX = Number(path?.getAttribute('d')?.match(/^M([\d.]+)/)?.[1])
    // The first March snapshot sits to the right of the February start (plot x = 72).
    expect(firstX).toBeGreaterThan(73)
  })
})

it.each([-500, 0, 81.81])(
  'uses 100 when the padded peak is at most 90 for target %s',
  async target => {
    mocks.goals.list.mockResolvedValue([
      { ...fixtures()[0], target_amount: target, current_amount: -2700 },
    ])
    mocks.history.mockResolvedValue({
      points: [
        { date: '2026-09-30', amount: -5000 },
        { date: '2026-10-31', amount: -3000 },
      ],
      notes: [],
    })
    renderWithProviders(<GoalsPage />)
    const region = await screen.findByRole('region', { name: 'Progress for Server Fund' })
    await waitFor(() => {
      expect(region.querySelector('path.recharts-line-curve')).toBeTruthy()
      expect(within(region).getByText('100')).toBeVisible()
    })
  },
)

it.each([
  { historical: 500, current: 2700, target: 6000, bottom: '450' },
  { historical: 50, current: 200, target: 500, bottom: '0' },
  { historical: 90, current: 200, target: 500, bottom: '0' },
  { historical: 91, current: 200, target: 500, bottom: '81.9' },
  { historical: -1, current: 200, target: 500, bottom: '-100' },
  { historical: -90, current: 200, target: 500, bottom: '-100' },
  { historical: -91, current: 200, target: 500, bottom: '-100.1' },
  { historical: -100, current: 200, target: 500, bottom: '-110' },
  { historical: -101, current: 200, target: 500, bottom: '-111.1' },
  { historical: -5000, current: -2700, target: 0, bottom: '-5.5K' },
  { historical: -3000, current: -5000, target: 0, bottom: '-5.5K' },
  { historical: 0, current: 2700, target: 6000, bottom: '0' },
  { historical: 500, current: 700, target: 200, bottom: '180' },
  { historical: -3000, current: -2000, target: -5000, bottom: '-5.5K' },
  { historical: 500, current: 700, target: 0, bottom: '0' },
])(
  'pads below history $historical, current $current, and target $target',
  async ({ historical, current, target, bottom }) => {
    mocks.goals.list.mockResolvedValue([
      { ...fixtures()[0], current_amount: current, target_amount: target },
    ])
    mocks.history.mockResolvedValue({
      points: [
        { date: '2026-09-30', amount: historical },
        { date: '2026-10-31', amount: historical + 100 },
      ],
      notes: [],
    })
    renderWithProviders(<GoalsPage />)
    const region = await screen.findByRole('region', { name: 'Progress for Server Fund' })
    await waitFor(() => {
      expect(region.querySelector('path.recharts-line-curve')).toBeTruthy()
      expect(within(region).getByText(bottom)).toBeVisible()
    })
  },
)

it('uses the padded ceiling when the target is just above 90', async () => {
  mocks.goals.list.mockResolvedValue([{ ...fixtures()[0], target_amount: 91, current_amount: 50 }])
  mocks.history.mockResolvedValue({
    points: [
      { date: '2026-09-30', amount: 30 },
      { date: '2026-10-31', amount: 40 },
    ],
    notes: [],
  })
  renderWithProviders(<GoalsPage />)
  const region = await screen.findByRole('region', { name: 'Progress for Server Fund' })
  await waitFor(() => {
    expect(region.querySelector('path.recharts-line-curve')).toBeTruthy()
    expect(within(region).getByText('100.1')).toBeVisible()
  })
})

it.each([
  { target: 50, current: 5000, history: 9000, top: '9.9K' },
  { target: 50, current: 10000, history: 9000, top: '11K' },
  { target: 81.82, current: 50, history: 40, top: '90' },
  { target: 90, current: 50, history: 40, top: '99' },
])(
  'uses the padded peak for target $target, current $current, history $history',
  async ({ target, current, history, top }) => {
    mocks.goals.list.mockResolvedValue([
      { ...fixtures()[0], target_amount: target, current_amount: current },
    ])
    mocks.history.mockResolvedValue({
      points: [
        { date: '2026-09-30', amount: history },
        { date: '2026-10-31', amount: history - 10 },
      ],
      notes: [],
    })
    renderWithProviders(<GoalsPage />)
    const region = await screen.findByRole('region', { name: 'Progress for Server Fund' })
    await waitFor(() => {
      expect(region.querySelector('path.recharts-line-curve')).toBeTruthy()
      expect(within(region).getByText(top)).toBeVisible()
    })
  },
)
