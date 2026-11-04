import { afterEach, describe, expect, it } from 'vitest'

import { hasOpenDialog, isInsideOverlayLayer } from '@/lib/overlay-layers'

function mount(html: string) {
  document.body.innerHTML = html
}

describe('overlay layers', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('treats dialog, popper, listbox and overlay content as an overlay layer', () => {
    mount(`
      <div role="dialog"><button id="in-dialog">a</button></div>
      <div data-radix-popper-content-wrapper><span id="in-popper">b</span></div>
      <div role="listbox"><div id="in-listbox">c</div></div>
      <div data-slot="dialog-overlay" id="overlay"></div>
      <main><button id="page">d</button></main>
    `)

    expect(isInsideOverlayLayer(document.getElementById('in-dialog'))).toBe(true)
    expect(isInsideOverlayLayer(document.getElementById('in-popper'))).toBe(true)
    expect(isInsideOverlayLayer(document.getElementById('in-listbox'))).toBe(true)
    expect(isInsideOverlayLayer(document.getElementById('overlay'))).toBe(true)
    expect(isInsideOverlayLayer(document.getElementById('page'))).toBe(false)
  })

  it('resolves text nodes to their parent element', () => {
    mount('<div role="dialog"><span id="label">Category</span></div>')
    const text = document.getElementById('label')!.firstChild

    expect(isInsideOverlayLayer(text)).toBe(true)
  })

  it('ignores missing targets', () => {
    expect(isInsideOverlayLayer(null)).toBe(false)
  })

  it('reports an open dialog only while its state is open', () => {
    mount('<div role="dialog" data-state="closed"></div>')
    expect(hasOpenDialog()).toBe(false)

    mount('<div role="dialog" data-state="open"></div>')
    expect(hasOpenDialog()).toBe(true)
  })
})
