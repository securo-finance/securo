import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { RuleDialog } from '@/components/rule-dialog'
import { rules as rulesApi } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Rule, RuleAction } from '@/types'

vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

const baseRule: Rule = {
  id: 'review-rule', user_id: 'user-1', name: 'Review rule',
  conditions_op: 'and',
  conditions: [{ field: 'description', op: 'contains', value: 'Coffee' }],
  actions: [{ op: 'append_notes', value: 'Keep this note' }],
  priority: 7, is_active: true,
}

// The current native selects have no accessible names. Locate them through
// their actual option values, not DOM classes, row order, or invented labels.
function selectWithOption(value: string): HTMLElement {
  const matches = screen.getAllByRole('combobox').filter(select =>
    within(select).queryAllByRole('option').some(option => option.getAttribute('value') === value),
  )
  expect(matches).toHaveLength(1)
  return matches[0]
}

function optionValues(select: HTMLElement) {
  return within(select).getAllByRole('option').map(option => option.getAttribute('value'))
}

function mount(rule: Rule | null = null) {
  const onSave = vi.fn((data: Partial<Rule>) => {
    if (rule) return rulesApi.update(rule.id, data)
    return rulesApi.create(data as Omit<Rule, 'id' | 'user_id'>)
  })
  return {
    ...renderWithProviders(
      <RuleDialog open onClose={vi.fn()} rule={rule}
        categories={[]} categoryGroups={[]} accounts={[]} payees={[]}
        initialData={rule ? undefined : { actions: [{ op: 'append_notes', value: 'Discard me' }] }}
        onSave={onSave} loading={false} />,
    ),
    onSave,
  }
}

describe('T5 RuleDialog review authoring', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(rulesApi, 'preview').mockResolvedValue({
      matched: 0, will_change: 0, will_apply: true, sample: [], offset: 0,
    })
    vi.spyOn(rulesApi, 'create').mockResolvedValue({ ...baseRule, applied_count: 0 })
    vi.spyOn(rulesApi, 'update').mockResolvedValue({ ...baseRule, applied_count: 0 })
  })

  it.each([
    { id: 'D1-main', op: 'equals', value: 'needs_review', label: 'Needs review' },
    { id: 'D2', op: 'not_equals', value: 'needs_review', label: 'Needs review' },
    { id: 'D3', op: 'equals', value: 'reviewed', label: 'Reviewed' },
    { id: 'D4', op: 'not_equals', value: 'reviewed', label: 'Reviewed' },
  ])('$id authors $op $value and previews/saves a canonical no-value action', async ({ op, value, label }) => {
    const { user, onSave } = mount()
    const field = selectWithOption('description')
    // FIRST feature oracle: the untouched UI lacks this option. No mock
    // supplies conditions, actions, translations, or RuleDialog rendering.
    expect(within(field).getByRole('option', { name: /^review status$/i })).toHaveValue('review_status')
    await user.selectOptions(field, 'review_status')

    const operator = selectWithOption('equals')
    expect(optionValues(operator)).toEqual(['equals', 'not_equals'])
    await user.selectOptions(operator, op)
    const status = selectWithOption('needs_review')
    expect(optionValues(status)).toEqual(['needs_review', 'reviewed'])
    expect(within(status).getByRole('option', { name: label })).toHaveValue(value)
    await user.selectOptions(status, value)

    const action = selectWithOption('append_notes')
    expect(within(action).getByRole('option', { name: /^mark reviewed$/i })).toHaveValue('set_reviewed')
    await user.selectOptions(action, 'set_reviewed')
    // Only the rule name remains: neither condition nor action is free text.
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    await user.type(screen.getByRole('textbox'), 'Review authored')
    const payload: Partial<Rule> = {
      name: 'Review authored', conditions_op: 'and',
      conditions: [{ field: 'review_status', op, value }],
      actions: [{ op: 'set_reviewed', value: '' }], priority: 0,
      is_active: true, apply_to_existing: true, overwrite_existing_categories: false,
    }
    const preview = screen.getByRole('button', { name: t('rules.preview') })
    expect(preview).toBeEnabled()
    await user.click(preview)
    await waitFor(() => expect(rulesApi.preview).toHaveBeenCalledWith({ ...payloadWithoutName(payload), limit: 50, offset: 0 }))
    const save = screen.getByRole('button', { name: t('common.save') })
    expect(save).toBeEnabled()
    await user.click(save)
    expect(onSave).toHaveBeenCalledExactlyOnceWith(payload)
    expect(rulesApi.create).toHaveBeenCalledExactlyOnceWith(payload)
    expect(rulesApi.update).not.toHaveBeenCalled()
  })

  it('D5 hydrates null/empty review actions and preserves a non-review action', async () => {
    for (const legacyValue of [null, ''] as const) {
      vi.mocked(rulesApi.preview).mockClear()
      vi.mocked(rulesApi.update).mockClear()
      // Deliberately model legacy wire data at the typed boundary only;
      // RuleAction.value is string today. No production type is changed.
      const actions = [
        { op: 'set_reviewed', value: legacyValue },
        { op: 'append_notes', value: 'Keep this note' },
      ] as unknown as RuleAction[]
      const rule = { ...baseRule, actions }
      const { user, onSave, unmount } = mount(rule)
      const reviewActions = screen.getAllByRole('combobox').filter(
        (select): select is HTMLSelectElement =>
          select instanceof HTMLSelectElement && select.value === 'set_reviewed',
      )
      expect(reviewActions).toHaveLength(1)
      const action = reviewActions[0]
      expect(action).toHaveValue('set_reviewed')
      expect(screen.getAllByRole('textbox')).toHaveLength(3) // name, condition, unchanged notes
      expect(screen.getByDisplayValue('Keep this note')).toBeInTheDocument()
      const payload: Partial<Rule> = {
        name: rule.name, conditions_op: 'and', conditions: rule.conditions,
        actions: [{ op: 'set_reviewed', value: '' }, { op: 'append_notes', value: 'Keep this note' }],
        priority: 7, is_active: true, apply_to_existing: false, overwrite_existing_categories: false,
      }
      await user.click(screen.getByRole('button', { name: t('rules.preview') }))
      await waitFor(() => expect(rulesApi.preview).toHaveBeenCalledWith({ ...payloadWithoutName(payload), limit: 50, offset: 0 }))
      await user.click(screen.getByRole('button', { name: t('common.save') }))
      expect(onSave).toHaveBeenCalledExactlyOnceWith(payload)
      expect(rulesApi.update).toHaveBeenCalledExactlyOnceWith(rule.id, payload)
      expect(rulesApi.create).not.toHaveBeenCalled()
      unmount()
    }
  })
})

// Preview accepts neither name nor priority; keep its complete request oracle
// explicit rather than using objectContaining on the feature-bearing fields.
function payloadWithoutName(payload: Partial<Rule>) {
  return {
    conditions_op: payload.conditions_op, conditions: payload.conditions,
    actions: payload.actions, is_active: payload.is_active,
    apply_to_existing: payload.apply_to_existing,
    overwrite_existing_categories: payload.overwrite_existing_categories,
  }
}
