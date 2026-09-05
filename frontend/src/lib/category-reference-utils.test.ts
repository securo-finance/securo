import { describe, expect, it } from 'vitest'

import {
  findCategoryReference,
  getRuleCategoryId,
  getRuleCategoryName,
} from './category-reference-utils'

const hiddenCategory = {
  id: 'hidden-category',
  name: 'Historic category',
  is_hidden: true,
}

const rule = {
  actions: [{ op: 'set_category', value: hiddenCategory.id }],
}

describe('category reference resolution', () => {
  it('resolves hidden categories when the full display catalog supplies them', () => {
    expect(findCategoryReference([hiddenCategory], hiddenCategory.id)).toBe(hiddenCategory)
    expect(getRuleCategoryId(rule)).toBe(hiddenCategory.id)
    expect(getRuleCategoryName(rule, [hiddenCategory])).toBe(hiddenCategory.name)
  })

  it('does not invent a label for a missing category reference', () => {
    expect(getRuleCategoryName(rule, [])).toBeNull()
  })
})
