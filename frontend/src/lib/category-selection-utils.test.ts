import { describe, expect, it } from 'vitest'
import type { Category } from '../types'
import {
  isCategoryHiddenFromSelection,
  resolveSelectedCategory,
} from './category-selection-utils'

const category = (overrides: Partial<Category>): Category => ({
  id: 'category-id',
  user_id: 'user-id',
  group_id: null,
  name: 'Category',
  icon: 'circle-help',
  color: '#6B7280',
  is_system: true,
  is_hidden: false,
  treat_as_transfer: false,
  is_ignored: false,
  ...overrides,
})

describe('resolveSelectedCategory', () => {
  it('uses the selectable category when the current value is visible', () => {
    const visible = category({ id: 'visible', name: 'Visible' })
    const staleCurrent = category({ id: 'visible', name: 'Stale', is_hidden: true })

    expect(resolveSelectedCategory([visible], 'visible', staleCurrent)).toBe(visible)
  })

  it('falls back to a hidden current category missing from selectable options', () => {
    const hidden = category({ id: 'hidden', name: 'Historical', is_hidden: true })

    expect(resolveSelectedCategory([], 'hidden', hidden)).toBe(hidden)
  })

  it('does not display an unrelated fallback category', () => {
    const hidden = category({ id: 'other', is_hidden: true })

    expect(resolveSelectedCategory([], 'selected', hidden)).toBeUndefined()
  })
})

describe('isCategoryHiddenFromSelection', () => {
  it('marks an explicitly hidden category as hidden', () => {
    const hidden = category({ id: 'hidden', is_hidden: true })

    expect(isCategoryHiddenFromSelection([hidden], hidden)).toBe(true)
  })

  it('marks a category filtered out with its hidden group as hidden', () => {
    const grouped = category({ id: 'grouped', group_id: 'hidden-group' })

    expect(isCategoryHiddenFromSelection([], grouped)).toBe(true)
  })

  it('keeps a selectable category visible', () => {
    const visible = category({ id: 'visible' })

    expect(isCategoryHiddenFromSelection([visible], visible)).toBe(false)
  })
})
