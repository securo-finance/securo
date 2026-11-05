import { describe, expect, it } from 'vitest'

import { previewableActions } from './rule-form-utils'

describe('previewableActions review actions', () => {
  it.each(['ignore', 'set_reviewed'])('keeps the value-less %s action', (op) => {
    const action = { op, value: '' }
    expect(previewableActions([action])).toEqual([action])
  })

  it.each(['set_category', 'set_payee', 'set_description', 'append_notes', 'unknown'])(
    'rejects blank values for %s rather than broadening the value-less exception',
    (op) => {
      expect(previewableActions([
        { op, value: '' },
        { op, value: '   ' },
      ])).toEqual([])
    },
  )

  it('retains the description guard alongside both value-less actions', () => {
    const ignore = { op: 'ignore', value: '' }
    const review = { op: 'set_reviewed', value: '' }
    const notes = { op: 'append_notes', value: 'Keep this note' }
    expect(previewableActions([
      ignore,
      { op: 'set_description', value: 'x'.repeat(501) },
      review,
      notes,
    ])).toEqual([ignore, review, notes])
  })
})
