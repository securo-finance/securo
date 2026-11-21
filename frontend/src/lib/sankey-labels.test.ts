import { describe, expect, it } from 'vitest'

import {
  LABEL_BLOCK_GAP,
  LABEL_CHAR_WIDTH,
  LABEL_LINE_HEIGHT,
  columnLabelSpan,
  placeColumnLabels,
  wrapLabelLines,
} from '@/lib/sankey-labels'

// The Money Map's label gutter measured in Chrome at 390px: the SVG is 316px
// wide and the gap between a side column and the centre bar leaves 102px.
const PHONE_GUTTER = 102
const BUDGET = Math.floor(PHONE_GUTTER / LABEL_CHAR_WIDTH)

describe('wrapLabelLines (#287)', () => {
  it('wraps the investment withdrawals label at phone width', () => {
    expect(wrapLabelLines('Investment withdrawals', PHONE_GUTTER)).toEqual(['Investment', 'withdrawals'])
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

describe('columnLabelSpan (#287)', () => {
  it('counts every block and the gaps between them', () => {
    // Two name lines plus the amount for the first block; one plus the amount
    // for the second.
    expect(
      columnLabelSpan([
        { center: 0, lines: ['Investment', 'withdrawals'] },
        { center: 0, lines: ['Rent'] },
      ]),
    ).toBe(3 * LABEL_LINE_HEIGHT + 2 * LABEL_LINE_HEIGHT + LABEL_BLOCK_GAP)
  })

  it('is zero for an empty column', () => {
    expect(columnLabelSpan([])).toBe(0)
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

  it('fits a tall wrapped block into the gaps of the column above it', () => {
    // CodeRabbit's case: a four-line name near the bottom, with free space
    // above it. Lifting the whole column stopped at the first label and left
    // the amount outside; each block has to use the gap over it instead.
    const placed = placeColumnLabels(
      [
        { center: 30, lines: ['Rent'] },
        { center: 175, lines: ['Groceries'] },
        { center: 321, lines: ['International', 'household', 'maintenance', 'A'] },
        { center: 351, lines: ['Fuel'] },
      ],
      8.5,
      376,
    )
    const edges = placed.map((p) => ({
      top: p.y - LABEL_LINE_HEIGHT / 2,
      // Name lines, then the amount line, measured to its lower edge.
      bottom: p.y + p.lines.length * LABEL_LINE_HEIGHT + LABEL_LINE_HEIGHT / 2,
    }))
    for (const edge of edges) {
      expect(edge.top).toBeGreaterThanOrEqual(8.5)
      expect(edge.bottom).toBeLessThanOrEqual(376)
    }
    for (let i = 1; i < edges.length; i++) {
      expect(edges[i].top).toBeGreaterThanOrEqual(edges[i - 1].bottom + LABEL_BLOCK_GAP)
    }
  })

  it('keeps the last small income label whole, as Gal measured on 2025 (#287)', () => {
    // Node centres taken from a 316px chart: salary, freelance, interest and a
    // tiny withdrawal squeezed against the bottom edge, as in Ana's 2025.
    const centres = [335, 547, 577, 593]
    const names = ['Salary', 'Freelance', 'Interest', 'Investment withdrawals']
    const placed = placeColumnLabels(
      centres.map((center, i) => ({ center, lines: wrapLabelLines(names[i], 110) })),
      8.5,
      596,
    )
    const blocks = placed.map((p) => ({
      top: p.y - LABEL_LINE_HEIGHT / 2,
      // Name lines, then the amount line, measured to its lower edge.
      bottom: p.y + p.lines.length * LABEL_LINE_HEIGHT + LABEL_LINE_HEIGHT / 2,
    }))
    for (const block of blocks) {
      expect(block.top).toBeGreaterThanOrEqual(8.5)
      expect(block.bottom).toBeLessThanOrEqual(596)
    }
    for (let i = 1; i < blocks.length; i++) {
      expect(blocks[i].top - blocks[i - 1].bottom).toBeGreaterThanOrEqual(4)
    }
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
