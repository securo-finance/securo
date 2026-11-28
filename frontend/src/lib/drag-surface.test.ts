import { afterEach, describe, expect, it } from 'vitest'
import { startsDragAt } from './drag-surface'

function render(html: string): HTMLElement {
  document.body.innerHTML = html
  return document.body.firstElementChild as HTMLElement
}

/** jsdom has no layout (and no Range.getClientRects): say where the page's text is. */
function textAt(rect: { left: number; right: number; top: number; bottom: number } | null) {
  Range.prototype.getClientRects = () => (rect ? [rect] : []) as unknown as DOMRectList
}

afterEach(() => {
  delete (Range.prototype as Partial<Range>).getClientRects
  document.body.innerHTML = ''
})

describe('startsDragAt', () => {
  it('starts on an empty part of the row', () => {
    const row = render('<div><span>Bond fund</span></div>')
    textAt(null)

    expect(startsDragAt(row, 5, 5)).toBe(true)
  })

  it('leaves the text selectable', () => {
    const name = render('<div><span>Bond fund</span></div>').querySelector('span')!
    textAt({ left: 0, right: 60, top: 0, bottom: 16 })

    expect(startsDragAt(name, 10, 8)).toBe(false)
    expect(startsDragAt(name, 80, 8)).toBe(true)
  })

  it('leaves a menu, a link and a field alone', () => {
    const row = render(
      '<div><button aria-haspopup="menu"><svg></svg></button><a href="/x"><i></i></a><input /></div>',
    )
    textAt(null)

    expect(startsDragAt(row.querySelector('svg'), 1, 1)).toBe(false)
    expect(startsDragAt(row.querySelector('i'), 1, 1)).toBe(false)
    expect(startsDragAt(row.querySelector('input'), 1, 1)).toBe(false)
  })

  it('starts on a button that acts on click', () => {
    const icon = render('<div><button><svg></svg></button></div>').querySelector('svg')!
    textAt(null)

    expect(startsDragAt(icon, 1, 1)).toBe(true)
  })
})
