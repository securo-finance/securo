import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import RulesPage from '@/pages/rules'
import {
  rules as rulesApi, categories as categoriesApi, categoryGroups as categoryGroupsApi,
  accounts as accountsApi, payees as payeesApi, reconciliation as reconciliationApi,
} from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Rule } from '@/types'

// Match neighboring page fixtures: only consumer-read context fields are
// replaced. Query hooks/providers, the page and all UI children stay real.
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

const rules: Rule[] = [
  {
    id: 'review-rule', user_id: 'user-1', name: 'Review coffee',
    conditions_op: 'and', priority: 1, is_active: true,
    conditions: [
      { field: 'review_status', op: 'equals', value: 'needs_review' },
      { field: 'review_status', op: 'not_equals', value: 'reviewed' },
    ],
    actions: [{ op: 'set_reviewed', value: '' }],
  },
  {
    id: 'notes-rule', user_id: 'user-1', name: 'Keep existing notes rule',
    conditions_op: 'and', priority: 2, is_active: true,
    conditions: [{ field: 'description', op: 'contains', value: 'Coffee' }],
    actions: [{ op: 'append_notes', value: 'Existing note' }],
  },
]

describe('T5 RulesPage review exposure', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    // These are the actual queryFn return values: bare arrays, not {items}
    // or {data}. Real useQuery runs through renderWithProviders' QueryClient.
    vi.spyOn(rulesApi, 'list').mockResolvedValue(rules)
    vi.spyOn(categoriesApi, 'list').mockResolvedValue([])
    vi.spyOn(categoriesApi, 'listIncludingHidden').mockResolvedValue([])
    vi.spyOn(categoryGroupsApi, 'list').mockResolvedValue([])
    vi.spyOn(accountsApi, 'list').mockResolvedValue([])
    vi.spyOn(payeesApi, 'list').mockResolvedValue([])
    vi.spyOn(reconciliationApi, 'suggestions').mockResolvedValue([])
    vi.spyOn(reconciliationApi, 'rules').mockResolvedValue([])
  })

  it('P1 renders readable review summaries and filters Mark reviewed without changing other actions', async () => {
    const { user } = renderWithProviders(<RulesPage />, { route: '/rules' })
    await screen.findByText('Review coffee')
    await screen.findByText('Keep existing notes rule')
    expect(rulesApi.list).toHaveBeenCalled()

    // Existing summaries are controls, not mocked output.
    expect(screen.getByText(`${t('rules.fieldDescription')} ${t('rules.opContains')} "Coffee"`)).toBeInTheDocument()
    expect(screen.getByText(`→ ${t('rules.fieldNotes')}: Existing note`)).toBeInTheDocument()
    const summary = screen.getByText(content =>
      content.includes('"Needs review"') && content.includes('"Reviewed"'),
    )
    expect(summary).toHaveTextContent(/review status/i)
    expect(summary).toHaveTextContent(t('rules.opIs'))
    expect(summary).toHaveTextContent(t('rules.opIsNot'))
    expect(summary).not.toHaveTextContent('review_status')
    expect(summary).not.toHaveTextContent('needs_review')
    expect(screen.getByText(/^→ Mark reviewed$/)).toBeInTheDocument()
    expect(screen.queryByText('set_reviewed', { exact: true })).not.toBeInTheDocument()

    // Current filter selects are unnamed: use the shipped all-actions option
    // to identify the control through roles rather than relying on its index.
    const filters = screen.getAllByRole('combobox').filter(select =>
      within(select).queryByRole('option', { name: t('rules.filterAllActions') }),
    )
    expect(filters).toHaveLength(1)
    const actionFilter = filters[0]
    expect(within(actionFilter).getByRole('option', { name: 'Mark reviewed' })).toHaveValue('set_reviewed')
    await user.selectOptions(actionFilter, 'set_reviewed')
    expect(screen.getByText('Review coffee')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Keep existing notes rule')).not.toBeInTheDocument())

    await user.selectOptions(actionFilter, 'append_notes')
    expect(screen.getByText('Keep existing notes rule')).toBeInTheDocument()
    expect(screen.queryByText('Review coffee')).not.toBeInTheDocument()
    await user.selectOptions(actionFilter, '')
    expect(screen.getByText('Review coffee')).toBeInTheDocument()
    expect(screen.getByText('Keep existing notes rule')).toBeInTheDocument()
  })
})
