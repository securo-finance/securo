import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { useLocation, useNavigate } from 'react-router-dom'

import GroupsPage from '@/pages/groups'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({ list: vi.fn() }))

vi.mock('@/lib/api', () => ({
  groups: { list: api.list },
  currencies: { list: vi.fn() },
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: false }),
}))

const groups = [
  { id: '1', name: 'Vacación familiar', is_archived: false },
  { id: '2', name: 'Trabajo', is_archived: false },
  { id: '3', name: 'Vacación pasada', is_archived: true },
].map((group) => ({
  ...group, kind: 'social', color: '#123456', members: [], default_currency: 'USD', is_owner: true,
}))

function LocationProbe() {
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <>
      <output data-testid="location">{location.pathname}{location.search}</output>
      <button onClick={() => navigate(-1)}>Back</button>
    </>
  )
}

describe('GroupsPage search', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.list.mockResolvedValue([groups[0], groups[1]])
  })

  it('sends a trimmed search to the server, renders its response, and clears the search', async () => {
    const { user } = renderWithProviders(<><GroupsPage /><LocationProbe /></>, { route: '/groups' })
    await screen.findByText('Vacación familiar')
    // Only the server decides whether a returned group matches the query.
    api.list.mockResolvedValue([groups[1]])
    await user.type(screen.getByRole('searchbox'), '  VACACION  ')

    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(false, { q: 'VACACION', status: 'active' }))
    await screen.findByText('Trabajo')
    expect(screen.queryByText('Vacación familiar')).not.toBeInTheDocument()

    api.list.mockResolvedValue([groups[0], groups[1]])
    await user.click(screen.getByRole('button', { name: t('splitGroups.clearSearch') }))
    expect(screen.getByRole('searchbox')).toHaveValue('')
    await screen.findByText('Vacación familiar')
    expect(screen.getByTestId('location').textContent).toBe('/groups')
  })

  it('combines search with status filters and distinguishes no matches from an empty list', async () => {
    const { user } = renderWithProviders(<><GroupsPage /><LocationProbe /></>, { route: '/groups' })
    await screen.findByText('Vacación familiar')
    await user.type(screen.getByRole('searchbox'), 'vacacion')
    api.list.mockResolvedValue([groups[2]])
    await user.click(screen.getByRole('button', { name: t('splitGroups.filter.archived') }))

    await screen.findByText('Vacación pasada')
    expect(screen.queryByText('Vacación familiar')).not.toBeInTheDocument()

    api.list.mockResolvedValue([groups[0], groups[2]])
    await user.click(screen.getByRole('button', { name: t('splitGroups.filter.all') }))
    await screen.findByText('Vacación familiar')
    expect(screen.getByText('Vacación pasada')).toBeInTheDocument()
    expect(screen.queryByText('Trabajo')).not.toBeInTheDocument()

    api.list.mockResolvedValue([])
    await user.clear(screen.getByRole('searchbox'))
    await user.type(screen.getByRole('searchbox'), 'missing')
    await screen.findByText(t('common.noResults'))
    const params = new URL(screen.getByTestId('location').textContent!, 'http://localhost').searchParams
    expect(Object.fromEntries(params)).toEqual({ q: 'missing', status: 'all' })
    expect(screen.queryByText(t('splitGroups.emptyHint'))).not.toBeInTheDocument()
  })

  it('restores search and status from a bookmarked URL, preserving other parameters when clearing', async () => {
    api.list.mockResolvedValue([groups[2]])
    const { user } = renderWithProviders(<><GroupsPage /><LocationProbe /></>, {
      route: '/groups?q=vacacion&status=archived&other=value',
    })
    await screen.findByText('Vacación pasada')
    expect(api.list).toHaveBeenLastCalledWith(true, { q: 'vacacion', status: 'archived' })
    expect(screen.getByRole('searchbox')).toHaveValue('vacacion')
    expect(screen.queryByText('Vacación familiar')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('splitGroups.clearSearch') }))
    expect(screen.getByTestId('location')).toHaveTextContent('/groups?status=archived&other=value')
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(true, { q: undefined, status: 'archived' }))
  })

  it('restores results on back navigation and uses active for an invalid status', async () => {
    const { user } = renderWithProviders(<><GroupsPage /><LocationProbe /></>, {
      route: '/groups?q=vacacion&status=invalid',
    })
    await screen.findByText('Vacación familiar')
    expect(api.list).toHaveBeenLastCalledWith(false, { q: 'vacacion', status: 'active' })
    api.list.mockResolvedValue([groups[2]])
    await user.click(screen.getByRole('button', { name: t('splitGroups.filter.archived') }))
    await screen.findByText('Vacación pasada')
    api.list.mockResolvedValue([groups[0]])
    await user.click(screen.getByRole('button', { name: /^Back$/ }))
    await screen.findByText('Vacación familiar')
    expect(screen.queryByText('Vacación pasada')).not.toBeInTheDocument()
    expect(screen.getByRole('searchbox')).toHaveValue('vacacion')
  })
})
