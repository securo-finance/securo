/**
 * `provider_category` compares against the label the bank/provider sent, which
 * the app never translates (#280). Left to type it by hand, a pt-BR user writes
 * "Supermercado" and the rule never matches the bank's "Groceries". The editor
 * therefore offers the values the workspace has already received and says where
 * they come from.
 */
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RuleDialog } from '@/components/rule-dialog'
import { rules } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    rules: { ...actual.rules, providerCategories: vi.fn() },
  }
})

beforeEach(() => {
  vi.mocked(rules.providerCategories).mockResolvedValue(['Groceries', 'Food and drinks'])
})

function renderDialog() {
  return renderWithProviders(
    <RuleDialog
      open
      onClose={vi.fn()}
      rule={null}
      categories={[]}
      categoryGroups={[]}
      accounts={[]}
      payees={[]}
      onSave={vi.fn()}
      loading={false}
      initialData={{
        name: 'Provider category',
        conditions: [{ field: 'provider_category', op: 'equals', value: '' }],
        actions: [{ op: 'set_category', value: '' }],
      }}
    />,
  )
}

describe('RuleDialog provider category condition', () => {
  it('suggests the labels the workspace already received', async () => {
    renderDialog()

    await waitFor(() => {
      const list = document.querySelector('#provider-category-suggestions')
      expect(list).not.toBeNull()
      const values = Array.from((list as HTMLDataListElement).options).map(o => o.value)
      expect(values).toEqual(['Groceries', 'Food and drinks'])
    })
  })

  it('says the value is the bank’s own label, not a translation', async () => {
    renderDialog()

    expect(await screen.findByText(t('rules.providerCategoryHint'))).toBeInTheDocument()
  })
})
