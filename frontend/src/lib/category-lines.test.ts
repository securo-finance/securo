import { describe, expect, it } from 'vitest'

import {
  buildLinesPayload,
  checkLines,
  largestLineCategoryId,
  type CategoryLineRow,
} from '@/lib/category-lines'
import type { TransactionCategoryLine } from '@/types'

const row = (categoryId: string, amount = '', percent = ''): CategoryLineRow => ({
  key: `${categoryId}-${amount}-${percent}`,
  categoryId,
  amount,
  percent,
})

describe('checkLines', () => {
  it('refuses the same category twice', () => {
    expect(checkLines([row('a', '100'), row('a', '50')], 'exact', 150, 'en-US')).toMatchObject({
      valid: false, reason: 'duplicateCategory',
    })
  })
  it('accepts exact parts that add up to the amount to the cent', () => {
    expect(checkLines([row('a', '0.1'), row('b', '0.2')], 'exact', 0.3, 'en-US')).toEqual({
      valid: true, remaining: 0, reason: null,
    })
  })

  it('reports what is left, in cents, and what is over', () => {
    expect(checkLines([row('a', '40'), row('b', '100')], 'exact', 150, 'en-US').remaining).toBe(1000)
    expect(checkLines([row('a', '40'), row('b', '120')], 'exact', 150, 'en-US').remaining).toBe(-1000)
  })

  it('reads amounts in the display locale', () => {
    expect(checkLines([row('a', '40,50'), row('b', '109,50')], 'exact', 150, 'pt-BR').valid).toBe(true)
  })

  it('needs two parts, a category on each and a positive share', () => {
    expect(checkLines([row('a', '150')], 'exact', 150, 'en-US').reason).toBe('tooFewLines')
    expect(checkLines([row('a', '50'), row('', '100')], 'exact', 150, 'en-US').reason).toBe('missingCategory')
    expect(checkLines([row('a', '150'), row('b', '0')], 'exact', 150, 'en-US').reason).toBe('zeroPart')
    expect(checkLines([row('a', 'x'), row('b', '150')], 'exact', 150, 'en-US').reason).toBe('invalidValue')
  })

  it('needs percentages to add up to exactly 100', () => {
    expect(checkLines([row('a', '', '33.33'), row('b', '', '66.67')], 'percent', 10, 'en-US').valid).toBe(true)
    expect(checkLines([row('a', '', '33.33'), row('b', '', '66.66')], 'percent', 10, 'en-US').remaining).toBe(1)
  })
})

describe('buildLinesPayload', () => {
  it('sends exact amounts in currency units and percentages as numbers', () => {
    expect(buildLinesPayload([row('a', '40,5'), row('b', '109,5')], 'exact', 'pt-BR')).toEqual({
      share_type: 'exact',
      lines: [{ category_id: 'a', amount: 40.5 }, { category_id: 'b', amount: 109.5 }],
    })
    expect(buildLinesPayload([row('a', '', '30'), row('b', '', '70')], 'percent', 'en-US')).toEqual({
      share_type: 'percent',
      lines: [{ category_id: 'a', share_pct: 30 }, { category_id: 'b', share_pct: 70 }],
    })
  })
})

describe('largestLineCategoryId', () => {
  const line = (category_id: string, amount: number, position: number): TransactionCategoryLine => ({
    id: category_id, category_id, amount, share_type: 'exact', share_pct: null, position, notes: null,
  })

  it('picks the largest part, the first one on a tie (the backend rule)', () => {
    expect(largestLineCategoryId([line('a', 40, 0), line('b', 110, 1)])).toBe('b')
    expect(largestLineCategoryId([line('a', 75, 0), line('b', 75, 1)])).toBe('a')
    expect(largestLineCategoryId([])).toBeNull()
  })
})
