// @vitest-environment jsdom
import { render } from '@testing-library/react'
import { ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

import { forwardArrow } from '@/lib/format'

// index.css mirrors these icons in right-to-left layouts by their class name,
// so a lucide rename would silently stop the mirroring.
describe('directional icons in right-to-left layouts', () => {
  afterEach(() => {
    document.documentElement.dir = ''
  })

  it('carry the class names the RTL mirroring rule targets', () => {
    const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
    const icons = { ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight }
    for (const [name, Icon] of Object.entries(icons)) {
      const { container } = render(<Icon />)
      const kebab = name.replace(/([a-z])([A-Z])/g, '$1-$2').toLowerCase()
      expect(container.querySelector('svg')?.classList.contains(`lucide-${kebab}`), name).toBe(true)
      expect(css).toContain(`.lucide-${kebab}`)
    }
  })

  it('points text arrows the reading direction', () => {
    expect(forwardArrow()).toBe('→')
    document.documentElement.dir = 'rtl'
    expect(forwardArrow()).toBe('←')
  })
})
