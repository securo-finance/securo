// The Money Map draws its own labels inside the SVG, so the browser never wraps
// them. A name wider than the gap between a side column and the centre bar ran
// underneath the bar and came out cut (the "Investment withdrawals" label at
// 390px, #287). These helpers wrap a name to the gap it actually has and stack
// a column's label blocks without overlaps, keeping them inside the chart.
//
// SVG text has no layout we can query before painting, so the wrap budget is
// estimated from the average character width of the label font (11px, medium):
// measured labels sit between 5.2 and 6.2 px per character, and 6 keeps the
// estimate on the safe side for the wider scripts.

/** Average width of one character at the label font (11px, medium). */
export const LABEL_CHAR_WIDTH = 6
/** Baseline-to-baseline distance between the lines of one label block. */
export const LABEL_LINE_HEIGHT = 13
/**
 * Smallest free space kept between two stacked label blocks. The glyph boxes
 * are about 5px shorter than their 13px line step, so a 2px gap printed as a
 * visible 0.3px and read as one run of text; 6 leaves about 4px between blocks.
 */
export const LABEL_BLOCK_GAP = 6

/**
 * Split a label into lines that fit `maxWidth`, breaking between words and
 * inside a single word only when that word cannot fit a line on its own.
 */
export function wrapLabelLines(text: string, maxWidth: number): string[] {
  const maxChars = Math.max(6, Math.floor(maxWidth / LABEL_CHAR_WIDTH))
  const words = text.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return ['']
  const lines: string[] = []
  let line = ''
  for (let word of words) {
    while (word.length > maxChars) {
      if (line) {
        lines.push(line)
        line = ''
      }
      lines.push(word.slice(0, maxChars))
      word = word.slice(maxChars)
    }
    if (!line) line = word
    else if (line.length + 1 + word.length <= maxChars) line += ` ${word}`
    else {
      lines.push(line)
      line = word
    }
  }
  if (line) lines.push(line)
  return lines
}

export interface ColumnLabelInput {
  /** Vertical centre of the node this label belongs to. */
  center: number
  lines: string[]
}

export interface ColumnLabelPlacement {
  lines: string[]
  /** y of the first line; the remaining lines and the amount follow every 13px. */
  y: number
}

/**
 * Height one column of labels takes when they are stacked back to back: every
 * block (name lines plus the amount) and a gap between neighbours. The chart
 * sizes itself from this, so a column with many wrapped names never runs out
 * of room.
 */
export function columnLabelSpan(items: ColumnLabelInput[]): number {
  if (items.length === 0) return 0
  return (
    items.reduce((sum, item) => sum + (item.lines.length + 1) * LABEL_LINE_HEIGHT, 0) +
    LABEL_BLOCK_GAP * (items.length - 1)
  )
}

/**
 * Place one column's labels, in node order. Each block starts at its node's
 * centre and grows downward (name lines, then the amount); a block is pushed
 * down only as far as needed to clear the previous one. When the last block
 * would leave the chart, the column is pulled up block by block, each into the
 * gap above it, so a tall wrapped label at the bottom does not drag the whole
 * column with it.
 */
export function placeColumnLabels(
  items: ColumnLabelInput[],
  top: number,
  bottom: number,
): ColumnLabelPlacement[] {
  // A block covers its name lines plus the amount line.
  const lines = items.map((item) => item.lines.length + 1)
  // Top edge to bottom edge of the block, measured on the line grid.
  const blockHeight = lines.map((count) => count * LABEL_LINE_HEIGHT)
  const ys = items.map((item) => item.center)

  for (let i = 1; i < items.length; i++) {
    ys[i] = Math.max(ys[i], ys[i - 1] + blockHeight[i - 1] + LABEL_BLOCK_GAP)
  }

  const last = items.length - 1
  if (last >= 0 && ys[last] + (lines[last] - 1) * LABEL_LINE_HEIGHT + LABEL_LINE_HEIGHT / 2 > bottom) {
    // Sit the last block on the bottom edge, then fit the ones above it into
    // the space they have instead of moving the whole column together.
    ys[last] = Math.min(
      ys[last],
      bottom - (lines[last] - 1) * LABEL_LINE_HEIGHT - LABEL_LINE_HEIGHT / 2,
    )
    for (let i = last - 1; i >= 0; i--) {
      ys[i] = Math.min(ys[i], ys[i + 1] - blockHeight[i] - LABEL_BLOCK_GAP)
    }
    // Anything left above the chart's top edge moves back down.
    ys[0] = Math.max(ys[0], top + LABEL_LINE_HEIGHT / 2)
    for (let i = 1; i <= last; i++) {
      ys[i] = Math.max(ys[i], ys[i - 1] + blockHeight[i - 1] + LABEL_BLOCK_GAP)
    }
  }

  return items.map((item, i) => ({ lines: item.lines, y: ys[i] }))
}
