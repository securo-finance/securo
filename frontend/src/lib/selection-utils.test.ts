import { describe, expect, it } from 'vitest'
import { calculateRangeSelection } from './selection-utils'

describe('calculateRangeSelection', () => {
  const items = [
    { id: '1', name: 'Item 1', is_shared: false },
    { id: '2', name: 'Item 2', is_shared: false },
    { id: '3', name: 'Item 3', is_shared: true }, // Not selectable/shared
    { id: '4', name: 'Item 4', is_shared: false },
    { id: '5', name: 'Item 5', is_shared: false },
  ]

  const isSelectable = (item: typeof items[number]) => !item.is_shared

  it('performs normal toggle selection (selects if unchecked)', () => {
    const selected = new Set<string>()
    const result = calculateRangeSelection(selected, null, '1', items, false, isSelectable)
    expect(result.has('1')).toBe(true)
    expect(result.size).toBe(1)
  })

  it('performs normal toggle selection (deselects if checked)', () => {
    const selected = new Set<string>(['1'])
    const result = calculateRangeSelection(selected, '1', '1', items, false, isSelectable)
    expect(result.has('1')).toBe(false)
    expect(result.size).toBe(0)
  })

  it('falls back to normal toggle if lastSelectedId is not set', () => {
    const selected = new Set<string>()
    const result = calculateRangeSelection(selected, null, '4', items, true, isSelectable)
    expect(result.has('4')).toBe(true)
    expect(result.size).toBe(1)
  })

  it('falls back to normal toggle if lastSelectedId is not found in items', () => {
    const selected = new Set<string>(['1'])
    const result = calculateRangeSelection(selected, 'unknown', '4', items, true, isSelectable)
    expect(result.has('4')).toBe(true)
    expect(result.has('1')).toBe(true)
    expect(result.size).toBe(2)
  })

  it('selects range with shift+click (checking range)', () => {
    const selected = new Set<string>(['1'])
    // lastSelectedId = '1', clickedId = '5', isShiftKey = true, clickedId is not selected
    const result = calculateRangeSelection(selected, '1', '5', items, true, isSelectable)
    
    expect(result.has('1')).toBe(true)
    expect(result.has('2')).toBe(true)
    expect(result.has('3')).toBe(false) // skipped (not selectable/shared)
    expect(result.has('4')).toBe(true)
    expect(result.has('5')).toBe(true)
    expect(result.size).toBe(4)
  })

  it('deselects range with shift+click (unchecking range)', () => {
    const selected = new Set<string>(['1', '2', '4', '5'])
    // lastSelectedId = '1', clickedId = '5', isShiftKey = true, clickedId is already selected
    const result = calculateRangeSelection(selected, '1', '5', items, true, isSelectable)
    
    expect(result.has('1')).toBe(false)
    expect(result.has('2')).toBe(false)
    expect(result.has('4')).toBe(false)
    expect(result.has('5')).toBe(false)
    expect(result.size).toBe(0)
  })
})
