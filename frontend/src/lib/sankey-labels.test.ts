import { describe, expect, it } from 'vitest'

import {
  LABEL_BLOCK_GAP,
  LABEL_CHAR_WIDTH,
  LABEL_LINE_HEIGHT,
  placeColumnLabels,
  wrapLabelLines,
} from '@/lib/sankey-labels'

// The Money Map's label gutter measured in Chrome at 390px: the SVG is 316px
// wide and the gap between a side column and the centre bar leaves 102px.
const PHONE_GUTTER = 102
const BUDGET = Math.floor(PHONE_GUTTER / LABEL_CHAR_WIDTH)

describe('wrapLabelLines (#287)', () => {
  it('wraps the investment withdrawals label at phone width', () => {
    expect(wrapLabelLines('Investment withdrawals', PHONE_GUTTER)).toEqual([
      'Investment',
      'withdrawals',
    ])
  })

  it('keeps a short name on one line', () => {
    expect(wrapLabelLines('Rent', PHONE_GUTTER)).toEqual(['Rent'])
  })

  it('keeps every line inside the budget, in every bundled language', () => {
    const labels = [
      'Investment withdrawals',
      'Resgates de investimento',
      'Entnahmen aus Anlagen',
      "Retraits d'investissements",
      'Retiros de inversiones',
      'Opnames uit beleggingen',
      'Wypłaty z inwestycji',
      'Αναλήψεις επενδύσεων',
      'Вывод из инвестиций',
    ]
    for (const label of labels) {
      for (const line of wrapLabelLines(label, PHONE_GUTTER)) {
        expect(line.length).toBeLessThanOrEqual(BUDGET)
      }
    }
  })

  it('breaks a single word wider than the gutter instead of letting it overflow', () => {
    const lines = wrapLabelLines('Supercalifragilisticexpialidocious', PHONE_GUTTER)
    expect(lines.length).toBeGreaterThan(1)
    for (const line of lines) expect(line.length).toBeLessThanOrEqual(BUDGET)
    expect(lines.join('')).toBe('Supercalifragilisticexpialidocious')
  })
})

describe('placeColumnLabels (#287)', () => {
  it('leaves a label on its node when nothing is in the way', () => {
    const [placed] = placeColumnLabels([{ center: 120, lines: ['Rent'] }], 8, 366)
    expect(placed.y).toBe(120)
  })

  it('pushes a label below a wrapped neighbour clear of its lines', () => {
    const placed = placeColumnLabels(
      [
        { center: 100, lines: ['Investment', 'withdrawals'] },
        { center: 104, lines: ['Rent'] },
      ],
      8,
      366,
    )
    // Two name lines plus the amount clear before the next block may start.
    expect(placed[1].y).toBeGreaterThanOrEqual(placed[0].y + 3 * LABEL_LINE_HEIGHT + LABEL_BLOCK_GAP)
  })

  it('lifts the column so the last amount line stays inside the chart', () => {
    const placed = placeColumnLabels(
      [
        { center: 340, lines: ['Investment', 'withdrawals'] },
        { center: 360, lines: ['Surplus'] },
      ],
      8,
      366,
    )
    const lastBottom = placed[1].y + LABEL_LINE_HEIGHT * 1.5
    expect(lastBottom).toBeLessThanOrEqual(366)
  })
})
