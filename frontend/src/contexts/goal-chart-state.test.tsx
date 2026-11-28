import { act, renderHook } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { goal } from '@/test/goal-fixtures'
import { useGoalChartState } from './goal-chart-state'

const scope = vi.hoisted(() => ({ userId: 'user-1', workspaceId: 'ws-1' }))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: scope.userId } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ current: { id: scope.workspaceId } }),
}))

const today = '2026-11-21'
beforeEach(() => {
  localStorage.clear()
  scope.userId = 'user-1'
  scope.workspaceId = 'ws-1'
})

it.each(['2027-01-01', null])(
  'defaults and restores the toggled state with deadline %s',
  target_date => {
    const fixture = goal({ target_date }, today)
    const first = renderHook(() => useGoalChartState(fixture))
    expect(first.result.current.expanded).toBe(Boolean(target_date))
    act(() => first.result.current.toggleExpanded())
    const expected = !target_date
    expect(first.result.current.expanded).toBe(expected)
    expect(localStorage.getItem('securo.goalChart.expanded.user-1.ws-1.goal')).toBe(
      String(expected),
    )
    first.unmount()

    const restored = renderHook(() => useGoalChartState(fixture))
    expect(restored.result.current.expanded).toBe(expected)
  },
)

it.each(['true', 'false'])('restores an explicitly saved %s preference', saved => {
  localStorage.setItem('securo.goalChart.expanded.user-1.ws-1.goal', saved)
  const { result } = renderHook(() => useGoalChartState(goal({}, today)))
  expect(result.current.expanded).toBe(saved === 'true')
})

it.each(['2027-01-01', null])(
  'uses the default for an invalid preference with deadline %s',
  target_date => {
    localStorage.setItem('securo.goalChart.expanded.user-1.ws-1.goal', 'invalid')
    const { result } = renderHook(() => useGoalChartState(goal({ target_date }, today)))
    expect(result.current.expanded).toBe(Boolean(target_date))
  },
)

it('keeps different goals separate, including when the goal changes without remounting', () => {
  const first = goal({ id: 'first' }, today)
  const second = goal({ id: 'second' }, today)
  const { result, rerender } = renderHook(({ fixture }) => useGoalChartState(fixture), {
    initialProps: { fixture: first },
  })
  act(() => result.current.toggleExpanded())
  expect(result.current.expanded).toBe(false)
  rerender({ fixture: second })
  expect(result.current.expanded).toBe(true)
  act(() => result.current.toggleExpanded())
  rerender({ fixture: first })
  expect(result.current.expanded).toBe(false)
})

it.each(['userId', 'workspaceId'] as const)('keeps preferences separate when %s changes', field => {
  const fixture = goal({}, today)
  const { result, rerender } = renderHook(() => useGoalChartState(fixture))
  act(() => result.current.toggleExpanded())
  const original = scope[field]
  scope[field] = 'other'
  rerender()
  expect(result.current.expanded).toBe(true)
  act(() => result.current.toggleExpanded())
  scope[field] = original
  rerender()
  expect(result.current.expanded).toBe(false)
})
