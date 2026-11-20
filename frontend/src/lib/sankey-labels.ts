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
/** Smallest free space kept between two stacked label blocks. */
export const LABEL_BLOCK_GAP = 2

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
 * Place one column's labels, in node order. Each block starts at its node's
 * centre and grows downward (name lines, then the amount); a block is pushed
 * down only as far as needed to clear the previous one, and the column is
 * lifted when its last block would leave the chart.
 */
export function placeColumnLabels(
  items: ColumnLabelInput[],
  top: number,
  bottom: number,
): ColumnLabelPlacement[] {
  // A block covers its name lines plus the amount line.
  const lines = items.map((item) => item.lines.length + 1)
  const ys = items.map((item) => item.center)

  for (let i = 1; i < items.length; i++) {
    const clearOfPrevious = ys[i - 1] + lines[i - 1] * LABEL_LINE_HEIGHT + LABEL_BLOCK_GAP
    if (ys[i] < clearOfPrevious) ys[i] = clearOfPrevious
  }

  const last = items.length - 1
  if (last >= 0) {
    const bottomEdge = ys[last] + (lines[last] - 1) * LABEL_LINE_HEIGHT + LABEL_LINE_HEIGHT / 2
    const overflow = bottomEdge - bottom
    if (overflow > 0) {
      // Lift the whole column, but never above the top of the chart.
      const lift = Math.min(overflow, ys[0] - LABEL_LINE_HEIGHT / 2 - top)
      if (lift > 0) for (let i = 0; i <= last; i++) ys[i] -= lift
    }
    const aboveTop = top - (ys[0] - LABEL_LINE_HEIGHT / 2)
    if (aboveTop > 0) for (let i = 0; i <= last; i++) ys[i] += aboveTop
  }

  return items.map((item, i) => ({ lines: item.lines, y: ys[i] }))
}
